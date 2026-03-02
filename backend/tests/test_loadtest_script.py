from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.loadtest.loadtest_lean_server import (
    AsyncMetricsSnapshot,
    Case,
    EvalRow,
    SevereThresholds,
    Thresholds,
    apply_failed_label_policy,
    accuracy,
    build_cases,
    checks_for_tier,
    classify_failed_label_kind,
    classify_repl_result,
    compute_tier_metrics,
    detect_global_stall,
    load_jsonl_cases,
    parse_ramp_schedule,
    parse_levels,
    render_incident_report,
    severe_failure_reasons,
)


def test_parse_levels_valid_and_invalid() -> None:
    assert parse_levels("10,50,100") == (10, 50, 100)
    with pytest.raises(ValueError):
        parse_levels("")
    with pytest.raises(ValueError):
        parse_levels("10,0")


def test_parse_ramp_schedule() -> None:
    assert parse_ramp_schedule("25:120,50:180") == [(25, 120.0), (50, 180.0)]
    with pytest.raises(ValueError):
        parse_ramp_schedule("25")


def test_load_jsonl_cases_handles_schema_and_errors(tmp_path: Path) -> None:
    path = tmp_path / "cases.jsonl"
    lines = [
        json.dumps({"code": "#check Nat"}),
        json.dumps({"proof": "example : True := by trivial"}),
        json.dumps("#check Int"),
        json.dumps({"other": "missing"}),
        "{bad json",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    cases, stats = load_jsonl_cases(
        path,
        expected_valid=True,
        id_prefix="verified",
        code_fields=("code", "proof"),
    )

    assert len(cases) == 3
    assert stats.total_lines == 5
    assert stats.parsed_rows == 3
    assert stats.missing_code_rows == 1
    assert stats.malformed_rows == 1


def test_load_jsonl_cases_combines_prompt_response(tmp_path: Path) -> None:
    path = tmp_path / "proof_sft.jsonl"
    path.write_text(
        json.dumps({"prompt": "theorem t : True := by\n  sorry", "response": "  trivial"}) + "\n",
        encoding="utf-8",
    )

    cases, stats = load_jsonl_cases(
        path,
        expected_valid=True,
        id_prefix="verified",
        code_fields=("prompt",),
    )
    assert stats.parsed_rows == 1
    assert len(cases) == 1
    assert cases[0].code == "theorem t : True := by\n  trivial"


def test_build_cases_respects_maximums() -> None:
    verified = [Case(case_id=f"v-{i}", code="#check Nat", expected_valid=True, source="v") for i in range(10)]
    failed = [Case(case_id=f"f-{i}", code="example : False := by trivial", expected_valid=False, source="f") for i in range(8)]

    cases = build_cases(verified, failed, max_verified=4, max_failed=3, seed=123)
    assert len(cases) == 7
    assert sum(1 for c in cases if c.expected_valid) == 4
    assert sum(1 for c in cases if not c.expected_valid) == 3


def test_failed_label_policy_split_by_error() -> None:
    cases = [
        Case("f-1", "#check Nat", False, "failed", "semantic_invalid", "Lean REPL header command timed out in 60.0 seconds"),
        Case("f-2", "example : False := by trivial", False, "failed", "semantic_invalid", "unexpected token"),
    ]
    relabeled, breakdown = apply_failed_label_policy(cases, policy="split_by_error")
    assert relabeled[0].label_kind == "transport_failure"
    assert relabeled[1].label_kind == "semantic_invalid"
    assert breakdown["bucket_counts"]["transport_timeout_or_header_timeout"] == 1
    assert breakdown["bucket_counts"]["true_lean_invalid_or_parse_error"] == 1


def test_classify_failed_label_kind_policies() -> None:
    assert classify_failed_label_kind("Lean REPL header command timed out in 60.0 seconds", "split_by_error") == "transport_failure"
    assert classify_failed_label_kind("unexpected token", "split_by_error") == "semantic_invalid"
    assert classify_failed_label_kind("anything", "strict_invalid") == "semantic_invalid"
    assert classify_failed_label_kind("anything", "transport_failure") == "transport_failure"


def test_classify_repl_result_variants() -> None:
    pred, sys_err, detail = classify_repl_result({"error": "transport"})
    assert pred is False and sys_err is True
    assert detail is not None and detail.startswith("repl_error:")

    pred, sys_err, detail = classify_repl_result(
        {
            "response": {
                "messages": [{"severity": "error", "data": "boom"}],
                "sorries": [],
            }
        }
    )
    assert (pred, sys_err, detail) == (False, False, "lean_error_message")

    pred, sys_err, detail = classify_repl_result(
        {
            "response": {
                "messages": [],
                "sorries": [{"goal": "x"}],
            }
        }
    )
    assert (pred, sys_err, detail) == (False, False, "contains_sorry")

    pred, sys_err, detail = classify_repl_result({"response": {"messages": []}})
    assert (pred, sys_err, detail) == (True, False, None)


def test_compute_tier_metrics_and_checks() -> None:
    rows = [
        EvalRow(
            mode="sync",
            tier_concurrency=10,
            case_id="v-1",
            expected_valid=True,
            predicted_valid=True,
            system_error=False,
            latency_ms=100.0,
            http_status=200,
            completed=True,
        ),
        EvalRow(
            mode="sync",
            tier_concurrency=10,
            case_id="f-1",
            expected_valid=False,
            predicted_valid=False,
            system_error=True,
            latency_ms=7000.0,
            http_status=429,
            detail="No available REPLs",
        ),
    ]
    metrics = compute_tier_metrics(rows, [50.0, 60.0])
    assert metrics["processed_attempts"] == 2
    assert metrics["http_429_rate"] == 0.5

    checks = checks_for_tier(
        mode="sync",
        metrics=metrics,
        thresholds=Thresholds(sync_p99_target_ms=5000.0),
        fail_on_accuracy=True,
        fail_on_performance=True,
    )
    assert checks["sync_p99"] is False
    assert checks["sync_429_rate"] is False


def test_async_severe_failure_reasons_includes_queue_stall() -> None:
    metrics = {
        "system_error_rate": 0.01,
        "http_429_rate": 0.0,
        "async_poll_timeout_rate": 0.06,
        "queue_stall_detected": True,
        "async_metrics_available": True,
    }
    reasons = severe_failure_reasons(
        mode="async",
        metrics=metrics,
        severe=SevereThresholds(
            system_error_rate=0.20,
            http_429_rate=0.20,
            async_poll_timeout_rate=0.05,
        ),
    )
    assert any("async_poll_timeout_rate" in reason for reason in reasons)
    assert "queue_stall_detected=true" in reasons


def test_detect_global_stall_requires_stagnant_windows() -> None:
    snapshots = [
        AsyncMetricsSnapshot(1.0, 10, 5, 2, 1.0, 0.5, 1.0, 0, 2),
        AsyncMetricsSnapshot(2.0, 10, 5, 2, 1.1, 0.5, 1.0, 0, 2),
        AsyncMetricsSnapshot(3.0, 10, 5, 2, 1.2, 0.5, 1.0, 0, 2),
        AsyncMetricsSnapshot(4.0, 10, 5, 2, 1.3, 0.5, 1.0, 0, 2),
    ]
    assert detect_global_stall(snapshots, required_windows=3) is True


def test_detect_global_stall_ignores_active_progress() -> None:
    snapshots = [
        AsyncMetricsSnapshot(1.0, 10, 5, 2, 1.0, 0.5, 1.0, 0, 2),
        AsyncMetricsSnapshot(2.0, 10, 5, 2, 1.1, 0.5, 1.0, 1, 2),
        AsyncMetricsSnapshot(3.0, 9, 4, 2, 1.2, 0.5, 1.0, 2, 2),
    ]
    assert detect_global_stall(snapshots, required_windows=2) is False


def test_accuracy_metrics() -> None:
    rows = [
        EvalRow("sync", 1, "1", True, True, False, 1.0, 200),
        EvalRow("sync", 1, "2", True, False, False, 1.0, 200),
        EvalRow("sync", 1, "3", False, False, False, 1.0, 200),
        EvalRow("sync", 1, "4", False, True, False, 1.0, 200),
    ]
    acc = accuracy(rows)
    assert acc["counts"] == {"tp": 1, "tn": 1, "fp": 1, "fn": 1}
    assert acc["rates"]["overall_accuracy"] == 0.5


def test_accuracy_excludes_transport_failure_rows() -> None:
    rows = [
        EvalRow("async", 1, "1", True, True, False, 1.0, 200, label_kind="semantic_valid"),
        EvalRow("async", 1, "2", False, True, False, 1.0, 200, label_kind="transport_failure"),
    ]
    acc = accuracy(rows)
    assert acc["sample_sizes"]["semantic_total"] == 1
    assert acc["sample_sizes"]["excluded_transport_failure"] == 1


def test_render_incident_report_contains_tier_table() -> None:
    summary = {
        "base_url": "https://lean-ui-production.up.railway.app",
        "mode": "both",
        "pass": False,
        "slo_results": {
            "all_tiers_pass": False,
            "required_async_concurrency_met": False,
        },
        "dataset": {
            "verified_cases_loaded": 10,
            "failed_cases_loaded": 10,
            "pool_size": 20,
            "code_fields": ["code", "proof"],
        },
        "label_policy": "split_by_error",
        "tier_results": [
            {
                "mode": "sync",
                "concurrency": 10,
                "pass": False,
                "throughput_attempts_per_sec": 0.5,
                "request_latency_ms": {"p99": 7000.0},
                "system_error_rate": 0.5,
                "http_429_rate": 0.4,
                "async_job_completion_rate": 0.0,
                "async_poll_timeout_rate": 0.0,
                "queue_stall_detected": False,
                "severe_failure": True,
                "severe_failure_reasons": ["http_429_rate=0.4000>0.2000"],
            }
        ],
    }
    report = render_incident_report(summary)
    assert "# Incident/Validation Report" in report
    assert "| mode | concurrency | pass |" in report
    assert "http_429_rate=0.4000>0.2000" in report
