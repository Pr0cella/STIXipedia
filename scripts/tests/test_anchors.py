"""Unit tests for stixmd.anchors: anchor map, link resolution, Target."""
import logging
import unittest

from stixmd.anchors import AnchorMap, slug
from stixmd.model import Cell, Heading, ListItem, Paragraph, Span, Table, Target


def T(kind: str, key: str) -> Target:
    return Target(kind=kind, key=key)


def heading(level: int, number: str, title: str, anchors: list[str] | None = None) -> Heading:
    text = f"Appendix {number}. {title}" if level == 1 and number.isalpha() else f"{number} {title}"
    return Heading(level=level, number=number, title=title, text=text, anchors=anchors or [])


class SlugTests(unittest.TestCase):
    def test_slug(self) -> None:
        self.assertEqual(slug("UnicodeTR#36"), "unicodetr-36")
        self.assertEqual(slug("X.509"), "x.509")
        self.assertEqual(slug("IEEE 754-2008"), "ieee-754-2008")


class ResolveTests(unittest.TestCase):
    def setUp(self) -> None:
        link_par = Paragraph(spans=[Span(text="see "), Span(text="7.3", href="#_missing"), Span(text=" and ["),
                                    Span(text="CAPEC", href="#kix.broken"), Span(text="]")])
        self.target = Paragraph(spans=[Span(text="anchored")], anchors=["_para"])
        cell_par = Paragraph(spans=[Span(text="cell link", href="#_para")])
        self.label = Paragraph(spans=[Span(text="[CAPEC]", bold=True)], anchors=["kix.capec"])
        self.kept = [heading(1, "7", "Data Markings"), heading(2, "7.3", "Ref", ["_ref73"]), link_par, self.target,
                     Table(rows=[[Cell(blocks=[cell_par])]]),
                     heading(1, "E", "References"), Paragraph(spans=[Span(text="desc")], anchors=["kix.early"]),
                     self.label, Paragraph(spans=[Span(text="The CAPEC site.")])]
        removed = [ListItem(spans=[Span(text="TAXII Version 2.1. Edited by X. "),
                                   Span(text=" ", href="https://invisible.example/"),
                                   Span(text="https://t.example/taxii.html", href="https://t.example/taxii.html")]),
                   Paragraph(spans=[Span(text="Related work:", bold=True)], anchors=["kix.cover"]),
                   Paragraph(spans=[Span(text="Part 3", href="https://p3.example/")], anchors=["kix.p3"])]
        logging.disable(logging.WARNING)
        self.amap = AnchorMap.build(self.kept, removed)

    def tearDown(self) -> None:
        logging.disable(logging.NOTSET)

    def test_rules(self) -> None:
        a = self.amap
        self.assertEqual(a.stats, dict.fromkeys(("R1", "R2", "R3", "R4", "R4b", "R5"), 0))
        self.assertEqual(a.resolve("#_ref73", "7.3"), T("section", "7.3"))
        self.assertEqual(a.resolve("#_missing", "7.3"), T("section", "7.3"))
        self.assertEqual(a.resolve("#kix.broken", "[CAPEC]"), T("ref", "capec"))
        self.assertEqual(a.resolve("#kix.p3", "Part 3"), T("url", "https://p3.example/"))
        self.assertEqual(a.resolve("#gone", "TAXII"), T("url", "https://t.example/taxii.html"))
        self.assertIsNone(a.resolve("#kix.cover", "STIX Version 2.0. Part 3: STIX Objects"))
        self.assertEqual(a.resolve(" https://e.org ", "x"), T("url", "https://e.org"))
        self.assertIsNone(a.resolve("https://e.org", "  "))
        self.assertEqual(a.stats, {"R1": 1, "R2": 1, "R3": 1, "R4": 1, "R4b": 1, "R5": 1})

    def test_ids(self) -> None:
        a = self.amap
        self.assertEqual(a.targets_for(self.kept[1]), [T("section", "7.3")])
        self.assertEqual(a.targets_for(self.label), [T("ref", "capec")])
        self.assertEqual(a.resolve("#kix.early", "x"), T("ref", "capec"))   # pending name moves to the next label
        self.assertEqual(a.targets_for(self.target), [T("block", "para")])
        self.assertIs(a.anchored["para"], self.target)
        self.assertEqual(a.targets_for(self.kept[2]), [])




class WarningTests(unittest.TestCase):
    def test_unresolved_link_reports_position_not_link(self) -> None:
        kept = [heading(1, "2", "Types"), heading(2, "2.13", "Observable Container"),
                Paragraph(spans=[Span(text="see "), Span(text="Secret Title", href="#kix.gone")])]
        removed = [Paragraph(spans=[Span(text="Related work:", bold=True)], anchors=["kix.gone"])]
        with self.assertLogs("stixmd", level="WARNING") as logs:
            AnchorMap.build(kept, removed, {"#kix.gone": [(4370, 70)]})
        self.assertEqual(len(logs.output), 1)
        message = logs.output[0]
        self.assertIn("line 4370 col 71", message)
        self.assertIn("section 2.13", message)
        self.assertIn("stripped content", message)
        self.assertNotIn("kix.gone", message)
        self.assertNotIn("Secret Title", message)

    def test_missing_bookmark_reason(self) -> None:
        kept = [heading(1, "2", "Types"), Paragraph(spans=[Span(text="x", href="#nowhere")])]
        with self.assertLogs("stixmd", level="WARNING") as logs:
            AnchorMap.build(kept, [])
        self.assertIn("unknown source position", logs.output[0])
        self.assertIn("does not exist", logs.output[0])


class ParserPositionTests(unittest.TestCase):
    def test_link_positions_recorded(self) -> None:
        from stixmd.parser import parse_html
        doc = parse_html("<html><body>\n<p class=MsoNormal>a <a\nhref='#x'>b</a> <a href='#x'>c</a></p></body></html>")
        self.assertEqual(doc.link_pos, {"#x": [(2, 21), (3, 16)]})       # (line, 0-based col of "<a")


if __name__ == "__main__":
    unittest.main()
