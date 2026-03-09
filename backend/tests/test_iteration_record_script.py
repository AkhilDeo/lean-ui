from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def test_record_iteration_updates_manifest(tmp_path: Path) -> None:
    script = Path(__file__).resolve().parents[1] / "scripts" / "record_iteration.py"
    cmd = [
        sys.executable,
        str(script),
        "--iteration",
        "3",
        "--label",
        "Queue reclaim",
        "--workstream",
        "async",
        "--decision",
        "accepted",
        "--hypothesis",
        "Reliable ack/reclaim should prevent redeploy loss.",
        "--candidate-commit",
        "abc1234",
        "--accepted-commit",
        "abc1234",
        "--correctness",
        "pass",
        "--local-tests",
        "pytest -q tests/test_async_queue.py",
        "--production-metrics",
        "async throughput +12%",
        "--artifact-ref",
        "backend/outputs/loadtests/verification/loop03_async_summary.json",
        "--research-link",
        "https://redis.io/docs/latest/commands/xautoclaim/",
        "--output-dir",
        str(tmp_path),
    ]
    completed = subprocess.run(cmd, check=True, capture_output=True, text=True)
    record_path = Path(completed.stdout.strip())
    assert record_path.exists()

    manifest = json.loads((tmp_path / "iteration_manifest.json").read_text(encoding="utf-8"))
    assert len(manifest) == 1
    assert manifest[0]["record_id"] == "03-queue_reclaim"
    assert manifest[0]["decision"] == "accepted"
    assert manifest[0]["artifact_refs"] == [
        "backend/outputs/loadtests/verification/loop03_async_summary.json"
    ]
    csv_text = (tmp_path / "iteration_manifest.csv").read_text(encoding="utf-8")
    assert "03-queue_reclaim" in csv_text
