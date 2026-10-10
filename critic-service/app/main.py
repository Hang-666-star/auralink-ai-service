from __future__ import annotations

import hmac
import logging
import json
import os

from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import JSONResponse

from . import __version__
from .config import get_settings
from .dimensions import PROFILES, get_profile
from .image_utils import InvalidImageError, prepare_image
from .llm import ModelOutputError, VisionLLM
from .orchestrator import EvaluationOrchestrator
from .schemas import EvaluationMeta, EvaluationResponse, HealthResponse

load_dotenv()
logger = logging.getLogger(__name__)

app = FastAPI(
    title="国画多智能体评估 API",
    version=__version__,
    description="面向中国画数字图像的四维、多智能体、无打分专业评估后端。",
)


def _stub_state() -> dict:
    path = os.getenv("CRITIC_STUB_STATE_FILE", "").strip()
    if not path:
        return {"evaluation_requests": 0, "model_steps": 0, "external_provider_calls": 0}
    try:
        return json.loads(open(path, encoding="utf-8").read())
    except (OSError, json.JSONDecodeError):
        return {"evaluation_requests": 0, "model_steps": 0, "external_provider_calls": 0}


def _record_evaluation_request() -> None:
    path = os.getenv("CRITIC_STUB_STATE_FILE", "").strip()
    if not path:
        return
    value = _stub_state()
    value["evaluation_requests"] = int(value.get("evaluation_requests", 0)) + 1
    value["external_provider_calls"] = 0
    with open(path, "w", encoding="utf-8") as target:
        json.dump(value, target, ensure_ascii=False, sort_keys=True)


@app.get("/local-stub/state")
async def local_stub_state() -> dict:
    # Test-only local evidence endpoint. It is not called by Java and reveals
    # no provider credential, prompt or uploaded bytes.
    return _stub_state()


@app.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    settings = get_settings()
    return HealthResponse(
        status="ok" if settings.evaluation_available else "degraded",
        version=__version__,
        provider=settings.llm_provider,
        model=settings.llm_model,
        model_configured=bool(settings.llm_api_key),
        vision_capable=settings.vision_capable,
        detail=settings.configuration_detail if settings.evaluation_available else settings.readiness_reason,
    )


@app.get("/api/v1/evaluation-profiles")
async def evaluation_profiles() -> dict:
    return {
        "profiles": {
            profile_id: [dimension.public_dict() for dimension in dimensions]
            for profile_id, dimensions in PROFILES.items()
        },
        "recommended": "authoritative_four",
    }


def _require_internal_token(settings, internal_token: str | None) -> None:
    if not settings.internal_token or not internal_token or not hmac.compare_digest(
        settings.internal_token, internal_token
    ):
        raise HTTPException(status_code=401, detail="未授权的内部评析请求")


@app.get("/api/v1/readiness")
async def evaluation_readiness(
    internal_token: str | None = Header(None, alias="X-Auralink-Critic-Token"),
) -> dict:
    settings = get_settings()
    _require_internal_token(settings, internal_token)
    # Pure configuration/policy inspection: no image, task, SDK client or
    # provider request is created by this endpoint.
    return {"evaluation_available": settings.evaluation_available,
            "reason": settings.readiness_reason,
            "provider": settings.llm_provider, "model": settings.llm_model,
            "execution_policy": settings.execution_policy}


@app.post("/api/v1/evaluations", response_model=EvaluationResponse)
async def create_evaluation(
    image: UploadFile = File(..., description="国画图片"),
    profile: str | None = Form(None),
    title: str | None = Form(None, max_length=200),
    context: str | None = Form(None, max_length=2000),
    internal_token: str | None = Header(None, alias="X-Auralink-Critic-Token"),
) -> EvaluationResponse:
    settings = get_settings()
    # Authenticate before reading image bytes or constructing an LLM client.
    _require_internal_token(settings, internal_token)
    profile_id = profile or settings.default_profile
    try:
        get_profile(profile_id)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if not settings.llm_api_key:
        raise HTTPException(
            status_code=503,
            detail="服务端尚未配置 API Key（千问可使用 DASHSCOPE_API_KEY）",
        )
    if not settings.vision_capable:
        raise HTTPException(
            status_code=503,
            detail=(
                "DeepSeek 官方 API 当前模型不支持图片输入，不能独立完成国画评估。"
                "请设置 LLM_PROVIDER=qwen 并使用 qwen3.7-plus，或接入支持视觉的自定义服务。"
            ),
        )

    if not settings.execution_permitted:
        raise HTTPException(status_code=503, detail=settings.readiness_reason)

    if settings.llm_provider == "stub":
        _record_evaluation_request()

    try:
        prepared = await prepare_image(
            image,
            max_bytes=settings.max_image_bytes,
            max_pixels=settings.max_image_pixels,
        )
        llm = VisionLLM(settings)
        orchestrator = EvaluationOrchestrator(llm, settings.max_concurrency)
        report, experts = await orchestrator.evaluate(
            image_data_url=prepared.data_url,
            profile_id=profile_id,
            title=title,
            context=context,
        )
    except InvalidImageError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except (ModelOutputError, ValueError) as exc:
        logger.exception("Evaluation model output failed")
        raise HTTPException(status_code=502, detail=f"模型评估结果不可用：{exc}") from exc
    except Exception as exc:
        logger.exception("Evaluation failed")
        raise HTTPException(status_code=502, detail="多智能体评估调用失败") from exc

    return EvaluationResponse(
        meta=EvaluationMeta(profile=profile_id, model=settings.llm_model),
        report=report,
        expert_assessments=experts,
    )


@app.exception_handler(413)
async def payload_too_large(_, __):
    return JSONResponse(status_code=413, content={"detail": "上传内容过大"})
