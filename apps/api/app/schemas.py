from datetime import datetime
from typing import Literal
from pydantic import BaseModel, Field, field_validator, model_validator


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    background: str = Field(default="", max_length=10000)


class RunCreate(BaseModel):
    question: str = Field(min_length=5, max_length=5000)
    subjects: list[str] = Field(default_factory=list, max_length=6)
    dimensions: list[str] = Field(default_factory=list, max_length=8)
    parent_run_id: str | None = None
    as_of: str | None = None
    max_evidence_age_days: int | None = Field(default=None, ge=1, le=3650)
    require_freshness: bool = False


class ResearchPlan(BaseModel):
    goal: str = Field(min_length=5, max_length=5000)
    subjects: list[str] = Field(min_length=1, max_length=6)
    dimensions: list[str] = Field(min_length=1, max_length=8)
    questions: list[str] = Field(default_factory=list, max_length=12)
    max_search_calls: int = Field(default=24, ge=1, le=60)
    max_gap_rounds: int = Field(default=1, ge=0, le=2)
    as_of: str | None = Field(default=None, max_length=32)
    max_evidence_age_days: int = Field(default=365, ge=1, le=3650)
    require_freshness: bool = False

    @field_validator("as_of", mode="before")
    @classmethod
    def clean_as_of(cls, value):
        if value in (None, ""):
            return None
        text = str(value).strip()
        try:
            datetime.strptime(text[:10], "%Y-%m-%d")
        except ValueError as exc:
            raise ValueError("as_of must be YYYY-MM-DD") from exc
        return text[:10]

    @model_validator(mode="after")
    def clean_items(self):
        for name in ("subjects", "dimensions"):
            values = list(dict.fromkeys(s.strip() for s in getattr(self, name) if s.strip()))
            if not values or any(len(s) > 120 for s in values):
                raise ValueError(f"{name} must contain nonempty items under 120 characters")
            setattr(self, name, values)
        return self


class PlanUpdate(BaseModel):
    expected_revision: int
    plan: ResearchPlan


class RunCommand(BaseModel):
    action: Literal["start", "pause", "resume", "cancel", "retry"]
    expected_revision: int


class ClaimDraft(BaseModel):
    subject: str
    dimension: str
    text: str = Field(max_length=2000)
    evidence_ids: list[str] = Field(default_factory=list, max_length=8)
    conflict_evidence_ids: list[str] = Field(default_factory=list, max_length=8)
    kind: Literal["fact", "analysis", "unknown"] = "fact"


class ClaimBundle(BaseModel):
    claims: list[ClaimDraft] = Field(default_factory=list, max_length=100)
    summary: str = Field(default="", max_length=3000)


class ProjectToolCall(BaseModel):
    query: str | None = None
    chunk_id: str | None = None

