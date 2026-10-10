"""Opt-in, secret-free attempt records for isolated Guide acceptance runs.

This module is inert unless the runner supplies a protected directory. It
never stores provider URLs, tokens, prompt text, image bytes, or raw upstream
responses.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import time
import uuid
from pathlib import Path
from typing import Optional


_SAFE_CODE = re.compile(r"[A-Z0-9_]{1,80}\Z")
_SAFE_STATE = re.compile(r"[A-Z_]{1,80}\Z")


class GuideDiagnosticError(RuntimeError):
    """The explicitly requested isolated diagnostic destination is unsafe."""


class GuideAttemptJournal:
    """Write one atomic, request-ID-addressed attempt record when enabled."""

    def __init__(self, directory: Optional[Path]) -> None:
        self._directory = directory

    def record(
        self,
        request_id: str,
        *,
        phase: str,
        upstream_attempt_state: str,
        upstream_attempts_started: int,
        failure_code: Optional[str] = None,
    ) -> None:
        if self._directory is None:
            return
        try:
            canonical_id = str(uuid.UUID(request_id))
        except (TypeError, ValueError) as exception:
            raise GuideDiagnosticError("diagnostic request ID is invalid") from exception
        if not _SAFE_STATE.fullmatch(phase) or not _SAFE_STATE.fullmatch(upstream_attempt_state):
            raise GuideDiagnosticError("diagnostic state is invalid")
        if not isinstance(upstream_attempts_started, int) or upstream_attempts_started < 0:
            raise GuideDiagnosticError("diagnostic attempt count is invalid")
        if failure_code is not None and not _SAFE_CODE.fullmatch(failure_code):
            raise GuideDiagnosticError("diagnostic failure code is invalid")

        directory = self._directory
        if directory.is_symlink() or not directory.is_dir():
            raise GuideDiagnosticError("diagnostic directory is invalid")
        mode = directory.stat().st_mode & 0o777
        if mode & 0o077:
            raise GuideDiagnosticError("diagnostic directory is not private")
        target = directory / (canonical_id + ".json")
        if target.exists() and (target.is_symlink() or not target.is_file()):
            raise GuideDiagnosticError("diagnostic record is invalid")
        document = {
            "format": 1,
            "kind": "auralink-guide-upstream-attempt",
            "requestId": canonical_id,
            "phase": phase,
            "upstreamAttemptState": upstream_attempt_state,
            "upstreamAttemptsStarted": upstream_attempts_started,
            "failureCode": failure_code,
            "updatedAtEpochMs": int(time.time() * 1000),
        }
        descriptor, temporary = tempfile.mkstemp(prefix=".guide-attempt.", dir=directory)
        try:
            os.fchmod(descriptor, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as output:
                json.dump(document, output, ensure_ascii=False, sort_keys=True)
                output.write("\n")
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, target)
        except BaseException:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
            raise
