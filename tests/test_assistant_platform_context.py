"""Offline prompt-boundary checks. These do not claim model compliance."""
import json
from types import SimpleNamespace
from app.assistant.prompts import system_prompt


def test_explicit_platform_snapshot_is_data_and_never_a_visual_inspection_claim():
    ref = {"id": "synthetic-citation", "type": "PLATFORM_ARTWORK", "artworkId": "synthetic-uuid",
           "version": "synthetic-batch:2", "source": {"imageSupplied": False, "contextTruncated": True},
           "text": '[{"key":"core.author_name","value":"未知","assertionStatus":"UNVERIFIED"}]',
           "untrustedUrl": "https://invalid.example/not-a-tool"}
    result = system_prompt(SimpleNamespace(purpose="CHAT", citations=[ref], retrievalStates={"artworks": "AVAILABLE", "ragflow": "DISABLED"}))
    assert "用户明确选择" in result and "不是自动数据库搜索" in result
    assert "不得声称看到了其画面" in result and "区分记录中的事实" in result
    data = json.loads(result.split("\nreference_data=", 1)[1])
    assert data["citations"][0]["text"] == ref["text"]
    assert "untrustedUrl" not in data["citations"][0]
    assert data["citations"][0]["source"]["imageSupplied"] is False
    assert data["retrieval"]["ragflow"] == "DISABLED"
