"""Unit tests for stixmd.validate on the GFM file. The vault's checks, the Obsidian dialect
included, are tested in test_vault."""
import unittest
from unittest import mock

from stixmd import validate as v
from stixmd.anchors import AnchorMap
from stixmd.assemble import assemble_single
from stixmd.flavor import GfmFlavor
from stixmd.layout import SingleLayout
from stixmd.model import Heading
from stixmd.parser import parse_html
from stixmd.regions import apply_regions

CODE = "style='font-size:9.0pt;font-family:Consolas'"


def stage(label: str, stem: str) -> str:
    links = "".join(f'<p class=MsoNormal><a href="https://docs.oasis-open.org/{stem}/s.{e}">'
                    f"https://docs.oasis-open.org/{stem}/s.{e}</a></p>" for e in ("docx", "html", "pdf"))
    return f"<p class=MsoNormal><b>{label}</b></p>{links}"


HTML = ("<html><head><meta content='text/html; charset=windows-1252'><title>STIX Version 2.1</title></head>"
        "<body><div class=WordSection1><p class=MsoTitle>STIX Version 2.1</p>"
        "<p class=MsoSubtitle>OASIS Standard</p><p class=MsoSubtitle>10 June 2021</p>"
        + stage("This stage:", "os") + stage("Previous stage:", "cs02") + stage("Latest stage:", "v2.1")
        + "<p class=MsoNormal><b>Abstract:</b></p><p class=MsoNormal>The abstract text.</p>"
        "<h1>Notices</h1><p class=MsoNormal>OASIS IPR Policy</p>"
        "<h1><a name='_aaa'></a>1 Introduction</h1>"
        "<p class=MsoNormal>Intro text that <b>MUST</b> link to <a href='#_bbb'>1.1</a> and "
        "<a href='https://example.org/x'>Example</a>.</p>"
        "<h2><a name='_bbb'></a>1.1 Details</h2>"
        "<p class=MsoNormal><b>Type Name:</b> <span style='font-family:Consolas'>widget</span></p>"
        f"<p class=MsoNormal><span {CODE}>{{</span></p>"
        f"<p class=MsoNormal><span {CODE}>&nbsp;&nbsp;&quot;a&quot;: 1</span></p>"
        f"<p class=MsoNormal><span {CODE}>}}</span></p>"
        "<table><tr><td style='background:#073763'><p class=MsoNormal>Name</p></td>"
        "<td style='background:#073763'><p class=MsoNormal>Value</p></td></tr>"
        "<tr><td><p class=MsoNormal>alpha</p></td><td><p class=MsoNormal>beta gamma</p></td></tr></table>"
        "<h1>Appendix G. Revision History</h1><p class=MsoNormal>old</p></div></body></html>")


HTML_E = HTML.replace(
    "and <a href='https://example.org/x'>Example</a>.</p>",
    "and <a href='https://example.org/x'>Example</a> [<a href='#_ref1'>RFC1</a>].</p>").replace(
    "<h1>Appendix G.",
    "<h1><a name='_eee'></a>Appendix E. References</h1><p class=MsoNormal><a name='_ref1'></a><b>[RFC1]</b></p>"
    "<p class=MsoNormal>The RFC1 text, cost $5.</p><h1>Appendix G.")
SPLIT_CODE = (f"<span {CODE}>{{</span></p>", f"<span {CODE}>{{</span></p><p class=MsoNormal><span {CODE}>&nbsp;</span></p>")
HTML_W = HTML_E.replace("Intro text that", "Intro text about <span style='font-family:Consolas'>widget</span> that")


def convert(html: str, name: str = "t.md") -> str:
    """Convert html to the GFM file."""
    doc = parse_html(html)
    removed = apply_regions(doc)
    amap = AnchorMap.build(doc.blocks, removed)
    layout = SingleLayout((b for b in doc.blocks if isinstance(b, Heading)), name[:-3])
    corpus = assemble_single(doc, GfmFlavor(amap, layout), name=name, source_sha256="0" * 64,
                             generated_utc="2026-10-15T12:00:00Z")
    return corpus[name]


@mock.patch.multiple(v, EXPECTED_KEPT_HEADINGS=2, EXPECTED_TYPE_NAMES=1, EXPECTED_VOCABS=0,
                     EXPECTED_DERIVED_TYPES=())
class ValidateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.md = convert(HTML)

    def failed(self, md: str) -> dict[str, list[str]]:
        report = v.validate(HTML, md)
        return {c.id: c.failures for c in report.checks if not c.passed}

    def test_tiny_pair_passes(self) -> None:
        self.assertEqual(self.failed(self.md), {})

    def test_deleted_word_fails_v2_with_section(self) -> None:
        md = self.md.replace("Intro text that", "Intro that")
        self.assertNotEqual(md, self.md)
        failures = self.failed(md)
        self.assertIn("V2", failures)
        self.assertTrue(any(f.startswith("sec 1:") and "text" in f for f in failures["V2"]))

    def test_removed_heading_fails_v1(self) -> None:
        md = self.md.replace("### 1.1 Details\n", "")
        self.assertIn("V1", self.failed(md))

    def test_broken_section_link_fails_v4(self) -> None:
        md = self.md.replace("**Type Name:**", "[x](#sec-x) **Type Name:**")
        self.assertIn("V4", self.failed(md))

    def test_code_change_fails_v3_and_table_row_fails_v8(self) -> None:
        self.assertIn("V3", self.failed(self.md.replace('"a": 1', '"a": 2')))
        self.assertIn("V8", self.failed(self.md.replace("| alpha | beta gamma |", "| alpha | gamma |")))

    def test_gfm_keeps_split_code(self) -> None:                    # GFM keeps the split JSON examples
        html = HTML.replace(*SPLIT_CODE)
        md = convert(html)
        self.assertEqual(md.count("```json"), 1)
        self.assertEqual(self.failed_on(html, md), {})
        self.assertIn("V3", self.failed_on(html, md.replace('{\n```\n\n```\n  "a": 1', '{\n\n  "a": 1')))

    def failed_on(self, html: str, md: str) -> dict[str, list[str]]:
        return {c.id: c.failures for c in v.validate(html, md).checks if not c.passed}

    def test_hygiene_and_stripped(self) -> None:
        failures = self.failed(self.md.replace("The abstract text.", "The abstract text. <span>x</span> Table of Contents"))
        self.assertIn("V6", failures)
        self.assertIn("V7", failures)


class EmphasisTests(unittest.TestCase):
    def test_literal_stars(self) -> None:                           # CommonMark delimiter runs
        for text, literal in (("**a**", 0), ("a **MUST** b", 0), ("foo*bar*", 0), ("***x***", 0),
                              ("**`)**", 0), ("[w]**)**", 0), ("Available**:**", 4), ('**"**ISO', 4),
                              ("*a****.*** The", 6), ("a * b", 1), ("*a **b** c*", 0), ("**a*", 1),
                              (" *a* ` (*x*,*y*,...) ", 0)):
            with self.subTest(text=text):
                self.assertEqual(v._literal_stars(text), literal)


if __name__ == "__main__":
    unittest.main()
