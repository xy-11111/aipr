# Phase 4 Test Record Round 02

## Summary

本轮不进行任何模型重训练，只将既有 `Phase 4` baseline 结果固化为论文可直接复用的表格、图片和源数据副本。

## Test Time

- Date: `2026-05-23`
- Workspace: `/home/ubuntu/wjy/ebpf_nodeport`

## Source Inputs

- Source results dir: `results/phase4_baselines_v1`
- Source run id: `phase4-baselines-20260523T134916Z`
- Export script: `scripts/phase4_export_paper_assets.py`

## Commands

```bash
python3 -m py_compile scripts/phase4_export_paper_assets.py
./.venv-phase4/bin/python scripts/phase4_export_paper_assets.py
```

## Generated Outputs

### Tables

- `paper_assets/phase4_v1/tables/table_phase4_binary_main.csv`
- `paper_assets/phase4_v1/tables/table_phase4_binary_main.tex`
- `paper_assets/phase4_v1/tables/table_phase4_multiclass_main.csv`
- `paper_assets/phase4_v1/tables/table_phase4_multiclass_main.tex`
- `paper_assets/phase4_v1/tables/table_phase4_multiclass_per_class.csv`
- `paper_assets/phase4_v1/tables/table_phase4_multiclass_per_class.tex`

### Figures

- `paper_assets/phase4_v1/figures/fig_phase4_binary_confusion.png`
- `paper_assets/phase4_v1/figures/fig_phase4_multiclass_confusion.png`
- `paper_assets/phase4_v1/figures/fig_phase4_per_class_f1.png`

### Source Copies

- `paper_assets/phase4_v1/source/binary_metrics.json`
- `paper_assets/phase4_v1/source/multiclass_metrics.json`
- `paper_assets/phase4_v1/source/binary_confusion_matrix.csv`
- `paper_assets/phase4_v1/source/multiclass_confusion_matrix.csv`
- `paper_assets/phase4_v1/source/binary_predictions_val.csv`
- `paper_assets/phase4_v1/source/binary_predictions_test.csv`
- `paper_assets/phase4_v1/source/multiclass_predictions_val.csv`
- `paper_assets/phase4_v1/source/multiclass_predictions_test.csv`
- `paper_assets/phase4_v1/source/feature_columns.json`
- `paper_assets/phase4_v1/source/run_manifest.json`
- `paper_assets/phase4_v1/source/baseline_summary.md`

## Results

- Export script completed successfully.
- No retraining was performed.
- All planned `CSV`, `TeX`, and `PNG` outputs were generated.
- `paper_assets/phase4_v1/README.md` was generated and records the source run id and export assumptions.

## Notes

- The existing `Phase 4` result bundle only contains held-out test metrics for the single best model selected in the original run.
- The export step therefore keeps validation metrics for all family-best rows, while non-best families leave test columns empty instead of inventing missing values.
- `matplotlib` was installed into `.venv-phase4` before running the export so the figures could be rendered locally.

## Exit Criteria

- `Phase 4` paper assets frozen: `passed`
- Ready to begin `Phase 5` 2x2 overhead evaluation: `passed`
