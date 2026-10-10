from __future__ import annotations

import os
from dataclasses import dataclass


def _as_bool(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    llm_provider: str
    llm_api_key: str
    llm_base_url: str | None
    llm_model: str
    llm_timeout_seconds: float
    llm_max_retries: int
    llm_json_mode: bool
    max_image_bytes: int
    max_image_pixels: int
    max_concurrency: int
    default_profile: str
    internal_token: str
    execution_policy: str = "isolated-stub-only"

    @property
    def vision_capable(self) -> bool:
        # DeepSeek 官方 API 当前公开模型为文本模型，不能直接接收画作图片。
        return self.llm_provider != "deepseek"

    @property
    def configuration_detail(self) -> str:
        if not self.llm_api_key:
            return "未配置 API Key"
        if not self.vision_capable:
            return "DeepSeek 官方 API 当前不支持图片输入，请改用千问视觉模型"
        if not self.internal_token:
            return "未配置 ArtLIVE 内部调用令牌"
        return "配置可用"

    @property
    def execution_permitted(self) -> bool:
        # Keep isolated deployments fail-closed. A real provider requires an
        # explicit persistent operator setting; readiness never exercises it.
        return self.execution_policy == "configured-provider" or (
            self.execution_policy == "isolated-stub-only" and self.llm_provider == "stub"
        )

    @property
    def evaluation_available(self) -> bool:
        return bool(self.llm_api_key and self.llm_model and self.internal_token
                    and self.vision_capable and self.execution_permitted)

    @property
    def readiness_reason(self) -> str:
        if self.execution_policy not in {"isolated-stub-only", "configured-provider"}:
            return "EXECUTION_POLICY_INVALID"
        if not self.execution_permitted:
            return "EXECUTION_POLICY_BLOCKED"
        if not self.llm_api_key or not self.llm_model or not self.internal_token or not self.vision_capable:
            return "PROVIDER_UNCONFIGURED"
        return "READY_FOR_CONTROLLED_EVALUATION"

    @classmethod
    def from_env(cls) -> "Settings":
        provider = os.getenv("LLM_PROVIDER", "qwen").strip().lower()
        if provider not in {"qwen", "deepseek", "custom", "stub"}:
            provider = "custom"

        provider_defaults = {
            "qwen": (
                "https://dashscope.aliyuncs.com/compatible-mode/v1",
                "qwen3.7-plus",
                os.getenv("DASHSCOPE_API_KEY", ""),
            ),
            "deepseek": (
                "https://api.deepseek.com",
                "deepseek-v4-flash",
                os.getenv("DEEPSEEK_API_KEY", ""),
            ),
            "custom": (None, "", ""),
            # The isolated test service has no network client or provider key
            # for this mode.  It only exercises the existing schemas and
            # orchestrator with deterministic local values.
            "stub": (None, "deterministic-local-stub", "local-stub-not-a-provider-key"),
        }
        default_base_url, default_model, provider_key = provider_defaults[provider]
        base_url = os.getenv("LLM_BASE_URL", "").strip() or default_base_url
        return cls(
            llm_provider=provider,
            llm_api_key=(os.getenv("LLM_API_KEY", "").strip() or provider_key.strip()),
            llm_base_url=base_url,
            llm_model=os.getenv("LLM_MODEL", default_model).strip() or default_model,
            llm_timeout_seconds=float(os.getenv("LLM_TIMEOUT_SECONDS", "120")),
            # A network outcome can be ambiguous to a vision provider.  The Java task
            # lifecycle never replays an evaluation, so keep the SDK layer equally
            # explicit unless an operator deliberately configures a bounded retry.
            llm_max_retries=max(0, int(os.getenv("LLM_MAX_RETRIES", "0"))),
            llm_json_mode=_as_bool(os.getenv("LLM_JSON_MODE"), True),
            max_image_bytes=int(os.getenv("MAX_IMAGE_MB", "20")) * 1024 * 1024,
            max_image_pixels=int(os.getenv("MAX_IMAGE_PIXELS", "40000000")),
            max_concurrency=max(1, int(os.getenv("MAX_CONCURRENCY", "4"))),
            default_profile=os.getenv(
                "DEFAULT_EVALUATION_PROFILE", "authoritative_four"
            ).strip(),
            internal_token=os.getenv("AURALINK_CRITIC_INTERNAL_TOKEN", "").strip(),
            execution_policy=os.getenv("AURALINK_CRITIC_EXECUTION_POLICY", "isolated-stub-only").strip(),
        )


def get_settings() -> Settings:
    return Settings.from_env()
