#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", value.strip().lower())
    slug = slug.strip("_")
    return slug or "iteration"


def _dedupe_keep_order(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        item = value.strip()
        if not item or item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


def _load_manifest(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError(f"Manifest must be a JSON list: {path}")
    out: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        record_id = item.get("record_id")
        if not record_id:
            iteration = int(item.get("iteration", 0) or 0)
            label = str(item.get("label") or item.get("name") or f"legacy_{len(out) + 1}")
            item = {
                "record_id": f"{iteration:02d}-{_slugify(label)}",
                "iteration": iteration,
                "label": label,
                "workstream": str(item.get("workstream", "")),
                "decision": str(item.get("decision", "legacy")),
                "candidate_commit": str(item.get("candidate_commit", "")),
                "revert_commit": str(item.get("revert_commit", "")),
                "accepted_commit": str(item.get("accepted_commit", "")),
                "correctness": str(item.get("correctness", "")),
                "local_tests": str(item.get("local_tests", "")),
                "production_metrics": str(item.get("production_metrics", "")),
                "research_links": list(item.get("research_links", [])),
                "artifact_refs": list(item.get("artifact_refs", [])),
                "baseline_refs": list(item.get("baseline_refs", [])),
                "note_path": str(item.get("note_path", "")),
                "record_path": str(item.get("record_path", "")),
                "timestamp_utc": str(item.get("timestamp_utc", "")),
            }
        out.append(item)
    return out


def _write_manifest_csv(path: Path, records: list[dict[str, Any]]) -> None:
    fieldnames = [
        "record_id",
        "iteration",
        "label",
        "workstream",
        "decision",
        "candidate_commit",
        "revert_commit",
        "accepted_commit",
        "correctness",
        "local_tests",
        "production_metrics",
        "research_links",
        "artifact_refs",
        "baseline_refs",
        "note_path",
        "record_path",
        "timestamp_utc",
    ]
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for record in records:
            writer.writerow(
                {
                    "record_id": record["record_id"],
                    "iteration": record["iteration"],
                    "label": record["label"],
                    "workstream": record["workstream"],
                    "decision": record["decision"],
                    "candidate_commit": record.get("candidate_commit", ""),
                    "revert_commit": record.get("revert_commit", ""),
                    "accepted_commit": record.get("accepted_commit", ""),
                    "correctness": record.get("correctness", ""),
                    "local_tests": record.get("local_tests", ""),
                    "production_metrics": record.get("production_metrics", ""),
                    "research_links": " | ".join(record.get("research_links", [])),
                    "artifact_refs": " | ".join(record.get("artifact_refs", [])),
                    "baseline_refs": " | ".join(record.get("baseline_refs", [])),
                    "note_path": record.get("note_path", ""),
                    "record_path": record.get("record_path", ""),
                    "timestamp_utc": record["timestamp_utc"],
                }
            )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Record a backend throughput experiment iteration and update the manifest."
    )
    backend_dir = Path(__file__).resolve().parents[1]
    parser.add_argument("--iteration", type=int, required=True)
    parser.add_argument("--label", required=True)
    parser.add_argument("--workstream", choices=["async", "sync", "shared"], required=True)
    parser.add_argument(
        "--decision",
        choices=["accepted", "reverted", "invalid-noise"],
        required=True,
    )
    parser.add_argument("--hypothesis", required=True)
    parser.add_argument("--candidate-commit", default="")
    parser.add_argument("--revert-commit", default="")
    parser.add_argument("--accepted-commit", default="")
    parser.add_argument("--correctness", default="")
    parser.add_argument("--local-tests", default="")
    parser.add_argument("--production-metrics", default="")
    parser.add_argument("--note", default="")
    parser.add_argument("--note-file", default="")
    parser.add_argument("--artifact-ref", action="append", default=[])
    parser.add_argument("--baseline-ref", action="append", default=[])
    parser.add_argument("--research-link", action="append", default=[])
    parser.add_argument(
        "--output-dir",
        default=str(backend_dir / "outputs" / "loadtests" / "verification"),
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    slug = _slugify(args.label)
    record_id = f"{args.iteration:02d}-{slug}"
    timestamp_utc = datetime.now(tz=timezone.utc).isoformat()

    note_text = args.note.strip()
    if args.note_file:
        note_text = Path(args.note_file).read_text(encoding="utf-8").strip()

    record_path = output_dir / f"iteration_{args.iteration:02d}_{slug}.json"
    note_path = output_dir / f"iteration_{args.iteration:02d}_{slug}.md"
    manifest_json = output_dir / "iteration_manifest.json"
    manifest_csv = output_dir / "iteration_manifest.csv"

    research_links = _dedupe_keep_order(list(args.research_link))
    artifact_refs = _dedupe_keep_order(list(args.artifact_ref))
    baseline_refs = _dedupe_keep_order(list(args.baseline_ref))

    if note_text or research_links:
        lines: list[str] = [
            f"# Iteration {args.iteration}: {args.label}",
            "",
            f"- Workstream: `{args.workstream}`",
            f"- Decision: `{args.decision}`",
            f"- Candidate commit: `{args.candidate_commit or 'n/a'}`",
            f"- Revert commit: `{args.revert_commit or 'n/a'}`",
            f"- Accepted commit after round: `{args.accepted_commit or 'n/a'}`",
            "",
            "## Hypothesis",
            args.hypothesis,
            "",
        ]
        if note_text:
            lines.extend(["## Notes", note_text, ""])
        if research_links:
            lines.append("## Research Links")
            for link in research_links:
                lines.append(f"- {link}")
            lines.append("")
        note_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")

    record: dict[str, Any] = {
        "record_id": record_id,
        "iteration": args.iteration,
        "label": args.label,
        "workstream": args.workstream,
        "decision": args.decision,
        "hypothesis": args.hypothesis,
        "candidate_commit": args.candidate_commit,
        "revert_commit": args.revert_commit,
        "accepted_commit": args.accepted_commit,
        "correctness": args.correctness,
        "local_tests": args.local_tests,
        "production_metrics": args.production_metrics,
        "research_links": research_links,
        "artifact_refs": artifact_refs,
        "baseline_refs": baseline_refs,
        "timestamp_utc": timestamp_utc,
        "note_path": str(note_path) if note_path.exists() else "",
        "record_path": str(record_path),
    }
    record_path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    records = _load_manifest(manifest_json)
    records = [item for item in records if item.get("record_id") != record_id]
    records.append(record)
    records.sort(key=lambda item: (int(item.get("iteration", 0)), str(item.get("record_id", ""))))
    manifest_json.write_text(json.dumps(records, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    _write_manifest_csv(manifest_csv, records)

    print(str(record_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
