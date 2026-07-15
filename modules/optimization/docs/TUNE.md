# TUNE: rubrics to optimizer to RFT

TUNE starts after `docs\OBSERVE.md` has produced a reviewed Foundry
rubric/eval, baseline output-items, and a calibrated deterministic grader. It
captures the generic optimizer-to-RFT path for improving a Forge hosted agent
and then transferring the optimized behavior to a cheaper RFT model.

## What this covers

1. Check Forge optimizer readiness from Caliber.
2. Review Agent Optimizer jobs and candidates.
3. Apply a selected candidate locally in Forge after review.
4. Deploy the reviewed optimized hosted agent.
5. Re-run the same Foundry eval.
6. Export optimized output-items and recalibrate the deterministic grader.
7. Package RFT assets for `o4-mini`.
8. Submit live RFT only after explicit spend approval.

## Inputs

Set these values for the environment you are working in:

```powershell
$Caliber = "<path-to-caliber>"
$Forge = "<path-to-forge>"
$ProjectEndpoint = "<foundry-project-endpoint>"
$EvalModel = "<deployed-eval-model>"
$OptimizationModel = "<deployed-optimization-model>"
$CheapBaseModel = "o4-mini"
$Agent = "contract-policy-expert"
$DatasetDir = "$Caliber\datasets\$Agent"
$RunDir = "$Caliber\runs\eval-results\$Agent"
$OptimizerRunDir = "$Caliber\runs\optimizer\$Agent"
$RftDir = "$Caliber\runs\rft\$Agent-expanded"
```

Keep optimizer snapshots, eval run outputs, RFT package artifacts, uploaded-file
metadata, and live RFT job state under ignored `runs\` storage. Do not commit
tenant-specific endpoints, local worktree paths, live eval/run IDs, optimizer
operation IDs, candidate IDs, uploaded-file IDs, or fine-tuning job IDs.

## Optimizer target

The accepted optimizer candidate becomes the gold quality target for RFT. The
demo story should show a visible hill climb:

```text
weak baseline -> candidate improvements -> accepted peak candidate
```

For `contract-policy-expert`, the desired improvement pattern is:

- Start with a realistic weak baseline that has enough failure signal.
- Optimize instructions and procedural skills first.
- Prefer a clean persisted optimizer job over recovered/failed persistence
  artifacts for public demo lineage.
- Use the accepted `gpt-5.5` hosted-agent candidate as the quality target.
- Use RFT as the cost-transfer step that attempts to preserve optimized
  evidence-contract behavior on `o4-mini`.

The dominant failure mode to optimize and train against is schema/evidence drift:
ad hoc JSON, missing source references, missing classifications, unsupported
claims, or prose outside the final evidence contract.

## Step 1 - Check optimizer readiness

```powershell
Set-Location $Caliber
uv run caliber optimizer plan `
  --forge-path $Forge `
  --agent $Agent `
  --dataset "$DatasetDir\$Agent-eval.jsonl" `
  --eval-config "$RunDir\$Agent-foundryiq-calibration.eval.yaml" `
  --json
```

Expected result: Forge is optimizer-ready for `contract-policy-expert`.

Required Forge surfaces:

```text
agents\contract-policy-expert\main.py
agents\contract-policy-expert\.agent_configs\baseline\metadata.yaml
agents\contract-policy-expert\.agent_configs\baseline\instructions.md
agents\contract-policy-expert\.agent_configs\baseline\skills\retrieval-discipline\SKILL.md
agents\contract-policy-expert\.agent_configs\baseline\skills\read-only-evidence-contract\SKILL.md
```

If model or tool-description optimization is in scope, Forge also needs
optimizer-visible model and tool configuration wired through runtime code. Keep
that as a separate reviewed implementation decision; do not infer local tool
wiring from recovered candidate JSON unless the selected mutation actually
changed tool metadata.

## Step 2 - Run or review optimizer candidates

Use the Foundry Agent Optimizer flow from the Forge agent root:

```powershell
Set-Location "$Forge\agents\$Agent"
azd ai agent optimize status <optimizer-operation-id> --watch
```

Review:

- Baseline score and pass rate
- Candidate scores
- Mutation surfaces, such as `system_prompt`, `skills`, `tools`, or `model`
- Whether the optimizer job persisted cleanly
- Candidate config and result artifacts
- Payload-size risks, especially result persistence failures such as Cosmos DB
  item-size limits

For a demo path, prefer a clean `succeeded` optimizer job with a clear hill climb
over a higher-risk failed persistence artifact. If a failed job produced
recoverable candidates, keep those snapshots under ignored `runs\optimizer\`.

## Step 3 - Show the hill climb

The demo story should make the hill-climbing metaphor explicit: Agent Optimizer
starts from a real but under-specified hosted agent, tries candidates, and
selects the highest-quality candidate before RFT starts.

Use a table like this in local notes or slides:

| Stage | Candidate | Score | Pass rate | Demo meaning |
|---|---|---:|---:|---|
| Start | `<baseline-candidate-id>` | `<score>` | `<pass-rate>` | Real weak baseline with enough failure signal |
| Climb 1 | `<candidate-id>` | `<score>` | `<pass-rate>` | First major optimizer jump |
| Climb 2 | `<candidate-id>` | `<score>` | `<pass-rate>` | Higher candidate after search |
| Explore | `<candidate-id>` | `<score>` | `<pass-rate>` | Slight regression, useful to show search is not linear |
| Peak | `<accepted-candidate-id>` | `<score>` | `<pass-rate>` | Optimized `gpt-5.5` quality target |

RFT only starts after the peak candidate is reviewed and accepted.

## Step 4 - Apply selected candidate locally

```powershell
Set-Location "$Forge\agents\$Agent"
azd ai agent optimize apply --candidate <accepted-candidate-id>
git -C $Forge diff -- agents\contract-policy-expert
```

Gate: apply only the selected candidate. If `azd ai agent optimize apply` cannot
resolve the candidate locally, recover the selected candidate config through the
documented Foundry data-plane route and manually apply only the reviewed mutation
surfaces. Stop before deploy if the Forge diff does not match the accepted
candidate delta.

When recovering manually, verify:

- Source/runtime agent version from the optimizer job
- Candidate mutation keys
- Local files changed
- Whether tool/model metadata actually changed
- Normalized file contents against recovered candidate fields

Do not deploy until the source diff is reviewed.

## Step 5 - Deploy reviewed optimized Forge agent

```powershell
Set-Location $Forge
azd deploy $Agent --no-prompt
azd ai agent show $Agent -e <azd-env-name> --no-prompt -o json
```

Record the active deployed version, model, endpoint availability, and image
digest in ignored run notes or durable process docs only when they are safe to
share. Do not commit tenant-specific values.

## Step 6 - Run post-optimizer eval

```powershell
Set-Location $Forge
azd ai agent eval run `
  --config "$RunDir\$Agent-foundryiq-calibration.eval.yaml" `
  --name "$Agent-foundryiq-optimized-run" `
  --no-wait `
  --no-prompt `
  -o json
```

```powershell
$EvalId = "<eval-id>"
$OptimizedRunId = "<optimized-eval-run-id>"
```

Use the same Foundry rubric/eval as the baseline run so the optimized result is
comparable.

## Step 7 - Export optimized output-items

```powershell
Set-Location $Caliber
uv run caliber eval export-output-items `
  --project-endpoint $ProjectEndpoint `
  --eval-id $EvalId `
  --run-id $OptimizedRunId `
  --out "$RunDir\output-items-optimized.jsonl" `
  --json
```

Expected ignored output:

```text
runs\eval-results\contract-policy-expert\output-items-optimized.jsonl
```

## Step 8 - Recalibrate deterministic grader

```powershell
uv run caliber grader calibrate `
  --dataset "$DatasetDir\$Agent-eval.jsonl" `
  --outputs "$RunDir\output-items-optimized.jsonl" `
  --grader "$Caliber\graders\$Agent\contract_policy_evidence_grader.py" `
  --json
```

Use the optimized output-items to ensure the deterministic grader rewards the
same behavior that the Foundry rubric accepted. Inspect failed rows before live
RFT submission.

## Step 9 - Plan RFT for cost transfer

RFT starts only after the hill climb is accepted. The training goal is not
"beat the optimizer"; it is "keep the optimized evidence-contract behavior while
moving inference from `gpt-5.5` to `o4-mini`."

Microsoft's RFT documentation lists `o4-mini` version `2025-04-16` as the GA RFT
base model. A normal serving deployment of `o4-mini` is not required before RFT
submission; treat RFT model support and explicit live-spend approval as the
gating conditions.

```powershell
uv run caliber rft plan `
  --train "$DatasetDir\$Agent-train.jsonl" `
  --validation "$DatasetDir\$Agent-validation.jsonl" `
  --grader "$Caliber\graders\$Agent\contract_policy_evidence_grader.py" `
  --base-model $CheapBaseModel `
  --project-endpoint $ProjectEndpoint `
  --suffix "$Agent-cost-expanded" `
  --json
```

## Step 10 - Package expanded RFT assets

```powershell
uv run caliber rft package `
  --train "$RftDir\$Agent-rft-source-combined-train.jsonl" `
  --validation "$RftDir\$Agent-rft-source-combined-validation.jsonl" `
  --grader "$Caliber\graders\$Agent\contract_policy_evidence_grader.py" `
  --agent $Agent `
  --base-model $CheapBaseModel `
  --suffix "$Agent-cost-expanded" `
  --out-dir $RftDir `
  --optimizer-job-id <optimizer-operation-id> `
  --optimizer-candidate-id <accepted-candidate-id> `
  --json
```

Expected ignored artifacts:

```text
runs\rft\contract-policy-expert-expanded\contract-policy-expert-rft-train.jsonl
runs\rft\contract-policy-expert-expanded\contract-policy-expert-rft-validation.jsonl
runs\rft\contract-policy-expert-expanded\contract-policy-expert-rft-grader.py
runs\rft\contract-policy-expert-expanded\contract-policy-expert-rft-job.dry-run.json
runs\rft\contract-policy-expert-expanded\manifest.json
```

The generated RFT train/validation JSONL files include a UTF-8 BOM for Foundry
fine-tuning upload compatibility.

## Step 11 - Inspect RFT package gate

```powershell
uv run caliber rft status --state "$RftDir\manifest.json" --json
```

Expected gate shape:

```json
{
  "ready_for_live_submit": false,
  "blocked_until": [
    "optimizer candidate is selected as the gold quality target",
    "gold candidate outputs are calibrated with the RFT grader",
    "RFT base model support is verified",
    "live RFT spend is explicitly approved"
  ]
}
```

## Step 12 - Submit live RFT

Caliber currently packages and tracks RFT artifacts; live submission may use the
Foundry SDK/script path until a first-class `caliber rft submit` command exists.

Before submitting:

1. Confirm the optimized `gpt-5.5` quality target is accepted.
2. Confirm `o4-mini` RFT support.
3. Confirm live spend approval.
4. Keep uploaded-file IDs, fine-tuning job IDs, and job state under ignored
   `runs\` storage.

Monitor the job with bounded status checks. Do not use an unbounded polling loop.

## Output contract for TUNE

TUNE is complete when:

1. A selected optimizer candidate is reviewed in Forge source.
2. The optimized hosted agent is deployed.
3. The same Foundry rubric/eval has been rerun.
4. Optimized output-items are exported.
5. The deterministic grader is recalibrated.
6. The RFT package exists under ignored `runs\rft\contract-policy-expert-expanded`.
7. If live RFT is submitted, the job ID and uploaded-file IDs are stored only in
   ignored run state.

## RFT golden path economics

Use economics as a promotion gate, not a slide-only claim. The expected shape is:

| Model path | Purpose | Approx cost shape |
|---|---|---|
| Optimized `gpt-5.5` hosted agent | Quality target after hill climb | Highest inference cost |
| RFT `o4-mini` candidate | Cost-transfer candidate | Lower token economics if output length stays similar |

Promote the RFT model only if it stays within the accepted quality band of the
optimized `gpt-5.5` candidate and materially lowers measured eval cost.
