"""Unit tests for stixmd.tables."""
import unittest

from stixmd.model import Cell, CodeBlock, Paragraph, Span, Table
from stixmd.inline import md_link_writer
from stixmd.tables import render_table


def cell(text: str, *, header: bool = False, colspan: int = 1, rowspan: int = 1, bold: bool = False) -> Cell:
    return Cell(blocks=[Paragraph(spans=[Span(text=text, bold=bold)])], is_header=header,
                colspan=colspan, rowspan=rowspan)


keep = md_link_writer(lambda href, text: href)


class TableTests(unittest.TestCase):
    def test_property_table(self) -> None:
        table = Table(rows=[[cell("Required Common Properties", header=True, colspan=3)],
                            [cell("type, id", colspan=3)],
                            [cell("Property Name", header=True), cell("Type", header=True),
                             cell("Description", header=True)],
                            [cell("name (required)"), cell("string"), cell("A | name.")]])
        self.assertEqual(render_table(table, keep, "4.1.1 Properties"), [
            "**Required Common Properties:** type, id", "",
            "| Property Name | Type | Description |", "| --- | --- | --- |",
            "| name (required) | string | A \\| name. |"])

    def test_bold_header_band_is_not_double_bold(self) -> None:
        table = Table(rows=[[cell("Common Relationships", header=True, colspan=2, bold=True)],
                            [cell("related-to", colspan=2)]])
        self.assertEqual(render_table(table, keep, "x"), ["**Common Relationships:** related-to"])

    def test_record_table(self) -> None:
        code = CodeBlock(lines=["{", '  "a": 1,', '  "b": 2,', '  "c": 3', "}"])
        table = Table(rows=[[cell("Step #", header=True), cell("STIX Object", header=True)],
                            [cell("1"), Cell(blocks=[code])]])
        self.assertEqual(render_table(table, keep, "3.6.2"), [
            "**Step #: 1**", "", "*STIX Object:*", "", "```json", *code.lines, "```"])

    def test_rowspan_expansion(self) -> None:
        table = Table(rows=[[cell("A", header=True, colspan=2), cell("B", header=True, rowspan=2)],
                            [cell("C"), cell("D")]])
        self.assertEqual(render_table(table, keep, "x"), ["| A |  | B |", "| --- | --- | --- |", "| C | D |  |"])

    def test_short_code_in_grid_cell(self) -> None:
        table = Table(rows=[[cell("k"), Cell(blocks=[CodeBlock(lines=["  a | b", "", "c"])])]])
        self.assertEqual(render_table(table, keep, "x"), ["|  |  |", "| --- | --- |", "| k | `a \\| b`<br>`c` |"])

    def test_empty_rows_dropped(self) -> None:
        table = Table(rows=[[cell("Only band", header=True, colspan=2)], [cell(""), cell("")]])
        self.assertEqual(render_table(table, keep, "x"), ["**Only band**"])


if __name__ == "__main__":
    unittest.main()
