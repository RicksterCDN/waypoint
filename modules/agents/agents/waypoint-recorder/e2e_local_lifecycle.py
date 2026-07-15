#!/usr/bin/env python3
"""Drive the recorder's Waypoint write path end-to-end against a LOCAL Waypoint API.

This is a developer harness (NOT part of the deploy) for watching the run lifecycle
pending -> running -> completed on Waypoint's Activity page WITHOUT standing up the full
Foundry hosted-agent/orchestrator stack. It imports the same tool functions the recorder
exposes to the model and calls them directly, in the exact order the orchestrator would:

    1. waypoint_enroll_batch(...)     -> every invoice appears as "pending" up front
    2. waypoint_open_run(ref, op, "running") for one invoice at a time
    3. waypoint_record_assurance(...) -> that invoice PATCHes running -> completed

Because every stage threads the SAME per-invoice operation_id, all three resolve to the
same run + case idempotency keys (assurance:{ref}:{op} / assurance-case:{ref}:{op}), so
nothing is duplicated — the harness asserts the run id from the early-open equals the run
id from the final write.

Run it from this directory inside the recorder venv:

    cd agents/waypoint-recorder
    python -m venv .venv && source .venv/bin/activate      # if not already
    pip install -r requirements.txt
    export WAYPOINT_API_BASE_URL=http://localhost:8010
    # auth: with the local Waypoint's APP_LOCAL_AUTH_ENABLED=true, unauthenticated writes
    # resolve to operator@waypoint.local (writer) — NO key/scope needed locally, just the
    # base URL above. Otherwise pick what your deployment accepts for the writer role:
    #   export WAYPOINT_API_KEY=<local writer key>          # -> sent as x-api-key
    #   export WAYPOINT_API_SCOPE=<entra scope>             # -> DefaultAzureCredential (az login)
    python e2e_local_lifecycle.py                           # 3 sample invoices
    python e2e_local_lifecycle.py INV-A INV-B --pause 4     # custom refs, 4s between transitions

Env vars (read by WaypointWriteConfig.try_from_env, also honored from a local .env):
    WAYPOINT_API_BASE_URL   required, e.g. http://localhost:8010
    WAYPOINT_API_KEY        optional; if set, sent as x-api-key. Not needed when the local
                            Waypoint has APP_LOCAL_AUTH_ENABLED=true (writes resolve to the
                            operator@waypoint.local writer identity).
    WAYPOINT_API_SCOPE      optional; if set (and no key), Entra token via DefaultAzureCredential
    WAYPOINT_API_VERIFY_SSL optional; "false" to skip TLS verify (not needed for http://)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid

from waypoint_write_client import WaypointWriteConfig
from waypoint_write_tools import (
    is_waypoint_configured,
    waypoint_enroll_batch,
    waypoint_open_run,
    waypoint_record_assurance,
)

DEFAULT_INVOICES = ["INV-2026-08034", "INV-2026-08120", "INV-2026-08205"]

# A small rotation so the recommendations/drafts aren't all identical on the board.
_DECISIONS = ["recover", "escalate", "review"]


def _pp(label: str, raw: str) -> dict:
    """Print a tool's JSON result compactly and return it parsed."""
    try:
        parsed = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        print(f"  {label}: <non-JSON> {raw!r}")
        return {}
    ok = parsed.get("ok")
    flag = "ok" if ok else ("FAIL" if ok is False else "?")
    print(f"  {label}: [{flag}] {json.dumps(parsed, ensure_ascii=False)}")
    return parsed


def _record_payload(invoice_ref: str, op_id: str, decision: str) -> str:
    """Build a realistic final evidence bundle for one invoice.

    Includes fanout (so experts_consulted + the recommendation's expert_evidence are
    populated) and a draft (so create_draft fires). operation_id ties this write back to
    the run/case opened in the earlier stages.
    """
    bundle = {
        "invoice_id": invoice_ref,
        "operation_id": op_id,
        "decision": decision,
        "reasoning": (
            f"Local e2e harness decision for {invoice_ref}: fused evidence across planes "
            f"supports '{decision}'."
        ),
        "confidence": 0.82,
        "money_at_risk": 4200.50,
        "summary": f"Assurance review for {invoice_ref} ({decision}).",
        "classification": "standard",
        "evidence_ids": [f"ev-{invoice_ref}-1", f"ev-{invoice_ref}-2"],
        "proposed_next_actions": ["notify_supplier", "hold_payment"],
        "fanout": [
            {
                "agent": "operations-data-expert",
                "plane": "fabriciq",
                "summary": "PO/receipt reconciliation shows a duplicated service line.",
                "evidence": [
                    {
                        "claim": "Service line billed twice across two POs.",
                        "supports": decision,
                        "source_ref": f"fabric://po/{invoice_ref}",
                        "classification": "standard",
                        "confidence": 0.86,
                    }
                ],
            },
            {
                "agent": "contract-policy-expert",
                "plane": "foundryiq",
                "summary": "Master services agreement caps this fee category.",
                "evidence": [
                    {
                        "claim": "Billed rate exceeds the contracted cap.",
                        "supports": decision,
                        "source_ref": f"foundry://msa/{invoice_ref}",
                        "classification": "standard",
                        "confidence": 0.78,
                    }
                ],
            },
        ],
        "draft": {
            "draft_type": "supplier_dispute",
            "title": f"Supplier dispute — {invoice_ref}",
            "body": (
                f"Re: {invoice_ref}. Our assurance review ({decision}) identified a "
                "duplicated service line billed above the contracted cap. Please issue a "
                "corrected invoice."
            ),
        },
    }
    return json.dumps(bundle, ensure_ascii=False)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("invoices", nargs="*", default=None, help="invoice refs (default: 3 samples)")
    parser.add_argument("--pause", type=float, default=2.0, help="seconds between transitions so you can watch the board (default 2)")
    parser.add_argument("--skip-enroll", action="store_true", help="skip the batch pending-enroll (invoices then first appear as running)")
    args = parser.parse_args(argv)

    invoices = args.invoices or list(DEFAULT_INVOICES)

    if not is_waypoint_configured():
        print("ERROR: WAYPOINT_API_BASE_URL is not set. Export it (e.g. http://localhost:8010) first.", file=sys.stderr)
        return 2

    cfg = WaypointWriteConfig.try_from_env()
    assert cfg is not None  # is_waypoint_configured() already guaranteed this
    auth = "x-api-key" if cfg.api_key else (f"entra scope={cfg.api_scope}" if cfg.api_scope else "none (local APP_LOCAL_AUTH_ENABLED writer)")
    print(f"Waypoint base : {cfg.api_base_url}")
    print(f"Auth mode     : {auth}")
    print(f"Invoices      : {', '.join(invoices)}")
    print(f"Pause         : {args.pause}s between transitions\n")

    # One shared, stable operation id per invoice — reused at every stage so the run/case
    # idempotency keys line up (this is exactly what the orchestrator does in-process).
    op_ids = {ref: uuid.uuid4().hex for ref in invoices}
    opened_run_ids: dict[str, str | None] = {}
    failures = 0

    # ── Phase 1: enroll the whole batch as pending ───────────────────────────────
    if not args.skip_enroll:
        print("== Phase 1: enroll batch as PENDING ==")
        batch = [{"invoice_id": ref, "operation_id": op_ids[ref]} for ref in invoices]
        enroll = _pp("enroll_batch", waypoint_enroll_batch(json.dumps(batch)))
        if enroll.get("ok") is False:
            failures += 1
        print("  -> Activity page should now show all invoices PENDING.")
        time.sleep(args.pause)
    else:
        print("== Phase 1 skipped (--skip-enroll): invoices will first appear as RUNNING ==")

    # ── Phase 2 + 3: process one invoice at a time (running -> completed) ─────────
    print("\n== Phase 2+3: process invoices one-by-one (RUNNING -> COMPLETED) ==")
    for i, ref in enumerate(invoices):
        op = op_ids[ref]
        decision = _DECISIONS[i % len(_DECISIONS)]
        print(f"\n[{ref}] op={op} decision={decision}")

        opened = _pp("open_run(running)", waypoint_open_run(ref, op, "running"))
        if opened.get("ok") is False:
            failures += 1
        opened_run_ids[ref] = (opened.get("correlation") or {}).get("waypoint_run_id")
        print(f"  -> {ref} should now be RUNNING (the rest still PENDING).")
        time.sleep(args.pause)

        recorded = _pp("record_assurance(completed)", waypoint_record_assurance(_record_payload(ref, op, decision)))
        if recorded.get("ok") is False:
            failures += 1
        final_run_id = (recorded.get("correlation") or {}).get("waypoint_run_id")

        # The whole point of the shared idempotency key: the early-open and the final
        # write must land on the SAME run row (no duplicate active run).
        early = opened_run_ids[ref]
        if early and final_run_id and early != final_run_id:
            print(f"  !! DUPLICATE RUN: early-open run_id={early} != final run_id={final_run_id}")
            failures += 1
        elif early and final_run_id:
            print(f"  -> same run reused (id={final_run_id}); now COMPLETED. No duplicate. ✔")
        print(f"  -> {ref} should now be COMPLETED.")
        if i < len(invoices) - 1:
            time.sleep(args.pause)

    print("\n== Done ==")
    if failures:
        print(f"Completed with {failures} failing tool call(s) — check the output above and the Waypoint logs.")
        return 1
    print("All stages returned ok. Watch pending -> running -> completed on the Activity page.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
