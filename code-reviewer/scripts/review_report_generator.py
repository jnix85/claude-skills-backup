#!/usr/bin/env python3
"""
Review Report Generator
Combines PR-diff analysis (pr_analyzer.PrAnalyzer) and static code-quality
analysis (code_quality_checker.CodeQualityChecker) into one unified code
review report, rendered as Markdown or JSON.
"""

import argparse
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).parent))

from _lib import is_git_repo, severity_icon, severity_meets_threshold, sort_findings

# Category -> recommendation text. Only categories that actually appear in
# the combined findings contribute a line to the Recommendations section.
CATEGORY_RECOMMENDATIONS: Dict[str, str] = {
    "possible-secret": (
        "Rotate and remove the flagged credentials before merging; "
        "consider a pre-commit secret scanner."
    ),
    "bare-except": (
        "Review swallowed exceptions — bare excepts can hide real bugs "
        "including KeyboardInterrupt/SystemExit."
    ),
    "empty-catch": (
        "Review swallowed exceptions — empty catch blocks can hide real bugs."
    ),
    "long-function": (
        "Consider extracting the flagged functions into smaller, "
        "single-purpose units."
    ),
    "high-complexity": (
        "Consider extracting the flagged functions into smaller, "
        "single-purpose units to reduce cyclomatic complexity."
    ),
    "long-file": (
        "Consider splitting the flagged files into smaller, more focused modules."
    ),
    "deep-nesting": (
        "Reduce nesting depth in the flagged locations by extracting helper "
        "functions or using early returns."
    ),
    "debug-statement": (
        "Remove leftover debug output before merging."
    ),
    "long-line": (
        "Wrap or refactor overly long lines for readability."
    ),
    "todo-comment": (
        "Resolve or ticket the flagged TODO/FIXME comments before merging."
    ),
}


class ReviewReportGenerator:
    """Combines PR-diff analysis and static code-quality analysis into one
    unified review report."""

    def __init__(
        self,
        target_path: str,
        verbose: bool = False,
        base_ref: Optional[str] = None,
        skip_pr_analysis: bool = False,
        skip_quality_check: bool = False,
        max_file_lines: int = 400,
        max_function_lines: int = 50,
        max_line_length: int = 120,
        max_nesting_depth: int = 5,
        output_format: str = "markdown",
        output_file: Optional[str] = None,
        fail_on: Optional[str] = None,
    ):
        self.target_path = Path(target_path)
        self.verbose = verbose
        self.base_ref = base_ref
        self.skip_pr_analysis = skip_pr_analysis
        self.skip_quality_check = skip_quality_check
        self.max_file_lines = max_file_lines
        self.max_function_lines = max_function_lines
        self.max_line_length = max_line_length
        self.max_nesting_depth = max_nesting_depth
        self.output_format = output_format
        self.output_file = output_file
        self.fail_on = fail_on
        self.results: Dict = {}

    def run(self) -> Dict:
        """Execute the main functionality."""
        if self.verbose:
            print(f"\U0001F680 Running {self.__class__.__name__}...")
            print(f"\U0001F4C1 Target: {self.target_path}")

        try:
            self.validate_target()
            self.analyze()
            report = self.generate_report()

            if self.output_file:
                Path(self.output_file).write_text(report, encoding="utf-8")
                print(f"Report written to {self.output_file}")
            else:
                print(report)

            if self.verbose:
                print("✅ Completed successfully!", file=sys.stderr)

        except Exception as e:
            print(f"❌ Error: {e}", file=sys.stderr)
            sys.exit(1)

        if self.fail_on:
            combined = self.results.get("combined_findings", [])
            if any(severity_meets_threshold(f.get("severity", "info"), self.fail_on) for f in combined):
                sys.exit(1)

        return self.results

    def validate_target(self):
        """Validate the target path exists and is accessible."""
        if not self.target_path.exists():
            raise ValueError(f"Target path does not exist: {self.target_path}")

        if self.verbose:
            print(f"✓ Target validated: {self.target_path}")

    def _run_pr_analysis(self) -> Dict:
        if self.skip_pr_analysis:
            return {"skipped": True, "reason": "skipped by --skip-pr-analysis"}

        if not is_git_repo(self.target_path):
            return {"skipped": True, "reason": "not a git repository"}

        try:
            from pr_analyzer import PrAnalyzer
        except ImportError as e:
            return {"skipped": True, "reason": f"PR analysis unavailable: {e}"}

        try:
            # PrAnalyzer's actual constructor takes `base`, not `base_ref`.
            analyzer = PrAnalyzer(str(self.target_path), base=self.base_ref, verbose=self.verbose)
            analyzer.validate_target()
            analyzer.analyze()
            return analyzer.results
        except SystemExit:
            return {"skipped": True, "reason": "PR analysis failed (analyzer exited)"}
        except Exception as e:
            return {"skipped": True, "reason": f"PR analysis failed: {e}"}

    def _run_quality_check(self) -> Dict:
        if self.skip_quality_check:
            return {"skipped": True, "reason": "skipped by --skip-quality-check"}

        try:
            from code_quality_checker import CodeQualityChecker
        except ImportError as e:
            return {"skipped": True, "reason": f"Code quality analysis unavailable: {e}"}

        try:
            # CodeQualityChecker's actual constructor takes a single
            # argparse.Namespace-like `args` object, not individual
            # keyword params — build one that satisfies every attribute
            # it reads (max_file_lines/max_function_lines/max_line_length/
            # max_nesting_depth/verbose/output).
            checker_args = SimpleNamespace(
                verbose=self.verbose,
                max_file_lines=self.max_file_lines,
                max_function_lines=self.max_function_lines,
                max_line_length=self.max_line_length,
                max_nesting_depth=self.max_nesting_depth,
                output=None,
            )
            checker = CodeQualityChecker(str(self.target_path), checker_args)
            checker.validate_target()
            checker.analyze()
            return checker.results
        except SystemExit:
            return {"skipped": True, "reason": "Code quality analysis failed (checker exited)"}
        except Exception as e:
            return {"skipped": True, "reason": f"Code quality analysis failed: {e}"}

    def analyze(self):
        """Run both sub-analyzers, merge and dedupe their findings, and
        compute the combined summary."""
        if self.verbose:
            print("\U0001F4CA Analyzing...")

        pr_analysis = self._run_pr_analysis()
        code_quality = self._run_quality_check()

        pr_findings = [] if pr_analysis.get("skipped") else pr_analysis.get("findings", [])
        quality_findings = [] if code_quality.get("skipped") else code_quality.get("findings", [])

        combined_raw = list(pr_findings) + list(quality_findings)

        seen = set()
        deduped: List[Dict] = []
        for f in combined_raw:
            key = (
                f.get("severity"),
                f.get("category"),
                f.get("file"),
                f.get("line"),
                f.get("message"),
            )
            if key in seen:
                continue
            seen.add(key)
            deduped.append(f)

        combined_findings = sort_findings(deduped)

        severity_counts = {"high": 0, "medium": 0, "low": 0, "info": 0}
        for f in combined_findings:
            sev = f.get("severity", "info")
            if sev not in severity_counts:
                severity_counts[sev] = 0
            severity_counts[sev] += 1

        languages: Dict[str, int] = {}
        if not pr_analysis.get("skipped"):
            for lang, count in pr_analysis.get("summary", {}).get("languages", {}).items():
                languages[lang] = languages.get(lang, 0) + count
        if not code_quality.get("skipped"):
            for lang, count in code_quality.get("summary", {}).get("languages", {}).items():
                languages[lang] = languages.get(lang, 0) + count

        recommendations = self._build_recommendations(combined_findings)

        self.results = {
            "status": "success",
            "target": str(self.target_path),
            "pr_analysis": pr_analysis,
            "code_quality": code_quality,
            "combined_findings": combined_findings,
            "summary": {
                "files_changed": (
                    None if pr_analysis.get("skipped")
                    else pr_analysis.get("summary", {}).get("files_changed")
                ),
                "pr_analysis_skip_reason": pr_analysis.get("reason") if pr_analysis.get("skipped") else None,
                "files_analyzed": (
                    None if code_quality.get("skipped")
                    else code_quality.get("summary", {}).get("files_analyzed")
                ),
                "quality_check_skip_reason": code_quality.get("reason") if code_quality.get("skipped") else None,
                "findings_by_severity": severity_counts,
                "languages": languages,
            },
            "recommendations": recommendations,
        }

        if self.verbose:
            print(f"✓ Analysis complete: {len(combined_findings)} combined findings")

    @staticmethod
    def _build_recommendations(findings: List[Dict]) -> List[str]:
        categories_present = {f.get("category") for f in findings if f.get("category")}

        recommendations: List[str] = []
        for category, text in CATEGORY_RECOMMENDATIONS.items():
            if category in categories_present:
                recommendations.append(text)

        if not findings:
            recommendations.append(
                "No issues found by automated analysis — this does not "
                "replace human review of logic and intent."
            )

        return recommendations

    def generate_report(self) -> str:
        """Render the final report as Markdown or JSON (does not print)."""
        if self.output_format == "json":
            return json.dumps(self.results, indent=2)
        return self._render_markdown()

    def _render_markdown(self) -> str:
        r = self.results
        pr = r.get("pr_analysis", {})
        cq = r.get("code_quality", {})
        summary = r.get("summary", {})
        lines: List[str] = []

        lines.append("# Code Review Report")
        lines.append("")
        lines.append(f"**Target:** {r.get('target')}")
        lines.append("_(timestamp omitted for reproducibility)_")
        lines.append("")

        # Summary
        lines.append("## Summary")
        if pr.get("skipped"):
            lines.append(f"- Files changed (PR diff): skipped ({pr.get('reason')})")
        else:
            lines.append(f"- Files changed (PR diff): {summary.get('files_changed')}")
        if cq.get("skipped"):
            lines.append(f"- Files analyzed (quality): skipped ({cq.get('reason')})")
        else:
            lines.append(f"- Files analyzed (quality): {summary.get('files_analyzed')}")
        sev = summary.get("findings_by_severity", {})
        lines.append(
            f"- Total findings: {sev.get('high', 0)} high, {sev.get('medium', 0)} medium, "
            f"{sev.get('low', 0)} low, {sev.get('info', 0)} info"
        )
        lines.append("")

        # PR Analysis
        lines.append("## PR Analysis")
        if pr.get("skipped"):
            lines.append(f"_Skipped: {pr.get('reason')}_")
        else:
            pr_summary = pr.get("summary", {})
            lines.append(f"- Base ref: `{pr.get('base_ref')}`")
            lines.append(f"- Files changed: {pr_summary.get('files_changed')}")
            lines.append(f"- Insertions: +{pr_summary.get('insertions', 0)}, Deletions: -{pr_summary.get('deletions', 0)}")
            pr_langs = pr_summary.get("languages", {})
            if pr_langs:
                lang_str = ", ".join(f"{lang}: {count}" for lang, count in sorted(pr_langs.items()))
                lines.append(f"- Languages: {lang_str}")
            pr_findings = sort_findings(pr.get("findings", []))
            if pr_findings:
                lines.append("")
                lines.append("**Findings:**")
                for f in pr_findings:
                    lines.append(ReviewReportGenerator._format_finding(f))
        lines.append("")

        # Code Quality
        lines.append("## Code Quality")
        if cq.get("skipped"):
            lines.append(f"_Skipped: {cq.get('reason')}_")
        else:
            cq_summary = cq.get("summary", {})
            cq_langs = cq_summary.get("languages", {})
            if cq_langs:
                lang_str = ", ".join(f"{lang}: {count}" for lang, count in sorted(cq_langs.items()))
                lines.append(f"- Languages: {lang_str}")
            lines.append(f"- Total lines: {cq_summary.get('total_lines')}")
            cq_findings = sort_findings(cq.get("findings", []))
            if cq_findings:
                lines.append("")
                lines.append("**Findings:**")
                for f in cq_findings:
                    lines.append(ReviewReportGenerator._format_finding(f))
        lines.append("")

        # All Findings
        lines.append("## All Findings")
        combined = r.get("combined_findings", [])
        if not combined:
            lines.append("_No findings._")
        else:
            by_severity: Dict[str, List[Dict]] = {}
            for f in combined:
                by_severity.setdefault(f.get("severity", "info"), []).append(f)
            for severity in ("high", "medium", "low", "info"):
                group = by_severity.get(severity)
                if not group:
                    continue
                icon = severity_icon(severity)
                lines.append(f"### {icon} {severity.capitalize()}")
                for f in group:
                    lines.append(ReviewReportGenerator._format_finding(f, include_icon=False))
                lines.append("")
        lines.append("")

        # Recommendations
        lines.append("## Recommendations")
        recs = r.get("recommendations", [])
        if recs:
            for rec in recs:
                lines.append(f"- {rec}")
        else:
            lines.append("_No recommendations._")

        return "\n".join(lines).rstrip() + "\n"

    @staticmethod
    def _format_finding(f: Dict, include_icon: bool = True) -> str:
        icon = f"{severity_icon(f.get('severity', 'info'))} " if include_icon else ""
        category = f.get("category", "general")
        file_ref = f.get("file", "?")
        line = f.get("line")
        location = f"{file_ref}:{line}" if line else f"{file_ref}"
        message = f.get("message", "")
        return f"- {icon}**[{category}]** {location} — {message}"


def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(
        description="Combine PR-diff analysis and static code-quality analysis into one unified review report."
    )
    parser.add_argument("target", help="Target path to analyze or process")
    parser.add_argument("--base", dest="base_ref", default=None, help="Base ref for PR diff analysis")
    parser.add_argument(
        "--format", choices=["markdown", "json"], default="markdown", help="Output format (default: markdown)"
    )
    parser.add_argument("--output", "-o", help="Output file path")
    parser.add_argument("--skip-pr-analysis", action="store_true", help="Skip PR-diff analysis")
    parser.add_argument("--skip-quality-check", action="store_true", help="Skip static code-quality analysis")
    parser.add_argument("--max-file-lines", type=int, default=400, help="Max lines per file before flagging")
    parser.add_argument("--max-function-lines", type=int, default=50, help="Max lines per function before flagging")
    parser.add_argument("--max-line-length", type=int, default=120, help="Max characters per line before flagging")
    parser.add_argument("--max-nesting-depth", type=int, default=5, help="Max nesting depth before flagging")
    parser.add_argument(
        "--fail-on",
        choices=["high", "medium", "low"],
        default=None,
        help="Exit with status 1 if combined findings include this severity or worse",
    )
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable verbose output")

    args = parser.parse_args()

    tool = ReviewReportGenerator(
        args.target,
        verbose=args.verbose,
        base_ref=args.base_ref,
        skip_pr_analysis=args.skip_pr_analysis,
        skip_quality_check=args.skip_quality_check,
        max_file_lines=args.max_file_lines,
        max_function_lines=args.max_function_lines,
        max_line_length=args.max_line_length,
        max_nesting_depth=args.max_nesting_depth,
        output_format=args.format,
        output_file=args.output,
        fail_on=args.fail_on,
    )

    tool.run()


if __name__ == "__main__":
    main()
