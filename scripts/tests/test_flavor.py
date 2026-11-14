"""Unit tests for stixmd.flavor (GFM)."""
import logging
import unittest

from stixmd.anchors import AnchorMap
from stixmd.flavor import GfmFlavor, gfm_id
from stixmd.inline import render_spans
from stixmd.layout import SingleLayout
from stixmd.model import Heading, Paragraph, Span, Target


def T(kind: str, key: str) -> Target:
    return Target(kind=kind, key=key)


class GfmFlavorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.head = Heading(level=1, number="7", title="Data Markings", text="7 Data Markings", anchors=["_s7"])
        self.para = Paragraph(spans=[Span(text="anchored")], anchors=["_para"])
        amap = AnchorMap.build([self.head, self.para], [])
        self.flavor = GfmFlavor(amap, SingleLayout([self.head]))

    def test_gfm_ids(self) -> None:
        self.assertEqual(gfm_id(T("section", "4.1")), "sec-4.1")
        self.assertEqual(gfm_id(T("ref", "x.509")), "ref-x.509")
        self.assertEqual(gfm_id(T("block", "para")), "p-para")

    def test_links(self) -> None:
        f = self.flavor
        self.assertEqual(f.link(T("section", "7"), "**7**", "7", ""), "[**7**](#sec-7)")
        self.assertEqual(f.link(T("ref", "rfc8259"), "RFC8259", "RFC8259", ""), "[RFC8259](#ref-rfc8259)")
        self.assertEqual(f.link(T("url", "https://e.org/a b"), "x", "x", ""), "[x](https://e.org/a%20b)")

    def test_writer_resolves_through_anchor_map(self) -> None:
        writer = self.flavor.writer("")
        spans = [Span(text="see "), Span(text="7", href="#_s7"), Span(text=" or "), Span(text="gone", href="#nope")]
        logging.disable(logging.WARNING)
        try:
            self.assertEqual(render_spans(spans, writer), "see [7](#sec-7) or gone")
        finally:
            logging.disable(logging.NOTSET)

    def test_anchors_and_headings(self) -> None:
        f = self.flavor
        self.assertEqual(f.anchor_lines([T("section", "7"), T("block", "para")]),
                         ['<a id="sec-7"></a>', '<a id="p-para"></a>'])
        self.assertEqual(f.anchor_suffix([T("block", "para")]), "")
        self.assertEqual(f.heading_line(self.head, 2), "## 7 Data Markings")
        self.assertEqual(f.heading_line(Heading(level=3, number="1.2", title="_x_", text="1.2 _x_"), 4),
                         "#### 1.2 \\_x\\_")


if __name__ == "__main__":
    unittest.main()
