#!/usr/bin/env python3
"""Shared utilities for the code-reviewer skill's scripts.

Not a CLI entry point itself — imported by pr_analyzer.py,
code_quality_checker.py, and review_report_generator.py so the three
scripts share one definition of "what counts as a secret", "what counts as
a debug statement", "what languages we understand", etc. instead of
maintaining three copies that can drift.
"""

import re
import subprocess
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Pattern, Tuple

LANGUAGE_EXTENSIONS: Dict[str, str] = {
    ".ts": "TypeScript",
    ".tsx": "TypeScript",
    ".js": "JavaScript",
    ".jsx": "JavaScript",
    ".mjs": "JavaScript",
    ".cjs": "JavaScript",
    ".py": "Python",
    ".go": "Go",
    ".swift": "Swift",
    ".kt": "Kotlin",
    ".kts": "Kotlin",
}

DEFAULT_EXCLUDE_DIRS = {
    ".git", "node_modules", "dist", "build", ".venv", "venv", "env",
    "__pycache__", ".next", ".nuxt", "target", "vendor", ".pytest_cache",
    "coverage", ".turbo", ".mypy_cache", ".ruff_cache", "Pods",
    "DerivedData", ".gradle",
}

TEST_PATH_HINT = re.compile(r"(^|[/_.-])(test|tests|spec|specs|__tests__)([/_.-]|$)", re.IGNORECASE)

TODO_PATTERN = re.compile(r"\b(TODO|FIXME|XXX|HACK)\b:?", re.IGNORECASE)

# Debug/leftover-diagnostic statements, by language. Intentionally narrow
# (anchored to real debug APIs) to keep the false-positive rate low.
DEBUG_STATEMENT_PATTERNS: Dict[str, Pattern] = {
    "Python": re.compile(r"\bprint\s*\(|\bpdb\.set_trace\s*\(|\bbreakpoint\s*\("),
    "JavaScript": re.compile(r"\bconsole\.(log|debug|warn)\s*\(|\bdebugger\s*;"),
    "TypeScript": re.compile(r"\bconsole\.(log|debug|warn)\s*\(|\bdebugger\s*;"),
    "Go": re.compile(r"\bfmt\.Println\s*\(|\bspew\.Dump\s*\("),
    "Swift": re.compile(r"\bprint\s*\("),
    "Kotlin": re.compile(r"\bprintln\s*\("),
}

# (label, pattern) — patterns are deliberately conservative (real key
# shapes / assignment-with-long-literal) to avoid flooding output with
# false positives on every `password: str` type annotation.
SECRET_PATTERNS: List[Tuple[str, Pattern]] = [
    ("AWS Access Key ID", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("Private key header", re.compile(r"-----BEGIN (RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----")),
    ("Slack token", re.compile(r"xox[baprs]-[0-9A-Za-z-]{10,}")),
    (
        "Hardcoded secret-like assignment",
        re.compile(
            r"(?i)\b(api[_-]?key|secret[_-]?key|access[_-]?token|auth[_-]?token|password)\b\s*[:=]\s*"
            r"[\"'][A-Za-z0-9/+_\-]{16,}[\"']"
        ),
    ),
]

# Matches a function/method definition's own line and captures its leading
# indentation (group 1) so callers can measure the block by watching for
# the next line at the same-or-lower indentation. Brace-delimited
# languages (Go/Swift/Kotlin/JS/TS) are measured by brace balance instead
# — see code_quality_checker.py — this map is only consulted for the
# indentation-based fallback used on Python.
FUNCTION_SIGNATURE_PATTERNS: Dict[str, Pattern] = {
    "Python": re.compile(r"^(\s*)(async\s+)?def\s+\w+\s*\("),
    "JavaScript": re.compile(
        r"^(\s*)(export\s+)?(default\s+)?(async\s+)?function\s*\*?\s*\w*\s*\("
        r"|^(\s*)(export\s+)?(const|let|var)\s+\w+\s*=\s*(async\s*)?\([^)]*\)\s*=>"
        r"|^(\s*)(async\s+)?\w+\s*\([^)]*\)\s*\{"
    ),
    "TypeScript": re.compile(
        r"^(\s*)(export\s+)?(default\s+)?(async\s+)?function\s*\*?\s*\w*\s*\("
        r"|^(\s*)(export\s+)?(const|let|var)\s+\w+\s*=\s*(async\s*)?\([^)]*\)\s*(:\s*[\w<>\[\], |]+\s*)?=>"
        r"|^(\s*)(public\s+|private\s+|protected\s+)?(async\s+)?\w+\s*\([^)]*\)\s*(:\s*[\w<>\[\], |]+\s*)?\{"
    ),
    "Go": re.compile(r"^func\s+(\([^)]*\)\s*)?\w+\s*\("),
    "Swift": re.compile(r"^(\s*)(public\s+|private\s+|internal\s+|fileprivate\s+)?(static\s+)?func\s+\w+\s*\("),
    "Kotlin": re.compile(r"^(\s*)(public\s+|private\s+|internal\s+)?(suspend\s+)?fun\s+\w+\s*\("),
}

BRACE_LANGUAGES = {"JavaScript", "TypeScript", "Go", "Swift", "Kotlin"}

DECISION_KEYWORD_PATTERN = re.compile(
    r"\b(if|else\s+if|elif|for|while|case|catch|except|when)\b|&&|\|\|"
)

BARE_EXCEPT_PATTERN = re.compile(r"^\s*except\s*:\s*$")
EMPTY_CATCH_PATTERN = re.compile(r"catch\s*(\([^)]*\))?\s*\{\s*\}")

SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2, "info": 3}
SEVERITY_ICON = {"high": "\U0001F534", "medium": "\U0001F7E1", "low": "\U0001F535", "info": "⚪"}


def detect_language(path: Path) -> Optional[str]:
    return LANGUAGE_EXTENSIONS.get(path.suffix.lower())


def is_test_path(path: Path) -> bool:
    return bool(TEST_PATH_HINT.search(str(path)))


def iter_source_files(root: Path, exclude_dirs: Optional[set] = None) -> Iterable[Path]:
    """Yield every source file under root whose extension we recognize.

    If root is itself a file, yields it (if recognized) rather than
    treating it as a directory — lets every script accept a single-file
    target as well as a directory/repo.
    """
    exclude = exclude_dirs if exclude_dirs is not None else DEFAULT_EXCLUDE_DIRS
    if root.is_file():
        if detect_language(root):
            yield root
        return
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if any(part in exclude for part in path.parts):
            continue
        if detect_language(path):
            yield path


def read_text_safely(path: Path) -> Optional[str]:
    try:
        return path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None


def run_git(args: List[str], cwd: Path) -> str:
    """Run git and return stdout. Raises subprocess.CalledProcessError on failure."""
    result = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout


def try_run_git(args: List[str], cwd: Path) -> Optional[str]:
    try:
        return run_git(args, cwd)
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def is_git_repo(path: Path) -> bool:
    return try_run_git(["rev-parse", "--is-inside-work-tree"], path) is not None


def resolve_base_ref(repo_path: Path) -> Optional[str]:
    """Pick a sensible diff base: the merge-base with a default branch if
    one exists, else the repo's very first commit. Returns None only if
    the repo has no commits at all (or isn't a git repo).

    A candidate is only used if its merge-base with HEAD is NOT HEAD
    itself — otherwise you're literally on that branch (e.g. checked out
    on "main" with no separate feature branch, which is the normal state
    of a freshly git-init'd repo, since modern git defaults the initial
    branch name to "main"), and "diff main...HEAD" would be a no-op empty
    diff even though there's real history to review. In that case we fall
    through to the root-commit fallback instead, which actually shows
    something.
    """
    head_sha = try_run_git(["rev-parse", "HEAD"], repo_path)
    head_sha = head_sha.strip() if head_sha else None

    for candidate in ("origin/main", "origin/master", "main", "master"):
        if try_run_git(["rev-parse", "--verify", "--quiet", candidate], repo_path) is not None:
            merge_base = try_run_git(["merge-base", candidate, "HEAD"], repo_path)
            if merge_base:
                merge_base = merge_base.strip()
                if head_sha is None or merge_base != head_sha:
                    return merge_base
    root_commit = try_run_git(["rev-list", "--max-parents=0", "HEAD"], repo_path)
    if root_commit and root_commit.strip():
        # A repo can have multiple root commits; take the first line.
        root_commit = root_commit.strip().splitlines()[0]
        if head_sha is None or root_commit != head_sha:
            return root_commit
    return None


def severity_icon(severity: str) -> str:
    return SEVERITY_ICON.get(severity, "⚪")


def sort_findings(findings: List[dict]) -> List[dict]:
    return sorted(findings, key=lambda f: (SEVERITY_ORDER.get(f.get("severity", "info"), 9), f.get("file", ""), f.get("line") or 0))


def max_severity(findings: List[dict]) -> Optional[str]:
    if not findings:
        return None
    return min((f.get("severity", "info") for f in findings), key=lambda s: SEVERITY_ORDER.get(s, 9))


def severity_meets_threshold(severity: str, threshold: str) -> bool:
    """True if `severity` is at least as severe as `threshold` (high > medium > low > info)."""
    return SEVERITY_ORDER.get(severity, 9) <= SEVERITY_ORDER.get(threshold, 9)
