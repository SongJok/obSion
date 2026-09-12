import os
from collections.abc import Callable, Iterator
from uuid import uuid4

import pytest
from conftest import TEST_BEARER_TOKEN
from fastapi.testclient import TestClient

from obsion.common.text import lexical_terms
from obsion.config import Settings
from obsion.main import create_app
from obsion.security.auth import get_principal
from obsion.security.identity import Principal


def _reader_dependency(reader: Principal) -> Callable[[], Principal]:
    return lambda: reader


@pytest.fixture(params=["sqlite", "postgresql"])
def knowledge_client(
    app_settings: Settings, request: pytest.FixtureRequest
) -> Iterator[TestClient]:
    settings = app_settings
    if request.param == "postgresql":
        if os.getenv("OBSION_RUN_POSTGRES_TESTS") != "1":
            pytest.skip("Explicit disposable PostgreSQL validation required")
        url = os.environ["OBSION_DATABASE_URL"]
        assert url.startswith("postgresql+asyncpg://")
        settings = settings.model_copy(update={"database_url": url})
    with TestClient(
        create_app(settings), headers={"Authorization": f"Bearer {TEST_BEARER_TOKEN}"}
    ) as client:
        yield client


def _ingest(client: TestClient, source: str, external_id: str, content: str, acl: str) -> dict:
    response = client.post(
        "/api/v1/knowledge/documents",
        files={"file": ("policy.md", content.encode(), "text/markdown")},
        data={
            "source": source,
            "external_id": external_id,
            "title": "中文制度",
            "classification": "INTERNAL",
            "acl": acl,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def _search(client: TestClient, source: str, question: str) -> list[dict]:
    response = client.post("/api/v1/knowledge/search", json={"query": question, "limit": 20})
    assert response.status_code == 200, response.text
    # Other tests may have authorized documents in the disposable PostgreSQL.
    # Assert visibility of this fixture's source, without inventing an API filter.
    return [hit for hit in response.json() if hit["source"] == source]


def test_chinese_natural_question_finds_current_authorized_content(
    knowledge_client: TestClient,
) -> None:
    client = knowledge_client
    source = f"chinese-{uuid4()}"
    _ingest(
        client,
        source,
        "reimburse",
        "报销制度：每人每月交通报销上限为500元。",
        '{"organization": true}',
    )
    hits = _search(client, source, "请问报销制度规定的交通报销上限是多少？")
    assert hits and "500元" in hits[0]["content"]
    _ingest(
        client,
        source,
        "reimburse",
        "报销制度：每人每月交通报销上限改为800元。",
        '{"organization": true}',
    )
    hits = _search(client, source, "交通报销上限是多少？")
    assert hits and all("500元" not in hit["content"] for hit in hits)
    assert "800元" in hits[0]["content"]


def test_chinese_retrieval_preserves_acl_denial_and_tenant_isolation(
    knowledge_client: TestClient,
) -> None:
    client = knowledge_client
    source = f"chinese-acl-{uuid4()}"
    reader_id = uuid4()
    _ingest(client, source, "secret", "奖金制度：项目奖金为99999元。", '{"organization": true}')
    assert _search(client, source, "项目奖金制度有哪些？")
    # The same-content ACL update must immediately revoke Chinese retrieval too.
    _ingest(
        client,
        source,
        "secret",
        "奖金制度：项目奖金为99999元。",
        '{"organization": true, "deny_users": ["' + str(reader_id) + '"]}',
    )
    settings = client.app.state.settings
    for organization_id in (settings.dev_organization_id, uuid4()):
        reader = Principal(
            id=reader_id,
            organization_id=organization_id,
            external_id="chinese-reader",
            display_name="Reader",
            permissions=frozenset({"knowledge.read.internal"}),
        )
        client.app.dependency_overrides[get_principal] = _reader_dependency(reader)
        try:
            assert _search(client, source, "项目奖金制度有哪些？") == []
        finally:
            client.app.dependency_overrides.pop(get_principal, None)


@pytest.mark.parametrize("query", ["Codeup仓库权限", "报销制度", "release policy", "项目_%报销"])
def test_lexical_terms_are_bounded_and_do_not_build_sql_operators(query: str) -> None:
    terms = lexical_terms(query)
    assert terms and len(terms) <= 32
    assert all("%" not in term and "'" not in term for term in terms)
    assert len(lexical_terms(" ".join(f"word{i}" for i in range(1000)))) == 32


def test_chinese_bigrams_are_not_one_unsearchable_sentence() -> None:
    terms = lexical_terms("Codeup仓库权限怎么查看？")
    assert {"codeup", "仓库", "权限", "查看"} <= set(terms)
