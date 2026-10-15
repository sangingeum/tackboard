"""``python -m tackboard`` — entry point for the CLI.

The command-line surface (argument parsing, output formatting, exit codes) is
delivered by a later leg; this module stays thin on purpose.
"""

from __future__ import annotations

import sys


def main() -> int:
    """Run the tackboard CLI. The CLI itself is not part of this leg."""
    sys.stderr.write(
        "tackboard CLI is not implemented yet: this build provides the domain "
        "library only.\n"
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
