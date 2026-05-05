from enum import Enum
from pydantic import BaseModel, Field


class Decision(str, Enum):
    APPROVE = "approve"
    ESCALATE = "escalate"


class InlineComment(BaseModel):
    path: str
    line: int
    body: str


class ReviewResult(BaseModel):
    decision: Decision
    summary: str
    inline_comments: list[InlineComment] = Field(default_factory=list)
    suggested_reviewers: list[str] = Field(default_factory=list)
    risk_notes: list[str] = Field(default_factory=list)


class RunMetrics(BaseModel):
    model: str
    input_tokens: int
    output_tokens: int
    latency_ms: int
