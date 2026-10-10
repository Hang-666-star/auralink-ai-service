"""Safe, minimal loader for the project's private ``backend/.env`` file.

This deliberately implements only properties-style ``KEY=VALUE`` parsing.  It
does not execute shell syntax, interpolate variables, or print loaded values.
"""

from __future__ import annotations

import os
from pathlib import Path
import re
from typing import Mapping, MutableMapping, Optional


_KEY_PATTERN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
_MAX_ENV_BYTES = 1024 * 1024
_ENV_FILE_VARIABLE = "AURALINK_ENV_FILE"
_SHARED_UNQUOTED_KEYS = {
    "AURALINK_GUIDE_ENABLED",
    "AURALINK_GUIDE_CONNECT_TIMEOUT_MS",
    "AURALINK_GUIDE_READ_TIMEOUT_MS",
    "AURALINK_GUIDE_TOTAL_TIMEOUT_MS",
    "AURALINK_GUIDE_INTERNAL_READ_TIMEOUT_MS",
    "AURALINK_GUIDE_RETRY_BACKOFF_MS",
    "AURALINK_GUIDE_MAX_CONCURRENT_GENERATIONS",
    "AURALINK_GUIDE_USER_GENERATION_LIMIT",
    "AURALINK_GUIDE_USER_GENERATION_WINDOW_SECONDS",
    "AURALINK_GUIDE_GLOBAL_GENERATION_LIMIT",
    "AURALINK_GUIDE_GLOBAL_GENERATION_WINDOW_SECONDS",
    "AURALINK_GUIDE_MAX_KNOWLEDGE_ITEMS",
    "AURALINK_GUIDE_MAX_KNOWLEDGE_CHARS",
    "AURALINK_GUIDE_SERVICE_PORT",
}


class EnvFileError(ValueError):
    """Raised when the private environment file cannot be parsed safely."""


def project_root() -> Path:
    """Return the project root containing ``guide_service`` and ``backend``."""

    return Path(__file__).resolve().parents[1]


def backend_env_path(
    root: Optional[Path] = None,
    environ: Optional[Mapping[str, str]] = None,
) -> Path:
    """Return the one private backend configuration file for this process.

    An isolated release may keep Guide source outside the server-local checkout
    while still reading that checkout's private ``backend/.env``.  The runner
    supplies this explicit path only in the Guide process; it remains a data
    file, never shell input, and must still pass ``load_env_file``'s regular
    file and symlink checks.
    """
    values = environ if environ is not None else os.environ
    configured = values.get(_ENV_FILE_VARIABLE, "").strip()
    if not configured:
        return (root or project_root()) / "backend" / ".env"
    if "\x00" in configured:
        raise EnvFileError("AURALINK_ENV_FILE is invalid")
    candidate = Path(configured)
    if not candidate.is_absolute():
        raise EnvFileError("AURALINK_ENV_FILE must be absolute")
    return candidate


def parse_env_text(text: str) -> dict[str, str]:
    """Parse inert ``KEY=VALUE`` text without interpolation or shell syntax."""

    values: dict[str, str] = {}
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise EnvFileError(
                "backend/.env contains a malformed entry on line "
                + str(line_number)
            )
        raw_key, raw_value = line.split("=", 1)
        key = raw_key.strip()
        if not _KEY_PATTERN.fullmatch(key):
            raise EnvFileError(
                "backend/.env contains an invalid variable name on line "
                + str(line_number)
            )
        value = raw_value.strip()
        starts_quoted = value[:1] in ("'", '"')
        ends_quoted = value[-1:] in ("'", '"')
        if starts_quoted or ends_quoted:
            if len(value) < 2 or value[0] != value[-1]:
                raise EnvFileError(
                    "backend/.env contains an unmatched quote on line "
                    + str(line_number)
                )
            if key in _SHARED_UNQUOTED_KEYS:
                raise EnvFileError(
                    "shared scalar Guide settings must be unquoted on line "
                    + str(line_number)
                )
            # Matching outer quotes are data delimiters only.  Backslash
            # escapes, interpolation, and shell evaluation remain unsupported.
            value = value[1:-1]
        values[key] = value
    return values


def load_env_file(
    path: Path,
    environ: Optional[MutableMapping[str, str]] = None,
) -> Mapping[str, str]:
    """Load missing values into ``environ``; process values always take priority."""

    target = environ if environ is not None else os.environ
    candidate = Path(path)
    if candidate.is_symlink() or not candidate.is_file():
        raise EnvFileError("backend/.env must be a regular non-symlink file")
    size = candidate.stat().st_size
    if size > _MAX_ENV_BYTES:
        raise EnvFileError("backend/.env exceeds the safe size limit")
    parsed = parse_env_text(candidate.read_text(encoding="utf-8"))
    for key, value in parsed.items():
        target.setdefault(key, value)
    return parsed


def load_backend_env(
    environ: Optional[MutableMapping[str, str]] = None,
    root: Optional[Path] = None,
) -> Mapping[str, str]:
    return load_env_file(backend_env_path(root, environ), environ)
