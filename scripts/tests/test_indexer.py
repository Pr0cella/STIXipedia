"""Unit tests for stixmd.indexer."""
import re
import unittest

from stixmd.indexer import build_markdown, plain_yaml, vault_bases
from stixmd.model import Cover, Document, Heading, Paragraph, Span


def make_doc() -> tuple[Document, list[str], list[tuple[Heading, int]]]:
    h1 = Heading(level=1, number="1", title="Intro", text="1 Intro")
    h2 = Heading(level=2, number="1.1", title="Sub", text="1.1 Sub")
    links = [(f"https://e.org/x.{e}", "(Authoritative)" if e == "docx" else "") for e in ("docx", "html", "pdf")]
    cover = Cover(title="STIX Version 2.1", stage="OASIS Standard", date_text="10 June 2021", date_iso="2021-06-10",
                  stage_links=[(label, links) for label in ("This stage:", "Previous stage:", "Latest stage:")],
                  abstract=[[Span(text="Abstract text.")]])
    blocks = [h1, Paragraph(spans=[Span(text="Type Name: widget")]), h2]
    body = ['<a id="sec-1"></a>', "## 1 Intro", "**Type Name:** `widget`", '<a id="sec-1.1"></a>', "### 1.1 Sub", "y"]
    return Document(blocks=blocks, cover=cover), body, [(h1, 0), (h2, 3)]


class IndexerTests(unittest.TestCase):
    def test_ranges_and_layout(self) -> None:
        doc, body, heads = make_doc()
        text = build_markdown(doc, body, heads, source_sha256="ab" * 32, generated_utc="2026-10-15T12:00:00Z")
        lines = text.split("\n")
        self.assertTrue(text.endswith("y\n"))
        self.assertEqual(lines[0], "---")
        self.assertIn('date: "2021-06-10"', lines)
        self.assertIn("sections: 2", lines)
        self.assertIn("- [https://e.org/x.docx](https://e.org/x.docx) (Authoritative)", lines)
        offset = lines.index('<a id="sec-1"></a>')
        self.assertEqual(lines[offset - 2:offset], ["---", ""])
        total = len(lines) - 1
        contents = [ln for ln in lines if re.match(r"^ *- \[1", ln)]
        self.assertEqual(contents, [f"- [1 Intro](#sec-1) · L{offset + 1}-L{total}",
                                    f"  - [1.1 Sub](#sec-1.1) · L{offset + 4}-L{total}"])
        self.assertIn(f"| `widget` | [1 Intro](#sec-1) | L{offset + 1}-L{total} |", lines)

    def test_header_length_is_stable(self) -> None:
        doc, body, heads = make_doc()
        a = build_markdown(doc, body, heads, source_sha256="0", generated_utc="t1")
        b = build_markdown(doc, body + ["more"] * 500, heads, source_sha256="0", generated_utc="t2")
        self.assertEqual(a.split("\n").index('<a id="sec-1"></a>'), b.split("\n").index('<a id="sec-1"></a>'))


# our STIX Relationships.base after Obsidian opened and re-saved it (2026-10-18), byte for byte
XF7_RELATIONSHIPS_BASE = (
    "filters:\n"
    "  and:\n"
    '    - file.hasProperty("relationship_types")\n'
    "views:\n"
    "  - type: table\n"
    "    name: Relationships\n"
    "    order:\n"
    "      - file.name\n"
    "      - stix_types\n"
    "      - relationship_types\n"
    "      - relationship_targets\n")


class BasesStyleTests(unittest.TestCase):
    def test_relationships_base_equals_obsidian_copy(self) -> None:
        self.assertEqual(vault_bases()["STIX Relationships.base"], XF7_RELATIONSHIPS_BASE)

    def test_plain_scalars_and_bare_columns(self) -> None:
        for name, text in vault_bases().items():
            with self.subTest(name=name):
                self.assertFalse(re.search(r'(^|: |- )"', text, re.M), "a quoted scalar")
                self.assertNotIn("note.", text)

    def test_unsafe_scalars_are_refused(self) -> None:
        self.assertEqual(plain_yaml({"a": ["All objects", 'file.hasTag("stix/sdo")']}),
                         ["a:", "  - All objects", '  - file.hasTag("stix/sdo")'])
        for bad in ("", " x", "x ", "- x", "*x", "a: b", "a #b", "true", "No", "null", "~", "12", "1.5", "0x1F", '"x'):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                plain_yaml({"a": bad})


if __name__ == "__main__":
    unittest.main()
