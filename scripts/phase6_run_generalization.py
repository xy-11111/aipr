#!/usr/bin/env python3

import argparse
import csv
import json
import re
import warnings
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import joblib
import numpy as np
import pandas as pd
from sklearn.metrics import (
    accuracy_score,
    average_precision_score,
    balanced_accuracy_score,
    precision_recall_fscore_support,
    roc_auc_score,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATASET_DIR = REPO_ROOT / "datasets" / "phase6_generalization_v1"
DEFAULT_RESULTS_DIR = REPO_ROOT / "results" / "phase6_generalization_v1"
DEFAULT_MODEL_ARTIFACTS_DIR = (
    REPO_ROOT
    / "artifacts"
    / "phase4_baselines_v1"
    / "phase4-baselines-20260523T134916Z"
)
NORMAL_LABEL = "normal"
EXPERIMENT_RE = re.compile(
    r"^phase6-(?P<topology>local|remote)-(?P<load_tier>low|medium|high)-(?P<family_slug>[a-z0-9-]+)-\d{8}T\d{6}Z$"
)
SCENARIO_FAMILY_FROM_TYPE = {
    "normal_steady_state": "normal",
    "backend_rollout_restart": "backend_churn",
    "conntrack_pressure": "conntrack_pressure",
    "path_degradation_netem": "path_degradation",
}
FAMILY_FROM_SLUG = {
    "normal": "normal",
    "backend-churn": "backend_churn",
    "conntrack-pressure": "conntrack_pressure",
    "path-degradation": "path_degradation",
}
PRIMARY_LABEL_BY_FAMILY = {
    "backend_churn": "backend_churn",
    "conntrack_pressure": "conntrack_pressure",
    "path_degradation": "path_degradation",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run Phase 6 generalization evaluation.")
    parser.add_argument("--dataset-dir", type=Path, default=DEFAULT_DATASET_DIR)
    parser.add_argument("--results-dir", type=Path, default=DEFAULT_RESULTS_DIR)
    parser.add_argument("--model-artifacts-dir", type=Path, default=DEFAULT_MODEL_ARTIFACTS_DIR)
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


def build_feature_matrix(
    frame: pd.DataFrame,
    excluded_columns: Sequence[str],
    final_feature_columns: Sequence[str],
) -> pd.DataFrame:
    feature_columns = [column for column in frame.columns if column not in set(excluded_columns)]
    features = convert_numeric_like(frame[feature_columns])
    encoded = pd.get_dummies(features, dummy_na=False, dtype=float)
    encoded = encoded.reindex(columns=list(final_feature_columns), fill_value=0.0)
    return encoded.fillna(0.0)


def parse_experiment_id(experiment_id: str) -> Dict[str, str]:
    match = EXPERIMENT_RE.match(experiment_id)
    if not match:
        return {
            "topology": "unknown",
            "load_tier": "unknown",
            "family_slug": "unknown",
            "family_from_slug": "unknown",
        }
    parts = match.groupdict()
    parts["family_from_slug"] = FAMILY_FROM_SLUG.get(parts["family_slug"], parts["family_slug"].replace("-", "_"))
    return parts


def normal_binary_target(frame: pd.DataFrame) -> np.ndarray:
    return (frame["label"] != NORMAL_LABEL).astype(int).to_numpy()


def predict_scores(estimator: object, features: pd.DataFrame) -> Optional[np.ndarray]:
    if hasattr(estimator, "predict_proba"):
        probabilities = estimator.predict_proba(features)
        if probabilities.ndim == 2 and probabilities.shape[1] >= 2:
            return probabilities[:, 1]
    if hasattr(estimator, "decision_function"):
        decision = estimator.decision_function(features)
        decision = np.asarray(decision, dtype=float)
        if decision.ndim == 1:
            return 1.0 / (1.0 + np.exp(-decision))
    return None


def predict_multiclass_scores(estimator: object, features: pd.DataFrame) -> Optional[np.ndarray]:
    if hasattr(estimator, "predict_proba"):
        return estimator.predict_proba(features)
    return None


def binary_metrics(y_true: np.ndarray, y_pred: np.ndarray, scores: Optional[np.ndarray]) -> Dict[str, object]:
    precision, recall, f1, support = precision_recall_fscore_support(
        y_true,
        y_pred,
        labels=[0, 1],
        average=None,
        zero_division=0,
    )
    payload = {
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
        "prediction_distribution": {
            "normal": int((y_pred == 0).sum()),
            "anomaly": int((y_pred == 1).sum()),
        },
    }
    if scores is not None and len(np.unique(y_true)) > 1:
        payload["pr_auc"] = float(average_precision_score(y_true, scores))
        payload["roc_auc"] = float(roc_auc_score(y_true, scores))
    else:
        payload["pr_auc"] = None
        payload["roc_auc"] = None
    return payload


def balanced_accuracy_without_known_warning(y_true: Sequence[object], y_pred: Sequence[object]) -> float:
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message="y_pred contains classes not in y_true",
            category=UserWarning,
            module="sklearn.metrics._classification",
        )
        return float(balanced_accuracy_score(y_true, y_pred))


def multiclass_metrics(
    y_true: Sequence[str],
    y_pred: Sequence[str],
    all_labels: Sequence[str],
    present_only: bool,
) -> Dict[str, object]:
    active_labels = list(all_labels)
    if present_only:
      present = {label for label in y_true} | {label for label in y_pred}
      active_labels = [label for label in all_labels if label in present]
      if not active_labels:
          active_labels = list(all_labels)

    precision, recall, f1, support = precision_recall_fscore_support(
        y_true,
        y_pred,
        labels=active_labels,
        average=None,
        zero_division=0,
    )
    macro_precision, macro_recall, macro_f1, _ = precision_recall_fscore_support(
        y_true,
        y_pred,
        labels=active_labels,
        average="macro",
        zero_division=0,
    )
    weighted_precision, weighted_recall, weighted_f1, _ = precision_recall_fscore_support(
        y_true,
        y_pred,
        labels=active_labels,
        average="weighted",
        zero_division=0,
    )

    per_class = {}
    for index, label in enumerate(active_labels):
        per_class[label] = {
            "precision": float(precision[index]),
            "recall": float(recall[index]),
            "f1": float(f1[index]),
            "support": int(support[index]),
        }

    return {
        "accuracy": float(accuracy_score(y_true, y_pred)),
        "balanced_accuracy": balanced_accuracy_without_known_warning(y_true, y_pred),
        "macro_precision": float(macro_precision),
        "macro_recall": float(macro_recall),
        "macro_f1": float(macro_f1),
        "weighted_precision": float(weighted_precision),
        "weighted_recall": float(weighted_recall),
        "weighted_f1": float(weighted_f1),
        "prediction_distribution": dict(sorted(Counter(y_pred).items())),
        "per_class": per_class,
        "active_labels": active_labels,
    }


def normal_group_metrics(y_true: np.ndarray, y_pred: np.ndarray) -> Dict[str, float]:
    total = int(len(y_true))
    predicted_anomaly = int((y_pred == 1).sum())
    false_positive_rate = float(predicted_anomaly) / float(total) if total else 0.0
    specificity = 1.0 - false_positive_rate
    return {
        "row_count": total,
        "false_positive_rate": false_positive_rate,
        "specificity": specificity,
        "normal_recall": specificity,
        "anomaly_prediction_rate": false_positive_rate,
    }


def family_rows_for_group(
    frame: pd.DataFrame,
    binary_scores: np.ndarray,
    multiclass_predictions: Sequence[str],
    multiclass_top_scores: Sequence[float],
    label_order: Sequence[str],
) -> pd.DataFrame:
    out = frame[
        [
            "experiment_id",
            "node_name",
            "window_start_unix_ms",
            "window_end_unix_ms",
            "label",
            "anomaly_active",
            "recovery_active",
            "experiment_type",
            "topology",
            "load_tier",
            "scenario_family",
            "family_slug",
        ]
    ].copy()
    out["true_binary"] = normal_binary_target(frame)
    out["score_anomaly"] = binary_scores
    out["pred_binary"] = (binary_scores >= 0.5).astype(int)
    out["pred_binary_label"] = np.where(out["pred_binary"] == 1, "anomaly", "normal")
    out["pred_multiclass_label"] = ""
    out["multiclass_top_score"] = np.nan
    if len(multiclass_predictions) == int((out["label"] != NORMAL_LABEL).sum()):
        anomaly_mask = out["label"] != NORMAL_LABEL
        out.loc[anomaly_mask, "pred_multiclass_label"] = list(multiclass_predictions)
        out.loc[anomaly_mask, "multiclass_top_score"] = list(multiclass_top_scores)
    return out


def main() -> int:
    args = parse_args()
    args.results_dir.mkdir(parents=True, exist_ok=True)

    binary_payload = joblib.load(args.model_artifacts_dir / "binary_best_model.joblib")
    multiclass_payload = joblib.load(args.model_artifacts_dir / "multiclass_best_model.joblib")

    frame = pd.read_csv(args.dataset_dir / "windows_all.csv")
    parsed = frame["experiment_id"].map(parse_experiment_id).apply(pd.Series)
    frame["topology"] = parsed["topology"]
    frame["load_tier"] = parsed["load_tier"]
    frame["family_slug"] = parsed["family_slug"]
    frame["scenario_family"] = frame["experiment_type"].map(SCENARIO_FAMILY_FROM_TYPE).fillna(parsed["family_from_slug"])

    binary_x = build_feature_matrix(
        frame,
        binary_payload["excluded_columns"],
        binary_payload["feature_columns"],
    )
    binary_scores = predict_scores(binary_payload["estimator"], binary_x)
    if binary_scores is None:
        raise SystemExit("binary estimator did not produce anomaly scores")
    binary_predictions = binary_payload["estimator"].predict(binary_x)

    anomaly_mask = frame["label"] != NORMAL_LABEL
    anomaly_frame = frame.loc[anomaly_mask].reset_index(drop=True)
    multiclass_x = build_feature_matrix(
        anomaly_frame,
        multiclass_payload["excluded_columns"],
        multiclass_payload["feature_columns"],
    )
    multiclass_predictions = multiclass_payload["estimator"].predict(multiclass_x)
    multiclass_scores = predict_multiclass_scores(multiclass_payload["estimator"], multiclass_x)
    multiclass_top_scores = (
        np.max(multiclass_scores, axis=1) if multiclass_scores is not None else np.zeros(len(multiclass_predictions), dtype=float)
    )

    predictions = family_rows_for_group(
        frame,
        np.asarray(binary_scores),
        multiclass_predictions,
        multiclass_top_scores,
        multiclass_payload["labels"],
    )
    predictions["pred_binary"] = np.asarray(binary_predictions, dtype=int)
    predictions["pred_binary_label"] = np.where(predictions["pred_binary"] == 1, "anomaly", "normal")
    predictions.to_csv(args.results_dir / "scenario_predictions.csv", index=False)

    normal_frame = predictions.loc[predictions["scenario_family"] == "normal"].reset_index(drop=True)
    anomaly_frame_full = predictions.loc[predictions["scenario_family"] != "normal"].reset_index(drop=True)
    anomaly_only_predictions = predictions.loc[predictions["label"] != NORMAL_LABEL].reset_index(drop=True)

    normal_rows = []
    for (topology, load_tier), group in normal_frame.groupby(["topology", "load_tier"], sort=True):
        metrics = normal_group_metrics(group["true_binary"].to_numpy(), group["pred_binary"].to_numpy())
        normal_rows.append(
            {
                "topology": topology,
                "load_tier": load_tier,
                "row_count": int(metrics["row_count"]),
                "experiment_count": int(group["experiment_id"].nunique()),
                "false_positive_rate": float(metrics["false_positive_rate"]),
                "specificity": float(metrics["specificity"]),
                "normal_recall": float(metrics["normal_recall"]),
                "anomaly_prediction_rate": float(metrics["anomaly_prediction_rate"]),
            }
        )

    anomaly_binary_rows = []
    for (scenario_family, topology, load_tier), group in anomaly_frame_full.groupby(
        ["scenario_family", "topology", "load_tier"],
        sort=True,
    ):
        metrics = binary_metrics(
            group["true_binary"].to_numpy(),
            group["pred_binary"].to_numpy(),
            group["score_anomaly"].to_numpy(),
        )
        anomaly_binary_rows.append(
            {
                "scenario_family": scenario_family,
                "topology": topology,
                "load_tier": load_tier,
                "row_count": int(len(group)),
                "experiment_count": int(group["experiment_id"].nunique()),
                "anomaly_precision": float(metrics["anomaly_precision"]),
                "anomaly_recall": float(metrics["anomaly_recall"]),
                "anomaly_f1": float(metrics["anomaly_f1"]),
                "balanced_accuracy": float(metrics["balanced_accuracy"]),
                "pr_auc": metrics["pr_auc"],
                "roc_auc": metrics["roc_auc"],
            }
        )

    anomaly_multiclass_rows = []
    for (scenario_family, topology, load_tier), group in anomaly_only_predictions.groupby(
        ["scenario_family", "topology", "load_tier"],
        sort=True,
    ):
        metrics = multiclass_metrics(
            group["label"].tolist(),
            group["pred_multiclass_label"].tolist(),
            multiclass_payload["labels"],
            present_only=True,
        )
        primary_label = PRIMARY_LABEL_BY_FAMILY.get(scenario_family, "")
        primary_metrics = metrics["per_class"].get(primary_label, {"f1": 0.0, "support": 0})
        anomaly_multiclass_rows.append(
            {
                "scenario_family": scenario_family,
                "topology": topology,
                "load_tier": load_tier,
                "row_count": int(len(group)),
                "experiment_count": int(group["experiment_id"].nunique()),
                "macro_f1": float(metrics["macro_f1"]),
                "balanced_accuracy": float(metrics["balanced_accuracy"]),
                "primary_label": primary_label,
                "primary_label_f1": float(primary_metrics["f1"]),
                "primary_label_support": int(primary_metrics["support"]),
            }
        )

    normal_header = [
        "topology",
        "load_tier",
        "row_count",
        "experiment_count",
        "false_positive_rate",
        "specificity",
        "normal_recall",
        "anomaly_prediction_rate",
    ]
    anomaly_binary_header = [
        "scenario_family",
        "topology",
        "load_tier",
        "row_count",
        "experiment_count",
        "anomaly_precision",
        "anomaly_recall",
        "anomaly_f1",
        "balanced_accuracy",
        "pr_auc",
        "roc_auc",
    ]
    anomaly_multiclass_header = [
        "scenario_family",
        "topology",
        "load_tier",
        "row_count",
        "experiment_count",
        "macro_f1",
        "balanced_accuracy",
        "primary_label",
        "primary_label_f1",
        "primary_label_support",
    ]

    write_csv(
        args.results_dir / "normal_binary_summary.csv",
        normal_header,
        [[row[column] for column in normal_header] for row in normal_rows],
    )
    write_csv(
        args.results_dir / "anomaly_binary_summary.csv",
        anomaly_binary_header,
        [[row[column] for column in anomaly_binary_header] for row in anomaly_binary_rows],
    )
    write_csv(
        args.results_dir / "anomaly_multiclass_summary.csv",
        anomaly_multiclass_header,
        [[row[column] for column in anomaly_multiclass_header] for row in anomaly_multiclass_rows],
    )

    normal_payload = {
        "generated_at_utc": utc_now(),
        "scope": "normal_only",
        "groups": normal_rows,
    }
    anomaly_binary_payload = {
        "generated_at_utc": utc_now(),
        "scope": "anomaly_experiments_binary",
        "overall": binary_metrics(
            anomaly_frame_full["true_binary"].to_numpy(),
            anomaly_frame_full["pred_binary"].to_numpy(),
            anomaly_frame_full["score_anomaly"].to_numpy(),
        ),
        "groups": anomaly_binary_rows,
    }
    anomaly_multiclass_payload = {
        "generated_at_utc": utc_now(),
        "scope": "anomaly_experiments_multiclass",
        "overall": multiclass_metrics(
            anomaly_only_predictions["label"].tolist(),
            anomaly_only_predictions["pred_multiclass_label"].tolist(),
            multiclass_payload["labels"],
            present_only=False,
        ),
        "groups": anomaly_multiclass_rows,
    }
    write_json(args.results_dir / "normal_binary_metrics.json", normal_payload)
    write_json(args.results_dir / "anomaly_binary_metrics.json", anomaly_binary_payload)
    write_json(args.results_dir / "anomaly_multiclass_metrics.json", anomaly_multiclass_payload)

    run_manifest = {
        "generated_at_utc": utc_now(),
        "dataset_dir": str(args.dataset_dir),
        "results_dir": str(args.results_dir),
        "model_artifacts_dir": str(args.model_artifacts_dir),
        "binary_model_run_id": binary_payload["run_id"],
        "multiclass_model_run_id": multiclass_payload["run_id"],
        "binary_model_task": binary_payload["task"],
        "multiclass_model_task": multiclass_payload["task"],
        "row_count": int(len(frame)),
        "normal_row_count": int(len(normal_frame)),
        "anomaly_row_count": int(len(anomaly_frame_full)),
        "anomaly_only_row_count": int(len(anomaly_only_predictions)),
        "experiment_count": int(frame["experiment_id"].nunique()),
        "label_distribution": dict(sorted(Counter(frame["label"]).items())),
        "experiment_type_distribution": dict(sorted(Counter(frame["experiment_type"]).items())),
        "topology_distribution": dict(sorted(Counter(frame["topology"]).items())),
        "load_tier_distribution": dict(sorted(Counter(frame["load_tier"]).items())),
    }
    write_json(args.results_dir / "run_manifest.json", run_manifest)

    print(f"wrote results to {args.results_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
