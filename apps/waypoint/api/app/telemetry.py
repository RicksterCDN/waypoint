"""OpenTelemetry configuration for distributed tracing, metrics, and logging."""

import logging

import opentelemetry._logs as otel_logs
import opentelemetry.exporter.otlp.proto.grpc._log_exporter as log_exporter
import opentelemetry.exporter.otlp.proto.grpc.metric_exporter as metric_exporter
import opentelemetry.exporter.otlp.proto.grpc.trace_exporter as trace_exporter
import opentelemetry.metrics as otel_metrics
import opentelemetry.sdk._logs as otel_sdk_logs
import opentelemetry.sdk._logs.export as otel_logs_export
import opentelemetry.sdk.metrics as otel_sdk_metrics
import opentelemetry.sdk.metrics.export as otel_metrics_export
import opentelemetry.sdk.trace as otel_sdk_trace
import opentelemetry.sdk.trace.export as otel_trace_export
import opentelemetry.trace as otel_trace

logger = logging.getLogger(__name__)


def configure_opentelemetry() -> None:
    """Configure OpenTelemetry with OTLP exporters for Aspire dashboard integration.

    Sets up:
    - Traces: Distributed tracing with span export
    - Metrics: Application metrics with periodic export
    - Logs: Structured logging with log record export

    All exporters use OTLP gRPC protocol, configured via environment variables:
    - OTEL_EXPORTER_OTLP_ENDPOINT: The Aspire dashboard OTLP endpoint
    - OTEL_SERVICE_NAME: Service name for identification
    """
    try:
        # Configure Traces
        otel_trace.set_tracer_provider(otel_sdk_trace.TracerProvider())
        otlp_span_exporter = trace_exporter.OTLPSpanExporter()
        span_processor = otel_trace_export.BatchSpanProcessor(otlp_span_exporter)
        trace_provider = otel_trace.get_tracer_provider()
        if isinstance(trace_provider, otel_sdk_trace.TracerProvider):
            trace_provider.add_span_processor(span_processor)

        # Configure Metrics
        otlp_metric_exporter = metric_exporter.OTLPMetricExporter()
        metric_reader = otel_metrics_export.PeriodicExportingMetricReader(
            otlp_metric_exporter, export_interval_millis=5000
        )
        otel_metrics.set_meter_provider(
            otel_sdk_metrics.MeterProvider(metric_readers=[metric_reader])
        )

        # Configure Logs
        otel_logs.set_logger_provider(otel_sdk_logs.LoggerProvider())
        otlp_log_exporter = log_exporter.OTLPLogExporter()
        log_processor = otel_logs_export.BatchLogRecordProcessor(otlp_log_exporter)
        logs_provider = otel_logs.get_logger_provider()
        if isinstance(logs_provider, otel_sdk_logs.LoggerProvider):
            logs_provider.add_log_record_processor(log_processor)

        # Add OpenTelemetry handler to Python logging
        handler = otel_sdk_logs.LoggingHandler(level=logging.INFO, logger_provider=logs_provider)
        logging.getLogger().addHandler(handler)

        logger.info("OpenTelemetry configured successfully")

    except Exception as e:
        logger.warning(f"Failed to configure OpenTelemetry: {e}")
