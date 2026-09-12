import copy
import json

from test_critic import evidence

from obsion.domain.enums import EvidenceType
from obsion.model_gateway.evidence_segments import evidence_context_segments


def test_document_author_context_keeps_bodies_and_hides_transport_metadata():
    item = evidence(EvidenceType.DOCUMENT, "private-source-marker", "resource-marker")
    item.content = {
        "query": "irrelevant-query-marker",
        "hits": [
            {
                "title": "报销制度",
                "content": "须先审批后报销。",
                "external_id": "external-marker",
                "chunk_id": "chunk-marker",
                "version": 8123,
                "source": "private-source-marker",
                "score": 0.923,
            },
            {"title": "空条目", "content": "   ", "external_id": "empty-marker"},
        ],
    }
    original = copy.deepcopy(item.content)
    text = evidence_context_segments([item], document_bodies_only=True)[0].content
    assert "须先审批后报销。" in text and "报销制度" in text and str(item.id) in text
    for marker in [
        "private-source-marker",
        "resource-marker",
        "external-marker",
        "chunk-marker",
        "irrelevant-query-marker",
        "empty-marker",
        "8123",
        "0.923",
    ]:
        assert marker not in text
    assert item.content == original


def test_document_projection_does_not_change_non_document_evidence():
    tool = evidence(EvidenceType.TOOL, "tool", "resource")
    tool.content = {"result": "tool data", "source": "tool-source-marker"}
    before = evidence_context_segments([tool])
    after = evidence_context_segments([tool], document_bodies_only=True)
    assert [json.loads(s.content) for s in before] == [json.loads(s.content) for s in after]


def test_review_body_positions_select_precise_sources_without_chunk_ids():
    from obsion.harness.grounding import GroundingAssessment, _bodies, validate_review
    from obsion.harness.runtime import HarnessRuntime

    item = evidence(EvidenceType.DOCUMENT, "knowledge", "resource")
    item.content = {
        "text": "  ",
        "content": "Unrelated top-level source body.",
        "title": "无关摘要",
        "hits": [
            None,
            {"content": ""},
            {
                "title": "第一份制度",
                "content": "交通报销必须先审批。",
                "external_id": "retained-first-id",
                "chunk_id": None,
            },
            {
                "title": "第二份制度",
                "content": "提交交通报销时需要发票。",
                "external_id": "retained-second-id",
                "chunk_id": None,
            },
        ],
    }
    eid = str(item.id)
    claims = [{"statement": "交通报销必须先审批并附发票。", "evidence_ids": [eid]}]
    model_review = {
        "answer_supported": True,
        "question_answered": True,
        "claims": [
            {
                "claim_index": 1,
                "verdict": "SUPPORTED",
                "quotes": [
                    {"evidence_id": eid, "quote": "交通报销必须先审批。"},
                    {"evidence_id": eid, "quote": "提交交通报销时需要发票。"},
                ],
            }
        ],
    }
    accepted, checked = validate_review(model_review, claims, {eid: _bodies(item)})
    assert accepted
    projection = json.loads(evidence_context_segments([item], document_bodies_only=True)[0].content)
    bodies = projection[0]["content"]["bodies"]
    for quote in checked[0]["quotes"]:
        assert bodies[quote["body_index"]]["text"][
            quote["start"] : quote["start"] + quote["length"]
        ] in {"交通报销必须先审批。", "提交交通报销时需要发票。"}
    grounding = GroundingAssessment(True, "grounding_supported", "candidate", "input", checked)
    citations = HarnessRuntime._knowledge_citations(claims, [item], grounding=grounding)
    assert [c["title"] for c in citations] == ["第一份制度", "第二份制度"]
    assert [c["body_index"] for c in citations] == [1, 2]
    assert [c["external_id"] for c in citations] == ["retained-first-id", "retained-second-id"]
    assert (
        HarnessRuntime._knowledge_citations(
            claims,
            [item],
            grounding=GroundingAssessment(False, "failed", "candidate", "input", checked),
        )
        == []
    )
    assert (
        HarnessRuntime._knowledge_citations(
            claims,
            [item],
            grounding=GroundingAssessment(True, "invalid-empty", "candidate", "input"),
        )
        == []
    )


def test_source_labels_render_as_text_without_exposing_bookkeeping():
    from obsion.harness.runtime import HarnessRuntime

    rendered = HarnessRuntime._append_knowledge_citations(
        "有据的回答。",
        [
            {
                "label": "[1]",
                "title": "制度\n![图片](https://example.invalid/tracker)",
                "version": 2,
                "source": "transport-marker",
                "chunk_id": "chunk-marker",
                "evidence_id": "evidence-marker",
            }
        ],
    )
    assert "版本 2" in rendered
    assert "\n![图片]" not in rendered and "\\!\\[图片\\]" in rendered
    assert all(
        marker not in rendered for marker in ["transport-marker", "chunk-marker", "evidence-marker"]
    )
