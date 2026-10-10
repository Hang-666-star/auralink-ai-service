"""Configuration for the dedicated Guide Service."""

from __future__ import annotations

from dataclasses import dataclass
import ipaddress
import os
from pathlib import Path
import stat
from typing import Mapping, MutableMapping, Optional
from urllib.parse import urlsplit

from .env_loader import EnvFileError, load_backend_env, project_root


DEFAULT_CONNECT_TIMEOUT_MS = 5_000
DEFAULT_READ_TIMEOUT_MS = 120_000
DEFAULT_TOTAL_TIMEOUT_MS = 250_000
DEFAULT_RETRY_BACKOFF_MS = 200
DEFAULT_SERVICE_PORT = 5_003
MAX_REQUEST_BYTES = 128 * 1024
MAX_UPSTREAM_RESPONSE_BYTES = 256 * 1024
SUPPORTED_QWEN_MODEL = "qwen3-vl-plus"


class GuideConfigurationError(ValueError):
    """A safe configuration error that never includes configuration values."""


def _positive_int(values: Mapping[str, str], key: str, default: int) -> int:
    raw = values.get(key, str(default)).strip()
    try:
        parsed = int(raw)
    except (TypeError, ValueError) as exception:
        raise GuideConfigurationError(key + " must be a positive integer") from exception
    if parsed <= 0:
        raise GuideConfigurationError(key + " must be a positive integer")
    return parsed


def _loopback_host(value: str) -> str:
    host = value.strip()
    try:
        address = ipaddress.ip_address(host)
    except ValueError as exception:
        raise GuideConfigurationError(
            "AURALINK_GUIDE_SERVICE_HOST must be a literal loopback address"
        ) from exception
    if str(address) not in {"127.0.0.1", "::1"}:
        raise GuideConfigurationError(
            "AURALINK_GUIDE_SERVICE_HOST must be 127.0.0.1 or ::1"
        )
    return str(address)


def _validate_service_url(values: Mapping[str, str], host: str, port: int) -> None:
    configured = values.get("GUIDE_AI_SERVICE_URL", "").strip()
    raw = configured or (
        "http://[::1]:" + str(port)
        if host == "::1"
        else "http://" + host + ":" + str(port)
    )
    try:
        parsed = urlsplit(raw)
        parsed_host = parsed.hostname
        canonical_host = _loopback_host(parsed_host or "")
        parsed_port = parsed.port if parsed.port is not None else 80
    except (ValueError, GuideConfigurationError) as exception:
        raise GuideConfigurationError(
            "GUIDE_AI_SERVICE_URL must match the configured loopback bind address"
        ) from exception
    if (
        parsed.scheme != "http"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
        or canonical_host != host
        or parsed_port != port
    ):
        raise GuideConfigurationError(
            "GUIDE_AI_SERVICE_URL must match the configured loopback bind address"
        )


@dataclass(frozen=True)
class GuideServiceConfig:
    service_host: str
    service_port: int
    internal_token: str
    qwen_api_key: str
    qwen_base_url: str
    qwen_model: str
    connect_timeout_ms: int
    read_timeout_ms: int
    total_timeout_ms: int
    max_request_bytes: int = MAX_REQUEST_BYTES
    max_upstream_response_bytes: int = MAX_UPSTREAM_RESPONSE_BYTES
    retry_backoff_seconds: float = DEFAULT_RETRY_BACKOFF_MS / 1000.0
    diagnostic_dir: Optional[Path] = None

    @property
    def provider_configured(self) -> bool:
        return bool(self.qwen_api_key and self.qwen_base_url)

    @property
    def model_configured(self) -> bool:
        return self.qwen_model == SUPPORTED_QWEN_MODEL

    @property
    def internal_auth_configured(self) -> bool:
        return bool(self.internal_token)

    @classmethod
    def load(
        cls,
        environ: Optional[MutableMapping[str, str]] = None,
        root: Optional[Path] = None,
    ) -> "GuideServiceConfig":
        values = environ if environ is not None else os.environ
        try:
            load_backend_env(values, root or project_root())
        except (EnvFileError, OSError, UnicodeError) as exception:
            raise GuideConfigurationError(
                "backend/.env could not be loaded safely"
            ) from exception
        host = _loopback_host(
            values.get("AURALINK_GUIDE_SERVICE_HOST", "127.0.0.1")
        )
        port = _positive_int(
            values, "AURALINK_GUIDE_SERVICE_PORT", DEFAULT_SERVICE_PORT
        )
        if port > 65_535:
            raise GuideConfigurationError(
                "AURALINK_GUIDE_SERVICE_PORT must be a valid TCP port"
            )
        _validate_service_url(values, host, port)
        diagnostic_dir = _diagnostic_dir(values)
        return cls(
            service_host=host,
            service_port=port,
            internal_token=values.get("AURALINK_GUIDE_INTERNAL_TOKEN", ""),
            qwen_api_key=values.get("QWEN_API_KEY", ""),
            qwen_base_url=values.get("QWEN_BASE_URL", ""),
            qwen_model=values.get("QWEN_MODEL", ""),
            connect_timeout_ms=_positive_int(
                values,
                "AURALINK_GUIDE_CONNECT_TIMEOUT_MS",
                DEFAULT_CONNECT_TIMEOUT_MS,
            ),
            read_timeout_ms=_positive_int(
                values,
                "AURALINK_GUIDE_READ_TIMEOUT_MS",
                DEFAULT_READ_TIMEOUT_MS,
            ),
            total_timeout_ms=_positive_int(
                values,
                "AURALINK_GUIDE_TOTAL_TIMEOUT_MS",
                DEFAULT_TOTAL_TIMEOUT_MS,
            ),
            retry_backoff_seconds=_positive_int(
                values,
                "AURALINK_GUIDE_RETRY_BACKOFF_MS",
                DEFAULT_RETRY_BACKOFF_MS,
            ) / 1000.0,
            diagnostic_dir=diagnostic_dir,
        )


def _diagnostic_dir(values: Mapping[str, str]) -> Optional[Path]:
    """Validate the optional, acceptance-run-only attempt journal directory."""

    raw = values.get("AURALINK_GUIDE_DIAGNOSTIC_DIR", "").strip()
    if not raw:
        return None
    if "\x00" in raw:
        raise GuideConfigurationError("AURALINK_GUIDE_DIAGNOSTIC_DIR is invalid")
    path = Path(raw)
    if not path.is_absolute() or path.is_symlink() or not path.is_dir():
        raise GuideConfigurationError("AURALINK_GUIDE_DIAGNOSTIC_DIR is invalid")
    if stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise GuideConfigurationError("AURALINK_GUIDE_DIAGNOSTIC_DIR must be private")
    return path
