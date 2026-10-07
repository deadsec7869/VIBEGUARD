import time
from typing import Literal, Optional
from pydantic import BaseModel, Field

Action = Literal["goto", "fill", "click", "double_click", "expect_url",
                 "expect_text", "expect_no_text", "resize", "reload"]
Category = Literal["functionality", "security", "reliability", "ux", "performance"]
Severity = Literal["critical", "high", "medium", "low"]


# ---- Discovery ----
class Element(BaseModel):
    role: str
    name: str
    tag: str
    target: str            # DSL target, e.g. "label:Email" or "role:button:Sign in"
    type: Optional[str] = None
    href: Optional[str] = None


class Page(BaseModel):
    path: str
    final_path: str
    status: int
    title: str = ""
    elements: list[Element] = Field(default_factory=list)
    links: list[str] = Field(default_factory=list)
    text_excerpt: str = ""

    @property
    def redirected(self) -> bool:
        return self.path != self.final_path


class AppMap(BaseModel):
    base_url: str
    pages: list[Page] = Field(default_factory=list)


# ---- Plan (internal) ----
class Step(BaseModel):
    action: Action
    target: Optional[str] = None
    value: Optional[str] = None


class TestCase(BaseModel):
    id: str
    category: Category
    attack_type: str
    rationale: str
    severity_hint: Severity = "medium"
    steps: list[Step]


# ---- Plan (LLM-facing). No defaults: the Gemini response_schema rejects them. ----
class LLMStep(BaseModel):
    action: Action
    target: Optional[str]
    value: Optional[str]


class LLMTest(BaseModel):
    category: Category
    attack_type: str
    rationale: str
    severity: Severity
    steps: list[LLMStep]


class LLMPlan(BaseModel):
    tests: list[LLMTest]


# ---- Execution & findings ----
class Evidence(BaseModel):
    screenshots: list[str] = Field(default_factory=list)   # relative to artifacts dir
    console_errors: list[str] = Field(default_factory=list)
    network_events: list[dict] = Field(default_factory=list)
    final_url: str = ""
    page_text_excerpt: str = ""
    steps_taken: list[str] = Field(default_factory=list)


class TestResult(BaseModel):
    test_id: str
    status: Literal["passed", "failed", "flaky", "error"]
    runs: int
    failures: int
    duration_ms: int
    failed_step: Optional[int] = None
    expected: Optional[str] = None
    actual: Optional[str] = None
    evidence: Evidence = Field(default_factory=Evidence)


class Finding(BaseModel):
    id: str
    category: Category
    type: str
    severity: Severity
    title: str
    target: str
    expected: str
    actual: str
    reproducible: bool
    confidence: float
    evidence: Evidence
    steps: list[Step]
    suspected_files: list[str] = Field(default_factory=list)
    fix_status: str = "pending"
    verification_status: str = "pending"


class Event(BaseModel):
    ts: float = Field(default_factory=time.time)
    stage: str
    level: str = "info"
    message: str


class Run(BaseModel):
    id: str
    target_url: str
    replay: bool = False
    status: Literal["running", "completed", "failed"] = "running"
    plan_source: str = ""
    app_map: Optional[AppMap] = None
    tests: list[TestCase] = Field(default_factory=list)
    results: list[TestResult] = Field(default_factory=list)
    findings: list[Finding] = Field(default_factory=list)
    events: list[Event] = Field(default_factory=list)
    error: Optional[str] = None
    started_at: float = Field(default_factory=time.time)
    finished_at: Optional[float] = None
