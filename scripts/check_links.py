#!/usr/bin/env python3
"""Check that relative markdown links in the top-level documents resolve.

The check is deliberately offline and checkout-only: it extracts `](target)`
destinations from README.md and SKILL.md, ignores external and anchor-only
targets, and verifies that every remaining path exists relative to the
repository root. A fragment is stripped before resolution, and a trailing slash
means "directory".

Usage::

    uv run python scripts/check_links.py
    uv run python scripts/check_links.py --root /path/to/checkout

Exit 0 when every relative link resolves, 1 when any target is missing.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from urllib.parse import unquote

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SOURCES = ("README.md", "SKILL.md")
LINK_RE = re.compile(r"\]\(([^)]+)\)")
SKIP_PREFIXES = ("http://", "https://", "mailto:", "tel:", "data:")


def _target_path(target: str) -> str | None:
    """Return the repo-relative path a markdown destination points at, or None."""
    value = target.strip()
    if value.startswith("<") and value.endswith(">"):
        value = value[1:-1].strip()
    # Drop an optional markdown title: ](path "title")
    if ' "' in value:
        value = value.split(' "', 1)[0]
    if not value or value.startswith("#"):
        return None
    if value.lower().startswith(SKIP_PREFIXES):
        return None
    path = unquote(value.split("#", 1)[0])
    if not path:
        return None
    # A leading slash is site-absolute in markdown; in this repository it means
    # the checkout root.
    return path.lstrip("/")


def check(root: Path, sources: tuple[str, ...] = DEFAULT_SOURCES) -> list[str]:
    """Return one message per missing relative target across *sources*."""
    problems: list[str] = []
    for name in sources:
        source = root / name
        if not source.is_file():
            continue
        text = source.read_text(encoding="utf-8")
        for raw in LINK_RE.findall(text):
            path = _target_path(raw)
            if path is None:
                continue
            resolved = root / path
            wanted = "directory" if path.endswith("/") else "file"
            exists = resolved.is_dir() if wanted == "directory" else resolved.exists()
            if not exists:
                problems.append(f"{name}: {raw.strip()} -> missing {wanted} {path}")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=REPO_ROOT,
        help="checkout to resolve links against (default: this repository)",
    )
    args = parser.parse_args(argv)

    problems = check(args.root)
    if problems:
        for problem in problems:
            print(problem, file=sys.stderr)
        print(f"{len(problems)} broken relative link(s)", file=sys.stderr)
        return 1

    checked = sum(
        len(LINK_RE.findall((args.root / name).read_text(encoding="utf-8")))
        for name in DEFAULT_SOURCES
        if (args.root / name).is_file()
    )
    print(f"{checked} markdown link(s) in {', '.join(DEFAULT_SOURCES)}: all relative targets resolve")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
