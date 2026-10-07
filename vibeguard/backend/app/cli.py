"""python -m app.cli http://localhost:3000 [--replay]"""
import asyncio
import sys

from .orchestrator import run_scan


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        sys.exit("usage: python -m app.cli <target_url> [--replay]")
    run = asyncio.run(run_scan(args[0], "--replay" in sys.argv))
    print(f"\nRun {run.id}: {run.status}, {len(run.findings)} finding(s)")
    for f in run.findings:
        print(f"  {f.id} [{f.severity}] {f.title}\n      expected: {f.expected}\n      actual:   {f.actual}"
              f"\n      repro={f.reproducible} confidence={f.confidence} screenshot={f.evidence.screenshots}")


if __name__ == "__main__":
    main()
