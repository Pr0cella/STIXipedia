"""Unit tests for stixmd.config."""
import re
import unittest

from stixmd import config


class ConfigTests(unittest.TestCase):
    def test_norm_style_removes_whitespace_and_lowercases(self) -> None:
        self.assertEqual(config.norm_style(" Font-Family:\n Consolas "), "font-family:consolas")
        self.assertEqual(config.norm_style(None), "")

    def test_project_root(self) -> None:
        self.assertTrue((config.PROJECT_ROOT / "scripts" / "stixmd" / "config.py").is_file())

    def test_error_carries_exit_code(self) -> None:
        err = config.StixMdError(config.EXIT_USAGE, "bad")
        self.assertEqual((err.exit_code, str(err)), (2, "bad"))

    def test_heading_patterns(self) -> None:
        def number(text: str) -> str | None:
            return next((m.group(1) for p in config.HEADING_RES if (m := re.match(p, text))), None)
        self.assertEqual(number("Appendix E. References"), "E")
        self.assertEqual(number("E.1 Normative References"), "E.1")
        self.assertEqual(number("6.7.6.2.1 Properties"), "6.7.6.2.1")
        self.assertIsNone(number("Notices"))

    def test_md_url_and_reserved_names(self) -> None:
        self.assertEqual(config.md_url(" https://e.org/a b(c) "), "https://e.org/a%20b%28c%29")
        self.assertTrue({"con", "conin$", "com1", "lpt9", "com" + chr(0xB9)} <= config.WINDOWS_RESERVED)

    def test_special_characters(self) -> None:
        self.assertEqual([ord(c) for c in (config.ZWSP, config.NBSP, config.BULLET_L1, config.BULLET_L2)],
                         [0x200B, 0xA0, 0x25CF, 0x25CB])


if __name__ == "__main__":
    unittest.main()
