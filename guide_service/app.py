"""Loopback-only Flask application for the internal Painting Guide contract."""

from __future__ import annotations

import hmac
import json
import os
import sys
from typing import Any, Optional

from flask import Flask, jsonify, request
from werkzeug.exceptions import BadRequest, RequestEntityTooLarge

from .config import GuideConfigurationError, GuideServiceConfig
from .diagnostics import GuideAttemptJournal, GuideDiagnosticError
from .qwen_client import QwenClientError, QwenGuideClient
from .schema import GuideSchemaError, ValidatedGuideRequest, validate_guide_request


INTERNAL_TOKEN_HEADER = "X-Auralink-Internal-Token"


def _failure(request_id: Optional[str], code: str, message: str, status: int):
    return (
        jsonify(
            {
                "requestId": request_id,
                "status": "FAILED",
                "result": None,
                "error": {"code": code, "message": message},
            }
        ),
        status,
    )


def _safe_request_id(value: Any) -> Optional[str]:
    if not isinstance(value, dict):
        return None
    candidate = value.get("requestId")
    if isinstance(candidate, str) and len(candidate) <= 36:
        return candidate
    return None


def _qwen_http_status(code: str) -> int:
    return {
        "PROVIDER_NOT_CONFIGURED": 503,
        "UPSTREAM_TIMEOUT": 504,
        "TIMEOUT": 504,
        "UPSTREAM_RATE_LIMIT": 503,
        "UPSTREAM_REJECTED": 502,
        "INVALID_RESULT": 502,
        "INVALID_PROVIDER_RESPONSE": 502,
        "UPSTREAM_UNAVAILABLE": 503,
        "UPSTREAM_SERVER_ERROR": 503,
    }.get(code, 503)


def create_app(
    config: GuideServiceConfig,
    qwen_client: Optional[QwenGuideClient] = None,
) -> Flask:
    app = Flask(__name__)
    app.config.update(
        DEBUG=False,
        TESTING=False,
        MAX_CONTENT_LENGTH=config.max_request_bytes,
        PROPAGATE_EXCEPTIONS=False,
    )
    journal = GuideAttemptJournal(config.diagnostic_dir)

    @app.errorhandler(RequestEntityTooLarge)
    def request_too_large(_error: RequestEntityTooLarge):
        return _failure(None, "REQUEST_TOO_LARGE", "Guide request is too large", 413)

    @app.get("/health")
    def health():
        return jsonify(
            {
                "status": "UP",
                "providerConfigured": config.provider_configured,
                "modelConfigured": config.model_configured,
            }
        )

    # This diagnostic endpoint exists only in the local deterministic stub
    # profile.  It exposes counters, never request bodies, provider settings,
    # or credentials; the production Guide service does not register it.
    if os.environ.get("AURALINK_GUIDE_LOCAL_STUB") == "1":
        @app.get("/local-stub/state")
        def local_stub_state():
            state_path = os.environ.get("AURALINK_GUIDE_STUB_STATE_FILE", "")
            try:
                with open(state_path, "r", encoding="utf-8") as source:
                    state = json.load(source)
                return jsonify({
                    "guideRequests": int(state.get("guide_requests", 0)),
                    "externalProviderCalls": int(state.get("external_provider_calls", 0)),
                })
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                return _failure(None, "LOCAL_STUB_STATE_UNAVAILABLE", "Local stub state is unavailable", 500)

    @app.post("/v1/guide/generate")
    def generate():
        if not config.internal_auth_configured:
            return _failure(
                None,
                "SERVICE_NOT_CONFIGURED",
                "Guide Service internal authentication is not configured",
                503,
            )
        supplied_token = request.headers.get(INTERNAL_TOKEN_HEADER, "")
        if not supplied_token or not hmac.compare_digest(
            supplied_token.encode("utf-8"), config.internal_token.encode("utf-8")
        ):
            return _failure(
                None,
                "UNAUTHORIZED_INTERNAL_CALL",
                "Internal authentication failed",
                401,
            )
        if not request.is_json:
            return _failure(None, "INVALID_REQUEST", "JSON request body is required", 400)
        try:
            raw_request = request.get_json(force=False, silent=False)
        except BadRequest:
            return _failure(None, "INVALID_REQUEST", "Malformed JSON request", 400)
        request_id = _safe_request_id(raw_request)
        try:
            validated: ValidatedGuideRequest = validate_guide_request(raw_request)
        except GuideSchemaError as exception:
            return _failure(request_id, exception.code, str(exception), 400)
        attempts_started = 0
        current_state = "NOT_STARTED"

        def observe(state: str, attempts: int) -> None:
            nonlocal attempts_started, current_state
            attempts_started = attempts
            current_state = state
            journal.record(
                validated.request_id,
                phase="UPSTREAM",
                upstream_attempt_state=state,
                upstream_attempts_started=attempts,
            )

        try:
            journal.record(
                validated.request_id,
                phase="VALIDATED",
                upstream_attempt_state=current_state,
                upstream_attempts_started=attempts_started,
            )
            client = qwen_client or QwenGuideClient(config, attempt_observer=observe)
            result = client.generate(validated)
        except QwenClientError as exception:
            try:
                journal.record(
                    validated.request_id,
                    phase="FAILED",
                    upstream_attempt_state=current_state,
                    upstream_attempts_started=attempts_started,
                    failure_code=exception.code,
                )
            except GuideDiagnosticError:
                return _failure(
                    validated.request_id,
                    "INTERNAL_SERVICE_ERROR",
                    "Guide Service could not retain diagnostic state",
                    500,
                )
            return _failure(
                validated.request_id,
                exception.code,
                str(exception),
                _qwen_http_status(exception.code),
            )
        except GuideDiagnosticError:
            return _failure(
                validated.request_id,
                "INTERNAL_SERVICE_ERROR",
                "Guide Service could not retain diagnostic state",
                500,
            )
        except Exception:
            return _failure(
                validated.request_id,
                "INTERNAL_SERVICE_ERROR",
                "Guide Service could not complete the request",
                500,
            )
        try:
            journal.record(
                validated.request_id,
                phase="COMPLETED",
                upstream_attempt_state=current_state,
                upstream_attempts_started=attempts_started,
            )
        except GuideDiagnosticError:
            return _failure(
                validated.request_id,
                "INTERNAL_SERVICE_ERROR",
                "Guide Service could not retain diagnostic state",
                500,
            )
        return jsonify(
            {
                "requestId": validated.request_id,
                "status": "SUCCESS",
                "result": result.payload,
                "error": None,
            }
        )

    return app


def main() -> int:
    try:
        config = GuideServiceConfig.load()
    except GuideConfigurationError as exception:
        print("GUIDE_SERVICE_CONFIGURATION_ERROR: " + str(exception), file=sys.stderr)
        return 2
    app = create_app(config)
    app.run(
        host=config.service_host,
        port=config.service_port,
        debug=False,
        use_reloader=False,
        threaded=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
