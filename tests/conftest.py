"""Service contracts run in-process without loading private env or a provider."""
from pathlib import Path
import os
import sys

import dotenv
import httpx
import pytest
import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "critic-service"))


@pytest.fixture(autouse=True)
def isolated_provider_boundary(monkeypatch):
    prefixes = ("AURALINK_", "QWEN_", "LLM_", "DASHSCOPE_", "DEEPSEEK_", "CRITIC_")
    for name in tuple(os.environ):
        if name.startswith(prefixes) or name in {"MAX_IMAGE_MB", "MAX_IMAGE_PIXELS", "MAX_CONCURRENCY", "DEFAULT_EVALUATION_PROFILE"}:
            monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(dotenv, "load_dotenv", lambda *args, **kwargs: False)

    def forbidden(*args, **kwargs):
        raise AssertionError("Offline contract tests must not contact a provider")

    async def forbidden_async(*args, **kwargs):
        forbidden()

    from guide_service.qwen_client import _DeadlineHttpSession
    monkeypatch.setattr(_DeadlineHttpSession, "post", forbidden)
    monkeypatch.setattr(requests.sessions.Session, "request", forbidden)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", forbidden_async)


@pytest.fixture
def guide_request():
    # Explicit Spring-to-Guide wire shape; do not derive keys from implementation.
    return {
        "requestId": "31f5909f-9772-48da-a04e-e0c1ee2e2760",
        "schemaVersion": "1",
        "painting": {
            "paintingId": "0e40bdf6-13dc-442e-8bb3-0548ee0c5be5",
            "basic": {"title": "山水习作", "creationYear": None,
                "creationDynastyRaw": None, "creationDynastyNormalized": None,
                "actualSize": None, "collectionInstitution": None},
            "artist": {"name": None, "birthYear": None, "birthPlace": None, "school": None},
            "art": {"category": None, "subject": "山水", "paintingSchool": None,
                "style": None, "color": None, "composition": None, "artisticConception": None,
                "brushwork": None, "inkMethod": None, "paintingMaterial": None,
                "pigment": None, "seal": None, "culturalSymbol": None},
            "officialAnnotations": {"generatedText": None, "musicSceneDescription": None},
        },
        "knowledge": [],
        "options": {"language": "zh-CN", "detailLevel": "STANDARD"},
    }
