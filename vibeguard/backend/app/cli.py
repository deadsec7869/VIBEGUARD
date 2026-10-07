"""python -m app.cli http://localhost:3000 [--replay]"""
import asyncio
import sys

from .orchestrator import run_scan


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        sys.exit("usage: python -m app.cli <target_url> [--replay]")

    target_url = args[0]
    replay = "--replay" in sys.argv
    run = asyncio.run(run_scan(target_url, replay))

    total_elements = sum(len(p.elements) for p in run.app_map.pages) if run.app_map else 0
    passed = sum(1 for r in run.results if r.status == "passed")
    failed = sum(1 for r in run.results if r.status in ("failed", "flaky"))
    errors = sum(1 for r in run.results if r.status == "error")

    print("\n" + "=" * 50)
    print("VIBEGUARD AI - SLICE 2: VIBE ATTACK")
    print("=" * 50)

    print("\nDISCOVERY")
    print(f"{total_elements} elements discovered")

    print("\nVIBE ATTACK")
    print(f"{len(run.tests)} attacks generated")

    print("\nEXECUTION")
    print(f"{passed} passed")
    print(f"{failed} failed")
    if errors:
        print(f"{errors} error(s)")

    print("\nFINDINGS")
    if not run.findings:
        print("No defects discovered.")
    else:
        for f in run.findings:
            title_display = f.title
            if "direct protected-route access" in f.title.lower() or "unauthenticated" in f.title.lower():
                title_display = "Authentication bypass"
            elif "malformed email" in f.title.lower() or "input validation" in f.title.lower():
                title_display = "Input validation failure"
            print(f"{f.id} {f.severity.upper()} {title_display}")
            print(f"  Target:       {f.target}")
            print(f"  Expected:     {f.expected}")
            print(f"  Actual:       {f.actual}")
            print(f"  Reproducible: {f.reproducible} (confidence {f.confidence:.2f})")
            if f.evidence.screenshots:
                print(f"  Screenshot:   {f.evidence.screenshots[0]}")
            if f.reproduction_steps:
                print("  Reproduction:")
                for step_desc in f.reproduction_steps:
                    print(f"    {step_desc}")
            print()


if __name__ == "__main__":
    main()
