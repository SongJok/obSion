"""Conservative layout interpretation; unsupported semantics stay explicit gaps."""

from __future__ import annotations

from typing import Any

MAX_TABLE_CELLS = 10_000
MAX_LIST_LEVEL = 8
MAX_LIST_NUMBER = 1_000_000
MAX_LAYOUT_CHARS = 1400


class OrderedLists:
    def __init__(self) -> None:
        self.groups: dict[str, dict[int, int]] = {}
        self.invalid: set[str] = set()

    def label(self, listing: dict[str, Any]) -> str | None:
        group, level = listing.get("listId"), listing.get("level")
        if not isinstance(group, str) or not group or len(group) > 256:
            return None
        label = self._label(group, level, listing)
        if label is None:
            # A lost item means subsequent counter values cannot be trusted either.
            self.invalid.add(group)
        return label

    def _label(self, group: str, level: Any, listing: dict[str, Any]) -> str | None:
        if (
            group in self.invalid
            or type(level) is not int
            or not 0 <= level <= MAX_LIST_LEVEL
            or listing.get("hideSymbol", False) is not False
            or listing.get("isTaskList", False) is not False
        ):
            return None
        style = listing.get("listStyle")
        if (
            not isinstance(style, dict)
            or style.get("format") not in ("decimal", "lowerLetter")
            or style.get("text") not in (f"%{level + 1}.", f"%{level + 1})")
        ):
            return None
        counters = self.groups.setdefault(group, {})
        if any(parent not in counters for parent in range(level)):
            return None
        start = listing.get("start", 1)
        if (
            type(start) is not int
            or not 1 <= start <= MAX_LIST_NUMBER
            or ("start" in listing and level in counters)
        ):
            return None
        number = counters.get(level, start - 1) + 1
        if number > MAX_LIST_NUMBER:
            return None
        for nested in list(counters):
            if nested > level:
                del counters[nested]
        counters[level] = number
        rendered = str(number)
        if style["format"] == "lowerLetter":
            rendered = ""
            while number:
                number, remainder = divmod(number - 1, 26)
                rendered = chr(97 + remainder) + rendered
        return "  " * level + rendered + str(style["text"])[-1] + " "


def table_geometry(rows: list[Any]) -> tuple[bool, bool]:
    """Validate a rectangular physical grid with explicit hidden merge placeholders.

    Return (valid, needs_markup). Compact grids without placeholders are deliberately
    unsupported: guessing their physical coordinates can associate values incorrectly.
    """
    if not rows or len(rows) > MAX_TABLE_CELLS:
        return False, False
    for row in rows:
        if (
            not isinstance(row, list)
            or len(row) < 3
            or row[0] != "tr"
            or not isinstance(row[1], dict)
            or type(row[1].get("isTblHeader", False)) is not bool
        ):
            return False, False
    width = len(rows[0]) - 2
    if width * len(rows) > MAX_TABLE_CELLS or any(len(row) - 2 != width for row in rows):
        return False, False
    covered: set[tuple[int, int]] = set()
    markup = any(row[1].get("isTblHeader") is True for row in rows)
    for r, row in enumerate(rows):
        for c, cell in enumerate(row[2:]):
            if (
                not isinstance(cell, list)
                or len(cell) < 2
                or cell[0] != "tc"
                or not isinstance(cell[1], dict)
            ):
                return False, False
            attributes = cell[1]
            hidden = attributes.get("hidden", False)
            rowspan, colspan = attributes.get("rowSpan", 1), attributes.get("colSpan", 1)
            if (
                type(hidden) is not bool
                or type(rowspan) is not int
                or type(colspan) is not int
                or rowspan < 1
                or colspan < 1
                or r + rowspan > len(rows)
                or c + colspan > width
            ):
                return False, False
            if hidden:
                if (r, c) not in covered or rowspan != 1 or colspan != 1:
                    return False, False
                continue
            if (r, c) in covered:
                return False, False
            markup |= rowspan > 1 or colspan > 1
            for y in range(r, r + rowspan):
                for x in range(c, c + colspan):
                    if (y, x) in covered:
                        return False, False
                    covered.add((y, x))
    return True, markup
