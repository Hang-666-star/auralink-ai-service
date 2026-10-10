from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field


Confidence = Literal["高", "中", "低"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EvidenceObservation(StrictModel):
    finding: str = Field(min_length=1, description="专业判断")
    visual_evidence: str = Field(min_length=1, description="画面中可定位的视觉依据")
    significance: str = Field(min_length=1, description="该证据为何影响本维度结论")
    confidence: Confidence


class DimensionAssessment(StrictModel):
    dimension_id: str
    dimension_name: str
    conclusion: str = Field(min_length=1)
    observations: list[EvidenceObservation] = Field(min_length=1)
    strengths: list[str]
    limitations: list[str]
    uncertainties: list[str]
    peer_response: str = ""


class FinalDimensionAssessment(StrictModel):
    dimension_id: str
    dimension_name: str
    conclusion: str
    evidence: list[str]
    strengths: list[str]
    limitations: list[str]


class FinalReport(StrictModel):
    work_overview: str
    dimension_assessments: list[FinalDimensionAssessment]
    consensus: list[str]
    disagreements: list[str]
    holistic_assessment: str
    improvement_suggestions: list[str]
    viewing_limitations: list[str]


class EvaluationMeta(StrictModel):
    evaluation_id: str = Field(default_factory=lambda: str(uuid4()))
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    profile: str
    model: str
    rounds: int = 2
    scoring_used: bool = False
    partial: bool = False


class EvaluationResponse(StrictModel):
    meta: EvaluationMeta
    report: FinalReport
    expert_assessments: list[DimensionAssessment]


class HealthResponse(StrictModel):
    status: str
    version: str
    provider: str
    model: str
    model_configured: bool
    vision_capable: bool
    detail: str
