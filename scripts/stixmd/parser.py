"""HTML -> Document parser."""
import html.parser
import logging
import re
from dataclasses import dataclass, field
from typing import NamedTuple
from urllib.parse import urljoin

from .config import (BASE_URL, BULLET_L1, BULLET_L2, CODE_FONTS, CODE_LINE_SIZE, EXIT_PARSE,
                     HEADER_BG, HEADING_RES, LETTER_MARKER_RE, LEVEL2_MARGIN, LIST_SPACER, MAX_SPAN,
                     NBSP, NEG_INDENT, SOURCE_ENCODING, VOID_TAGS, ZWSP, StixMdError, norm_style)
from .model import (Block, Cell, CodeBlock, Document, Heading, Image, ListItem, Paragraph, Span,
                    Table)

log = logging.getLogger("stixmd")
BLOCK_TAGS = frozenset({"p", "li", "h1", "h2", "h3", "h4", "h5"})
SKIP_TAGS = frozenset({"head", "style", "script", "title"})


def decode_html(raw: bytes) -> str:
    """Decode cp1252 strictly after checking the declared charset."""
    if b"charset=windows-1252" not in raw[:4096].lower():
        raise StixMdError(EXIT_PARSE, "charset=windows-1252 not declared in the first 4096 bytes")
    try:
        return raw.decode(SOURCE_ENCODING, "strict")
    except UnicodeDecodeError as exc:
        raise StixMdError(EXIT_PARSE, f"source does not decode as {SOURCE_ENCODING}: {exc}") from exc


def _visible(text: str) -> bool:
    return any(not (ch.isspace() or ch == ZWSP) for ch in text)


def _plain(spans: list[Span]) -> str:
    return "".join(s.text for s in spans).replace(ZWSP, "").replace(NBSP, " ")


class Frame(NamedTuple):
    tag: str
    bold: bool
    italic: bool
    code: bool
    code_line: bool
    skip: bool
    href: str | None


@dataclass
class _OpenBlock:
    tag: str
    style: str
    css_class: str
    spans: list[Span] = field(default_factory=list)


@dataclass
class _Container:
    blocks: list[Block]
    run: list[str] = field(default_factory=list)
    run_anchors: list[str] = field(default_factory=list)
    soft: bool = False        # the last code block ended at an invisible all-code-line paragraph


class _SpecParser(html.parser.HTMLParser):
    """Stateful converter from Word-filtered HTML to model blocks."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.doc = Document(blocks=[])
        self.stack: list[Frame] = [Frame("#root", False, False, False, False, False, None)]
        self.outputs: list[_Container] = [_Container(self.doc.blocks)]
        self.block: _OpenBlock | None = None
        self.pending_anchors: list[str] = []
        self.pending_images: list[Image] = []
        self.table: Table | None = None

    # ---- inline state -------------------------------------------------------
    def _push(self, tag: str, attrs: dict[str, str | None]) -> None:
        top = self.stack[-1]
        st = norm_style(attrs.get("style"))
        font = "font-family:" in st and any(f in st for f in CODE_FONTS)
        href = attrs.get("href") if tag == "a" and attrs.get("href") is not None else top.href
        if tag == "a" and attrs.get("href") is not None:
            self.doc.link_pos.setdefault(href, []).append(self.getpos())   # for diagnostics only
        self.stack.append(Frame(
            tag, top.bold or tag in {"b", "strong"}, top.italic or tag in {"i", "em"},
            top.code or font, top.code_line or (font and CODE_LINE_SIZE in st),
            top.skip or LIST_SPACER in st or tag in SKIP_TAGS, href))

    def _pop(self, tag: str) -> None:
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                return

    def _span(self, text: str) -> Span:
        f = self.stack[-1]
        return Span(text=text, bold=f.bold, italic=f.italic, code=f.code,
                    code_line=f.code_line, href=f.href)

    def handle_starttag(self, tag: str, attrs_list: list[tuple[str, str | None]]) -> None:
        """Track formatting, anchors, images, blocks and tables."""
        attrs = dict(attrs_list)
        if tag == "a" and attrs.get("name"):
            self.pending_anchors.append(attrs["name"] or "")
        if tag == "img" and attrs.get("src"):
            self.pending_images.append(Image(src=urljoin(BASE_URL, attrs["src"] or "")))
        if tag == "br" and self.block is not None:
            self.block.spans.append(self._span("\n"))
        if tag not in VOID_TAGS:
            self._push(tag, attrs)
        if tag in BLOCK_TAGS:
            self._start_block(tag, attrs)
        elif tag in {"table", "tr", "td", "th"}:
            self._structure_start(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        """Close blocks and table parts, then pop the format stack."""
        if tag in BLOCK_TAGS and self.block is not None and self.block.tag == tag:
            self._end_block()
        elif tag in {"td", "th"} and self.table is not None:
            self._close_cell()
        elif tag == "table" and self.table is not None:
            self._close_cell()
            table, self.table = self.table, None
            self.outputs[-1].blocks.append(table)
        if tag not in VOID_TAGS:
            self._pop(tag)

    def handle_data(self, data: str) -> None:
        """Append text to the open block as a Span carrying the frame flags."""
        if self.stack[-1].skip or self.block is None:
            return
        text = data.replace("\r\n", " ").replace("\r", " ").replace("\n", " ").replace(ZWSP, "")
        if text:
            self.block.spans.append(self._span(text))

    # ---- block assembly -----------------------------------------------------
    def _start_block(self, tag: str, attrs: dict[str, str | None]) -> None:
        if self.block is not None:
            log.warning("<%s> opened inside <%s>; closing the outer block", tag, self.block.tag)
            self._end_block()
        self.block = _OpenBlock(tag, norm_style(attrs.get("style")), attrs.get("class") or "")

    def _structure_start(self, tag: str, attrs: dict[str, str | None]) -> None:
        if tag == "table":
            if self.table is not None:
                raise StixMdError(EXIT_PARSE, "nested table")
            self._flush_run(self.outputs[-1])
            self.table = Table(anchors=self.pending_anchors)
            self.pending_anchors = []
        elif tag == "tr" and self.table is not None:
            self._close_cell()
            self.table.rows.append([])
        elif tag in {"td", "th"} and self.table is not None:
            if not self.table.rows:
                self.table.rows.append([])
            self._close_cell()
            cell = Cell(colspan=_span_attr(attrs.get("colspan")), rowspan=_span_attr(attrs.get("rowspan")),
                        is_header=HEADER_BG in norm_style(attrs.get("style")))
            self.table.rows[-1].append(cell)
            self.outputs.append(_Container(cell.blocks))

    def _close_cell(self) -> None:
        """End the open cell, if any (also used when </td> or </tr> is missing)."""
        if self.block is not None and len(self.outputs) > 1:
            self._end_block()
        if len(self.outputs) > 1:
            self._flush_run(self.outputs[-1])
            self.outputs.pop()

    def _flush_run(self, out: _Container, soft: bool = False) -> None:
        """End the code run; soft: the end is an invisible all-code-line paragraph, so a run that follows
        directly gets `soft_break_before`."""
        lines = out.run
        while lines and not lines[0]:
            lines.pop(0)
        while lines and not lines[-1]:
            lines.pop()
        if lines:
            out.blocks.append(CodeBlock(lines=list(lines), anchors=out.run_anchors, soft_break_before=out.soft))
        out.soft = soft and (bool(lines) or out.soft)
        out.run, out.run_anchors = [], []

    def _end_block(self) -> None:
        blk, self.block = self.block, None
        assert blk is not None
        out = self.outputs[-1]
        spans = blk.spans
        if blk.tag.startswith("h") and _visible("".join(s.text for s in spans)):
            self._flush_run(out)
            out.blocks.append(_heading(blk, self._take_anchors()))
        elif not _visible("".join(s.text for s in spans)):
            self._flush_run(out, soft=bool(spans) and all(s.code_line for s in spans))
        elif all(s.code_line for s in spans if _visible(s.text)):
            text = "".join(s.text for s in spans).replace(NBSP, " ").replace(ZWSP, "")
            out.run.extend(piece.rstrip() for piece in text.split("\n"))
            out.run_anchors.extend(self._take_anchors())
        else:
            self._flush_run(out)
            out.blocks.append(self._prose(blk))
        if self.pending_images:
            out.soft = False
        out.blocks.extend(self.pending_images)
        self.pending_images = []

    def _take_anchors(self) -> list[str]:
        anchors, self.pending_anchors = self.pending_anchors, []
        return anchors

    def _prose(self, blk: _OpenBlock) -> Block:
        spans, style = blk.spans, blk.style
        if blk.tag == "li":
            return ListItem(spans=spans, ordered=True, level=1, anchors=self._take_anchors())
        first = next((ch for ch in _plain(spans) if not ch.isspace()), "")
        if NEG_INDENT in style and first in (BULLET_L1, BULLET_L2):
            level = 2 if LEVEL2_MARGIN in style else 1
            return ListItem(spans=_strip_glyph(spans, first), level=level, anchors=self._take_anchors())
        m = re.match(LETTER_MARKER_RE, _plain(spans).lstrip())
        if NEG_INDENT in style and m:
            return ListItem(spans=_strip_prefix(spans, m.end()), marker=m.group(1) + ")",
                            anchors=self._take_anchors())
        return Paragraph(spans=spans, css_class=blk.css_class, anchors=self._take_anchors())

    def finish(self) -> Document:
        """Flush open state at end of input and return the document."""
        self.close()
        if self.block is not None:
            self._end_block()
        for out in reversed(self.outputs):
            self._flush_run(out)
        self.doc.blocks.extend(self.pending_images)
        leftover = [f.tag for f in self.stack[1:] if f.tag not in {"html", "body", "div"}]
        if leftover:
            log.warning("unclosed tags at end of document: %s", leftover)
        return self.doc


def _span_attr(value: str | None) -> int:
    """colspan/rowspan as a positive int; malformed counts as 1, above MAX_SPAN is corrupt input."""
    number = int(value) if value and re.fullmatch(r"[0-9]+", value.strip()) and int(value) > 0 else 1
    if number > MAX_SPAN:
        raise StixMdError(EXIT_PARSE, f"colspan/rowspan {number} exceeds {MAX_SPAN}")
    return number


def _heading(blk: _OpenBlock, anchors: list[str]) -> Heading:
    text = " ".join(_plain(blk.spans).split())
    number, title = "", text
    for pattern in HEADING_RES:
        m = re.match(pattern, text)
        if m:
            number, title = m.group(1), m.group(2)
            break
    return Heading(level=int(blk.tag[1]), number=number, title=title, text=text, anchors=anchors)


def _strip_glyph(spans: list[Span], glyph: str) -> list[Span]:
    """Remove the bullet glyph from the first span holding it and leading blanks after it."""
    out = list(spans)
    for i, span in enumerate(out):
        if glyph in span.text:
            out[i] = _replace_text(span, span.text.replace(glyph, "", 1))
            break
    return _strip_prefix(out, 0)


def _strip_prefix(spans: list[Span], count: int) -> list[Span]:
    """Drop leading whitespace, then `count` characters, then whitespace again, across spans."""
    out: list[Span] = []
    lead = True
    for span in spans:
        text = span.text
        if lead:
            text = text.lstrip(" \t\n" + NBSP + ZWSP)
            take = min(count, len(text))
            text, count = text[take:], count - take
            if count == 0:
                text = text.lstrip(" \t\n" + NBSP + ZWSP)
            lead = not text
        if text:
            out.append(_replace_text(span, text))
    return out


def _replace_text(span: Span, text: str) -> Span:
    return Span(text=text, bold=span.bold, italic=span.italic, code=span.code,
                code_line=span.code_line, href=span.href)


def parse_html(html_text: str) -> Document:
    """Convert decoded HTML into a Document of blocks (cover is None)."""
    parser = _SpecParser()
    parser.feed(html_text)
    return parser.finish()
