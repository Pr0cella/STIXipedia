"""Unit tests for the vault: sanitiser, VaultLayout, assemble_vault and the vault validator."""
import hashlib
import json
import logging
import unittest
from unittest import mock

from stixmd import validate as v
from stixmd.anchors import AnchorMap
from stixmd.assemble import assemble_vault
from stixmd.config import INDEX_NOTE, MANIFEST_NAME, SOURCE_URL, SPLIT_LEVEL, StixMdError, sanitise_name
from stixmd.facts import collect_facts
from stixmd.layout import VaultLayout, note_name
from stixmd.model import Heading
from stixmd.parser import parse_html
from stixmd.regions import apply_regions
from tests.test_validate import HTML_E, HTML_W, SPLIT_CODE


def H(level: int, number: str, title: str) -> Heading:
    text = f"Appendix {number}. {title}" if level == 1 and number.isalpha() else f"{number} {title}"
    return Heading(level=level, number=number, title=title, text=text)


HEADS = [H(1, "4", "STIX Domain Objects"), H(2, "4.1", "Attack Pattern"), H(3, "4.1.1", "Properties"),
         H(4, "4.1.1.1", "Deep"), H(2, "4.2", "Campaign"), H(3, "4.2.1", "Properties"),
         H(1, "B", "Relationship Summary Table")]


class SanitiserTests(unittest.TestCase):
    def test_o1_cases(self) -> None:
        cases = {"3.6.2 New Version or New Object?": "3.6.2 New Version or New Object",
                 "12.7.1 Level 1: Basic Conformance": "12.7.1 Level 1 - Basic Conformance",
                 "C.1.3 Related/Component Botnet Infrastructure": "C.1.3 Related-Component Botnet Infrastructure",
                 'a\\b *"<>|?#^[]c': "a-b c", "Appendix B. Relationship Summary Table": "Appendix B. Relationship Summary Table",
                 "x.  ": "x", "2.13 STIX™   Thing.": "2.13 STIX™ Thing"}
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(sanitise_name(text), expected)

    def test_unusable_names(self) -> None:
        for text in ("???", "CON", "...", "nul.txt"):
            with self.subTest(text=text), self.assertRaises(StixMdError):
                note_name(text)


class VaultLayoutTests(unittest.TestCase):
    def test_note_roots(self) -> None:                 # one note per chapter and section
        layout = VaultLayout(HEADS, SPLIT_LEVEL)
        roots = ["4", "4.1", "4.2", "B"]
        self.assertEqual([n.heading.number for n in layout.notes], roots)
        self.assertEqual([h.number for h in HEADS if layout.is_note_root(h.number)], roots)

    def test_level_2_mapping(self) -> None:
        layout = VaultLayout(HEADS, 2)
        self.assertEqual(layout.note_of("4"), "4 STIX Domain Objects/4 STIX Domain Objects")
        self.assertEqual(layout.note_of("4.1.1.1"), "4 STIX Domain Objects/4.1 Attack Pattern")
        self.assertEqual(layout.note_of("B"), "Appendix B. Relationship Summary Table/"
                                             "Appendix B. Relationship Summary Table")
        notes = {n.heading.number: n for n in layout.notes}
        self.assertIsNone(notes["4"].parent)
        self.assertEqual(notes["4.2"].parent, notes["4"].id)
        self.assertEqual([c.heading.number for c in layout.children(notes["4"])], ["4.1", "4.2"])
        self.assertEqual(notes["4.1"].name, "4.1 Attack Pattern")

    def test_errors(self) -> None:
        for split in (1, 3, 4):                         # only split level 2 is supported
            with self.subTest(split=split), self.assertRaises(StixMdError):
                VaultLayout(HEADS, split)
        with self.assertRaises(StixMdError):          # case-insensitive name collision
            VaultLayout([H(1, "1", "A"), H(2, "1.1", "x"), Heading(level=2, number="1.2", title="X", text="1.1 X")], 2)
        with self.assertRaises(StixMdError):          # collides with the index note
            VaultLayout([Heading(level=1, number="9", title="", text="STIX 2.1 index")], 2)
        with self.assertRaises(StixMdError):          # section before any chapter
            VaultLayout([H(2, "1.1", "x")], 2)


def build(html: str = HTML_E, *, links: bool = False) -> dict[str, str]:
    """The tiny vault; links=True gives it the document's identifiers."""
    doc = parse_html(html)
    removed = apply_regions(doc)
    source_sha256 = hashlib.sha256(html.encode("cp1252")).hexdigest()       # V9 checks it
    return assemble_vault(doc, AnchorMap.build(doc.blocks, removed), SPLIT_LEVEL, source_sha256=source_sha256,
                          generated_utc="2026-10-17T00:00:00Z", facts=collect_facts(doc) if links else None)


INTRO = "1 Introduction/1 Introduction.md"
DETAILS = "1 Introduction/1.1 Details.md"
REFS = "Appendix E. References/Appendix E. References.md"


class AssembleVaultTests(unittest.TestCase):
    def setUp(self) -> None:
        self.corpus = build()

    def test_files(self) -> None:
        self.assertEqual(list(self.corpus), [f"{INDEX_NOTE}.md", INTRO, DETAILS, REFS, MANIFEST_NAME])
        manifest = json.loads(self.corpus[MANIFEST_NAME])
        self.assertEqual(manifest["split_level"], 2)
        for entry in manifest["files"]:
            data = self.corpus[entry["path"]].encode()
            self.assertEqual((entry["sha256"], entry["bytes"]), (hashlib.sha256(data).hexdigest(), len(data)))
        self.assertNotIn("generated_utc", self.corpus[f"{INDEX_NOTE}.md"])

    def test_index_properties(self) -> None:                         # no split_level
        lines = self.corpus[f"{INDEX_NOTE}.md"].split("\n")
        keys = [ln.split(": ", 1)[0] for ln in lines[1:lines.index("---", 1)]]
        self.assertEqual(keys, ["title", "kind", "spec_title", "stage", "date", "source_url", "source_sha256",
                                "generator", "notes", "aliases", "tags"])

    def test_section_note(self) -> None:
        lines = self.corpus[DETAILS].split("\n")
        front = dict(ln.split(": ", 1) for ln in lines[1:lines.index("---", 1)])
        self.assertEqual(json.loads(front["title"]), "1.1 Details")
        self.assertEqual(json.loads(front["kind"]), "section")
        self.assertEqual(json.loads(front["aliases"]), ["Details", "widget"])
        self.assertEqual(json.loads(front["stix_types"]), ["widget"])
        self.assertEqual(json.loads(front["tags"]), ["stix/intro"])
        self.assertEqual(json.loads(front["source_url"]), SOURCE_URL + "#_bbb")
        self.assertEqual((json.loads(front["up"]), json.loads(front["prev"]), json.loads(front["next"])),
                         ("[[1 Introduction]]", "[[1 Introduction]]", "[[Appendix E. References]]"))
        nav = lines[lines.index("---", 1) + 2]
        self.assertEqual(nav, "[[STIX 2.1 Index|Index]] › [[1 Introduction|1]] · ← [[1 Introduction|1]] · "
                              "[[Appendix E. References|E]] →")
        self.assertIn("# 1.1 Details", lines)

    def test_heading_without_one_anchor_stops(self) -> None:          # no silent wrong link
        for html, n in ((HTML_E.replace("<a name='_bbb'></a>1.1", "1.1"), 0),
                        (HTML_E.replace("<a name='_bbb'></a>", "<a name='_bbb'></a><a name='_bb2'></a>"), 2)):
            with self.subTest(n=n), self.assertRaisesRegex(StixMdError, f"heading 1.1 has {n} anchors"):
                build(html)

    def test_chapter_note(self) -> None:
        text = self.corpus[INTRO]
        self.assertIn('kind: "chapter"', text)
        self.assertIn('up: "[[STIX 2.1 Index]]"', text)
        self.assertNotIn("prev:", text)
        self.assertIn("[[1.1 Details|1.1]]", text)                   # body link to another note's root
        self.assertIn(r"\[[[Appendix E. References#^ref-rfc1|RFC1]]\]", text)
        nav = "[[STIX 2.1 Index|Index]] · [[1.1 Details|1.1]] →"
        self.assertIn(f"---\n\n{nav}\n", text)                          # the NAV line, and the footer
        self.assertTrue(text.endswith(f"**Sections in this chapter**\n\n- [[1.1 Details]]\n\n{nav}\n"))
        self.assertIn('tags: ["stix/appendix"]', self.corpus[REFS])
        self.assertIn("**\\[RFC1\\]** ^ref-rfc1", self.corpus[REFS])


TINY_COUNTS = {"EXPECTED_KEPT_HEADINGS": 3, "EXPECTED_TYPE_NAMES": 1, "EXPECTED_VOCABS": 0,
               "EXPECTED_DERIVED_TYPES": (), "EXPECTED_OBJECTS": {}, "EXPECTED_FORWARD_TABLES": 0,
               "EXPECTED_TRIPLES": 0, "EXPECTED_REL_TYPES": 0, "EXPECTED_APPENDIX_B_TRIPLES": 0,
               "APPENDIX_B_ONLY": frozenset(), "FORWARD_ONLY": frozenset()}   # the spec's counts don't apply here


@mock.patch.multiple(v, **TINY_COUNTS)
class VaultValidateTests(unittest.TestCase):
    def setUp(self) -> None:
        logging.disable(logging.WARNING)
        self.corpus = build()

    def tearDown(self) -> None:
        logging.disable(logging.NOTSET)

    def failed(self, corpus: dict[str, str], root: str = "C:/v", html: str = HTML_E) -> dict[str, list[str]]:
        report = v.validate_corpus(html, corpus, "vault", root=root)
        return {c.id: c.failures for c in report.checks if not c.passed}

    def edit(self, path: str, old: str, new: str, corpus: dict[str, str] | None = None) -> dict[str, str]:
        corpus = self.corpus if corpus is None else corpus
        self.assertIn(old, corpus[path])
        return {**corpus, path: corpus[path].replace(old, new, 1)}

    def test_tiny_vault_passes(self) -> None:
        self.assertEqual(self.failed(self.corpus), {})

    def test_obsidian_dialect(self) -> None:                         # Obsidian hazards
        intro, refs = self.corpus[INTRO], self.corpus[REFS]
        self.assertIn("link to [[1.1 Details|1.1]] and", intro)
        self.assertIn(r"\[[[Appendix E. References#^ref-rfc1|RFC1]]\]", intro)
        self.assertIn("**\\[RFC1\\]** ^ref-rfc1", refs)
        self.assertIn(r"cost \$5", refs)
        self.assertFalse(any("<a id" in t for t in self.corpus.values()))

    def test_identifier_links_v10(self) -> None:                    # identifier links
        corpus = build(HTML_W, links=True)
        link = "about [`widget`](1.1%20Details.md) that"
        self.assertIn(link, corpus[INTRO])
        self.assertIn("**Type Name:** `widget`", corpus[DETAILS])           # its home section: no link
        self.assertEqual(self.failed(corpus, html=HTML_W), {})
        report = v.validate_corpus(HTML_W, corpus, "vault", root="C:/v")
        self.assertEqual(next(c for c in report.checks if c.id == "V10").stats,
                         {"idents": 1, "id_links": 1, "failures": 0})
        for new, why in (("about `widget` that", "is not linked"),
                         ("about [`widget`](1%20Introduction.md) that", "section 1, not 1.1"),
                         ("about [`widget`](1.9%20Nothing.md) that", "no note '1 Introduction/1.9 Nothing.md'"),
                         ("about [`gadget`](1.1%20Details.md) that", "is not an identifier")):
            with self.subTest(new=new):
                fails = self.failed(self.edit(INTRO, link, new, corpus), html=HTML_W)
                self.assertIn(why, fails["V10"][0])                    # `gadget` also fails V2 (a lost token)
        self_link = self.edit(DETAILS, "| alpha |", "| alpha [`widget`](1.1%20Details.md) |", corpus)
        self.assertIn("from inside its home", "\n".join(self.failed(self_link, html=HTML_W)["V10"]))
        label = self.edit(DETAILS, "**Type Name:** `widget`", "**Type Name:** `gadget`", corpus)
        self.assertIn("V10", self.failed(label, html=HTML_W))

    def test_missing_contents_entry_fails_v5(self) -> None:
        self.assertIn("V5", self.failed(self.edit(f"{INDEX_NOTE}.md", "  - [[1.1 Details]]\n", "")))

    def test_split_code_is_joined_v3(self) -> None:
        html = HTML_E.replace(*SPLIT_CODE)
        corpus = build(html)
        self.assertIn('```json\n{\n\n  "a": 1\n}\n```', corpus[DETAILS])
        self.assertEqual(self.failed(corpus, html=html), {})
        split = self.edit(DETAILS, '{\n\n  "a": 1', '{\n```\n\n```\n  "a": 1', corpus)
        self.assertIn("count html 1 != md 2", self.failed(split, html=html)["V3"])

    def test_other_split_level_fails_v9(self) -> None:               # the manifest must say level 2
        manifest = json.loads(self.corpus[MANIFEST_NAME])
        for split in (1, 3):
            with self.subTest(split=split):
                text = json.dumps({**manifest, "split_level": split}, indent=2) + "\n"
                self.assertEqual(self.failed({**self.corpus, MANIFEST_NAME: text}),
                                 {"V9": [f"manifest split_level {split}"]})

    def test_broken_wikilink_fails_v4(self) -> None:
        for old, new in (("[[1.1 Details|1.1]]", "[[1.9 Nothing|1.1]]"),
                         ("#^ref-rfc1|", "#^ref-rfc9|"),
                         ("[[1.1 Details|1.1]]", "[[1.1 Details#No such heading|1.1]]"),
                         ("link to [[1.1 Details|1.1]]", "link to [1.1](#sec-1.1)")):    # a GFM-style link
            with self.subTest(new=new):
                self.assertIn("V4", self.failed(self.edit(INTRO, old, new)))

    def test_missing_and_extra_notes(self) -> None:
        missing = {k: t for k, t in self.corpus.items() if k != DETAILS}
        self.assertIn("V9", self.failed(missing))
        extra = {**self.corpus, "1 Introduction/My note.md": "---\ntitle: \"x\"\n---\n\nmine\n"}
        failures = self.failed(extra)
        self.assertIn("V9", failures)
        self.assertIn("V5", failures)                                 # not linked from the index

    def test_duplicate_note_name_fails_v9(self) -> None:
        dup = {**self.corpus, "Appendix E. References/1.1 details.md": self.corpus[DETAILS]}
        self.assertTrue(any("used 2 times" in f for f in self.failed(dup)["V9"]))

    def test_broken_chain_and_properties_fail_v9(self) -> None:
        for path, old, new in ((DETAILS, 'next: "[[Appendix E. References]]"', 'next: "[[1 Introduction]]"'),
                               (DETAILS, 'up: "[[1 Introduction]]"', 'up: "[[STIX 2.1 Index]]"'),
                               (DETAILS, 'kind: "section"', "kind: section"),
                               (DETAILS, "[[STIX 2.1 Index|Index]] ›", "Index ›")):
            with self.subTest(new=new):
                self.assertIn("V9", self.failed(self.edit(path, old, new)))

    def test_manifest_and_path_length_fail_v9(self) -> None:
        changed = {**self.corpus, DETAILS: self.corpus[DETAILS] + "x\n"}      # manifest hash no longer matches
        self.assertTrue(any("manifest hash" in f for f in self.failed(changed)["V9"]))
        self.assertIn("V9", self.failed(self.corpus, root="C:/" + "x" * 200))

    def test_hazards_fail_v6(self) -> None:
        for old, new in ((r"cost \$5", "cost $5"), ("The RFC1 text", "The ==RFC1== text"),
                         ("The RFC1 text", "The #RFC1 text"), ("The RFC1 text", "The %%RFC1 text"),
                         ("The RFC1 text", '<a id="x"></a>The RFC1 text'),
                         ("The RFC1 text", "The ~~RFC1~~ text"),                       # strikethrough
                         ("The RFC1 text", "The RFC1**** text"),                       # literal asterisks
                         ("The RFC1 text", "The RFC1**:** text"),
                         (r"cost \$5.", r"cost \$5. ^stray"),                          # a stray block id
                         ("** ^ref-rfc1", "** ^ref-rfc2")):
            with self.subTest(new=new):
                self.assertIn("V6", self.failed(self.edit(REFS, old, new)))

    def test_code_spans_are_not_hazards(self) -> None:
        failed = self.failed(self.edit(REFS, "The RFC1 text", "The RFC1 `$x == #y %%` text"))
        self.assertNotIn("V6", failed)                  # V2 reports the inserted tokens x, y


if __name__ == "__main__":
    unittest.main()
