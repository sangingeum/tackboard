"""Project and database resolution (spec section 5).

Precedence: ``--db`` > ``TACKBOARD_DB`` > nearest ``.tackboard.toml`` walking up
from the current directory.
"""

from __future__ import annotations

import re
import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .errors import NotFoundError, UsageError, ValidationError

ENV_DB = "TACKBOARD_DB"
ENV_AGENT = "TACKBOARD_AGENT"
CONFIG_FILENAME = ".tackboard.toml"
CONFIG_TABLE = "tackboard"

#: Agent identifiers are advisory labels, but they are still format-checked.
AGENT_ID_RE = re.compile(r"[A-Za-z0-9._:-]{1,64}\Z")


def _resolve_path(value: str | Path, base: Path) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = base / path
    return path.resolve()


def find_config_file(start: Path) -> Path | None:
    """Return the nearest ``.tackboard.toml`` at ``start`` or above it."""
    current = start.resolve()
    candidates = [current, *current.parents]
    for directory in candidates:
        candidate = directory / CONFIG_FILENAME
        if candidate.is_file():
            return candidate
    return None


def load_config(path: Path) -> dict[str, Any]:
    """Parse a ``.tackboard.toml`` file."""
    try:
        with path.open("rb") as handle:
            return tomllib.load(handle)
    except tomllib.TOMLDecodeError as exc:
        raise ValidationError(f"Cannot parse {path}: {exc}") from exc
    except OSError as exc:
        raise NotFoundError(f"Cannot read {path}: {exc}") from exc


def resolve_database(
    explicit: str | None,
    *,
    cwd: Path,
    env: Mapping[str, str],
) -> Path:
    """Resolve the project database path (spec 5.2).

    Relative paths in ``--db`` and ``TACKBOARD_DB`` resolve against ``cwd``;
    paths inside ``.tackboard.toml`` resolve against that file's directory.
    """
    if explicit:
        return _resolve_path(explicit, cwd)

    from_env = env.get(ENV_DB)
    if from_env:
        return _resolve_path(from_env, cwd)

    config_path = find_config_file(cwd)
    if config_path is not None:
        config = load_config(config_path)
        section = config.get(CONFIG_TABLE)
        value: Any = section.get("db") if isinstance(section, Mapping) else None
        if not isinstance(value, str) or not value.strip():
            raise NotFoundError(
                f"No database configured in {config_path}. "
                f"Add a [{CONFIG_TABLE}] table with a 'db' key, or set {ENV_DB}."
            )
        return _resolve_path(value, config_path.parent)

    raise NotFoundError(
        f"No project database configured. Run 'tack init --db PATH' or set {ENV_DB}."
    )


def validate_agent_id(value: str) -> str:
    """Validate an agent identifier against ``[A-Za-z0-9._:-]{1,64}``."""
    if not AGENT_ID_RE.fullmatch(value):
        raise UsageError(
            f"Invalid agent identifier {value!r}: "
            "use 1-64 characters from [A-Za-z0-9._:-]."
        )
    return value


def resolve_actor(explicit: str | None, *, env: Mapping[str, str]) -> str:
    """Resolve the acting agent: ``--agent`` wins, else ``TACKBOARD_AGENT``."""
    if explicit:
        return validate_agent_id(explicit)
    from_env = env.get(ENV_AGENT)
    if from_env:
        return validate_agent_id(from_env)
    raise UsageError(f"No actor. Pass --agent or set {ENV_AGENT}.")


__all__ = [
    "AGENT_ID_RE",
    "CONFIG_FILENAME",
    "CONFIG_TABLE",
    "ENV_AGENT",
    "ENV_DB",
    "find_config_file",
    "load_config",
    "resolve_actor",
    "resolve_database",
    "validate_agent_id",
]
