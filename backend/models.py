"""Pydantic v2 request/response models for all API boundaries."""

from typing import Literal

from pydantic import BaseModel, ConfigDict


# --- Request Models ---

class TranscriptRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: str


GranularityLevel = Literal[
    "one_liner", "tldr", "short", "detailed", "chapters", "custom"
]


class SummarizeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: str
    granularity: GranularityLevel
    custom_instruction: str | None = None
    output_language: str | None = None


# --- Response Models ---

class TranscriptResponse(BaseModel):
    video_id: str
    title: str
    transcript: str
    language: str
    source: Literal["captions", "whisper"]
    duration_seconds: float
    platform: str = "youtube"


class SummaryResponse(BaseModel):
    granularity: str
    summary: str
    chunk_count: int
    model: str
    input_tokens: int
    output_tokens: int


class ErrorResponse(BaseModel):
    error_code: str
    message: str


class HealthResponse(BaseModel):
    status: str
