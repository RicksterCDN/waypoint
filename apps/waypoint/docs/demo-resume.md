# Waypoint demo resume brief

## Current product direction

Waypoint is a contract manufacturing supplier oversight demo.

The user starts in Teams or Microsoft 365 Copilot, not inside Waypoint chat. Copilot asks or answers a high-level question such as: "Why did contract manufacturing invoice spend spike around the prescription surge?" Then Copilot hands off to Waypoint for evidence, traceability, and controlled action.

Waypoint is the deep investigation and control surface for supplier invoices, purchase orders, master service agreements, batch records, quality release logs, production schedules, supplier correspondence, and IP-sensitive escalation.

## Current vocabulary

- **Waypoint** is the software name, made by **Caldova**.
- **Caldova** is the customer/company name.
- Use **Intake**, not Chat.
- Use **Runs**, not Agent run.
- Use **supplier invoice decisions**, **contract manufacturing**, **batch records**, **MSAs**, **QA release logs**, **production schedule**, **prescription surge**, and **IP-sensitive escalation**.
- Avoid stale terms: `chat`, `freight`, `carrier`, `shipment`, `lane`, `accessorial`, and `Logistics Company`.

## Current app shape

Navigation should read:

```text
Overview -> Intake -> Invoices -> Runs -> Costs -> Optimize
```

Route intent:

- **Overview**: explains the contract manufacturing supplier oversight scenario.
- **Intake**: Teams/Copilot handoff landing page with original question, evidence, findings, and escalation packet status.
- **Invoices**: supplier invoice decisions with source-linked evidence and drawer context.
- **Runs**: agent execution runbook for supplier invoice monitoring and IP-sensitive anomaly detection.
- **Costs**: software/agent operating cost for supplier oversight workloads.
- **Optimize**: model evaluation for approve/recover/escalate/review decisions, especially IP-risk false negatives.

## Current uncommitted implementation

The current working tree contains the pivot implementation and is not committed unless a later commit records it.

Expected changed surface:

```text
web/app/components/AppHeader.tsx
web/app/routes.ts
web/app/routes/agent.tsx
web/app/routes/chat.tsx deleted
web/app/routes/costs.tsx
web/app/routes/home.tsx
web/app/routes/invoices.tsx
web/app/routes/intake.tsx added
web/app/routes/login.tsx
web/app/routes/optimize.tsx
```

## Implementation notes

- `web/app/routes/chat.tsx` was removed.
- `web/app/routes/intake.tsx` was added.
- `/chat` was replaced with `/intake` in route registration.
- The visible nav now includes Intake instead of Chat.
- The `/agent` URL and `agent.tsx` filename still back the visible **Runs** page to minimize churn.
- The current visual system should be preserved: dense shell, tables, drawers, runbook layout, scrollbar styling, auth header, and restrained card styling.

## Validation commands

Run these after resuming or before committing:

```powershell
Set-Location D:\projects\waypoint
git fetch --all --prune
git --no-pager status --short --branch
git --no-pager diff --stat

Set-Location D:\projects\waypoint\web
npm run typecheck
npm run build

Set-Location D:\projects\waypoint
git --no-pager diff --check
```

Optional stale-term check:

```powershell
Set-Location D:\projects\waypoint
git --no-pager grep -n -i -E "chat|freight|carrier|shipment|lane|accessorial|Logistics Company" -- web/app
```

## Workflow reminders

- Fetch latest remote refs before starting code changes.
- Do not commit or push unless the user explicitly asks.
- Ask before deploying.
- The user generally prefers to manage Aspire themselves; inspect logs or restart resources when asked or when clearly needed.
