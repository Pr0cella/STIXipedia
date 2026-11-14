"""Object and vocabulary Properties on the real spec, the derived types, the Bases files, V11 and V12."""
import json
import logging
import unittest

from stixmd import config
from stixmd import validate as v
from stixmd.anchors import AnchorMap
from stixmd.assemble import assemble_vault
from stixmd.facts import build_facts
from stixmd.parser import decode_html, parse_html
from stixmd.regions import apply_regions

MALWARE = "4 STIX Domain Objects/4.11 Malware.md"
INFRA = "4 STIX Domain Objects/4.8 Infrastructure.md"
FILE = "6 STIX Cyber-observable Objects/6.7 File Object.md"
MARKINGS = "7 STIX Meta Objects/7.2 Data Markings.md"
LANGUAGE = "7 STIX Meta Objects/7.1 Language Content.md"
MALWARE_TYPES = "10 STIX Vocabularies/10.15 Malware Type Vocabulary.md"
BASES = [n + config.BASE_SUFFIX for n in (config.OBJECTS_BASE, config.RELATIONSHIPS_BASE, config.VOCABULARIES_BASE)]


def front(text: str) -> dict:
    lines = text.split("\n")
    return {k: json.loads(val) for k, _, val in (ln.partition(": ") for ln in lines[1:lines.index("---", 1)])}


class PropertiesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        raw = (config.PROJECT_ROOT / config.DEFAULT_INPUT).read_bytes()
        cls.html = decode_html(raw)
        doc = parse_html(cls.html)
        removed = apply_regions(doc)
        logging.disable(logging.WARNING)                             # the one R5 warning of the real spec
        try:
            amap = AnchorMap.build(doc.blocks, removed, doc.link_pos)
        finally:
            logging.disable(logging.NOTSET)
        facts = build_facts(doc)
        build = {"source_sha256": "0" * 64, "generated_utc": "2026-10-18T00:00:00Z", "facts": facts}
        amap.reset_stats()
        cls.vault = assemble_vault(doc, amap, config.SPLIT_LEVEL, **build)
        cls.facts = v._HtmlFacts()
        cls.facts.feed(cls.html)
        cls.facts.close()

    def checks(self, corpus: dict[str, str]) -> dict[str, v.Check]:
        vault = v._read_vault(corpus)
        v11 = v._v11(self.facts, vault)
        return {"V11": v11, "V12": v._v12(vault, corpus, v11.stats["objects"])}

    def test_object_properties(self) -> None:                       # spot checks
        malware = front(self.vault[MALWARE])
        self.assertEqual(malware["not_applicable_properties"], ["defanged"])
        self.assertIn("architecture_execution_envs", malware["specific_properties"])
        self.assertNotIn("id_contributing_properties", malware)
        self.assertIn("[[4.11 Malware]]", malware["relationship_targets"])             # controls, a self-loop
        self.assertEqual(malware["vocabularies"][0], "[[10.15 Malware Type Vocabulary]]")
        keys = list(malware)
        self.assertEqual(keys[keys.index("stix_types") + 1], "required_properties")
        self.assertEqual(keys[keys.index("vocabularies") + 1], "up")
        self.assertEqual(front(self.vault[FILE])["id_contributing_properties"],
                         ["hashes", "name", "extensions", "parent_directory_ref"])
        self.assertIn("[[6 STIX Cyber-observable Objects]]", front(self.vault[INFRA])["relationship_targets"])
        self.assertEqual(front(self.vault[MARKINGS])["specific_properties"], ["name", "definition_type", "definition"])

    def test_vocabulary_properties(self) -> None:
        vocab = front(self.vault[MALWARE_TYPES])
        self.assertEqual((vocab["vocabulary_name"], vocab["vocabulary_kind"], vocab["used_by"]),
                         ("malware-type-ov", "vocabulary", ["[[4.11 Malware]]"]))
        self.assertEqual(vocab["vocabulary_values"][:2], ["adware", "backdoor"])
        self.assertEqual(sum("\nvocabulary_name: " in t for t in self.vault.values()), 32)

    def test_derived_types(self) -> None:
        language = front(self.vault[LANGUAGE])
        self.assertEqual(language["stix_types"], ["language-content"])
        self.assertIn("language-content", language["aliases"])
        index = self.vault[f"{config.INDEX_NOTE}.md"].split("\n")
        start = index.index("### Type index")
        rows = [ln for ln in index[start + 4:index.index("### Vocabulary index")] if ln.startswith("| `")]
        self.assertEqual(len(rows), 77)

    def test_bases(self) -> None:
        self.assertEqual(len(self.vault), 151)                     # with the atlas note
        self.assertEqual([p for p in self.vault if p.endswith(config.BASE_SUFFIX)], BASES)
        base = v._read_base(self.vault[BASES[0]])
        self.assertEqual(base["filters"], {"and": ['file.hasProperty("specific_properties")']})
        self.assertEqual([view["name"] for view in base["views"]], ["All objects", "SDOs", "SCOs", "SROs and SMOs"])
        self.assertEqual(base["views"][3]["filters"], {"or": ['file.hasTag("stix/sro")', 'file.hasTag("stix/smo")']})
        self.assertIn("id_contributing_properties", base["views"][2]["order"])        # bare ids
        index = self.vault[f"{config.INDEX_NOTE}.md"]
        self.assertIn("[[STIX Objects.base|Object catalog]]", index)
        self.assertIn('`relationship_targets.contains(link("4.10 Location"))`', index)

    def test_validator_passes_and_fails(self) -> None:
        clean = self.checks(self.vault)
        self.assertEqual(clean["V11"].stats, {"objects": 42, "forward_tables": 14, "triples": 120, "rel_types": 32,
                                              "vocabularies": 32, "failures": 0})
        self.assertEqual(clean["V12"].stats, {"bases": 3, "failures": 0})
        wrong = dict(self.vault, **{MALWARE: self.vault[MALWARE].replace('["defanged"]', '["lang"]', 1)})
        self.assertIn("not_applicable_properties", self.checks(wrong)["V11"].failures[0])
        for old, new in (("- stix_types", "- stix_typez"), ("- stix_types", "- note.stix_typez")):
            unknown = dict(self.vault, **{BASES[0]: self.vault[BASES[0]].replace(old, new)})
            self.assertIn(f"'{new[2:]}' is no note Property", "\n".join(self.checks(unknown)["V12"].failures))
        legacy = dict(self.vault, **{BASES[0]: self.vault[BASES[0]].replace("- stix_types", "- note.stix_types")})
        legacy_fails = self.checks(legacy)["V12"].failures          # note.<key> is a valid column, but not the
        self.assertEqual(legacy_fails, ["STIX Objects.base: differs from the OBSIDIAN-1.1 §5.2 structure"])
        other = dict(self.vault, **{BASES[1]: self.vault[BASES[1]].replace("file.hasProperty", "file.inFolder")})
        self.assertIn("uses more than hasProperty and hasTag", "\n".join(self.checks(other)["V12"].failures))

    def v9(self, corpus: dict[str, str]) -> v.Check:
        return v._v9(self.facts, v._read_vault(corpus), corpus, str(config.PROJECT_ROOT / config.DEFAULT_VAULT))

    def test_wide_notes(self) -> None:
        classes = {p: front(t).get("cssclasses") or [] for p, t in self.vault.items() if p.endswith(".md")}
        wide = [p for p, c in classes.items() if "stix-wide" in c]
        objects = [p for p, c in classes.items() if "stix-object" in c]
        self.assertEqual((len(wide), len(objects)), (54, 42))
        self.assertEqual(objects, [p for p, t in self.vault.items() if p.endswith(".md") and "\nrequired_properties: " in t])
        self.assertEqual({tuple(c) for c in classes.values()} - {()},                # the atlas
                         {("stix-wide",), ("stix-wide", "stix-object"), ("stix-atlas",)})
        self.assertEqual([p for p, c in classes.items() if "stix-atlas" in c], [f"{config.ATLAS_NOTE}.md"])
        keys = list(front(self.vault[MALWARE]))
        self.assertEqual(keys[keys.index("aliases") + 1], "cssclasses")
        self.assertNotIn("cssclasses", front(self.vault[f"{config.INDEX_NOTE}.md"]))
        self.assertEqual(self.v9(self.vault).stats["wide"], 54)
        self.assertTrue(self.v9(self.vault).passed)
        both = 'cssclasses: ["stix-wide", "stix-object"]\n'
        dropped = dict(self.vault, **{MALWARE: self.vault[MALWARE].replace(both, "", 1)})
        self.assertIn("cssclasses is None, expected ['stix-wide', 'stix-object']", self.v9(dropped).failures[0])
        no_object = dict(self.vault, **{MALWARE: self.vault[MALWARE].replace(both, 'cssclasses: ["stix-wide"]\n', 1)})
        self.assertIn("cssclasses is ['stix-wide'], expected ['stix-wide', 'stix-object']", self.v9(no_object).failures[0])
        vocab = next(p for p in wide if p not in objects)
        extra = dict(self.vault, **{vocab: self.vault[vocab].replace('cssclasses: ["stix-wide"]', 'cssclasses: '
                                                                     '["stix-wide", "stix-object"]', 1)})
        self.assertIn("expected ['stix-wide']", self.v9(extra).failures[0])
        narrow = next(p for p, t in self.vault.items() if p.endswith(".md") and p not in wide + objects
                      and p not in (f"{config.INDEX_NOTE}.md", f"{config.ATLAS_NOTE}.md"))
        added = dict(self.vault, **{narrow: self.vault[narrow].replace("\nup: ", '\ncssclasses: ["stix-wide"]\nup: ', 1)})
        self.assertIn("expected None", self.v9(added).failures[0])


if __name__ == "__main__":
    unittest.main()
