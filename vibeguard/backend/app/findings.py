from .models import Finding, TestCase, TestResult


def build_findings(tests: list[TestCase], results: list[TestResult]) -> list[Finding]:
    by_id = {t.id: t for t in tests}
    out: list[Finding] = []
    for r in results:
        if r.status not in ("failed", "flaky"):
            continue
        t = by_id[r.test_id]
        first_goto = next((s.value for s in t.steps if s.action == "goto"), "/")
        reproducible = r.status == "failed"
        out.append(Finding(
            id=f"VG-{len(out) + 1:03d}", category=t.category, type=t.attack_type,
            severity=t.severity_hint, title=t.rationale[:140], target=first_goto,
            expected=r.expected or "", actual=r.actual or "",
            reproducible=reproducible, confidence=0.95 if reproducible else 0.4,
            evidence=r.evidence, steps=t.steps))
    return out
