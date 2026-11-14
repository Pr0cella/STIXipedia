"""Unit tests for the Obsidian flavour: escaping, links, block ids, emphasis, identifier links."""
import logging
import unittest
from unittest import mock

from stixmd import flavor as flavor_module
from stixmd.anchors import AnchorMap
from stixmd.config import StixMdError
from stixmd.facts import Ident, SpecFacts
from stixmd.flavor import GfmFlavor, ObsidianFlavor, heading_target, obsidian_block_id, obsidian_escape, wikilink
from stixmd.inline import render_spans
from stixmd.layout import Layout, SingleLayout
from stixmd.model import Cell, CodeBlock, Heading, Paragraph, Span, Table, Target
from stixmd.render import render_body


def T(kind: str, key: str) -> Target:
    return Target(kind=kind, key=key)


def H(level: int, number: str, title: str, anchors: list[str] | None = None) -> Heading:
    text = f"Appendix {number}. {title}" if level == 1 and number.isalpha() else f"{number} {title}"
    return Heading(level=level, number=number, title=title, text=text, anchors=anchors or [])


class FakeVaultLayout(Layout):
    """Split level 2: chapters and h2 sections are note roots; deeper headings live in their h2's note."""
    NOTES = {"4": "4 SDOs/4 SDOs", "4.1": "4 SDOs/4.1 Attack Pattern", "4.1.1": "4 SDOs/4.1 Attack Pattern",
             "4.2": "4 SDOs/4.2 Campaign", "E": "Appendix E. References/Appendix E. References",
             "E.1": "Appendix E. References/E.1 Normative References"}

    def note_of(self, number: str) -> str:
        self.heading(number)
        return self.NOTES[number]

    def is_note_root(self, number: str) -> bool:
        return self.heading(number).level <= 2


class EscapeTests(unittest.TestCase):
    def test_hazard_table(self) -> None:          # Obsidian hazards, one row each
        cases = {"Step #: 1": r"Step \#: 1", "#tag": r"\#tag", "a#b": "a#b", "cost $5": r"cost \$5",
                 "a == b": r"a =\= b", "a === b": r"a =\=\= b", "50%% x": r"50%\% x",
                 "[[x]] and ![[y]]": r"\[\[x\]\] and !\[\[y\]\]", "[RFC1]": r"\[RFC1\]",
                 "note ^ref-a": r"note \^ref-a", "x^2": "x^2",
                 "snake_case _x * y": r"snake_case \_x \* y", "<b> &amp;": r"\<b> \&amp;"}
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(obsidian_escape(raw), expected)

    def test_heading_target(self) -> None:
        self.assertEqual(heading_target("12.7.1 Level 1: Basic Conformance"), "12.7.1 Level 1 Basic Conformance")
        self.assertEqual(heading_target("a#b|c^d %%e [[f]]"), "a b c d e f")
        self.assertEqual(heading_target("4.1 Attack Pattern"), "4.1 Attack Pattern")

    def test_wikilink_alias_rules(self) -> None:  # bracket rule
        self.assertEqual(wikilink("#^ref-rfc7493", "[RFC7493]"), r"\[[[#^ref-rfc7493|RFC7493]]\]")
        self.assertEqual(wikilink("#a", " x|y[z] "), "[[#a|xyz]]")
        self.assertEqual(wikilink("Note", ""), "[[Note]]")

    def test_block_ids(self) -> None:
        self.assertEqual(obsidian_block_id(T("ref", "x.509")), "ref-x-509")
        self.assertEqual(obsidian_block_id(T("ref", "fm-2-22.3")), "ref-fm-2-22-3")
        self.assertEqual(obsidian_block_id(T("block", "toc1.2")), "p-toc1-2")


class LinkTableTests(unittest.TestCase):
    """Every vault link-resolution case, plus the single-file forms."""

    def setUp(self) -> None:
        self.para = Paragraph(spans=[Span(text="anchored")], anchors=["_para"])
        self.in_table = Paragraph(spans=[Span(text="cell")], anchors=["_cell"])
        self.label = Paragraph(spans=[Span(text="[X.509]", bold=True)], anchors=["_x509"])
        self.kept = [H(1, "4", "SDOs"), H(2, "4.1", "Attack Pattern"), H(3, "4.1.1", "Properties: Common"),
                     self.para, Table(rows=[[Cell(blocks=[self.in_table])]]), H(2, "4.2", "Campaign"),
                     H(1, "E", "References"), H(2, "E.1", "Normative References"), self.label]
        self.amap = AnchorMap.build(self.kept, [])
        heads = [b for b in self.kept if isinstance(b, Heading)]
        self.vault = ObsidianFlavor(self.amap, FakeVaultLayout(heads))
        self.single = ObsidianFlavor(self.amap, SingleLayout(heads, "STIX2.1.obsidian"))

    def link(self, flavor: ObsidianFlavor, target: Target, note: str, plain: str = "x") -> str:
        return flavor.link(target, "ignored **inner**", plain, note)

    def test_vault_rows(self) -> None:
        here = "4 SDOs/4.1 Attack Pattern"
        f = self.vault
        self.assertEqual(self.link(f, T("section", "4.1"), here), "[[4.1 Attack Pattern|x]]")   # own root
        self.assertEqual(self.link(f, T("section", "4.1.1"), here), "[[#4.1.1 Properties Common|x]]")
        self.assertEqual(self.link(f, T("section", "4.2"), here), "[[4.2 Campaign|x]]")         # other root
        self.assertEqual(self.link(f, T("section", "4.1.1"), "4 SDOs/4.2 Campaign"),
                         "[[4.1 Attack Pattern#4.1.1 Properties Common|x]]")                     # other inner
        self.assertEqual(self.link(f, T("ref", "x.509"), here, "[X.509]"),
                         r"\[[[E.1 Normative References#^ref-x-509|X.509]]\]")
        self.assertEqual(self.link(f, T("block", "para"), "4 SDOs/4.2 Campaign"),
                         "[[4.1 Attack Pattern#^p-para|x]]")
        self.assertEqual(self.link(f, T("block", "para"), here), "[[#^p-para|x]]")
        self.assertEqual(self.link(f, T("url", "https://e.org/a b"), here), "[ignored **inner**](https://e.org/a%20b)")

    def test_single_file_forms(self) -> None:
        f, note = self.single, "STIX2.1.obsidian"
        self.assertEqual(self.link(f, T("section", "4"), note), "[[#4 SDOs|x]]")
        self.assertEqual(self.link(f, T("ref", "x.509"), note, "X.509"), "[[#^ref-x-509|X.509]]")

    def test_block_inside_table_links_to_section(self) -> None:
        logging.disable(logging.INFO)
        try:
            self.assertEqual(self.link(self.single, T("block", "cell"), "STIX2.1.obsidian"),
                             "[[#4.1.1 Properties Common|x]]")
        finally:
            logging.disable(logging.NOTSET)

    def test_anchor_emission(self) -> None:
        f = self.single
        self.assertEqual(f.anchor_lines([T("section", "4")]), [])
        self.assertEqual(f.anchor_suffix([T("ref", "x.509")]), " ^ref-x-509")
        self.assertEqual(f.anchor_suffix([T("section", "4")]), "")
        with self.assertRaises(StixMdError):
            f.anchor_suffix([T("block", "a"), T("block", "b")])

    def test_block_id_collision(self) -> None:
        kept = [H(1, "E", "References"), Paragraph(spans=[Span(text="[X.509]")]),
                Paragraph(spans=[Span(text="[X-509]")])]
        amap = AnchorMap.build(kept, [])
        GfmFlavor(amap, SingleLayout([kept[0]]))                 # GFM ids ref-x.509 / ref-x-509 differ
        with self.assertRaises(StixMdError):
            ObsidianFlavor(amap, SingleLayout([kept[0]]))


class RenderTests(unittest.TestCase):
    def test_emphasis_merge_only_in_obsidian(self) -> None:
        spans = [Span(text="dll_characteristics", bold=True, code=True), Span(text="_hex", bold=True),
                 Span(text=" (optional)")]
        head = H(1, "6", "SCOs")
        amap = AnchorMap.build([head], [])
        obs = ObsidianFlavor(amap, SingleLayout([head])).writer("")
        gfm = GfmFlavor(amap, SingleLayout([head])).writer("")
        self.assertEqual(render_spans(spans, obs), r"**`dll_characteristics`\_hex** (optional)")
        self.assertEqual(render_spans(spans, gfm), r"**`dll_characteristics`****\_hex** (optional)")
        self.assertEqual(render_spans([Span(text="a "), Span(text="x ", bold=True), Span(text="y")], obs),
                         "a **x** y")

    def test_body_lines(self) -> None:
        label = Paragraph(spans=[Span(text="[RFC1]", bold=True)])
        blocks = [H(1, "E", "References"), label, Paragraph(spans=[Span(text="Text.")])]
        flavor = ObsidianFlavor(AnchorMap.build(blocks, []), SingleLayout([blocks[0]]))
        lines, heads = render_body(blocks, flavor)
        self.assertEqual(lines, ["## Appendix E. References", "", r"**\[RFC1\]** ^ref-rfc1", "", "Text."])
        self.assertEqual(heads, [(blocks[0], 0)])


def C(text: str, **flags: object) -> Span:
    return Span(text=text, code=True, **flags)


class IdentifierLinkTests(unittest.TestCase):
    """Identifier-link destinations for every placement of the home section, and where no link is written."""

    def setUp(self) -> None:
        self.kept = [H(1, "4", "SDOs"), H(2, "4.1", "Attack Pattern"), H(3, "4.1.1", "Properties: Common"),
                     H(2, "4.2", "Campaign"), H(1, "E", "References"), H(2, "E.1", "Normative References")]
        heads = self.kept
        amap = AnchorMap.build(self.kept, [])
        idents = {"props": Ident(name="props", kind="type", section="4.1.1"),
                  "campaign": Ident(name="campaign", kind="type", section="4.2"),
                  "ref-type": Ident(name="ref-type", kind="type", section="E.1")}
        facts = SpecFacts(idents=idents, objects={}, vocabularies={}, appendix_b=set())
        self.vault = ObsidianFlavor(amap, FakeVaultLayout(heads), facts)
        self.single = ObsidianFlavor(amap, SingleLayout(heads, "STIX2.1.obsidian"), facts)
        self.gfm = GfmFlavor(amap, SingleLayout(heads, "STIX2.1"))

    def test_vault_destinations(self) -> None:
        f, here = self.vault, "4 SDOs/4.2 Campaign"
        self.assertEqual(f.code_dest("props", here, "4.2"),
                         "4.1%20Attack%20Pattern.md#4.1.1%20Properties%20Common")      # inner heading, unsafe -> space
        self.assertEqual(f.code_dest("campaign", "4 SDOs/4.1 Attack Pattern", "4.1"), "4.2%20Campaign.md")
        self.assertEqual(f.code_dest("ref-type", here, "4.2"),
                         "../Appendix%20E.%20References/E.1%20Normative%20References.md")   # other folder
        self.assertEqual(f.code_dest("props", "4 SDOs/4.1 Attack Pattern", "4.1"),
                         "4.1%20Attack%20Pattern.md#4.1.1%20Properties%20Common")      # same note, other section

    def test_single_destinations(self) -> None:
        note = "STIX2.1.obsidian"
        self.assertEqual(self.single.code_dest("props", note, "4.2"), "#4.1.1%20Properties%20Common")
        self.assertEqual(self.single.code_dest("campaign", note, "4.1"), "#4.2%20Campaign")

    def test_no_link(self) -> None:
        f, here = self.vault, "4 SDOs/4.1 Attack Pattern"
        self.assertIsNone(f.code_dest("campaign", "4 SDOs/4.2 Campaign", "4.2"))     # its home section
        self.assertIsNone(f.code_dest("props", here, "4.1.1"))                        # its home
        self.assertIsNone(f.code_dest("unknown", here, "4.1"))
        with mock.patch.object(flavor_module, "ID_LINK_SKIP", {("4.1", "campaign")}):
            self.assertIsNone(f.code_dest("campaign", here, "4.1"))
            self.assertEqual(f.code_dest("campaign", here, "4.1.1"), "4.2%20Campaign.md")

    def test_od2_span_rules(self) -> None:
        writer = self.vault.writer("4 SDOs/4.1 Attack Pattern", "4.1")
        spans = [S("a "), C("campaign"), S(", "), C("campaign", bold=True), S(", "),
                 C("campaign", href="https://example.org"), S(", "), C("campaign, props"), S(" and "),
                 C("campaign", italic=True)]
        self.assertEqual(render_spans(spans, writer),
                         "a [`campaign`](4.2%20Campaign.md), **`campaign`**, [`campaign`](https://example.org), "
                         "`campaign, props` and *[`campaign`](4.2%20Campaign.md)*")

    def test_writer_follows_the_section(self) -> None:
        para = Paragraph(spans=[S("See "), C("props"), S(".")])
        blocks = [H(2, "4.1", "Attack Pattern"), para, H(3, "4.1.1", "Properties: Common"), para,
                  CodeBlock(lines=["props"]), H(2, "4.2", "Campaign"), para]
        lines, _ = render_body(blocks, self.single, note="STIX2.1.obsidian")
        self.assertEqual([ln for ln in lines if ln.startswith("See")],
                         ["See [`props`](#4.1.1%20Properties%20Common).", "See `props`.",
                          "See [`props`](#4.1.1%20Properties%20Common)."])
        self.assertIn("props", lines)                                                 # the code block is plain
        self.assertEqual(render_body(blocks, self.gfm)[0].count("See `props`."), 3)   # GFM never links


def S(text: str) -> Span:
    return Span(text=text)


if __name__ == "__main__":
    unittest.main()
