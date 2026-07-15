# GitHub Actions OIDC setup

The deploy workflow (`.github/workflows/deploy.yml`) authenticates to Azure with
**OIDC** (federated credentials) — no client secrets are stored in GitHub. This
doc explains how to stand that up in a fresh tenant so anyone can reproduce the
deploy in one command.

## What you need

- `az` CLI logged in as a user who can create an app registration + role
  assignments (Owner, or Contributor + User Access Administrator on the target
  subscription).
- `gh` CLI authenticated to the GitHub repo (only needed to auto-write the repo
  variables; otherwise the script prints them).
- `jq`.

## One command

```bash
make setup-oidc SUBSCRIPTION_ID=<your-subscription-id>
```

That runs `scripts/oidc.sh` with the umbrella-ready defaults and, idempotently:

1. Creates (or reuses) a dedicated Entra app registration `forge-gha-oidc` + its
   service principal.
2. Adds a federated credential for the deploy branch
   (`repo:<owner>/<repo>:ref:refs/heads/main`).
3. Adds a **flexible** federated credential matching `job_workflow_ref` so other
   repos that call forge's **reusable** deploy workflow (the keystone umbrella)
   are trusted. When workflow A calls forge's reusable workflow, the OIDC subject
   reflects the *caller* repo, so matching `job_workflow_ref` is the robust way
   to trust callers without hard-coding each caller's subject.
4. Assigns `Contributor` + `User Access Administrator` on the subscription
   (UAA is required because the infra bicep creates role assignments of its own).
5. Writes `AZURE_CLIENT_ID` / `AZURE_TENANT_ID` / `AZURE_SUBSCRIPTION_ID` /
   `AZURE_LOCATION` / `AZD_ENV_NAME` as GitHub repo **variables** (forge reads
   Azure creds as variables, not secrets). This happens **automatically** — after
   one run the repo is fully configured. Requires `gh` authenticated with repo
   admin; if a write fails the script prints the exact manual commands.

## Direct script usage

`make setup-oidc` is a thin wrapper. For full control call the script directly:

```bash
./scripts/oidc.sh \
  --owner caldova --repo forge \
  --subscription-id <SUB_ID> \
  --app-name forge-gha-oidc \
  --branch main \
  --location swedencentral --env-name forge \
  --pull-request \
  --reusable-callers
```

Useful flags:

- `--environment <name>` — use a GitHub Environment subject instead of a branch.
- `--location <region>` / `--env-name <azd-env>` — values written to the
  `AZURE_LOCATION` / `AZD_ENV_NAME` repo variables (default `swedencentral` /
  `forge`). Keep `AZURE_LOCATION` in sync with where you provision — `deploy.yml`'s
  built-in fallback is `eastus2`, so the variable must be set to override it.
- `--no-set-repo-variables` — skip writing repo variables (they're written by
  default); the values are still printed so you can set them yourself.
- `--pull-request` — trust `pull_request` runs. **Included by default in
  `make setup-oidc`** because forge's deploy now runs a read-only
  `azd provision --preview` what-if on every PR (the `whatif` job). That job needs
  a `pull_request` federated credential to mint its OIDC token. Drop this flag
  only if you intentionally do not want PR what-if (the job would then fail to
  authenticate). Real provision/deploy are still gated off `pull_request`.
- `--roles "Contributor,User Access Administrator"` — override role assignments.
- `--workflow-path .github/workflows/deploy.yml` — workflow file the
  reusable-caller match expression points at.

The script is idempotent — re-running never duplicates anything.

## What runs on a PR

`deploy.yml` is gated so the **real** `provision` and `deploy` jobs **do not run
on `pull_request`**. Instead a PR runs:

- `validate.yml` — static checks (no Azure).
- the `whatif` job in `deploy.yml` — `azd provision --preview`, an ARM what-if
  that compiles the bicep, resolves parameters, and prints the resource delta
  **without creating or mutating anything**. It authenticates via the
  `pull_request` federated credential created by `make setup-oidc`.

This fully vets the infra side of a PR while keeping it non-mutating. (Agent
images use `docker.remoteBuild=true`, so they build in ACR at deploy time — there
is no local image build to vet in a PR.)

> Fork PRs cannot mint an OIDC token, so the `whatif` job is skipped for forks.

## Simulating a deploy locally (no mutation)

To see what a deploy *would* do without changing Azure, from your machine:

```bash
azd auth login
azd provision --preview   # ARM what-if: shows resource changes, mutates nothing
```

## Troubleshooting

- `AADSTS700213: No matching federated identity record found for presented
  assertion subject 'repo:<owner>/<repo>:...'` — the app exists but has no
  federated credential for that subject. Re-run `make setup-oidc` (for a branch
  subject) or add `--pull-request` / `--environment` for those subjects.
- Flexible credential warning — creating the `job_workflow_ref` match needs a
  recent `az` CLI. Upgrade `az` and re-run, or add it in the portal under the
  app's **Federated credentials** with the printed match expression.
