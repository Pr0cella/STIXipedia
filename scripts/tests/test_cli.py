"""Unit tests for the stix2md command line: format selection, usage errors and exit codes."""
import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import stix2md
from stixmd.config import EXIT_USAGE, MANIFEST_NAME, OBSIDIAN_SETTINGS_DIR, PROJECT_ROOT, StixMdError
from stixmd.meta import sha256_file
from stixmd.safepath import VaultDirs, resolve_vault_dir


def run(*argv: str) -> int:
    """Run main() quietly, without touching the real META.md."""
    with mock.patch.object(stix2md, "write_meta"), contextlib.redirect_stdout(io.StringIO()), \
            contextlib.redirect_stderr(io.StringIO()):
        try:
            return stix2md.main(list(argv))
        except SystemExit as exc:                  # argparse usage errors
            return int(exc.code)


class TargetTests(unittest.TestCase):
    def test_all_formats_in_order(self) -> None:
        targets = stix2md.resolve_targets("all", "html/stix-v2.1-os.html", None, preflight=False)
        self.assertEqual(list(targets), ["gfm", "vault"])            # gfm, then the vault
        self.assertEqual(targets["gfm"][1].name, "STIX2.1.md")
        self.assertIsInstance(targets["vault"], VaultDirs)

    def test_output_needs_one_format(self) -> None:
        with self.assertRaises(StixMdError) as ctx:
            stix2md.resolve_targets("all", "html/stix-v2.1-os.html", "markdown/x.md", preflight=False)
        self.assertEqual(ctx.exception.exit_code, EXIT_USAGE)

    def test_overall_status(self) -> None:
        self.assertEqual(stix2md.overall_status(["success", "success"]), "success")
        self.assertEqual(stix2md.overall_status(["success", "forced"]), "forced")
        self.assertEqual(stix2md.overall_status(["forced", "failed", "success"]), "failed")


class UsageErrorTests(unittest.TestCase):
    def test_validate_only_refuses_fetch_without_writing(self) -> None:
        with mock.patch.object(stix2md, "fetch_spec") as fetch, \
                mock.patch.object(stix2md, "convert") as convert, \
                mock.patch.object(stix2md, "run_validate_only") as validate_only, \
                mock.patch.object(stix2md, "write_meta") as write_meta, \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as ctx:
                stix2md.main(["--fetch", "--validate-only"])
            self.assertEqual(ctx.exception.code, EXIT_USAGE)
            fetch.assert_not_called()
            convert.assert_not_called()
            validate_only.assert_not_called()
            write_meta.assert_not_called()

    def test_usage_errors_exit_2_without_converting(self) -> None:
        cases = [("--output", "../evil.md"), ("--input", "../x.html"), ("--format", "all", "--output", "markdown/x.md"),
                 ("--format", "vault", "--output", "markdown/x.md"), ("--format", "vault", "--output", "markdown/a/b"),
                 ("--format", "nope"), ("--format", "gfm", "--output", "markdown/CON.md")]
        with mock.patch.object(stix2md, "convert") as convert:
            for argv in cases:
                with self.subTest(argv=argv):
                    self.assertEqual(run(*argv), EXIT_USAGE)
            convert.assert_not_called()

    def test_removed_options_exit_2(self) -> None:
        """--format obsidian and --split-level are usage errors (exit 2); nothing is converted."""
        cases = [("--format", "obsidian"), ("--split-level", "2"), ("--format", "vault", "--split-level", "2"),
                 ("--format", "all", "--split-level", "2")]
        with mock.patch.object(stix2md, "convert") as convert, \
                mock.patch.object(stix2md, "run_validate_only") as validate_only:
            for argv in cases:
                with self.subTest(argv=argv):
                    self.assertEqual(run(*argv), EXIT_USAGE)
            convert.assert_not_called()
            validate_only.assert_not_called()

    def test_validate_md_rejects_obsidian(self) -> None:
        import validate_md
        with mock.patch.object(validate_md, "run_validate_only") as validate_only, \
                contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()), \
                self.assertRaises(SystemExit) as ctx:
            validate_md.main(["--format", "obsidian"])
        self.assertEqual(ctx.exception.code, EXIT_USAGE)
        validate_only.assert_not_called()


class ReportTests(unittest.TestCase):
    """Run-report lines for .obsidian/ and the META backup entries."""

    def test_seed_lines(self) -> None:
        dirs = resolve_vault_dir("markdown/STIX2.1-vault")
        seeded = [{"path": f".obsidian/{p}", "status": s} for p, s in (
            ("graph.json", "replaced"), ("snippets/stix.css", "unchanged"), ("appearance.json", "replaced"),
            ("bookmarks.json", "unchanged"), ("hotkeys.json", "dropped"), ("workspace.json", "dropped"))]
        self.assertEqual(stix2md.seed_lines(dirs, seeded), [
            "seeded replaced: .obsidian/graph.json, .obsidian/appearance.json "
            "(previous copies in markdown/STIX2.1-vault.bak/.obsidian)",
            "seeded unchanged: .obsidian/snippets/stix.css, .obsidian/bookmarks.json",
            "settings not carried over (kept in markdown/STIX2.1-vault.bak/.obsidian): "
            ".obsidian/hotkeys.json, .obsidian/workspace.json"])
        written = [{"path": ".obsidian/graph.json", "status": "written"}]
        self.assertEqual(stix2md.seed_lines(dirs, written), ["seeded written: .obsidian/graph.json"])  # no hint
        self.assertEqual(stix2md.seed_lines(dirs, []), [])

    def test_backup_info(self) -> None:
        with tempfile.TemporaryDirectory(dir=PROJECT_ROOT / "markdown") as tmp:
            single = Path(tmp) / "x.bak.md"
            single.write_bytes(b"abc\n")
            self.assertEqual(stix2md.backup_info(single, True),
                             {"path": stix2md.rel(single), "bytes": 4, "sha256": sha256_file(single), "made": True})
            vault = Path(tmp) / "v.bak"
            (vault / OBSIDIAN_SETTINGS_DIR).mkdir(parents=True)
            (vault / MANIFEST_NAME).write_bytes(b"{}")
            (vault / "a.md").write_bytes(b"12345")
            (vault / OBSIDIAN_SETTINGS_DIR / "graph.json").write_bytes(b"not counted")
            self.assertEqual(stix2md.backup_info(vault, False),
                             {"path": stix2md.rel(vault), "files": 2, "bytes": 7,
                              "sha256": sha256_file(vault / MANIFEST_NAME), "made": False})
            gone = Path(tmp) / "gone.bak"
            self.assertEqual(stix2md.backup_info(gone, False),
                             {"path": stix2md.rel(gone), "bytes": None, "sha256": None, "made": False})

    def test_meta_schema(self) -> None:
        self.assertEqual(stix2md.base_info("2026-10-18T00:00:00Z", None)["schema"], "stix2md-meta/4")


if __name__ == "__main__":
    unittest.main()
