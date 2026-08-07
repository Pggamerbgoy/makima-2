#!/usr/bin/env python3
"""
scan_gaps.py — heuristic static scanner used by the codebase-gap-analysis skill.

This script does NOT decide what's actually wrong with a codebase — it just
surfaces candidate locations worth reasoning about, cheaply and deterministically,
so the model doesn't have to grep for the same 20 patterns by hand on every run.
The model is expected to read the JSON output, discard noise, and do the actual
judgment call about severity, priority, and whether something is a real gap.

Usage:
    python scan_gaps.py <repo_root> [--out gaps.json] [--ext py,js,ts,go]

Output: a JSON report grouped by category, each entry with file, line, snippet.
"""

import argparse
import json
import os
import re
import sys
from pathlib import Path

DEFAULT_EXCLUDE_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv", "dist", "build",
    ".next", ".nuxt", "target", "vendor", ".mypy_cache", ".pytest_cache",
    "coverage", ".tox", "egg-info",
}

# Each pattern: (category, compiled_regex, note)
# Kept intentionally simple/high-recall -- precision is the model's job, not this script's.
PATTERNS = {
    "todo_markers": [
        (re.compile(r"#\s*(TODO|FIXME|HACK|XXX)\b(.*)", re.IGNORECASE), "marker"),
        (re.compile(r"//\s*(TODO|FIXME|HACK|XXX)\b(.*)", re.IGNORECASE), "marker"),
    ],
    "broad_exception_handling": [
        (re.compile(r"^\s*except\s*:\s*$"), "bare except (Python) — swallows everything including KeyboardInterrupt/SystemExit"),
        (re.compile(r"^\s*except\s+Exception\s*:\s*$"), "overly broad except Exception"),
        (re.compile(r"\bcatch\s*\(\s*\)\s*\{"), "empty catch parameter (JS/TS) — likely swallowing errors"),
        (re.compile(r"catch\s*\([^)]*\)\s*\{\s*\}"), "empty catch block — error silently discarded"),
    ],
    "debug_leftovers": [
        (re.compile(r"^\s*print\("), "stray print() — likely debug leftover in Python"),
        (re.compile(r"console\.(log|debug)\("), "console.log/debug left in code"),
        (re.compile(r"\bdebugger;"), "debugger; statement left in JS"),
        (re.compile(r"\bpdb\.set_trace\(\)"), "pdb.set_trace() left in code"),
    ],
    "hardcoded_secrets_suspects": [
        (re.compile(r"(api[_-]?key|secret|password|token)\s*=\s*[\"'][A-Za-z0-9\-_.]{8,}[\"']", re.IGNORECASE), "possible hardcoded credential"),
        (re.compile(r"sk-[A-Za-z0-9]{16,}"), "string matching an API-key-like pattern"),
        (re.compile(r"AKIA[0-9A-Z]{16}"), "string matching an AWS access key ID pattern"),
    ],
    "missing_input_validation_suspects": [
        (re.compile(r"request\.(GET|POST|args|form|json)\[[\"'][^\"']+[\"']\]"), "direct dict-style access to request data without .get()/validation"),
        (re.compile(r"os\.system\("), "os.system() call — check for unsanitized input / shell injection risk"),
        (re.compile(r"subprocess\.\w+\(.*shell\s*=\s*True"), "subprocess with shell=True — check for injection risk"),
        (re.compile(r"eval\("), "eval() call — check what input reaches it"),
        (re.compile(r"\bexec\("), "exec() call — check what input reaches it"),
    ],
    "empty_or_stub_functions": [
        (re.compile(r"^\s*def\s+\w+\([^)]*\):\s*$"), "function definition with no body on this line — check next line for pass/...  only"),
        (re.compile(r"^\s*pass\s*$"), "bare pass — possible stub"),
        (re.compile(r"raise\s+NotImplementedError"), "explicit NotImplementedError — confirmed stub"),
        (re.compile(r"^\s*//\s*not implemented", re.IGNORECASE), "comment marking unimplemented code"),
    ],
    "commented_out_code_blocks": [
        (re.compile(r"^\s*#\s*(def|class|import|if|for|while)\s"), "commented-out Python statement"),
        (re.compile(r"^\s*//\s*(function|const|let|var|if|for|while|import)\s"), "commented-out JS/TS statement"),
    ],
}

# File extensions we scan by default and their comment-style hints.
DEFAULT_EXTENSIONS = {"py", "js", "jsx", "ts", "tsx", "go", "java", "rb", "php", "c", "cpp", "h", "cs"}


def iter_source_files(root: Path, extensions: set):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in DEFAULT_EXCLUDE_DIRS and not d.startswith(".")]
        for fname in filenames:
            ext = fname.rsplit(".", 1)[-1].lower() if "." in fname else ""
            if ext in extensions:
                yield Path(dirpath) / fname


def scan_file(path: Path, root: Path) -> dict:
    findings = {}
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return findings
    lines = text.splitlines()
    rel = str(path.relative_to(root))

    for category, patterns in PATTERNS.items():
        for lineno, line in enumerate(lines, 1):
            for regex, note in patterns:
                if regex.search(line):
                    findings.setdefault(category, []).append({
                        "file": rel,
                        "line": lineno,
                        "snippet": line.strip()[:200],
                        "note": note,
                    })
    return findings


def find_untested_modules(root: Path, extensions: set) -> list:
    """Very rough heuristic: a src file with no similarly-named test file anywhere in the repo."""
    src_files = []
    test_stems = set()
    for f in iter_source_files(root, extensions):
        name = f.stem
        if name.startswith("test_") or name.endswith("_test") or name.endswith(".test") or name.endswith(".spec"):
            core = re.sub(r"^test_|_test$|\.test$|\.spec$", "", name)
            test_stems.add(core)
        else:
            src_files.append(f)

    untested = []
    for f in src_files:
        if f.stem in ("__init__", "index", "setup", "conftest"):
            continue
        if f.stem not in test_stems:
            untested.append(str(f.relative_to(root)))
    return sorted(untested)


def merge_findings(all_findings: dict, new: dict):
    for category, items in new.items():
        all_findings.setdefault(category, []).extend(items)


def main():
    parser = argparse.ArgumentParser(description="Heuristic static scan for candidate code gaps.")
    parser.add_argument("repo_root", help="Path to the repository root to scan.")
    parser.add_argument("--out", default="gaps.json", help="Where to write the JSON report.")
    parser.add_argument("--ext", default=None, help="Comma-separated list of extensions to scan (default: common set).")
    parser.add_argument("--max-per-category", type=int, default=200, help="Cap findings per category to avoid a wall of noise.")
    args = parser.parse_args()

    root = Path(args.repo_root).resolve()
    if not root.exists():
        print(f"[ERROR] Path does not exist: {root}", file=sys.stderr)
        sys.exit(1)

    extensions = set(e.strip().lstrip(".") for e in args.ext.split(",")) if args.ext else DEFAULT_EXTENSIONS

    all_findings = {}
    file_count = 0
    for f in iter_source_files(root, extensions):
        file_count += 1
        merge_findings(all_findings, scan_file(f, root))

    for category in all_findings:
        all_findings[category] = all_findings[category][: args.max_per_category]

    untested = find_untested_modules(root, extensions)

    report = {
        "repo_root": str(root),
        "files_scanned": file_count,
        "categories": all_findings,
        "possibly_untested_modules": untested[:200],
        "note": (
            "This is a heuristic first pass, not a verdict. High false-positive rate is "
            "expected and acceptable -- the consuming model is responsible for filtering "
            "noise, confirming real issues by reading surrounding context, and discarding "
            "anything that doesn't hold up."
        ),
    }

    out_path = Path(args.out)
    out_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    total = sum(len(v) for v in all_findings.values())
    print(f"[scan_gaps] Scanned {file_count} files under {root}")
    print(f"[scan_gaps] {total} raw candidate findings across {len(all_findings)} categories")
    print(f"[scan_gaps] {len(untested)} source files with no obviously matching test file")
    print(f"[scan_gaps] Report written to {out_path}")


if __name__ == "__main__":
    main()
