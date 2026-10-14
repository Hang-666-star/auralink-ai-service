from __future__ import annotations
from typing import Literal
from uuid import UUID
from pydantic import Field
from .config import StrictModel

class ImageInput(StrictModel):
    mimeType: Literal["image/jpeg", "image/png"]
    dataBase64: str = Field(min_length=1, max_length=14000000)

class Message(StrictModel):
    role: Literal["user", "assistant"]
    content: str = Field(default="", max_length=64000)
    images: list[ImageInput] = Field(default_factory=list, max_length=4)

class Budgets(StrictModel):
    totalMs: int | None = Field(default=None, ge=100, le=180000)
    maxOutputTokens: int = Field(default=2048, ge=1, le=8192)

class Execution(StrictModel):
    contractVersion: Literal[1]
    runId: UUID
    attemptId: UUID
    fence: int = Field(ge=1)
    configSha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    purpose: Literal["CHAT", "SUMMARY"]
    modelKey: str = Field(min_length=1, max_length=80)
    messages: list[Message] = Field(min_length=1, max_length=100)
    citations: list[dict] = Field(default_factory=list, max_length=16)
    retrievalStates: dict[str, str] = Field(default_factory=dict, max_length=2)
    budgets: Budgets = Field(default_factory=Budgets)

class Retrieval(StrictModel):
    contractVersion: Literal[1]
    configSha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    enabled: bool
    query: str = Field(default="", max_length=2000)
