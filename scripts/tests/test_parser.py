"""Unit tests for stixmd.parser."""
import unittest

from stixmd.config import EXIT_PARSE, NBSP, StixMdError
from stixmd.model import CodeBlock, Heading, ListItem, Paragraph, Table
from stixmd.parser import decode_html, parse_html


def plain_text(spans: list) -> str:
    """Local helper: independent of inline.py."""
    return " ".join("".join(s.text for s in spans).replace(NBSP, " ").split())

FIXTURE = """<html><head><title>STIX Version 2.1</title></head><body><div class=WordSection1>
<h2><a name="_abc123"></a><span lang=EN>4.1 Attack
Pattern</span></h2>
<p class=MsoNormal><b><span lang=EN>Type Name:</span></b><span lang=EN> </span><span lang=EN style='font-family:Consolas;color:#C7254E'>attack-pattern</span></p>
<p class=MsoNormal><span lang=EN>&nbsp;</span></p>
<p class=MsoNormal style='margin-left:.5in;text-indent:-.25in'><span lang=EN>&#9679;<span style='font:7.0pt "Times New Roman"'>&nbsp;&nbsp; </span></span><span lang=EN>Item one</span></p>
<p class=MsoNormal><span lang=EN style='font-size:9.0pt;line-height:115%;
font-family:Consolas;background:#EFEFEF'>{</span></p>
<p class=MsoNormal><span lang=EN style='font-size:9.0pt;line-height:115%;
font-family:Consolas;background:#EFEFEF'>&nbsp;
&quot;type&quot;: &quot;x&quot;,<br>
&nbsp;&quot;id&quot;: 1</span></p>
<p class=MsoNormal><span lang=EN style='font-size:9.0pt;font-family:Consolas'>}</span></p>
<table><tr><td style='background:#073763' colspan=2><p class=MsoNormal><b>Hdr</b></p></td></tr></table>
</div></body></html>
"""


class DecodeTests(unittest.TestCase):
    def test_requires_declared_charset(self) -> None:
        with self.assertRaises(StixMdError) as ctx:
            decode_html(b"<html><head><title>x</title></head></html>")
        self.assertEqual(ctx.exception.exit_code, EXIT_PARSE)

    def test_decodes_cp1252(self) -> None:
        raw = b'<meta content="text/html; charset=windows-1252"><p>caf\xe9 \x99</p>'
        self.assertIn("café ™", decode_html(raw))


class ParseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.blocks = parse_html(FIXTURE).blocks

    def test_block_sequence(self) -> None:
        self.assertEqual([type(b) for b in self.blocks], [Heading, Paragraph, ListItem, CodeBlock, Table])

    def test_heading(self) -> None:
        self.assertEqual(self.blocks[0], Heading(level=2, number="4.1", title="Attack Pattern",
                                                 text="4.1 Attack Pattern", anchors=["_abc123"]))

    def test_type_name_spans(self) -> None:
        spans = self.blocks[1].spans  # type: ignore[attr-defined]
        self.assertEqual([(s.text, s.bold, s.code) for s in spans],
                         [("Type Name:", True, False), (" ", False, False), ("attack-pattern", False, True)])
        self.assertEqual(plain_text(spans), "Type Name: attack-pattern")

    def test_bullet(self) -> None:
        item = self.blocks[2]
        assert isinstance(item, ListItem)
        self.assertEqual((item.level, item.ordered, item.marker, plain_text(item.spans)), (1, False, None, "Item one"))

    def test_code_block(self) -> None:
        self.assertEqual(self.blocks[3].lines, ["{", '  "type": "x",', '  "id": 1', "}"])  # type: ignore[attr-defined]

    def test_table(self) -> None:
        table = self.blocks[4]
        assert isinstance(table, Table)
        self.assertEqual(len(table.rows), 1)
        self.assertEqual(len(table.rows[0]), 1)
        cell = table.rows[0][0]
        self.assertEqual((cell.colspan, cell.is_header), (2, True))

    def test_nested_table_rejected(self) -> None:
        with self.assertRaises(StixMdError):
            parse_html("<table><tr><td><table></table></td></tr></table>")

    def test_letter_marker_and_level2(self) -> None:
        html = ("<p style='margin-left:.5in;text-indent:-.25in'>b)&nbsp; second</p>"
                "<p style='margin-left:1.0in;text-indent:-.25in'>&#9675;<span style='font:7.0pt x'>&nbsp;</span>sub</p>"
                "<ol><li class=MsoNormal>first</li></ol>")
        items = parse_html(html).blocks
        self.assertEqual([(i.marker, i.level, i.ordered, plain_text(i.spans)) for i in items],  # type: ignore[attr-defined]
                         [("b)", 1, False, "second"), (None, 2, False, "sub"), (None, 1, True, "first")])

    def test_malformed_table_markup(self) -> None:
        blocks = parse_html("<table><tr><td colspan='2;'><p>a<td rowspan=0><p>b</table><p>after</p>").blocks
        self.assertEqual([type(b) for b in blocks], [Table, Paragraph])
        cells = blocks[0].rows[0]  # type: ignore[attr-defined]
        self.assertEqual([(c.colspan, c.rowspan, len(c.blocks)) for c in cells], [(1, 1, 1), (1, 1, 1)])

    def test_span_limit_and_empty_heading(self) -> None:
        with self.assertRaises(StixMdError):
            parse_html("<table><tr><td colspan=65><p>x</p></td></tr></table>")
        cell = parse_html("<table><tr><td colspan=" + chr(0xB2) + "><p>x</p></td></tr></table>").blocks[0]
        self.assertEqual(cell.rows[0][0].colspan, 1)  # type: ignore[attr-defined]
        blocks = parse_html("<h2><a name=q></a>&nbsp;</h2><p>t</p>").blocks
        self.assertEqual([(type(b), b.anchors) for b in blocks], [(Paragraph, ["q"])])

    def test_empty_paragraph_keeps_anchor_for_next_block(self) -> None:
        blocks = parse_html("<p><a name=x></a>&nbsp;</p><p>text</p>").blocks
        self.assertEqual([(type(b), b.anchors) for b in blocks], [(Paragraph, ["x"])])

    def test_soft_break_before(self) -> None:                      # only a code-line blank
        code = "<p class=MsoNormal><span style='font-size:9.0pt;font-family:Consolas'>{}</span></p>"
        blank = code.format("&nbsp;")
        html = (code.format("{") + blank + blank + code.format("x") + blank + "<p>&nbsp;</p>" + code.format("y")
                + blank + "<p>prose</p>" + code.format("z"))
        blocks = parse_html(html).blocks
        self.assertEqual([(b.lines, b.soft_break_before) for b in blocks if isinstance(b, CodeBlock)],
                         [(["{"], False), (["x"], True), (["y"], False), (["z"], False)])


if __name__ == "__main__":
    unittest.main()
