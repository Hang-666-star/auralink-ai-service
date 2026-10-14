"""One startup-bound Assistant configuration, independent from Critic settings."""
from __future__ import annotations

import hashlib
import ipaddress
import json
import os
from pathlib import Path
import stat
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Capabilities(StrictModel):
    images: bool = False
    streaming: bool = True


class Model(StrictModel):
    id: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9._-]+$")
    provider: str = Field(min_length=1, max_length=80)
    model: str = Field(min_length=1, max_length=160)
    label: str = Field(default="", max_length=160)
    baseUrl: str
    keyFile: str
    capabilities: Capabilities = Field(default_factory=Capabilities)
    enabled: bool = True
    evidence: str = "CONFIGURED_NOT_VERIFIED"


class Ragflow(StrictModel):
    enabled: bool = False
    baseUrl: str = ""
    apiKeyFile: str = ""
    datasetIds: list[str] = Field(default_factory=list, max_length=20)
    version: Literal["v1-retrieval"] = "v1-retrieval"
    topN: int = Field(default=6, ge=1, le=10)
    timeoutMs: int = Field(default=15000, ge=100, le=30000)


class Limits(StrictModel):
    concurrency: int = Field(default=2, ge=1, le=8)
    connectMs: int = Field(default=5000, ge=100, le=10000)
    idleMs: int = Field(default=30000, ge=100, le=60000)
    chatMs: int = Field(default=120000, ge=100, le=180000)
    summaryMs: int = Field(default=60000, ge=100, le=120000)
    maxOutputBytes: int = Field(default=1048576, ge=1024, le=2097152)
    maxRequestBytes: int = Field(default=15000000, ge=1024, le=30000000)
    maxImageBytes: int = Field(default=10000000, ge=1, le=10000000)
    maxImagePixels: int = Field(default=32000000, ge=1, le=40000000)
    attemptCapacity: int = Field(default=10000, ge=10, le=100000)


class Settings(StrictModel):
    mode: Literal["disabled", "enabled"] = "disabled"
    allowLoopbackHttp: bool = False
    models: list[Model] = Field(default_factory=list, max_length=20)
    ragflow: Ragflow = Field(default_factory=Ragflow)
    limits: Limits = Field(default_factory=Limits)

    @model_validator(mode="after")
    def validate_endpoints(self):
        if len({m.id for m in self.models}) != len(self.models):
            raise ValueError("duplicate model ids")
        for url in [m.baseUrl for m in self.models] + ([self.ragflow.baseUrl] if self.ragflow.enabled else []):
            parsed = urlsplit(url)
            if parsed.username or parsed.password or parsed.query or parsed.fragment or not parsed.hostname:
                raise ValueError("invalid configured endpoint")
            try:
                loopback = ipaddress.ip_address(parsed.hostname).is_loopback
            except ValueError:
                loopback = False
            if parsed.scheme != "https" and not (self.allowLoopbackHttp and loopback and parsed.scheme == "http"):
                raise ValueError("HTTPS required except explicitly configured loopback fixtures")
        if self.ragflow.enabled and (not self.ragflow.datasetIds or not all(self.ragflow.datasetIds)):
            raise ValueError("RAG dataset allowlist required")
        return self


def private_secret(path: str) -> str:
    source = Path(path)
    meta = source.lstat()
    if not source.is_absolute() or not stat.S_ISREG(meta.st_mode) or meta.st_mode & 0o077:
        raise ValueError("secret file must be absolute, regular and owner-only")
    if meta.st_size > 8192:
        raise ValueError("secret too large")
    with source.open("rb") as handle:
        raw = handle.read(8193)
    value = raw.decode("utf-8").strip()
    if len(raw) > 8192 or not 16 <= len(value) <= 8192 or any(ord(c) <= 32 or ord(c) >= 127 for c in value):
        raise ValueError("invalid secret length or characters")
    return value


def strict_json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate configuration field")
            result[key] = value
        return result
    def invalid(value):
        raise ValueError("non-finite configuration")
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=invalid)


def load_configuration():
    """Failure affects Assistant only; never expose path, exception or secret."""
    path = os.getenv("AURALINK_ASSISTANT_CONFIG_FILE", "").strip()
    if not path:
        return None, "", {}, "", "DISABLED"
    try:
        source = Path(path)
        metadata = source.lstat()
        if not source.is_absolute() or not stat.S_ISREG(metadata.st_mode) or metadata.st_size > 65536:
            raise ValueError("invalid config file")
        with source.open("rb") as handle:
            raw = handle.read(65537)
        if len(raw) > 65536:
            raise ValueError("config too large")
        settings = Settings.model_validate(strict_json(raw))
        digest = hashlib.sha256(raw).hexdigest()
        if settings.mode == "disabled":
            return settings, digest, {}, "", "DISABLED"
        token = private_secret(os.environ.get("AURALINK_ASSISTANT_INTERNAL_TOKEN_FILE", ""))
        keys = {m.id: private_secret(m.keyFile) for m in settings.models if m.enabled}
        if settings.ragflow.enabled:
            keys["__ragflow"] = private_secret(settings.ragflow.apiKeyFile)
        return settings, digest, keys, token, "READY"
    except (OSError, ValueError, TypeError):
        return None, "", {}, "", "MISCONFIGURED"
