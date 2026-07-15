#!/usr/bin/env bash
set -euo pipefail

# ------------------------------------------------------------------------------
# Azure OIDC bootstrap for GitHub Actions (forge)
# Stands up a FRESH (or reused) Entra app dedicated to one GitHub repo so the
# deploy workflow can `az login` with OIDC — no client secrets stored anywhere.
#
# It is idempotent: re-running never duplicates the app, SP, federated
# credentials, or role assignments.
#
# What it ensures:
#   - An Entra app registration + service principal
#   - Federated credential for the deploy branch (default: main) OR a GitHub
#     Environment subject
#   - a `pull_request` federated credential — used by the `whatif` job in
#     deploy.yml to run a read-only `azd provision --preview` what-if on every PR.
#     Enabled with `--pull-request` (included by default in `make setup-oidc`).
#     Real provision/deploy are still gated off pull_request.
#   - (optional) a FLEXIBLE federated credential matching `job_workflow_ref` so
#     OTHER repos that call forge's REUSABLE deploy workflow (e.g. the keystone
#     umbrella) are trusted. When workflow A calls forge's reusable workflow,
#     the OIDC subject reflects the CALLER repo, so a per-caller subject FIC
#     would be brittle; matching job_workflow_ref trusts any caller invoking
#     forge's own deploy.yml. Recommended ON for the one-click umbrella.
#   - Subscription role assignments (default: Contributor + User Access
#     Administrator — UAA is required because the infra bicep creates role
#     assignments of its own).
#   - writes AZURE_CLIENT_ID / AZURE_TENANT_ID / AZURE_SUBSCRIPTION_ID /
#     AZURE_LOCATION / AZD_ENV_NAME as GitHub repo VARIABLES via `gh` (forge
#     reads Azure creds as variables, not secrets). ON by default so the repo is
#     fully configured after one run; opt out with --no-set-repo-variables. The
#     values are always printed too.
#
# Requirements:
#   - az CLI logged in as a user able to create app/SP + role assignments
#     (Owner or Contributor+User Access Administrator on the subscription)
#   - jq
#   - gh, authenticated with repo admin (for the default repo-variable write)
#
# Usage:
#   ./scripts/oidc.sh \
#     --owner caldova --repo forge \
#     --subscription-id <SUB_ID> \
#     --app-name forge-gha-oidc \
#     --branch main \
#     --pull-request \
#     --reusable-callers
#   # repo variables are written by default; add --no-set-repo-variables to skip.
#   # override deploy config: --location <region> --env-name <azd-env>
#
#   # GitHub Environment subject instead of a branch:
#   ./scripts/oidc.sh ... --environment prod
# ------------------------------------------------------------------------------

OWNER=""
REPO=""
SUBSCRIPTION_ID=""
APP_NAME="forge-gha-oidc"
BRANCH="main"
ENVIRONMENT=""
ROLES="Contributor,User Access Administrator"
WORKFLOW_PATH=".github/workflows/deploy.yml"
LOCATION="swedencentral"
ENV_NAME="forge"
ADD_PULL_REQUEST="false"
ADD_REUSABLE_CALLERS="false"
# Repo variables are written by DEFAULT so `make setup-oidc` leaves the repo
# fully configured (one-click). Opt out with --no-set-repo-variables.
SET_REPO_VARIABLES="true"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --owner) OWNER="$2"; shift 2 ;;
    --repo) REPO="$2"; shift 2 ;;
    --subscription-id) SUBSCRIPTION_ID="$2"; shift 2 ;;
    --app-name) APP_NAME="$2"; shift 2 ;;
    --branch) BRANCH="$2"; shift 2 ;;
    --environment) ENVIRONMENT="$2"; shift 2 ;;
    --roles) ROLES="$2"; shift 2 ;;
    --workflow-path) WORKFLOW_PATH="$2"; shift 2 ;;
    --location) LOCATION="$2"; shift 2 ;;
    --env-name) ENV_NAME="$2"; shift 2 ;;
    --pull-request) ADD_PULL_REQUEST="true"; shift 1 ;;
    --reusable-callers) ADD_REUSABLE_CALLERS="true"; shift 1 ;;
    # Back-compat no-op: variable writing is on by default now.
    --set-repo-variables) SET_REPO_VARIABLES="true"; shift 1 ;;
    --no-set-repo-variables) SET_REPO_VARIABLES="false"; shift 1 ;;
    -h|--help)
      sed -n '1,60p' "$0"
      exit 0
      ;;
    *)
      echo "Unknown arg: $1" >&2
      exit 1
      ;;
  esac
done

if [[ -z "$OWNER" || -z "$REPO" || -z "$SUBSCRIPTION_ID" ]]; then
  echo "Missing required args. Need --owner, --repo, --subscription-id" >&2
  exit 1
fi

if ! command -v jq >/dev/null 2>&1; then
  echo "jq is required. Install jq and retry." >&2
  exit 1
fi

echo "Setting Azure subscription..."
az account set --subscription "$SUBSCRIPTION_ID"

TENANT_ID="$(az account show --query tenantId -o tsv)"
SCOPE="/subscriptions/${SUBSCRIPTION_ID}"

echo "Looking up app registration: ${APP_NAME}"
APP_JSON="$(az ad app list --display-name "$APP_NAME" --query '[0]' -o json)"
APP_ID="$(echo "$APP_JSON" | jq -r '.appId // empty')"
APP_OBJECT_ID="$(echo "$APP_JSON" | jq -r '.id // empty')"

if [[ -z "$APP_ID" ]]; then
  echo "Creating app registration: ${APP_NAME}"
  CREATED_APP="$(az ad app create --display-name "$APP_NAME" -o json)"
  APP_ID="$(echo "$CREATED_APP" | jq -r '.appId')"
  APP_OBJECT_ID="$(echo "$CREATED_APP" | jq -r '.id')"
else
  echo "Reusing existing app registration: ${APP_NAME} (${APP_ID})"
fi

echo "Ensuring service principal exists..."
SP_OBJECT_ID="$(az ad sp list --filter "appId eq '$APP_ID'" --query '[0].id' -o tsv)"
if [[ -z "$SP_OBJECT_ID" ]]; then
  az ad sp create --id "$APP_ID" >/dev/null
  # short retry loop for AAD propagation
  for i in {1..10}; do
    SP_OBJECT_ID="$(az ad sp list --filter "appId eq '$APP_ID'" --query '[0].id' -o tsv || true)"
    [[ -n "$SP_OBJECT_ID" ]] && break
    sleep 2
  done
fi

if [[ -z "$SP_OBJECT_ID" ]]; then
  echo "Failed to resolve service principal for appId ${APP_ID}" >&2
  exit 1
fi

echo "Service principal object id: ${SP_OBJECT_ID}"

# ---- federated credential helpers -------------------------------------------
# ensure_fed_cred_subject NAME SUBJECT DESCRIPTION
ensure_fed_cred_subject() {
  local name="$1" subject="$2" desc="$3"
  echo "Ensuring federated credential: ${name}  (subject: ${subject})"
  local count
  count="$(az ad app federated-credential list --id "$APP_OBJECT_ID" --query "[?name=='${name}'] | length(@)" -o tsv)"
  if [[ "$count" != "0" ]]; then
    echo "  already exists"
    return 0
  fi
  local tmp; tmp="$(mktemp)"
  cat > "$tmp" <<EOF
{
  "name": "${name}",
  "issuer": "https://token.actions.githubusercontent.com",
  "subject": "${subject}",
  "audiences": ["api://AzureADTokenExchange"],
  "description": "${desc}"
}
EOF
  az ad app federated-credential create --id "$APP_OBJECT_ID" --parameters "$tmp" >/dev/null
  rm -f "$tmp"
  echo "  created"
}

# ensure_fed_cred_expr NAME EXPRESSION DESCRIPTION
# Flexible federated credential: matches a claim instead of a fixed subject.
# Used to trust reusable-workflow CALLERS via job_workflow_ref.
ensure_fed_cred_expr() {
  local name="$1" expr="$2" desc="$3"
  echo "Ensuring flexible federated credential: ${name}  (match: ${expr})"
  local count
  count="$(az ad app federated-credential list --id "$APP_OBJECT_ID" --query "[?name=='${name}'] | length(@)" -o tsv)"
  if [[ "$count" != "0" ]]; then
    echo "  already exists"
    return 0
  fi
  local tmp; tmp="$(mktemp)"
  cat > "$tmp" <<EOF
{
  "name": "${name}",
  "issuer": "https://token.actions.githubusercontent.com",
  "audiences": ["api://AzureADTokenExchange"],
  "claimsMatchingExpression": { "value": "${expr}", "languageVersion": 1 },
  "description": "${desc}"
}
EOF
  if az ad app federated-credential create --id "$APP_OBJECT_ID" --parameters "$tmp" >/dev/null 2>&1; then
    echo "  created"
  else
    echo "  WARNING: could not create flexible (claimsMatchingExpression) credential." >&2
    echo "           This needs a recent az CLI (flexible FIC support). Either upgrade az" >&2
    echo "           and re-run, or add it in the portal under Federated credentials with" >&2
    echo "           match expression: ${expr}" >&2
  fi
  rm -f "$tmp"
}
# -----------------------------------------------------------------------------

# Primary subject: a branch ref, or a GitHub Environment.
if [[ -n "$ENVIRONMENT" ]]; then
  ensure_fed_cred_subject \
    "github-${OWNER}-${REPO}-env-${ENVIRONMENT}" \
    "repo:${OWNER}/${REPO}:environment:${ENVIRONMENT}" \
    "GitHub Actions OIDC for ${OWNER}/${REPO} (env ${ENVIRONMENT})"
else
  ensure_fed_cred_subject \
    "github-${OWNER}-${REPO}-branch-${BRANCH}" \
    "repo:${OWNER}/${REPO}:ref:refs/heads/${BRANCH}" \
    "GitHub Actions OIDC for ${OWNER}/${REPO} (branch ${BRANCH})"
fi

# Optional: pull_request subject (only if you want PR runs to reach Azure).
if [[ "$ADD_PULL_REQUEST" == "true" ]]; then
  ensure_fed_cred_subject \
    "github-${OWNER}-${REPO}-pull-request" \
    "repo:${OWNER}/${REPO}:pull_request" \
    "GitHub Actions OIDC for ${OWNER}/${REPO} (pull_request)"
fi

# Optional: trust reusable-workflow callers (e.g. the keystone umbrella) by
# matching job_workflow_ref against THIS repo's deploy workflow at any ref.
if [[ "$ADD_REUSABLE_CALLERS" == "true" ]]; then
  ensure_fed_cred_expr \
    "github-${OWNER}-${REPO}-reusable-callers" \
    "claims['job_workflow_ref'] matches '${OWNER}/${REPO}/${WORKFLOW_PATH}@*'" \
    "Trust any GitHub Actions caller of ${OWNER}/${REPO}/${WORKFLOW_PATH} (reusable workflow)"
fi

# ---- role assignments --------------------------------------------------------
IFS=',' read -r -a ROLE_ARRAY <<< "$ROLES"
for ROLE in "${ROLE_ARRAY[@]}"; do
  ROLE_TRIMMED="$(echo "$ROLE" | xargs)"
  [[ -z "$ROLE_TRIMMED" ]] && continue
  echo "Ensuring role assignment: ${ROLE_TRIMMED} on ${SCOPE}"
  RA_COUNT="$(az role assignment list \
    --assignee "$SP_OBJECT_ID" \
    --scope "$SCOPE" \
    --role "$ROLE_TRIMMED" \
    --query 'length(@)' -o tsv)"
  if [[ "$RA_COUNT" == "0" ]]; then
    az role assignment create \
      --assignee "$SP_OBJECT_ID" \
      --role "$ROLE_TRIMMED" \
      --scope "$SCOPE" >/dev/null
    echo "  Assigned ${ROLE_TRIMMED}"
  else
    echo "  Already assigned ${ROLE_TRIMMED}"
  fi
done

# ---- write the repo variables (on by default) -------------------------------
if [[ "$SET_REPO_VARIABLES" == "true" ]]; then
  if ! command -v gh >/dev/null 2>&1; then
    echo "::error:: gh CLI not found — cannot auto-set repo variables." >&2
    echo "Install gh (https://cli.github.com) and re-run, or set them manually (see below)." >&2
    SET_REPO_VARIABLES="failed"
  elif ! gh auth status >/dev/null 2>&1; then
    echo "::error:: gh is not authenticated — run 'gh auth login' and re-run." >&2
    SET_REPO_VARIABLES="failed"
  else
    echo "Setting GitHub repository variables on ${OWNER}/${REPO}..."
    set_var() {
      local name="$1" value="$2"
      if gh variable set "$name" --repo "${OWNER}/${REPO}" --body "$value" >/dev/null 2>&1; then
        echo "  ✓ ${name}=${value}"
      else
        echo "::error:: failed to set ${name} on ${OWNER}/${REPO}." >&2
        echo "    Your gh token likely lacks variable-write permission (needs repo admin / 'repo' scope)." >&2
        echo "    Fix: gh auth refresh -h github.com -s repo   (or set it manually, command below)" >&2
        SET_REPO_VARIABLES="failed"
      fi
    }
    set_var AZURE_CLIENT_ID       "${APP_ID}"
    set_var AZURE_TENANT_ID       "${TENANT_ID}"
    set_var AZURE_SUBSCRIPTION_ID "${SUBSCRIPTION_ID}"
    set_var AZURE_LOCATION        "${LOCATION}"
    set_var AZD_ENV_NAME          "${ENV_NAME}"
  fi
fi

if [[ "$SET_REPO_VARIABLES" == "failed" ]]; then
  cat >&2 <<EOF

Repo variables were NOT fully set. Run these manually with a gh token that has
repo admin permission:
  gh variable set AZURE_CLIENT_ID       --repo ${OWNER}/${REPO} --body ${APP_ID}
  gh variable set AZURE_TENANT_ID       --repo ${OWNER}/${REPO} --body ${TENANT_ID}
  gh variable set AZURE_SUBSCRIPTION_ID --repo ${OWNER}/${REPO} --body ${SUBSCRIPTION_ID}
  gh variable set AZURE_LOCATION        --repo ${OWNER}/${REPO} --body ${LOCATION}
  gh variable set AZD_ENV_NAME          --repo ${OWNER}/${REPO} --body ${ENV_NAME}
EOF
fi

cat <<EOF

Done.

GitHub repository variables for ${OWNER}/${REPO} (forge reads Azure creds as VARIABLES):
- AZURE_CLIENT_ID=${APP_ID}
- AZURE_TENANT_ID=${TENANT_ID}
- AZURE_SUBSCRIPTION_ID=${SUBSCRIPTION_ID}
- AZURE_LOCATION=${LOCATION}
- AZD_ENV_NAME=${ENV_NAME}

Next:
1) Trigger the deploy workflow manually (workflow_dispatch) with provision=true.
2) Open a PR — the 'whatif' job runs a read-only 'azd provision --preview'.
EOF
