from .models import AttackScenario, Finding, TestResult


def build_findings(tests: list[AttackScenario], results: list[TestResult]) -> list[Finding]:
    by_id = {t.id: t for t in tests}
    out: list[Finding] = []
    for r in results:
        if r.status not in ("failed", "flaky"):
            continue
        t = by_id[r.test_id]
        first_goto = next(
            (s.value or s.url or "/" for s in t.steps if s.action in ("goto", "navigate")),
            "/",
        )
        reproducible = r.status == "failed"
        severity = getattr(t, "severity_if_failed", None) or getattr(t, "severity_hint", "medium")
        title = getattr(t, "title", None) or getattr(t, "rationale", "Security/QA defect discovered")
        attack_type = getattr(t, "attack_type", None) or t.category

        # Construct numbered reproduction steps
        repro: list[str] = []
        for i, s in enumerate(t.steps, 1):
            target_str = f" on {s.target}" if s.target else ""
            val_str = f" with '{s.value}'" if s.value else ""
            repro.append(f"{i}. {s.action}{target_str}{val_str}")
        repro.append(f"Result: {r.actual or 'Assertion failed'}")

        out.append(
            Finding(
                id=f"VG-{len(out) + 1:03d}",
                attack_id=t.id,
                category=t.category,
                type=attack_type,
                severity=severity,
                title=title[:140],
                target=first_goto,
                expected=r.expected or getattr(t, "expected_behavior", ""),
                actual=r.actual or "",
                reproducible=reproducible,
                confidence=0.95 if reproducible else 0.4,
                evidence=r.evidence,
                steps=t.steps,
                reproduction_steps=repro,
            )
        )
    return out
