from __future__ import annotations

import asyncio
import json
import os
import re
from typing import Any, TypeVar
from pydantic import BaseModel, ValidationError

from .config import Settings
from .dimensions import get_profile
from .schemas import DimensionAssessment, FinalReport

T = TypeVar("T", bound=BaseModel)


class ModelOutputError(RuntimeError):
    pass


def _extract_json(content: str) -> dict[str, Any]:
    text = content.strip()
    if text.startswith("```json"):
        text = text[7:]
    elif text.startswith("```"):
        text = text[3:]
    if text.endswith("```"):
        text = text[:-3]
    try:
        value = json.loads(text.strip())
    except json.JSONDecodeError as exc:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            raise ModelOutputError("模型没有返回合法 JSON") from exc
        try:
            value = json.loads(text[start : end + 1])
        except json.JSONDecodeError as nested:
            raise ModelOutputError("模型返回的 JSON 无法解析") from nested
    if not isinstance(value, dict):
        raise ModelOutputError("模型输出必须是 JSON 对象")
    return value


class VisionLLM:
    def __init__(self, settings: Settings):
        if not settings.llm_api_key:
            raise ValueError("LLM_API_KEY 未配置")
        self.is_stub = settings.llm_provider == "stub"
        if self.is_stub:
            self.client = None
            self.model = settings.llm_model
            self.json_mode = True
            self.provider = settings.llm_provider
            return
        # The deterministic local profile deliberately has no OpenAI SDK
        # dependency or real provider path. Import it only for a separately
        # configured non-stub deployment.
        from openai import AsyncOpenAI
        kwargs: dict[str, Any] = {
            "api_key": settings.llm_api_key,
            "timeout": settings.llm_timeout_seconds,
            "max_retries": settings.llm_max_retries,
        }
        if settings.llm_base_url:
            kwargs["base_url"] = settings.llm_base_url
        self.client = AsyncOpenAI(**kwargs)
        self.model = settings.llm_model
        self.json_mode = settings.llm_json_mode
        self.provider = settings.llm_provider

    async def structured_vision(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        image_data_url: str,
        response_model: type[T],
        temperature: float = 0.15,
    ) -> T:
        if self.is_stub:
            _record_stub_step()
            if _stub_mode() == "failure":
                raise ModelOutputError("deterministic local stub failure")
            return response_model.model_validate(_stub_payload(response_model, user_prompt))
        messages = [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": user_prompt},
                    {
                        "type": "image_url",
                        # 只使用 OpenAI 与百炼均支持的最小公共字段。
                        "image_url": {"url": image_data_url},
                    },
                ],
            },
        ]
        last_error: Exception | None = None
        for attempt in range(2):
            try:
                kwargs: dict[str, Any] = {
                    "model": self.model,
                    "messages": messages,
                    "temperature": temperature,
                }
                if self.json_mode:
                    kwargs["response_format"] = {"type": "json_object"}
                if self.provider == "qwen":
                    # 视觉结构化输出在非思考模式下兼容性最好；多智能体复议本身
                    # 已提供第二阶段推理，避免九次调用额外产生长思维链。
                    kwargs["extra_body"] = {"enable_thinking": False}
                response = await self.client.chat.completions.create(**kwargs)
                content = response.choices[0].message.content or ""
                return response_model.model_validate(_extract_json(content))
            except (ModelOutputError, ValidationError, IndexError) as exc:
                last_error = exc
                messages.append(
                    {
                        "role": "user",
                        "content": "上一次输出不符合约定的数据结构。只返回完整、合法的 JSON 对象，不要解释。",
                    }
                )
                await asyncio.sleep(0)
        raise ModelOutputError(f"模型结构化输出校验失败：{last_error}")


def _stub_mode() -> str:
    control = os.getenv("CRITIC_STUB_CONTROL_FILE", "").strip()
    if not control:
        return "success"
    try:
        return json.loads(open(control, encoding="utf-8").read()).get("mode", "success")
    except (OSError, json.JSONDecodeError):
        return "failure"


def _record_stub_step() -> None:
    state = os.getenv("CRITIC_STUB_STATE_FILE", "").strip()
    if not state:
        return
    try:
        value = json.loads(open(state, encoding="utf-8").read())
    except (OSError, json.JSONDecodeError):
        value = {"evaluation_requests": 0, "model_steps": 0, "external_provider_calls": 0}
    value["model_steps"] = int(value.get("model_steps", 0)) + 1
    value["external_provider_calls"] = 0
    with open(state, "w", encoding="utf-8") as target:
        json.dump(value, target, ensure_ascii=False, sort_keys=True)


def _stub_payload(response_model: type[T], prompt: str) -> dict[str, Any]:
    if response_model is DimensionAssessment:
        return {
            "dimension_id": "stub_dimension", "dimension_name": "确定性本地维度",
            "conclusion": "仅供本地链路验证的可见画面结论。",
            "observations": [{"finding": "画面主体与留白形成可见关系", "visual_evidence": "画面中央主体周围留有空白",
                              "significance": "用于验证四维报告结构", "confidence": "中"}],
            "strengths": ["结构化输出有效"], "limitations": ["不评价真实作品质量"],
            "uncertainties": ["这是确定性本地 stub"], "peer_response": "本地 stub 已完成交叉复议结构。",
        }
    if response_model is FinalReport:
        # The chair prompt includes a literal output *example* with a
        # placeholder dimension id.  Deriving ids with a generic JSON regex
        # therefore silently selects the wrong profile for requested_four.
        # Read the explicit profile marker generated by judge_prompt, then
        # use the real profile definitions that the orchestrator validates.
        profile_match = re.search(r"评价方案：([a-z_]+)", prompt)
        if profile_match is None:
            raise ModelOutputError("deterministic stub could not identify the Critic profile")
        dimensions = get_profile(profile_match.group(1))
        return {
            "work_overview": "本地 stub 仅验证可见图像的报告传输与持久化。",
            "dimension_assessments": [{"dimension_id": dimension.id, "dimension_name": dimension.name,
                "conclusion": "结构化结论已通过本地模式生成。", "evidence": ["仅使用确定性验证文本"],
                "strengths": ["可读取"], "limitations": ["不代表真实模型判断"]} for dimension in dimensions],
            "consensus": ["本地结果通过既有模式验证"], "disagreements": ["未发现实质性分歧"],
            "holistic_assessment": "本输出用于验证应用生命周期，不代表艺术评价。",
            "improvement_suggestions": ["真实部署前必须进行真实提供方质量验证"],
            "viewing_limitations": ["确定性 stub 不产生模型质量证据"],
        }
    raise ModelOutputError("unsupported deterministic response schema")
