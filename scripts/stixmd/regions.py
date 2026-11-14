"""Cover extraction and region stripping."""
import re

from .config import (COVER_ABSTRACT_LABEL, COVER_LABEL_RE, COVER_STAGE_LABELS, EXIT_PARSE,
                     EXPECTED_TITLE, NBSP, STRIP_H1_PREFIXES, ZWSP, StixMdError)
from .inline import plain_text
from .model import Block, Cover, Document, Heading, Paragraph, Span


def _visible(text: str) -> bool:
    return any(not (ch.isspace() or ch in (NBSP, ZWSP)) for ch in text)


def _is_label(block: Block) -> bool:
    if not isinstance(block, Paragraph) or not re.match(COVER_LABEL_RE, plain_text(block.spans)):
        return False
    return all(s.bold for s in block.spans if _visible(s.text))


def _external(href: str | None) -> bool:
    return bool(href) and not href.strip().startswith("#")


def _stage_link(par: Paragraph) -> tuple[str, str] | None:
    first = next((s for s in par.spans if _external(s.href) and _visible(s.text)), None)
    if first is None:
        return None
    link_text = plain_text([s for s in par.spans if s.href == first.href])
    note = plain_text(par.spans).replace(link_text, "", 1).strip()
    return first.href.strip(), note  # type: ignore[union-attr]


def _sections(cover: list[Block]) -> dict[str, list[Block]]:
    """Group cover blocks under the label paragraph that precedes them."""
    groups: dict[str, list[Block]] = {}
    current: str | None = None
    for block in cover:
        if _is_label(block):
            current = plain_text(block.spans)  # type: ignore[attr-defined]
            groups[current] = []
        elif current is not None:
            groups[current].append(block)
    return groups


MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August", "September",
          "October", "November", "December")


def _iso_date(text: str) -> str:
    """'10 June 2021' -> '2021-06-10' with a fixed English month table (locale independent)."""
    m = re.fullmatch(r"(\d{1,2}) ([A-Z][a-z]+) (\d{4})", text)
    if not m or m.group(2) not in MONTHS:
        raise StixMdError(EXIT_PARSE, f"cover date {text!r} not parseable")
    return f"{m.group(3)}-{MONTHS.index(m.group(2)) + 1:02d}-{int(m.group(1)):02d}"


def _cover(blocks: list[Block]) -> Cover:
    paras = [b for b in blocks if isinstance(b, Paragraph)]
    titles = [plain_text(p.spans) for p in paras if p.css_class == "MsoTitle"]
    if not titles or titles[0] != EXPECTED_TITLE:
        raise StixMdError(EXIT_PARSE, f"cover title is {titles[:1]!r}, expected {EXPECTED_TITLE!r}")
    subs = [plain_text(p.spans) for p in paras if p.css_class == "MsoSubtitle"]
    if len(subs) < 2:
        raise StixMdError(EXIT_PARSE, "cover lacks the stage and date subtitles")
    date_iso = _iso_date(subs[1])
    groups = _sections(blocks)
    stage_links: list[tuple[str, list[tuple[str, str]]]] = []
    for label in COVER_STAGE_LABELS:
        links = [lk for b in groups.get(label, []) if isinstance(b, Paragraph)
                 and (lk := _stage_link(b)) is not None]
        if len(links) != 3:
            raise StixMdError(EXIT_PARSE, f"cover {label!r} has {len(links)} links, expected 3")
        stage_links.append((label, links))
    abstract: list[list[Span]] = [b.spans for b in groups.get(COVER_ABSTRACT_LABEL, [])
                                  if isinstance(b, Paragraph)]
    if not abstract:
        raise StixMdError(EXIT_PARSE, "cover abstract is empty")
    return Cover(title=titles[0], stage=subs[0], date_text=subs[1], date_iso=date_iso,
                 stage_links=stage_links, abstract=abstract)


def apply_regions(doc: Document) -> list[Block]:
    """Set doc.cover, keep only wanted blocks in doc.blocks and return the removed blocks."""
    first = next((i for i, b in enumerate(doc.blocks) if isinstance(b, Heading)), None)
    if first is None:
        raise StixMdError(EXIT_PARSE, "document has no headings")
    cover_blocks = doc.blocks[:first]
    cover = _cover(cover_blocks)
    removed: list[Block] = list(cover_blocks)
    kept: list[Block] = []
    dropping = False
    for block in doc.blocks[first:]:
        if isinstance(block, Heading) and block.level == 1:
            dropping = block.text.startswith(STRIP_H1_PREFIXES)
        (removed if dropping else kept).append(block)
    doc.blocks = kept
    doc.cover = cover
    return removed
