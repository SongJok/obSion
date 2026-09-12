"""Bounded, body-only extraction from DingTalk's complete JSONML document.

Unknown and non-text elements are explicit gaps. Attributes are never generally
flattened into evidence: they contain URLs, identities, layout and embedded data.
"""

from __future__ import annotations

import hashlib
import html
import json
from dataclasses import dataclass
from typing import Any

from obsion.capabilities.dingtalk_docs import DingTalkDocsResponseError
from obsion.knowledge.dingtalk_layout import MAX_LAYOUT_CHARS, OrderedLists, table_geometry

PARSER_VERSION = "dingtalk-jsonml-v2"
MAX_JSONML_BYTES = 8_000_000
MAX_ELEMENTS = 100_000
MAX_DEPTH = 64


@dataclass(frozen=True, slots=True)
class JsonmlBody:
    text: str
    revision: str
    title: str
    raw_checksum: str
    gaps: tuple[str, ...]

    @property
    def complete(self) -> bool:
        return not self.gaps


def extract_full_document(payload: dict[str, Any], node_id: str) -> JsonmlBody:
    content = payload.get("content")
    target = payload.get("target")
    if (
        payload.get("complete") is not True
        or payload.get("status") != "success"
        or payload.get("contractVersion") != "doc.content.v1"
        or not isinstance(target, dict)
        or target.get("canonicalId") != node_id
        or target.get("product") != "doc"
        or not isinstance(content, dict)
        or content.get("success") is not True
        or content.get("nodeId") != node_id
    ):
        raise DingTalkDocsResponseError("The complete document envelope could not be verified")
    raw, revision, title = (content.get(key) for key in ("jsonml", "revision", "title"))
    if (
        not isinstance(raw, str)
        or len(raw.encode("utf-8")) > MAX_JSONML_BYTES
        or not isinstance(revision, str)
        or not revision.strip()
        or not isinstance(title, str)
        or not title.strip()
        or len(title) > 500
    ):
        raise DingTalkDocsResponseError("The document body or version was invalid")
    try:
        tree = json.loads(raw)
    except (ValueError, RecursionError) as exc:
        raise DingTalkDocsResponseError("The document JSONML could not be decoded") from exc
    if not isinstance(tree, list) or not tree or tree[0] != "root":
        raise DingTalkDocsResponseError("The document JSONML root was invalid")
    parser = _Parser()
    body = parser.render(tree, 0).strip()
    if len(body.encode("utf-8")) > MAX_JSONML_BYTES:
        raise DingTalkDocsResponseError("The document exceeded its rendering budget")
    return JsonmlBody(
        text=body,
        revision=revision,
        title=title,
        raw_checksum=hashlib.sha256(raw.encode("utf-8")).hexdigest(),
        gaps=tuple(sorted(parser.gaps)),
    )


class _Parser:
    def __init__(self) -> None:
        self.count = 0
        self.gaps: set[str] = set()
        self.lists = OrderedLists()

    def _visit(self, depth: int) -> None:
        self.count += 1
        if depth > MAX_DEPTH or self.count > MAX_ELEMENTS:
            raise DingTalkDocsResponseError("The document JSONML exceeded its structural budget")

    def render(self, value: Any, depth: int, *, validated_cell: bool = False) -> str:
        self._visit(depth)
        if isinstance(value, str):
            return value
        if (
            not isinstance(value, list)
            or len(value) < 2
            or not isinstance(value[0], str)
            or not isinstance(value[1], dict)
        ):
            raise DingTalkDocsResponseError("The document JSONML contained an invalid element")
        tag, attributes = value[:2]
        known = {
            "root",
            "p",
            "span",
            "a",
            "h1",
            "h2",
            "h3",
            "h4",
            "h5",
            "h6",
            "table",
            "tr",
            "tc",
            "hr",
            "br",
            "container",
            "code",
        }
        if tag not in known:
            # Do not leak opaque embedded properties or claim OCR/attachment extraction.
            self.gaps.add("unsupported_element")
            return ""
        if tag == "span" and attributes.get("data-type", "text") not in {"text", "leaf"}:
            self.gaps.add("non_text_span")
        if tag == "container" and attributes.get("subType") != "colorBlocks":
            self.gaps.add("unsupported_container")
        if tag == "table":
            valid, markup = table_geometry(value[2:])
            columns = attributes.get("sr", False)
            if columns is not False and not (
                columns is True and valid and len(value[2:]) == 1 and not markup
            ):
                self.gaps.add("unsupported_column_layout")
            if valid:
                return self._table(value[2:], depth, markup, columns is True and not markup)
            self.gaps.add("invalid_table_geometry")
        if tag == "tc" and not validated_cell:
            # Keep textual rows useful, but do not imply merged/hidden geometry survived.
            if attributes.get("hidden", False) is not False:
                self.gaps.add("hidden_table_cell")
            if any(attributes.get(key, 1) != 1 for key in ("colSpan", "rowSpan")):
                self.gaps.add("merged_table_cell")
        children = [self.render(child, depth + 1) for child in value[2:]]
        if tag == "code":
            code = attributes.get("code")
            rendered_code = "".join(children)
            if not isinstance(code, str) or (rendered_code and rendered_code != code):
                self.gaps.add("unsupported_code")
                return ""
            return code + "\n\n"
        if tag in {"br", "hr"}:
            if any(child.strip() for child in children):
                self.gaps.add("unexpected_leaf_content")
            return "\n"
        if tag == "tr":
            if any(not isinstance(child, list) or child[0] != "tc" for child in value[2:]):
                raise DingTalkDocsResponseError("The document table row was invalid")
            return " | ".join(child.strip() for child in children) + "\n"
        text = "".join(children)
        if tag in {"p", "h1", "h2", "h3", "h4", "h5", "h6"}:
            if attributes.get("blockquote") is True:
                text = "\n".join("> " + line for line in text.split("\n"))
            listing = attributes.get("list")
            if listing:
                if not isinstance(listing, dict):
                    raise DingTalkDocsResponseError("The document list structure was invalid")
                if listing.get("isOrdered") is True:
                    label = self.lists.label(listing)
                    if label is None:
                        self.gaps.add("ordered_list_labels")
                    else:
                        return label + text + "\n\n"
                # List order is the source order; do not invent numbering or checked state.
                prefix = "- "
                if listing.get("isTaskList") is True:
                    checked = listing.get("isChecked")
                    if not isinstance(checked, bool):
                        self.gaps.add("unknown_task_state")
                    prefix = "[x] " if checked is True else "[ ] "
                text = prefix + text
            return text + "\n\n"
        if tag in {"table", "container"}:
            return text + "\n\n"
        return text

    def _table(self, rows: list[Any], depth: int, markup: bool, columns: bool = False) -> str:
        rendered_rows: list[str] = []
        for row in rows:
            self._visit(depth + 1)
            cells: list[str] = []
            for cell in row[2:]:
                text = self.render(cell, depth + 2, validated_cell=True).strip()
                attributes = cell[1]
                if attributes.get("hidden") is True:
                    if text:
                        self.gaps.add("hidden_table_cell")
                    continue
                if columns:
                    text = '<div class="column">' + html.escape(text, quote=False) + "</div>"
                elif markup:
                    tag = "th" if row[1].get("isTblHeader") is True else "td"
                    spans = "".join(
                        f' {html_name}="{attributes[key]}"'
                        for key, html_name in (("rowSpan", "rowspan"), ("colSpan", "colspan"))
                        if attributes.get(key, 1) > 1
                    )
                    text = f"<{tag}{spans}>{html.escape(text, quote=False)}</{tag}>"
                cells.append(text)
            if columns:
                rendered_rows.append("\n".join(cells))
            else:
                rendered_rows.append(
                    "<tr>" + "".join(cells) + "</tr>" if markup else " | ".join(cells)
                )
        result = "\n".join(rendered_rows)
        if columns:
            result = '<div class="columns">\n' + result + "\n</div>"
        elif markup:
            result = "<table>\n" + result + "\n</table>"
        if len(result.encode("utf-8")) > MAX_JSONML_BYTES:
            raise DingTalkDocsResponseError("The document table exceeded its rendering budget")
        if (markup or columns) and len(result) > MAX_LAYOUT_CHARS:
            self.gaps.add("oversized_layout_evidence")
        return result + "\n\n"
