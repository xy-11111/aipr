# Phase 4 Baseline 设计文档

## 1. 目标

`Phase 4` 的目标是基于已经冻结的 [datasets/phase3_v1](/home/ubuntu/wjy/ebpf_nodeport/datasets/phase3_v1:1)，建立一套可重复、可比较、可直接写入论文实验节的 baseline 训练与评估链路。

本阶段不追求最强模型，而是先回答两个问题：

1. 仅靠窗口级 telemetry，`normal vs anomaly` 能做到什么程度
2. 在只看异常窗口时，6 类异常之间能区分到什么程度

## 2. 固定输入与任务定义

### 2.1 数据集输入

本阶段只读取以下三个 split 文件：

- [windows_train.csv](/home/ubuntu/wjy/ebpf_nodeport/datasets/phase3_v1/windows_train.csv:1)
- [windows_val.csv](/home/ubuntu/wjy/ebpf_nodeport/datasets/phase3_v1/windows_val.csv:1)
- [windows_test.csv](/home/ubuntu/wjy/ebpf_nodeport/datasets/phase3_v1/windows_test.csv:1)

不再回刷 `phase3_v1`，也不引入新的实验轮次。

### 2.2 主任务：二分类

- `normal -> 0`
- `label != normal -> 1`

二分类是本阶段主结果，后续模型选择和论文主叙事都优先看这条线。

### 2.3 副任务：多分类

多分类只在异常窗口上训练和评估，标签空间固定为当前 6 类异常：

- `backend_churn`
- `conntrack_pressure`
- `control_plane_recovery`
- `load_surge`
- `path_degradation`
- `service_reconcile`

`normal` 不进入多分类训练集。

## 3. 特征与预处理

### 3.1 显式排除列

以下字段不允许进入训练特征：

- 标签泄漏列：
  - `label`
  - `label_source`
  - `anomaly_active`
  - `recovery_active`
- split / 离线派生列：
  - `split`
  - `experiment_type`
  - `is_baseline_experiment`
  - `event_count`
- 标识列：
  - `experiment_id`
  - `node_name`
  - `node_ip`
  - `service_namespace`
  - `service_name`
  - `service_nodeport`
  - `source_artifact_dir`
- 结果背景列：
  - `traffic_probe_total`
  - `traffic_probe_success`
  - `traffic_probe_success_rate`
- 时间定位列：
  - `window_start_unix_ms`
  - `window_end_unix_ms`

### 3.2 预处理规则

训练脚本对其余特征统一做：

1. 数值化
2. 对非数值类别做 one-hot
3. 缺失值补 `0`
4. 仅基于训练集剔除常量列
5. 线性模型使用标准化，树模型不标准化

当前 `routing_mode` 只有 `encap`，它会在 one-hot 后自然变成常量列并被剔除。

## 4. 基线模型集合

### 4.1 二分类

固定训练 4 类 baseline：

1. `DummyClassifier(strategy="prior")`
2. `RuleBasedBinaryDetector`
3. `LogisticRegression`
4. `RandomForestClassifier`

### 4.2 多分类

固定训练 3 类 baseline：

1. `DummyClassifier(strategy="prior")`
2. `LogisticRegression`
3. `RandomForestClassifier`

### 4.3 规则基线

规则基线使用四组信号：

1. 控制面事件位：
   - `service_event_seen`
   - `slice_event_seen`
   - `node_event_seen`
2. `backend_total_delta != 0`
3. 错误计数：
   - `delta_map_miss`
   - `delta_rewrite_fail`
   - `delta_redirect_fail`
   - `delta_fallback_pass`
4. 压力阈值：
   - `ct_active_count`
   - `delta_new_conn`
   - `delta_ct_lookup_miss`
   - `delta_tcp_packets`

其中压力阈值统一取训练集 `normal` 窗口的 `P95`。

### 4.4 参数搜索

`LogisticRegression`：

- `C in {0.1, 1.0, 10.0}`
- `class_weight in {None, "balanced"}`

`RandomForestClassifier`：

- `n_estimators in {100, 300}`
- `max_depth in {None, 8, 16}`
- `min_samples_leaf in {1, 5}`
- `class_weight in {None, "balanced"}`

## 5. 模型选择与评估

### 5.1 模型选择

- 二分类：按 `val anomaly F1` 选最佳，平手看 `PR-AUC`
- 多分类：按 `val macro-F1` 选最佳，平手看 `balanced accuracy`

本阶段固定采用：

- 候选模型全部只在 `train` 上拟合
- 用 `val` 做选择
- 用同一已选模型直接评估 `test`

也就是说，本轮不做“选完超参后再用 `train+val` 重训”。

### 5.2 二分类指标

至少输出：

- `accuracy`
- `balanced_accuracy`
- `anomaly_precision`
- `anomaly_recall`
- `anomaly_f1`
- `PR-AUC`
- `ROC-AUC`

### 5.3 多分类指标

至少输出：

- `accuracy`
- `balanced_accuracy`
- `macro_precision`
- `macro_recall`
- `macro_f1`
- `weighted_f1`
- 每类 `precision / recall / f1 / support`

## 6. 环境与目录

### 6.1 Python 环境

本阶段固定使用仓库内独立环境：

- `.venv-phase4`

准备脚本为：

- [phase4_prepare_venv.sh](/home/ubuntu/wjy/ebpf_nodeport/scripts/phase4_prepare_venv.sh:1)

依赖文件为：

- [requirements-phase4.txt](/home/ubuntu/wjy/ebpf_nodeport/requirements-phase4.txt:1)

### 6.2 训练脚本

主脚本为：

- [phase4_train_baselines.py](/home/ubuntu/wjy/ebpf_nodeport/scripts/phase4_train_baselines.py:1)

默认参数：

- `--dataset-dir=datasets/phase3_v1`
- `--results-dir=results/phase4_baselines_v1`
- `--artifacts-dir=artifacts/phase4_baselines_v1`
- `--random-seed=42`

### 6.3 结果目录

固定输出到：

- `results/phase4_baselines_v1/`

至少包含：

- `binary_metrics.json`
- `multiclass_metrics.json`
- `binary_confusion_matrix.csv`
- `multiclass_confusion_matrix.csv`
- `binary_predictions_val.csv`
- `binary_predictions_test.csv`
- `multiclass_predictions_val.csv`
- `multiclass_predictions_test.csv`
- `feature_columns.json`
- `run_manifest.json`
- `baseline_summary.md`

运行期模型与明细写到：

- `artifacts/phase4_baselines_v1/<run_id>/`

## 7. 验收标准

只有同时满足以下条件，才视为 `Phase 4 Round 01` 通过：

1. `.venv-phase4` 成功建立
2. baseline 训练脚本可以完整跑完二分类与多分类
3. 固定结果文件全部产出
4. 预测文件行数与输入 split 匹配
5. 被排除的泄漏列未进入训练特征
6. 所有指标落在合法范围内
7. `baseline_summary.md` 可直接支撑论文实验节摘要

## 8. 下一步

`Phase 4 Round 01` 跑通后，下一步顺序建议为：

1. 写 `aipr_phase4_test_record_round01.md`
2. 整理二分类与多分类的主要表格
3. 再决定是否进入更强模型或序列模型阶段
