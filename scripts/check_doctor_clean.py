#!/usr/bin/env python3
"""Fail unless a ``tack doctor --json`` report is clean.

The migration matrix uses this after ``tack migrate``: a migrated board must
have no errors and no warnings. The report is read from a file (the CI step
redirects doctor's stdout there) so the check itself never parses a pipe.

Usage:
    python scripts/check_doctor_clean.py /tmp/doctor.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("report", type=Path)
    args = parser.parse_args(argv)
    report = json.loads(args.report.read_text(encoding="utf-8"))
    errors = report.get("errors", [])
    warnings = report.get("warnings", [])
    if report.get("ok") is not True or errors or warnings:
        print(json.dumps(report, indent=2), file=sys.stderr)
        return 1
    print(f"doctor is clean: {report.get('schema_version')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
