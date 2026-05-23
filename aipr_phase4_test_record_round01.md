# Phase 4 Round 01 Baseline 测试记录

## 1. 记录概览

- 测试时间：2026-05-23 12:57 UTC - 13:49 UTC
- 记录目的：
  - 建立 `Phase 4` baseline 训练环境
  - 基于固定的 `phase3_v1` 运行二分类与多分类 baseline
  - 产出可直接用于论文实验节的第一版结果
- 结论：**Round 01 通过**

## 2. 本轮新增内容

### 2.1 代码与配置

本轮新增或更新了以下内容：

- [requirements-phase4.txt](/home/ubuntu/wjy/ebpf_nodeport/requirements-phase4.txt:1)
- [phase4_prepare_venv.sh](/home/ubuntu/wjy/ebpf_nodeport/scripts/phase4_prepare_venv.sh:1)
- [phase4_train_baselines.py](/home/ubuntu/wjy/ebpf_nodeport/scripts/phase4_train_baselines.py:1)
- [aipr_phase4_baseline_design.md](/home/ubuntu/wjy/ebpf_nodeport/aipr_phase4_baseline_design.md:1)

同时更新了 [.gitignore](/home/ubuntu/wjy/ebpf_nodeport/.gitignore:1)，忽略：

- `.venv-phase4/`
- `artifacts/phase4_baselines_v1/`

### 2.2 环境实现注记

当前宿主机缺少 `ensurepip`，直接 `python3 -m venv` 后没有可用的 `pip`。  
因此本轮实际采用的仓库内环境方式是：

1. 先建立 `.venv-phase4`
2. 再通过系统 `python3 -m pip --target` 把依赖装入 `.venv-phase4/lib/python3.10/site-packages`

这样仍然满足“仓库内独立环境”的目标，同时避免依赖系统级安装权限。

## 3. 执行命令

本轮关键命令如下：

```bash
bash -n scripts/phase4_prepare_venv.sh
python3 -m py_compile scripts/phase4_train_baselines.py
bash ./scripts/phase4_prepare_venv.sh
.venv-phase4/bin/python scripts/phase4_train_baselines.py
```

环境导入检查结果：

- `numpy 1.26.4`
- `pandas 2.2.2`
- `scikit-learn 1.5.1`
- `joblib 1.4.2`

## 4. 输入数据与输出目录

### 4.1 固定输入

本轮只使用：

- [windows_train.csv](/home/ubuntu/wjy/ebpf_nodeport/datasets/phase3_v1/windows_train.csv:1)
- [windows_val.csv](/home/ubuntu/wjy/ebpf_nodeport/datasets/phase3_v1/windows_val.csv:1)
- [windows_test.csv](/home/ubuntu/wjy/ebpf_nodeport/datasets/phase3_v1/windows_test.csv:1)

### 4.2 结果输出

主结果目录：

- [results/phase4_baselines_v1](/home/ubuntu/wjy/ebpf_nodeport/results/phase4_baselines_v1)

运行期 artifact：

- [phase4-baselines-20260523T134916Z](/home/ubuntu/wjy/ebpf_nodeport/artifacts/phase4_baselines_v1/phase4-baselines-20260523T134916Z)

## 5. 结果摘要

### 5.1 特征处理

- 原始候选特征列：`37`
- 常量列：`10`
- 最终特征列：`27`

被剔除的常量列包括：

- `window_seconds`
- `delta_backend_lookup_miss`
- `delta_fwd_ct_hit`
- `delta_new_conn`
- `delta_map_miss`
- `delta_rewrite_fail`
- `delta_redirect_ok`
- `delta_redirect_fail`
- `delta_fallback_pass`
- `routing_mode_encap`

### 5.2 二分类最佳模型

- 最佳模型：`rf_n100_dnone_leaf5_cwnone`
- 验证集：
  - `anomaly F1 = 0.7349`
  - `PR-AUC = 0.8277`
- 测试集：
  - `accuracy = 0.8684`
  - `balanced_accuracy = 0.8401`
  - `anomaly_precision = 0.7852`
  - `anomaly_recall = 0.7697`
  - `anomaly_f1 = 0.7774`
  - `PR-AUC = 0.8449`
  - `ROC-AUC = 0.8656`

### 5.3 多分类最佳模型

- 最佳模型：`rf_n100_dnone_leaf5_cwbalanced`
- 验证集：
  - `macro-F1 = 0.3722`
  - `balanced_accuracy = 0.3744`
- 测试集：
  - `accuracy = 0.5000`
  - `balanced_accuracy = 0.4159`
  - `macro-F1 = 0.4181`
  - `weighted-F1 = 0.5014`

### 5.4 多分类测试集按类表现

- `backend_churn`：`f1 = 0.0000`
- `conntrack_pressure`：`f1 = 0.6292`
- `control_plane_recovery`：`f1 = 0.6250`
- `load_surge`：`f1 = 0.4468`
- `path_degradation`：`f1 = 0.5217`
- `service_reconcile`：`f1 = 0.2857`

## 6. 产物检查

### 6.1 固定结果文件

以下文件均已生成：

- [binary_metrics.json](/home/ubuntu/wjy/ebpf_nodeport/results/phase4_baselines_v1/binary_metrics.json:1)
- [multiclass_metrics.json](/home/ubuntu/wjy/ebpf_nodeport/results/phase4_baselines_v1/multiclass_metrics.json:1)
- [binary_confusion_matrix.csv](/home/ubuntu/wjy/ebpf_nodeport/results/phase4_baselines_v1/binary_confusion_matrix.csv:1)
- [multiclass_confusion_matrix.csv](/home/ubuntu/wjy/ebpf_nodeport/results/phase4_baselines_v1/multiclass_confusion_matrix.csv:1)
- [binary_predictions_val.csv](/home/ubuntu/wjy/ebpf_nodeport/results/phase4_baselines_v1/binary_predictions_val.csv:1)
- [binary_predictions_test.csv](/home/ubuntu/wjy/ebpf_nodeport/results/phase4_baselines_v1/binary_predictions_test.csv:1)
- [multiclass_predictions_val.csv](/home/ubuntu/wjy/ebpf_nodeport/results/phase4_baselines_v1/multiclass_predictions_val.csv:1)
- [multiclass_predictions_test.csv](/home/ubuntu/wjy/ebpf_nodeport/results/phase4_baselines_v1/multiclass_predictions_test.csv:1)
- [feature_columns.json](/home/ubuntu/wjy/ebpf_nodeport/results/phase4_baselines_v1/feature_columns.json:1)
- [run_manifest.json](/home/ubuntu/wjy/ebpf_nodeport/results/phase4_baselines_v1/run_manifest.json:1)
- [baseline_summary.md](/home/ubuntu/wjy/ebpf_nodeport/results/phase4_baselines_v1/baseline_summary.md:1)

### 6.2 预测文件行数

- `binary_predictions_val.csv`：`400`
- `binary_predictions_test.csv`：`509`
- `multiclass_predictions_val.csv`：`111`
- `multiclass_predictions_test.csv`：`152`

这些行数与输入 split 规模一致：

- 二分类覆盖全部窗口
- 多分类只覆盖异常窗口

## 7. 关键观察

1. 二分类结果已经比较像样，`test anomaly F1=0.7774`，说明当前窗口级 telemetry 对“是否异常”判断已经有现实可用性。
2. 多分类明显更难，特别是 `backend_churn` 在测试集上完全没有打中，这意味着：
   - 仅靠当前窗口级特征，`backend_churn` 和其他异常的可分性偏弱
   - 后续可能需要加入更强的时间上下文或实验级聚合特征
3. `conntrack_pressure` 和 `control_plane_recovery` 的多分类表现相对最好，说明这两类异常在当前特征空间里更有独立模式。

## 8. 判定

本轮满足：

1. `.venv-phase4` 成功建立
2. 训练脚本完整跑通二分类与多分类
3. 固定结果文件全部生成
4. 预测文件行数正确
5. 被排除的泄漏列未进入训练特征
6. 指标均在合法范围内

因此：

**`Phase 4 Round 01` 通过。**

## 9. 下一步

下一步建议按这个顺序走：

1. 将当前仓库整体推送到 `git@github.com:xy-11111/aipr.git`
2. 基于 `baseline_summary.md` 整理论文实验节的第一版表格
3. 再决定是否进入更强模型或序列模型阶段
