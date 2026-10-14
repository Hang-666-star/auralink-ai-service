from __future__ import annotations
import asyncio
import json
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import ValidationError

from .execution import Runtime
from .providers import ProviderFailure, validate_images
from .retrieval import retrieve
from .schemas import Execution, Retrieval

router = APIRouter(prefix="/internal/v1/assistant", tags=["private-assistant"])


def runtime(request):
    state = request.app.state.assistant_runtime
    state.authenticate(request.headers.get("authorization"))
    return state


async def payload(request, state, schema):
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > state.settings.limits.maxRequestBytes:
            raise HTTPException(413, "ASSISTANT_REQUEST_TOO_LARGE")
    try:
        return schema.model_validate(json.loads(raw))
    except (ValueError, ValidationError):
        # Never return Pydantic input values (could contain images/private history).
        raise HTTPException(422, "ASSISTANT_INVALID_REQUEST") from None


@router.get("/models")
async def models(request: Request):
    return runtime(request).models()


@router.get("/readiness")
async def readiness(request: Request):
    state = runtime(request)
    return {"contractVersion": 1, "state": state.state, "configSha256": state.digest}


@router.post("/retrieval")
async def retrieval(request: Request):
    state = runtime(request)
    command = await payload(request, state, Retrieval)
    state.bind(command.configSha256)
    if state.retrieval_active >= state.settings.limits.concurrency:
        raise HTTPException(429, "ASSISTANT_RETRIEVAL_LIMIT")
    state.retrieval_active += 1
    try:
        return await retrieve(state.client, command, state.settings, state.keys.get("__ragflow", ""))
    finally:
        state.retrieval_active -= 1


@router.post("/executions")
async def executions(request: Request):
    state = runtime(request)
    command = await payload(request, state, Execution)
    state.bind(command.configSha256)
    model = state.model(command.modelKey)
    if not model.capabilities.streaming:
        raise HTTPException(422, "MODEL_STREAMING_UNSUPPORTED")
    # Image decode runs off-loop and is bounded by the same Assistant slot.
    attempt = state.admit(command)
    try:
        await asyncio.to_thread(validate_images, command, model, state.settings.limits)
    except BaseException as exc:
        state.active -= 1
        attempt["state"] = "REJECTED"
        if isinstance(exc, ProviderFailure):
            raise HTTPException(422, exc.code) from None
        raise
    return StreamingResponse(state.stream(command, model, attempt), media_type="text/event-stream",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


@router.post("/executions/{attempt_id}/cancel")
async def cancel(attempt_id: UUID, request: Request):
    state = runtime(request)
    attempt = state.attempts.get(str(attempt_id))
    if attempt is None:
        raise HTTPException(404, "ATTEMPT_NOT_FOUND")
    if attempt["state"] == "ACTIVE":
        attempt["cancelled"].set()
    return {"attemptId": str(attempt_id), "state": attempt["state"], "cancelRequested": attempt["cancelled"].is_set()}


def install_assistant(app):
    """Hook only independent Assistant lifecycle; existing /health is untouched."""
    app.include_router(router)

    @app.on_event("startup")
    async def assistant_startup():
        state = Runtime()
        app.state.assistant_runtime = state
        try:
            await state.start()
        except Exception:
            # Assistant resource failures are isolated from Critic startup/health.
            state.state = "MISCONFIGURED"
            state.token = ""

    @app.on_event("shutdown")
    async def assistant_shutdown():
        await app.state.assistant_runtime.close()
