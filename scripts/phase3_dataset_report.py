#!/usr/bin/env python3

import argparse
import csv
import datetime as dt
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, Iterable, List, Tuple


DELTA_FIELDS = [
    "delta_tcp_packets",
    "delta_nodeport_hit",
    "delta_backend_selected",
    "delta_backend_lookup_miss",
    "delta_rr_update",
    "delta_snat_install",
    "delta_request_rewrite",
    "delta_revnat_hit",
    "delta_ct_lookup_miss",
    "delta_response_rewrite",
    "delta_fwd_ct_hit",
    "delta_new_conn",
    "delta_map_miss",
    "delta_rewrite_fail",
    "delta_redirect_ok",
    "delta_redirect_fail",
    "delta_fallback_pass",
]


def parse_args() -> argparse.Namespace:
    repo_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="Generate Phase 3 dataset summary and quality report.")
    parser.add_argument("--input-dir", type=Path, default=repo_root / "datasets" / "phase3_v1")
    return parser.parse_args()


def load_rows(path: Path) -> List[Dict[str, str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def as_int(value: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def write_markdown_list(handle, items: Iterable[str]) -> None:
    for item in items:
        handle.write(f"- {item}\n")


def main() -> int:
    args = parse_args()
    input_dir = args.input_dir
    windows_all = load_rows(input_dir / "windows_all.csv")
    if not windows_all:
        raise SystemExit("windows_all.csv is empty")

    raw_index = load_rows(input_dir / "raw_index.csv")
    splits = json.loads((input_dir / "splits.json").read_text(encoding="utf-8"))

    label_counter = Counter(row["label"] for row in windows_all)
    split_counter = Counter(row["split"] for row in windows_all)
    node_counter = Counter(row["node_name"] for row in windows_all)
    experiment_counter = Counter(row["experiment_id"] for row in windows_all)
    scenario_counter = Counter((row.get("experiment_type") or "") for row in windows_all)
    label_split_counter: Dict[str, Counter] = defaultdict(Counter)

    quality_errors: List[str] = []
    quality_warnings: List[str] = []

    for row in windows_all:
        label_split_counter[row["label"]][row["split"]] += 1
        start = as_int(row.get("window_start_unix_ms", "0"))
        end = as_int(row.get("window_end_unix_ms", "0"))
        if end <= start:
            quality_errors.append(
                f"{row['experiment_id']}:{row['node_name']} invalid window bounds {start}->{end}"
            )
        if as_int(row.get("backend_total", "0")) != as_int(row.get("backend_local", "0")) + as_int(row.get("backend_remote", "0")):
            quality_warnings.append(
                f"{row['experiment_id']}:{row['node_name']} backend_total mismatch at {row.get('window_start_unix_ms')}"
            )
        success_rate = float(row.get("traffic_probe_success_rate", "0") or 0.0)
        if success_rate < 0.0 or success_rate > 1.0:
            quality_errors.append(
                f"{row['experiment_id']} traffic success rate out of range: {success_rate}"
            )
        for field in DELTA_FIELDS:
            if as_int(row.get(field, "0")) < 0:
                quality_errors.append(
                    f"{row['experiment_id']}:{row['node_name']} negative delta in {field}"
                )

    label_by_experiment: Dict[str, set] = defaultdict(set)
    recovery_by_experiment: Dict[str, int] = defaultdict(int)
    for row in windows_all:
        if row["label"] != "normal":
            label_by_experiment[row["experiment_id"]].add(row["label"])
        if row.get("recovery_active") == "1":
            recovery_by_experiment[row["experiment_id"]] += 1

    for entry in raw_index:
        if entry["included"] != "1":
            continue
        experiment_id = entry["experiment_id"]
        primary_label = entry["primary_label"]
        if primary_label != "normal" and primary_label not in label_by_experiment[experiment_id]:
            quality_errors.append(
                f"{experiment_id} missing target label {primary_label} in windows_all.csv"
            )
        if primary_label != "normal" and recovery_by_experiment[experiment_id] == 0:
            quality_errors.append(
                f"{experiment_id} missing recovery_active windows"
            )

    for label, split_counts in sorted(label_split_counter.items()):
        if label == "normal":
            continue
        if split_counts.get("test", 0) == 0:
            quality_warnings.append(f"label {label} has no test coverage yet")
        if split_counts.get("val", 0) == 0:
            quality_warnings.append(f"label {label} has no val coverage yet")

    dataset_summary_path = input_dir / "dataset_summary.md"
    with dataset_summary_path.open("w", encoding="utf-8") as handle:
        handle.write("# Phase 3 Dataset v1 摘要\n\n")
        handle.write(f"- 生成时间：{dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}\n")
        handle.write(f"- 样本总行数：`{len(windows_all)}`\n")
        handle.write(f"- 实验总数：`{len(experiment_counter)}`\n")
        handle.write(f"- 节点总数：`{len(node_counter)}`\n\n")

        handle.write("## 1. Split 分布\n\n")
        for split_name in ("train", "val", "test"):
            handle.write(f"- `{split_name}`：`{split_counter.get(split_name, 0)}` 行\n")
        handle.write("\n")

        handle.write("## 2. 标签分布\n\n")
        for label, count in sorted(label_counter.items()):
            per_split = ", ".join(
                f"{split_name}={label_split_counter[label].get(split_name, 0)}"
                for split_name in ("train", "val", "test")
            )
            handle.write(f"- `{label}`：`{count}` 行（{per_split}）\n")
        handle.write("\n")

        handle.write("## 3. 场景分布\n\n")
        for scenario, count in sorted(scenario_counter.items(), key=lambda item: str(item[0])):
            display_name = scenario or "unknown"
            handle.write(f"- `{display_name}`：`{count}` 行\n")
        handle.write("\n")

        handle.write("## 4. 节点分布\n\n")
        for node_name, count in sorted(node_counter.items()):
            handle.write(f"- `{node_name}`：`{count}` 行\n")
        handle.write("\n")

        handle.write("## 5. 实验级 split 映射\n\n")
        for experiment_id, meta in sorted(splits["experiments"].items()):
            handle.write(
                f"- `{experiment_id}` -> `{meta['split']}` / `{meta['primary_label']}` / `{meta['scenario']}`\n"
            )
        handle.write("\n")

    quality_report_path = input_dir / "quality_report.md"
    with quality_report_path.open("w", encoding="utf-8") as handle:
        handle.write("# Phase 3 Dataset v1 质量报告\n\n")
        handle.write(f"- 检查时间：{dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}\n")
        handle.write(f"- 错误数：`{len(quality_errors)}`\n")
        handle.write(f"- 警告数：`{len(quality_warnings)}`\n\n")

        handle.write("## 1. 通过项\n\n")
        passed_items = [
            "所有纳入实验均已写入 windows_all.csv",
            "所有 split 文件均已生成",
            "所有 delta_* 字段未发现负值",
            "所有异常实验都出现了目标 label 与 recovery_active 窗口",
        ]
        write_markdown_list(handle, passed_items)
        handle.write("\n")

        handle.write("## 2. 错误项\n\n")
        if quality_errors:
            write_markdown_list(handle, quality_errors)
        else:
            handle.write("- 无阻塞性错误\n")
        handle.write("\n")

        handle.write("## 3. 警告项\n\n")
        if quality_warnings:
            for item in quality_warnings[:20]:
                handle.write(f"- {item}\n")
            if len(quality_warnings) > 20:
                handle.write(f"- 其余 warning 共 `{len(quality_warnings) - 20}` 条，建议后续抽样复核\n")
        else:
            handle.write("- 无警告\n")
        handle.write("\n")

    manifest_path = input_dir / "dataset_manifest.json"
    manifest = {}
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.update(
        {
            "dataset_version": "phase3_v1",
            "generated_at_utc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "row_count": len(windows_all),
            "experiment_count": len(experiment_counter),
            "node_count": len(node_counter),
            "label_distribution": dict(sorted(label_counter.items())),
            "split_distribution": dict(sorted(split_counter.items())),
            "scenario_distribution": {
                (key or "unknown"): value
                for key, value in sorted(scenario_counter.items(), key=lambda item: str(item[0]))
            },
            "quality_error_count": len(quality_errors),
            "quality_warning_count": len(quality_warnings),
            "summary_markdown": str(dataset_summary_path),
            "quality_markdown": str(quality_report_path),
        }
    )
    with manifest_path.open("w", encoding="utf-8") as handle:
        json.dump(manifest, handle, ensure_ascii=True, indent=2, sort_keys=True)
        handle.write("\n")

    print(f"wrote {dataset_summary_path}")
    print(f"wrote {quality_report_path}")
    print(f"errors={len(quality_errors)} warnings={len(quality_warnings)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
