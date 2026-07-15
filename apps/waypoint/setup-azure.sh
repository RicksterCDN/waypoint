#!/usr/bin/env bash
# Setup Azure and GitHub for deployment
# This script creates Azure resources and configures GitHub secrets for OIDC authentication
#
# Prerequisites:
#   - Azure CLI (az) - logged in with `az login`
#   - GitHub CLI (gh) - logged in with `gh auth login`
#   - Permission to create Azure AD app registrations (may require admin approval in some tenants)
#
# Usage: ./setup-azure.sh <github-org/repo> [app-name] [location]
# Example: ./setup-azure.sh caldova/waypoint waypoint eastus

set -e

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m' # No Color

# Arguments
GITHUB_REPO="${1:-}"
APP_NAME="${2:-}"
LOCATION="${3:-eastus}"

if [ -z "$GITHUB_REPO" ]; then
    echo -e "${RED}❌ Usage: ./setup-azure.sh <github-org/repo> [app-name] [location]${NC}"
    echo -e "${YELLOW}   Example: ./setup-azure.sh caldova/waypoint waypoint eastus${NC}"
    exit 1
fi

# Derive app name from repo name if not provided
if [ -z "$APP_NAME" ]; then
    APP_NAME=$(echo "$GITHUB_REPO" | cut -d'/' -f2)
fi

# Sanitize app name (lowercase, alphanumeric and hyphens only)
APP_NAME=$(echo "$APP_NAME" | tr '[:upper:]' '[:lower:]' | sed 's/[^a-z0-9-]/-/g')

# Derive resource names from app name
RESOURCE_GROUP="${APP_NAME}-rg"
APP_REGISTRATION_NAME="${APP_NAME}-deploy"

echo -e "${CYAN}🚀 Setting up Azure and GitHub for deployment...${NC}"
echo -e "   App Name: $APP_NAME"
echo ""

# Check prerequisites
check_command() {
    if ! command -v "$1" &> /dev/null; then
        echo -e "${RED}❌ $1 is not installed. $2${NC}"
        exit 1
    fi
}

check_command "az" "Install from: https://docs.microsoft.com/cli/azure/install-azure-cli"
check_command "gh" "Install from: https://cli.github.com/"

# Verify Azure login
echo -e "${CYAN}Checking Azure login...${NC}"
if ! az account show &> /dev/null; then
    echo -e "${YELLOW}Please log in to Azure:${NC}"
    az login
fi
SUBSCRIPTION_ID=$(az account show --query id -o tsv)
TENANT_ID=$(az account show --query tenantId -o tsv)
echo -e "${GREEN}✅ Logged in to Azure (Subscription: $SUBSCRIPTION_ID)${NC}"

# Verify GitHub login
echo -e "${CYAN}Checking GitHub login...${NC}"
if ! gh auth status &> /dev/null; then
    echo -e "${YELLOW}Please log in to GitHub:${NC}"
    gh auth login
fi
echo -e "${GREEN}✅ Logged in to GitHub${NC}"

# Create resource group if it doesn't exist
echo ""
echo -e "${CYAN}Creating resource group '$RESOURCE_GROUP' in '$LOCATION'...${NC}"
az group create --name "$RESOURCE_GROUP" --location "$LOCATION" --output none 2>/dev/null || true
echo -e "${GREEN}✅ Resource group ready${NC}"

# Create Azure AD application
echo ""
echo -e "${CYAN}Creating Azure AD application '$APP_REGISTRATION_NAME'...${NC}"
APP_ID=$(az ad app list --display-name "$APP_REGISTRATION_NAME" --query "[0].appId" -o tsv)

if [ -z "$APP_ID" ] || [ "$APP_ID" == "null" ]; then
    APP_ID=$(az ad app create --display-name "$APP_REGISTRATION_NAME" --sign-in-audience AzureADMyOrg --query appId -o tsv 2>/dev/null)
    if [ -z "$APP_ID" ]; then
        echo -e "${RED}❌ Failed to create Azure AD application. You may need to create it manually in the Azure portal.${NC}"
        exit 1
    fi
    echo -e "${GREEN}✅ Created new Azure AD application${NC}"
else
    echo -e "${YELLOW}ℹ️  Using existing Azure AD application${NC}"
fi

# Create service principal if it doesn't exist
echo ""
echo -e "${CYAN}Creating service principal...${NC}"
SP_ID=$(az ad sp list --filter "appId eq '$APP_ID'" --query "[0].id" -o tsv)

if [ -z "$SP_ID" ] || [ "$SP_ID" == "null" ]; then
    az ad sp create --id "$APP_ID" --output none 2>/dev/null
    if [ $? -ne 0 ]; then
        echo -e "${RED}❌ Failed to create service principal${NC}"
        exit 1
    fi
    echo -e "${GREEN}✅ Created service principal${NC}"
else
    echo -e "${YELLOW}ℹ️  Using existing service principal${NC}"
fi

# Create federated credential for GitHub Actions
echo ""
echo -e "${CYAN}Creating federated credential for GitHub Actions...${NC}"
CREDENTIAL_NAME="github-actions-main"

# Check if credential exists
EXISTING_CRED=$(az ad app federated-credential list --id "$APP_ID" --query "[?name=='$CREDENTIAL_NAME'].name" -o tsv 2>/dev/null || echo "")

if [ -z "$EXISTING_CRED" ]; then
    az ad app federated-credential create --id "$APP_ID" --parameters "{
        \"name\": \"$CREDENTIAL_NAME\",
        \"issuer\": \"https://token.actions.githubusercontent.com\",
        \"subject\": \"repo:$GITHUB_REPO:ref:refs/heads/main\",
        \"audiences\": [\"api://AzureADTokenExchange\"]
    }" --output none
    echo -e "${GREEN}✅ Created federated credential for main branch${NC}"
else
    echo -e "${YELLOW}ℹ️  Federated credential already exists${NC}"
fi

# Assign Contributor role to the service principal
echo ""
echo -e "${CYAN}Assigning Contributor role to service principal...${NC}"
az role assignment create \
    --assignee "$APP_ID" \
    --role "Contributor" \
    --scope "/subscriptions/$SUBSCRIPTION_ID/resourceGroups/$RESOURCE_GROUP" \
    --output none 2>/dev/null || echo -e "${YELLOW}ℹ️  Role assignment may already exist${NC}"
echo -e "${GREEN}✅ Role assignment ready${NC}"

# Configure GitHub secrets
echo ""
echo -e "${CYAN}Configuring GitHub repository secrets...${NC}"

gh secret set AZURE_CLIENT_ID --repo "$GITHUB_REPO" --body "$APP_ID"
echo -e "${GREEN}✅ Set AZURE_CLIENT_ID${NC}"

gh secret set AZURE_TENANT_ID --repo "$GITHUB_REPO" --body "$TENANT_ID"
echo -e "${GREEN}✅ Set AZURE_TENANT_ID${NC}"

gh secret set AZURE_SUBSCRIPTION_ID --repo "$GITHUB_REPO" --body "$SUBSCRIPTION_ID"
echo -e "${GREEN}✅ Set AZURE_SUBSCRIPTION_ID${NC}"

gh secret set AZURE_RESOURCE_GROUP --repo "$GITHUB_REPO" --body "$RESOURCE_GROUP"
echo -e "${GREEN}✅ Set AZURE_RESOURCE_GROUP${NC}"

gh secret set AZURE_LOCATION --repo "$GITHUB_REPO" --body "$LOCATION"
echo -e "${GREEN}✅ Set AZURE_LOCATION${NC}"

# Summary
echo ""
echo -e "${GREEN}════════════════════════════════════════════════════════════${NC}"
echo -e "${GREEN}✅ Setup complete!${NC}"
echo -e "${GREEN}════════════════════════════════════════════════════════════${NC}"
echo ""
echo -e "Azure Resources:"
echo -e "  • Subscription:    $SUBSCRIPTION_ID"
echo -e "  • Tenant:          $TENANT_ID"
echo -e "  • Resource Group:  $RESOURCE_GROUP"
echo -e "  • App Registration: $APP_REGISTRATION_NAME (ID: $APP_ID)"
echo ""
echo -e "GitHub Secrets configured in ${CYAN}$GITHUB_REPO${NC}:"
echo -e "  • AZURE_CLIENT_ID"
echo -e "  • AZURE_TENANT_ID"
echo -e "  • AZURE_SUBSCRIPTION_ID"
echo -e "  • AZURE_RESOURCE_GROUP"
echo -e "  • AZURE_LOCATION"
echo ""
echo -e "${YELLOW}To deploy, push to main branch or manually trigger the deploy workflow.${NC}"
