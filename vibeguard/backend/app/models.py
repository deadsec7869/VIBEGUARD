import time
from typing import Literal, Optional
from pydantic import BaseModel, Field

Action = Literal["goto", "navigate", "fill", "click", "double_click", "clear",
                 "expect_url", "expect_text", "expect_no_text", "resize", "reload",
                 "go_back", "go_forward"]
Category = Literal["functionality", "functional", "security", "authentication",
                   "authorization", "reliability", "ux", "performance"]
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


# ---- Action DSL & Attack Scenarios ----
class Step(BaseModel):
    action: Action
    target: Optional[str] = None
    value: Optional[str] = None
    url: Optional[str] = None  # convenient alias for navigate / goto value


class AttackScenario(BaseModel):
    id: str
    category: Category
    title: str
    goal: str
    preconditions: list[str] = Field(default_factory=list)
    steps: list[Step]
    expected_behavior: str
    severity_if_failed: Severity = "medium"
    rationale: str = ""
    confidence: float = 0.9


# Alias TestCase to AttackScenario for backwards compatibility with Slice 1
TestCase = AttackScenario


# ---- LLM-facing Attack Schema (no defaults for Gemini response_schema compatibility) ----
class LLMStep(BaseModel):
    action: Action
    target: Optional[str]
    value: Optional[str]


class LLMAttackScenario(BaseModel):
    id: str
    category: Category
    title: str
    goal: str
    steps: list[LLMStep]
    expected_behavior: str
    severity_if_failed: Severity
    rationale: str


class LLMAttackPlan(BaseModel):
    scenarios: list[LLMAttackScenario]


# Backwards compatibility alias
LLMTest = LLMAttackScenario
LLMPlan = LLMAttackPlan


# ---- Execution & findings ----
class Evidence(BaseModel):
    screenshots: list[str] = Field(default_factory=list)   # relative to artifacts dir
    console_errors: list[str] = Field(default_factory=list)
    network_events: list[dict] = Field(default_factory=list)
    final_url: str = ""
    page_text_excerpt: str = ""
    steps_taken: list[str] = Field(default_factory=list)


class TestResult(BaseModel):
    __test__ = False
    test_id: str
    status: Literal["passed", "failed", "flaky", "error"]
    runs: int
    failures: int
    duration_ms: int
    failed_step: Optional[int] = None
    expected: Optional[str] = None
    actual: Optional[str] = None
    evidence: Evidence = Field(default_factory=Evidence)


# ---- Fix Agent Models ----
class PatchOperation(BaseModel):
    file: Optional[str] = None
    type: Literal["replace"] = "replace"
    old_text: str
    new_text: str
    expected_matches: int = 1


class StructuredPatch(BaseModel):
    file: str
    operations: list[PatchOperation]


class FixResult(BaseModel):
    finding_id: str
    status: Literal["pending", "analyzing", "patching", "retesting", "regression_testing", "fixed", "failed", "regression_failed"] = "pending"
    attempts: int = 0
    root_cause: str = ""
    files_changed: list[str] = Field(default_factory=list)
    patch_summary: str = ""
    diff: str = ""
    patch_operations: list[PatchOperation] = Field(default_factory=list)
    targeted_test: dict = Field(default_factory=dict)
    targeted_test_passed: bool = False
    regression_test: dict = Field(default_factory=dict)
    regression_passed: bool = False
    verification_status: str = "pending"


class VerificationResult(BaseModel):
    finding_id: str
    status: Literal["pending", "running", "verified", "rejected", "error"] = "pending"
    confidence: float = 0.0
    verifier_reason: str = ""
    reproduction_steps: list[str] = Field(default_factory=list)
    expected: str = ""
    actual: str = ""
    evidence: Optional[Evidence] = None
    targeted_test_passed: bool = False
    regression_passed: bool = False
    verified_at: Optional[float] = None


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
    reproduction_steps: list[str] = Field(default_factory=list)
    suspected_files: list[str] = Field(default_factory=list)
    fix_status: str = "pending"
    verification_status: str = "pending"
    fix_result: Optional[FixResult] = None
    verification_result: Optional[VerificationResult] = None


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
    tests: list[AttackScenario] = Field(default_factory=list)
    results: list[TestResult] = Field(default_factory=list)
    findings: list[Finding] = Field(default_factory=list)
    events: list[Event] = Field(default_factory=list)
    error: Optional[str] = None
    started_at: float = Field(default_factory=time.time)
    finished_at: Optional[float] = None

