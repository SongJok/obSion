import time
from uuid import uuid4

from fastapi.testclient import TestClient

from obsion.capabilities.engineering import normalize_response
from obsion.harness.planner import Planner
from obsion.security.auth import get_principal
from obsion.security.identity import Principal

_CONTROLLER = """
from order_service import OrderService

router = type("Router", (), {})()
service = OrderService()

@router.post("/order/create")
def create_order():
    return service.create()
"""

_SERVICE = """
from coupon_service import CouponService
from order_mapper import OrderMapper

class OrderService:
    def __init__(self):
        self.coupon = CouponService()
        self.mapper = OrderMapper()

    def create(self):
        self.coupon.apply()
        return self.mapper.insert()
"""

_COUPON = """
class CouponService:
    def apply(self):
        return True
"""

_MAPPER = """
class OrderMapper:
    def insert(self):
        return self._execute("INSERT INTO order_table (id) VALUES (1)")

    def find(self):
        return self._execute("SELECT id FROM order_table WHERE id = 1")

    def _execute(self, sql: str):
        return sql
"""

_JAVA = """
public class PaymentClient {
    public void charge() {
        return;
    }
}
"""


def _wait_terminal(client: TestClient, run_id: str) -> dict:
    for _ in range(100):
        response = client.get(f"/api/v1/runs/{run_id}")
        assert response.status_code == 200, response.text
        run = response.json()
        if run["status"] in {"COMPLETED", "FAILED", "CANCELLED"}:
            return run
        time.sleep(0.05)
    raise AssertionError(f"Run did not reach a terminal state: {run}")


def _index_sample(
    client: TestClient, *, name: str = "payment-service", acl: str | None = None
) -> dict:
    created = client.post(
        "/api/v1/code/repositories",
        json={
            "name": name,
            "classification": "INTERNAL",
            "acl": {"organization": True} if acl is None else None,
        }
        if acl is None
        else {
            "name": name,
            "classification": "INTERNAL",
            "acl": __import__("json").loads(acl),
        },
    )
    assert created.status_code == 201, created.text
    indexed = client.post(
        f"/api/v1/code/repositories/{created.json()['id']}/snapshots",
        json={
            "commit_id": "abc1234def",
            "files": [
                {"path": "src/order_controller.py", "content": _CONTROLLER},
                {"path": "src/order_service.py", "content": _SERVICE},
                {"path": "src/coupon_service.py", "content": _COUPON},
                {"path": "src/order_mapper.py", "content": _MAPPER},
                {"path": "src/PaymentClient.java", "content": _JAVA},
            ],
        },
    )
    assert indexed.status_code == 201, indexed.text
    return indexed.json()


def test_python_ast_extracts_api_call_chain_and_sql_tables() -> None:
    from obsion.code_intelligence.parsers import parse_source_file
    from obsion.domain.enums import CodeRelation, CodeSymbolKind

    parsed = parse_source_file("src/order_controller.py", _CONTROLLER.encode())
    names = {symbol.qualified_name for symbol in parsed.symbols}
    assert any(item.endswith("create_order") for item in names)
    assert any(symbol.kind is CodeSymbolKind.API for symbol in parsed.symbols)
    assert any(edge.relation is CodeRelation.EXPOSES_API for edge in parsed.edges)

    mapper = parse_source_file("src/order_mapper.py", _MAPPER.encode())
    relations = {edge.relation for edge in mapper.edges}
    assert CodeRelation.WRITES_TABLE in relations
    assert CodeRelation.READS_TABLE in relations


def test_git_blame_is_part_of_read_only_engineering_contract() -> None:
    normalized = normalize_response(
        {
            "items": [
                {
                    "timestamp": "2026-08-29T01:00:00Z",
                    "repository": "obsion/control-plane",
                    "commit_id": "abcdef1234567",
                    "title": "timeout",
                }
            ]
        },
        operation="git.blame",
        default_repository="obsion/control-plane",
        default_environment="production",
    )
    assert normalized["operation"] == "git.blame"
    assert normalized["items"][0]["commit_id"] == "abcdef1234567"


def test_engineering_plan_uses_code_graph_capabilities() -> None:
    plan = Planner().create(
        {"route": "ENGINEERING", "question": "POST /order/create 的调用链是什么"},
        available_capabilities=frozenset({"code.symbol", "code.reference", "code.callers"}),
    )
    assert [step.capability for step in plan.steps] == [
        "code.symbol",
        "code.reference",
        "code.callers",
    ]
    assert "code.search" not in {step.capability for step in plan.steps}


def test_code_graph_indexes_symbols_callers_and_sql(client: TestClient) -> None:
    _index_sample(client)
    symbols = client.post(
        "/api/v1/code/symbols/search",
        json={"query": "OrderService", "limit": 20},
    )
    assert symbols.status_code == 200, symbols.text
    names = {item["qualified_name"] for item in symbols.json()}
    assert any(name.endswith("OrderService") for name in names)

    api = client.post("/api/v1/code/symbols/search", json={"query": "/order/create", "limit": 20})
    assert api.status_code == 200, api.text
    assert api.json()
    assert any(item["kind"] == "API" for item in api.json())

    tables = client.post("/api/v1/code/symbols/search", json={"query": "order_table", "limit": 20})
    assert tables.status_code == 200, tables.text
    assert any(item["kind"] == "TABLE" for item in tables.json())

    java = client.post("/api/v1/code/symbols/search", json={"query": "PaymentClient", "limit": 20})
    assert java.status_code == 200, java.text
    assert java.json()


def test_code_graph_acl_zero_recall_for_denied_principal(client: TestClient) -> None:
    other_user_id = uuid4()
    created = client.post(
        "/api/v1/code/repositories",
        json={
            "name": "restricted-payments",
            "classification": "INTERNAL",
            "acl": {
                "users": [str(client.app.state.settings.dev_user_id)],
                "deny_users": [str(other_user_id)],
            },
        },
    )
    assert created.status_code == 201, created.text
    indexed = client.post(
        f"/api/v1/code/repositories/{created.json()['id']}/snapshots",
        json={
            "commit_id": "def5678abc",
            "files": [{"path": "src/secret.py", "content": "def hidden():\n    return 1\n"}],
        },
    )
    assert indexed.status_code == 201, indexed.text

    restricted = Principal(
        id=other_user_id,
        organization_id=client.app.state.settings.dev_organization_id,
        external_id="phase21-restricted",
        display_name="Restricted reader",
        permissions=frozenset({"code.read.internal"}),
    )
    client.app.dependency_overrides[get_principal] = lambda: restricted
    try:
        search = client.post("/api/v1/code/symbols/search", json={"query": "hidden", "limit": 20})
        assert search.status_code == 200, search.text
        assert search.json() == []
    finally:
        client.app.dependency_overrides.pop(get_principal, None)


def test_identical_snapshot_is_incremental_noop(client: TestClient) -> None:
    first = _index_sample(client, name="idempotent-service")
    second = client.post(
        f"/api/v1/code/repositories/{first['repository']['id']}/snapshots",
        json={
            "commit_id": "abc1234def",
            "files": [
                {"path": "src/order_controller.py", "content": _CONTROLLER},
                {"path": "src/order_service.py", "content": _SERVICE},
                {"path": "src/coupon_service.py", "content": _COUPON},
                {"path": "src/order_mapper.py", "content": _MAPPER},
                {"path": "src/PaymentClient.java", "content": _JAVA},
            ],
        },
    )
    assert second.status_code == 201, second.text
    assert second.json()["snapshot"]["id"] == first["snapshot"]["id"]
    assert second.json()["snapshot"]["ordinal"] == first["snapshot"]["ordinal"]


def test_changed_commit_creates_new_snapshot(client: TestClient) -> None:
    first = _index_sample(client, name="evolving-service")
    second = client.post(
        f"/api/v1/code/repositories/{first['repository']['id']}/snapshots",
        json={
            "commit_id": "fff9999aaa",
            "files": [
                {"path": "src/order_controller.py", "content": _CONTROLLER},
                {"path": "src/order_service.py", "content": _SERVICE + "\n"},
                {"path": "src/coupon_service.py", "content": _COUPON},
                {"path": "src/order_mapper.py", "content": _MAPPER},
                {"path": "src/PaymentClient.java", "content": _JAVA},
            ],
        },
    )
    assert second.status_code == 201, second.text
    assert second.json()["snapshot"]["id"] != first["snapshot"]["id"]
    assert second.json()["snapshot"]["ordinal"] == first["snapshot"]["ordinal"] + 1
    assert second.json()["snapshot"]["metadata_json"]["reused_files"] >= 1


def test_engineering_agent_answers_code_architecture_with_evidence(client: TestClient) -> None:
    _index_sample(client, name="payment-service")
    workspace = client.post(
        "/api/v1/workspaces",
        json={"name": "Code architecture", "description": "Phase 21 EngineeringAgent"},
    )
    assert workspace.status_code == 201, workspace.text
    thread = client.post(
        "/api/v1/threads",
        json={"workspace_id": workspace.json()["id"], "title": "Order create call chain"},
    )
    assert thread.status_code == 201, thread.text
    created = client.post(
        f"/api/v1/threads/{thread.json()['id']}/turns",
        json={"input": "代码里 POST /order/create 的调用链是什么？"},
    )
    assert created.status_code == 202, created.text
    run = _wait_terminal(client, created.json()["run"]["id"])
    assert run["status"] == "COMPLETED", f"{run.get('error_code')}: {run.get('error_message')}"
    assert run["intent"]["route"] == "ENGINEERING"
    assert run["intent"]["agent"] == "engineering-agent"
    assert run["intent"]["skill"] == "code-architecture"
    capabilities = [step["capability"] for step in run["plan"]["steps"]]
    assert "code.symbol" in capabilities
    artifacts = client.get(f"/api/v1/runs/{run['id']}/artifacts").json()
    answer = next(item["inline_content"] for item in artifacts if item["kind"] == "TEXT")
    assert answer["citations"]
    assert "### 代码证据" in answer["markdown"]
    assert {item["kind"] for item in artifacts} >= {"TEXT", "CODE", "REPORT"}
    claims = client.get(f"/api/v1/runs/{run['id']}/claims").json()
    assert claims and claims[0]["evidence_ids"]
    evidence = client.get(f"/api/v1/runs/{run['id']}/evidence").json()
    assert evidence and evidence[0]["evidence_type"] == "CODE"
