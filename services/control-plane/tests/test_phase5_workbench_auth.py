import time

from fastapi.testclient import TestClient

from obsion.app_server.protocol import PROTOCOL_VERSION, WEBSOCKET_SUBPROTOCOL
from obsion.config import Settings
from obsion.main import create_app


def _login(client: TestClient, settings: Settings):  # type: ignore[no-untyped-def]
    return client.post(
        "/api/v1/auth/session",
        json={"access_token": settings.dev_bearer_token.get_secret_value()},
        headers={"Origin": settings.allowed_origins[0]},
    )


def test_browser_session_is_opaque_http_only_and_shared_by_rest_and_app_server(
    app_settings: Settings,
) -> None:
    with TestClient(create_app(app_settings)) as browser:
        login = _login(browser, app_settings)
        assert login.status_code == 201, login.text
        assert login.json() == {
            "principal_id": str(app_settings.dev_user_id),
            "organization_id": str(app_settings.dev_organization_id),
            "display_name": "Local Administrator",
            "department": "Engineering",
            "roles": ["admin"],
        }
        set_cookie = login.headers["set-cookie"]
        assert app_settings.auth_session_cookie_name in set_cookie
        assert app_settings.dev_bearer_token.get_secret_value() not in set_cookie
        assert "HttpOnly" in set_cookie
        assert "SameSite=strict" in set_cookie
        assert "Path=/api/v1" in set_cookie

        session = browser.get("/api/v1/auth/session")
        assert session.status_code == 200, session.text
        assert session.json() == login.json()
        assert session.headers["cache-control"] == "no-store"

        workspace = browser.post(
            "/api/v1/workspaces",
            json={"name": "Phase 5", "description": "Authenticated Workbench"},
            headers={"Origin": app_settings.allowed_origins[0]},
        )
        assert workspace.status_code == 201, workspace.text

        with browser.websocket_connect(
            "/api/v1/app-server",
            subprotocols=[WEBSOCKET_SUBPROTOCOL],
            headers={"Origin": app_settings.allowed_origins[0]},
        ) as websocket:
            ready = websocket.receive_json()
            assert ready["method"] == "server.ready"
            websocket.send_json(
                {
                    "jsonrpc": "2.0",
                    "id": "phase5-initialize",
                    "method": "server.initialize",
                    "params": {
                        "protocol_version": PROTOCOL_VERSION,
                        "client_name": "phase5-workbench-test",
                        "client_version": "0.1.0",
                    },
                }
            )
            initialized = websocket.receive_json()
            assert initialized["id"] == "phase5-initialize"
            assert initialized["result"]["principal"]["id"] == str(app_settings.dev_user_id)


def test_browser_session_rejects_cross_origin_mutation_and_is_revoked_on_logout(
    app_settings: Settings,
) -> None:
    with TestClient(create_app(app_settings)) as browser:
        login = _login(browser, app_settings)
        assert login.status_code == 201, login.text
        stale_token = browser.cookies.get(app_settings.auth_session_cookie_name)
        assert stale_token

        denied = browser.post(
            "/api/v1/workspaces",
            json={"name": "Cross-site write", "description": "must fail"},
            headers={"Origin": "https://untrusted.example"},
        )
        assert denied.status_code == 403
        assert denied.json()["code"] == "request_origin_denied"

        logout = browser.delete(
            "/api/v1/auth/session",
            headers={"Origin": app_settings.allowed_origins[0]},
        )
        assert logout.status_code == 204, logout.text
        assert logout.headers["cache-control"] == "no-store"
        assert app_settings.auth_session_cookie_name not in browser.cookies

        anonymous = browser.get("/api/v1/auth/session")
        assert anonymous.status_code == 403
        assert anonymous.json()["code"] == "authentication_required"

        revoked = browser.get(
            "/api/v1/auth/session",
            headers={
                "Cookie": f"{app_settings.auth_session_cookie_name}={stale_token}",
            },
        )
        assert revoked.status_code == 403
        assert revoked.json()["code"] == "invalid_token"


def test_browser_login_rejects_invalid_bearer_without_setting_a_cookie(
    app_settings: Settings,
) -> None:
    with TestClient(create_app(app_settings)) as browser:
        response = browser.post(
            "/api/v1/auth/session",
            json={"access_token": "invalid-development-token"},
            headers={"Origin": app_settings.allowed_origins[0]},
        )

    assert response.status_code == 403
    assert response.json()["code"] == "invalid_token"
    assert "set-cookie" not in response.headers


def test_browser_login_rotates_and_revokes_an_existing_session(
    app_settings: Settings,
) -> None:
    with TestClient(create_app(app_settings)) as browser:
        assert _login(browser, app_settings).status_code == 201
        first_token = browser.cookies.get(app_settings.auth_session_cookie_name)
        assert first_token
        assert _login(browser, app_settings).status_code == 201
        second_token = browser.cookies.get(app_settings.auth_session_cookie_name)
        assert second_token and second_token != first_token

        browser.cookies.clear()
        stale = browser.get(
            "/api/v1/auth/session",
            headers={
                "Cookie": f"{app_settings.auth_session_cookie_name}={first_token}",
            },
        )
        assert stale.status_code == 403
        assert stale.json()["code"] == "invalid_token"
        current = browser.get(
            "/api/v1/auth/session",
            headers={
                "Cookie": f"{app_settings.auth_session_cookie_name}={second_token}",
            },
        )
        assert current.status_code == 200, current.text


def test_cookie_authenticated_turn_exposes_the_runtime_timeline_contract(
    app_settings: Settings,
) -> None:
    origin = app_settings.allowed_origins[0]
    with TestClient(create_app(app_settings)) as browser:
        assert _login(browser, app_settings).status_code == 201
        workspace = browser.post(
            "/api/v1/workspaces",
            json={"name": "Workbench timeline", "description": "Phase 5 acceptance"},
            headers={"Origin": origin},
        )
        assert workspace.status_code == 201, workspace.text
        thread = browser.post(
            "/api/v1/threads",
            json={"workspace_id": workspace.json()["id"], "title": "Visible runtime"},
            headers={"Origin": origin},
        )
        assert thread.status_code == 201, thread.text
        turn = browser.post(
            f"/api/v1/threads/{thread.json()['id']}/turns",
            json={"input": "Summarize the authorized knowledge available to this workspace."},
            headers={"Origin": origin},
        )
        assert turn.status_code == 202, turn.text
        run_id = turn.json()["run"]["id"]

        run = turn.json()["run"]
        steps: list[dict] = []
        events: list[dict] = []
        for _ in range(100):
            run_response = browser.get(f"/api/v1/runs/{run_id}")
            steps_response = browser.get(f"/api/v1/runs/{run_id}/steps")
            events_response = browser.get(f"/api/v1/runs/{run_id}/events")
            assert run_response.status_code == 200, run_response.text
            assert steps_response.status_code == 200, steps_response.text
            assert events_response.status_code == 200, events_response.text
            run = run_response.json()
            steps = steps_response.json()
            events = events_response.json()
            if steps and run["status"] in {"COMPLETED", "FAILED", "CANCELLED"}:
                break
            time.sleep(0.05)

        assert steps, "the Runtime panel must have at least one persisted timeline step"
        assert run["plan"]["steps"]
        assert [item["ordinal"] for item in steps] == list(range(1, len(steps) + 1))
        timeline_statuses = {"PENDING", "RUNNING", "COMPLETED", "FAILED", "CANCELLED"}
        assert all(item["status"] in timeline_statuses for item in steps)
        assert [item["run_sequence"] for item in events] == list(range(1, len(events) + 1))
        assert {"step_count", "input_tokens", "output_tokens", "cost_amount"} <= run.keys()
