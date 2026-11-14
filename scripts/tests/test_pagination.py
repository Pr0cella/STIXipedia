"""The vault footer on the real spec, with the mutations that V2, V4 and V9 must reject."""
import unittest

from stixmd import config
from stixmd import validate as v
from stixmd.assemble import manifest_text
from tests import realspec

MALWARE = "4 STIX Domain Objects/4.11 Malware.md"


def note_paths(corpus: dict[str, str]) -> list[str]:
    return [p for p in corpus if p.endswith(".md") and p not in (f"{config.INDEX_NOTE}.md", f"{config.ATLAS_NOTE}.md")]


class PaginationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.real = realspec.outputs()
        cls.facts = cls.real.facts

    # ---- footer and V9 --------------------------------------------------------------------------------------
    def vault_fails(self, corpus: dict[str, str]) -> set[str]:
        corpus = {p: t for p, t in corpus.items() if p != config.MANIFEST_NAME}
        corpus[config.MANIFEST_NAME] = manifest_text(corpus, split_level=config.SPLIT_LEVEL,
                                                     source_sha256=self.real.sha, generated_utc=realspec.GENERATED_UTC)
        vault = v._read_vault(corpus)
        root = str(config.PROJECT_ROOT / config.DEFAULT_VAULT)
        checks = [v._v2(self.facts, vault.virtual), v._v4_vault(self.facts, vault),
                  v._v9(self.facts, vault, corpus, root, self.real.sha)]
        return {c.id for c in checks if not c.passed}

    def test_footers(self) -> None:
        corpus = self.real.vault2
        self.assertEqual(len(note_paths(corpus)), 145)
        for path in note_paths(corpus):
            lines = corpus[path].split("\n")
            nav = lines[lines.index("---", 1) + 2]
            self.assertTrue(nav.startswith(config.NAV_PREFIX), path)
            self.assertEqual(lines[-4:], [lines[-4], "", nav, ""], path)
            self.assertNotEqual(lines[-4], "", path)
        self.assertEqual(self.vault_fails(corpus), set())

    def test_footer_mutations(self) -> None:
        text = self.real.vault2[MALWARE]
        nav = text.split("\n")[text.split("\n").index("---", 1) + 2]
        other = nav.replace("[[4.12 Malware Analysis|4.12]]", "[[4.13 Note|4.13]]")
        cases = {
            "deleted": (text.removesuffix(f"\n\n{nav}\n") + "\n", {"V9"}),
            "different": (text.removesuffix(f"{nav}\n") + f"{other}\n", {"V2", "V4", "V9"}),
            "duplicated": (text + f"\n{nav}\n", {"V2", "V4"}),          # the footer is the last copy; the other is body
            "two blank lines": (text.removesuffix(f"\n{nav}\n") + f"\n\n{nav}\n", {"V9"}),
            "copy in the body": (text.replace("\n## 4.11.1 ", f"\n{nav}\n\n## 4.11.1 ", 1), {"V2", "V4"}),
        }
        for name, (mutated, want) in cases.items():
            with self.subTest(name):
                self.assertNotEqual(mutated, text)
                got = self.vault_fails({**self.real.vault2, MALWARE: mutated})
                self.assertTrue(want <= got, f"{name}: {got}")


if __name__ == "__main__":
    unittest.main()
