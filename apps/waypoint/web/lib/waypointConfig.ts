/**
 * Runtime drill-down configuration client.
 *
 * Fetches non-secret Foundry and App Insights deep-link bases from the Waypoint API so
 * the Runs, Costs, and Optimize views can deep-link into Foundry and Azure Monitor
 * without baking endpoints into the web bundle (avoids a rebuild when endpoints change).
 */

import { authFetch } from "./msalAuth";
import { traced } from "./telemetry";

export interface DrilldownConfig {
  foundry_endpoint: string;
  foundry_project_url: string;
  app_insights_resource_id: string;
}

const emptyConfig: DrilldownConfig = {
  foundry_endpoint: "",
  foundry_project_url: "",
  app_insights_resource_id: "",
};

/**
 * Fetch the runtime drill-down configuration. Returns empty bases when unauthenticated
 * or when the API has no Foundry/App Insights configuration, so callers can degrade
 * gracefully to "telemetry unavailable" states.
 */
export async function fetchDrilldownConfig(): Promise<DrilldownConfig> {
  try {
    const response = await traced("fetchDrilldownConfig", () =>
      authFetch("/api/config", undefined, { requireToken: true }),
    );
    if (!response.ok) {
      return emptyConfig;
    }
    return (await response.json()) as DrilldownConfig;
  } catch {
    return emptyConfig;
  }
}

/** Build an App Insights transaction-search deep link for a correlation operation ID. */
export function appInsightsOperationLink(
  config: DrilldownConfig,
  operationId: string | null | undefined,
): string | null {
  if (!config.app_insights_resource_id || !operationId) {
    return null;
  }
  const resource = encodeURIComponent(config.app_insights_resource_id);
  const operation = encodeURIComponent(operationId);
  return (
    "https://portal.azure.com/#blade/AppInsightsExtension/DetailsV2Blade" +
    `/ComponentId/${resource}/DataModel/%7B%22eventId%22%3A%22${operation}%22%7D`
  );
}

/** Build a Foundry deep link for an agent or conversation when configured. */
export function foundryAgentLink(
  config: DrilldownConfig,
  conversationId: string | null | undefined,
): string | null {
  const base = config.foundry_project_url || config.foundry_endpoint;
  if (!base) {
    return null;
  }
  const trimmed = base.replace(/\/$/, "");
  return conversationId
    ? `${trimmed}/threads/${encodeURIComponent(conversationId)}`
    : trimmed;
}
