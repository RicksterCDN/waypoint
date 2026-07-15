# Caliber

Caliber is the fine-tuning support project for selected agents in
`caldova/waypoint`. It owns the repeatable tooling around datasets, graders,
evals, and Foundry fine-tuning jobs while treating related repositories as
external inputs.

## Reference sources

- `caldova/waypoint` is the target agent repository. Caliber reads agent
  definitions and eval assets from a Forge checkout, but does not modify Forge.
- `caldova/waypoint` is the reference pattern for canonical data,
  generated artifacts, and agent-facing manifest/doctor surfaces.
- The reinforcement-learning reference implementation is the pattern for
  Deploy -> Observe -> Learn workflows, including JSONL datasets, graders,
  eval harnesses, and Foundry RFT job lifecycle scripts.

## Setup

```powershell
uv sync
uv run caliber doctor
uv run caliber manifest
uv run caliber datasets contract-policy build --ledgerfield-path C:\path\to\ledgerfield --agent contract-policy-expert --variants-per-scenario 8
uv run caliber datasets contract-policy expand-contracts --ledgerfield-path C:\path\to\ledgerfield --agent contract-policy-expert --variants-per-clause 3
uv run caliber eval plan --dataset datasets\smoke.jsonl --grader graders\caliber_default.py
uv run caliber rle plan --agent contract-policy-expert --environment dev --train datasets\smoke.jsonl --validation datasets\smoke.jsonl --eval datasets\smoke.jsonl --grader graders\caliber_default.py --plain-reinforcement
```

Inspect a local Forge checkout without modifying it:

```powershell
uv run caliber inspect-forge --path C:\path\to\forge
```

Build the first hosted FoundryIQ contract-policy datasets from a read-only
Ledgerfield checkout:

```powershell
uv run caliber datasets contract-policy build --ledgerfield-path C:\path\to\ledgerfield --agent contract-policy-expert --variants-per-scenario 8
uv run caliber datasets contract-policy expand-contracts --ledgerfield-path C:\path\to\ledgerfield --agent contract-policy-expert --variants-per-clause 3
uv run caliber optimizer plan --forge-path C:\path\to\forge --agent contract-policy-expert --dataset datasets\contract-policy-expert\contract-policy-expert-eval.jsonl --eval-config runs\eval-results\contract-policy-expert\contract-policy-expert-foundryiq-baseline.eval.yaml
uv run caliber rft plan --train datasets\contract-policy-expert\contract-policy-expert-train.jsonl --validation datasets\contract-policy-expert\contract-policy-expert-validation.jsonl --grader graders\contract-policy-expert\contract_policy_evidence_grader.py --base-model <cheap-model> --project-endpoint <foundry-project-endpoint>
uv run caliber rft package --train datasets\contract-policy-expert\contract-policy-expert-train.jsonl --validation datasets\contract-policy-expert\contract-policy-expert-validation.jsonl --grader graders\contract-policy-expert\contract_policy_evidence_grader.py --agent contract-policy-expert --base-model <cheap-model> --suffix contract-policy-expert-cost
uv run caliber rle plan --agent contract-policy-expert --environment dev --train datasets\contract-policy-expert\contract-policy-expert-train.jsonl --validation datasets\contract-policy-expert\contract-policy-expert-validation.jsonl --eval datasets\contract-policy-expert\contract-policy-expert-eval.jsonl --grader graders\contract-policy-expert\contract_policy_evidence_grader.py --plain-reinforcement
```

Generate Foundry rubric/eval assets and export output-items for grader
calibration:

```powershell
azd ai agent eval generate -C C:\path\to\forge --project-endpoint <foundry-project-endpoint> --agent contract-policy-expert --dataset datasets\contract-policy-expert\contract-policy-expert-eval.jsonl --gen-instruction-file C:\path\to\forge\agents\contract-policy-expert\prompt.md --eval-model gpt-5.5 --name contract-policy-expert-foundryiq-baseline --out-file runs\eval-results\contract-policy-expert\contract-policy-expert-foundryiq-baseline.eval.yaml --no-wait --no-prompt -o json
azd ai agent eval run -C C:\path\to\forge --config runs\eval-results\contract-policy-expert\contract-policy-expert-foundryiq-baseline.eval.yaml --name contract-policy-expert-foundryiq-baseline-run --no-wait --no-prompt -o json
uv run caliber eval export-output-items --project-endpoint <foundry-project-endpoint> --eval-id <eval-id> --run-id <run-id> --out runs\eval-results\contract-policy-expert\output-items.jsonl
uv run caliber grader calibrate --dataset datasets\contract-policy-expert\contract-policy-expert-eval.jsonl --outputs runs\eval-results\contract-policy-expert\output-items.jsonl --grader graders\contract-policy-expert\contract_policy_evidence_grader.py
```

The previous `assurance-analyst-contract-grounding-rubric-baseline` run completed
with 24 total, 4 passed, 20 failed, and 0 errored. Keep it as calibration and
roadmap context. The first real hosted FoundryIQ lane now targets
`contract-policy-expert`: generate rubrics/evals first, use Agent Optimizer to
improve accuracy, then use RFT to preserve that behavior on a cheaper model.

## Repository layout

```text
src/caliber/       Python package and CLI
datasets/          Small committed seed/eval JSONL files, when needed
docs/              Continuation notes and workflow design
graders/           Reward functions and grading helpers
runs/              Generated eval results and job metadata (ignored)
outputs/           Generated artifacts (ignored)
```

## Fine-tuning workflow shape

Caliber starts with safe, local setup commands. RFT commands validate inputs and
produce a submission plan first. `caliber rft package` then writes local
RFT-ready JSONL, a self-contained Python grader, and a dry-run job spec under
ignored `runs\rft\` storage. Real Foundry submission remains gated until the
target agent's optimizer candidate, dataset contracts, and grader behavior are
stable.

`caliber rle plan` is the offline reinforcement learning environment check. It
frames the Deploy -> Observe -> Learn loop for a target agent, validates the
train/validation/eval datasets and reward grader, reports Foundry/tool endpoint
readiness, and points generated outputs at ignored paths.

For the current hosted FoundryIQ lane, Caliber targets Forge's hosted
`contract-policy-expert` from `caldova/waypoint#24`. Ledgerfield is the
read-only source for contract, policy, invoice, and scenario evidence used to
build the seed JSONL rows. The dataset builder can expand each scenario into
deterministic prompt variants with `--variants-per-scenario`; the expected
grounded output stays unchanged while request wording changes for eval and RFT
diversity.

For larger RFT candidate sets, `caliber datasets contract-policy expand-contracts`
adds clause-grounded train, validation, eval, and Foundry eval-input JSONL from
Ledgerfield contract and policy Markdown. These rows are generated review seeds:
keep them separate until reviewed, then combine them into ignored RFT packages or
promote a curated subset.

For calibration, use Foundry eval output-items rather than exported summary
counts. Grade the final assistant message and keep tool-role messages as
retrieval trace context.

Before model fine-tuning, use Foundry-generated rubrics and baseline evals, then
`caliber optimizer plan` to check Agent Optimizer readiness for the hosted azd
service. RFT follows optimizer and is used for cost optimization: move the
optimized behavior to a cheaper model only after grader calibration and
before/after evals show the quality target is clear.

See [`docs\DEMO_FLOW.md`](docs/DEMO_FLOW.md) for the command-by-command demo
flow.

## Continue work

Start with:

- [`docs/CONTINUING_WORK.md`](docs/CONTINUING_WORK.md)
- [`docs/FOUNDRY_TRACES_AND_FINE_TUNING.md`](docs/FOUNDRY_TRACES_AND_FINE_TUNING.md)
