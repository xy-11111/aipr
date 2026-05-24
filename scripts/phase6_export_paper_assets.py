#!/usr/bin/env python3

import csv
import json
import math
import shutil
from pathlib import Path
from typing import Dict, Iterable, List, Sequence

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd


REPO_ROOT = Path(__file__).resolve().parents[1]
GENERALIZATION_DIR = REPO_ROOT / "results" / "phase6_generalization_v1"
ABLATION_DIR = REPO_ROOT / "results" / "phase6_ablation_v1"
ASSET_ROOT = REPO_ROOT / "paper_assets" / "phase6_v1"
TABLES_DIR = ASSET_ROOT / "tables"
FIGURES_DIR = ASSET_ROOT / "figures"
SOURCE_DIR = ASSET_ROOT / "source"
LOAD_ORDER = ["low", "medium", "high"]
VARIANT_ORDER = [
    "all_features",
    "minus_datapath_stats",
    "minus_ct_gc",
    "minus_k8s_events",
    "minus_topology_backend",
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
    replacements = {
        "\\": r"\textbackslash{}",
        "_": r"\_",
        "%": r"\%",
        "&": r"\&",
        "#": r"\#",
    }
    out = str(text)
    for old, new in replacements.items():
        out = out.replace(old, new)
    return out


def write_latex_table(path: Path, caption: str, label: str, header: Sequence[str], rows: List[Sequence[object]]) -> None:
    col_spec = "l" * min(1, len(header)) + "r" * (len(header) - 1)
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
                    if math.isnan(item):
                        formatted.append("--")
                    else:
                        formatted.append(f"{item:.4f}")
                else:
                    formatted.append(latex_escape(item))
            handle.write(" & ".join(formatted) + " \\\\\n")
        handle.write("\\hline\n\\end{tabular}\n\\end{table}\n")


def save_generalization_figure(path: Path, normal_df: pd.DataFrame, anomaly_df: pd.DataFrame) -> None:
    fig, axes = plt.subplots(1, 2, figsize=(11.0, 4.6), facecolor="white")
    for topology, color in [("local", "#4C72B0"), ("remote", "#C44E52")]:
        rows = normal_df.loc[normal_df["topology"] == topology].copy()
        rows["load_tier"] = pd.Categorical(rows["load_tier"], categories=LOAD_ORDER, ordered=True)
        rows = rows.sort_values("load_tier")
        axes[0].plot(rows["load_tier"], rows["false_positive_rate"], marker="o", label=topology, color=color)
    axes[0].set_title("Normal-window false positive rate", fontsize=12)
    axes[0].set_ylabel("False positive rate", fontsize=10)
    axes[0].set_ylim(bottom=0.0)
    axes[0].grid(axis="y", alpha=0.25)
    axes[0].legend(fontsize=9)

    agg = (
        anomaly_df.groupby(["topology", "load_tier"], as_index=False)["anomaly_f1"]
        .mean()
    )
    for topology, color in [("local", "#55A868"), ("remote", "#8172B3")]:
        rows = agg.loc[agg["topology"] == topology].copy()
        rows["load_tier"] = pd.Categorical(rows["load_tier"], categories=LOAD_ORDER, ordered=True)
        rows = rows.sort_values("load_tier")
        axes[1].plot(rows["load_tier"], rows["anomaly_f1"], marker="o", label=topology, color=color)
    axes[1].set_title("Anomaly binary F1 by load", fontsize=12)
    axes[1].set_ylabel("Anomaly F1", fontsize=10)
    axes[1].set_ylim(0.0, 1.0)
    axes[1].grid(axis="y", alpha=0.25)
    axes[1].legend(fontsize=9)

    fig.tight_layout()
    fig.savefig(path, dpi=200, bbox_inches="tight")
    plt.close(fig)


def save_ablation_figure(path: Path, binary_df: pd.DataFrame, multiclass_df: pd.DataFrame) -> None:
    binary_baseline = float(binary_df.loc[binary_df["variant"] == "all_features", "test_anomaly_f1"].iloc[0])
    multiclass_baseline = float(multiclass_df.loc[multiclass_df["variant"] == "all_features", "test_macro_f1"].iloc[0])
    binary_plot = binary_df.copy()
    binary_plot["drop_vs_all"] = binary_plot["test_anomaly_f1"] - binary_baseline
    multiclass_plot = multiclass_df.copy()
    multiclass_plot["drop_vs_all"] = multiclass_plot["test_macro_f1"] - multiclass_baseline

    fig, axes = plt.subplots(1, 2, figsize=(11.5, 4.8), facecolor="white")
    axes[0].bar(binary_plot["variant"], binary_plot["drop_vs_all"], color="#4C72B0")
    axes[0].set_title("Binary ablation delta vs all features", fontsize=12)
    axes[0].set_ylabel("Test anomaly F1 delta", fontsize=10)
    axes[0].tick_params(axis="x", rotation=20, labelsize=9)
    axes[0].axhline(0.0, color="black", linewidth=0.8)
    axes[0].grid(axis="y", alpha=0.25)

    axes[1].bar(multiclass_plot["variant"], multiclass_plot["drop_vs_all"], color="#55A868")
    axes[1].set_title("Multiclass ablation delta vs all features", fontsize=12)
    axes[1].set_ylabel("Test macro-F1 delta", fontsize=10)
    axes[1].tick_params(axis="x", rotation=20, labelsize=9)
    axes[1].axhline(0.0, color="black", linewidth=0.8)
    axes[1].grid(axis="y", alpha=0.25)

    fig.tight_layout()
    fig.savefig(path, dpi=200, bbox_inches="tight")
    plt.close(fig)


def write_readme() -> None:
    content = """# Phase 6 Paper Assets v1

- Purpose: freeze Phase 6 generalization and ablation outputs into paper-ready tables and figures
- Source results:
  - `results/phase6_generalization_v1/`
  - `results/phase6_ablation_v1/`

## Directory layout

- `tables/`: CSV and LaTeX tables
- `figures/`: PNG figures
- `source/`: copied source summaries and manifests

## Notes

- Phase 6 generalization keeps the Phase 4 best models fixed and evaluates them on newly collected experiments.
- Phase 6 ablation reuses the Phase 4 best random-forest configurations and only changes feature groups.
"""
    (ASSET_ROOT / "README.md").write_text(content, encoding="utf-8")


def main() -> int:
    TABLES_DIR.mkdir(parents=True, exist_ok=True)
    FIGURES_DIR.mkdir(parents=True, exist_ok=True)
    SOURCE_DIR.mkdir(parents=True, exist_ok=True)
    stale_manifest = SOURCE_DIR / "run_manifest.json"
    if stale_manifest.exists():
        stale_manifest.unlink()

    normal_df = pd.read_csv(GENERALIZATION_DIR / "normal_binary_summary.csv")
    anomaly_binary_df = pd.read_csv(GENERALIZATION_DIR / "anomaly_binary_summary.csv")
    anomaly_multiclass_df = pd.read_csv(GENERALIZATION_DIR / "anomaly_multiclass_summary.csv")
    binary_ablation_df = pd.read_csv(ABLATION_DIR / "binary_variant_summary.csv")
    multiclass_ablation_df = pd.read_csv(ABLATION_DIR / "multiclass_variant_summary.csv")

    source_copies = [
        (GENERALIZATION_DIR / "normal_binary_metrics.json", "normal_binary_metrics.json"),
        (GENERALIZATION_DIR / "anomaly_binary_metrics.json", "anomaly_binary_metrics.json"),
        (GENERALIZATION_DIR / "anomaly_multiclass_metrics.json", "anomaly_multiclass_metrics.json"),
        (GENERALIZATION_DIR / "normal_binary_summary.csv", "normal_binary_summary.csv"),
        (GENERALIZATION_DIR / "anomaly_binary_summary.csv", "anomaly_binary_summary.csv"),
        (GENERALIZATION_DIR / "anomaly_multiclass_summary.csv", "anomaly_multiclass_summary.csv"),
        (GENERALIZATION_DIR / "run_manifest.json", "generalization_run_manifest.json"),
        (ABLATION_DIR / "binary_metrics.json", "binary_metrics.json"),
        (ABLATION_DIR / "multiclass_metrics.json", "multiclass_metrics.json"),
        (ABLATION_DIR / "binary_variant_summary.csv", "binary_variant_summary.csv"),
        (ABLATION_DIR / "multiclass_variant_summary.csv", "multiclass_variant_summary.csv"),
        (ABLATION_DIR / "run_manifest.json", "ablation_run_manifest.json"),
    ]
    for source_path, target_name in source_copies:
        shutil.copy2(source_path, SOURCE_DIR / target_name)

    generalization_binary_header = [
        "scope",
        "scenario_family",
        "topology",
        "load_tier",
        "row_count",
        "false_positive_rate",
        "specificity",
        "anomaly_f1",
        "balanced_accuracy",
    ]
    generalization_binary_rows: List[List[object]] = []
    for _, row in normal_df.iterrows():
        generalization_binary_rows.append(
            [
                "normal",
                "normal",
                row["topology"],
                row["load_tier"],
                int(row["row_count"]),
                float(row["false_positive_rate"]),
                float(row["specificity"]),
                math.nan,
                math.nan,
            ]
        )
    for _, row in anomaly_binary_df.iterrows():
        generalization_binary_rows.append(
            [
                "anomaly",
                row["scenario_family"],
                row["topology"],
                row["load_tier"],
                int(row["row_count"]),
                math.nan,
                math.nan,
                float(row["anomaly_f1"]),
                float(row["balanced_accuracy"]),
            ]
        )
    write_csv(
        TABLES_DIR / "table_phase6_generalization_binary.csv",
        generalization_binary_header,
        generalization_binary_rows,
    )
    write_latex_table(
        TABLES_DIR / "table_phase6_generalization_binary.tex",
        "Phase 6 generalization results for binary anomaly detection.",
        "tab:phase6_generalization_binary",
        generalization_binary_header,
        generalization_binary_rows,
    )

    generalization_multiclass_header = [
        "scenario_family",
        "topology",
        "load_tier",
        "row_count",
        "macro_f1",
        "balanced_accuracy",
        "primary_label_f1",
    ]
    generalization_multiclass_rows = [
        [
            row["scenario_family"],
            row["topology"],
            row["load_tier"],
            int(row["row_count"]),
            float(row["macro_f1"]),
            float(row["balanced_accuracy"]),
            float(row["primary_label_f1"]),
        ]
        for _, row in anomaly_multiclass_df.iterrows()
    ]
    write_csv(
        TABLES_DIR / "table_phase6_generalization_multiclass.csv",
        generalization_multiclass_header,
        generalization_multiclass_rows,
    )
    write_latex_table(
        TABLES_DIR / "table_phase6_generalization_multiclass.tex",
        "Phase 6 generalization results for multiclass anomaly classification.",
        "tab:phase6_generalization_multiclass",
        generalization_multiclass_header,
        generalization_multiclass_rows,
    )

    binary_baseline = float(binary_ablation_df.loc[binary_ablation_df["variant"] == "all_features", "test_anomaly_f1"].iloc[0])
    ablation_binary_header = [
        "variant",
        "feature_count",
        "test_anomaly_f1",
        "test_pr_auc",
        "test_balanced_accuracy",
        "delta_vs_all",
    ]
    ablation_binary_rows = []
    for _, row in binary_ablation_df.iterrows():
        test_f1 = float(row["test_anomaly_f1"])
        ablation_binary_rows.append(
            [
                row["variant"],
                int(row["feature_count"]),
                test_f1,
                float(row["test_pr_auc"]),
                float(row["test_balanced_accuracy"]),
                test_f1 - binary_baseline,
            ]
        )
    write_csv(TABLES_DIR / "table_phase6_ablation_binary.csv", ablation_binary_header, ablation_binary_rows)
    write_latex_table(
        TABLES_DIR / "table_phase6_ablation_binary.tex",
        "Phase 6 binary ablation results.",
        "tab:phase6_ablation_binary",
        ablation_binary_header,
        ablation_binary_rows,
    )

    multiclass_baseline = float(multiclass_ablation_df.loc[multiclass_ablation_df["variant"] == "all_features", "test_macro_f1"].iloc[0])
    ablation_multiclass_header = [
        "variant",
        "feature_count",
        "test_macro_f1",
        "test_balanced_accuracy",
        "delta_vs_all",
    ]
    ablation_multiclass_rows = []
    for _, row in multiclass_ablation_df.iterrows():
        test_macro = float(row["test_macro_f1"])
        ablation_multiclass_rows.append(
            [
                row["variant"],
                int(row["feature_count"]),
                test_macro,
                float(row["test_balanced_accuracy"]),
                test_macro - multiclass_baseline,
            ]
        )
    write_csv(
        TABLES_DIR / "table_phase6_ablation_multiclass.csv",
        ablation_multiclass_header,
        ablation_multiclass_rows,
    )
    write_latex_table(
        TABLES_DIR / "table_phase6_ablation_multiclass.tex",
        "Phase 6 multiclass ablation results.",
        "tab:phase6_ablation_multiclass",
        ablation_multiclass_header,
        ablation_multiclass_rows,
    )

    save_generalization_figure(FIGURES_DIR / "fig_phase6_generalization_trend.png", normal_df, anomaly_binary_df)
    save_ablation_figure(FIGURES_DIR / "fig_phase6_ablation_drop.png", binary_ablation_df, multiclass_ablation_df)
    write_readme()
    print(f"wrote phase6 paper assets to {ASSET_ROOT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
