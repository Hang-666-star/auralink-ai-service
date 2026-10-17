from __future__ import annotations
import asyncio
import base64
import codecs
import io
import json
import time

import anyio
import httpx
from PIL import Image

from .prompts import system_prompt


class ProviderFailure(Exception):
    def __init__(self, code):
        self.code = code


def validate_images(request, model, limits):
    total = 0
    for message in request.messages:
        if message.images and not model.capabilities.images:
            raise ProviderFailure("MODEL_IMAGES_UNSUPPORTED")
        for item in message.images:
            try:
                raw = base64.b64decode(item.dataBase64, validate=True)
                total += len(raw)
                if len(raw) > limits.maxImageBytes or total > limits.maxImageBytes:
                    raise ValueError("too large")
                with Image.open(io.BytesIO(raw)) as image:
                    if image.format not in {"JPEG", "PNG"} or image.width * image.height > limits.maxImagePixels:
                        raise ValueError("invalid image")
                    if Image.MIME[image.format] != item.mimeType:
                        raise ValueError("mismatched mime")
                    image.verify()
            except Exception as exc:
                raise ProviderFailure("INVALID_IMAGE") from exc


def provider_body(request, model):
    messages = [{"role": "system", "content": system_prompt(request)}]
    for message in request.messages:
        content = message.content
        if message.images:
            content = [{"type": "text", "text": message.content}] + [
                {"type": "image_url", "image_url": {"url": "data:" + image.mimeType + ";base64," + image.dataBase64}}
                for image in message.images]
        messages.append({"role": message.role, "content": content})
    body = {"model": model.model, "messages": messages, "stream": True,
            "stream_options": {"include_usage": True}, "max_tokens": request.budgets.maxOutputTokens}
    if model.thinkingMode == "disabled":
        # DashScope's raw Chat Completions HTTP contract uses a top-level field,
        # not the OpenAI SDK's extra_body wrapper. Config validates the exact route.
        body["enable_thinking"] = False
    return body


async def sse_data(chunks):
    """Incremental strict UTF-8; bounded line/event buffers; CRLF and multi-data."""
    decoder = codecs.getincrementaldecoder("utf-8")("strict")
    buffer, data = "", []
    size = 0
    async for chunk in chunks:
        buffer += decoder.decode(chunk)
        if len(buffer) > 262144:
            raise ProviderFailure("UPSTREAM_FRAME_TOO_LARGE")
        while "\n" in buffer:
            line, buffer = buffer.split("\n", 1)
            line = line.removesuffix("\r")
            if len(line) > 65536:
                raise ProviderFailure("UPSTREAM_FRAME_TOO_LARGE")
            if not line:
                if data:
                    yield "\n".join(data)
                data, size = [], 0
            elif line.startswith("data:"):
                value = line[5:]
                if value.startswith(" "):
                    value = value[1:]
                size += len(value)
                if size > 262144:
                    raise ProviderFailure("UPSTREAM_FRAME_TOO_LARGE")
                data.append(value)
    decoder.decode(b"", final=True)
    # Unterminated frames are deliberately not interpreted as terminal success.


async def stream_provider(client, request, model, key, settings, cancelled):
    limit = settings.limits
    total_ms = limit.summaryMs if request.purpose == "SUMMARY" else limit.chatMs
    if request.budgets.totalMs is not None:
        total_ms = min(total_ms, request.budgets.totalMs)
    deadline = time.monotonic() + total_ms / 1000
    if cancelled.is_set():
        raise ProviderFailure("CANCELLED")
    body = provider_body(request, model)
    # httpx transport retries=0, redirects/proxy env disabled. No SDK retry layer.
    response = None
    connect = asyncio.create_task(client.send(client.build_request("POST", model.baseUrl.rstrip("/") + "/chat/completions",
        headers={"Authorization": "Bearer " + key, "Accept": "text/event-stream"}, json=body), stream=True))
    cancel_waiter = asyncio.create_task(cancelled.wait())
    try:
        finished, _ = await asyncio.wait([connect, cancel_waiter], timeout=min(limit.connectMs / 1000, total_ms / 1000), return_when=asyncio.FIRST_COMPLETED)
        if cancel_waiter in finished:
            raise ProviderFailure("CANCELLED")
        if connect not in finished:
            raise ProviderFailure("UPSTREAM_TIMEOUT")
        response = connect.result()
        if response.status_code != 200:
            raise ProviderFailure("UPSTREAM_REJECTED")
        if "text/event-stream" not in response.headers.get("content-type", "").lower():
            raise ProviderFailure("UPSTREAM_CONTENT_TYPE")
        iterator = sse_data(response.aiter_bytes()).__aiter__()
        finish_reason, output_bytes, done_marker = None, 0, False
        # Core's idle timer measures received internal events. Provider reasoning
        # is activity, not answer content: forward only a bounded, redacted signal
        # when such a real frame is observed. Silence/comments generate nothing.
        activity_interval = min(1.0, limit.idleMs / 3000)
        last_activity = float("-inf")
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ProviderFailure("UPSTREAM_TIMEOUT")
            next_frame = asyncio.create_task(iterator.__anext__())
            try:
                finished, _ = await asyncio.wait([next_frame, cancel_waiter], timeout=min(remaining, limit.idleMs / 1000), return_when=asyncio.FIRST_COMPLETED)
                if cancel_waiter in finished:
                    raise ProviderFailure("CANCELLED")
                if next_frame not in finished:
                    raise ProviderFailure("UPSTREAM_TIMEOUT")
                try:
                    data = next_frame.result()
                except StopAsyncIteration:
                    break
            finally:
                # The read task may be closing its httpcore stream. Do not let
                # the outer ASGI cancellation cancel that cleanup a second time.
                with anyio.move_on_after(1.0, shield=True):
                    if not next_frame.done():
                        next_frame.cancel()
                        await asyncio.gather(next_frame, return_exceptions=True)
            if data == "[DONE]":
                done_marker = True
                break
            try:
                payload = json.loads(data)
                choices = payload.get("choices", [])
                if "error" in payload:
                    raise ProviderFailure("UPSTREAM_ERROR")
                non_answer_activity = False
                if choices:
                    choice = choices[0]
                    prior_finish = finish_reason
                    if choice.get("finish_reason"):
                        finish_reason = choice["finish_reason"]
                    fields = choice.get("delta", {})
                    delta = fields.get("content")
                    reasoning = fields.get("reasoning_content")
                    role = fields.get("role")
                    if reasoning is not None and not isinstance(reasoning, str):
                        raise ProviderFailure("UPSTREAM_INVALID_FRAME")
                    if role is not None and role != "assistant":
                        raise ProviderFailure("UPSTREAM_INVALID_FRAME")
                    non_answer_activity = bool(reasoning) or role == "assistant"
                    if delta is not None:
                        if not isinstance(delta, str) or prior_finish and delta:
                            raise ProviderFailure("UPSTREAM_INVALID_FRAME")
                        output_bytes += len(delta.encode("utf-8"))
                        if output_bytes > limit.maxOutputBytes:
                            raise ProviderFailure("OUTPUT_LIMIT")
                        if delta:
                            yield "delta", {"text": delta}
                usage = payload.get("usage")
                if isinstance(usage, dict):
                    safe = {k: usage.get(k) for k in ("prompt_tokens", "completion_tokens", "total_tokens") if isinstance(usage.get(k), int) and usage[k] >= 0}
                    yield "usage", {"providerReported": safe, "purpose": request.purpose}
                elif non_answer_activity and not delta and not finish_reason:
                    now = time.monotonic()
                    if now - last_activity >= activity_interval:
                        last_activity = now
                        yield "heartbeat", {"kind": "provider_activity"}
            except (ValueError, TypeError, AttributeError, IndexError) as exc:
                raise ProviderFailure("UPSTREAM_INVALID_FRAME") from exc
        if not done_marker or finish_reason not in {"stop", "length"}:
            raise ProviderFailure("UPSTREAM_INCOMPLETE")
        if cancelled.is_set():
            raise ProviderFailure("CANCELLED")
        yield "done", {"finishReason": finish_reason, "usageMayBeUnavailable": True}
    except httpx.TimeoutException as exc:
        raise ProviderFailure("UPSTREAM_TIMEOUT") from exc
    except (httpx.HTTPError, UnicodeError) as exc:
        raise ProviderFailure("UPSTREAM_TRANSPORT_ERROR") from exc
    finally:
        # Starlette cancels the enclosing AnyIO scope on disconnect. Protect only
        # bounded cleanup, not generation, so response.aclose() can actually run.
        with anyio.move_on_after(1.0, shield=True):
            cancel_waiter.cancel()
            if not connect.done():
                connect.cancel()
            await asyncio.gather(connect, cancel_waiter, return_exceptions=True)
            # Cancellation can win the same wait iteration as received headers.
            # Reclaim that completed response even if it was not assigned above.
            if response is None and not connect.cancelled():
                try:
                    response = connect.result()
                except (asyncio.CancelledError, Exception):
                    pass
            if response is not None:
                await response.aclose()
