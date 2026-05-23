#!/usr/bin/env python3

import argparse
import csv
import datetime as dt
import json
from pathlib import Path
from typing import Dict, Iterable, List, Sequence

DERIVED_FIELDS = [
    "experiment_type",
    "split",
    "is_baseline_experiment",
    "traffic_probe_total",
    "traffic_probe_success",
    "traffic_probe_success_rate",
    "event_count",
    "source_artifact_dir",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build Phase 3 dataset windows from experiment artifacts.")
    repo_root = Path(__file__).resolve().parents[1]
    parser.add_argument("--artifacts-dir", type=Path, default=repo_root / "artifacts")
    parser.add_argument("--output-dir", type=Path, default=repo_root / "datasets" / "phase3_v1")
    parser.add_argument(
        "--selection-file",
        type=Path,
        default=repo_root / "datasets" / "phase3_v1" / "experiment_selection.json",
        help="JSON file that defines included/excluded experiment ids.",
    )
    parser.add_argument("--include-experiment", action="append", default=[], help="Include one more experiment id.")
    parser.add_argument("--exclude-experiment", action="append", default=[], help="Exclude one experiment id.")
    return parser.parse_args()


def load_json(path: Path) -> Dict:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def load_events(path: Path) -> List[Dict]:
    events = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            events.append(json.loads(line))
    return events


def load_traffic_stats(path: Path) -> Dict[str, object]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    total = len(rows)
    success = sum(1 for row in rows if row.get("ok") == "1")
    success_rate = float(success) / float(total) if total else 0.0
    return {
        "traffic_probe_total": total,
        "traffic_probe_success": success,
        "traffic_probe_success_rate": f"{success_rate:.6f}",
    }


def scenario_to_label(events: Sequence[Dict], scenario: str) -> str:
    if events:
        labels = {event.get("label", "") for event in events if event.get("label")}
        labels.discard("normal")
        if len(labels) == 1:
            return next(iter(labels))
        if len(labels) > 1:
            return ",".join(sorted(labels))
    if scenario == "normal_steady_state":
        return "normal"
    return "unknown"


def read_csv_rows(path: Path) -> Iterable[Dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            yield row


def stable_int(value: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def load_experiment_selection(path: Path) -> Dict[str, List[str]]:
    if not path.exists():
        raise SystemExit(f"selection file not found: {path}")
    data = load_json(path)
    included = data.get("included_experiments", [])
    excluded = data.get("excluded_experiments", [])
    if not isinstance(included, list) or not isinstance(excluded, list):
        raise SystemExit(f"invalid selection file format: {path}")
    return {
        "included_experiments": [str(value) for value in included],
        "excluded_experiments": [str(value) for value in excluded],
    }


def main() -> int:
    args = parse_args()
    output_dir: Path = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    selection = load_experiment_selection(args.selection_file)
    include_ids = list(dict.fromkeys(selection["included_experiments"] + args.include_experiment))
    exclude_ids = set(selection["excluded_experiments"] + args.exclude_experiment)

    raw_index_rows: List[Dict[str, object]] = []
    windows_rows: List[Dict[str, str]] = []
    canonical_header: List[str] = []

    for experiment_id in include_ids:
        artifact_dir = args.artifacts_dir / experiment_id
        status = "included"
        reason = ""
        if experiment_id in exclude_ids:
            status = "excluded"
            reason = "explicit_excluded"

        meta_path = artifact_dir / "meta.json"
        events_path = artifact_dir / "events.jsonl"
        traffic_path = artifact_dir / "traffic.csv"
        telemetry_dir = artifact_dir / "telemetry"
        log_dir = artifact_dir / "agent-logs"

        missing = [
            name
            for name, path in (
                ("meta.json", meta_path),
                ("events.jsonl", events_path),
                ("traffic.csv", traffic_path),
                ("telemetry", telemetry_dir),
            )
            if not path.exists()
        ]
        if missing and status == "included":
            status = "excluded"
            reason = "missing:" + ",".join(missing)

        meta = load_json(meta_path) if meta_path.exists() else {}
        events = load_events(events_path) if events_path.exists() else []
        traffic_stats = load_traffic_stats(traffic_path) if traffic_path.exists() else {
            "traffic_probe_total": 0,
            "traffic_probe_success": 0,
            "traffic_probe_success_rate": "0.000000",
        }

        node_csv_paths = sorted(telemetry_dir.glob("*.csv")) if telemetry_dir.exists() else []
        node_names = [path.stem for path in node_csv_paths]
        raw_index_rows.append(
            {
                "experiment_id": experiment_id,
                "scenario": meta.get("scenario", ""),
                "primary_label": scenario_to_label(events, meta.get("scenario", "")),
                "included": "1" if status == "included" else "0",
                "status": status,
                "reason": reason,
                "node_count": str(len(node_csv_paths)),
                "node_names": ",".join(node_names),
                "event_count": str(len(events)),
                "traffic_probe_total": str(traffic_stats["traffic_probe_total"]),
                "traffic_probe_success": str(traffic_stats["traffic_probe_success"]),
                "traffic_probe_success_rate": str(traffic_stats["traffic_probe_success_rate"]),
                "artifact_dir": str(artifact_dir),
                "agent_log_count": str(len(list(log_dir.glob("*.log"))) if log_dir.exists() else 0),
            }
        )

        if status != "included":
            continue

        for csv_path in node_csv_paths:
            with csv_path.open("r", encoding="utf-8", newline="") as handle:
                reader = csv.DictReader(handle)
                header = reader.fieldnames or []
                if not canonical_header:
                    canonical_header = list(header)
                elif header != canonical_header:
                    raise SystemExit(
                        f"schema mismatch in {csv_path}: {header} != {canonical_header}"
                    )
                for row in reader:
                    dataset_row = dict(row)
                    dataset_row["experiment_type"] = meta.get("scenario", "")
                    dataset_row["split"] = ""
                    dataset_row["is_baseline_experiment"] = "1" if meta.get("scenario") == "normal_steady_state" else "0"
                    dataset_row["traffic_probe_total"] = str(traffic_stats["traffic_probe_total"])
                    dataset_row["traffic_probe_success"] = str(traffic_stats["traffic_probe_success"])
                    dataset_row["traffic_probe_success_rate"] = str(traffic_stats["traffic_probe_success_rate"])
                    dataset_row["event_count"] = str(len(events))
                    dataset_row["source_artifact_dir"] = str(artifact_dir)
                    windows_rows.append(dataset_row)

    raw_index_rows.sort(key=lambda row: row["experiment_id"])
    windows_rows.sort(
        key=lambda row: (
            row.get("experiment_id", ""),
            row.get("node_name", ""),
            stable_int(row.get("window_start_unix_ms", "0")),
        )
    )

    raw_index_path = output_dir / "raw_index.csv"
    with raw_index_path.open("w", encoding="utf-8", newline="") as handle:
        fieldnames = [
            "experiment_id",
            "scenario",
            "primary_label",
            "included",
            "status",
            "reason",
            "node_count",
            "node_names",
            "event_count",
            "traffic_probe_total",
            "traffic_probe_success",
            "traffic_probe_success_rate",
            "artifact_dir",
            "agent_log_count",
        ]
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(raw_index_rows)

    windows_all_path = output_dir / "windows_all.csv"
    if not canonical_header:
        raise SystemExit("no included telemetry rows found")
    dataset_header = canonical_header + DERIVED_FIELDS
    with windows_all_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=dataset_header)
        writer.writeheader()
        writer.writerows(windows_rows)

    manifest = {
        "dataset_version": "phase3_v1",
        "built_at_utc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "artifacts_dir": str(args.artifacts_dir),
        "output_dir": str(output_dir),
        "selection_file": str(args.selection_file),
        "included_experiments": include_ids,
        "excluded_experiments": sorted(exclude_ids),
        "raw_index_csv": str(raw_index_path),
        "windows_all_csv": str(windows_all_path),
        "telemetry_field_count": len(canonical_header),
        "dataset_field_count": len(dataset_header),
        "row_count": len(windows_rows),
        "experiment_count": len({row["experiment_id"] for row in windows_rows}),
    }
    with (output_dir / "dataset_manifest.json").open("w", encoding="utf-8") as handle:
        json.dump(manifest, handle, ensure_ascii=True, indent=2, sort_keys=True)
        handle.write("\n")

    print(f"wrote {raw_index_path}")
    print(f"wrote {windows_all_path}")
    print(f"rows={len(windows_rows)} experiments={manifest['experiment_count']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
