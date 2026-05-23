#!/usr/bin/env python3

import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path
from typing import Dict, List


def parse_args() -> argparse.Namespace:
    repo_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="Split Phase 3 dataset by experiment id.")
    parser.add_argument("--input-dir", type=Path, default=repo_root / "datasets" / "phase3_v1")
    return parser.parse_args()


def load_rows(path: Path) -> List[Dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def primary_label_for_experiment(rows: List[Dict[str, str]]) -> str:
    labels = sorted({row["label"] for row in rows if row.get("label") and row["label"] != "normal"})
    if labels:
        return labels[0]
    return "normal"


def split_group(experiment_ids: List[str]) -> Dict[str, str]:
    experiment_ids = sorted(experiment_ids)
    assignments: Dict[str, str] = {}
    count = len(experiment_ids)
    if count == 1:
        assignments[experiment_ids[0]] = "train"
        return assignments
    if count == 2:
        assignments[experiment_ids[0]] = "train"
        assignments[experiment_ids[1]] = "test"
        return assignments
    if count >= 3:
        assignments[experiment_ids[0]] = "train"
        assignments[experiment_ids[1]] = "val"
        assignments[experiment_ids[2]] = "test"
        for experiment_id in experiment_ids[3:]:
            assignments[experiment_id] = "train"
        return assignments
    return assignments


def main() -> int:
    args = parse_args()
    windows_all_path = args.input_dir / "windows_all.csv"
    rows = load_rows(windows_all_path)
    if not rows:
        raise SystemExit(f"no rows in {windows_all_path}")

    rows_by_experiment: Dict[str, List[Dict[str, str]]] = defaultdict(list)
    for row in rows:
        rows_by_experiment[row["experiment_id"]].append(row)

    experiments_by_label: Dict[str, List[str]] = defaultdict(list)
    experiment_metadata: Dict[str, Dict[str, str]] = {}
    for experiment_id, experiment_rows in rows_by_experiment.items():
        label = primary_label_for_experiment(experiment_rows)
        scenario = experiment_rows[0].get("experiment_type", "")
        experiment_metadata[experiment_id] = {"label": label, "scenario": scenario}
        experiments_by_label[label].append(experiment_id)

    assignments: Dict[str, str] = {}
    for label, experiment_ids in sorted(experiments_by_label.items()):
        assignments.update(split_group(experiment_ids))

    fieldnames = list(rows[0].keys())
    split_rows: Dict[str, List[Dict[str, str]]] = {"train": [], "val": [], "test": []}
    for row in rows:
        split = assignments[row["experiment_id"]]
        new_row = dict(row)
        new_row["split"] = split
        split_rows[split].append(new_row)

    updated_all = []
    for split_name in ("train", "val", "test"):
        updated_all.extend(split_rows[split_name])

    with (args.input_dir / "windows_all.csv").open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(updated_all)

    for split_name, rows_for_split in split_rows.items():
        path = args.input_dir / f"windows_{split_name}.csv"
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows_for_split)

    split_manifest = {
        "split_strategy": "group_by_primary_label_then_split_by_experiment_id",
        "assignments": assignments,
        "experiments": {
            experiment_id: {
                "split": assignments[experiment_id],
                "primary_label": metadata["label"],
                "scenario": metadata["scenario"],
            }
            for experiment_id, metadata in sorted(experiment_metadata.items())
        },
    }
    with (args.input_dir / "splits.json").open("w", encoding="utf-8") as handle:
        json.dump(split_manifest, handle, ensure_ascii=True, indent=2, sort_keys=True)
        handle.write("\n")

    print("split counts:")
    for split_name in ("train", "val", "test"):
        print(f"  {split_name}: rows={len(split_rows[split_name])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
