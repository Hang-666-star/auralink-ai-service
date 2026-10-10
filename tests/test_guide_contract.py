from dataclasses import replace
import json
from unittest.mock import Mock

import pytest
import requests

from guide_service.app import INTERNAL_TOKEN_HEADER, create_app
from guide_service.config import GuideConfigurationError, GuideServiceConfig
from guide_service.env_loader import EnvFileError, parse_env_text
from guide_service.qwen_client import QwenClientError, QwenGuideClient, _PreSendConnectionError
from guide_service.schema import validate_guide_request


@pytest.fixture
def config(tmp_path):
    private_env = tmp_path / "service.env"
    private_env.write_text("QWEN_MODEL=qwen3-vl-plus\n", encoding="utf-8")
    return GuideServiceConfig.load({
        "AURALINK_ENV_FILE": str(private_env),
        "AURALINK_GUIDE_SERVICE_HOST": "127.0.0.1",
        "AURALINK_GUIDE_SERVICE_PORT": "19084",
        "AURALINK_GUIDE_INTERNAL_TOKEN": "test-internal-token",
    })


def test_health_is_configuration_evidence_and_does_not_expose_credentials(config):
    response = create_app(config).test_client().get("/health")
    assert response.status_code == 200
    assert response.json == {"status": "UP", "providerConfigured": False, "modelConfigured": True}
    configured = replace(config, qwen_api_key="test-provider-key", qwen_base_url="https://example.invalid/v1")
    health = create_app(configured).test_client().get("/health").json
    assert health == {"status": "UP", "providerConfigured": True, "modelConfigured": True}
    assert "test-provider-key" not in json.dumps(health)


@pytest.mark.parametrize("header,status", [(None, 401), ("wrong-token", 401)])
def test_authentication_precedes_body_and_provider(config, header, status):
    provider = Mock()
    headers = {INTERNAL_TOKEN_HEADER: header} if header else {}
    response = create_app(config, provider).test_client().post(
        "/v1/guide/generate", data="invalid-json", content_type="application/json", headers=headers)
    assert response.status_code == status
    assert response.json["error"]["code"] == "UNAUTHORIZED_INTERNAL_CALL"
    provider.generate.assert_not_called()


def test_missing_internal_configuration_is_unavailable(config):
    response = create_app(replace(config, internal_token="")).test_client().post("/v1/guide/generate")
    assert response.status_code == 503
    assert response.json["error"]["code"] == "SERVICE_NOT_CONFIGURED"


def test_unconfigured_provider_cannot_claim_success(config, guide_request):
    response = create_app(config).test_client().post("/v1/guide/generate", json=guide_request,
        headers={INTERNAL_TOKEN_HEADER: config.internal_token})
    assert response.status_code == 503
    assert response.json["status"] == "FAILED"
    assert response.json["error"]["code"] == "PROVIDER_NOT_CONFIGURED"


def test_unknown_request_fields_rejected_before_provider(config, guide_request):
    provider = Mock()
    guide_request["providerKey"] = "not-a-supported-field"
    response = create_app(config, provider).test_client().post("/v1/guide/generate", json=guide_request,
        headers={INTERNAL_TOKEN_HEADER: config.internal_token})
    assert response.status_code == 400
    assert response.json["error"]["code"] == "INVALID_REQUEST"
    provider.generate.assert_not_called()


def test_local_stub_exercises_real_schema_and_http_contract(config, guide_request, monkeypatch, tmp_path):
    state, control = tmp_path / "state.json", tmp_path / "control.json"
    state.write_text('{"guide_requests":0,"external_provider_calls":0}')
    control.write_text('{"mode":"success"}')
    monkeypatch.setenv("AURALINK_GUIDE_LOCAL_STUB", "1")
    monkeypatch.setenv("AURALINK_GUIDE_STUB_STATE_FILE", str(state))
    monkeypatch.setenv("AURALINK_GUIDE_STUB_CONTROL_FILE", str(control))
    response = create_app(config).test_client().post("/v1/guide/generate", json=guide_request,
        headers={INTERNAL_TOKEN_HEADER: config.internal_token})
    assert response.status_code == 200
    assert response.json["requestId"] == guide_request["requestId"]
    assert response.json["status"] == "SUCCESS" and response.json["error"] is None
    result = response.json["result"]
    assert result["schemaVersion"] == "1" and result["knowledgeReferences"] == []
    assert len(result["sections"]) == 8 and 2 <= len(result["highlights"]) <= 5
    assert json.loads(state.read_text()) == {"guide_requests": 1, "external_provider_calls": 0}


@pytest.mark.parametrize("error,code", [
    (requests.Timeout("test"), "UPSTREAM_TIMEOUT"),
    (requests.ConnectionError("test"), "UPSTREAM_UNAVAILABLE"),
])
def test_ambiguous_provider_failure_is_not_retried(config, guide_request, error, code):
    session = Mock()
    session.post.side_effect = error
    configured = replace(config, qwen_api_key="test-provider-key", qwen_base_url="https://example.invalid/v1")
    with pytest.raises(QwenClientError) as failure:
        QwenGuideClient(configured, session=session).generate(validate_guide_request(guide_request))
    assert failure.value.code == code
    assert session.post.call_count == 1


def test_only_proven_presend_failure_can_retry_once(config, guide_request):
    session = Mock()
    session.post.side_effect = _PreSendConnectionError("test")
    configured = replace(config, qwen_api_key="test-provider-key", qwen_base_url="https://example.invalid/v1")
    with pytest.raises(QwenClientError) as failure:
        QwenGuideClient(configured, session=session, sleeper=lambda seconds: None).generate(
            validate_guide_request(guide_request))
    assert failure.value.code == "UPSTREAM_UNAVAILABLE"
    assert session.post.call_count == 2


@pytest.mark.parametrize("host", ["0.0.0.0", "localhost", "192.0.2.1"])
def test_non_loopback_bind_is_refused(tmp_path, host):
    env = tmp_path / "service.env"
    env.write_text("")
    with pytest.raises(GuideConfigurationError):
        GuideServiceConfig.load({"AURALINK_ENV_FILE": str(env), "AURALINK_GUIDE_SERVICE_HOST": host})


def test_env_parser_keeps_shell_text_inert_and_rejects_quoted_shared_scalars():
    assert parse_env_text('QWEN_API_KEY="${NOT_EXPANDED}"\nNOTE=$(not-executed)') == {
        "QWEN_API_KEY": "${NOT_EXPANDED}", "NOTE": "$(not-executed)"}
    with pytest.raises(EnvFileError):
        parse_env_text('AURALINK_GUIDE_SERVICE_PORT="19084"')
