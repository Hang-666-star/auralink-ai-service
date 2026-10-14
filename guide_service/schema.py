"""Strict request and structured Guide-result validation."""

from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Any, Mapping
import uuid


SCHEMA_VERSION = "1"
SECTION_NAMES = (
    "artistAndEra",
    "subjectAndScene",
    "composition",
    "brushworkAndInk",
    "colorAndMaterial",
    "artisticConception",
    "culturalMeaning",
    "musicAssociation",
)

_PAINTING_GROUPS: dict[str, tuple[str, ...]] = {
    "basic": (
        "title",
        "creationYear",
        "creationDynastyRaw",
        "creationDynastyNormalized",
        "actualSize",
        "collectionInstitution",
    ),
    "artist": ("name", "birthYear", "birthPlace", "school"),
    "art": (
        "category",
        "subject",
        "paintingSchool",
        "style",
        "color",
        "composition",
        "artisticConception",
        "brushwork",
        "inkMethod",
        "paintingMaterial",
        "pigment",
        "seal",
        "culturalSymbol",
    ),
    "officialAnnotations": ("generatedText", "musicSceneDescription"),
}

MAX_PAINTING_FIELD_CHARS = 12_000
MAX_KNOWLEDGE_ITEMS = 5
MAX_KNOWLEDGE_CONTENT_CHARS = 8_000
MAX_TOTAL_KNOWLEDGE_CHARS = 8_000
MAX_SUMMARY_CHARS = 2_000
MAX_SECTION_CHARS = 4_000
MAX_HIGHLIGHT_CHARS = 500
MAX_REFERENCE_TITLE_CHARS = 512
MAX_RESULT_BYTES = 65_536

_HTML_PATTERN = re.compile(r"<\s*/?\s*[A-Za-z][^>]*>")
_CJK_PATTERN = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")


class GuideSchemaError(ValueError):
    """Strict, operator-safe validation failure."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ValidatedGuideRequest:
    request_id: str
    schema_version: str
    painting: Mapping[str, Any]
    knowledge: tuple[Mapping[str, str], ...]
    options: Mapping[str, str]

    @property
    def knowledge_by_id(self) -> dict[str, Mapping[str, str]]:
        return {item["sourceId"]: item for item in self.knowledge}

    def source_payload(self) -> dict[str, Any]:
        return {
            "painting": self.painting,
            "knowledge": list(self.knowledge),
            "options": self.options,
        }


@dataclass(frozen=True)
class ValidatedGuideResult:
    payload: Mapping[str, Any]
    canonical_json: str


def _object(value: Any, field: str) -> Mapping[str, Any]:
    if not isinstance(value, dict):
        raise GuideSchemaError("INVALID_REQUEST", field + " must be an object")
    return value


def _exact_keys(value: Mapping[str, Any], expected: set[str], field: str) -> None:
    if set(value) != expected:
        raise GuideSchemaError(
            "INVALID_REQUEST", field + " contains missing or unsupported fields"
        )


def _required_string(
    value: Any,
    field: str,
    maximum: int,
    *,
    trim: bool = True,
) -> str:
    if not isinstance(value, str):
        raise GuideSchemaError("INVALID_REQUEST", field + " must be a string")
    normalized = value.strip() if trim else value
    if not normalized:
        raise GuideSchemaError("INVALID_REQUEST", field + " must not be blank")
    if len(normalized) > maximum:
        raise GuideSchemaError("INVALID_REQUEST", field + " exceeds its size limit")
    return normalized


def _optional_source_string(value: Any, field: str) -> Any:
    if value is None:
        return None
    if not isinstance(value, str):
        raise GuideSchemaError(
            "INVALID_REQUEST", field + " must be a string or null"
        )
    if len(value) > MAX_PAINTING_FIELD_CHARS:
        raise GuideSchemaError("INVALID_REQUEST", field + " exceeds its size limit")
    return value


def _validate_uuid(value: Any, field: str) -> str:
    normalized = _required_string(value, field, 36)
    try:
        parsed = uuid.UUID(normalized)
    except (ValueError, AttributeError) as exception:
        raise GuideSchemaError("INVALID_REQUEST", field + " must be a UUID") from exception
    if str(parsed) != normalized.lower():
        raise GuideSchemaError(
            "INVALID_REQUEST", field + " must be a canonical UUID string"
        )
    return str(parsed)


def validate_guide_request(value: Any) -> ValidatedGuideRequest:
    request = _object(value, "request")
    _exact_keys(
        request,
        {"requestId", "schemaVersion", "painting", "knowledge", "options"},
        "request",
    )
    request_id = _validate_uuid(request.get("requestId"), "requestId")
    schema_version = _required_string(
        request.get("schemaVersion"), "schemaVersion", 16
    )
    if schema_version != SCHEMA_VERSION:
        raise GuideSchemaError("INVALID_REQUEST", "unsupported schemaVersion")

    painting_value = _object(request.get("painting"), "painting")
    _exact_keys(
        painting_value,
        {"paintingId", *set(_PAINTING_GROUPS)},
        "painting",
    )
    painting: dict[str, Any] = {
        "paintingId": _validate_uuid(
            painting_value.get("paintingId"), "painting.paintingId"
        )
    }
    for group_name, field_names in _PAINTING_GROUPS.items():
        source_group = _object(painting_value.get(group_name), "painting." + group_name)
        _exact_keys(source_group, set(field_names), "painting." + group_name)
        painting[group_name] = {
            field_name: _optional_source_string(
                source_group.get(field_name),
                "painting." + group_name + "." + field_name,
            )
            for field_name in field_names
        }

    knowledge_value = request.get("knowledge")
    if not isinstance(knowledge_value, list):
        raise GuideSchemaError("INVALID_REQUEST", "knowledge must be an array")
    if len(knowledge_value) > MAX_KNOWLEDGE_ITEMS:
        raise GuideSchemaError("INVALID_REQUEST", "knowledge contains too many items")
    knowledge: list[Mapping[str, str]] = []
    source_ids: set[str] = set()
    total_knowledge_chars = 0
    for index, raw_item in enumerate(knowledge_value):
        item = _object(raw_item, "knowledge item")
        _exact_keys(
            item, {"sourceId", "sourceType", "title", "content"}, "knowledge item"
        )
        prefix = "knowledge[" + str(index) + "]"
        source_id = _required_string(item.get("sourceId"), prefix + ".sourceId", 256)
        if source_id in source_ids:
            raise GuideSchemaError(
                "INVALID_REQUEST", "knowledge sourceId values must be unique"
            )
        source_ids.add(source_id)
        source_type = _required_string(
            item.get("sourceType"), prefix + ".sourceType", 64
        )
        title = _required_string(item.get("title"), prefix + ".title", 500)
        content = _required_string(
            item.get("content"),
            prefix + ".content",
            MAX_KNOWLEDGE_CONTENT_CHARS,
            trim=False,
        )
        total_knowledge_chars += len(content)
        if total_knowledge_chars > MAX_TOTAL_KNOWLEDGE_CHARS:
            raise GuideSchemaError(
                "INVALID_REQUEST", "knowledge exceeds the total character limit"
            )
        knowledge.append(
            {
                "sourceId": source_id,
                "sourceType": source_type,
                "title": title,
                "content": content,
            }
        )

    options_value = _object(request.get("options"), "options")
    _exact_keys(options_value, {"language", "detailLevel"}, "options")
    language = _required_string(options_value.get("language"), "options.language", 32)
    detail_level = _required_string(
        options_value.get("detailLevel"), "options.detailLevel", 32
    )
    if language != "zh-CN" or detail_level != "STANDARD":
        raise GuideSchemaError("INVALID_REQUEST", "unsupported guide options")

    return ValidatedGuideRequest(
        request_id=request_id,
        schema_version=schema_version,
        painting=painting,
        knowledge=tuple(knowledge),
        options={"language": language, "detailLevel": detail_level},
    )


def _reject_markup(value: str, field: str) -> None:
    if "```" in value:
        raise GuideSchemaError("INVALID_GUIDE_RESULT", field + " contains a code fence")
    if _HTML_PATTERN.search(value):
        raise GuideSchemaError("INVALID_GUIDE_RESULT", field + " contains HTML")


def _result_string(value: Any, field: str, maximum: int) -> str:
    if not isinstance(value, str):
        raise GuideSchemaError("INVALID_GUIDE_RESULT", field + " must be a string")
    normalized = value.strip()
    if not normalized:
        raise GuideSchemaError("INVALID_GUIDE_RESULT", field + " must not be blank")
    if len(normalized) > maximum:
        raise GuideSchemaError(
            "INVALID_GUIDE_RESULT", field + " exceeds its size limit"
        )
    _reject_markup(normalized, field)
    return normalized


def validate_guide_result(
    value: Any,
    supplied_knowledge: Mapping[str, Mapping[str, str]],
) -> ValidatedGuideResult:
    if not isinstance(value, dict):
        raise GuideSchemaError("INVALID_GUIDE_RESULT", "guide result must be an object")
    expected = {
        "schemaVersion",
        "summary",
        "sections",
        "highlights",
        "knowledgeReferences",
    }
    if set(value) != expected:
        raise GuideSchemaError(
            "INVALID_GUIDE_RESULT", "guide result fields do not match schemaVersion 1"
        )
    if value.get("schemaVersion") != SCHEMA_VERSION:
        raise GuideSchemaError(
            "INVALID_GUIDE_RESULT", "guide result schemaVersion is unsupported"
        )
    summary = _result_string(value.get("summary"), "summary", MAX_SUMMARY_CHARS)
    if not _CJK_PATTERN.search(summary):
        raise GuideSchemaError("INVALID_GUIDE_RESULT", "summary must be Chinese")

    raw_sections = value.get("sections")
    if not isinstance(raw_sections, dict) or set(raw_sections) != set(SECTION_NAMES):
        raise GuideSchemaError(
            "INVALID_GUIDE_RESULT", "sections must contain the eight v1 fields"
        )
    sections: dict[str, Any] = {}
    for section_name in SECTION_NAMES:
        raw_section = raw_sections[section_name]
        if raw_section is None:
            sections[section_name] = None
        else:
            section = _result_string(
                raw_section, "sections." + section_name, MAX_SECTION_CHARS
            )
            if not _CJK_PATTERN.search(section):
                raise GuideSchemaError(
                    "INVALID_GUIDE_RESULT", "non-null sections must be Chinese"
                )
            sections[section_name] = section

    raw_highlights = value.get("highlights")
    if not isinstance(raw_highlights, list) or not 2 <= len(raw_highlights) <= 5:
        raise GuideSchemaError(
            "INVALID_GUIDE_RESULT", "highlights must contain between 2 and 5 items"
        )
    highlights: list[str] = []
    normalized_highlights: set[str] = set()
    for index, raw_highlight in enumerate(raw_highlights):
        highlight = _result_string(
            raw_highlight, "highlights[" + str(index) + "]", MAX_HIGHLIGHT_CHARS
        )
        if not _CJK_PATTERN.search(highlight):
            raise GuideSchemaError(
                "INVALID_GUIDE_RESULT", "highlights must be Chinese"
            )
        duplicate_key = " ".join(highlight.split()).casefold()
        if duplicate_key in normalized_highlights:
            raise GuideSchemaError(
                "INVALID_GUIDE_RESULT", "duplicate highlights are not allowed"
            )
        normalized_highlights.add(duplicate_key)
        highlights.append(highlight)

    raw_references = value.get("knowledgeReferences")
    if not isinstance(raw_references, list):
        raise GuideSchemaError(
            "INVALID_GUIDE_RESULT", "knowledgeReferences must be an array"
        )
    if len(raw_references) > len(supplied_knowledge):
        raise GuideSchemaError(
            "INVALID_GUIDE_RESULT", "knowledgeReferences contains too many items"
        )
    references: list[dict[str, str]] = []
    referenced_ids: set[str] = set()
    for index, raw_reference in enumerate(raw_references):
        if not isinstance(raw_reference, dict) or set(raw_reference) != {
            "sourceId",
            "sourceType",
            "title",
        }:
            raise GuideSchemaError(
                "INVALID_GUIDE_RESULT", "knowledge reference fields are invalid"
            )
        prefix = "knowledgeReferences[" + str(index) + "]"
        source_id = _result_string(
            raw_reference.get("sourceId"), prefix + ".sourceId", 256
        )
        source_type = _result_string(
            raw_reference.get("sourceType"),
            prefix + ".sourceType",
            64,
        )
        title = _result_string(
            raw_reference.get("title"), prefix + ".title", MAX_REFERENCE_TITLE_CHARS
        )
        supplied = supplied_knowledge.get(source_id)
        if supplied is None:
            raise GuideSchemaError(
                "INVALID_GUIDE_RESULT", "knowledge reference was not supplied"
            )
        if source_id in referenced_ids:
            raise GuideSchemaError(
                "INVALID_GUIDE_RESULT", "duplicate knowledge references are not allowed"
            )
        if supplied.get("sourceType") != source_type or supplied.get("title") != title:
            raise GuideSchemaError(
                "INVALID_GUIDE_RESULT", "knowledge reference metadata changed"
            )
        referenced_ids.add(source_id)
        references.append(
            {"sourceId": source_id, "sourceType": source_type, "title": title}
        )

    normalized: dict[str, Any] = {
        "schemaVersion": SCHEMA_VERSION,
        "summary": summary,
        "sections": sections,
        "highlights": highlights,
        "knowledgeReferences": references,
    }
    canonical = json.dumps(
        normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    if len(canonical.encode("utf-8")) > MAX_RESULT_BYTES:
        raise GuideSchemaError(
            "INVALID_GUIDE_RESULT", "guide result exceeds the total size limit"
        )
    return ValidatedGuideResult(payload=normalized, canonical_json=canonical)


def parse_guide_result_json(
    content: str,
    supplied_knowledge: Mapping[str, Mapping[str, str]],
) -> ValidatedGuideResult:
    if not isinstance(content, str):
        raise GuideSchemaError(
            "INVALID_GUIDE_RESULT", "provider content must be a JSON string"
        )
    if len(content.encode("utf-8")) > MAX_RESULT_BYTES:
        raise GuideSchemaError(
            "INVALID_GUIDE_RESULT", "provider JSON exceeds the result size limit"
        )
    if "```" in content:
        raise GuideSchemaError(
            "INVALID_GUIDE_RESULT", "provider JSON must not use code fences"
        )
    try:
        parsed = json.loads(content)
    except (json.JSONDecodeError, UnicodeError) as exception:
        raise GuideSchemaError(
            "INVALID_GUIDE_RESULT", "provider content is not valid JSON"
        ) from exception
    return validate_guide_result(parsed, supplied_knowledge)
