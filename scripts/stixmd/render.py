"""Blocks -> Markdown body lines, parameterised by Flavor and the current note."""
from .flavor import Flavor
from .inline import code_fence, render_spans
from .layout import NoteId
from .model import Block, CodeBlock, Heading, Image, ListItem, Paragraph, Table
from .tables import render_table


def bracket_depth(lines: list[str]) -> int:
    """{ and [ minus } and ], outside double-quoted strings (a backslash escapes inside a string)."""
    depth, in_string, escaped = 0, False, False
    for ch in "\n".join(lines):
        if in_string:
            escaped, in_string = (False, True) if escaped else (ch == "\\", ch != '"')
        elif ch == '"':
            in_string = True
        elif ch in "{[":
            depth += 1
        elif ch in "}]":
            depth -= 1
    return depth


def merge_split_code(blocks: list[Block]) -> list[Block]:
    """Join code blocks that a code-line blank split in two; the vault only.

    A block with soft_break_before joins the code block before it while that block's bracket depth is above 0.
    Merged blocks are new objects: the parsed Document serves every format and stays unchanged.
    """
    out: list[Block] = []
    for block in blocks:
        prev = out[-1] if out else None
        if (isinstance(block, CodeBlock) and block.soft_break_before and isinstance(prev, CodeBlock)
                and bracket_depth(prev.lines) > 0):
            out[-1] = CodeBlock(lines=[*prev.lines, "", *block.lines], anchors=[*prev.anchors, *block.anchors],
                                soft_break_before=prev.soft_break_before)
        else:
            out.append(block)
    return out


def render_body(blocks: list[Block], flavor: Flavor, *, note: NoteId = "",
                shift: int = 1) -> tuple[list[str], list[tuple[Heading, int]]]:
    """Render blocks of one note; return body lines and (heading, anchor line index) pairs.

    A heading of HTML level L is written at Markdown level L + shift. Each heading starts a new inline writer for
    its section (identifier links need the current section).
    """
    if flavor.merge_code_blocks:
        blocks = merge_split_code(blocks)
    lines: list[str] = []
    heads: list[tuple[Heading, int]] = []
    current: Heading | None = None
    prev: Block | None = None
    number = 0
    writer = flavor.writer(note)
    for block in blocks:
        if lines and lines[-1] != "" and not (isinstance(prev, ListItem) and isinstance(block, ListItem)):
            lines.append("")
        anchor_index = len(lines)
        targets = flavor.amap.targets_for(block)
        lines.extend(flavor.anchor_lines(targets))
        number = number + 1 if isinstance(block, ListItem) and block.ordered else 0
        context = current.text if current else ""
        if isinstance(block, Heading):
            heads.append((block, anchor_index))
            lines.append(flavor.heading_line(block, block.level + shift))
            current = block
            writer = flavor.writer(note, block.number)
        elif isinstance(block, Paragraph):
            text = render_spans(block.spans, writer)
            if text:
                lines.append(text + flavor.anchor_suffix(targets))
        elif isinstance(block, ListItem):
            indent = "  " * (block.level - 1)
            if block.marker:
                prefix = f"- {block.marker} "
            elif block.ordered:
                prefix = f"{number}. "
            else:
                prefix = "- "
            lines.append(indent + prefix + render_spans(block.spans, writer, at_line_start=False)
                         + flavor.anchor_suffix(targets))
        elif isinstance(block, CodeBlock):
            lines.extend(code_fence(block.lines))
        elif isinstance(block, Image):
            lines.append(f"![Figure in section {context.replace(']', ')')}]({block.src})")
        elif isinstance(block, Table):
            lines.extend(render_table(block, writer, context))
        prev = block
    while lines and lines[-1] == "":
        lines.pop()
    return lines, heads
