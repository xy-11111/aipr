#!/usr/bin/env python3

import csv
import json
import math
from pathlib import Path
from typing import Dict, Iterable, List, Sequence

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt


REPO_ROOT = Path(__file__).resolve().parents[1]
PHASE5_ROOT = REPO_ROOT / "paper_assets" / "phase5_v1"
RAW_ROOT = PHASE5_ROOT / "raw"
TABLES_DIR = PHASE5_ROOT / "tables"
FIGURES_DIR = PHASE5_ROOT / "figures"

SCENARIO_ORDER = [
    "phase5-off-local",
    "phase5-on-local",
    "phase5-off-remote",
    "phase5-on-remote",
]


def load_json(path: Path) -> Dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_csv(path: Path, header: Sequence[str], rows: Iterable[Sequence[object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerows(rows)


def latex_escape(text: str) -> str:
    return str(text).replace("_", r"\_").replace("%", r"\%")


def write_latex_table(path: Path, caption: str, label: str, header: Sequence[str], rows: List[Sequence[object]]) -> None:
    col_spec = "l" + "r" * (len(header) - 1)
    with path.open("w", encoding="utf-8") as handle:
        handle.write("\\begin{table}[t]\n\\centering\n")
        handle.write(f"\\caption{{{caption}}}\n")
        handle.write(f"\\label{{{label}}}\n")
        handle.write(f"\\begin{{tabular}}{{{col_spec}}}\n\\hline\n")
        handle.write(" & ".join(latex_escape(item) for item in header) + " \\\\\n\\hline\n")
        for row in rows:
            formatted = []
            for item in row:
                if isinstance(item, float):
                    formatted.append(f"{item:.3f}")
                else:
                    formatted.append(latex_escape(item))
            handle.write(" & ".join(formatted) + " \\\\\n")
        handle.write("\\hline\n\\end{tabular}\n\\end{table}\n")


def scenario_meta(name: str) -> Dict[str, str]:
    telemetry = "on" if "-on-" in name else "off"
    topology = "local" if name.endswith("local") else "remote"
    return {"telemetry": telemetry, "topology": topology}


def format_pct_delta(off_value: float, on_value: float) -> str:
    if off_value == 0.0:
      return "n/a"
    return f"{((on_value - off_value) / off_value) * 100.0:.2f}%"


def write_readme() -> None:
    content = """# Phase 5 Paper Assets v1

- Purpose: capture the minimum 2x2 overhead matrix for telemetry off/on and local/remote backends
- Raw directories live under `raw/phase5-*`
- Tables and figures are exported from the raw JSON/CSV summaries

## Metrics

- Throughput is reported in requests per second
- Latency values are p50/p99 in milliseconds
- Node CPU is host-level average CPU usage percentage during the measurement window
- Agent CPU is pod-level average CPU usage percentage of one core during the measurement window

## Fallback note

- `kubectl top` was unavailable in this environment, so CPU metrics were derived from `/proc/stat` and pod cgroup `cpu.stat`
- The exported matrix keeps `success_rate` explicitly so low-availability scenarios are visible instead of being misread as pure performance overhead
"""
    (PHASE5_ROOT / "README.md").write_text(content, encoding="utf-8")


def save_phase5_figures(matrix_rows: List[Dict[str, object]]) -> None:
    labels = [f"{row['topology']}/{row['telemetry']}" for row in matrix_rows]
    throughput = [row["throughput_rps"] for row in matrix_rows]
    p50 = [row["p50_latency_ms"] for row in matrix_rows]
    p99 = [row["p99_latency_ms"] for row in matrix_rows]
    x = range(len(labels))

    fig, axes = plt.subplots(1, 2, figsize=(10.5, 4.5), facecolor="white")
    axes[0].bar(x, throughput, color="#4C72B0")
    axes[0].set_title("Throughput by scenario", fontsize=12)
    axes[0].set_ylabel("Requests per second", fontsize=10)
    axes[0].set_xticks(list(x))
    axes[0].set_xticklabels(labels, rotation=20, ha="right", fontsize=9)
    axes[0].grid(axis="y", alpha=0.25)

    width = 0.35
    axes[1].bar([item - width / 2 for item in x], p50, width=width, label="p50", color="#55A868")
    axes[1].bar([item + width / 2 for item in x], p99, width=width, label="p99", color="#C44E52")
    axes[1].set_title("Latency by scenario", fontsize=12)
    axes[1].set_ylabel("Milliseconds", fontsize=10)
    axes[1].set_xticks(list(x))
    axes[1].set_xticklabels(labels, rotation=20, ha="right", fontsize=9)
    axes[1].legend(fontsize=9)
    axes[1].grid(axis="y", alpha=0.25)
    fig.tight_layout()
    fig.savefig(FIGURES_DIR / "fig_phase5_throughput_latency.png", dpi=200, bbox_inches="tight")
    plt.close(fig)

    w1_node = [row["worker1_node_cpu_pct"] for row in matrix_rows]
    w2_node = [row["worker2_node_cpu_pct"] for row in matrix_rows]
    w1_agent = [row["worker1_agent_cpu_pct"] for row in matrix_rows]
    w2_agent = [row["worker2_agent_cpu_pct"] for row in matrix_rows]
    fig, axes = plt.subplots(1, 2, figsize=(11.5, 4.5), facecolor="white")
    width = 0.35
    axes[0].bar([item - width / 2 for item in x], w1_node, width=width, label="worker1 node", color="#4C72B0")
    axes[0].bar([item + width / 2 for item in x], w2_node, width=width, label="worker2 node", color="#8172B3")
    axes[0].set_title("Node CPU by scenario", fontsize=12)
    axes[0].set_ylabel("CPU %", fontsize=10)
    axes[0].set_xticks(list(x))
    axes[0].set_xticklabels(labels, rotation=20, ha="right", fontsize=9)
    axes[0].legend(fontsize=8)
    axes[0].grid(axis="y", alpha=0.25)

    axes[1].bar([item - width / 2 for item in x], w1_agent, width=width, label="worker1 agent", color="#55A868")
    axes[1].bar([item + width / 2 for item in x], w2_agent, width=width, label="worker2 agent", color="#C44E52")
    axes[1].set_title("Agent CPU by scenario", fontsize=12)
    axes[1].set_ylabel("CPU % of one core", fontsize=10)
    axes[1].set_xticks(list(x))
    axes[1].set_xticklabels(labels, rotation=20, ha="right", fontsize=9)
    axes[1].legend(fontsize=8)
    axes[1].grid(axis="y", alpha=0.25)
    fig.tight_layout()
    fig.savefig(FIGURES_DIR / "fig_phase5_cpu_overhead.png", dpi=200, bbox_inches="tight")
    plt.close(fig)


def main() -> int:
    TABLES_DIR.mkdir(parents=True, exist_ok=True)
    FIGURES_DIR.mkdir(parents=True, exist_ok=True)
    matrix_rows: List[Dict[str, object]] = []

    for scenario_name in SCENARIO_ORDER:
        scenario_dir = RAW_ROOT / scenario_name
        bench_summary = load_json(scenario_dir / "bench_summary.json")
        cpu_summary = load_json(scenario_dir / "cpu_summary.json")
        meta = scenario_meta(scenario_name)
        matrix_rows.append(
            {
                "scenario": scenario_name,
                "telemetry": meta["telemetry"],
                "topology": meta["topology"],
                "throughput_rps": float(bench_summary["throughput_rps"]),
                "p50_latency_ms": float(bench_summary["p50_latency_ms"]),
                "p99_latency_ms": float(bench_summary["p99_latency_ms"]),
                "success_rate": float(bench_summary["success_rate"]),
                "worker1_node_cpu_pct": float(cpu_summary["worker1_node_cpu_pct"]),
                "worker2_node_cpu_pct": float(cpu_summary["worker2_node_cpu_pct"]),
                "worker1_agent_cpu_pct": float(cpu_summary["worker1_agent_cpu_pct"]),
                "worker2_agent_cpu_pct": float(cpu_summary["worker2_agent_cpu_pct"]),
            }
        )

    matrix_header = [
        "scenario",
        "telemetry",
        "topology",
        "throughput_rps",
        "p50_latency_ms",
        "p99_latency_ms",
        "success_rate",
        "worker1_node_cpu_pct",
        "worker2_node_cpu_pct",
        "worker1_agent_cpu_pct",
        "worker2_agent_cpu_pct",
    ]
    matrix_table_rows = [
        [row[column] for column in matrix_header]
        for row in matrix_rows
    ]
    write_csv(TABLES_DIR / "table_phase5_overhead_matrix.csv", matrix_header, matrix_table_rows)
    write_latex_table(
        TABLES_DIR / "table_phase5_overhead_matrix.tex",
        "Phase 5 minimum 2x2 overhead matrix.",
        "tab:phase5_overhead_matrix",
        matrix_header,
        matrix_table_rows,
    )

    by_topology = {
        "local": {
            "off": next(row for row in matrix_rows if row["topology"] == "local" and row["telemetry"] == "off"),
            "on": next(row for row in matrix_rows if row["topology"] == "local" and row["telemetry"] == "on"),
        },
        "remote": {
            "off": next(row for row in matrix_rows if row["topology"] == "remote" and row["telemetry"] == "off"),
            "on": next(row for row in matrix_rows if row["topology"] == "remote" and row["telemetry"] == "on"),
        },
    }
    delta_rows = []
    for topology, values in by_topology.items():
        off = values["off"]
        on = values["on"]
        delta_rows.append(
            [
                topology,
                on["throughput_rps"] - off["throughput_rps"],
                format_pct_delta(off["throughput_rps"], on["throughput_rps"]),
                on["p50_latency_ms"] - off["p50_latency_ms"],
                format_pct_delta(off["p50_latency_ms"], on["p50_latency_ms"]),
                on["p99_latency_ms"] - off["p99_latency_ms"],
                format_pct_delta(off["p99_latency_ms"], on["p99_latency_ms"]),
                on["worker1_agent_cpu_pct"] - off["worker1_agent_cpu_pct"],
                on["worker2_agent_cpu_pct"] - off["worker2_agent_cpu_pct"],
                on["worker1_node_cpu_pct"] - off["worker1_node_cpu_pct"],
                on["worker2_node_cpu_pct"] - off["worker2_node_cpu_pct"],
            ]
        )
    delta_header = [
        "topology",
        "throughput_delta_rps",
        "throughput_delta_pct",
        "p50_delta_ms",
        "p50_delta_pct",
        "p99_delta_ms",
        "p99_delta_pct",
        "worker1_agent_cpu_delta_pct",
        "worker2_agent_cpu_delta_pct",
        "worker1_node_cpu_delta_pct",
        "worker2_node_cpu_delta_pct",
    ]
    write_csv(TABLES_DIR / "table_phase5_overhead_delta.csv", delta_header, delta_rows)
    write_latex_table(
        TABLES_DIR / "table_phase5_overhead_delta.tex",
        "Phase 5 telemetry on versus off deltas by topology.",
        "tab:phase5_overhead_delta",
        delta_header,
        delta_rows,
    )

    save_phase5_figures(matrix_rows)
    write_readme()
    print(f"wrote phase5 paper assets to {PHASE5_ROOT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
