#!/usr/bin/env python3
"""Regenerate the five README screenshots from a running demo board.

Serve the deterministic seed first::

    uv run python scripts/demo_seed.py --db /tmp/tackboard-demo.sqlite3
    uv run tack serve --db /tmp/tackboard-demo.sqlite3 --port 8765

Then run this script against that server::

    uv run python scripts/capture_demo_screenshots.py

Every image uses the same viewport size and device scale factor, so the five
screenshots agree with each other and with the seed board. The script refuses
to overwrite an image whose capture failed; Chrome writes each PNG and the
script checks the PNG signature, the pixel dimensions and a minimum byte size
before it reports success.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

#: One viewport for every shot: width, height and device scale factor.
VIEWPORT = (1440, 1200)
SCALE = 1
MIN_BYTES = 20_000

#: PNG signature, then the IHDR width and height as big-endian 32-bit words.
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


@dataclass(frozen=True)
class Shot:
    """One screenshot: its file name and the page path it captures."""

    name: str
    path: str
    description: str


SHOTS = (
    Shot("demo-board.png", "/board", "board view with the project memo"),
    Shot("demo-graph.png", "/graph", "full dependency graph"),
    Shot(
        "demo-graph-active.png",
        "/graph?hide_closed=1",
        "graph with done and cancelled nodes hidden",
    ),
    Shot(
        "demo-task-inprogress.png",
        "/task/T-000004",
        "in-progress task claimed by backend-agent",
    ),
    Shot(
        "demo-task-blocked.png",
        "/task/T-000010",
        "blocked task with two unfinished prerequisites",
    ),
)


class CaptureError(RuntimeError):
    """A screenshot did not arrive as a usable PNG."""


def png_dimensions(path: Path) -> tuple[int, int]:
    """Return the width and height from a PNG's IHDR chunk."""
    header = path.read_bytes()[:24]
    if len(header) < 24 or not header.startswith(PNG_SIGNATURE):
        raise CaptureError(f"{path} is not a PNG")
    if header[12:16] != b"IHDR":
        raise CaptureError(f"{path} has no IHDR chunk")
    width = int.from_bytes(header[16:20], "big")
    height = int.from_bytes(header[20:24], "big")
    return width, height


def check_shot(path: Path) -> None:
    """Fail when a capture is missing, tiny or the wrong size."""
    if not path.is_file():
        raise CaptureError(f"{path} was not written")
    size = path.stat().st_size
    if size < MIN_BYTES:
        raise CaptureError(f"{path} is only {size} bytes")
    dimensions = png_dimensions(path)
    if dimensions != VIEWPORT:
        raise CaptureError(f"{path} is {dimensions[0]}x{dimensions[1]}, "
                           f"expected {VIEWPORT[0]}x{VIEWPORT[1]}")


def capture(chrome: str, base_url: str, shot: Shot, target: Path) -> None:
    """Capture one page with headless Chrome."""
    command = [
        chrome,
        "--headless=new",
        "--disable-gpu",
        "--disable-dev-shm-usage",
        "--hide-scrollbars",
        "--no-first-run",
        f"--force-device-scale-factor={SCALE}",
        f"--window-size={VIEWPORT[0]},{VIEWPORT[1]}",
        "--virtual-time-budget=4000",
        f"--screenshot={target}",
        f"{base_url.rstrip('/')}{shot.path}",
    ]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        raise CaptureError(
            f"chrome failed for {shot.path} ({result.returncode}):\n"
            f"{result.stdout}{result.stderr}"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8765",
                        help="running tack serve base URL")
    parser.add_argument("--out-dir", type=Path, default=ROOT / "images",
                        help="directory that receives the five PNGs")
    parser.add_argument("--chrome", default=None,
                        help="Chrome binary (default: search PATH)")
    args = parser.parse_args(argv)

    chrome = args.chrome or shutil.which("google-chrome") or shutil.which(
        "google-chrome-stable") or shutil.which("chromium")
    if not chrome:
        raise CaptureError("no google-chrome, google-chrome-stable or chromium "
                           "binary found on PATH")

    args.out_dir.mkdir(parents=True, exist_ok=True)
    for shot in SHOTS:
        target = args.out_dir / shot.name
        capture(chrome, args.base_url, shot, target)
        check_shot(target)
        width, height = png_dimensions(target)
        print(f"{shot.name}: {width}x{height}, {target.stat().st_size} bytes "
              f"— {shot.description}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except CaptureError as error:
        print(f"capture failed: {error}", file=sys.stderr)
        raise SystemExit(1) from error
