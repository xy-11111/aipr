#!/usr/bin/env python3

import argparse
import json
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timezone
from itertools import product
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import joblib
import numpy as np
import pandas as pd
from sklearn.dummy import DummyClassifier
from sklearn.ensemble import RandomForestClassifier
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    accuracy_score,
    average_precision_score,
    balanced_accuracy_score,
    confusion_matrix,
    precision_recall_fscore_support,
    roc_auc_score,
)
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler


NORMAL_LABEL = "normal"
ANOMALY_LABEL = "anomaly"
EXCLUDED_COLUMNS = {
    "label",
    "label_source",
    "anomaly_active",
    "recovery_active",
    "split",
    "experiment_type",
    "is_baseline_experiment",
    "event_count",
    "experiment_id",
    "node_name",
    "node_ip",
    "service_namespace",
    "service_name",
    "service_nodeport",
    "source_artifact_dir",
    "traffic_probe_total",
    "traffic_probe_success",
    "traffic_probe_success_rate",
    "window_start_unix_ms",
    "window_end_unix_ms",
}
RULE_PRESSURE_FIELDS = [
    "ct_active_count",
    "delta_new_conn",
    "delta_ct_lookup_miss",
    "delta_tcp_packets",
]
RULE_ERROR_FIELDS = [
    "delta_map_miss",
    "delta_rewrite_fail",
    "delta_redirect_fail",
    "delta_fallback_pass",
]
RULE_CONTROL_FIELDS = [
    "service_event_seen",
    "slice_event_seen",
    "node_event_seen",
]


@dataclass
class ModelResult:
    name: str
    family: str
    config: Dict[str, object]
    val_metrics: Dict[str, object]
    estimator: object
    test_metrics: Optional[Dict[str, object]] = None
    val_predictions: Optional[np.ndarray] = None
    val_scores: Optional[np.ndarray] = None
    test_predictions: Optional[np.ndarray] = None
    test_scores: Optional[np.ndarray] = None


class RuleBasedBinaryDetector:
    def __init__(self) -> None:
        self.thresholds: Dict[str, float] = {}
        self.rule_count = 0

    def fit(self, frame: pd.DataFrame) -> "RuleBasedBinaryDetector":
        normal_frame = frame.loc[frame["label"] == NORMAL_LABEL]
        threshold_source = normal_frame if not normal_frame.empty else frame
        for field in RULE_PRESSURE_FIELDS:
            self.thresholds[field] = float(threshold_source[field].quantile(0.95))
        self.rule_count = 3 + len(RULE_PRESSURE_FIELDS)
        return self

    def score_samples(self, frame: pd.DataFrame) -> np.ndarray:
        control_trigger = (frame[RULE_CONTROL_FIELDS].sum(axis=1) > 0).astype(float)
        backend_trigger = (frame["backend_total_delta"].abs() > 0).astype(float)
        error_trigger = (frame[RULE_ERROR_FIELDS].sum(axis=1) > 0).astype(float)
        pressure_scores = []
        for field in RULE_PRESSURE_FIELDS:
            threshold = self.thresholds.get(field, 0.0)
            pressure_scores.append((frame[field] > threshold).astype(float))
        stacked = np.column_stack(
            [control_trigger, backend_trigger, error_trigger, *pressure_scores]
        )
        return stacked.sum(axis=1) / float(self.rule_count)

    def predict(self, frame: pd.DataFrame) -> np.ndarray:
        scores = self.score_samples(frame)
        return (scores > 0.0).astype(int)

    def predict_proba(self, frame: pd.DataFrame) -> np.ndarray:
        scores = self.score_samples(frame)
        return np.column_stack([1.0 - scores, scores])


def parse_args() -> argparse.Namespace:
    repo_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="Run Phase 4 baseline experiments.")
    parser.add_argument("--dataset-dir", type=Path, default=repo_root / "datasets" / "phase3_v1")
    parser.add_argument("--results-dir", type=Path, default=repo_root / "results" / "phase4_baselines_v1")
    parser.add_argument("--artifacts-dir", type=Path, default=repo_root / "artifacts" / "phase4_baselines_v1")
    parser.add_argument("--random-seed", type=int, default=42)
    return parser.parse_args()


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def utc_compact() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def load_split_frame(dataset_dir: Path, name: str) -> pd.DataFrame:
    return pd.read_csv(dataset_dir / f"windows_{name}.csv")


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
    train_df: pd.DataFrame, val_df: pd.DataFrame, test_df: pd.DataFrame
) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, List[str], List[str], List[str]]:
    feature_columns = [column for column in train_df.columns if column not in EXCLUDED_COLUMNS]
    train_features = convert_numeric_like(train_df[feature_columns])
    val_features = convert_numeric_like(val_df[feature_columns])
    test_features = convert_numeric_like(test_df[feature_columns])

    train_encoded = pd.get_dummies(train_features, dummy_na=False, dtype=float)
    val_encoded = pd.get_dummies(val_features, dummy_na=False, dtype=float)
    test_encoded = pd.get_dummies(test_features, dummy_na=False, dtype=float)

    val_encoded = val_encoded.reindex(columns=train_encoded.columns, fill_value=0.0)
    test_encoded = test_encoded.reindex(columns=train_encoded.columns, fill_value=0.0)

    constant_columns = [
        column
        for column in train_encoded.columns
        if train_encoded[column].nunique(dropna=False) <= 1
    ]
    train_encoded = train_encoded.drop(columns=constant_columns, errors="ignore").fillna(0.0)
    val_encoded = val_encoded.drop(columns=constant_columns, errors="ignore").fillna(0.0)
    test_encoded = test_encoded.drop(columns=constant_columns, errors="ignore").fillna(0.0)

    return (
        train_encoded,
        val_encoded,
        test_encoded,
        feature_columns,
        constant_columns,
        train_encoded.columns.tolist(),
    )


def binary_target(frame: pd.DataFrame) -> np.ndarray:
    return (frame["label"] != NORMAL_LABEL).astype(int).to_numpy()


def anomaly_only(frame: pd.DataFrame) -> pd.DataFrame:
    return frame.loc[frame["label"] != NORMAL_LABEL].reset_index(drop=True)


def predict_scores(estimator: object, features) -> Optional[np.ndarray]:
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


def predict_multiclass_scores(estimator: object, features) -> Optional[np.ndarray]:
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
    metrics = {
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
            NORMAL_LABEL: int((y_pred == 0).sum()),
            ANOMALY_LABEL: int((y_pred == 1).sum()),
        },
    }
    if scores is not None and len(np.unique(y_true)) > 1:
        metrics["pr_auc"] = float(average_precision_score(y_true, scores))
        metrics["roc_auc"] = float(roc_auc_score(y_true, scores))
    else:
        metrics["pr_auc"] = None
        metrics["roc_auc"] = None
    return metrics


def multiclass_metrics(
    y_true: Sequence[str],
    y_pred: Sequence[str],
    labels: Sequence[str],
) -> Dict[str, object]:
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
        average="macro",
        zero_division=0,
    )
    weighted_precision, weighted_recall, weighted_f1, _ = precision_recall_fscore_support(
        y_true,
        y_pred,
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


def fit_binary_candidates(
    train_df: pd.DataFrame,
    val_df: pd.DataFrame,
    train_x: pd.DataFrame,
    val_x: pd.DataFrame,
    random_seed: int,
) -> List[ModelResult]:
    results: List[ModelResult] = []
    y_train = binary_target(train_df)
    y_val = binary_target(val_df)

    dummy = DummyClassifier(strategy="prior")
    dummy.fit(train_x, y_train)
    dummy_val_pred = dummy.predict(val_x)
    dummy_val_scores = predict_scores(dummy, val_x)
    results.append(
        ModelResult(
            name="dummy_prior",
            family="DummyClassifier",
            config={"strategy": "prior"},
            val_metrics=binary_metrics(y_val, dummy_val_pred, dummy_val_scores),
            estimator=dummy,
            val_predictions=dummy_val_pred,
            val_scores=dummy_val_scores,
        )
    )

    rule = RuleBasedBinaryDetector().fit(train_df)
    rule_val_pred = rule.predict(val_df)
    rule_val_scores = rule.predict_proba(val_df)[:, 1]
    results.append(
        ModelResult(
            name="rule_based",
            family="RuleBasedBinaryDetector",
            config={"pressure_quantile": 0.95},
            val_metrics=binary_metrics(y_val, rule_val_pred, rule_val_scores),
            estimator=rule,
            val_predictions=rule_val_pred,
            val_scores=rule_val_scores,
        )
    )

    for c_value, class_weight in product([0.1, 1.0, 10.0], [None, "balanced"]):
        estimator = Pipeline(
            [
                ("scaler", StandardScaler()),
                (
                    "clf",
                    LogisticRegression(
                        C=c_value,
                        class_weight=class_weight,
                        max_iter=2000,
                        random_state=random_seed,
                    ),
                ),
            ]
        )
        estimator.fit(train_x, y_train)
        val_pred = estimator.predict(val_x)
        val_scores = predict_scores(estimator, val_x)
        results.append(
            ModelResult(
                name=f"logreg_c{c_value}_cw{class_weight or 'none'}",
                family="LogisticRegression",
                config={"C": c_value, "class_weight": class_weight},
                val_metrics=binary_metrics(y_val, val_pred, val_scores),
                estimator=estimator,
                val_predictions=val_pred,
                val_scores=val_scores,
            )
        )

    for n_estimators, max_depth, min_samples_leaf, class_weight in product(
        [100, 300],
        [None, 8, 16],
        [1, 5],
        [None, "balanced"],
    ):
        estimator = RandomForestClassifier(
            n_estimators=n_estimators,
            max_depth=max_depth,
            min_samples_leaf=min_samples_leaf,
            class_weight=class_weight,
            n_jobs=-1,
            random_state=random_seed,
        )
        estimator.fit(train_x, y_train)
        val_pred = estimator.predict(val_x)
        val_scores = predict_scores(estimator, val_x)
        results.append(
            ModelResult(
                name=(
                    f"rf_n{n_estimators}_d{max_depth if max_depth is not None else 'none'}"
                    f"_leaf{min_samples_leaf}_cw{class_weight or 'none'}"
                ),
                family="RandomForestClassifier",
                config={
                    "n_estimators": n_estimators,
                    "max_depth": max_depth,
                    "min_samples_leaf": min_samples_leaf,
                    "class_weight": class_weight,
                },
                val_metrics=binary_metrics(y_val, val_pred, val_scores),
                estimator=estimator,
                val_predictions=val_pred,
                val_scores=val_scores,
            )
        )
    return results


def fit_multiclass_candidates(
    train_x: pd.DataFrame,
    val_x: pd.DataFrame,
    y_train: pd.Series,
    y_val: pd.Series,
    labels: Sequence[str],
    random_seed: int,
) -> List[ModelResult]:
    results: List[ModelResult] = []

    dummy = DummyClassifier(strategy="prior")
    dummy.fit(train_x, y_train)
    dummy_val_pred = dummy.predict(val_x)
    results.append(
        ModelResult(
            name="dummy_prior",
            family="DummyClassifier",
            config={"strategy": "prior"},
            val_metrics=multiclass_metrics(y_val, dummy_val_pred, labels),
            estimator=dummy,
            val_predictions=np.asarray(dummy_val_pred),
            val_scores=predict_multiclass_scores(dummy, val_x),
        )
    )

    for c_value, class_weight in product([0.1, 1.0, 10.0], [None, "balanced"]):
        estimator = Pipeline(
            [
                ("scaler", StandardScaler()),
                (
                    "clf",
                    LogisticRegression(
                        C=c_value,
                        class_weight=class_weight,
                        max_iter=2000,
                        random_state=random_seed,
                    ),
                ),
            ]
        )
        estimator.fit(train_x, y_train)
        val_pred = estimator.predict(val_x)
        results.append(
            ModelResult(
                name=f"logreg_c{c_value}_cw{class_weight or 'none'}",
                family="LogisticRegression",
                config={"C": c_value, "class_weight": class_weight},
                val_metrics=multiclass_metrics(y_val, val_pred, labels),
                estimator=estimator,
                val_predictions=np.asarray(val_pred),
                val_scores=predict_multiclass_scores(estimator, val_x),
            )
        )

    for n_estimators, max_depth, min_samples_leaf, class_weight in product(
        [100, 300],
        [None, 8, 16],
        [1, 5],
        [None, "balanced"],
    ):
        estimator = RandomForestClassifier(
            n_estimators=n_estimators,
            max_depth=max_depth,
            min_samples_leaf=min_samples_leaf,
            class_weight=class_weight,
            n_jobs=-1,
            random_state=random_seed,
        )
        estimator.fit(train_x, y_train)
        val_pred = estimator.predict(val_x)
        results.append(
            ModelResult(
                name=(
                    f"rf_n{n_estimators}_d{max_depth if max_depth is not None else 'none'}"
                    f"_leaf{min_samples_leaf}_cw{class_weight or 'none'}"
                ),
                family="RandomForestClassifier",
                config={
                    "n_estimators": n_estimators,
                    "max_depth": max_depth,
                    "min_samples_leaf": min_samples_leaf,
                    "class_weight": class_weight,
                },
                val_metrics=multiclass_metrics(y_val, val_pred, labels),
                estimator=estimator,
                val_predictions=np.asarray(val_pred),
                val_scores=predict_multiclass_scores(estimator, val_x),
            )
        )
    return results


def choose_best_binary(results: Sequence[ModelResult]) -> ModelResult:
    return max(
        results,
        key=lambda result: (
            result.val_metrics["anomaly_f1"],
            result.val_metrics["pr_auc"] if result.val_metrics["pr_auc"] is not None else -1.0,
        ),
    )


def choose_best_multiclass(results: Sequence[ModelResult]) -> ModelResult:
    return max(
        results,
        key=lambda result: (
            result.val_metrics["macro_f1"],
            result.val_metrics["balanced_accuracy"],
        ),
    )


def write_json(path: Path, payload: Dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=True, indent=2, sort_keys=True)
        handle.write("\n")


def write_confusion_matrix(path: Path, matrix: np.ndarray, labels: Sequence[str]) -> None:
    frame = pd.DataFrame(matrix, index=[f"true:{label}" for label in labels], columns=[f"pred:{label}" for label in labels])
    frame.insert(0, "true_label", frame.index)
    frame.to_csv(path, index=False)


def write_binary_predictions(
    path: Path,
    split_name: str,
    source_df: pd.DataFrame,
    y_true: np.ndarray,
    y_pred: np.ndarray,
    scores: Optional[np.ndarray],
) -> None:
    frame = source_df[
        [
            "experiment_id",
            "node_name",
            "window_start_unix_ms",
            "window_end_unix_ms",
            "label",
            "anomaly_active",
            "recovery_active",
        ]
    ].copy()
    frame.insert(len(frame.columns), "split", split_name)
    frame.insert(len(frame.columns), "true_binary", y_true)
    frame.insert(len(frame.columns), "pred_binary", y_pred)
    frame.insert(len(frame.columns), "pred_label", np.where(y_pred == 1, ANOMALY_LABEL, NORMAL_LABEL))
    frame.insert(
        len(frame.columns),
        "score_anomaly",
        scores if scores is not None else np.zeros(len(frame), dtype=float),
    )
    frame.to_csv(path, index=False)


def write_multiclass_predictions(
    path: Path,
    split_name: str,
    source_df: pd.DataFrame,
    y_pred: Sequence[str],
    scores: Optional[np.ndarray],
    label_order: Sequence[str],
) -> None:
    frame = source_df[
        [
            "experiment_id",
            "node_name",
            "window_start_unix_ms",
            "window_end_unix_ms",
            "label",
            "anomaly_active",
            "recovery_active",
        ]
    ].copy()
    frame.insert(len(frame.columns), "split", split_name)
    frame.insert(len(frame.columns), "true_label", source_df["label"].to_numpy())
    frame.insert(len(frame.columns), "pred_label", np.asarray(y_pred))
    if scores is not None:
        top_scores = np.max(scores, axis=1)
        frame.insert(len(frame.columns), "top_score", top_scores)
        for index, label in enumerate(label_order):
            frame.insert(len(frame.columns), f"score_{label}", scores[:, index])
    frame.to_csv(path, index=False)


def results_to_payload(results: Sequence[ModelResult], best_name: str) -> Dict[str, object]:
    payload = {
        "best_model": best_name,
        "candidates": {},
    }
    for result in results:
        payload["candidates"][result.name] = {
            "family": result.family,
            "config": result.config,
            "val_metrics": result.val_metrics,
            "test_metrics": result.test_metrics,
        }
    return payload


def summarise_markdown(
    path: Path,
    run_id: str,
    dataset_dir: Path,
    feature_columns: Sequence[str],
    constant_columns: Sequence[str],
    binary_best: ModelResult,
    multiclass_best: ModelResult,
) -> None:
    with path.open("w", encoding="utf-8") as handle:
        handle.write("# Phase 4 Baseline 摘要\n\n")
        handle.write(f"- 运行 ID：`{run_id}`\n")
        handle.write(f"- 数据集目录：`{dataset_dir}`\n")
        handle.write(f"- 最终特征列数：`{len(feature_columns)}`\n")
        handle.write(f"- 被移除的常量列数：`{len(constant_columns)}`\n\n")

        handle.write("## 1. 二分类最佳模型\n\n")
        handle.write(f"- 模型：`{binary_best.name}`\n")
        handle.write(f"- 验证集 anomaly F1：`{binary_best.val_metrics['anomaly_f1']:.4f}`\n")
        if binary_best.val_metrics.get("pr_auc") is not None:
            handle.write(f"- 验证集 PR-AUC：`{binary_best.val_metrics['pr_auc']:.4f}`\n")
        if binary_best.test_metrics:
            handle.write(f"- 测试集 anomaly F1：`{binary_best.test_metrics['anomaly_f1']:.4f}`\n")
            if binary_best.test_metrics.get("pr_auc") is not None:
                handle.write(f"- 测试集 PR-AUC：`{binary_best.test_metrics['pr_auc']:.4f}`\n")
        handle.write("\n")

        handle.write("## 2. 多分类最佳模型\n\n")
        handle.write(f"- 模型：`{multiclass_best.name}`\n")
        handle.write(f"- 验证集 macro-F1：`{multiclass_best.val_metrics['macro_f1']:.4f}`\n")
        handle.write(f"- 验证集 balanced accuracy：`{multiclass_best.val_metrics['balanced_accuracy']:.4f}`\n")
        if multiclass_best.test_metrics:
            handle.write(f"- 测试集 macro-F1：`{multiclass_best.test_metrics['macro_f1']:.4f}`\n")
            handle.write(f"- 测试集 balanced accuracy：`{multiclass_best.test_metrics['balanced_accuracy']:.4f}`\n")
        handle.write("\n")

        handle.write("## 3. 多分类测试集按类表现\n\n")
        if multiclass_best.test_metrics:
            for label, metrics in sorted(multiclass_best.test_metrics["per_class"].items()):
                handle.write(
                    f"- `{label}`：precision=`{metrics['precision']:.4f}`，"
                    f" recall=`{metrics['recall']:.4f}`，f1=`{metrics['f1']:.4f}`，support=`{metrics['support']}`\n"
                )
        handle.write("\n")

        handle.write("## 4. 说明\n\n")
        handle.write("- 二分类正类固定为 `anomaly`\n")
        handle.write("- 多分类只在异常窗口上训练和评估\n")
        handle.write("- 本轮所有结果均基于固定的 `phase3_v1` 数据集\n")


def main() -> int:
    args = parse_args()
    dataset_dir = args.dataset_dir
    results_dir = args.results_dir
    artifacts_root = args.artifacts_dir
    results_dir.mkdir(parents=True, exist_ok=True)
    artifacts_root.mkdir(parents=True, exist_ok=True)

    train_df = load_split_frame(dataset_dir, "train")
    val_df = load_split_frame(dataset_dir, "val")
    test_df = load_split_frame(dataset_dir, "test")

    train_x, val_x, test_x, raw_feature_columns, constant_columns, final_feature_columns = build_feature_matrices(
        train_df, val_df, test_df
    )

    run_id = f"phase4-baselines-{utc_compact()}"
    run_artifacts_dir = artifacts_root / run_id
    run_artifacts_dir.mkdir(parents=True, exist_ok=True)

    binary_results = fit_binary_candidates(train_df, val_df, train_x, val_x, args.random_seed)
    binary_best = choose_best_binary(binary_results)

    y_test_binary = binary_target(test_df)
    if isinstance(binary_best.estimator, RuleBasedBinaryDetector):
        binary_best.test_predictions = binary_best.estimator.predict(test_df)
        binary_best.test_scores = binary_best.estimator.predict_proba(test_df)[:, 1]
    else:
        binary_best.test_predictions = binary_best.estimator.predict(test_x)
        binary_best.test_scores = predict_scores(binary_best.estimator, test_x)
    binary_best.test_metrics = binary_metrics(y_test_binary, binary_best.test_predictions, binary_best.test_scores)

    anomaly_labels = sorted({label for label in pd.concat([train_df["label"], val_df["label"], test_df["label"]]).unique() if label != NORMAL_LABEL})
    anomaly_train_mask = train_df["label"] != NORMAL_LABEL
    anomaly_val_mask = val_df["label"] != NORMAL_LABEL
    anomaly_test_mask = test_df["label"] != NORMAL_LABEL
    anomaly_train_df = train_df.loc[anomaly_train_mask].reset_index(drop=True)
    anomaly_val_df = val_df.loc[anomaly_val_mask].reset_index(drop=True)
    anomaly_test_df = test_df.loc[anomaly_test_mask].reset_index(drop=True)
    anomaly_train_x = train_x.loc[anomaly_train_mask].reset_index(drop=True)
    anomaly_val_x = val_x.loc[anomaly_val_mask].reset_index(drop=True)
    anomaly_test_x = test_x.loc[anomaly_test_mask].reset_index(drop=True)
    anomaly_y_train = anomaly_train_df["label"]
    anomaly_y_val = anomaly_val_df["label"]
    anomaly_y_test = anomaly_test_df["label"]

    multiclass_results = fit_multiclass_candidates(
        anomaly_train_x,
        anomaly_val_x,
        anomaly_y_train,
        anomaly_y_val,
        anomaly_labels,
        args.random_seed,
    )
    multiclass_best = choose_best_multiclass(multiclass_results)
    multiclass_best.test_predictions = multiclass_best.estimator.predict(anomaly_test_x)
    multiclass_best.test_scores = predict_multiclass_scores(multiclass_best.estimator, anomaly_test_x)
    multiclass_best.test_metrics = multiclass_metrics(anomaly_y_test, multiclass_best.test_predictions, anomaly_labels)

    binary_confusion = confusion_matrix(y_test_binary, binary_best.test_predictions, labels=[0, 1])
    multiclass_confusion = confusion_matrix(anomaly_y_test, multiclass_best.test_predictions, labels=anomaly_labels)

    binary_metrics_payload = results_to_payload(binary_results, binary_best.name)
    multiclass_metrics_payload = results_to_payload(multiclass_results, multiclass_best.name)
    binary_metrics_payload["test_best"] = binary_best.test_metrics
    multiclass_metrics_payload["test_best"] = multiclass_best.test_metrics

    write_json(results_dir / "binary_metrics.json", binary_metrics_payload)
    write_json(results_dir / "multiclass_metrics.json", multiclass_metrics_payload)
    write_confusion_matrix(results_dir / "binary_confusion_matrix.csv", binary_confusion, [NORMAL_LABEL, ANOMALY_LABEL])
    write_confusion_matrix(results_dir / "multiclass_confusion_matrix.csv", multiclass_confusion, anomaly_labels)
    write_binary_predictions(
        results_dir / "binary_predictions_val.csv",
        "val",
        val_df,
        binary_target(val_df),
        binary_best.val_predictions,
        binary_best.val_scores,
    )
    write_binary_predictions(
        results_dir / "binary_predictions_test.csv",
        "test",
        test_df,
        y_test_binary,
        binary_best.test_predictions,
        binary_best.test_scores,
    )
    write_multiclass_predictions(
        results_dir / "multiclass_predictions_val.csv",
        "val",
        anomaly_val_df,
        multiclass_best.val_predictions,
        multiclass_best.val_scores,
        anomaly_labels,
    )
    write_multiclass_predictions(
        results_dir / "multiclass_predictions_test.csv",
        "test",
        anomaly_test_df,
        multiclass_best.test_predictions,
        multiclass_best.test_scores,
        anomaly_labels,
    )

    feature_columns_payload = {
        "excluded_columns": sorted(EXCLUDED_COLUMNS),
        "raw_feature_columns": raw_feature_columns,
        "dropped_constant_columns": constant_columns,
        "final_feature_columns": final_feature_columns,
    }
    write_json(results_dir / "feature_columns.json", feature_columns_payload)

    run_manifest = {
        "run_id": run_id,
        "generated_at_utc": utc_now(),
        "dataset_dir": str(dataset_dir),
        "results_dir": str(results_dir),
        "artifacts_dir": str(run_artifacts_dir),
        "random_seed": args.random_seed,
        "binary_best_model": binary_best.name,
        "multiclass_best_model": multiclass_best.name,
        "binary_best_config": binary_best.config,
        "multiclass_best_config": multiclass_best.config,
        "binary_label_definition": {"normal": 0, "anomaly": 1},
        "multiclass_labels": anomaly_labels,
        "train_rows": int(len(train_df)),
        "val_rows": int(len(val_df)),
        "test_rows": int(len(test_df)),
        "train_label_distribution": dict(sorted(Counter(train_df["label"]).items())),
        "val_label_distribution": dict(sorted(Counter(val_df["label"]).items())),
        "test_label_distribution": dict(sorted(Counter(test_df["label"]).items())),
        "feature_columns": feature_columns_payload,
    }
    write_json(results_dir / "run_manifest.json", run_manifest)

    summarise_markdown(
        results_dir / "baseline_summary.md",
        run_id,
        dataset_dir,
        final_feature_columns,
        constant_columns,
        binary_best,
        multiclass_best,
    )

    joblib.dump(
        {
            "run_id": run_id,
            "estimator": binary_best.estimator,
            "feature_columns": final_feature_columns,
            "constant_columns": constant_columns,
            "excluded_columns": sorted(EXCLUDED_COLUMNS),
            "task": "binary",
            "best_config": binary_best.config,
        },
        run_artifacts_dir / "binary_best_model.joblib",
    )
    joblib.dump(
        {
            "run_id": run_id,
            "estimator": multiclass_best.estimator,
            "feature_columns": final_feature_columns,
            "constant_columns": constant_columns,
            "excluded_columns": sorted(EXCLUDED_COLUMNS),
            "task": "multiclass",
            "labels": anomaly_labels,
            "best_config": multiclass_best.config,
        },
        run_artifacts_dir / "multiclass_best_model.joblib",
    )
    write_json(run_artifacts_dir / "binary_candidate_metrics.json", binary_metrics_payload)
    write_json(run_artifacts_dir / "multiclass_candidate_metrics.json", multiclass_metrics_payload)

    print(f"wrote results to {results_dir}")
    print(f"wrote artifacts to {run_artifacts_dir}")
    print(f"binary best: {binary_best.name}")
    print(f"multiclass best: {multiclass_best.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
