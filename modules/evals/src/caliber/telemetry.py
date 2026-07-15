from __future__ import annotations

from datetime import UTC, datetime, timedelta

DEFAULT_STREAMS = (
    "assurance_orchestrator.run",
    "contract_policy_expert.evidence",
    "foundryiq_expert.evidence",
    "fabriciq_expert.evidence",
    "waypoint_recorder.write_plan",
    "caliber.optimizer.candidate",
)


def build_telemetry_backfill_plan(
    *,
    hours: int = 48,
    environment: str = "demo",
    app_insights_resource_id: str | None = None,
    log_analytics_workspace_id: str | None = None,
    streams: list[str] | None = None,
) -> dict[str, object]:
    """Build the deterministic 48-hour telemetry backfill plan from APP_MIGRATION."""

    if hours <= 0:
        raise ValueError("hours must be greater than zero.")

    end = datetime.now(UTC).replace(microsecond=0)
    start = end - timedelta(hours=hours)
    selected_streams = streams or list(DEFAULT_STREAMS)

    return {
        "environment": environment,
        "window": {
            "start": start.isoformat().replace("+00:00", "Z"),
            "end": end.isoformat().replace("+00:00", "Z"),
            "hours": hours,
        },
        "targets": {
            "app_insights_resource_id": app_insights_resource_id
            or "${APP_APP_INSIGHTS_RESOURCE_ID}",
            "log_analytics_workspace_id": log_analytics_workspace_id
            or "${LOG_ANALYTICS_WORKSPACE_ID}",
        },
        "streams": [
            {
                "name": stream,
                "cadence_minutes": 15,
                "expected_events": (hours * 60) // 15,
            }
            for stream in selected_streams
        ],
        "validation": [
            "Confirm traces appear for all selected streams in App Insights.",
            "Confirm Log Analytics has at least one event per stream per hour.",
            "Confirm Waypoint run/case IDs are synthetic and do not reference tenant data.",
            "Confirm dashboards cover the full requested backfill window.",
        ],
        "artifact_policy": {
            "commit_generated_events": False,
            "safe_output_directory": "outputs/telemetry",
        },
    }
