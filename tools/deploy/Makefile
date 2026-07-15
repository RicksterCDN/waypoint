# keystone — umbrella one-click deploy orchestrator.
# Owns no app code; drives keyvault -> ledgerfield -> forge -> waypoint -> onelake-upload
# -> seed-import -> wire.

SHELL := /bin/bash
-include .env
export

OWNER ?= caldova
REPO ?= keystone
APP_NAME ?= forge-gha-oidc

.PHONY: help deploy-all discover keyvault msal seed deploy-forge deploy-waypoint \
        onelake-upload seed-import wire check oidc preflight

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

check: ## Verify required env is present
	@: $${AZURE_TENANT_ID:?set in .env}; \
	  : $${AZURE_SUBSCRIPTION_ID:?set in .env}; \
	  : $${AZURE_CLIENT_ID:?set in .env}; \
	  echo "env OK"

oidc: ## Bootstrap (idempotent) the Azure OIDC trust + keystone repo vars/secrets
	@scripts/oidc.sh \
	  --owner "$(OWNER)" --repo "$(REPO)" \
	  --subscription-id "$(AZURE_SUBSCRIPTION_ID)" \
	  --app-name "$(APP_NAME)" \
	  --branch "$${DEPLOY_BRANCH:-main}" \
	  --location "$${AZURE_LOCATION:-swedencentral}" \
	  --pull-request

discover: ## Discover live topology + derive chosen names (prints key=value)
	@AZURE_SUBSCRIPTION_ID="$(AZURE_SUBSCRIPTION_ID)" AZURE_CLIENT_ID="$(AZURE_CLIENT_ID)" \
	  AZD_ENV_NAME="$${AZD_ENV_NAME:-waypoint}" scripts/discover.sh

preflight: ## Print the change/existence detection decision locally
	@GH_REPO="$(OWNER)/$(REPO)" scripts/preflight.sh

deploy-all: check discover keyvault msal seed deploy-forge deploy-waypoint onelake-upload seed-import wire ## Run the full ordered deploy (best-effort local)
	@echo "keystone deploy-all complete"

keyvault: ## Stage 0a: provision Key Vault + generate-once the x-api-keys + pg password
	@eval "$$(scripts/discover.sh 2>/dev/null | sed 's/^/export /' | sed 's/=/=\"/; s/$$/\"/')"; \
	  KV_NAME="$$kv_name" STATE_RG="$$state_rg" SP_OBJECT_ID="$$sp_object_id" \
	  AZURE_LOCATION="$${AZURE_LOCATION:-swedencentral}" scripts/keyvault.sh

msal: ## Stage 0b: ensure the waypoint MSAL app reg + Waypoint.Read/Write/Admin roles
	@scripts/msal.sh ensure

seed: ## Stage 1: generate + publish ledgerfield waypoint-seed.json
	@scripts/run_stage.sh ledgerfield "$(LEDGERFIELD_REF)"

deploy-forge: ## Stage 2: azd provision + deploy the forge agents
	@scripts/run_stage.sh forge "$(FORGE_REF)"

deploy-waypoint: ## Stage 3: aspire deploy waypoint (+ optional Fabric OneLake lake)
	@scripts/run_stage.sh waypoint "$(WAYPOINT_REF)"

onelake-upload: ## Stage 4: upload the ledgerfield corpus into the OneLake lakehouse
	@scripts/run_stage.sh onelake-upload "$(LEDGERFIELD_REF)"

seed-import: ## Stage 5: POST the seed into the deployed Waypoint admin endpoint
	@scripts/run_stage.sh seed-import "$(WAYPOINT_REF)"

wire: ## Stage 6: push cross-repo endpoint/config values into forge
	@scripts/wire.sh
