from __future__ import annotations

import json
from typing import Any

import pytest

from obsion.knowledge.dingtalk_jsonml import extract_full_document
from obsion.knowledge.parsers import chunk_document


def document(*children: Any) -> Any:
    return extract_full_document(
        {
            "complete": True,
            "status": "success",
            "contractVersion": "doc.content.v1",
            "target": {"canonicalId": "doc_one1", "product": "doc"},
            "content": {
                "success": True,
                "nodeId": "doc_one1",
                "revision": "17",
                "title": "Synthetic layout",
                "jsonml": json.dumps(["root", {}, *children]),
            },
        },
        "doc_one1",
    )


def item(text: str, group: str = "a", level: int = 0, **overrides: Any) -> list[Any]:
    listing = {
        "listId": group,
        "level": level,
        "isOrdered": True,
        "isTaskList": False,
        "listStyle": {
            "format": "decimal" if level == 0 else "lowerLetter",
            "text": f"%{level + 1}.",
        },
        **overrides,
    }
    return ["p", {"list": listing}, text]


def cell(text: str = "", **attributes: Any) -> list[Any]:
    return ["tc", attributes, ["p", {}, text]]


def table(*rows: list[Any]) -> list[Any]:
    return ["table", {}, *[["tr", {}, *row] for row in rows]]


def test_numbering_preserves_start_nested_labels_and_group_restart() -> None:
    body = document(
        item("First", start=3),
        item("Child", level=1),
        item("Child two", level=1),
        item("Second"),
        item("Reset child", level=1),
        item("New group", group="b"),
    )
    assert body.complete
    assert body.text == (
        "3. First\n\n  a. Child\n\n  b. Child two\n\n4. Second\n\n  a. Reset child\n\n1. New group"
    )


@pytest.mark.parametrize(
    "attributes",
    [
        {"start": True},
        {"start": 0},
        {"start": 1_000_001},
        {"level": -1},
        {"level": True},
        {"level": 9},
        {"listId": ""},
        {"hideSymbol": True},
        {"listStyle": {"format": "unknown", "text": "%1."}},
        {"listStyle": {"format": "decimal", "text": "untrusted %1."}},
        {"isTaskList": True},
    ],
)
def test_ambiguous_numbering_stays_partial(attributes: dict[str, Any]) -> None:
    body = document(item("First", **attributes), item("Second"))
    assert not body.complete and "ordered_list_labels" in body.gaps


def test_orphan_nested_and_mid_group_start_are_not_invented() -> None:
    assert not document(item("Child", level=1)).complete
    assert not document(item("First"), item("Second", start=5)).complete


def test_code_duplicate_body_is_retained_once_and_conflicts_are_partial() -> None:
    body = document(["code", {"code": "x < 2\nprint(x)"}, ["span", {}, "x < 2\nprint(x)"]])
    assert body.complete and body.text == "x < 2\nprint(x)"
    conflicting = document(["code", {"code": "approved"}, ["span", {}, "other"]])
    assert not conflicting.complete and "unsupported_code" in conflicting.gaps


def test_merged_table_preserves_geometry_escapes_text_and_emits_each_body_once() -> None:
    body = document(
        table(
            [cell("Category", rowSpan=2), cell("Limit", colSpan=2), cell(hidden=True)],
            [cell(hidden=True), cell("A < 200"), cell("B & C")],
        )
    )
    assert body.complete
    assert body.text == (
        '<table>\n<tr><td rowspan="2">Category</td><td colspan="2">Limit</td></tr>\n'
        "<tr><td>A &lt; 200</td><td>B &amp; C</td></tr>\n</table>"
    )
    assert body.text.count("Category") == 1


@pytest.mark.parametrize(
    "rows",
    [
        [[cell("A", colSpan=2), cell("B")]],  # occupied covered slot
        [[cell("A", colSpan=3), cell(hidden=True)]],  # out of bounds
        [[cell("A", rowSpan=2)]],
        [[cell("A"), cell(hidden=True)]],  # orphan hidden cell
        [[cell("A", colSpan=2), cell("concealed", hidden=True)]],
        [[cell("A", colSpan=2), cell(hidden=True, rowSpan=2)]],
        [[cell("A", colSpan=True)]],
        [[cell("A", rowSpan=0)]],
        [[cell("A"), cell("B")], [cell("C")]],  # missing placeholders
    ],
)
def test_invalid_or_lossy_table_is_not_publishable(rows: list[list[Any]]) -> None:
    assert not document(table(*rows)).complete


def test_nested_unknown_cells_and_ambiguous_column_layout_remain_partial() -> None:
    assert not document(table([["tc", {}, ["img", {"src": "not-evidence"}]]])).complete
    columns = table([cell("A"), cell("B")], [cell("C"), cell("D")])
    columns[1]["sr"] = True
    assert not document(columns).complete


def test_single_row_column_layout_does_not_imply_tabular_relationships() -> None:
    columns = table([cell("A < B"), cell("Independent paragraph")])
    columns[1]["sr"] = True
    body = document(columns)
    assert body.complete
    assert body.text == (
        '<div class="columns">\n<div class="column">A &lt; B</div>\n'
        '<div class="column">Independent paragraph</div>\n</div>'
    )


def test_table_header_role_is_preserved_without_inventing_headers() -> None:
    value = table([cell("Item"), cell("Cost")], [cell("A"), cell("2")])
    value[2][1]["isTblHeader"] = True
    body = document(value)
    assert body.complete and "<th>Item</th><th>Cost</th>" in body.text
    assert "<td>A</td><td>2</td>" in body.text


def test_structured_evidence_keeps_whole_table_and_no_overlap_fragments() -> None:
    body = document(table([cell("Merged\n\nparagraph", colSpan=2), cell(hidden=True)]))
    assert body.complete
    chunks = chunk_document(
        "Before " * 210 + "\n\n" + body.text + "\n\n" + "After " * 250,
        preserve_dingtalk_layout=True,
    )
    layouts = [content for _, content in chunks if "<table>" in content or "</table>" in content]
    assert layouts == [body.text]
    assert sum("Merged" in content for _, content in chunks) == 1
    assert all(len(content) <= 1560 for _, content in chunks)


def test_oversized_layout_is_explicitly_incomplete() -> None:
    body = document(table([cell("x" * 1500, colSpan=2), cell(hidden=True)]))
    assert not body.complete and "oversized_layout_evidence" in body.gaps


def test_many_unclosed_literal_tags_are_plain_text_not_a_backtracking_scan() -> None:
    text = ("<table>\nliteral\n" * 20_000) + "final paragraph"
    chunks = chunk_document(text, preserve_dingtalk_layout=True)
    assert chunks and "final paragraph" in chunks[-1][1]
