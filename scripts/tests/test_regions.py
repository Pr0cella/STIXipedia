"""Unit tests for stixmd.regions."""
import unittest

from stixmd.config import StixMdError
from stixmd.inline import plain_text
from stixmd.model import Block, Document, Heading, Paragraph, Span
from stixmd.regions import _iso_date, apply_regions


def para(text: str, *, bold: bool = False, href: str | None = None, css: str = "MsoNormal") -> Paragraph:
    return Paragraph(spans=[Span(text=text, bold=bold, href=href)], css_class=css)


def heading(level: int, text: str, number: str) -> Heading:
    return Heading(level=level, number=number, title=text, text=text)


def cover_blocks(links_per_stage: int = 3) -> list[Block]:
    blocks: list[Block] = [para("STIX Version 2.1", css="MsoTitle"), para("OASIS Standard", css="MsoSubtitle"),
                           para("10 June 2021", css="MsoSubtitle")]
    for label, stem in (("This stage:", "os"), ("Previous stage:", "cs02"), ("Latest stage:", "latest")):
        blocks.append(para(label, bold=True))
        for ext in ("docx", "html", "pdf")[:links_per_stage]:
            url = f"https://docs.oasis-open.org/{stem}/spec.{ext}"
            spans = [Span(text=url, href=url)] + ([Span(text=" (Authoritative)")] if ext == "docx" else [])
            blocks.append(Paragraph(spans=spans))
    blocks += [para("Editors:", bold=True), para("Someone"), para("Abstract:", bold=True),
               para("The abstract text."), para("Status:", bold=True), para("Status text.")]
    return blocks


class RegionTests(unittest.TestCase):
    def test_strip_and_cover(self) -> None:
        intro, body = heading(1, "1 Intro", "1"), para("kept text")
        doc = Document(blocks=[*cover_blocks(), heading(1, "Notices", ""), para("legal"),
                               para("Table of Contents", bold=True), intro, body,
                               heading(1, "Appendix F. X", "F"), para("thanks")])
        removed = apply_regions(doc)
        self.assertEqual(doc.blocks, [intro, body])
        self.assertIn("legal", [plain_text(b.spans) for b in removed if isinstance(b, Paragraph)])
        cover = doc.cover
        assert cover is not None
        self.assertEqual((cover.title, cover.stage, cover.date_text, cover.date_iso),
                         ("STIX Version 2.1", "OASIS Standard", "10 June 2021", "2021-06-10"))
        self.assertEqual([label for label, _ in cover.stage_links], ["This stage:", "Previous stage:", "Latest stage:"])
        self.assertEqual(cover.stage_links[0][1][0], ("https://docs.oasis-open.org/os/spec.docx", "(Authoritative)"))
        self.assertEqual(cover.stage_links[0][1][1], ("https://docs.oasis-open.org/os/spec.html", ""))
        self.assertEqual([plain_text(p) for p in cover.abstract], ["The abstract text."])

    def test_iso_date_is_locale_free(self) -> None:
        self.assertEqual(_iso_date("1 January 2020"), "2020-01-01")
        for bad in ("10 Juni 2021", "June 10 2021", "10 june 2021"):
            with self.subTest(bad=bad), self.assertRaises(StixMdError):
                _iso_date(bad)

    def test_cover_checks(self) -> None:
        with self.assertRaises(StixMdError):
            apply_regions(Document(blocks=[*cover_blocks(links_per_stage=2), heading(1, "1 Intro", "1")]))
        bad_title = [para("Other Spec", css="MsoTitle"), *cover_blocks()[1:], heading(1, "1 Intro", "1")]
        with self.assertRaises(StixMdError):
            apply_regions(Document(blocks=bad_title))


if __name__ == "__main__":
    unittest.main()
