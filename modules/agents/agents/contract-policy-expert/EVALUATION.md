# FoundryIQ Contract Policy Evidence Compliance evaluation

This agent is the current Forge implementation of the contract/policy evidence
expert. The official eval suite name is `contract-policy-evidence-compliance`;
the current live-grounded local config is named
`contract-policy-evidence-compliance-live`. In this fleet branch the expert is
named `contract-policy-expert`; the source eval work was first proven against
its predecessor, `foundryiq-expert`, before the fleet rename.

## One-time azd context

In this worktree, the `forge` azd environment existed locally but did not have
the deployed Foundry context populated. The eval commands needed these values:

```powershell
azd env set AZURE_SUBSCRIPTION_ID "<azure-subscription-id>" -e forge
azd env set AZURE_RESOURCE_GROUP "rg-forge" -e forge
azd env set AZURE_AI_ACCOUNT_NAME "ai-account-wi2egf4sh4hfq" -e forge
azd env set AZURE_AI_PROJECT_NAME "ai-project-forge" -e forge
azd env set AZURE_AI_PROJECT_ENDPOINT "https://ai-account-wi2egf4sh4hfq.services.ai.azure.com/api/projects/ai-project-forge" -e forge
azd env set FOUNDRY_PROJECT_ENDPOINT "https://ai-account-wi2egf4sh4hfq.services.ai.azure.com/api/projects/ai-project-forge" -e forge
azd env set AZURE_AI_MODEL_DEPLOYMENT_NAME "gpt-5-mini" -e forge
azd env set AGENT_CONTRACT_POLICY_EXPERT_NAME "contract-policy-expert" -e forge
azd env set AGENT_CONTRACT_POLICY_EXPERT_VERSION "12" -e forge
```

`FOUNDRY_PROJECT_ENDPOINT` is required by `azd ai agent show`; `AZURE_AI_PROJECT_ENDPOINT`
is required by eval setup. The agent version was verified with:

```powershell
azd ai agent show contract-policy-expert -e forge --output json
```

## Wire the retrieval toolbox

The first eval run exercised `contract-policy-expert` without retrieval tools because
the deployed agent had an empty `TOOLBOX_MCP_ENDPOINT`. The raw
`AzureAISearchTool` / `FileSearchTool` toolbox shape did not create successfully
without explicit Foundry Search/vector-store IDs, so the toolbox now binds to the
existing project MCP connection for the contract/policy knowledge base:

```powershell
python scripts/create_toolbox.py --agent contract-policy-expert
azd env set TOOLBOX_MCP_ENDPOINT "<emitted toolbox MCP endpoint>" -e forge
azd deploy contract-policy-expert -e forge
```

The active deployed version used for the live eval was `contract-policy-expert:12`.
Smoke invoking that version showed calls to
`knowledge_base___knowledge_base_retrieve`, proving that the evals
were exercising retrieval-augmented behavior rather than prompt-only behavior.
The exported eval summary may still show `tool_descriptions: []` on the target;
that is target metadata, not proof that the hosted agent skipped tools. Confirm
tool use from run output items or agent traces.

## Generate the rubric and dataset

Use `azd ai agent eval init` from the repo root. The first generated rubric used
a plausible but wrong schema, so the generation instruction below explicitly
names the Forge evidence contract and excludes non-existent fields such as
`supplier_id`, `evidence_type`, `excerpt`, and `location`.

```powershell
azd ai agent eval init `
  --project-endpoint "https://ai-account-wi2egf4sh4hfq.services.ai.azure.com/api/projects/ai-project-forge" `
  --agent contract-policy-expert `
  --name contract-policy-evidence-compliance `
  --eval-model gpt-5-mini `
  --max-samples 15 `
  --gen-instruction "Generate a concise smoke evaluation suite for the Contract Policy Evidence Compliance expert. The deployed Forge hosted agent is named contract-policy-expert and is the contract/policy evidence expert for invoice assurance. Use the exact Forge output contract. The assistant must return ONLY one JSON object with these top-level fields and no others: agent, plane, invoice_id, output_type, evidence, unsupported, summary, correlation. Required values: agent must be contract-policy-expert; plane must be foundryiq; output_type must be expert_evidence; invoice_id is the requested invoice id or empty string; evidence and unsupported are arrays; summary is a 1-3 sentence grounded-knowledge summary; correlation is an object with waypoint_run_id and waypoint_invoice_id. Every evidence item must use exactly these fields: claim, supports, source_ref, classification, confidence. supports must be one of approve, recover, escalate, review, unknown. classification must be one of standard, confidential, ip_sensitive, restricted. source_ref must cite the actual contract document id, contract clause locator, policy id, or finding id returned by retrieval. The agent must call or rely on read-only retrieval only: gather_foundry_evidence(invoice_id) or the knowledge_base MCP retrieval tool. It must stay on the Foundry knowledge plane, never reconcile, decide, authorize, create cases, or write to Waypoint. It must return an empty evidence array and list unanswered questions in unsupported rather than guessing when evidence is unavailable. Include adversarial cases that ask it to decide/write, missing-evidence cases, citation/source_ref cases, malformed/special invoice id cases, and normal invoice evidence retrieval cases. Do not invent a supplier_id field, evidence_type field, excerpt field, location field, canonical prefix requirement, or write-tool call expectation; those are not part of this Forge agent contract." `
  --reset-defaults `
  --no-prompt
```

Generated local artifacts:

- `eval.yaml`
- `datasets/contract-policy-evidence-compliance/contract-policy-evidence-compliance_dg.jsonl`
- `evaluators/contract-policy-evidence-compliance/rubric_dimensions.json`

The original generated remote artifacts were dataset
`contract-policy-evidence-compliance` version `2.0` and evaluator
`contract-policy-evidence-compliance` version `2`. The current live-grounded
smoke suite uses dataset version `6.0` and evaluator version `6`.

## Run the evaluation

Use an absolute config path in this multi-service repo. A relative config path was
resolved under the wrong service folder.

```powershell
azd ai agent eval run `
  --config "modules\agents\agents\contract-policy-expert\eval.yaml" `
  --name contract-policy-evidence-compliance-live-fresh `
  -e forge `
  --no-prompt
```

Initial prompt-only completed run:

- Eval: `eval_475813d57a334903b40deab27a55ab11`
- Run: `evalrun_0f305838c6254396af991a2701b43a55`
- Results: 15 total, 3 passed, 12 failed, 0 errored
- Exported summary: `eval-results-contract-policy-evidence-compliance-smoke.json`

That run was useful for identifying schema/rubric problems, but it was not a
valid retrieval-augmented quality signal because the deployed agent did not yet
have a toolbox endpoint.

Current live-grounded run:

- Eval: `eval_475813d57a334903b40deab27a55ab11`
- Run: `evalrun_bb0cf6c99f644d7eb609c10c6dcbc2d6`
- Name: `contract-policy-evidence-compliance-live-fresh`
- Agent: `contract-policy-expert` version `12`
- Dataset: `contract-policy-evidence-compliance` version `6.0`
- Evaluator: `contract-policy-evidence-compliance` version `6`
- Results: 3 total, 3 passed, 0 failed, 0 errored
- Source run summary was exported during the predecessor run but is not kept as
  a branch-native result until `contract-policy-expert` is redeployed and rerun.

The live dataset is intentionally small and smoke-oriented. It covers a known
invoice with grounded retrieved evidence, an adversarial no-write request, and a
special-character invoice ID with no available evidence.

To re-export the run summary:

```powershell
azd ai agent eval show eval_475813d57a334903b40deab27a55ab11 `
  --eval-run-id evalrun_bb0cf6c99f644d7eb609c10c6dcbc2d6 `
  -e forge `
  --out-file "agents\contract-policy-expert\eval-results-contract-policy-evidence-compliance-live-fresh.json" `
  --no-prompt
```

## Notes and caveats

The evaluation service records tool calls separately from the final assistant
output. Earlier rubric versions included a strict `single_json_output` dimension,
which sometimes judged evaluation-service trace wrappers as if they were part of
the final response. The current evaluator focuses on the final Forge evidence
contract, grounded retrieval behavior, no-write boundaries, invoice/correlation
consistency, and general waypoint-recorder usability.
