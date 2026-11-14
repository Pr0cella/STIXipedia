"""Unit tests for stixmd.meta."""
import re
import tempfile
import unittest
from pathlib import Path

from stixmd.config import PROJECT_ROOT
from stixmd.meta import _matrix, render_meta, sha256_file, to_yaml, write_meta

INFO = {"schema": "stix2md-meta/4", "run_utc": "2026-10-15T12:00:00Z", "status": "failed", "exit_code": 1,
        "generator": "stix2md 0.1", "python": "3.15.0",
        "scripts": [{"path": "scripts/a.py", "sha256": "00"}, {"path": "scripts/b.py", "sha256": "11"}],
        "source": {"path": "html/x.html", "bytes": 3, "sha256": "aa", "url": None}, "counts": {},
        "outputs": {
            "gfm": {"status": "failed", "exit_code": 1, "output": {"path": "markdown/x.md", "bytes": 9, "sha256": "cc"},
                    "rejected": None, "backup": {"path": "markdown/x.bak.md", "bytes": 7, "sha256": "bb", "made": True},
                    "validation": {"passed": False, "checks": {"V1": "pass", "V2": "fail: 3 failures"}},
                    "links": {"R1": 5, "R5": 0}},
            "vault": {"status": "success", "exit_code": 0,
                      "output": {"path": "markdown/v", "files": 147, "bytes": 99, "sha256": "dd"},
                      "validation": {"passed": True, "checks": {"V1": "pass", "V9": "pass"}},
                      "links": {"R1": 5, "R5": 0}, "user_files": [{"path": "My notes.md", "change": "added"}],
                      "obsidian_seed": [{"path": ".obsidian/graph.json", "status": "written"},
                                        {"path": ".obsidian/hotkeys.json", "status": "dropped"}]}}}


def front_matter_keys(text: str) -> list[str]:
    lines = text.split("\n")
    end = lines.index("---", 1)
    for ln in lines[1:end]:
        assert ln.startswith((" ", "- ")) or re.match(r"^[a-z_]+:( |$)", ln), ln
    return [ln.split(":")[0] for ln in lines[1:end] if ln and not ln.startswith((" ", "-"))]


class MetaTests(unittest.TestCase):
    def test_yaml_scalars(self) -> None:
        self.assertEqual(to_yaml({"a": None, "b": True, "c": 3, "d": "x\"y", "e": [], "f": ["ü"]}),
                         ['a: null', 'b: true', 'c: 3', 'd: "x\\"y"', 'e: []', 'f:', '  - "ü"'])

    def test_write_meta(self) -> None:
        with tempfile.TemporaryDirectory(dir=PROJECT_ROOT / "markdown") as tmp:
            path = Path(tmp) / "META.md"
            write_meta(path, INFO)
            text = path.read_text("utf-8")
            self.assertTrue(text.startswith("---\n"))
            keys = front_matter_keys(text)
            for key in ("schema", "run_utc", "status", "outputs"):
                self.assertIn(key, keys)
            self.assertIn("# Run metadata", text)
            self.assertIn('generator: "stix2md 0.1"\n', text)
            self.assertIn("| generator | stix2md 0.1 (Python 3.15.0) |", text)
            self.assertIn("| check | gfm | vault |", text)
            self.assertIn("| V2 | fail: 3 failures |  |", text)
            self.assertIn("| V9 |  | pass |", text)
            self.assertIn("| R1 | 5 | 5 |", text)
            self.assertIn("| vault output (147 files) | markdown/v | 99 | dd |", text)
            self.assertIn("| vault | My notes.md | added |", text)
            self.assertIn("| gfm backup | markdown/x.bak.md | 7 | bb |", text)
            self.assertIn("## Obsidian settings (written by every run)", text)
            self.assertIn("| vault | .obsidian/graph.json | written |", text)
            self.assertIn("| vault | .obsidian/hotkeys.json | dropped |", text)
            self.assertIn("    obsidian_seed:\n      - path: \".obsidian/graph.json\"\n        status: \"written\"", text)
            self.assertIn("  - path: \"scripts/a.py\"\n    sha256: \"00\"", text)
            self.assertEqual(len(sha256_file(path) or ""), 64)
        self.assertIsNone(sha256_file(Path(tmp) / "gone.md"))


    def test_validation_rows_in_number_order(self) -> None:     # by check number, not first-seen order
        gfm = ["V1", "V2", "V2c", "V3", "V4", "V5", "V6", "V7", "V8"]
        vault = [*gfm, "V10", "V16", "V17", "V9", "V11", "V12"]          # first-seen order would put V9 after V17
        links = {r: 0 for r in ("R1", "R2", "R3", "R4", "R4b", "R5")}
        outputs = {fmt: {"validation": {"checks": dict.fromkeys(checks, "pass")}, "links": links}
                   for fmt, checks in (("gfm", gfm), ("vault", vault))}
        self.assertEqual([row[0] for row in _matrix(outputs, "validation")],
                         [*gfm, "V9", "V10", "V11", "V12", "V16", "V17"])
        self.assertEqual(_matrix(outputs, "validation")[9:11], [("V9", None, "pass"), ("V10", None, "pass")])
        self.assertEqual([row[0] for row in _matrix(outputs, "links")], list(links))
        text = render_meta({"outputs": outputs})
        self.assertLess(text.index("| V9 |"), text.index("| V10 |"))

if __name__ == "__main__":
    unittest.main()
