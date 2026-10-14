from __future__ import annotations
import asyncio
import hmac
import json

import httpx
from fastapi import HTTPException

from .config import load_configuration
from .prompts import parse_choices
from .providers import ProviderFailure, stream_provider


class Runtime:
    def __init__(self):
        self.settings, self.digest, self.keys, self.token, self.state = load_configuration()
        self.client = None
        self.attempts = {}  # Process-lifetime replay guard; Core owns durable recovery.
        self.active = 0
        self.retrieval_active = 0

    async def start(self):
        if self.state == "READY":
            limits = self.settings.limits
            self.client = httpx.AsyncClient(transport=httpx.AsyncHTTPTransport(retries=0),
                timeout=httpx.Timeout(limits.idleMs / 1000, connect=limits.connectMs / 1000),
                limits=httpx.Limits(max_connections=limits.concurrency * 2, max_keepalive_connections=limits.concurrency),
                follow_redirects=False, trust_env=False)

    async def close(self):
        for attempt in self.attempts.values():
            attempt["cancelled"].set()
        if self.client is not None:
            await self.client.aclose()

    def authenticate(self, authorization):
        if self.state != "READY":
            raise HTTPException(503, detail="ASSISTANT_" + self.state)
        if not authorization or not hmac.compare_digest(authorization, "Bearer " + self.token):
            raise HTTPException(401, detail="ASSISTANT_UNAUTHORIZED")

    def bind(self, digest):
        if not hmac.compare_digest(self.digest, digest):
            raise HTTPException(409, detail="ASSISTANT_CONFIGURATION_CHANGED")

    def model(self, key):
        for item in self.settings.models:
            if item.id == key and item.enabled:
                return item
        raise HTTPException(422, detail="MODEL_UNAVAILABLE")

    def models(self):
        return {"contractVersion": 1, "configSha256": self.digest,
            "models": [{"id": m.id, "providerId": m.provider, "modelId": m.model,
                "label": m.label or m.model, "configured": m.id in self.keys, "enabled": m.enabled,
                "capabilities": {"images": m.capabilities.images, "streaming": m.capabilities.streaming,
                    "imageMimeTypes": ["image/jpeg", "image/png"] if m.capabilities.images else []},
                "evidence": m.evidence} for m in self.settings.models],
            "retrieval": {"state": "AVAILABLE" if self.settings.ragflow.enabled else "NOT_CONFIGURED"},
            "limits": {"concurrency": self.settings.limits.concurrency}}

    def admit(self, request):
        key = str(request.attemptId)
        if key in self.attempts:
            raise HTTPException(409, detail="ATTEMPT_ALREADY_SEEN")
        if len(self.attempts) >= self.settings.limits.attemptCapacity:
            raise HTTPException(503, detail="ASSISTANT_ATTEMPT_CAPACITY")
        if self.active >= self.settings.limits.concurrency:
            raise HTTPException(429, detail="ASSISTANT_EXECUTION_LIMIT")
        self.attempts[key] = {"runId": str(request.runId), "cancelled": asyncio.Event(), "state": "ACTIVE"}
        self.active += 1
        return self.attempts[key]

    async def stream(self, request, model, attempt):
        answer = []
        try:
            async for event, data in stream_provider(self.client, request, model, self.keys[model.id], self.settings, attempt["cancelled"]):
                if event == "delta":
                    answer.append(data["text"])
                if event == "done":
                    cards = parse_choices("".join(answer))
                    if cards:
                        yield self.event("choices", {"cards": cards})
                    attempt["state"] = "COMPLETED"
                yield self.event(event, data)
        except ProviderFailure as exc:
            attempt["state"] = "CANCELLED" if exc.code == "CANCELLED" else "FAILED"
            yield self.event("error", {"code": exc.code})
        except asyncio.CancelledError:
            attempt["state"] = "INTERRUPTED"
            raise
        except Exception:
            attempt["state"] = "FAILED"
            yield self.event("error", {"code": "EXECUTION_FAILED"})
        finally:
            self.active -= 1

    @staticmethod
    def event(name, data):
        return "event: " + name + "\ndata: " + json.dumps(data, ensure_ascii=False, separators=(",", ":")) + "\n\n"
