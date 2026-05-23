#!/usr/bin/env python3

import csv
import json
import math
import shutil
from pathlib import Path
from typing import Dict, Iterable, List, Sequence, Tuple

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np


REPO_ROOT = Path(__file__).resolve().parents[1]
RESULTS_DIR = REPO_ROOT / "results" / "phase4_baselines_v1"
ASSET_ROOT = REPO_ROOT / "paper_assets" / "phase4_v1"
TABLES_DIR = ASSET_ROOT / "tables"
FIGURES_DIR = ASSET_ROOT / "figures"
SOURCE_DIR = ASSET_ROOT / "source"


def load_json(path: Path) -> Dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def read_csv(path: Path) -> List[List[str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.reader(handle))


def write_csv(path: Path, header: Sequence[str], rows: Iterable[Sequence[object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        for row in rows:
            writer.writerow(row)


def latex_escape(text: str) -> str:
    replacements = {
        "\\": r"\textbackslash{}",
        "_": r"\_",
        "%": r"\%",
        "&": r"\&",
        "#": r"\#",
    }
    out = text
    for old, new in replacements.items():
        out = out.replace(old, new)
    return out


def write_latex_table(path: Path, caption: str, label: str, header: Sequence[str], rows: Iterable[Sequence[object]]) -> None:
    rows = list(rows)
    col_spec = "l" + "r" * (len(header) - 1)
    with path.open("w", encoding="utf-8") as handle:
        handle.write("\\begin{table}[t]\n")
        handle.write("\\centering\n")
        handle.write(f"\\caption{{{caption}}}\n")
        handle.write(f"\\label{{{label}}}\n")
        handle.write(f"\\begin{{tabular}}{{{col_spec}}}\n")
        handle.write("\\hline\n")
        handle.write(" & ".join(latex_escape(str(item)) for item in header) + " \\\\\n")
        handle.write("\\hline\n")
        for row in rows:
            formatted = []
            for item in row:
                if isinstance(item, float):
                    formatted.append(f"{item:.4f}")
                else:
                    formatted.append(latex_escape(str(item)))
            handle.write(" & ".join(formatted) + " \\\\\n")
        handle.write("\\hline\n")
        handle.write("\\end{tabular}\n")
        handle.write("\\end{table}\n")


def family_rank(family: str) -> int:
    order = {
        "DummyClassifier": 0,
        "RuleBasedBinaryDetector": 1,
        "LogisticRegression": 2,
        "RandomForestClassifier": 3,
    }
    return order.get(family, 99)


def pick_family_best_binary(payload: Dict[str, object]) -> List[Dict[str, object]]:
    candidates = payload["candidates"]
    grouped: Dict[str, List[Tuple[str, Dict[str, object]]]] = {}
    for name, info in candidates.items():
        grouped.setdefault(info["family"], []).append((name, info))
    selected = []
    for family, entries in grouped.items():
        best_name, best_info = max(
            entries,
            key=lambda entry: (
                entry[1]["val_metrics"]["anomaly_f1"],
                entry[1]["val_metrics"]["pr_auc"] if entry[1]["val_metrics"]["pr_auc"] is not None else -1.0,
            ),
        )
        selected.append({"name": best_name, **best_info})
    selected.sort(key=lambda item: family_rank(item["family"]))
    return selected


def pick_family_best_multiclass(payload: Dict[str, object]) -> List[Dict[str, object]]:
    candidates = payload["candidates"]
    grouped: Dict[str, List[Tuple[str, Dict[str, object]]]] = {}
    for name, info in candidates.items():
        grouped.setdefault(info["family"], []).append((name, info))
    selected = []
    for family, entries in grouped.items():
        best_name, best_info = max(
            entries,
            key=lambda entry: (
                entry[1]["val_metrics"]["macro_f1"],
                entry[1]["val_metrics"]["balanced_accuracy"],
            ),
        )
        selected.append({"name": best_name, **best_info})
    selected.sort(key=lambda item: family_rank(item["family"]))
    return selected


def resolve_test_metrics(payload: Dict[str, object], item: Dict[str, object]) -> Dict[str, object] | None:
    if item.get("test_metrics") is not None:
        return item["test_metrics"]
    if item["name"] == payload.get("best_model"):
        return payload.get("test_best")
    return None


def save_confusion_png(path: Path, matrix_rows: List[List[str]], title: str) -> None:
    labels = [item.replace("true:", "") for item in [row[0] for row in matrix_rows[1:]]]
    matrix = np.array([[int(value) for value in row[1:]] for row in matrix_rows[1:]], dtype=int)
    fig, ax = plt.subplots(figsize=(6.2, 5.0), facecolor="white")
    image = ax.imshow(matrix, cmap="Blues")
    ax.set_title(title, fontsize=12)
    ax.set_xlabel("Predicted label", fontsize=10)
    ax.set_ylabel("True label", fontsize=10)
    ax.set_xticks(range(len(labels)))
    ax.set_yticks(range(len(labels)))
    ax.set_xticklabels(labels, rotation=20, ha="right", fontsize=9)
    ax.set_yticklabels(labels, fontsize=9)
    for i in range(matrix.shape[0]):
        for j in range(matrix.shape[1]):
            ax.text(j, i, str(matrix[i, j]), ha="center", va="center", fontsize=9, color="black")
    fig.colorbar(image, ax=ax, fraction=0.046, pad=0.04)
    fig.tight_layout()
    fig.savefig(path, dpi=200, bbox_inches="tight")
    plt.close(fig)


def save_per_class_f1_png(path: Path, per_class: Dict[str, Dict[str, float]]) -> None:
    items = sorted(
        ((label, metrics["f1"]) for label, metrics in per_class.items()),
        key=lambda item: item[1],
        reverse=True,
    )
    labels = [item[0] for item in items]
    values = [item[1] for item in items]
    fig, ax = plt.subplots(figsize=(7.2, 4.6), facecolor="white")
    bars = ax.bar(range(len(labels)), values, color="#4C72B0")
    ax.set_title("Per-class F1 on multiclass test split", fontsize=12)
    ax.set_ylabel("F1 score", fontsize=10)
    ax.set_ylim(0.0, 1.0)
    ax.set_xticks(range(len(labels)))
    ax.set_xticklabels(labels, rotation=20, ha="right", fontsize=9)
    for bar, value in zip(bars, values):
        ax.text(bar.get_x() + bar.get_width() / 2.0, value + 0.02, f"{value:.3f}", ha="center", va="bottom", fontsize=8)
    ax.grid(axis="y", alpha=0.25)
    fig.tight_layout()
    fig.savefig(path, dpi=200, bbox_inches="tight")
    plt.close(fig)


def write_readme(run_manifest: Dict[str, object]) -> None:
    content = f"""# Phase 4 Paper Assets v1

- Source run id: `{run_manifest["run_id"]}`
- Source results dir: `{RESULTS_DIR}`
- Purpose: freeze baseline outputs into paper-ready tables, figures, and source copies

## Directory layout

- `tables/`: CSV and LaTeX tables
- `figures/`: PNG figures
- `source/`: copied source JSON/CSV used to build the paper assets

## Notes

- No model retraining is performed in this export step.
- Binary results are summarized by model family best candidate.
- Multiclass results are summarized by model family best candidate.
- Held-out test metrics are only available for the best model selected in the original Phase 4 run. Other family rows keep test columns empty.
"""
    (ASSET_ROOT / "README.md").write_text(content, encoding="utf-8")


def main() -> int:
    TABLES_DIR.mkdir(parents=True, exist_ok=True)
    FIGURES_DIR.mkdir(parents=True, exist_ok=True)
    SOURCE_DIR.mkdir(parents=True, exist_ok=True)

    binary_metrics = load_json(RESULTS_DIR / "binary_metrics.json")
    multiclass_metrics = load_json(RESULTS_DIR / "multiclass_metrics.json")
    run_manifest = load_json(RESULTS_DIR / "run_manifest.json")

    for filename in [
        "binary_metrics.json",
        "multiclass_metrics.json",
        "binary_confusion_matrix.csv",
        "multiclass_confusion_matrix.csv",
        "binary_predictions_val.csv",
        "binary_predictions_test.csv",
        "multiclass_predictions_val.csv",
        "multiclass_predictions_test.csv",
        "feature_columns.json",
        "run_manifest.json",
        "baseline_summary.md",
    ]:
        shutil.copy2(RESULTS_DIR / filename, SOURCE_DIR / filename)

    binary_rows = []
    for item in pick_family_best_binary(binary_metrics):
        val_metrics = item["val_metrics"]
        test_metrics = resolve_test_metrics(binary_metrics, item)
        binary_rows.append(
            [
                item["family"],
                item["name"],
                float(val_metrics["anomaly_f1"]),
                float(val_metrics["pr_auc"]) if val_metrics["pr_auc"] is not None else math.nan,
                float(val_metrics["roc_auc"]) if val_metrics["roc_auc"] is not None else math.nan,
                float(test_metrics["anomaly_f1"]) if test_metrics is not None else math.nan,
                float(test_metrics["pr_auc"]) if test_metrics is not None and test_metrics["pr_auc"] is not None else math.nan,
                float(test_metrics["roc_auc"]) if test_metrics is not None and test_metrics["roc_auc"] is not None else math.nan,
                float(test_metrics["balanced_accuracy"]) if test_metrics is not None else math.nan,
            ]
        )
    binary_header = [
        "family",
        "model_name",
        "val_anomaly_f1",
        "val_pr_auc",
        "val_roc_auc",
        "test_anomaly_f1",
        "test_pr_auc",
        "test_roc_auc",
        "test_balanced_accuracy",
    ]
    write_csv(TABLES_DIR / "table_phase4_binary_main.csv", binary_header, binary_rows)
    write_latex_table(
        TABLES_DIR / "table_phase4_binary_main.tex",
        "Phase 4 binary anomaly detection baseline results.",
        "tab:phase4_binary_main",
        binary_header,
        binary_rows,
    )

    multiclass_rows = []
    for item in pick_family_best_multiclass(multiclass_metrics):
        val_metrics = item["val_metrics"]
        test_metrics = resolve_test_metrics(multiclass_metrics, item)
        multiclass_rows.append(
            [
                item["family"],
                item["name"],
                float(val_metrics["macro_f1"]),
                float(val_metrics["balanced_accuracy"]),
                float(test_metrics["macro_f1"]) if test_metrics is not None else math.nan,
                float(test_metrics["balanced_accuracy"]) if test_metrics is not None else math.nan,
                float(test_metrics["accuracy"]) if test_metrics is not None else math.nan,
            ]
        )
    multiclass_header = [
        "family",
        "model_name",
        "val_macro_f1",
        "val_balanced_accuracy",
        "test_macro_f1",
        "test_balanced_accuracy",
        "test_accuracy",
    ]
    write_csv(TABLES_DIR / "table_phase4_multiclass_main.csv", multiclass_header, multiclass_rows)
    write_latex_table(
        TABLES_DIR / "table_phase4_multiclass_main.tex",
        "Phase 4 multiclass anomaly classification baseline results.",
        "tab:phase4_multiclass_main",
        multiclass_header,
        multiclass_rows,
    )

    per_class = multiclass_metrics["test_best"]["per_class"]
    per_class_rows = []
    for label, metrics in sorted(per_class.items()):
        per_class_rows.append(
            [
                label,
                float(metrics["precision"]),
                float(metrics["recall"]),
                float(metrics["f1"]),
                int(metrics["support"]),
            ]
        )
    per_class_header = ["label", "precision", "recall", "f1", "support"]
    write_csv(TABLES_DIR / "table_phase4_multiclass_per_class.csv", per_class_header, per_class_rows)
    write_latex_table(
        TABLES_DIR / "table_phase4_multiclass_per_class.tex",
        "Per-class multiclass anomaly detection results on the test split.",
        "tab:phase4_multiclass_per_class",
        per_class_header,
        per_class_rows,
    )

    save_confusion_png(
        FIGURES_DIR / "fig_phase4_binary_confusion.png",
        read_csv(RESULTS_DIR / "binary_confusion_matrix.csv"),
        "Binary confusion matrix on the test split",
    )
    save_confusion_png(
        FIGURES_DIR / "fig_phase4_multiclass_confusion.png",
        read_csv(RESULTS_DIR / "multiclass_confusion_matrix.csv"),
        "Multiclass confusion matrix on the test split",
    )
    save_per_class_f1_png(FIGURES_DIR / "fig_phase4_per_class_f1.png", per_class)
    write_readme(run_manifest)

    print(f"wrote paper assets to {ASSET_ROOT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
