/**
 * Browser OpenTelemetry configuration.
 *
 * Sets up distributed tracing from the browser, including:
 * - Automatic fetch instrumentation (traces API calls)
 * - OTLP HTTP export to Aspire dashboard
 * - Trace context propagation to backend
 */

import { trace, SpanStatusCode } from "@opentelemetry/api";
import { W3CTraceContextPropagator } from "@opentelemetry/core";
import { OTLPTraceExporter } from "@opentelemetry/exporter-trace-otlp-http";
import { registerInstrumentations } from "@opentelemetry/instrumentation";
import { FetchInstrumentation } from "@opentelemetry/instrumentation-fetch";
import { Resource } from "@opentelemetry/resources";
import {
  BatchSpanProcessor,
  ConsoleSpanExporter,
  SimpleSpanProcessor,
  WebTracerProvider,
} from "@opentelemetry/sdk-trace-web";
import { ATTR_SERVICE_NAME, ATTR_SERVICE_VERSION } from "@opentelemetry/semantic-conventions";

let isInitialized = false;

export interface TelemetryConfig {
  serviceName: string;
  serviceVersion?: string;
  otlpHttpEndpoint?: string;
  otlpHeaders?: string;
  consoleExport?: boolean;
}

/**
 * Check if telemetry has been initialized.
 */
export function isTelemetryInitialized(): boolean {
  return isInitialized;
}

/**
 * Initialize OpenTelemetry in the browser.
 *
 * @param config - Telemetry configuration from server loader
 */
export function initTelemetry(config: TelemetryConfig): void {
  if (isInitialized) {
    return;
  }

  const { serviceName, serviceVersion = "1.0.0", otlpHttpEndpoint, otlpHeaders, consoleExport } = config;

  // Create resource with service identification
  const resource = new Resource({
    [ATTR_SERVICE_NAME]: serviceName,
    [ATTR_SERVICE_VERSION]: serviceVersion,
  });

  // Create trace provider
  const provider = new WebTracerProvider({ resource });

  // Add console exporter for development debugging
  if (consoleExport) {
    provider.addSpanProcessor(new SimpleSpanProcessor(new ConsoleSpanExporter()));
  }

  // Add OTLP exporter for Aspire dashboard
  if (otlpHttpEndpoint) {
    const headers: Record<string, string> = {};
    const url = new URL(otlpHttpEndpoint, window.location.origin).toString();

    // Parse OTLP headers (format: "key1=value1,key2=value2")
    if (otlpHeaders) {
      otlpHeaders.split(",").forEach((header) => {
        const [key, value] = header.split("=");
        if (key && value) {
          headers[key.trim()] = value.trim();
        }
      });
    }

    const otlpExporter = new OTLPTraceExporter({
      url,
      headers,
    });

    provider.addSpanProcessor(new BatchSpanProcessor(otlpExporter));
  }

  // Register provider globally
  provider.register({
    propagator: new W3CTraceContextPropagator(),
  });

  // Register fetch instrumentation for automatic API call tracing
  const sameOriginApiPattern = new RegExp(
    `^${escapeRegExp(window.location.origin)}/api`,
  );
  registerInstrumentations({
    instrumentations: [
      new FetchInstrumentation({
        ignoreUrls: [
          /\.(js|css|png|jpg|jpeg|gif|svg|ico|woff|woff2)$/,
          /\/client-telemetry$/,
          /\/otlp\/v1\/traces$/,
          /\/v1\/traces$/,
        ],
        propagateTraceHeaderCorsUrls: [/^\/api/, sameOriginApiPattern],
      }),
    ],
  });

  isInitialized = true;
  console.log(`OpenTelemetry initialized for ${serviceName}`);
}

/**
 * Get a tracer for creating manual spans.
 *
 * @param name - Tracer name (usually component or module name)
 */
export function getTracer(name: string = "web") {
  return trace.getTracer(name);
}

/**
 * Create a traced function wrapper.
 *
 * Usage:
 *   const tracedFetch = traced("fetchItems", async () => {
 *     return await fetch("/api/items");
 *   });
 */
export function traced<T>(name: string, fn: () => Promise<T>): Promise<T> {
  const tracer = getTracer();
  return tracer.startActiveSpan(name, async (span) => {
    try {
      const result = await fn();
      span.setStatus({ code: SpanStatusCode.OK });
      return result;
    } catch (error) {
      span.setStatus({
        code: SpanStatusCode.ERROR,
        message: error instanceof Error ? error.message : "Unknown error",
      });
      throw error;
    } finally {
      span.end();
    }
  });
}

function escapeRegExp(value: string) {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}
