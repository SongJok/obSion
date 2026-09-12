from dataclasses import replace
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from obsion.common.errors import NotFoundError
from obsion.db.models import Document, DocumentChunk
from obsion.domain.enums import Classification
from obsion.knowledge.document_read import read_document_page
from obsion.security.classification import maximum_classification
from obsion.security.identity import Principal


@pytest.mark.parametrize(
    ("floor", "result", "expected"),
    [
        (Classification.INTERNAL, Classification.RESTRICTED, Classification.RESTRICTED),
        (Classification.RESTRICTED, Classification.PUBLIC, Classification.RESTRICTED),
        (Classification.INTERNAL, Classification.PUBLIC, Classification.INTERNAL),
        (None, None, Classification.PUBLIC),
    ],
)
def test_result_classification_cannot_lower_the_descriptor_floor(floor, result, expected):
    assert maximum_classification(floor, result) == expected


@pytest.mark.parametrize("denial", [None, "foreign_org", "no_acl", "deny_acl", "old_version"])
def test_native_document_pages_preserve_order_versions_and_access(client, denial):
    principal = Principal(
        id=client.app.state.settings.dev_user_id,
        organization_id=client.app.state.settings.dev_organization_id,
        external_id="native-reader",
        display_name="Native Reader",
        permissions=frozenset({"knowledge.read"}),
    )
    body = "\n\n".join(
        f"# Section {i}\n" + (f"Section {i} records source material. " * 38) for i in range(8)
    )
    response = client.post(
        "/api/v1/knowledge/documents",
        files={"file": ("pages.md", body.encode(), "text/markdown")},
        data={
            "source": "native-read-test",
            "external_id": "pages",
            "title": "Pages",
            "classification": "RESTRICTED",
            "acl": '{"users":["' + str(principal.id) + '"]}',
        },
    )
    assert response.status_code == 201, response.text
    identifier = UUID(response.json()["document"]["id"])

    async def check():
        async with client.app.state.database.sessions() as session, session.begin():
            document = await session.get(Document, identifier)
            assert document is not None
            reader = principal
            version = document.current_version
            if denial == "foreign_org":
                reader = replace(principal, organization_id=uuid4())
            elif denial == "no_acl":
                reader = replace(principal, id=uuid4())
            elif denial == "deny_acl":
                document.acl = {**document.acl, "deny_users": [str(principal.id)]}
                await session.flush()
            elif denial == "old_version":
                version += 1
            request = {
                "operation": "document.read",
                "document_id": str(identifier),
                "version": version,
                "limit": 3,
            }
            if denial:
                with pytest.raises(NotFoundError):
                    await read_document_page(session, reader, request)
                return
            pages = []
            offset = 0
            while offset is not None:
                page = await read_document_page(session, reader, {**request, "offset": offset})
                assert 0 < page["count"] <= 3
                assert page["version"] == version
                assert page["classification"] == "RESTRICTED"
                pages.extend(page["hits"])
                assert page["next_offset"] is None or page["next_offset"] > offset
                offset = page["next_offset"]
            expected = list(
                await session.scalars(
                    select(DocumentChunk)
                    .where(DocumentChunk.organization_id == principal.organization_id)
                    .order_by(DocumentChunk.ordinal)
                )
            )
            assert len(pages) > 3
            assert [p["chunk_id"] for p in pages] == [str(c.id) for c in expected]
            assert [p["content"] for p in pages] == [c.content for c in expected]
            assert len({p["chunk_id"] for p in pages}) == len(pages)

    client.portal.call(check)
