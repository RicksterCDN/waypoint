const MAX_CLIENT_TELEMETRY_BYTES = 32 * 1024;

export async function action({ request }: { request: Request }) {
  const contentLength = Number(request.headers.get("content-length") ?? "0");
  if (contentLength > MAX_CLIENT_TELEMETRY_BYTES) {
    return new Response(null, { status: 413 });
  }

  const payload = parseTelemetryPayload(await request.text());
  console.log(
    JSON.stringify({
      event: "client_telemetry",
      clientEvent: typeof payload.event === "string" ? payload.event : "unknown",
      details: isRecord(payload.details) ? payload.details : {},
      path: typeof payload.path === "string" ? payload.path : undefined,
      timestamp: typeof payload.timestamp === "string" ? payload.timestamp : undefined,
    }),
  );
  return new Response(null, { status: 204 });
}

export async function loader() {
  return new Response(null, { status: 204 });
}

function parseTelemetryPayload(body: string): Record<string, unknown> {
  if (new TextEncoder().encode(body).length > MAX_CLIENT_TELEMETRY_BYTES) {
    return {};
  }

  try {
    const parsed: unknown = JSON.parse(body || "{}");
    return isRecord(parsed) ? parsed : {};
  } catch {
    return {};
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
