"""Unit tests for stixmd.render."""
import unittest

from stixmd.anchors import AnchorMap
from stixmd.model import Block, CodeBlock, Heading, Image, ListItem, Paragraph, Span
from stixmd.flavor import GfmFlavor, ObsidianFlavor
from stixmd.layout import SingleLayout
from stixmd.render import bracket_depth, merge_split_code, render_body


def li(text: str, **kw: object) -> ListItem:
    return ListItem(spans=[Span(text=text)], **kw)  # type: ignore[arg-type]


def gfm(blocks: list[Block]) -> GfmFlavor:
    return GfmFlavor(AnchorMap.build(blocks, []), SingleLayout(b for b in blocks if isinstance(b, Heading)))


def render(blocks: list[Block]) -> list[str]:
    return render_body(blocks, gfm(blocks))[0]


class RenderTests(unittest.TestCase):
    def test_list_items_are_adjacent_and_paragraph_separated(self) -> None:
        lines = render([li("one"), li("two", level=2), Paragraph(spans=[Span(text="after")])])
        self.assertEqual(lines, ["- one", "  - two", "", "after"])

    def test_heading_anchor_line(self) -> None:
        head = Heading(level=3, number="4.1.1", title="Properties", text="4.1.1 Properties")
        lines, heads = render_body([Paragraph(spans=[Span(text="x")]), head], gfm([head]))
        self.assertEqual(lines, ["x", "", '<a id="sec-4.1.1"></a>', "#### 4.1.1 Properties"])
        self.assertEqual(heads, [(head, 2)])

    def test_ordered_numbering_restarts_and_markers(self) -> None:
        lines = render([li("a", ordered=True), li("b", ordered=True), Paragraph(spans=[Span(text="p")]),
                        li("c", ordered=True), li("d", marker="a)")])
        self.assertEqual(lines, ["1. a", "2. b", "", "p", "", "1. c", "- a) d"])

    def test_code_and_image(self) -> None:
        head = Heading(level=2, number="4.7", title="Img [x]", text="4.7 Img [x]")
        lines = render([head, CodeBlock(lines=["{", "}"]), Image(src="https://h/i.png")])
        self.assertEqual(lines[-6:], ["```json", "{", "}", "```", "", "![Figure in section 4.7 Img [x)](https://h/i.png)"])

    def test_no_trailing_blank_lines(self) -> None:
        self.assertEqual(render([Paragraph(spans=[Span(text="x")]), Paragraph(spans=[Span(text=" ")])]), ["x"])


def code(*lines: str, soft: bool = False) -> CodeBlock:
    return CodeBlock(lines=list(lines), soft_break_before=soft)


class MergeSplitCodeTests(unittest.TestCase):
    def test_bracket_depth_skips_strings_and_escapes(self) -> None:
        self.assertEqual(bracket_depth(["{", '  "a": "[{",', '  "b": "\\"]}"', "  [1, [2]"]), 2)
        self.assertEqual(bracket_depth(['{"c": "\\\\"}']), 0)          # an escaped backslash ends no string
        self.assertEqual(bracket_depth(["}", "]"]), -2)

    def test_joins_only_while_depth_is_above_zero(self) -> None:   # the 6.6.2.1 sequence
        balanced, opener, rest = code('{"a": 1}'), code("{", soft=True), code('  "b": 2', "}", soft=True)
        para, nested = Paragraph(spans=[Span(text="p")]), code("[", "{", soft=False)
        blocks = [balanced, opener, rest, para, nested, code("1}", soft=True), code("]", soft=True),
                  code("[]", soft=True)]
        merged = merge_split_code(blocks)
        self.assertEqual([b.lines if isinstance(b, CodeBlock) else "para" for b in merged],
                         [['{"a": 1}'], ["{", "", '  "b": 2', "}"], "para", ["[", "{", "", "1}", "", "]"], ["[]"]])
        self.assertIs(merged[0], balanced)
        self.assertEqual((opener.lines, rest.lines, nested.lines, len(blocks)), (["{"], ['  "b": 2', "}"], ["[", "{"], 8))

    def test_only_obsidian_merges(self) -> None:
        blocks: list[Block] = [code("{"), code("}", soft=True)]
        obsidian = ObsidianFlavor(AnchorMap.build(blocks, []), SingleLayout([]))
        self.assertEqual(render(blocks), ["```json", "{", "```", "", "```", "}", "```"])
        self.assertEqual(render_body(blocks, obsidian)[0], ["```json", "{", "", "}", "```"])


if __name__ == "__main__":
    unittest.main()
