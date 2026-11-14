"""Relationship diagrams and the atlas note on the real spec, with
the mutations that V3, V4, V6, V9, V13 and V16 must reject."""
import json
import unittest

from stixmd import config
from stixmd import validate as v
from stixmd.assemble import manifest_text
from stixmd.diagrams import diagram_lines, diagram_types
from stixmd.facts import build_facts
from stixmd.layout import VaultLayout
from stixmd.model import Heading
from tests import realspec

MALWARE = "4 STIX Domain Objects/4.11 Malware.md"
ANALYSIS = "4 STIX Domain Objects/4.12 Malware Analysis.md"
NOTE = "4 STIX Domain Objects/4.13 Note.md"
FILE = "6 STIX Cyber-observable Objects/6.7 File Object.md"
INFRASTRUCTURE = "4 STIX Domain Objects/4.8 Infrastructure.md"
ATLAS = f"{config.ATLAS_NOTE}.md"
OF12 = ["attack-pattern", "campaign", "course-of-action", "identity", "indicator", "infrastructure", "intrusion-set",
        "location", "malware", "malware-analysis", "observed-data", "threat-actor", "tool", "vulnerability",
        "autonomous-system", "domain-name", "file", "ipv4-addr", "ipv6-addr", "mac-addr", "url"]
EXAMPLE = [                                                                  # expected output, verbatim
    "**Relationship diagram**", "", "```mermaid", "flowchart LR", '  n0["4.12 Malware Analysis"]',
    '  n1["4.11 Malware"]',
    '  n0 -->|"characterizes, analysis-of, static-analysis-of, dynamic-analysis-of"| n1',
    "  classDef self stroke-width:3px", "  class n0 self", "  class n1 internal-link", "```",
    "^stix-rel-malware-analysis"]


def block(text: str) -> str:
    """The diagram block of a note, label to block id, as one string."""
    start = text.index(config.DIAGRAM_LABEL)
    end = text.index("\n", text.index("\n^" + config.DIAGRAM_BLOCK_PREFIX, start) + 1)
    return text[start:end]


class DiagramTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.real = realspec.outputs()
        cls.spec = build_facts(cls.real.doc)
        cls.layout = VaultLayout((b for b in cls.real.doc.blocks if isinstance(b, Heading)), config.SPLIT_LEVEL)

    def test_diagram_lines(self) -> None:                         # OF12: 21 objects with edges; malware: 18 nodes
        self.assertEqual(diagram_types(self.spec), OF12)
        self.assertEqual(diagram_lines(self.spec, self.layout, "malware-analysis"), EXAMPLE)
        lines = diagram_lines(self.spec, self.layout, "malware")
        self.assertEqual(sum(ln.startswith('  n') and '["' in ln for ln in lines), 18)
        self.assertEqual(sum(" -->|" in ln for ln in lines), 23)
        self.assertIn('  n0 -->|"controls, downloads, drops, uses, variant-of"| n0', lines)      # the self-loop
        self.assertEqual(diagram_lines(self.spec, self.layout, "note"), [])
        edges = sum(sum(" -->|" in ln for ln in diagram_lines(self.spec, self.layout, t)) for t in OF12)
        self.assertEqual(edges, 158)

    def test_placement(self) -> None:
        vault = self.real.vault2
        for path, text in vault.items():
            if not path.endswith(".md") or path in (ATLAS, f"{config.INDEX_NOTE}.md"):
                continue
            lines = text.split("\n")
            nav = lines[lines.index("---", 1) + 2]
            if config.DIAGRAM_LABEL in lines:
                at = lines.index(config.DIAGRAM_LABEL)
                self.assertEqual(lines[at - 1], "", path)
                self.assertNotEqual(lines[at - 2], "", path)
                self.assertTrue(lines[-4].startswith("^" + config.DIAGRAM_BLOCK_PREFIX), path)
                self.assertEqual(lines[-3:], ["", nav, ""], path)
        self.assertEqual(sum(config.DIAGRAM_LABEL in t for t in vault.values()), 21)
        self.assertTrue(vault[MALWARE].endswith("^stix-rel-malware\n\n" + vault[MALWARE].split("\n")[-2] + "\n"))
        file_lines = vault[FILE].split("\n")                     # incoming edges only, after 6.7.6.3.1
        last_heading = max(i for i, ln in enumerate(file_lines) if ln.startswith("#"))
        self.assertEqual(file_lines[last_heading], "#### 6.7.6.3.1 Properties")
        self.assertGreater(file_lines.index(config.DIAGRAM_LABEL), last_heading)
        self.assertNotIn("  n0 -->", block(vault[FILE]))                                # incoming edges only
        self.assertIn('["6 STIX Cyber-observable Objects"]', block(vault[INFRASTRUCTURE]))

    def test_atlas(self) -> None:                                 # only headings with entries
        text = self.real.vault2[ATLAS]
        front, _, errors = v._parse_front(text.split("\n"))
        self.assertEqual(errors, [])
        self.assertEqual(front, {"title": config.ATLAS_NOTE, "kind": "atlas", "source_url": config.SOURCE_URL,
                                 "source_sha256": self.real.sha, "aliases": ["Relationship Atlas"],
                                 "tags": ["stix", "stix/atlas"], "cssclasses": ["stix-atlas"]})
        self.assertEqual(list(front)[-1], "cssclasses")                                       # the last key
        lines = text.split("\n")
        self.assertEqual([ln for ln in lines if ln.startswith("#")], ["## Domain objects", "## Cyber-observable objects"])
        self.assertEqual(sum(ln.startswith("![[") for ln in lines), 21)
        self.assertIn("**[[4.11 Malware|malware]]**\n\n![[4.11 Malware#^stix-rel-malware]]\n", text)
        self.assertFalse(any(ln.startswith(config.NAV_PREFIX) for ln in lines))
        self.assertFalse([t for t in config.HOW_TO_TOOL_TERMS if t in text.casefold()])     # standalone
        index = self.real.vault2[f"{config.INDEX_NOTE}.md"]
        self.assertIn(f"[[{config.ATLAS_NOTE}]] collects all diagrams.", index)
        self.assertIn("hide the hub notes: this index, the relationship atlas and the notes in", index)
        self.assertNotIn("stix/atlas", index)                    # not in the Tag overview
        self.assertEqual(json.loads(self.real.vault2[config.MANIFEST_NAME])["files"][1]["path"], ATLAS)

    # ---- validator ---------------------------------------------------------------------------------------
    def failed(self, corpus: dict[str, str]) -> dict[str, v.Check]:
        corpus = {p: t for p, t in corpus.items() if p != config.MANIFEST_NAME}
        corpus[config.MANIFEST_NAME] = manifest_text(corpus, split_level=config.SPLIT_LEVEL,
                                                     source_sha256=self.real.sha, generated_utc=realspec.GENERATED_UTC)
        facts, vault = self.real.facts, v._read_vault(corpus)     # the checks the mutations aim at
        checks = [v._v2(facts, vault.virtual), v._v3(facts, vault.virtual), v._v4_vault(facts, vault),
                  *v._v6_v7_vault(vault), v._v9(facts, vault, corpus, str(config.PROJECT_ROOT / config.DEFAULT_VAULT),
                                                self.real.sha), v._v13(facts, vault), v._v16(facts, vault.virtual)]
        return {c.id: c for c in checks if not c.passed}

    def test_clean_vault(self) -> None:
        report = v.validate_vault(self.real.html, self.real.vault2, str(config.PROJECT_ROOT / config.DEFAULT_VAULT))
        stats = {c.id: c.stats for c in report.checks}
        self.assertTrue(report.passed, [c.failures[:2] for c in report.checks if not c.passed])
        self.assertEqual(stats["V13"], {"diagrams": 21, "edges": 158, "failures": 0})
        self.assertEqual((stats["V4"]["block_ids"], stats["V4"]["targets"]), (79, 214))
        self.assertEqual((stats["V2"]["extra_md_tokens"], stats["V3"]["md"], stats["V16"]["bold_tokens"]),
                         (103, 218, 4772))
        self.assertEqual((stats["V9"]["files"], stats["V5"]["notes"]), (151, 145))

    def test_mutations(self) -> None:                            # targeted check first: each must fail
        vault = self.real.vault2
        mal, ana, atlas = vault[MALWARE], vault[ANALYSIS], vault[ATLAS]
        mal_block = block(mal)
        body_at = "\n## 4.11.1 "
        cases = {
            "diagram deleted": (MALWARE, mal.replace(mal_block + "\n\n", ""), {"V4", "V13"}),
            "edge label": (ANALYSIS, ana.replace('|"characterizes, ', '|"', 1), {"V13"}),
            "node label": (ANALYSIS, ana.replace('n1["4.11 Malware"]', 'n1["4.13 Note"]'), {"V13"}),
            "edge deleted": (MALWARE, mal.replace('  n0 -->|"targets"| n4\n', ""), {"V13"}),
            "class line": (ANALYSIS, ana.replace("  class n1 internal-link\n", ""), {"V13"}),
            "moved into the body": (MALWARE, mal.replace(mal_block + "\n\n", "").replace(
                body_at, f"\n{mal_block}\n{body_at}", 1), {"V13"}),
            "duplicated": (MALWARE, mal.replace(mal_block, f"{mal_block}\n\n{mal_block}"), {"V13"}),
            "fence without label": (MALWARE, mal.replace(f"{config.DIAGRAM_LABEL}\n\n", ""), {"V2", "V3", "V6", "V13"}),
            "bare block id in prose": (MALWARE, mal.replace(body_at, "\n^stix-rel-x\n" + body_at, 1), {"V2", "V6"}),
            "label without diagram": (MALWARE, mal.replace(body_at, f"\n{config.DIAGRAM_LABEL}\n{body_at}", 1),
                                      {"V2", "V16"}),
            "wrong block id": (MALWARE, mal.replace("\n^stix-rel-malware\n", "\n^stix-rel-tool\n"), {"V4", "V13"}),
            "in a note without edges": (NOTE, vault[NOTE].replace(
                "\n\n" + vault[NOTE].split("\n")[-2] + "\n",
                f"\n\n{mal_block}\n\n" + vault[NOTE].split("\n")[-2] + "\n"), {"V13"}),
            "atlas entry deleted": (ATLAS, atlas.replace(
                "**[[4.11 Malware|malware]]**\n\n![[4.11 Malware#^stix-rel-malware]]\n\n", ""), {"V13"}),
            "atlas embed id": (ATLAS, atlas.replace("#^stix-rel-malware]]", "#^stix-rel-malwar]]"), {"V4", "V13"}),
            "atlas Property": (ATLAS, atlas.replace('kind: "atlas"', 'kind: "index"'), {"V9"}),
            "atlas NAV line": (ATLAS, atlas + vault[MALWARE].split("\n")[-2] + "\n", {"V9", "V13"}),
        }
        for name, (path, mutated, want) in cases.items():
            with self.subTest(name):
                self.assertNotEqual(mutated, vault[path])
                got = self.failed({**vault, path: mutated})
                self.assertTrue(want <= set(got), f"{name}: {sorted(got)}")
        with self.subTest("atlas deleted"):
            got = self.failed({p: t for p, t in vault.items() if p != ATLAS})
            self.assertEqual(set(got), {"V4", "V13"})            # V4: the index bullet links the atlas
            self.assertIn(f"no {ATLAS}", got["V13"].failures)


if __name__ == "__main__":
    unittest.main()
