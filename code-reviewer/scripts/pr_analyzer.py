#!/usr/bin/env python3
"""
PR Analyzer

Diffs a git repository against a base ref and reports real findings about
the change: TODO/FIXME markers, leftover debug statements, possible
hardcoded secrets, and changeset-level signals (oversized file changes,
large changesets, added binary files). Intended for CI use via --fail-on.
"""

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional

sys.path.insert(0, str(Path(__file__).parent))

from _lib import (  # noqa: E402
    DEBUG_STATEMENT_PATTERNS,
    SECRET_PATTERNS,
    TODO_PATTERN,
    detect_language,
    is_git_repo,
    is_test_path,
    resolve_base_ref,
    run_git,
    severity_icon,
    severity_meets_threshold,
    sort_findings,
    try_run_git,
)

# Matches a unified-diff hunk header and captures the starting line number
# of the new file, e.g. "@@ -12,3 +15,4 @@" -> group(1) == "15".
HUNK_HEADER = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")

LARGE_FILE_CHANGE_THRESHOLD = 500
LARGE_CHANGESET_THRESHOLD = 30


class PrAnalyzerError(Exception):
    """Raised for expected/handleable failures (bad target, bad ref, etc.)."""


class PrAnalyzer:
    """Analyzes the diff between a base ref and HEAD in a git repository."""

    def __init__(self, target_path: str, base: Optional[str] = None, verbose: bool = False):
        self.target_path = Path(target_path)
        self.base = base
        self.verbose = verbose
        self.base_ref: Optional[str] = None
        self.results: Dict = {}

    def log(self, message: str):
        if self.verbose:
            print(message)

    def run(self) -> Dict:
        """Validate the target and perform the analysis. Exits(1) on any
        expected failure with a clean message (no traceback)."""
        print(f"Running {self.__class__.__name__}...")
        print(f"Target: {self.target_path}")

        try:
            self.validate_target()
            self.analyze()
        except PrAnalyzerError as e:
            print(f"Error: {e}")
            sys.exit(1)
        except subprocess.CalledProcessError as e:
            stderr = (e.stderr or "").strip()
            print(f"Error: git command failed: {stderr or e}")
            sys.exit(1)
        except FileNotFoundError:
            print("Error: git is not installed or not found on PATH.")
            sys.exit(1)

        return self.results

    def validate_target(self):
        """Validate the target path is a usable git repository and resolve
        the base ref we will diff against."""
        if shutil.which("git") is None:
            raise PrAnalyzerError("git is not installed or not found on PATH.")

        if not self.target_path.exists():
            raise PrAnalyzerError(f"Target path does not exist: {self.target_path}")

        if not self.target_path.is_dir():
            raise PrAnalyzerError(f"Target path is not a directory: {self.target_path}")

        if not is_git_repo(self.target_path):
            raise PrAnalyzerError(f"Not a git repository: {self.target_path}")

        if self.base:
            if try_run_git(["rev-parse", "--verify", "--quiet", self.base], self.target_path) is None:
                raise PrAnalyzerError(f"Base ref does not resolve in this repository: {self.base}")
            base_ref = self.base
        else:
            base_ref = resolve_base_ref(self.target_path)
            if base_ref is None:
                raise PrAnalyzerError(
                    "Could not resolve a base ref to diff against (repository has no commits)."
                )

        self.base_ref = base_ref
        if self.verbose:
            print(f"Base ref resolved: {self.base_ref}")

    def analyze(self):
        """Run the diff and populate self.results."""
        diff_range = f"{self.base_ref}...HEAD"

        self.log("Running git diff --numstat ...")
        numstat_out = run_git(["diff", "--numstat", "-z", diff_range], self.target_path)
        self.log("Running git diff --name-status ...")
        name_status_out = run_git(["diff", "--name-status", "-z", diff_range], self.target_path)

        files = self._parse_numstat_z(numstat_out)
        statuses = self._parse_name_status_z(name_status_out)
        for f in files:
            f["status"] = statuses.get(f["path"], "M")

        languages: Dict[str, int] = {}
        for f in files:
            if f["language"]:
                languages[f["language"]] = languages.get(f["language"], 0) + 1

        findings: List[dict] = []

        for f in files:
            if f["status"] == "D":
                continue  # nothing to scan in a deleted file

            if f["binary"]:
                if f["status"] == "A":
                    findings.append({
                        "severity": "info",
                        "category": "binary-file-added",
                        "file": f["path"],
                        "line": None,
                        "message": f"Binary file added: {f['path']}",
                    })
                continue

            if not f["language"]:
                continue

            self.log(f"Scanning {f['path']}...")
            findings.extend(self._scan_file_diff(f["path"], f["language"]))

        for f in files:
            total_changed = f["insertions"] + f["deletions"]
            if total_changed > LARGE_FILE_CHANGE_THRESHOLD:
                findings.append({
                    "severity": "medium",
                    "category": "large-file-change",
                    "file": f["path"],
                    "line": None,
                    "message": (
                        f"{f['path']} has a large change ({f['insertions']} insertions, "
                        f"{f['deletions']} deletions, {total_changed} lines total) — "
                        "consider splitting into smaller commits/PRs."
                    ),
                })

        if len(files) > LARGE_CHANGESET_THRESHOLD:
            findings.append({
                "severity": "info",
                "category": "large-changeset",
                "file": "",
                "line": None,
                "message": (
                    f"{len(files)} files changed in this diff — consider splitting into "
                    "smaller PRs for easier review."
                ),
            })

        findings = sort_findings(findings)

        self.results = {
            "status": "success",
            "target": str(self.target_path),
            "base_ref": self.base_ref,
            "summary": {
                "files_changed": len(files),
                "insertions": sum(f["insertions"] for f in files),
                "deletions": sum(f["deletions"] for f in files),
                "languages": languages,
            },
            "files": files,
            "findings": findings,
        }

        if self.verbose:
            print(f"Analysis complete: {len(files)} files changed, {len(findings)} findings")

    # -- parsing helpers -----------------------------------------------

    def _parse_numstat_z(self, output: str) -> List[dict]:
        """Parse `git diff --numstat -z` output into file entries.

        With -z, a normal file is one NUL-terminated token
        "<ins>\\t<del>\\t<path>". A renamed file has an empty path field
        followed by two additional NUL-terminated tokens: old path, then
        new path. This avoids the ambiguous "old => new" text notation.
        """
        tokens = output.split("\0")
        files: List[dict] = []
        i = 0
        n = len(tokens)
        while i < n:
            tok = tokens[i]
            if tok == "":
                i += 1
                continue
            parts = tok.split("\t")
            if len(parts) >= 3 and parts[2] != "":
                ins_str, del_str = parts[0], parts[1]
                path = parts[2]
                i += 1
            elif len(parts) >= 2:
                ins_str, del_str = parts[0], parts[1]
                # Rename: path field was empty; next two tokens are old/new path.
                new_path = tokens[i + 2] if i + 2 < n else tokens[i + 1] if i + 1 < n else ""
                path = new_path
                i += 3
            else:
                i += 1
                continue

            binary = ins_str == "-" or del_str == "-"
            insertions = 0 if binary else int(ins_str or 0)
            deletions = 0 if binary else int(del_str or 0)

            files.append({
                "path": path,
                "language": detect_language(Path(path)),
                "insertions": insertions,
                "deletions": deletions,
                "binary": binary,
            })
        return files

    def _parse_name_status_z(self, output: str) -> Dict[str, str]:
        """Parse `git diff --name-status -z` into {new_path: status_letter}."""
        tokens = output.split("\0")
        statuses: Dict[str, str] = {}
        i = 0
        n = len(tokens)
        while i < n:
            tok = tokens[i]
            if tok == "":
                i += 1
                continue
            status_letter = tok[0]
            if status_letter in ("R", "C"):
                new_path = tokens[i + 2] if i + 2 < n else ""
                statuses[new_path] = status_letter
                i += 3
            else:
                path = tokens[i + 1] if i + 1 < n else ""
                statuses[path] = status_letter
                i += 2
        return statuses

    def _scan_file_diff(self, path: str, language: str) -> List[dict]:
        """Scan only the ADDED lines of one file's diff for findings."""
        findings: List[dict] = []
        diff_output = try_run_git(
            ["diff", f"{self.base_ref}...HEAD", "--", path], self.target_path
        )
        if diff_output is None:
            return findings

        debug_pattern = DEBUG_STATEMENT_PATTERNS.get(language)
        test_path = is_test_path(Path(path))
        new_lineno = 0

        for line in diff_output.splitlines():
            hunk_match = HUNK_HEADER.match(line)
            if hunk_match:
                new_lineno = int(hunk_match.group(1))
                continue
            if line.startswith("+++") or line.startswith("---"):
                continue
            if line.startswith("+"):
                content = line[1:]
                findings.extend(
                    self._scan_added_line(path, new_lineno, content, debug_pattern, test_path)
                )
                new_lineno += 1
            elif line.startswith("-"):
                continue  # removed line: does not exist in new file, don't advance
            elif line.startswith(" "):
                new_lineno += 1
            # any other line (diff --git, index, mode changes, \ No newline...) is ignored

        return findings

    def _scan_added_line(
        self, path: str, lineno: int, content: str, debug_pattern, is_test: bool
    ) -> List[dict]:
        findings: List[dict] = []

        if TODO_PATTERN.search(content):
            findings.append({
                "severity": "low",
                "category": "todo-added",
                "file": path,
                "line": lineno,
                "message": f"TODO/FIXME marker added: {content.strip()[:120]}",
            })

        if debug_pattern and debug_pattern.search(content):
            findings.append({
                "severity": "low" if is_test else "medium",
                "category": "debug-statement",
                "file": path,
                "line": lineno,
                "message": f"Debug statement added: {content.strip()[:120]}",
            })

        for label, pattern in SECRET_PATTERNS:
            if pattern.search(content):
                findings.append({
                    "severity": "high",
                    "category": "possible-secret",
                    "file": path,
                    "line": lineno,
                    # Deliberately do not include the matched text/content here —
                    # the report itself must not leak the credential it flags.
                    "message": f"Possible {label} added (value redacted).",
                })
                break  # first match wins per line, avoid duplicate noise

        return findings

    def generate_report(self):
        """Print a human-readable report of self.results."""
        summary = self.results.get("summary", {})
        findings = self.results.get("findings", [])

        print("\n" + "=" * 60)
        print("PR ANALYSIS REPORT")
        print("=" * 60)
        print(f"Target:        {self.results.get('target')}")
        print(f"Base ref:      {self.results.get('base_ref')}")
        print(f"Files changed: {summary.get('files_changed', 0)}")
        print(f"Insertions:    +{summary.get('insertions', 0)}")
        print(f"Deletions:     -{summary.get('deletions', 0)}")

        languages = summary.get("languages", {})
        if languages:
            lang_str = ", ".join(
                f"{lang}: {count}" for lang, count in sorted(languages.items(), key=lambda kv: -kv[1])
            )
            print(f"Languages:     {lang_str}")

        print("-" * 60)
        if not findings:
            print("No issues found.")
        else:
            print(f"Findings ({len(findings)}):")
            for f in findings:
                severity = f.get("severity", "info")
                icon = severity_icon(severity)
                location = f.get("file") or "(repo)"
                if f.get("line"):
                    location = f"{location}:{f['line']}"
                print(f"  {icon} [{severity.upper()}] {location} — {f.get('message', '')}")
        print("=" * 60 + "\n")


def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(
        description="Analyze a git repo's changes against a base ref and report findings."
    )
    parser.add_argument(
        'repo_path',
        help='Path to the git repository (or a subdirectory of one) to analyze'
    )
    parser.add_argument(
        '--base',
        help="Base ref to diff against (default: auto-resolved via merge-base with "
             "origin/main, origin/master, main, master, else the repo's first commit)"
    )
    parser.add_argument(
        '--verbose', '-v',
        action='store_true',
        help='Enable verbose output'
    )
    parser.add_argument(
        '--json',
        action='store_true',
        help='Output results as JSON instead of the human-readable report'
    )
    parser.add_argument(
        '--output', '-o',
        help='Output file path (used with --json)'
    )
    parser.add_argument(
        '--fail-on',
        choices=['high', 'medium', 'low'],
        help='Exit with status 1 if any finding is at or above this severity'
    )

    args = parser.parse_args()

    tool = PrAnalyzer(
        args.repo_path,
        base=args.base,
        verbose=args.verbose,
    )

    results = tool.run()

    if args.json:
        output = json.dumps(results, indent=2)
        if args.output:
            with open(args.output, 'w') as f:
                f.write(output)
            print(f"Results written to {args.output}")
        else:
            print(output)
    else:
        tool.generate_report()

    if args.fail_on:
        findings = results.get('findings', [])
        if any(severity_meets_threshold(f.get('severity', 'info'), args.fail_on) for f in findings):
            sys.exit(1)


if __name__ == '__main__':
    main()
