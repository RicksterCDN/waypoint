"""Drive the deterministic workflow (fan-out -> synthesize -> write-plan) in-process.

Uses the REAL hosted prompt-experts (live WebIQ / FoundryIQ / FabricIQ / WorkIQ) for the
fan-out, but a stub Waypoint reader for invoice discovery/context so it does not need a
running Waypoint. WorkIQ may fail; the workflow tolerates it and still synthesizes a
judgement and prepares a (read-only) Waypoint write plan.

Env required:
  ASSURANCE_ORCHESTRATOR_EXPERT_INVOCATION_MODE=prompt
  AZURE_AI_PROJECT_ENDPOINT=<foundry project endpoint>
  (optional) ASSURANCE_ORCHESTRATOR_EXPERT_MODEL=<model deployment>
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from expert_clients import FoundryPromptExpertClient  # noqa: E402
from assurance_workflow import run_assurance_orchestrator_invoice_assurance  # noqa: E402

INVOICE_ID = sys.argv[1] if len(sys.argv) > 1 else "INV-2026-08034"


class StubReader:
    """Minimal read-only Waypoint reader: one real-looking invoice target + context."""

    def get_work(self) -> list[dict[str, Any]]:
        return [
            {
                "invoice_id": INVOICE_ID,
                "finding_id": "finding-live-001",
                "case_id": "case-live-001",
                "summary": "Capacity premium on invoice requires external corroboration.",
            }
        ]

    def get_action_types(self) -> list[dict[str, Any]]:
        return [{"id": "recommend_review"}, {"id": "escalate_quality"}]

    def get_runs(self) -> list[dict[str, Any]]:
        return []

    def get_invoice_context(self, invoice_id: str) -> dict[str, Any]:
        return {
            "invoice": {
                "id": invoice_id,
                "invoice_number": invoice_id,
                "supplier": "Atlas Regional Manufacturing",
                "total_amount": "48250.00",
                "currency": "USD",
                "lines": [
                    {"description": "Contract manufacturing capacity premium", "amount": "48250.00"}
                ],
            },
            "contract": {"supplier": "Atlas Regional Manufacturing"},
        }


def main() -> int:
    expert_client = FoundryPromptExpertClient.from_env()
    if expert_client is None:
        print(
            "FoundryPromptExpertClient not enabled. Set "
            "ASSURANCE_ORCHESTRATOR_EXPERT_INVOCATION_MODE=prompt and a project endpoint.",
            file=sys.stderr,
        )
        return 2

    result = run_assurance_orchestrator_invoice_assurance(
        invoice_id=INVOICE_ID,
        client=StubReader(),
        expert_client=expert_client,
    )

    # Persist full result for review.
    out_path = Path("/tmp/stages_live_webiq_out.json")
    out_path.write_text(json.dumps(result, indent=2), encoding="utf-8")

    validators = result.get("validators", [])
    write_plan = result.get("write_plan", {}) or {}
    print("=" * 72)
    print(f"overall status         : {result.get('status')}")
    print(f"side_effects_performed : {result.get('side_effects_performed')}")
    print(f"targets                : {len(result.get('targets', []))}")
    print(f"validators (fan-out)   : {len(validators)}")
    for v in validators:
        print(
            f"   - {v.get('validator_id'):10s} status={v.get('status'):10s} "
            f"conf={v.get('confidence')} path={v.get('execution_path')} "
            f"summary={(v.get('summary') or '')[:70]!r}"
        )
    judgements = result.get("judgements") or result.get("judgement") or []
    print(f"judgements (synthesize): {len(judgements) if isinstance(judgements, list) else 'see json'}")
    payloads = write_plan.get("future_payloads", [])
    print(f"write_plan.future_payloads: {len(payloads)}")
    print(f"full result saved to   : {out_path}")
    print("=" * 72)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
