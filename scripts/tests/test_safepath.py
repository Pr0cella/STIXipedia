"""Unit tests for stixmd.safepath, the vault directories included."""
import hashlib
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from stixmd import safepath
from stixmd.config import (EXIT_USAGE, EXIT_VALIDATION, INPUT_SUFFIXES, MANIFEST_NAME, OUTPUT_SUFFIXES,
                           PROJECT_ROOT, StixMdError)
from stixmd.safepath import (atomic_write_text, backup_file, check_name, check_vault_dirs, derived, read_tree,
                             reject_vault,
                             resolve_in_project, resolve_vault_dir, safe_rmtree, swap_vault, write_tree)


def md_path(value: str, subdir: str = "markdown") -> Path:
    return resolve_in_project(value, subdir=subdir, suffixes=OUTPUT_SUFFIXES)


class ResolveTests(unittest.TestCase):
    def test_accepted(self) -> None:
        self.assertEqual(md_path("markdown/STIX2.1.md"), PROJECT_ROOT / "markdown" / "STIX2.1.md")
        html = resolve_in_project("html/stix-v2.1-os.html", subdir="html", suffixes=INPUT_SUFFIXES)
        self.assertEqual(html, PROJECT_ROOT / "html" / "stix-v2.1-os.html")

    def test_rejected(self) -> None:
        bad = ["../evil.md", "markdown/../README.md", "markdown/sub/../../README.md", "C:\\Windows\\x.md",
               "markdown/CON.md", "markdown/x.txt", "html/stix.md", "", "a" + chr(0) + "b.md",
               "markdown/.hidden.md", "markdown/x:y.md", "markdown/x.md.", "markdown/x.md ",
               "markdown/CON .md", "markdown/a|b.md", "markdown/a?.md", "markdown/" + chr(9) + ".md"]
        for value in bad:
            with self.subTest(value=value), self.assertRaises(StixMdError) as ctx:
                md_path(value)
            self.assertEqual(ctx.exception.exit_code, EXIT_USAGE)

    def test_symlink_rejected(self) -> None:
        with tempfile.TemporaryDirectory(dir=PROJECT_ROOT / "markdown") as tmp:
            link = Path(tmp) / "link"
            try:
                os.symlink(tmp, link, target_is_directory=True)
            except OSError as exc:
                self.skipTest(f"cannot create symlinks here: {exc}")
            with self.assertRaises(StixMdError):
                md_path(str(link / "x.md"))

    def test_derived_paths(self) -> None:
        out = md_path("markdown/STIX2.1.md")
        self.assertEqual(derived(out, "STIX2.1.bak.md"), out.with_name("STIX2.1.bak.md"))
        for name in ("../x.md", "x.txt", "NUL.md"):
            with self.subTest(name=name), self.assertRaises(StixMdError):
                derived(out, name)


class WriteTests(unittest.TestCase):
    def test_atomic_round_trip_and_backup(self) -> None:
        with tempfile.TemporaryDirectory(dir=PROJECT_ROOT / "markdown") as tmp:
            target = Path(tmp) / "t.md"
            text = "line one\nzweite Zeile ü\n"
            atomic_write_text(target, text)
            raw = target.read_bytes()
            self.assertEqual(raw.decode("utf-8"), text)
            self.assertNotIn(b"\r", raw)
            self.assertFalse(raw.startswith(b"\xef\xbb\xbf"))
            self.assertEqual([p.name for p in Path(tmp).iterdir()], ["t.md"])
            bak = Path(tmp) / "t.bak.md"
            self.assertTrue(backup_file(target, bak))
            self.assertEqual(bak.read_bytes(), raw)
            self.assertFalse(backup_file(Path(tmp) / "missing.md", bak))


def manifest_for(corpus: dict[str, str]) -> str:
    files = [{"path": p, "sha256": hashlib.sha256(t.encode()).hexdigest(), "bytes": len(t.encode())}
             for p, t in corpus.items()]
    return json.dumps({"schema": "test", "files": files})


def generated(corpus: dict[str, str]) -> dict[str, str]:
    return {**corpus, MANIFEST_NAME: manifest_for(corpus)}


def make_link(test: unittest.TestCase, target: Path, link: Path) -> None:
    """A directory symlink, or a junction where symlinks need privileges; removed before the folder cleanup."""
    try:
        os.symlink(target, link, target_is_directory=True)
    except OSError as exc:
        try:
            import _winapi
            _winapi.CreateJunction(str(target), str(link))
        except (ImportError, OSError):
            test.skipTest(f"cannot create symlinks or junctions here: {exc}")
    test.addCleanup(os.unlink if link.is_symlink() else os.rmdir, link)


class VaultDirTests(unittest.TestCase):
    def test_resolve_vault_dir(self) -> None:
        dirs = resolve_vault_dir("markdown/STIX2.1-vault")
        md = PROJECT_ROOT / "markdown"
        self.assertEqual((dirs.out, dirs.bak, dirs.rejected, dirs.tmp),
                         (md / "STIX2.1-vault", md / "STIX2.1-vault.bak", md / "STIX2.1-vault.rejected",
                          md / ".STIX2.1-vault.tmp"))
        bad = ["markdown/sub/v", "markdown/v.md", "markdown/v.bak", "markdown/v.TMP", "../v", "markdown",
               "markdown/CON", "markdown/.v", "markdown/v.", "markdown/a:b", "html/v"]
        for value in bad:
            with self.subTest(value=value), self.assertRaises(StixMdError) as ctx:
                resolve_vault_dir(value)
            self.assertEqual(ctx.exception.exit_code, EXIT_USAGE)

    def test_check_name(self) -> None:
        for name in ("4.1 Attack Pattern.md", "Appendix B. Relationship Summary Table", "12.7.1 Level 1 - Basic"):
            self.assertEqual(check_name(name), name)
        self.assertEqual(check_name(MANIFEST_NAME, hidden_ok=True), MANIFEST_NAME)
        for name in ("", ".", "..", "a/b", "a\\b", "a:b", "a?", "CON.md", "nul", ".x", "x.", "x ", "a" + chr(1)):
            with self.subTest(name=name), self.assertRaises(StixMdError):
                check_name(name)


class VaultTreeTests(unittest.TestCase):
    """Real folders under markdown/: safe_rmtree only accepts direct children of it."""

    def setUp(self) -> None:
        self.dirs = resolve_vault_dir(f"markdown/test-vault-{os.getpid()}")
        self.addCleanup(self._cleanup)
        patcher = mock.patch.object(safepath, "RENAME_PAUSE_S", 0)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _cleanup(self) -> None:
        for path in (self.dirs.out, self.dirs.bak, self.dirs.rejected, self.dirs.tmp):
            shutil.rmtree(path, ignore_errors=True)

    def test_write_read_round_trip(self) -> None:
        corpus = {"Index.md": "a\nü\n", "1 Intro/1 Intro.md": "x\n", MANIFEST_NAME: "{}"}
        write_tree(self.dirs.tmp, corpus)
        self.assertEqual(read_tree(self.dirs.tmp), {k: corpus[k] for k in sorted(corpus)})
        self.assertNotIn(b"\r", (self.dirs.tmp / "Index.md").read_bytes())
        with self.assertRaises(FileExistsError):                  # the folder must be fresh
            write_tree(self.dirs.tmp, {"x.md": ""})

    def test_write_tree_rejects(self) -> None:
        cases = [{"../x.md": ""}, {"a/../../x.md": ""}, {"a:b.md": ""}, {".hidden.md": ""}, {"CON/x.md": ""},
                 {"A.md": "", "a.md": ""}, {"x" * 250 + ".md": ""}, {"": ""}]
        for corpus in cases:
            with self.subTest(corpus=list(corpus)), self.assertRaises(StixMdError):
                write_tree(self.dirs.tmp, corpus)
            self.assertFalse(self.dirs.tmp.exists())               # validated before anything is written

    def test_rmtree_refusals(self) -> None:
        self.dirs.out.mkdir()
        (self.dirs.out / "user.md").write_text("mine")
        with self.assertRaises(StixMdError) as ctx:                 # no manifest
            safe_rmtree(self.dirs.out, self.dirs)
        self.assertEqual(ctx.exception.exit_code, EXIT_USAGE)
        other = PROJECT_ROOT / "markdown" / f"test-other-{os.getpid()}"
        other.mkdir()
        self.addCleanup(shutil.rmtree, other, True)
        (other / MANIFEST_NAME).write_text("{}")
        with self.assertRaises(StixMdError):                        # wrong name
            safe_rmtree(other, self.dirs)
        with tempfile.TemporaryDirectory() as outside:
            fake = Path(outside) / self.dirs.out.name
            fake.mkdir()
            (fake / MANIFEST_NAME).write_text("{}")
            with self.assertRaises(StixMdError):                    # outside markdown/
                safe_rmtree(fake, self.dirs)
        self.assertTrue((self.dirs.out / "user.md").is_file())
        self.dirs.tmp.mkdir()                                       # tmp needs no manifest
        safe_rmtree(self.dirs.tmp, self.dirs)
        self.assertFalse(self.dirs.tmp.exists())
        safe_rmtree(self.dirs.tmp, self.dirs)                       # missing is a no-op

    def test_rmtree_refuses_links_inside(self) -> None:
        write_tree(self.dirs.out, generated({"a.md": "x"}))
        link = self.dirs.out / "sub" / "link"
        link.parent.mkdir()
        make_link(self, PROJECT_ROOT / "html", link)
        with self.assertRaises(StixMdError):
            safe_rmtree(self.dirs.out, self.dirs)
        self.assertTrue((PROJECT_ROOT / "html" / "stix-v2.1-os.html").is_file())

    def test_swap_first_and_second_run(self) -> None:
        write_tree(self.dirs.tmp, generated({"a.md": "one\n", "b/c.md": "two\n"}))
        self.assertEqual(swap_vault(self.dirs), ([], []))
        self.assertFalse(self.dirs.tmp.exists())
        out = self.dirs.out
        (out / ".obsidian").mkdir()
        (out / ".obsidian" / "app.json").write_text('{"x": 1}')
        (out / "notes.md").write_text("user note")
        (out / "a.md").write_text("edited")
        write_tree(self.dirs.tmp, generated({"a.md": "one v2\n"}))
        changes, seeded = swap_vault(self.dirs)
        self.assertEqual(changes, [{"path": "a.md", "change": "edited"}, {"path": "notes.md", "change": "added"}])
        self.assertEqual((out / "a.md").read_text(), "one v2\n")
        self.assertFalse((out / ".obsidian").exists())                # written afresh: nothing is carried over
        self.assertEqual(seeded, [{"path": ".obsidian/app.json", "status": "dropped"}])
        self.assertEqual((self.dirs.bak / ".obsidian" / "app.json").read_text(), '{"x": 1}')
        self.assertFalse((out / "notes.md").exists())
        self.assertEqual((self.dirs.bak / "notes.md").read_text(), "user note")
        self.assertTrue((self.dirs.bak / "b" / "c.md").is_file())

    SEED = {"graph.json": "{}\n", "snippets/stix.css": "css\n", "appearance.json": "{}\n", "bookmarks.json": "[]\n"}

    def settings(self, folder: Path) -> dict[str, bytes]:
        root = folder / ".obsidian"
        return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}

    def test_seed_fresh_vault_gets_every_file(self) -> None:
        write_tree(self.dirs.tmp, generated({"a.md": "one\n"}))
        _, seeded = swap_vault(self.dirs, seed=self.SEED)
        self.assertEqual(seeded, [{"path": f".obsidian/{p}", "status": "written"} for p in self.SEED])
        self.assertEqual(self.settings(self.dirs.out), {p: t.encode() for p, t in self.SEED.items()})
        self.assertEqual(read_tree(self.dirs.out), generated({"a.md": "one\n"}))    # seeds stay outside the Corpus
        write_tree(self.dirs.tmp, generated({"a.md": "one\n"}))                     # an identical second run
        _, seeded = swap_vault(self.dirs, seed=self.SEED)
        self.assertEqual(seeded, [{"path": f".obsidian/{p}", "status": "unchanged"} for p in self.SEED])

    def test_seed_always_overwrites_and_backs_up(self) -> None:
        write_tree(self.dirs.out, generated({"a.md": "one\n"}))
        settings = self.dirs.out / ".obsidian"
        (settings / "snippets").mkdir(parents=True)
        (settings / "appearance.json").write_text('{"theme": "obsidian"}')
        (settings / "graph.json").mkdir()                           # not a file: replaced too
        (settings / "graph.json" / "inner.json").write_text("i")
        (settings / "snippets" / "stix.css").write_bytes(b"css\n")      # write_text would give CRLF: replaced
        (settings / "snippets" / "mine.css").write_text("m")
        (settings / "workspace.json").write_text("w")
        (settings / "bookmarks.json").write_text("not json")
        old = self.settings(self.dirs.out)
        write_tree(self.dirs.tmp, generated({"a.md": "one v2\n"}))
        changes, seeded = swap_vault(self.dirs, seed=self.SEED)
        self.assertEqual(changes, [])
        self.assertEqual([(s["path"], s["status"]) for s in seeded],
                         [(".obsidian/graph.json", "replaced"), (".obsidian/snippets/stix.css", "unchanged"),
                          (".obsidian/appearance.json", "replaced"), (".obsidian/bookmarks.json", "replaced"),
                          (".obsidian/graph.json/inner.json", "dropped"), (".obsidian/snippets/mine.css", "dropped"),
                          (".obsidian/workspace.json", "dropped")])
        self.assertEqual(self.settings(self.dirs.out), {p: t.encode() for p, t in self.SEED.items()})
        self.assertEqual(self.settings(self.dirs.bak), old)          # the previous copies are in the backup

    def test_seed_link_in_old_settings_refuses_swap(self) -> None:
        write_tree(self.dirs.out, generated({"a.md": "one\n"}))
        (self.dirs.out / ".obsidian").mkdir()
        link = self.dirs.out / ".obsidian" / "snippets"
        make_link(self, PROJECT_ROOT / "html", link)
        write_tree(self.dirs.tmp, generated({"a.md": "one v2\n"}))
        with self.assertRaises(StixMdError):                        # the pre-flight refuses; nothing is followed
            swap_vault(self.dirs, seed=self.SEED)
        self.assertEqual((self.dirs.out / "a.md").read_text(), "one\n")
        self.assertTrue((PROJECT_ROOT / "html" / "stix-v2.1-os.html").is_file())

    def test_reject_leaves_old_settings(self) -> None:
        write_tree(self.dirs.out, generated({"a.md": "one\n"}))
        (self.dirs.out / ".obsidian").mkdir()
        (self.dirs.out / ".obsidian" / "workspace.json").write_text("w")
        write_tree(self.dirs.tmp, generated({"a.md": "bad\n"}))
        reject_vault(self.dirs)
        self.assertEqual(self.settings(self.dirs.out), {"workspace.json": b"w"})

    def test_seed_refuses_unsafe_names(self) -> None:
        write_tree(self.dirs.tmp, generated({"a.md": ""}))
        for rel in ("../x.json", "a:b.json", "CON.json", ".hidden"):
            with self.subTest(rel=rel), self.assertRaises(StixMdError):
                safepath.write_seed(self.dirs.tmp / ".obsidian", {rel: "x"})

    def test_preflight_refuses_foreign_folders(self) -> None:
        check_vault_dirs(self.dirs)                                 # nothing exists yet: fine
        self.dirs.bak.mkdir()
        (self.dirs.bak / "mine.md").write_text("x")
        with self.assertRaises(StixMdError) as ctx:
            check_vault_dirs(self.dirs)
        self.assertEqual(ctx.exception.exit_code, EXIT_USAGE)

    def test_swap_refuses_foreign_vault(self) -> None:
        self.dirs.out.mkdir()
        (self.dirs.out / "mine.md").write_text("x")
        write_tree(self.dirs.tmp, generated({"a.md": ""}))
        with self.assertRaises(StixMdError) as ctx:
            swap_vault(self.dirs)
        self.assertEqual(ctx.exception.exit_code, EXIT_USAGE)
        self.assertTrue((self.dirs.out / "mine.md").is_file())

    def test_swap_rolls_back_when_final_rename_fails(self) -> None:
        write_tree(self.dirs.out, generated({"old.md": "old"}))
        write_tree(self.dirs.tmp, generated({"new.md": "new"}))
        real = os.rename

        def flaky(src, dst):  # noqa: ANN001
            if Path(src) == self.dirs.tmp:
                raise PermissionError("locked")
            real(src, dst)
        with mock.patch.object(safepath.os, "rename", flaky), self.assertRaises(StixMdError) as ctx:
            swap_vault(self.dirs)
        self.assertEqual(ctx.exception.exit_code, EXIT_VALIDATION)
        self.assertTrue((self.dirs.out / "old.md").is_file())       # rolled back
        self.assertTrue((self.dirs.tmp / "new.md").is_file())       # new vault kept

    def test_reject(self) -> None:
        write_tree(self.dirs.tmp, generated({"a.md": ""}))
        reject_vault(self.dirs)
        self.assertTrue((self.dirs.rejected / "a.md").is_file())
        write_tree(self.dirs.tmp, generated({"b.md": ""}))
        reject_vault(self.dirs)                                     # replaces the old rejected folder
        self.assertEqual(sorted(p.name for p in self.dirs.rejected.iterdir()), [MANIFEST_NAME, "b.md"])


if __name__ == "__main__":
    unittest.main()
