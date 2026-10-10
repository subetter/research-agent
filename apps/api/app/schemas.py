from typing import Literal
from pydantic import BaseModel, Field, model_validator


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    background: str = Field(default="", max_length=10000)


class RunCreate(BaseModel):
    question: str = Field(min_length=5, max_length=5000)
    subjects: list[str] = Field(default_factory=list, max_length=6)
    dimensions: list[str] = Field(default_factory=list, max_length=8)
    mode: Literal["grid", "general"] | None = None
    parent_run_id: str | None = None


class SubQuestion(BaseModel):
    id: str = Field(default="", max_length=40)
    question: str = Field(min_length=2, max_length=400)
    parent_id: str | None = Field(default=None, max_length=40)


class ResearchPlan(BaseModel):
    goal: str = Field(min_length=5, max_length=5000)
    mode: Literal["grid", "general"] = "grid"
    subjects: list[str] = Field(default_factory=list, max_length=6)
    dimensions: list[str] = Field(default_factory=list, max_length=8)
    questions: list[str] = Field(default_factory=list, max_length=12)
    subquestions: list[SubQuestion] = Field(default_factory=list, max_length=24)
    max_search_calls: int = Field(default=24, ge=1, le=60)
    max_gap_rounds: int = Field(default=1, ge=0, le=2)
    max_followup_rounds: int = Field(default=2, ge=0, le=4)
    as_of: str = Field(default="", max_length=40)
    regions: list[str] = Field(default_factory=list, max_length=8)
    skill_name: str = Field(default="", max_length=80)
    skill_version: str = Field(default="", max_length=20)

    @model_validator(mode="after")
    def clean_items(self):
        from .research_mode import flatten_subquestions, topic_label

        for name in ("subjects", "dimensions"):
            values = list(dict.fromkeys(s.strip() for s in getattr(self, name) if s.strip()))
            if any(len(s) > 120 for s in values):
                raise ValueError(f"{name} must contain nonempty items under 120 characters")
            setattr(self, name, values)
        self.regions = list(dict.fromkeys(item.strip() for item in self.regions if item.strip()))
        self.as_of = self.as_of.strip()
        self.subquestions = [SubQuestion.model_validate(item) for item in flatten_subquestions([item.model_dump() if hasattr(item, "model_dump") else item for item in self.subquestions])]
        if self.mode == "general":
            if not self.subquestions:
                raise ValueError("general mode requires subquestions")
            if not self.subjects:
                self.subjects = [topic_label(self.goal)]
            if not self.dimensions:
                self.dimensions = ["要点"]
        elif not self.subjects or not self.dimensions:
            raise ValueError("subjects and dimensions must contain nonempty items under 120 characters")
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


class ClaimEdit(BaseModel):
    id: str
    text: str = Field(min_length=1, max_length=2000)


class ArtifactEdit(BaseModel):
    expected_revision: int
    base_version: int = Field(ge=1)
    summary: str | None = Field(default=None, max_length=3000)
    claims: list[ClaimEdit] = Field(default_factory=list, max_length=100)


class RunCommand(BaseModel):
    action: Literal["start", "pause", "resume", "cancel", "retry"]
    expected_revision: int


class ClaimDraft(BaseModel):
    subject: str
    dimension: str
    text: str = Field(max_length=2000)
    evidence_ids: list[str] = Field(default_factory=list, max_length=8)
    kind: Literal["fact", "analysis", "unknown"] = "fact"
    subquestion_id: str | None = Field(default=None, max_length=40)


class ReportSection(BaseModel):
    id: str = Field(max_length=80)
    title: str = Field(max_length=200)
    body: str = Field(default="", max_length=20000)
    evidence_ids: list[str] = Field(default_factory=list, max_length=16)


class ReportOutlineItem(BaseModel):
    id: str = Field(max_length=80)
    title: str = Field(max_length=200)


class ReportChart(BaseModel):
    id: str = Field(max_length=40)
    title: str = Field(max_length=200)
    kind: Literal["bar", "line"] = "bar"
    labels: list[str] = Field(default_factory=list, max_length=12)
    values: list[float] = Field(default_factory=list, max_length=12)


class ReportTable(BaseModel):
    id: str = Field(max_length=40)
    title: str = Field(default="", max_length=200)
    headers: list[str] = Field(default_factory=list, max_length=12)
    rows: list[list[str]] = Field(default_factory=list, max_length=40)


class ClaimBundle(BaseModel):
    claims: list[ClaimDraft] = Field(default_factory=list, max_length=100)
    summary: str = Field(default="", max_length=3000)
    outline: list[ReportOutlineItem] = Field(default_factory=list, max_length=24)
    sections: list[ReportSection] = Field(default_factory=list, max_length=24)
    open_questions: list[str] = Field(default_factory=list, max_length=12)
    charts: list[ReportChart] = Field(default_factory=list, max_length=6)
    tables: list[ReportTable] = Field(default_factory=list, max_length=4)


class ClaimVerdict(BaseModel):
    subject: str
    dimension: str
    verification: Literal["fully", "partial", "contradicted", "unrelated"]


class VerificationBundle(BaseModel):
    results: list[ClaimVerdict] = Field(default_factory=list, max_length=100)

