import importlib
import io
import json

from fastapi.testclient import TestClient
from PIL import Image
import pytest


@pytest.fixture
def critic():
    return importlib.import_module("app.main")


@pytest.fixture
def image_bytes():
    stream = io.BytesIO()
    Image.new("RGB", (256, 256), "white").save(stream, "PNG")
    return stream.getvalue()


def configured_environment(monkeypatch, provider="qwen", policy="configured-provider"):
    monkeypatch.setenv("LLM_PROVIDER", provider)
    monkeypatch.setenv("LLM_API_KEY", "test-provider-key")
    monkeypatch.setenv("AURALINK_CRITIC_INTERNAL_TOKEN", "test-internal-token")
    monkeypatch.setenv("AURALINK_CRITIC_EXECUTION_POLICY", policy)


def test_health_reports_degraded_without_using_provider(critic):
    with TestClient(critic.app) as client:
        response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "degraded"
    assert response.json()["model_configured"] is False


@pytest.mark.parametrize("provider,policy,available,reason", [
    ("qwen", "configured-provider", True, "READY_FOR_CONTROLLED_EVALUATION"),
    ("qwen", "isolated-stub-only", False, "EXECUTION_POLICY_BLOCKED"),
    ("qwen", "invalid", False, "EXECUTION_POLICY_INVALID"),
    ("deepseek", "configured-provider", False, "PROVIDER_UNCONFIGURED"),
    ("stub", "isolated-stub-only", True, "READY_FOR_CONTROLLED_EVALUATION"),
])
def test_readiness_is_authenticated_policy_inspection(critic, monkeypatch, provider, policy, available, reason):
    configured_environment(monkeypatch, provider, policy)
    def no_client(*args, **kwargs):
        raise AssertionError("Readiness must not construct a model client")
    monkeypatch.setattr(critic, "VisionLLM", no_client)
    with TestClient(critic.app) as client:
        assert client.get("/api/v1/readiness").status_code == 401
        response = client.get("/api/v1/readiness", headers={"X-Auralink-Critic-Token": "test-internal-token"})
    assert response.status_code == 200
    assert response.json()["evaluation_available"] is available
    assert response.json()["reason"] == reason
    assert response.json()["execution_policy"] == policy
    assert "test-provider-key" not in response.text and "test-internal-token" not in response.text


def test_evaluation_authentication_precedes_image_or_model_work(critic, monkeypatch):
    configured_environment(monkeypatch, provider="stub", policy="isolated-stub-only")
    async def forbidden(*args, **kwargs):
        raise AssertionError("Unauthenticated request reached image processing")
    monkeypatch.setattr(critic, "prepare_image", forbidden)
    with TestClient(critic.app) as client:
        response = client.post("/api/v1/evaluations", files={"image": ("invalid.png", b"not-image", "image/png")})
    assert response.status_code == 401


@pytest.mark.parametrize("profile,expected", [
    ("authoritative_four", ["qiyun_yijing", "bimo_se_cai_zaoxing", "style_tradition_innovation", "composition_space"]),
    ("requested_four", ["work_quality", "painting_technique", "style", "composition"]),
])
def test_stub_runs_real_nine_step_profile_and_score_free_response(critic, monkeypatch, tmp_path, image_bytes, profile, expected):
    configured_environment(monkeypatch, provider="stub", policy="isolated-stub-only")
    state = tmp_path / "critic-state.json"
    monkeypatch.setenv("CRITIC_STUB_STATE_FILE", str(state))
    with TestClient(critic.app) as client:
        response = client.post("/api/v1/evaluations", files={"image": ("painting.png", image_bytes, "image/png")},
            data={"profile": profile, "title": "Local contract fixture"},
            headers={"X-Auralink-Critic-Token": "test-internal-token"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["meta"]["profile"] == profile and body["meta"]["rounds"] == 2
    assert body["meta"]["scoring_used"] is False and body["meta"]["partial"] is False
    assert [x["dimension_id"] for x in body["report"]["dimension_assessments"]] == expected
    assert [x["dimension_id"] for x in body["expert_assessments"]] == expected
    assert json.loads(state.read_text()) == {"evaluation_requests": 1, "model_steps": 9, "external_provider_calls": 0}


@pytest.mark.parametrize("profile,image,status", [("unknown-profile", b"invalid", 422), ("authoritative_four", b"invalid", 422)])
def test_invalid_profile_or_image_is_rejected(critic, monkeypatch, profile, image, status):
    configured_environment(monkeypatch, provider="stub", policy="isolated-stub-only")
    with TestClient(critic.app) as client:
        response = client.post("/api/v1/evaluations", files={"image": ("invalid.png", image, "image/png")},
            data={"profile": profile}, headers={"X-Auralink-Critic-Token": "test-internal-token"})
    assert response.status_code == status


def test_real_provider_is_blocked_by_default_policy(critic, monkeypatch, image_bytes):
    configured_environment(monkeypatch, policy="isolated-stub-only")
    with TestClient(critic.app) as client:
        response = client.post("/api/v1/evaluations", files={"image": ("painting.png", image_bytes, "image/png")},
            headers={"X-Auralink-Critic-Token": "test-internal-token"})
    assert response.status_code == 503
    assert response.json()["detail"] == "EXECUTION_POLICY_BLOCKED"
