"""The .obsidian/ seed files on the real spec: graph, CSS snippet, appearance, bookmarks."""
import json
import os
import shutil
import unittest
from unittest import mock

from stixmd import config, safepath
from stixmd.flavor import heading_target
from stixmd.layout import VaultLayout
from stixmd.model import Heading
from stixmd.parser import decode_html, parse_html
from stixmd.regions import apply_regions
from stixmd.safepath import resolve_vault_dir, swap_vault, write_tree
from stixmd.seed import APP_FILE, APPEARANCE_FILE, BOOKMARKS_FILE, GRAPH_FILE, SNIPPET_FILE, obsidian_seed
from tests.test_safepath import generated

STIX_CSS = """\
/* STIX 2.1 vault: wide notes, property tables, identifier links, atlas */

/* F4: wider notes */
.stix-wide { --file-line-width: 1600px; }

/* F6.1: identifier links keep the link colour (reading view) */
a.internal-link > code { color: var(--link-color); }
a.internal-link:hover > code { color: var(--link-color-hover); text-decoration: underline; }

/* F6.2: tables in wide notes */
.stix-wide tbody tr:nth-child(even) { background-color: var(--background-secondary); }
.stix-wide td:first-child { min-width: 12em; }

/* F6.3: long Properties blocks on object notes scroll */
.stix-object .metadata-container { max-height: 16em; overflow-y: auto; }

/* F11: the relationship atlas uses the full width; a diagram that is still too wide shrinks to fit */
.stix-atlas { --file-line-width: 100%; }
.stix-atlas .markdown-embed { border: none; padding: 0; }
.stix-atlas .markdown-embed-content { max-height: none; }
.stix-atlas .mermaid svg { max-width: 100%; height: auto; }
"""
OLD_CSS = """\
/* stix2md: wider line width for notes with cssclasses: stix-wide */
.stix-wide { --file-line-width: 1600px; }
"""                                                                 # stix2md 0.1 to 0.2.1


def real_headings() -> list[Heading]:
    doc = parse_html(decode_html((config.PROJECT_ROOT / config.DEFAULT_INPUT).read_bytes()))
    apply_regions(doc)
    return [b for b in doc.blocks if isinstance(b, Heading)]


class SeedTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.heads = real_headings()

    def layout(self) -> VaultLayout:
        return VaultLayout(self.heads, config.SPLIT_LEVEL)

    def test_files_and_json(self) -> None:
        seed = obsidian_seed(self.layout())
        self.assertEqual(list(seed), [GRAPH_FILE, SNIPPET_FILE, APPEARANCE_FILE, BOOKMARKS_FILE, APP_FILE])
        for rel, text in seed.items():
            self.assertTrue(text.endswith("\n") and "\r" not in text, rel)
            if rel.endswith(".json"):
                json.loads(text)
        self.assertEqual(seed, obsidian_seed(self.layout()))                       # deterministic

    def test_graph(self) -> None:
        layout = self.layout()
        graph = json.loads(obsidian_seed(layout)[GRAPH_FILE])
        self.assertEqual(list(graph), ["search", "colorGroups"])
        self.assertEqual(graph["search"], '-path:"2 Common Data Types" -path:"Appendix B. Relationship Summary Table" '
                                          '-path:"Appendix E. References" -file:"STIX 2.1 Index" '
                                          '-file:"STIX Relationship Atlas"')
        self.assertEqual(graph["colorGroups"][0], {"query": "tag:#stix/sdo", "color": {"a": 1, "rgb": 0xE8590C}})
        self.assertEqual([g["query"] for g in graph["colorGroups"]],
                         [f"tag:#stix/{t}" for t in ("sdo", "sro", "sco", "smo", "vocabulary", "patterning")])
        folders = {n.id.split("/", 1)[0] for n in layout.notes}         # the hidden folders exist
        for part in graph["search"].split(" -")[:3]:
            self.assertIn(part.split('"')[1], folders)

    def test_snippet_and_appearance(self) -> None:
        seed = obsidian_seed(self.layout())
        self.assertEqual(seed[SNIPPET_FILE], STIX_CSS)
        self.assertFalse([t for t in config.HOW_TO_TOOL_TERMS if t in seed[SNIPPET_FILE].casefold()])
        self.assertEqual(json.loads(seed[APPEARANCE_FILE]), {"enabledCssSnippets": ["stix"]})
        self.assertEqual(json.loads(seed[APP_FILE]), {"defaultViewMode": "preview"})     # reading view

    def test_bookmarks_resolve(self) -> None:
        layout = self.layout()
        marks = json.loads(obsidian_seed(layout)[BOOKMARKS_FILE])
        (group,) = marks["items"]
        self.assertEqual((group["type"], group["title"], group["ctime"]), ("group", "STIX 2.1", config.BOOKMARK_CTIME))
        items = group["items"]
        self.assertEqual({i["type"] for i in items}, {"file"})             # Obsidian has no "heading" type
        self.assertEqual(["subpath" in i for i in items], [False, False, True])     # 1.2.7 is a heading bookmark
        paths = {n.id + ".md": n for n in layout.notes}
        for section, item in zip(config.BOOKMARK_SECTIONS, items):
            self.assertEqual(item["ctime"], config.BOOKMARK_CTIME)
            self.assertIn(item["path"], paths)
            note = paths[item["path"]]
            if "subpath" not in item:
                self.assertEqual(note.heading.number, section)
            else:
                self.assertEqual(layout.note_of(section) + ".md", item["path"])
                self.assertEqual(item["subpath"], "#" + heading_target(layout.heading_of(section)))
        self.assertEqual(items, [
            {"type": "file", "ctime": config.BOOKMARK_CTIME, "path": "3 STIX General Concepts/3.2 Common Properties.md"},
            {"type": "file", "ctime": config.BOOKMARK_CTIME, "path": "2 Common Data Types/2.9 Identifier.md"},
            {"type": "file", "ctime": config.BOOKMARK_CTIME, "path": "1 Introduction/1.2 Overview.md",
             "subpath": "#1.2.7 STIX Patterning ANTLR Grammar"}])


class SettingsSwapTests(unittest.TestCase):
    """With the real seeds: every run writes .obsidian/ afresh, the old one is in the backup."""

    @classmethod
    def setUpClass(cls) -> None:
        heads = real_headings()
        cls.seed = obsidian_seed(VaultLayout(heads, config.SPLIT_LEVEL))

    def setUp(self) -> None:
        self.dirs = resolve_vault_dir(f"markdown/test-seed-{os.getpid()}")
        self.addCleanup(lambda: [shutil.rmtree(p, ignore_errors=True)
                                 for p in (self.dirs.out, self.dirs.bak, self.dirs.rejected, self.dirs.tmp)])
        patcher = mock.patch.object(safepath, "RENAME_PAUSE_S", 0)
        patcher.start()
        self.addCleanup(patcher.stop)

    def rerun(self) -> list[tuple[str, str]]:
        write_tree(self.dirs.tmp, generated({"a.md": "x\n"}))
        _, seeded = swap_vault(self.dirs, seed=self.seed)
        return [(s["path"].removeprefix(".obsidian/"), s["status"]) for s in seeded]

    def settings(self, folder) -> dict[str, str]:  # noqa: ANN001
        root = folder / config.OBSIDIAN_SETTINGS_DIR
        return {p.relative_to(root).as_posix(): p.read_text("utf-8") for p in root.rglob("*") if p.is_file()}

    def test_reruns(self) -> None:
        self.assertEqual(self.rerun(), [(p, "written") for p in self.seed])
        self.assertEqual(self.rerun(), [(p, "unchanged") for p in self.seed])
        (self.dirs.out / config.OBSIDIAN_SETTINGS_DIR / "workspace.json").write_text("{}", "utf-8")
        self.assertEqual(self.rerun(), [*((p, "unchanged") for p in self.seed), ("workspace.json", "dropped")])
        self.assertEqual(self.settings(self.dirs.out), self.seed)
        self.assertEqual(self.settings(self.dirs.bak)["workspace.json"], "{}")
        self.assertEqual(self.rerun(), [(p, "unchanged") for p in self.seed])

    def test_old_and_edited_files_are_replaced(self) -> None:
        self.rerun()
        settings = self.dirs.out / config.OBSIDIAN_SETTINGS_DIR
        old_heading = {"items": [{"type": "group", "title": "STIX 2.1", "items": [         # an older bookmark type
            {"type": "heading", "path": "1 Introduction/1.2 Overview.md", "heading": "1.2.7 STIX Patterning"}]}]}
        (settings / BOOKMARKS_FILE).write_text(json.dumps(old_heading), "utf-8")
        (settings / GRAPH_FILE).write_text('{"search": "mine"}', "utf-8")
        (settings / APPEARANCE_FILE).write_text('{"theme": "obsidian"}', "utf-8")
        (settings / APP_FILE).write_text('{"defaultViewMode": "source", "vimMode": true}', "utf-8")   # Obsidian's own
        self.assertEqual(self.rerun(), [(GRAPH_FILE, "replaced"), (SNIPPET_FILE, "unchanged"),
                                          (APPEARANCE_FILE, "replaced"), (BOOKMARKS_FILE, "replaced"),
                                          (APP_FILE, "replaced")])
        self.assertEqual(self.settings(self.dirs.out), self.seed)
        self.assertEqual(json.loads(self.settings(self.dirs.bak)[BOOKMARKS_FILE]), old_heading)

    def test_old_snippet_is_replaced(self) -> None:
        for old in (OLD_CSS, OLD_CSS + ".mine { color: red; }\n"):       # unchanged and edited old snippet
            with self.subTest(edited=old != OLD_CSS):
                self.rerun()
                (self.dirs.out / config.OBSIDIAN_SETTINGS_DIR / SNIPPET_FILE).write_bytes(old.encode())
                self.assertEqual(self.rerun(), [(GRAPH_FILE, "unchanged"), (SNIPPET_FILE, "replaced"),
                                                  (APPEARANCE_FILE, "unchanged"), (BOOKMARKS_FILE, "unchanged"),
                                                  (APP_FILE, "unchanged")])
                self.assertEqual(self.settings(self.dirs.out)[SNIPPET_FILE], STIX_CSS)
                self.assertEqual(self.settings(self.dirs.bak)[SNIPPET_FILE], old)


if __name__ == "__main__":
    unittest.main()
