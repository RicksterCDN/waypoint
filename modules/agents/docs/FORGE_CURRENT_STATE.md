# Caldova Forge current state and Jess handoff

This is the current working picture for the Forge invoice-assurance fleet. It is
intended to be honest about what is real, what is stubbed, and what Jess needs to
finish before the demo can be called end-to-end.

## Current active fleet

| Agent | Kind | Deployment path | What it owns today | Current confidence |
| --- | --- | --- | --- | --- |
| `assurance-orchestrator` | Hosted container agent | `azd deploy assurance-orchestrator` from `agents/assurance-orchestrator` | Deterministic invoice-assurance workflow, Content Understanding integration, hosted expert fan-out, write-plan preview, and a **code-owned run lifecycle** (`run_invoice_assurance`) that opens, bounds, and ALWAYS finalizes the run (recorder handoff on success; `failed` on timeout/error) — the model no longer choreographs `consult_*`/`handoff`. | Local workflow + harness tests and deployed hosted diagnostic fan-out through all four experts are proven. Hosted PDF/CU and the hosted code-owned recorder finalize still need a prod trace. |
| `waypoint-recorder` | Hosted container agent | `azd deploy waypoint-recorder` from `agents/waypoint-recorder` | Final governed Waypoint write boundary for run/case/recommendation/draft payloads. | Local fake-client and local Waypoint writes are proven. Hosted handoff from Assurance Orchestrator still needs proof. |
| `assurance-analyst` | Hosted container agent | `azd deploy assurance-analyst` from `agents/assurance-analyst` | Human-facing read-only Q&A/status agent. It combines direct Waypoint status tools, WaypointIQ operational reads, FoundryIQ KB grounding, Responses, and Microsoft 365 activity protocol. | Code is merged and deployable after collapsing `status-concierge`. Needs redeploy and smoke against the merged agent. |
| `collaboration-evidence-expert` | Hosted container agent | `azd deploy collaboration-evidence-expert` from `agents/collaboration-evidence-expert` | WorkIQ/Microsoft 365 evidence lane through the `collaboration-evidence-tools` toolbox. | Deployed hosted smoke through Assurance Orchestrator proved WorkIQ calls execute; the smoke invoice returned no Teams/SharePoint/Copilot matches. |
| `market-evidence-expert` | Hosted container agent | `azd deploy market-evidence-expert` from `agents/market-evidence-expert` | WebIQ/external market evidence lane. | Deployed hosted fan-out executed successfully; invoice-id-only smoke returned no public evidence, so a known-positive case is still needed. |
| `contract-policy-expert` | Hosted container agent | `azd deploy contract-policy-expert` from `agents/contract-policy-expert` | FoundryIQ contract/policy/finding evidence lane. | Deployed hosted fan-out returned contract/policy evidence from the KB lane. |
| `operations-data-expert` | Hosted container agent | `azd deploy operations-data-expert` from `agents/operations-data-expert` | FabricIQ/structured operational data lane. | Intentionally stubbed and contract-valid. No real Fabric/OneLake/warehouse source is wired. |

Retired names that should not appear as active fleet members:

- `pacioli` -> renamed to `assurance-orchestrator`.
- `aggregator` -> renamed to `waypoint-recorder`.
- `status-concierge` -> collapsed into `assurance-analyst`.
- `foundryiq-expert`, `fabriciq-expert`, `webiq-expert`, and `workiq-expert`
  are lineage/rollback names only.

## How each kind deploys

### Hosted container agents

Hosted agents are the services listed in `azure.yaml`:

- `assurance-orchestrator`
- `waypoint-recorder`
- `assurance-analyst`
- `collaboration-evidence-expert`
- `market-evidence-expert`
- `contract-policy-expert`
- `operations-data-expert`

Each hosted agent has an `agents/<name>/agent.yaml`, `Dockerfile`, dependency
manifest, and runtime source. The deploy workflow discovers hosted services with
`scripts/discover_agents.py`; the four evidence experts are included because
their `prompt.md` frontmatter sets `metadata.forge.deployment.hosted.enabled:
true`.

Deploy flow:

1. `azd provision` creates or updates shared infrastructure when requested.
2. The deploy workflow hydrates azd env values from the latest successful
   subscription deployment outputs.
3. `scripts/create_toolbox.py --agent <name>` creates or updates
   `<name>-tools` for hosted agents that still expose a toolbox.
4. The workflow seeds runtime env vars for Waypoint, WorkIQ, Content
   Understanding, blueprint IDs, and Assurance Orchestrator fan-out endpoints.
5. `azd deploy <name> --no-prompt` remote-builds the container in ACR and deploys
   the hosted Foundry agent.
6. The workflow grants the hosted agent's instance identity `Cognitive Services
   User` and `Foundry User` on the AI account.
7. The workflow patches endpoint protocol/auth shape and runs a control-plane
   active-version smoke.

Important caveat: the deploy workflow smoke confirms the Foundry control plane
sees an active version and ACR image. It is not a full business E2E invocation.
Real container-path confidence still comes from targeted hosted smokes, Teams
testing, evals, and Pipeline Mission Control runs.

### Prompt-agent evidence experts

There are no active prompt-agent evidence experts in the current fleet. The
four single-plane experts keep `prompt.md` as their instruction source, but
`metadata.forge.deployment.prompt.enabled` is false and the hosted containers
load those instructions at runtime. `scripts/prompt_agent_plan.py --check`
therefore reports zero prompt-agent deployments unless a future agent opts into
the prompt path.

### IQ/toolbox surfaces

Toolboxes are capability surfaces, not agents.

| Surface | Deployment / source | Resource dependencies | Current state |
| --- | --- | --- | --- |
| `waypoint-iq` | `iqs/waypoint-iq/scripts/deploy_toolbox.py` | Hosted Waypoint API, Foundry project, Waypoint read/write app roles | Durable toolbox v1 exists and has passed read smoke for hosted Waypoint. Write-path binding is still future work. |
| `assurance-analyst-tools` | `scripts/create_toolbox.py --agent assurance-analyst` | FoundryIQ KB MCP connection (`kb-mcp-connection`) and Azure AI Search KB | Source exists for contract/policy grounding. Merged `assurance-analyst` needs redeploy/smoke. |
| `assurance-orchestrator-tools` | `scripts/create_toolbox.py --agent assurance-orchestrator` | Waypoint read config, Content Understanding env, expert routing env | Used for hosted workflow/debug tools. Full hosted PDF + handoff path still needs proof. |
| `waypoint-recorder-tools` | `scripts/create_toolbox.py --agent waypoint-recorder` | Waypoint writer API URL/scope/key or MI app role | Local and fake-client write contract tests pass. Hosted handoff needs proof. |
| WorkIQ RemoteTool connections | Bicep project connections `WorkIQCopilot` / `WorkIQTeams` / `WorkIQSharePoint` provisioned by exact name in `infra/main.bicep` (UserEntraToken, agent365 catalog metadata; endpoints overridable via `WORKIQ_*_MCP_SERVER_URL`) and projected into toolbox `collaboration-evidence-tools`. | Tenant-supported Microsoft 365 MCP endpoints and a signed-in-user evidence smoke | Deployed hosted fan-out proved the WorkIQ toolbox executes as a lane; the smoke invoice returned no matching Microsoft 365 evidence. A known-positive WorkIQ case is still needed. |
| WebIQ/external evidence | Foundry native web-search grounding inside `market-evidence-expert`. | Foundry project web-search availability | Deployed hosted fan-out executed successfully. Invoice-id-only smoke returned no public evidence, so a known-positive market case is still needed. |
| FoundryIQ KB | Azure AI Search + knowledge base MCP connection | AI Search, storage, embedding deployment, `kb-mcp-connection` | KB-level smoke passed; hosted/prompt expert needs refresh to prove it uses the live KB. |
| FabricIQ | Prompt and local stub behavior only | Future Fabric/OneLake/warehouse/semantic-model source | Intentionally stubbed. Do not claim real Fabric evidence yet. |

## Shared Azure resources

`infra/main.bicep` parameterizes resources by azd environment. For the primary
Forge environment this currently maps to `rg-forge`, `ai-project-forge`, and the
shared AI Services account, but new environments should be isolated by
`AZURE_ENV_NAME`.

| Resource | What creates/configures it | Used by |
| --- | --- | --- |
| Resource group `rg-${AZURE_ENV_NAME}` | `azd provision` / `infra/main.bicep` | All Forge resources for that env. |
| AI Services / Foundry account | `infra/core/ai/ai-project.bicep` via `infra/main.bicep` | Hosted agents, prompt agents, model deployments, Content Understanding, Foundry project. |
| Foundry project `ai-project-${AZURE_ENV_NAME}` | `infra/main.bicep` | Agent definitions, prompt agents, toolboxes, project connections. |
| Chat model deployment | `infra/main.bicep` outputs `AZURE_AI_MODEL_DEPLOYMENT_NAME` | All hosted/prompt agents. Current primary env uses `gpt-5.5`. |
| Embedding deployment | `infra/main.bicep` outputs `AZURE_AI_EMBEDDING_DEPLOYMENT_NAME` | FoundryIQ/Search knowledge base. |
| Content Understanding completion deployment | `infra/main.bicep` outputs `CONTENT_UNDERSTANDING_COMPLETION_*` | Prebuilt invoice analysis used by Assurance Orchestrator. |
| Azure Container Registry | `infra/core/host/acr.bicep` or existing ACR env vars | Remote builds and hosted container images. |
| Application Insights | `infra/core/monitor/applicationinsights.bicep` or existing connection | Hosted-agent telemetry and trace/eval harvesting. |
| Azure AI Search + storage | `enableSearch=true` dependent resources | FoundryIQ contracts knowledge base and KB MCP connection. |
| WorkIQ RemoteTool project connections | Provisioned in `infra/main.bicep` as `WorkIQCopilot` / `WorkIQTeams` / `WorkIQSharePoint` (endpoints overridable via `WORKIQ_EMAIL_MCP_SERVER_URL`, `WORKIQ_TEAMS_MCP_SERVER_URL`, `WORKIQ_SHAREPOINT_MCP_SERVER_URL`) | Collaboration evidence expert. Names/auth/metadata match the prompt bindings so a fresh env self-provisions. |
| WebIQ MCP connection | `web-iq` (CustomKeys) — keyed connection, supplied via secret/out-of-band, not auto-provisioned by infra | Market evidence expert. |
| Waypoint API / Waypoint app roles | Passed in by Keystone/Waypoint deploy outputs and Key Vault/secrets | Waypoint reads/writes from Assurance Analyst, Assurance Orchestrator, and Waypoint Recorder. |
| Bot Service + Teams channel | Optional workflow dispatch `publish=true` and/or `make publish <agent>` | Microsoft 365 activity surfaces. Store/admin publication is separate from Foundry deploy. |

## What is stubbed or incomplete

| Area | Honest state | What Jess needs to finish |
| --- | --- | --- |
| WorkIQ/collaboration lane | Hosted lane executes through the WorkIQ toolbox/UserEntraToken path, but the current smoke invoice has no Microsoft 365 matches. | Add or identify a known-positive WorkIQ test case and rerun direct expert + Assurance Orchestrator fan-out. |
| FabricIQ/operations lane | Intentionally stubbed and says no Fabric/OneLake/warehouse/semantic-model source was queried. | Define the real Fabric source, auth model, invoice keys, query shape, and smoke data. Replace stub only after a direct Fabric evidence smoke passes. |
| FoundryIQ/contract lane | KB resource and hosted expert fan-out are real; deployed smoke returned evidence. | Expand known-positive eval coverage and source-ref checks. |
| WebIQ/market lane | Hosted fan-out executes, but invoice-id-only smoke returned no public evidence. | Add a known-positive supplier/product/date case for market evidence. |
| Content Understanding in hosted Orchestrator | Client and resource readiness are real. Latest local trace did not pass PDF input; older hosted version missed CU env vars. | Redeploy `assurance-orchestrator` with CU env, run hosted PDF URL or base64 smoke, and save a traceable run artifact. |
| Assurance Orchestrator -> Waypoint Recorder hosted handoff | **Decision resolved: handoff moved INSIDE the Orchestrator as deterministic code.** The hosted run lifecycle is now code-owned (`run_harness.run_invoice_assurance_and_finalize`, exposed as the single `run_invoice_assurance` tool): it opens the run, runs the deterministic workflow under the enforced 30-min bound, and ALWAYS finalizes in a `try/finally` — success hands fused evidence to the waypoint-recorder; timeout/error marks the run `failed` so it never orphans at `running`. The model no longer chooses `consult_*`/`handoff`. A durable finalize outbox (`ASSURANCE_ORCHESTRATOR_RUN_JOURNAL_DIR`) retries a transient recorder blip. Hosted endpoint handoff is still not proven in prod. | Deploy `assurance-orchestrator`, then prove the hosted `run_invoice_assurance` -> waypoint-recorder path exactly once for both the completed and a simulated-failure (`failed`, not `running`) outcome. Land the F2 Waypoint W2 invoice-scoped early-open guard to converge duplicate triggers on one run. |
| Assurance Analyst status surface | Status tools and activity protocol are merged into `assurance-analyst`. Prior status smoke was against retired `status-concierge`. | Redeploy `assurance-analyst`, fix Waypoint reader auth/role if still 401, then smoke status over Responses and Teams/activity. |
| Fresh environment automation | Primary env has been manually corrected during development. | Run clean `azd provision`/deploy through workflow, prove outputs hydrate, no manual ACR/Foundry/CU values are needed, and all hosted agents become active. |

## Jess completion checklist

The handoff is complete when these are true:

1. `python -m compileall -q agents scripts evals`, `python scripts/discover_agents.py`,
   `python scripts/prompt_agent_plan.py --check`, and
   `python scripts/test_evidence_contract.py` pass.
2. `azd deploy assurance-analyst`, `azd deploy assurance-orchestrator`,
   `azd deploy waypoint-recorder`, and the four hosted evidence expert deploys
   succeed in the target env.
3. `assurance-orchestrator` is deployed with hosted expert endpoint env vars:
   `WORKIQ_EXPERT_ENDPOINT`, `WEBIQ_EXPERT_ENDPOINT`,
   `FOUNDRYIQ_EXPERT_ENDPOINT`, and `FABRICIQ_EXPERT_ENDPOINT`.
4. Assurance Analyst can answer run/case status from Waypoint without writing.
5. Content Understanding runs for a real invoice PDF inside Assurance
   Orchestrator.
6. WorkIQ, WebIQ, FoundryIQ, and FabricIQ each return a completed evidence
   contract with cited sources for the same invoice. If FabricIQ is intentionally
   deferred, the demo story must say it is stubbed.
7. Assurance Orchestrator synthesizes the evidence and produces one
   `write_plan.future_payloads[]` preview with `side_effects_performed=false`.
8. Waypoint Recorder writes exactly one run/case/recommendation/draft set to
   Waypoint from that preview.
9. The resulting Waypoint artifacts are visible in the Waypoint app.
10. A saved trace/artifact exists for the full flow so reviewers do not have to
    reconstruct the story from logs.
