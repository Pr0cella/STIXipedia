"""Regression tests on the real spec: the merged JSON examples, punctuation-only emphasis, and the validator
checks with the mutations they must reject. The outputs come from tests.realspec (built once per run)."""
import unittest

from stixmd import config
from stixmd import validate as v
from stixmd.assemble import manifest_text
from stixmd.inline import code_fence
from stixmd.model import Block, CodeBlock, Table
from stixmd.render import bracket_depth, merge_split_code
from tests import realspec

ARTIFACT = "6 STIX Cyber-observable Objects/6.1 Artifact Object.md"
ATTACK = "4 STIX Domain Objects/4.1 Attack Pattern.md"
INDICATOR = "4 STIX Domain Objects/4.7 Indicator.md"
LOCATION = "4 STIX Domain Objects/4.10 Location.md"
MALWARE = "4 STIX Domain Objects/4.11 Malware.md"
COMPARISON = "9 STIX Patterning/9.6 Comparison Expressions.md"
CUSTOM = "11 Customizing STIX (Deprecated)/11.1 Custom Properties (Deprecated).md"
NORMATIVE = "Appendix E. References/E.1 Normative References.md"
GROUPING = "4 STIX Domain Objects/4.4 Grouping.md"
SDOS = "4 STIX Domain Objects/4 STIX Domain Objects.md"
INTRO = "1 Introduction/1 Introduction.md"
APPENDIX_B = "Appendix B. Relationship Summary Table/Appendix B. Relationship Summary Table.md"
INDEX = config.INDEX_NOTE + ".md"
ATLAS = config.ATLAS_NOTE + ".md"


def code_blocks(blocks: list[Block], *, cells: bool) -> list[CodeBlock]:
    out = []
    for b in blocks:
        if isinstance(b, Table):
            out += [c for row in b.rows for cell in row for c in code_blocks(cell.blocks, cells=True)] if cells else []
        elif isinstance(b, CodeBlock):
            out.append(b)
    return out


class FixesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        real = realspec.outputs()
        for name in ("html", "sha", "doc", "gfm", "vault2", "facts"):
            setattr(cls, name, getattr(real, name))

    # ---- merged JSON examples -------------------------------------------------------------------------------
    def test_soft_breaks(self) -> None:                             # 46 pairs, all in the body
        body = code_blocks(self.doc.blocks, cells=False)
        flagged = [b for b in body if b.soft_break_before]
        self.assertEqual(len(flagged), 46)
        self.assertFalse(any(b.anchors for b in flagged))
        self.assertEqual(sum(b.soft_break_before for b in code_blocks(self.doc.blocks, cells=True)), 46)
        self.assertEqual([tid for _, tid, soft in self.facts.runs if soft], [None] * 46)   # the validator agrees

    def test_three_merges(self) -> None:                            # 10, 8 and 8 lines, all json
        before = code_blocks(self.doc.blocks, cells=False)
        merged = merge_split_code(self.doc.blocks)
        self.assertEqual(len(merged), len(self.doc.blocks) - 3)
        new = [b for b in merged if isinstance(b, CodeBlock) and not any(b is o for o in before)]
        self.assertEqual([(len(b.lines), code_fence(b.lines)[0]) for b in new], [(10, "```json"), (8, "```json"),
                                                                                  (8, "```json")])
        self.assertEqual([bracket_depth(b.lines) for b in new], [0, 0, 0])
        unbalanced = [b for b in merged if isinstance(b, CodeBlock) and bracket_depth(b.lines)]
        self.assertEqual([(len(b.lines), bracket_depth(b.lines)) for b in unbalanced], [(11, 1)])   # 4.6.2, kept
        self.assertEqual(code_blocks(self.doc.blocks, cells=False), before)      # the Document is unchanged

    def test_v3_and_restored_split(self) -> None:
        check = v._v3(self.facts, v._read_vault(self.vault2).virtual)
        self.assertEqual((check.passed, check.stats["html"], check.stats["md"]), (True, 218, 218))
        split = self.vault_with(ARTIFACT, '```json\n{\n\n  "type": "artifact",',
                                '```json\n{\n```\n\n```\n  "type": "artifact",')
        failed = v._v3(self.facts, v._read_vault(split).virtual)
        self.assertFalse(failed.passed)
        self.assertIn("count html 218 != md 219", failed.failures)

    # ---- punctuation-only emphasis and literal asterisks ----------------------------------------------------
    def v6(self, corpus: dict[str, str]) -> v.Check:
        return v._v6_v7_vault(v._read_vault(corpus))[0]

    def test_punctuation_runs(self) -> None:
        for text, count in (("Available:\n", 1), ('\n"ISO ISO 3166-1:2013 ', 1), ('\n"ISO 3166-2:2020 ', 1),
                            ("specified by *a*. The return value", 1), ("**`type`)**", 1),
                            ("Appendix C]])\n", 2), ("**.**", 0), ("**)**", 0), ("*.*", 0), ('**"**', 0)):
            with self.subTest(text=text):
                self.assertEqual(sum(t.count(text) for t in self.vault2.values()), count)
        check = self.v6(self.vault2)
        self.assertEqual((check.passed, check.stats["emphasis_lines"]), (True, 0))

    def test_restored_asterisks_fail_v6(self) -> None:              # literal-asterisk mutations on the real note
        for path, old, new in ((NORMATIVE, "Available:\n", "Available**:**\n"),
                               (COMPARISON, "specified by *a*. The", "specified by *a****.*** The"),
                               (NORMATIVE, "Available:\n", "Available~~:~~\n")):
            with self.subTest(new=new):
                check = self.v6(self.vault_with(path, old, new))
                self.assertFalse(check.passed)
        restored = self.vault_with(NORMATIVE, "Available:\n", "Available**:**\n")
        self.assertEqual(self.v6(restored).stats["emphasis_lines"], 1)


    # ---- how-to examples (V17) ------------------------------------------------------------------------------
    def v17_vault(self, corpus: dict[str, str], old: str = "", new: str = "") -> v.Check:
        index = f"{config.INDEX_NOTE}.md"
        if old:
            self.assertIn(old, corpus[index])
        return v._v17_vault(v._read_vault({**corpus, index: corpus[index].replace(old, new, 1)}))

    def test_how_to_passes_v17(self) -> None:
        check = self.v17_vault(self.vault2)
        self.assertEqual((check.passed, check.stats["examples"]), (True, 17), check.failures)

    def test_tool_usage_fails_v17(self) -> None:                   # the outputs are used standalone
        for text in (" Regenerate the vault to refresh it.", " Run `scripts/stix2md.py --split-level 3`.",
                     " Your edits are kept in the `.bak` folder.", " The run report prints a `hint:` line.",
                     " See the README.", " The `split_level` Property shows the level.", " At split level 2, …"):
            with self.subTest(text=text):
                failed = self.v17_vault(self.vault2, "a read-only reference.", "a read-only reference." + text)
                self.assertTrue(any(f.startswith("tool usage") for f in failed.failures), failed.failures)

    def test_atlas_tool_usage_fails_v17(self) -> None:             # V17: the atlas text too
        atlas = f"{config.ATLAS_NOTE}.md"
        corpus = {**self.vault2, atlas: self.vault2[atlas].replace("opens its note.", "opens its note. See the README.")}
        self.assertNotEqual(corpus[atlas], self.vault2[atlas])
        failed = v._v17_vault(v._read_vault(corpus))
        self.assertTrue(any(f.startswith(f"{atlas}: tool usage") for f in failed.failures), failed.failures)

    # ---- mutations: duplicated content, link targets, the R4b link ------------------------------------------
    def gfm_md(self, text: str | None = None) -> v._Md:
        return v._md_facts(self.gfm if text is None else text, "gfm")

    def vault_with(self, path: str, old: str, new: str, *, count: int = 1) -> dict[str, str]:
        """The level-2 vault with one edit and a recomputed manifest, so V9's hash check can't hide it."""
        self.assertIn(old, self.vault2[path], path)
        corpus = {p: t for p, t in self.vault2.items() if p != config.MANIFEST_NAME}
        corpus[path] = corpus[path].replace(old, new, count)
        corpus[config.MANIFEST_NAME] = manifest_text(corpus, split_level=config.SPLIT_LEVEL,
                                                     source_sha256=self.sha, generated_utc="2026-10-18T00:00:00Z")
        return corpus

    @staticmethod
    def first_para(text: str, heading: str) -> str:
        lines = text.split("\n")
        return next(ln for ln in lines[lines.index(heading) + 1:] if ln[:1].isalpha())

    @staticmethod
    def in_section(text: str, heading: str, old: str, new: str) -> str:
        at = text.index(old, text.index(heading + "\n"))
        return text[:at] + new + text[at + len(old):]

    def test_clean_outputs_pass_x4_checks(self) -> None:
        vault = v._read_vault(self.vault2)
        for fmt, v2, v4 in (("gfm", v._v2(self.facts, self.gfm_md()), v._v4(self.facts, self.gfm_md())),
                            ("vault", v._v2(self.facts, vault.virtual), v._v4_vault(self.facts, vault))):
            with self.subTest(fmt=fmt):
                self.assertTrue(v2.passed and v4.passed, v2.failures + v4.failures)
                self.assertEqual((v2.stats["extra_md_tokens"], v4.stats["targets"]), (103, 214))
        for check in (v._v5_vault(self.facts, vault), v._v9(self.facts, vault, self.vault2, "markdown/STIX2.1-vault")):
            self.assertTrue(check.passed, (check.id, check.failures))

    def test_repeated_content_fails_v2(self) -> None:
        text = self.gfm
        p411, p47 = self.first_para(text, "### 4.11 Malware"), self.first_para(text, "### 4.7 Indicator")
        row = next(ln for ln in text[text.index("### 4.1 Attack Pattern"):].split("\n")
                   if ln.startswith("| **`name`** (required)"))
        for name, mutated in (("4.11 repeated", text.replace(p411, p411 + "\n\n" + p411, 1)),
                              ("4.7 repeated", text.replace(p47, p47 + "\n\n" + p47, 1)),
                              ("table row repeated", text.replace(row, row + "\n" + row, 1)),
                              ("4.11 copied into 4.7", text.replace(p47, p47 + "\n\n" + p411, 1))):
            with self.subTest(fmt="gfm", mutation=name):
                self.assertFalse(v._v2(self.facts, self.gfm_md(mutated)).passed)
        p411, p47 = self.first_para(self.vault2[MALWARE], "# 4.11 Malware"), self.first_para(self.vault2[INDICATOR],
                                                                                              "# 4.7 Indicator")
        row = next(ln for ln in self.vault2[ATTACK].split("\n") if ln.startswith("| **`name`** (required)"))
        for name, (path, old, new) in (("4.11 repeated", (MALWARE, p411, p411 + "\n\n" + p411)),
                                       ("4.7 repeated", (INDICATOR, p47, p47 + "\n\n" + p47)),
                                       ("table row repeated", (ATTACK, row, row + "\n" + row)),
                                       ("4.11 copied into 4.7", (INDICATOR, p47, p47 + "\n\n" + p411))):
            with self.subTest(fmt="vault", mutation=name):
                vault = v._read_vault(self.vault_with(path, old, new))
                self.assertFalse(v._v2(self.facts, vault.virtual).passed)

    def test_wrong_link_targets_fail(self) -> None:                   # link targets: V4 body links
        gfm = self.in_section(self.gfm, "### 3.2 Common Properties", "(#sec-3.6)", "(#sec-3.3)")
        self.assertFalse(v._v4(self.facts, self.gfm_md(gfm)).passed)
        corpus = self.vault_with("1 Introduction/1.2 Overview.md", "[[9 STIX Patterning|9]]",
                                 "[[8 STIX Bundle Object|9]]")
        self.assertFalse(v._v4_vault(self.facts, v._read_vault(corpus)).passed)

    def test_wrong_nav_and_child_lists_fail_v9(self) -> None:         # link targets: V9
        for path, old, new in (("4 STIX Domain Objects/4.1 Attack Pattern.md", "[[4.2 Campaign|4.2]] →",
                                "[[4.3 Course of Action|4.2]] →"),
                               ("4 STIX Domain Objects/4.1 Attack Pattern.md", "[[4 STIX Domain Objects|4]] ·",
                                "[[3 STIX General Concepts|4]] ·"),
                               ("4 STIX Domain Objects/4 STIX Domain Objects.md", "- [[4.2 Campaign]]\n", ""),
                               ("4 STIX Domain Objects/4 STIX Domain Objects.md", "- [[4.2 Campaign]]",
                                "- [[4.3 Course of Action]]")):
            with self.subTest(new=new):
                corpus = self.vault_with(path, old, new)
                self.assertFalse(v._v9(self.facts, v._read_vault(corpus), corpus, "markdown/STIX2.1-vault").passed)

    def test_wrong_index_rows_fail_v5(self) -> None:                  # link targets: V5 index targets
        index = f"{config.INDEX_NOTE}.md"
        for old, new in ((r"| `attack-pattern` | [[4.1 Attack Pattern\|", r"| `attack-pattern` | [[4.2 Campaign\|"),
                         (r"[[10.15 Malware Type Vocabulary\|10.15]]", r"[[10.16 Network Socket Address Family "
                                                                       r"Enumeration\|10.15]]"),
                         ("| Malware Type Vocabulary |", "| Malware Vocabulary |")):
            with self.subTest(new=new):
                vault = v._read_vault(self.vault_with(index, old, new))
                self.assertFalse(v._v5_vault(self.facts, vault).passed)

    def test_r4b_link_url_fails_v4(self) -> None:
        url = "(https://docs.oasis-open.org/cti/taxii/v2.1/taxii-v2.1.html)"
        self.assertFalse(v._v4(self.facts, self.gfm_md(self.gfm.replace(url, "(https://example.org/taxii)", 1))).passed)
        corpus = self.vault_with("1 Introduction/1.2 Overview.md", url, "(https://example.org/taxii)")
        self.assertFalse(v._v4_vault(self.facts, v._read_vault(corpus)).passed)

    # ---- mutations: table rows, Properties, emphasis and lists ----------------------------------------------
    def v9(self, corpus: dict[str, str]) -> v.Check:
        return v._v9(self.facts, v._read_vault(corpus), corpus, "markdown/STIX2.1-vault", self.sha)

    def test_clean_outputs_pass_x5_checks(self) -> None:
        vault = v._read_vault(self.vault2)
        for fmt, v8 in (("gfm", v._v8(self.facts, self.gfm_md())),
                        ("vault", v._v8(self.facts, vault.virtual, tuple(vault.index.lines)))):
            with self.subTest(fmt=fmt):
                self.assertEqual((v8.passed, v8.stats["cells_checked"]), (True, 3587), v8.failures)
        self.assertEqual(v._v16(self.facts, vault.virtual).stats,
                         {"bold_tokens": 4772, "italic_tokens": 1019, "list_items": 178, "failures": 0})
        for check in (self.v9(self.vault2), v._v5_vault(self.facts, vault), v._v12(vault, self.vault2, 42),
                      v._v8(self.facts, vault.virtual, tuple(vault.index.lines))):
            self.assertTrue(check.passed, (check.id, check.failures))

    def test_table_shape_fails_v8(self) -> None:
        def mutations(text: str) -> tuple[str, list[tuple[str, str]]]:
            row = next(ln for ln in text.split("\n") if ln.startswith("| **`name`** (required)"))
            cells = v._md_cells(row)
            shifted = "| " + " | ".join(["", *cells[:-2], cells[-2] + " " + cells[-1]]) + " |"   # same tokens
            return row, [("raw pipe", row.replace("A name used", "A | name used", 1)), ("shifted", shifted)]

        row, cases = mutations(self.gfm[self.gfm.index("### 4.1 Attack Pattern"):])
        for name, new in cases:
            with self.subTest(fmt="gfm", mutation=name):
                md = self.gfm_md(self.gfm.replace(row, new, 1))
                self.assertTrue(v._v2(self.facts, md).passed)           # the words are all still there
                self.assertFalse(v._v8(self.facts, md).passed)
        row, cases = mutations(self.vault2[ATTACK])
        for name, new in cases:
            with self.subTest(fmt="vault", mutation=name):
                vault = v._read_vault(self.vault_with(ATTACK, row, new))
                self.assertTrue(v._v2(self.facts, vault.virtual).passed)
                self.assertFalse(v._v8(self.facts, vault.virtual, tuple(vault.index.lines)).passed)
        index = f"{config.INDEX_NOTE}.md"
        corpus = self.vault_with(index, "| `attack-pattern` |", "| `attack-pattern` | extra |")
        vault = v._read_vault(corpus)
        self.assertFalse(v._v8(self.facts, vault.virtual, tuple(vault.index.lines)).passed)

    def test_front_matter_fails(self) -> None:
        note = "4 STIX Domain Objects/4.1 Attack Pattern.md"
        for old, new in (('"stix/sdo"', '"stix/sco"'), ('["Attack Pattern", "attack-pattern"]', '["Attack Pattern"]'),
                         ('chapter: "4"', 'chapter: "3"'), ("stix-v2.1-os.html", "stix-v2.1-cs02.html"),
                         (f'"{self.sha}"', f'"{"0" * 64}"'), ('stix_types: ["attack-pattern"]', 'stix_types: []')):
            with self.subTest(new=new):
                self.assertFalse(self.v9(self.vault_with(note, old, new)).passed)
        vault = v._read_vault(self.vault_with(f"{config.INDEX_NOTE}.md", "| #stix/sdo | 20 |", "| #stix/sdo | 21 |"))
        self.assertFalse(v._v5_vault(self.facts, vault).passed)
        base = config.OBJECTS_BASE + config.BASE_SUFFIX
        for old, new in (('file.hasTag("stix/sdo")', 'file.hasTag("stix/sro")'), ("      - specific_properties\n", ""),
                         ("name: SCOs", "name: Observables")):
            with self.subTest(new=new):
                corpus = self.vault_with(base, old, new)
                self.assertFalse(v._v12(v._read_vault(corpus), corpus, 42).passed)

    def test_emphasis_and_lists_fail_v16(self) -> None:
        item = next(ln for ln in self.vault2[LOCATION].split("\n") if ln.startswith("- "))   # the first after 4.1
        for path, old, new in ((MALWARE, "**MUST**", "*MUST*"), (MALWARE, "**MUST**", "MUST"),
                               (LOCATION, item, item[2:]), (CUSTOM, "*Custom properties*", "Custom properties")):
            with self.subTest(new=new):
                vault = v._read_vault(self.vault_with(path, old, new))
                self.assertTrue(v._v2(self.facts, vault.virtual).passed)
                self.assertFalse(v._v16(self.facts, vault.virtual).passed)

    def test_broken_examples_fail_v17(self) -> None:
        for old, new in (("`[section:4.1]`", "`[section:99.9]`"), ("`[kind:chapter]`", "`[kind:volume]`"),
                         ("`tag:#stix/sdo`", "`tag:#stix/nope`"), ('link("4.10 Location")', 'link("4.99 Nowhere")'),
                         ("`[[2.9 Identifier]]`", "`[[2.9 Identity]]`"), ("(`4.1`)", "(`4.99`)"),
                         ("#^ref-rfc8259]]`", "#^ref-rfc9999]]`"), ("`2 Common Data Types`", "`2 Common Types`"),
                         ("`cssclasses: stix-wide`", "`cssclasses: stix-wid`"),    # Property examples
                         ("`cssclasses: stix-object`", "`cssclasses: stix-objects`"),
                         ("`cssclasses: stix-atlas`", "`cssclasses: stix-atlases`"),
                         ("#^stix-rel-attack-pattern]]`", "#^stix-rel-campaign]]`")):     # the diagram embed example
            with self.subTest(new=new):
                self.assertFalse(self.v17_vault(self.vault2, old, new).passed)

    # ---- the child list is the exact tail of a chapter note -------------------------------------------------
    def before_footer(self, path: str, text: str) -> dict[str, str]:
        """The level-2 vault with `text` inserted right before one note's footer (the last copy of its NAV line)."""
        note = self.vault2[path]
        nav = next(ln for ln in note.split("\n") if ln.startswith(config.NAV_PREFIX))
        head, sep, tail = note.rpartition("\n\n" + nav + "\n")
        return self.vault_with(path, note, head + text + sep + tail)

    def failing(self, corpus: dict[str, str]) -> list[str]:
        """IDs of the vault checks a mutated corpus fails."""
        vault = v._read_vault(corpus)
        checks = [v._v2(self.facts, vault.virtual), v._v4_vault(self.facts, vault), *v._v6_v7_vault(vault),
                  v._v9(self.facts, vault, corpus, str(config.PROJECT_ROOT / config.DEFAULT_VAULT), self.sha),
                  v._v13(self.facts, vault), v._v16(self.facts, vault.virtual)]
        return [c.id for c in checks if not c.passed]

    def test_stray_child_lists_fail(self) -> None:                  # stray content around a chapter's child list
        junk = "Junk paragraph with a link to [[4.2 Campaign|campaign]]."
        label = "**Sections in this chapter**"
        for name, corpus in (("M1", self.before_footer(GROUPING, f"\n\n**Subsections**\n\n{junk}")),
                             ("M3", self.before_footer(SDOS, f"\n\n{junk}")),
                             ("M5", self.vault_with(GROUPING, "\n## 4.4.1 Properties",
                                                    f"\n{label}\n\n{junk}\n\n## 4.4.1 Properties")),
                             ("M5b", self.before_footer(GROUPING, f"\n\n{label}\n\n- [[4.2 Campaign]]"))):
            with self.subTest(name):
                self.assertTrue(self.failing(corpus))

    # ---- source_url with the anchor of the note's heading ---------------------------------------------------
    def test_anchored_source_urls(self) -> None:
        url = f'source_url: "{config.SOURCE_URL}'
        for path, anchor in ((MALWARE, "#_s5l7katgbp09"), (INTRO, "#_ha77jftyxqcx"), (APPENDIX_B, "#_6n2czpjuie3v"),
                             (INDEX, ""), (ATLAS, "")):
            with self.subTest(path):
                self.assertIn(f'\n{url}{anchor}"\n', self.vault2[path])

    def test_wrong_source_urls_fail_v9(self) -> None:
        url = f'source_url: "{config.SOURCE_URL}'
        malware = f'{url}#_s5l7katgbp09"'
        for path, old, new in ((MALWARE, malware, f'{url}#_eeb5g5wkvryu"'),          # the anchor of 1.1
                               (MALWARE, malware, f'{url}"'),                        # the bare URL
                               (MALWARE, malware, f'{url}#{self.facts.sections["4.11.1"]["anchors"][0]}"'),
                               (ATLAS, f'{url}"', malware),                          # an anchored atlas URL
                               (INDEX, f'{url}"', f'{url}#_ha77jftyxqcx"')):
            with self.subTest(path=path, new=new):
                corpus = self.vault_with(path, old, new)
                check = v._v9(self.facts, v._read_vault(corpus), corpus,
                              str(config.PROJECT_ROOT / config.DEFAULT_VAULT), self.sha)
                self.assertFalse(check.passed)
                self.assertTrue(any("source_url" in f for f in check.failures), check.failures)

    # ---- clickable tags in the Tag overview -----------------------------------------------------------------
    def test_tag_overview(self) -> None:
        self.assertEqual(v._tag_overview(v._read_vault(self.vault2).index), {
            "stix/appendix": 9, "stix/bundle": 3, "stix/concept": 11, "stix/conformance": 8, "stix/customization": 4,
            "stix/data-type": 17, "stix/deprecated": 5, "stix/intro": 5, "stix/patterning": 9, "stix/sco": 19,
            "stix/sdo": 20, "stix/smo": 4, "stix/sro": 3, "stix/vocabulary": 33})

    def test_tag_mutations_fail(self) -> None:
        row, base = "| #stix/sco | 19 |", config.OBJECTS_BASE + config.BASE_SUFFIX
        last = "| #stix/vocabulary | 33 |"
        for check, path, old, new in (
                ("V6", MALWARE, "**MUST**", "**MUST** #stix/sco"),                     # a tag in a note's prose
                ("V6", INDEX, "\n### Tag overview\n", "\nSee #stix/sco.\n\n### Tag overview\n"),   # index, outside
                ("V6", INDEX, last + "\n", f"{last}\n\n{row}\n"),                       # after the table's end
                ("V5", INDEX, row, "| #stix/sco | 18 |"),                               # a wrong count
                ("V5", INDEX, row, "| `stix/sco` | 19 |"),                              # a code-span row, not a tag
                ("V5", INDEX, row, f"{row}\n{row}"),                                    # a tag listed twice
                ("V12", base, 'file.hasTag("stix/sdo")', 'file.hasTag("stix/nope")')):  # not in the overview
            with self.subTest(check=check, new=new):
                vault = v._read_vault(corpus := self.vault_with(path, old, new))
                got = {"V5": lambda: v._v5_vault(self.facts, vault), "V6": lambda: v._v6_v7_vault(vault)[0],
                       "V12": lambda: v._v12(vault, corpus, 42)}[check]()
                self.assertFalse(got.passed, check)

    # ---- the atlas class --------------------------------------------------------------------------------------
    def test_atlas_class_mutations_fail_v9(self) -> None:          # exactly ["stix-atlas"], the last key
        css = 'cssclasses: ["stix-atlas"]\n'
        tags = 'tags: ["stix", "stix/atlas"]\n'
        for old, new in ((css, ""), (css, 'cssclasses: ["stix-wide"]\n'),
                         (tags + css, css + tags)):                                   # the key in another place
            with self.subTest(new=new):
                corpus = self.vault_with(ATLAS, old, new)
                check = v._v9(self.facts, v._read_vault(corpus), corpus,
                              str(config.PROJECT_ROOT / config.DEFAULT_VAULT), self.sha)
                self.assertFalse(check.passed)
                self.assertTrue(any(f.startswith(f"{ATLAS}: Properties") for f in check.failures), check.failures)


if __name__ == "__main__":
    unittest.main()
