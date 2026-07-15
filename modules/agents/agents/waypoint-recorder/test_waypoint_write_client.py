"""Unit tests for the Waypoint write client's run-lifecycle PATCH support.

Covers `update_run` / `_patch`: it sends only the provided fields, degrades to a
no-op when the PATCH route isn't deployed yet (HTTP 404/405), and still raises on
other server errors. Run directly (``python test_waypoint_write_client.py``) or via
pytest.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import waypoint_write_client as m

_CONFIG = m.WaypointWriteConfig(
    api_base_url="https://waypoint.example.com",
    api_scope=None,
    api_key="test-key",
    verify_ssl=True,
    agent_name="waypoint-recorder",
    app_insights_operation_id=None,
)


def _response(status: int, *, content: bytes = b"", body: dict | None = None) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.is_error = status >= 400
    resp.content = content
    resp.text = ""
    resp.json = lambda: (body or {})
    return resp


def _client() -> m.WaypointWriteClient:
    return m.WaypointWriteClient(_CONFIG)


def test_update_run_noops_when_patch_route_missing() -> None:
    for status in (404, 405):
        with patch.object(m.httpx, "Client") as client_cls:
            client_cls.return_value.__enter__.return_value.patch.return_value = _response(status)
            assert _client().update_run("run-1", status="completed") is None


def test_update_run_raises_on_server_error() -> None:
    with patch.object(m.httpx, "Client") as client_cls:
        client_cls.return_value.__enter__.return_value.patch.return_value = _response(500)
        try:
            _client().update_run("run-1", status="completed")
        except RuntimeError as exc:
            assert "PATCH" in str(exc)
        else:
            raise AssertionError("HTTP 500 should raise")


def test_update_run_sends_only_set_fields() -> None:
    captured: dict = {}

    def _capture(url, headers=None, json=None):  # noqa: A002 - mirrors httpx signature
        captured.update(json or {})
        return _response(200, content=b"{}", body={"id": "run-1"})

    with patch.object(m.httpx, "Client") as client_cls:
        client_cls.return_value.__enter__.return_value.patch.side_effect = _capture
        _client().update_run("run-1", status="completed", summary="done")

    assert set(captured.keys()) == {"status", "summary"}
    assert captured["status"] == "completed"


def test_open_run_includes_idempotency_key_when_provided() -> None:
    captured: dict = {}

    def _capture(url, headers=None, json=None):  # noqa: A002 - mirrors httpx signature
        captured.update(json or {})
        return _response(201, content=b"{}", body={"id": "run-1"})

    with patch.object(m.httpx, "Client") as client_cls:
        client_cls.return_value.__enter__.return_value.post.side_effect = _capture
        _client().open_run("assurance:INV-1", status="running", idempotency_key="assurance:INV-1:op")

    assert captured["idempotency_key"] == "assurance:INV-1:op"
    assert captured["status"] == "running"


def test_create_case_includes_idempotency_key_when_provided() -> None:
    captured: dict = {}

    def _capture(url, headers=None, json=None):  # noqa: A002 - mirrors httpx signature
        captured.update(json or {})
        return _response(201, content=b"{}", body={"id": "case-1"})

    with patch.object(m.httpx, "Client") as client_cls:
        client_cls.return_value.__enter__.return_value.post.side_effect = _capture
        _client().create_case("INV-1", idempotency_key="assurance-case:INV-1:op")

    assert captured["idempotency_key"] == "assurance-case:INV-1:op"


def test_assurance_key_helpers_match_scheme() -> None:
    assert m.assurance_run_key("INV-1", "op") == "assurance:INV-1:op"
    assert m.assurance_case_key("INV-1", "op") == "assurance-case:INV-1:op"


def test_open_assurance_run_patches_status_when_run_preexists_as_pending() -> None:
    patches: list[dict] = []

    def _post(url, headers=None, json=None):  # noqa: A002 - mirrors httpx signature
        if "/api/cases" in url:
            return _response(201, content=b"{}", body={"id": "case-1"})
        # Simulate an already-enrolled run: an idempotent re-open returns it unchanged
        # (still "pending"), so open_assurance_run must PATCH it up to "running".
        return _response(201, content=b"{}", body={"id": "run-1", "status": "pending"})

    def _patch(url, headers=None, json=None):  # noqa: A002 - mirrors httpx signature
        patches.append(json or {})
        return _response(200, content=b"{}", body={"id": "run-1", "status": "running"})

    with patch.object(m.httpx, "Client") as client_cls:
        inst = client_cls.return_value.__enter__.return_value
        inst.post.side_effect = _post
        inst.patch.side_effect = _patch
        result = _client().open_assurance_run("INV-1", operation_id="op", status="running")

    assert result["run_id"] == "run-1"
    assert result["case_id"] == "case-1"
    assert result["run_key"] == "assurance:INV-1:op"
    assert result["case_key"] == "assurance-case:INV-1:op"
    assert len(patches) == 1 and patches[0]["status"] == "running"


def test_open_assurance_run_skips_patch_when_status_matches() -> None:
    patches: list[str] = []

    def _post(url, headers=None, json=None):  # noqa: A002 - mirrors httpx signature
        if "/api/cases" in url:
            return _response(201, content=b"{}", body={"id": "case-1"})
        return _response(201, content=b"{}", body={"id": "run-1", "status": "running"})

    def _patch(url, headers=None, json=None):  # noqa: A002 - mirrors httpx signature
        patches.append(url)
        return _response(200, content=b"{}", body={"id": "run-1"})

    with patch.object(m.httpx, "Client") as client_cls:
        inst = client_cls.return_value.__enter__.return_value
        inst.post.side_effect = _post
        inst.patch.side_effect = _patch
        _client().open_assurance_run("INV-1", operation_id="op", status="running")

    assert patches == []


if __name__ == "__main__":
    test_update_run_noops_when_patch_route_missing()
    test_update_run_raises_on_server_error()
    test_update_run_sends_only_set_fields()
    test_open_run_includes_idempotency_key_when_provided()
    test_create_case_includes_idempotency_key_when_provided()
    test_assurance_key_helpers_match_scheme()
    test_open_assurance_run_patches_status_when_run_preexists_as_pending()
    test_open_assurance_run_skips_patch_when_status_matches()
    print("all waypoint write client tests passed")
