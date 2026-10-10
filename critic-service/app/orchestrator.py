from __future__ import annotations

import asyncio

from .dimensions import Dimension, get_profile
from .llm import VisionLLM
from .prompts import (
    first_round_prompt,
    judge_prompt,
    judge_system_prompt,
    revision_prompt,
    specialist_system_prompt,
)
from .schemas import DimensionAssessment, FinalReport


class EvaluationOrchestrator:
    """Parallel specialist review -> peer revision -> chair synthesis."""

    def __init__(self, llm: VisionLLM, max_concurrency: int = 4):
        self.llm = llm
        self._semaphore = asyncio.Semaphore(max_concurrency)

    async def _call(self, **kwargs):
        async with self._semaphore:
            return await self.llm.structured_vision(**kwargs)

    async def _first_round(
        self,
        dimension: Dimension,
        image_data_url: str,
        title: str | None,
        context: str | None,
    ) -> DimensionAssessment:
        result = await self._call(
            system_prompt=specialist_system_prompt(dimension),
            user_prompt=first_round_prompt(dimension, title=title, context=context),
            image_data_url=image_data_url,
            response_model=DimensionAssessment,
        )
        return self._normalize_identity(result, dimension)

    async def _revision(
        self,
        dimension: Dimension,
        own: DimensionAssessment,
        peers: list[DimensionAssessment],
        image_data_url: str,
    ) -> DimensionAssessment:
        result = await self._call(
            system_prompt=specialist_system_prompt(dimension),
            user_prompt=revision_prompt(dimension, own, peers),
            image_data_url=image_data_url,
            response_model=DimensionAssessment,
        )
        return self._normalize_identity(result, dimension)

    @staticmethod
    def _normalize_identity(
        result: DimensionAssessment, dimension: Dimension
    ) -> DimensionAssessment:
        return result.model_copy(
            update={"dimension_id": dimension.id, "dimension_name": dimension.name}
        )

    @staticmethod
    def _validate_report_dimensions(
        report: FinalReport, dimensions: tuple[Dimension, ...]
    ) -> FinalReport:
        expected = {item.id: item for item in dimensions}
        received = {item.dimension_id: item for item in report.dimension_assessments}
        if set(received) != set(expected):
            missing = set(expected) - set(received)
            extra = set(received) - set(expected)
            raise ValueError(f"主持人维度不完整，缺少={missing}，多出={extra}")
        ordered = []
        for dimension in dimensions:
            item = received[dimension.id]
            ordered.append(
                item.model_copy(update={"dimension_name": dimension.name})
            )
        return report.model_copy(update={"dimension_assessments": ordered})

    async def evaluate(
        self,
        *,
        image_data_url: str,
        profile_id: str,
        title: str | None = None,
        context: str | None = None,
    ) -> tuple[FinalReport, list[DimensionAssessment]]:
        dimensions = get_profile(profile_id)
        first = await asyncio.gather(
            *(
                self._first_round(d, image_data_url, title, context)
                for d in dimensions
            )
        )

        revised = await asyncio.gather(
            *(
                self._revision(
                    dimension,
                    first[index],
                    [item for peer_index, item in enumerate(first) if peer_index != index],
                    image_data_url,
                )
                for index, dimension in enumerate(dimensions)
            )
        )

        report = await self._call(
            system_prompt=judge_system_prompt(),
            user_prompt=judge_prompt(
                profile_id=profile_id,
                assessments=list(revised),
                title=title,
                context=context,
            ),
            image_data_url=image_data_url,
            response_model=FinalReport,
            temperature=0.1,
        )
        return self._validate_report_dimensions(report, dimensions), list(revised)
