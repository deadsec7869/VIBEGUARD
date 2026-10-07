import asyncio
import sys
import json
from pathlib import Path

from .models import Run, Finding
from .verification_agent import run_verification_agent

def get_run(run_id: str, artifacts_dir: Path) -> Run:
    run_file = artifacts_dir / run_id / "run.json"
    if not run_file.exists():
        sys.exit(f"Run file not found: {run_file}")
    data = json.loads(run_file.read_text(encoding="utf-8"))
    return Run(**data)

def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if len(args) < 2:
        sys.exit("usage: python -m app.cli_verify <run_id> <finding_id> [--cwd <path>] [--cmd <start_cmd>]")
    
    run_id = args[0]
    finding_id = args[1]
    
    cwd = Path(sys.argv[sys.argv.index("--cwd") + 1]) if "--cwd" in sys.argv else Path.cwd().parent
    cmd = sys.argv[sys.argv.index("--cmd") + 1] if "--cmd" in sys.argv else "node server.js"
    
    workspace_dir = cwd
    if not (workspace_dir / "demo-app").exists():
        if (Path.cwd().parents[1] / "demo-app").exists():
            workspace_dir = Path.cwd().parents[1]
        elif (Path.cwd() / "demo-app").exists():
            workspace_dir = Path.cwd()

    artifacts_dir = workspace_dir / "artifacts"
    if os := __import__('os').getenv("VIBEGUARD_ARTIFACTS"):
        artifacts_dir = Path(os)
    elif (workspace_dir / "artifacts").exists():
        artifacts_dir = workspace_dir / "artifacts"

    run = get_run(run_id, artifacts_dir)
    finding = next((f for f in run.findings if f.id == finding_id), None)
    if not finding:
        sys.exit(f"Finding {finding_id} not found in run {run_id}")

    print("\n" + "=" * 60)
    print(f"VIBEGUARD VERIFICATION AGENT: TARGETING {finding.id} ({finding.title})")
    print("=" * 60 + "\n")

    def emit(stage: str, message: str, level: str = "info"):
        print(f"[{level.upper():5s}] {message}")

    result = asyncio.run(run_verification_agent(
        finding=finding,
        run=run,
        workspace_dir=workspace_dir,
        restart_cmd=cmd,
        emit=emit
    ))

    print("\n" + "=" * 60)
    print("INDEPENDENT VERIFICATION RESULT")
    print("=" * 60)
    print(f"Status:               {result.status.upper()}")
    print(f"Confidence:           {result.confidence}")
    print(f"Reason:               {result.verifier_reason}")
    print(f"Targeted Test Passed: {result.targeted_test_passed}")
    print(f"Regression Passed:    {result.regression_passed}")
    
    if result.status == "verified":
        print("\nFix verified successfully. Bug is no longer reproducible.")
    else:
        print("\nFix REJECTED.")

    (artifacts_dir / run_id / "run.json").write_text(run.model_dump_json(indent=2), encoding="utf-8")

if __name__ == "__main__":
    main()
