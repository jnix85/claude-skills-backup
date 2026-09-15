#!/usr/bin/env python3
"""Static-analysis heuristics for the code-reviewer skill.

Walks a file or directory tree and flags common code-quality smells: long
files/functions, deep nesting, TODO/FIXME markers, debug statements left
in, bare excepts, empty catch blocks, and possible hardcoded secrets.

This is a heuristic scanner, not a real parser/linter — it trades
precision for being fast, dependency-free, and language-agnostic across
Python/TypeScript/JavaScript/Go/Swift/Kotlin.
"""

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from _lib import (  # noqa: E402
    BARE_EXCEPT_PATTERN,
    BRACE_LANGUAGES,
    DEBUG_STATEMENT_PATTERNS,
    DECISION_KEYWORD_PATTERN,
    EMPTY_CATCH_PATTERN,
    FUNCTION_SIGNATURE_PATTERNS,
    SECRET_PATTERNS,
    TODO_PATTERN,
    detect_language,
    is_test_path,
    iter_source_files,
    read_text_safely,
    severity_icon,
    severity_meets_threshold,
    sort_findings,
)

DEFAULT_MAX_FILE_LINES = 400
DEFAULT_MAX_FUNCTION_LINES = 50
DEFAULT_MAX_LINE_LENGTH = 120
DEFAULT_MAX_NESTING_DEPTH = 5
COMPLEXITY_THRESHOLD = 10
LONG_LINE_SHOWN_LIMIT = 5


def make_finding(severity, category, file, line, message):
    return {
        "severity": severity,
        "category": category,
        "file": file,
        "line": line,
        "message": message,
    }


# --------------------------------------------------------------------------
# Function-span detection (used for long-function and high-complexity checks)
# --------------------------------------------------------------------------

def get_python_function_spans(lines):
    """Find def/async def spans using indentation: a function body runs
    until the next non-blank line at or below the def's own indentation."""
    pattern = FUNCTION_SIGNATURE_PATTERNS["Python"]
    name_re = re.compile(r"def\s+(\w+)")
    n = len(lines)
    spans = []
    for i, line in enumerate(lines):
        m = pattern.match(line)
        if not m:
            continue
        indent = len(m.group(1))
        name_match = name_re.search(line)
        name = name_match.group(1) if name_match else "<unknown>"

        j = i + 1
        while j < n:
            candidate = lines[j]
            if candidate.strip() == "":
                j += 1
                continue
            cand_indent = len(candidate) - len(candidate.lstrip(" \t"))
            if cand_indent <= indent:
                break
            j += 1
        end = j - 1
        while end > i and lines[end].strip() == "":
            end -= 1
        if end < i:
            end = i

        spans.append({"name": name, "start": i + 1, "end": end + 1, "length": end - i + 1})
    return spans


def extract_function_name(line):
    """Best-effort function/method name extraction for brace-language
    signature lines (JS/TS function decls, arrow-fn assignments, Go/Swift
    funcs, Kotlin funs, method shorthand)."""
    m = re.search(r"\bfunction\s*\*?\s*(\w+)", line)
    if m:
        return m.group(1)
    m = re.search(r"\bfunc\s+(?:\([^)]*\)\s*)?(\w+)", line)
    if m:
        return m.group(1)
    m = re.search(r"\bfun\s+(\w+)", line)
    if m:
        return m.group(1)
    m = re.search(r"\b(?:const|let|var)\s+(\w+)\s*=", line)
    if m:
        return m.group(1)
    m = re.search(
        r"^\s*(?:public\s+|private\s+|protected\s+|internal\s+|fileprivate\s+|static\s+|async\s+)*"
        r"(\w+)\s*\(",
        line,
    )
    if m:
        return m.group(1)
    return "<anonymous>"


def find_brace_body_end(lines, start_idx, search_window=3):
    """Find the first '{' at or after lines[start_idx] (within a small
    lookahead window, to skip arrow functions with expression bodies), then
    scan forward tracking brace balance (best-effort aware of string/char
    literals and // line comments) until the balance returns to zero.

    Returns the 0-indexed line at which the body closes, or None if no
    opening brace was found nearby (e.g. `const f = () => x + 1`)."""
    n = len(lines)
    limit = min(n, start_idx + search_window)
    brace_line = None
    brace_col = None
    for li in range(start_idx, limit):
        idx = lines[li].find("{")
        if idx != -1:
            brace_line = li
            brace_col = idx
            break
    if brace_line is None:
        return None

    depth = 0
    in_string = None
    li = brace_line
    col = brace_col
    while li < n:
        line = lines[li]
        j = col
        length = len(line)
        while j < length:
            ch = line[j]
            if in_string:
                if ch == "\\":
                    j += 2
                    continue
                if ch == in_string:
                    in_string = None
                j += 1
                continue
            if ch in ('"', "'", "`"):
                in_string = ch
                j += 1
                continue
            if ch == "/" and j + 1 < length and line[j + 1] == "/":
                break
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return li
            j += 1
        li += 1
        col = 0
    return n - 1


def get_brace_function_spans(language, lines):
    pattern = FUNCTION_SIGNATURE_PATTERNS.get(language)
    if not pattern:
        return []
    spans = []
    for i, line in enumerate(lines):
        if not pattern.match(line):
            continue
        end_idx = find_brace_body_end(lines, i)
        if end_idx is None:
            continue
        name = extract_function_name(line)
        spans.append({"name": name, "start": i + 1, "end": end_idx + 1, "length": end_idx - i + 1})
    return spans


def get_function_spans(language, lines):
    if language == "Python":
        return get_python_function_spans(lines)
    if language in BRACE_LANGUAGES:
        return get_brace_function_spans(language, lines)
    return []


# --------------------------------------------------------------------------
# Nesting-depth detection
# --------------------------------------------------------------------------

def compute_python_max_nesting(lines):
    indents = [len(l) - len(l.lstrip(" ")) for l in lines if l.strip() != ""]
    positive = [i for i in indents if i > 0]
    unit = min(positive) if positive else 4
    max_depth = 0
    max_line = 0
    for idx, line in enumerate(lines):
        if line.strip() == "":
            continue
        indent = len(line) - len(line.lstrip(" "))
        depth = indent // unit
        if depth > max_depth:
            max_depth = depth
            max_line = idx + 1
    return max_depth, max_line


def compute_brace_max_nesting(lines):
    depth = 0
    max_depth = 0
    max_line = 0
    in_string = None
    for idx, line in enumerate(lines):
        j = 0
        length = len(line)
        while j < length:
            ch = line[j]
            if in_string:
                if ch == "\\":
                    j += 2
                    continue
                if ch == in_string:
                    in_string = None
                j += 1
                continue
            if ch in ('"', "'", "`"):
                in_string = ch
                j += 1
                continue
            if ch == "/" and j + 1 < length and line[j + 1] == "/":
                break
            if ch == "{":
                depth += 1
                if depth > max_depth:
                    max_depth = depth
                    max_line = idx + 1
            elif ch == "}":
                depth = max(depth - 1, 0)
            j += 1
    return max_depth, max_line


class CodeQualityChecker:
    """Runs static-analysis quality heuristics against a file or directory."""

    def __init__(self, target_path, args):
        self.target_path = Path(target_path)
        self.args = args
        self.verbose = getattr(args, "verbose", False)
        self.results = {
            "status": "success",
            "target": str(self.target_path),
            "summary": {
                "files_analyzed": 0,
                "languages": {},
                "total_lines": 0,
                "findings_by_severity": {"high": 0, "medium": 0, "low": 0, "info": 0},
            },
            "findings": [],
        }

    # ----------------------------------------------------------------
    def validate_target(self):
        if not self.target_path.exists():
            return False, f"Target path does not exist: {self.target_path}"
        return True, None

    # ----------------------------------------------------------------
    def analyze_file(self, path, rel_display, language, text):
        findings = []
        lines = text.splitlines()
        line_count = len(lines)
        args = self.args

        # 1. Long file
        if line_count > args.max_file_lines:
            findings.append(make_finding(
                "low", "long-file", rel_display, 1,
                f"File has {line_count} lines (threshold {args.max_file_lines})",
            ))

        # 2. Long lines (cap reported occurrences, summarize the rest)
        long_line_total = 0
        shown = 0
        for idx, line in enumerate(lines, start=1):
            if line.strip() == "":
                continue
            if len(line) > args.max_line_length:
                long_line_total += 1
                if shown < LONG_LINE_SHOWN_LIMIT:
                    findings.append(make_finding(
                        "info", "long-line", rel_display, idx,
                        f"Line is {len(line)} characters long (threshold {args.max_line_length})",
                    ))
                    shown += 1
        if long_line_total > LONG_LINE_SHOWN_LIMIT:
            findings.append(make_finding(
                "info", "long-line", rel_display, 0,
                f"{long_line_total - LONG_LINE_SHOWN_LIMIT} more long lines not shown",
            ))

        # 3. Long functions (+ 10. high complexity, reusing the same spans)
        spans = get_function_spans(language, lines)
        for span in spans:
            if span["length"] > args.max_function_lines:
                findings.append(make_finding(
                    "medium", "long-function", rel_display, span["start"],
                    f"Function '{span['name']}' is {span['length']} lines long "
                    f"(threshold {args.max_function_lines})",
                ))
            segment = lines[span["start"] - 1:span["end"]]
            decision_count = sum(len(DECISION_KEYWORD_PATTERN.findall(l)) for l in segment)
            if decision_count > COMPLEXITY_THRESHOLD:
                findings.append(make_finding(
                    "medium", "high-complexity", rel_display, span["start"],
                    f"Function '{span['name']}' has {decision_count} decision points "
                    f"(threshold {COMPLEXITY_THRESHOLD})",
                ))

        # 4. Deep nesting (one finding per file, at the deepest line)
        if language == "Python":
            max_depth, max_line = compute_python_max_nesting(lines)
        elif language in BRACE_LANGUAGES:
            max_depth, max_line = compute_brace_max_nesting(lines)
        else:
            max_depth, max_line = 0, 0
        if max_depth > args.max_nesting_depth:
            findings.append(make_finding(
                "medium", "deep-nesting", rel_display, max_line or 1,
                f"Maximum nesting depth is {max_depth} (threshold {args.max_nesting_depth})",
            ))

        # 5/6/7/9: per-line checks (TODO, debug statements, bare except, secrets)
        debug_pattern = DEBUG_STATEMENT_PATTERNS.get(language)
        test_path = is_test_path(path)
        for idx, line in enumerate(lines, start=1):
            for m in TODO_PATTERN.finditer(line):
                findings.append(make_finding(
                    "info", "todo", rel_display, idx,
                    f"{m.group(1).upper()} comment: {line.strip()[:120]}",
                ))

            if debug_pattern and debug_pattern.search(line):
                severity = "low" if test_path else "medium"
                findings.append(make_finding(
                    severity, "debug-statement", rel_display, idx,
                    f"Debug statement found: {line.strip()[:120]}",
                ))

            if language == "Python" and BARE_EXCEPT_PATTERN.match(line):
                findings.append(make_finding(
                    "medium", "bare-except", rel_display, idx,
                    "Bare 'except:' swallows all exceptions, including KeyboardInterrupt/SystemExit",
                ))

            for label, pattern in SECRET_PATTERNS:
                if pattern.search(line):
                    findings.append(make_finding(
                        "high", "possible-secret", rel_display, idx,
                        f"Possible secret detected ({label}) — value redacted",
                    ))

        # 8. Empty catch blocks (brace languages; can span multiple lines)
        if language in BRACE_LANGUAGES:
            for m in EMPTY_CATCH_PATTERN.finditer(text):
                line_no = text.count("\n", 0, m.start()) + 1
                findings.append(make_finding(
                    "medium", "empty-catch", rel_display, line_no,
                    "Empty catch block silently swallows the exception",
                ))

        return findings, line_count

    # ----------------------------------------------------------------
    def analyze(self):
        args = self.args
        is_dir = self.target_path.is_dir()
        findings = []
        languages = {}
        total_lines = 0
        files_analyzed = 0

        for path in iter_source_files(self.target_path):
            text = read_text_safely(path)
            if text is None:
                continue
            language = detect_language(path)
            rel_display = str(path.relative_to(self.target_path)) if is_dir else path.name

            files_analyzed += 1
            if self.verbose:
                print(f"[{files_analyzed}] Analyzing {rel_display} ({language})", file=sys.stderr)

            file_findings, line_count = self.analyze_file(path, rel_display, language, text)
            findings.extend(file_findings)
            languages[language] = languages.get(language, 0) + 1
            total_lines += line_count

        findings = sort_findings(findings)
        severity_counts = {"high": 0, "medium": 0, "low": 0, "info": 0}
        for f in findings:
            severity_counts[f["severity"]] = severity_counts.get(f["severity"], 0) + 1

        self.results = {
            "status": "success",
            "target": str(self.target_path),
            "summary": {
                "files_analyzed": files_analyzed,
                "languages": languages,
                "total_lines": total_lines,
                "findings_by_severity": severity_counts,
            },
            "findings": findings,
        }
        return self.results

    # ----------------------------------------------------------------
    def generate_report(self):
        summary = self.results["summary"]
        findings = self.results["findings"]
        out = []

        out.append(f"Code Quality Report — {self.results['target']}")
        out.append("")
        out.append(f"Files analyzed: {summary['files_analyzed']}")
        if summary["languages"]:
            lang_str = ", ".join(f"{k}: {v}" for k, v in sorted(summary["languages"].items()))
            out.append(f"Languages: {lang_str}")
        out.append(f"Total lines: {summary['total_lines']}")
        sev = summary["findings_by_severity"]
        out.append(
            f"Findings by severity: high={sev.get('high', 0)} medium={sev.get('medium', 0)} "
            f"low={sev.get('low', 0)} info={sev.get('info', 0)}"
        )
        out.append("")

        if not findings:
            out.append("No issues found.")
        else:
            for severity in ("high", "medium", "low", "info"):
                group = [f for f in findings if f["severity"] == severity]
                if not group:
                    continue
                out.append(f"{severity_icon(severity)} {severity.upper()} ({len(group)})")
                for f in group:
                    loc = f"{f['file']}:{f['line']}" if f["line"] else f["file"]
                    out.append(f"  {loc} — {f['message']}")
                out.append("")

        report_text = "\n".join(out).rstrip() + "\n"
        self._write_output(report_text)

    # ----------------------------------------------------------------
    def _write_output(self, text):
        if getattr(self.args, "output", None):
            body = text if text.endswith("\n") else text + "\n"
            Path(self.args.output).write_text(body, encoding="utf-8")
        else:
            print(text)

    # ----------------------------------------------------------------
    def run(self):
        ok, error = self.validate_target()
        if not ok:
            self.results = {
                "status": "error",
                "target": str(self.target_path),
                "error": error,
                "summary": {
                    "files_analyzed": 0,
                    "languages": {},
                    "total_lines": 0,
                    "findings_by_severity": {"high": 0, "medium": 0, "low": 0, "info": 0},
                },
                "findings": [],
            }
            if getattr(self.args, "json", False):
                self._write_output(json.dumps(self.results, indent=2))
            else:
                print(f"Error: {error}", file=sys.stderr)
            return 1

        self.analyze()

        if getattr(self.args, "json", False):
            self._write_output(json.dumps(self.results, indent=2))
        else:
            self.generate_report()

        fail_on = getattr(self.args, "fail_on", None)
        if fail_on:
            findings = self.results.get("findings", [])
            if any(severity_meets_threshold(f["severity"], fail_on) for f in findings):
                return 1
        return 0


def build_arg_parser():
    parser = argparse.ArgumentParser(
        description="Run static-analysis quality heuristics against a file or directory.",
    )
    parser.add_argument("target", help="File or directory to analyze")
    parser.add_argument("--verbose", "-v", action="store_true", help="Print progress as files are analyzed")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON instead of a text report")
    parser.add_argument("--output", "-o", default=None, help="Write the report to this file instead of stdout")
    parser.add_argument("--max-file-lines", type=int, default=DEFAULT_MAX_FILE_LINES)
    parser.add_argument("--max-function-lines", type=int, default=DEFAULT_MAX_FUNCTION_LINES)
    parser.add_argument("--max-line-length", type=int, default=DEFAULT_MAX_LINE_LENGTH)
    parser.add_argument("--max-nesting-depth", type=int, default=DEFAULT_MAX_NESTING_DEPTH)
    parser.add_argument(
        "--fail-on", choices=["high", "medium", "low"], default=None,
        help="Exit with status 1 if any finding meets or exceeds this severity",
    )
    return parser


def main():
    parser = build_arg_parser()
    args = parser.parse_args()
    checker = CodeQualityChecker(args.target, args)
    sys.exit(checker.run())


if __name__ == "__main__":
    main()
