#!/usr/bin/env python3

import argparse
import csv
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Sequence, Tuple

import pandas as pd
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import (
    accuracy_score,
    average_precision_score,
    balanced_accuracy_score,
    precision_recall_fscore_support,
    roc_auc_score,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATASET_DIR = REPO_ROOT / "datasets" / "phase3_v1"
DEFAULT_RESULTS_DIR = REPO_ROOT / "results" / "phase6_ablation_v1"
DEFAULT_PHASE4_RUN_MANIFEST = REPO_ROOT / "results" / "phase4_baselines_v1" / "run_manifest.json"
NORMAL_LABEL = "normal"
VARIANT_ORDER = [
    "all_features",
    "minus_datapath_stats",
    "minus_ct_gc",
    "minus_k8s_events",
    "minus_topology_backend",
]
FEATURE_GROUPS = {
    "datapath_stats": [
        "delta_tcp_packets",
        "delta_nodeport_hit",
        "delta_backend_selected",
        "delta_rr_update",
        "delta_snat_install",
        "delta_request_rewrite",
        "delta_revnat_hit",
        "delta_ct_lookup_miss",
        "delta_response_rewrite",
    ],
    "ct_gc": [
        "ct_active_count",
        "fwd_ct_active_count",
        "gc_runs_in_window",
        "gc_deleted_ct",
        "gc_deleted_fwd_ct",
        "ct_entry_timeout_seconds",
        "ct_gc_interval_seconds",
    ],
    "k8s_events": [
        "service_event_seen",
        "slice_event_seen",
        "node_event_seen",
        "sync_reconcile_count",
        "sync_upserted_services",
        "sync_removed_services",
    ],
    "topology_backend": [
        "has_remote_backend",
        "backend_total",
        "backend_local",
        "backend_remote",
        "backend_total_delta",
    ],
}
VARIANT_TO_GROUP = {
    "minus_datapath_stats": "datapath_stats",
    "minus_ct_gc": "ct_gc",
    "minus_k8s_events": "k8s_events",
    "minus_topology_backend": "topology_backend",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Phase 6 feature-group ablation.")
    parser.add_argument("--dataset-dir", type=Path, default=DEFAULT_DATASET_DIR)
    parser.add_argument("--results-dir", type=Path, default=DEFAULT_RESULTS_DIR)
    parser.add_argument("--phase4-run-manifest", type=Path, default=DEFAULT_PHASE4_RUN_MANIFEST)
    return parser.parse_args()


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_json(path: Path) -> Dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=True, indent=2, sort_keys=True)
        handle.write("\n")


def write_csv(path: Path, header: Sequence[str], rows: Iterable[Sequence[object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerows(rows)


def convert_numeric_like(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    for column in out.columns:
      series = out[column]
      if pd.api.types.is_numeric_dtype(series):
          continue
      converted = pd.to_numeric(series, errors="coerce")
      if int(converted.notna().sum() + series.isna().sum()) == len(series):
          out[column] = converted
    return out


def build_feature_matrices(
    train_df: pd.DataFrame,
    val_df: pd.DataFrame,
    test_df: pd.DataFrame,
    excluded_columns: Sequence[str],
    final_feature_columns: Sequence[str],
) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    feature_columns = [column for column in train_df.columns if column not in set(excluded_columns)]
    train_features = convert_numeric_like(train_df[feature_columns])
    val_features = convert_numeric_like(val_df[feature_columns])
    test_features = convert_numeric_like(test_df[feature_columns])

    train_encoded = pd.get_dummies(train_features, dummy_na=False, dtype=float)
    val_encoded = pd.get_dummies(val_features, dummy_na=False, dtype=float)
    test_encoded = pd.get_dummies(test_features, dummy_na=False, dtype=float)

    train_encoded = train_encoded.reindex(columns=list(final_feature_columns), fill_value=0.0).fillna(0.0)
    val_encoded = val_encoded.reindex(columns=list(final_feature_columns), fill_value=0.0).fillna(0.0)
    test_encoded = test_encoded.reindex(columns=list(final_feature_columns), fill_value=0.0).fillna(0.0)
    return train_encoded, val_encoded, test_encoded


def binary_target(frame: pd.DataFrame) -> pd.Series:
    return (frame["label"] != NORMAL_LABEL).astype(int)


def predict_scores(estimator: RandomForestClassifier, features: pd.DataFrame) -> List[float]:
    probabilities = estimator.predict_proba(features)
    return probabilities[:, 1].tolist()


def binary_metrics(y_true: Sequence[int], y_pred: Sequence[int], scores: Sequence[float]) -> Dict[str, object]:
    precision, recall, f1, support = precision_recall_fscore_support(
        y_true,
        y_pred,
        labels=[0, 1],
        average=None,
        zero_division=0,
    )
    return {
        "accuracy": float(accuracy_score(y_true, y_pred)),
        "balanced_accuracy": float(balanced_accuracy_score(y_true, y_pred)),
        "normal_precision": float(precision[0]),
        "normal_recall": float(recall[0]),
        "normal_f1": float(f1[0]),
        "normal_support": int(support[0]),
        "anomaly_precision": float(precision[1]),
        "anomaly_recall": float(recall[1]),
        "anomaly_f1": float(f1[1]),
        "anomaly_support": int(support[1]),
        "pr_auc": float(average_precision_score(y_true, scores)),
        "roc_auc": float(roc_auc_score(y_true, scores)),
        "prediction_distribution": {
            "normal": int(sum(1 for value in y_pred if value == 0)),
            "anomaly": int(sum(1 for value in y_pred if value == 1)),
        },
    }


def multiclass_metrics(y_true: Sequence[str], y_pred: Sequence[str], labels: Sequence[str]) -> Dict[str, object]:
    precision, recall, f1, support = precision_recall_fscore_support(
        y_true,
        y_pred,
        labels=labels,
        average=None,
        zero_division=0,
    )
    macro_precision, macro_recall, macro_f1, _ = precision_recall_fscore_support(
        y_true,
        y_pred,
        labels=labels,
        average="macro",
        zero_division=0,
    )
    weighted_precision, weighted_recall, weighted_f1, _ = precision_recall_fscore_support(
        y_true,
        y_pred,
        labels=labels,
        average="weighted",
        zero_division=0,
    )
    per_class = {}
    for index, label in enumerate(labels):
        per_class[label] = {
            "precision": float(precision[index]),
            "recall": float(recall[index]),
            "f1": float(f1[index]),
            "support": int(support[index]),
        }
    return {
        "accuracy": float(accuracy_score(y_true, y_pred)),
        "balanced_accuracy": float(balanced_accuracy_score(y_true, y_pred)),
        "macro_precision": float(macro_precision),
        "macro_recall": float(macro_recall),
        "macro_f1": float(macro_f1),
        "weighted_precision": float(weighted_precision),
        "weighted_recall": float(weighted_recall),
        "weighted_f1": float(weighted_f1),
        "prediction_distribution": dict(sorted(Counter(y_pred).items())),
        "per_class": per_class,
    }


def build_variant_columns(all_columns: Sequence[str], variant_name: str) -> List[str]:
    if variant_name == "all_features":
        return list(all_columns)
    group_name = VARIANT_TO_GROUP[variant_name]
    dropped = set(FEATURE_GROUPS[group_name])
    return [column for column in all_columns if column not in dropped]


def main() -> int:
    args = parse_args()
    args.results_dir.mkdir(parents=True, exist_ok=True)

    phase4_run_manifest = load_json(args.phase4_run_manifest)
    feature_columns = phase4_run_manifest["feature_columns"]["final_feature_columns"]
    excluded_columns = phase4_run_manifest["feature_columns"]["excluded_columns"]
    binary_best_config = phase4_run_manifest["binary_best_config"]
    multiclass_best_config = phase4_run_manifest["multiclass_best_config"]
    multiclass_labels = phase4_run_manifest["multiclass_labels"]

    train_df = pd.read_csv(args.dataset_dir / "windows_train.csv")
    val_df = pd.read_csv(args.dataset_dir / "windows_val.csv")
    test_df = pd.read_csv(args.dataset_dir / "windows_test.csv")
    train_x, val_x, test_x = build_feature_matrices(train_df, val_df, test_df, excluded_columns, feature_columns)

    anomaly_train_mask = train_df["label"] != NORMAL_LABEL
    anomaly_val_mask = val_df["label"] != NORMAL_LABEL
    anomaly_test_mask = test_df["label"] != NORMAL_LABEL

    binary_results: Dict[str, object] = {}
    multiclass_results: Dict[str, object] = {}
    binary_summary_rows = []
    multiclass_summary_rows = []

    for variant_name in VARIANT_ORDER:
        selected_columns = build_variant_columns(feature_columns, variant_name)
        binary_model = RandomForestClassifier(
            n_estimators=binary_best_config["n_estimators"],
            max_depth=binary_best_config["max_depth"],
            min_samples_leaf=binary_best_config["min_samples_leaf"],
            class_weight=binary_best_config["class_weight"],
            n_jobs=-1,
            random_state=42,
        )
        binary_model.fit(train_x[selected_columns], binary_target(train_df))
        binary_val_pred = binary_model.predict(val_x[selected_columns])
        binary_test_pred = binary_model.predict(test_x[selected_columns])
        binary_val_scores = predict_scores(binary_model, val_x[selected_columns])
        binary_test_scores = predict_scores(binary_model, test_x[selected_columns])
        binary_val_metrics = binary_metrics(binary_target(val_df), binary_val_pred, binary_val_scores)
        binary_test_metrics = binary_metrics(binary_target(test_df), binary_test_pred, binary_test_scores)
        binary_results[variant_name] = {
            "feature_count": len(selected_columns),
            "dropped_group": VARIANT_TO_GROUP.get(variant_name, ""),
            "selected_columns": selected_columns,
            "val_metrics": binary_val_metrics,
            "test_metrics": binary_test_metrics,
        }
        binary_summary_rows.append(
            [
                variant_name,
                len(selected_columns),
                binary_val_metrics["anomaly_f1"],
                binary_val_metrics["pr_auc"],
                binary_val_metrics["balanced_accuracy"],
                binary_test_metrics["anomaly_f1"],
                binary_test_metrics["pr_auc"],
                binary_test_metrics["balanced_accuracy"],
            ]
        )

        anomaly_train_x = train_x.loc[anomaly_train_mask, selected_columns].reset_index(drop=True)
        anomaly_val_x = val_x.loc[anomaly_val_mask, selected_columns].reset_index(drop=True)
        anomaly_test_x = test_x.loc[anomaly_test_mask, selected_columns].reset_index(drop=True)
        anomaly_y_train = train_df.loc[anomaly_train_mask, "label"].reset_index(drop=True)
        anomaly_y_val = val_df.loc[anomaly_val_mask, "label"].reset_index(drop=True)
        anomaly_y_test = test_df.loc[anomaly_test_mask, "label"].reset_index(drop=True)

        multiclass_model = RandomForestClassifier(
            n_estimators=multiclass_best_config["n_estimators"],
            max_depth=multiclass_best_config["max_depth"],
            min_samples_leaf=multiclass_best_config["min_samples_leaf"],
            class_weight=multiclass_best_config["class_weight"],
            n_jobs=-1,
            random_state=42,
        )
        multiclass_model.fit(anomaly_train_x, anomaly_y_train)
        multiclass_val_pred = multiclass_model.predict(anomaly_val_x)
        multiclass_test_pred = multiclass_model.predict(anomaly_test_x)
        multiclass_val_metrics = multiclass_metrics(anomaly_y_val, multiclass_val_pred, multiclass_labels)
        multiclass_test_metrics = multiclass_metrics(anomaly_y_test, multiclass_test_pred, multiclass_labels)
        multiclass_results[variant_name] = {
            "feature_count": len(selected_columns),
            "dropped_group": VARIANT_TO_GROUP.get(variant_name, ""),
            "selected_columns": selected_columns,
            "val_metrics": multiclass_val_metrics,
            "test_metrics": multiclass_test_metrics,
        }
        multiclass_summary_rows.append(
            [
                variant_name,
                len(selected_columns),
                multiclass_val_metrics["macro_f1"],
                multiclass_val_metrics["balanced_accuracy"],
                multiclass_test_metrics["macro_f1"],
                multiclass_test_metrics["balanced_accuracy"],
            ]
        )

    binary_summary_header = [
        "variant",
        "feature_count",
        "val_anomaly_f1",
        "val_pr_auc",
        "val_balanced_accuracy",
        "test_anomaly_f1",
        "test_pr_auc",
        "test_balanced_accuracy",
    ]
    multiclass_summary_header = [
        "variant",
        "feature_count",
        "val_macro_f1",
        "val_balanced_accuracy",
        "test_macro_f1",
        "test_balanced_accuracy",
    ]

    write_csv(args.results_dir / "binary_variant_summary.csv", binary_summary_header, binary_summary_rows)
    write_csv(args.results_dir / "multiclass_variant_summary.csv", multiclass_summary_header, multiclass_summary_rows)
    write_json(args.results_dir / "binary_metrics.json", {"variants": binary_results, "generated_at_utc": utc_now()})
    write_json(args.results_dir / "multiclass_metrics.json", {"variants": multiclass_results, "generated_at_utc": utc_now()})

    run_manifest = {
        "generated_at_utc": utc_now(),
        "dataset_dir": str(args.dataset_dir),
        "results_dir": str(args.results_dir),
        "phase4_run_manifest": str(args.phase4_run_manifest),
        "variant_order": VARIANT_ORDER,
        "feature_groups": FEATURE_GROUPS,
        "binary_best_config": binary_best_config,
        "multiclass_best_config": multiclass_best_config,
        "binary_best_model": phase4_run_manifest["binary_best_model"],
        "multiclass_best_model": phase4_run_manifest["multiclass_best_model"],
        "feature_column_count": len(feature_columns),
        "train_rows": int(len(train_df)),
        "val_rows": int(len(val_df)),
        "test_rows": int(len(test_df)),
        "train_label_distribution": dict(sorted(Counter(train_df["label"]).items())),
        "val_label_distribution": dict(sorted(Counter(val_df["label"]).items())),
        "test_label_distribution": dict(sorted(Counter(test_df["label"]).items())),
    }
    write_json(args.results_dir / "run_manifest.json", run_manifest)
    print(f"wrote results to {args.results_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
