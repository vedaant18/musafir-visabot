"""Pydantic models matching the Streamlit harness API contract."""

from __future__ import annotations
from typing import Optional
from pydantic import BaseModel, Field


# ── Request Models ──────────────────────────────────────────────────

class UserContext(BaseModel):
    """User context sent by the harness."""
    nationality: Optional[str] = None
    residencyCountry: Optional[str] = None
    travelMonth: Optional[str] = None
    interests: Optional[list[str]] = None
    budgetBand: Optional[str] = None
    hasVisaOrPermit: Optional[list[str]] = None
    stayingWithFamily: Optional[bool] = None
    travelGroup: Optional[str] = None
    travelInDays: Optional[int] = None


class ChatMessage(BaseModel):
    """A previous message in the conversation history."""
    role: str
    content: str


class ChatRequest(BaseModel):
    """Incoming request from the harness — POST /vendor/chat."""
    message: str
    context: Optional[UserContext] = None
    history: Optional[list[ChatMessage]] = None


# ── Response Models ─────────────────────────────────────────────────

class DocumentRef(BaseModel):
    """A required document reference."""
    docCode: str
    mandatory: bool


class FinalResult(BaseModel):
    """Structured result data."""
    destinations: list[str] = Field(default_factory=list)
    skuCodes: list[str] = Field(default_factory=list)
    documents: list[DocumentRef] = Field(default_factory=list)
    processingTimeDays: int = 0


class Trace(BaseModel):
    """Debugging / traceability info."""
    retrieved: dict = Field(default_factory=dict)
    matchedRules: list[str] = Field(default_factory=list)
    appliedAdjustments: list[str] = Field(default_factory=list)


class Meta(BaseModel):
    """Response metadata."""
    latencyMs: int = 0


class ChatResponse(BaseModel):
    """Outgoing response to the harness."""
    answerText: str = ""
    final: FinalResult = Field(default_factory=FinalResult)
    trace: Trace = Field(default_factory=Trace)
    meta: Meta = Field(default_factory=Meta)
