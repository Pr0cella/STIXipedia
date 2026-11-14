"""Unit tests for stixmd.inline."""
import unittest
from dataclasses import replace

from stixmd.config import NBSP, ZWSP
from stixmd.inline import code_fence, md_link_writer, plain_text, render_spans
from stixmd.model import Span


def _resolve(href: str, text: str) -> str | None:
    if href == "#kix.x":
        return "#ref-rfc8259"
    return href if href.strip() and text.strip() else None


resolve = md_link_writer(_resolve)


def S(text: str, **kw: object) -> Span:  # noqa: N802 - short factory for readable tables
    return Span(text=text, **kw)  # type: ignore[arg-type]


class RenderSpansTests(unittest.TestCase):
    CASES = [
        ([S("The "), S("type", bold=True, code=True), S(" property "), S("MUST", bold=True), S(" be.")],
         "The **`type`** property **MUST** be."),
        ([S("see ["), S("RFC8259", href="#kix.x"), S("]")], "see [[RFC8259](#ref-rfc8259)]"),
        ([S("a" + NBSP + NBSP + "b" + ZWSP + "c  d")], "a bc d"),
        ([S("snake_case and _lead * star")], "snake_case and \\_lead \\* star"),
        ([S("bold ", bold=True), S("text", bold=True)], "**bold text**"),
        ([S("a"), S(" x ", bold=True), S("b")], "a **x** b"),
        ([S("# not heading")], "\\# not heading"),
        ([S("1. x")], "1\\. x"),
        ([S("a`b", code=True)], "``a`b``"),
        ([S("x"), S(" ", href="https://e.org")], "x"),
        ([S("x <tag> and <= y")], "x \\<tag> and <= y"),
        ([S("both", bold=True, italic=True)], "***both***"),
        ([S("`edge", code=True)], "`` `edge ``"),
        ([S("[a](b) &amp; a & b")], "[a\\](b) \\&amp; a & b"),
        ([S("x", href="https://e.org/a b(c)")], "[x](https://e.org/a%20b%28c%29)"),
    ]

    def test_cases(self) -> None:
        for spans, expected in self.CASES:
            with self.subTest(expected=expected):
                self.assertEqual(render_spans(spans, resolve), expected)

    def test_punctuation_only_runs_lose_marks_when_merged(self) -> None:   # Obsidian only
        merged = replace(resolve, merge_emphasis=True)
        for spans, gfm, obsidian in (
                ([S("Available"), S(":", bold=True)], "Available**:**", "Available:"),
                ([S('"', bold=True), S("ISO")], '**"**ISO', '"ISO'),
                ([S("by "), S("a", italic=True), S(". ", bold=True, italic=True), S("The")],
                 "by *a****.*** The", "by *a*. The"),
                ([S("type", bold=True, code=True), S(")", bold=True)], "**`type`****)**", "**`type`)**"),
                ([S("x"), S("C", bold=True, href="https://e.org"), S(")", bold=True)],
                 "x[**C**](https://e.org)**)**", "x[**C**](https://e.org))"),
                ([S("-", bold=True), S("x")], "**-**x", "-x"),
                ([S("+", bold=True)], "**+**", "**+**")):                 # a symbol, not punctuation (P*)
            with self.subTest(gfm=gfm):
                self.assertEqual(render_spans(spans, resolve), gfm)
                self.assertEqual(render_spans(spans, merged, at_line_start=False), obsidian)

    def test_not_at_line_start(self) -> None:
        self.assertEqual(render_spans([S("- item")], resolve, at_line_start=False), "- item")

    def test_plain_text(self) -> None:
        self.assertEqual(plain_text([S(" a" + NBSP + "\n"), S(ZWSP + "b  c ")]), "a b c")


class CodeFenceTests(unittest.TestCase):
    def test_json_and_plain(self) -> None:
        self.assertEqual(code_fence(["{", "}"]), ["```json", "{", "}", "```"])
        self.assertEqual(code_fence(["[", "  {", "  }", "]"])[0], "```json")
        self.assertEqual(code_fence(["[a] AND [b]"])[0], "```")

    def test_tilde_fence_when_backticks_inside(self) -> None:
        self.assertEqual(code_fence(["x ``` y"]), ["~~~~", "x ``` y", "~~~~"])


if __name__ == "__main__":
    unittest.main()
