"""Dataclasses for the parsed document model and link targets."""
from dataclasses import dataclass, field
from typing import Literal


@dataclass(kw_only=True)
class Span:
    text: str
    bold: bool = False
    italic: bool = False
    code: bool = False        # inline code font (Consolas/Courier)
    code_line: bool = False   # text is in a 9pt code-line span; set by parser only
    href: str | None = None   # raw href exactly as in HTML ("#_abc..." or "https://...")


@dataclass(kw_only=True)
class Block:
    anchors: list[str] = field(default_factory=list)   # Word bookmark names attached here


@dataclass(kw_only=True)
class Heading(Block):
    level: int                # HTML level 1..5
    number: str               # "4.1.1", "E.1", "A"; "" only for "Notices"
    title: str                # "Properties"
    text: str                 # full normalised heading text, e.g. "Appendix A. Confidence Scales"


@dataclass(kw_only=True)
class Paragraph(Block):
    spans: list[Span]
    css_class: str = "MsoNormal"


@dataclass(kw_only=True)
class ListItem(Block):
    spans: list[Span]
    level: int = 1            # 1 or 2
    ordered: bool = False     # True only for <ol><li>
    marker: str | None = None # "a)", "b)", "c)" for letter items, else None


@dataclass(kw_only=True)
class CodeBlock(Block):
    lines: list[str]          # NBSP already converted to " ", each line rstripped
    soft_break_before: bool = False   # the run began right after the previous block's code-line blank


@dataclass(kw_only=True)
class Image(Block):
    src: str                  # absolute URL


@dataclass(kw_only=True)
class Cell:
    blocks: list[Block] = field(default_factory=list)
    colspan: int = 1
    rowspan: int = 1
    is_header: bool = False


@dataclass(kw_only=True)
class Table(Block):
    rows: list[list[Cell]] = field(default_factory=list)


@dataclass(kw_only=True)
class Cover:
    title: str
    stage: str                # "OASIS Standard"
    date_text: str            # "10 June 2021"
    date_iso: str             # "2021-06-10"
    stage_links: list[tuple[str, list[tuple[str, str]]]]  # [("This stage:", [(url, note), ...]), ...]
    abstract: list[list[Span]]                            # paragraphs


@dataclass(kw_only=True)
class Document:
    blocks: list[Block]
    cover: Cover | None = None
    link_pos: dict[str, list[tuple[int, int]]] = field(default_factory=dict)  # href -> (line, col0) of each <a>


@dataclass(frozen=True, kw_only=True)
class Target:
    """Format-agnostic link target; each Flavor writes it in its own syntax."""
    kind: Literal["section", "ref", "block", "url"]
    key: str                  # section number | ref slug | block slug | absolute URL


Corpus = dict[str, str]       # ordered {POSIX path relative to the output root: file text}
