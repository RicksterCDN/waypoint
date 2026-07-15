# Optimization

This module owns the improvement workflows for a measured Waypoint agent system after the baseline evaluations are known.

It owns the public-facing optimization story: prompt optimization, RFT/RLE planning, cost-quality comparisons, promotion metadata, and telemetry backfill planning. The compatibility CLI still lives in `modules/evals` during the first migration pass so existing `caliber ...` commands continue to work while the public structure becomes easier to learn.

Moved optimization artifacts:

- `docs/TUNE.md`
- `docs/CONTRACT_POLICY_EXPERT_RFT_ARTIFACTS.md`
- `docs/FOUNDRY_TRACES_AND_FINE_TUNING.md`
- `extensions/caldova-rft-process`
- `extensions/optimizer-diff-canvas`
- `extensions/rft-cost-quality-canvas`
