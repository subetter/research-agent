from typing import Literal
from pydantic import BaseModel, Field, model_validator


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    background: str = Field(default="", max_length=10000)


class RunCreate(BaseModel):
    question: str = Field(min_length=5, max_length=5000)
    subjects: list[str] = Field(default_factory=list, max_length=6)
    dimensions: list[str] = Field(default_factory=list, max_length=8)
    parent_run_id: str | None = None


class ResearchPlan(BaseModel):
    goal: str = Field(min_length=5, max_length=5000)
    subjects: list[str] = Field(min_length=1, max_length=6)
    dimensions: list[str] = Field(min_length=1, max_length=8)
    questions: list[str] = Field(default_factory=list, max_length=12)
    max_search_calls: int = Field(default=24, ge=1, le=60)
    max_gap_rounds: int = Field(default=1, ge=0, le=2)
    as_of: str = Field(default="", max_length=40)
    regions: list[str] = Field(default_factory=list, max_length=8)

    @model_validator(mode="after")
    def clean_items(self):
        for name in ("subjects", "dimensions"):
            values = list(dict.fromkeys(s.strip() for s in getattr(self, name) if s.strip()))
            if not values or any(len(s) > 120 for s in values):
                raise ValueError(f"{name} must contain nonempty items under 120 characters")
            setattr(self, name, values)
        self.regions = list(dict.fromkeys(item.strip() for item in self.regions if item.strip()))
        self.as_of = self.as_of.strip()
        return self


class PlanUpdate(BaseModel):
    expected_revision: int
    plan: ResearchPlan


class ClarifyUpdate(BaseModel):
    expected_revision: int
    subjects: list[str] = Field(min_length=1, max_length=6)
    dimensions: list[str] = Field(min_length=1, max_length=8)

    @model_validator(mode="after")
    def clean_items(self):
        for name in ("subjects", "dimensions"):
            values = list(dict.fromkeys(s.strip() for s in getattr(self, name) if s.strip()))
            if not values or any(len(s) > 120 for s in values):
                raise ValueError(f"{name} must contain nonempty items under 120 characters")
            setattr(self, name, values)
        return self


class DocumentUpdate(BaseModel):
    citable: bool


class RunCommand(BaseModel):
    action: Literal["start", "pause", "resume", "cancel", "retry"]
    expected_revision: int


class ClaimDraft(BaseModel):
    subject: str
    dimension: str
    text: str = Field(max_length=2000)
    evidence_ids: list[str] = Field(default_factory=list, max_length=8)
    kind: Literal["fact", "analysis", "unknown"] = "fact"


class ClaimBundle(BaseModel):
    claims: list[ClaimDraft] = Field(default_factory=list, max_length=100)
    summary: str = Field(default="", max_length=3000)


class ClaimVerdict(BaseModel):
    subject: str
    dimension: str
    verification: Literal["fully", "partial", "contradicted", "unrelated"]


class VerificationBundle(BaseModel):
    results: list[ClaimVerdict] = Field(default_factory=list, max_length=100)

