"""Independent Markdown-vs-HTML validator for the GFM file and the vault. Imports only config."""
import hashlib
import html.parser
import json
import posixpath
import re
import unicodedata
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from itertools import product
from urllib.parse import unquote

from .config import (ALL_SCO_SECTION, ALL_SCO_TARGET, APPENDIX_B_ONLY, APPENDIX_TAG, ATLAS_ALIASES, ATLAS_GROUPS,
                     ATLAS_CSS_CLASS, ATLAS_NOTE, ATLAS_TAGS, BASE_SUFFIX, BULLET_L1,
                     BULLET_L2, CHAPTER_TAGS, CHILD_LIST_LABEL, CODE_CELL_FENCE_MIN_LINES, CODE_FONTS,
                     CODE_LINE_SIZE, COVER_ABSTRACT_LABEL, COVER_LABEL_RE, COVER_STAGE_LABELS, DEFAULT_VAULT,
                     DEPRECATED_TAG, DIAGRAM_BLOCK_PREFIX, DIAGRAM_FENCE, DIAGRAM_LABEL, ENUM_NAME_LABEL, EXPECTED_APPENDIX_B_TRIPLES, EXPECTED_DERIVED_TYPES,
                     EXPECTED_FORWARD_TABLES, EXPECTED_KEPT_HEADINGS, EXPECTED_OBJECTS, EXPECTED_REL_TYPES,
                     EXPECTED_TITLE, EXPECTED_TRIPLES, EXPECTED_TYPE_NAMES, EXPECTED_VOCABS, FORWARD_ONLY,
                     HEADER_BG, HEADING_RES, HOW_TO_TOOL_TERMS, ID_LINK_SKIP, INDEX_NOTE, LETTER_MARKER_RE,
                     LIST_SPACER, MANIFEST_NAME, MAX_PATH_CHARS, NAV_PREFIX, NBSP, NEG_INDENT, OBJECT_CSS_CLASS,
                     OBJECTS_BASE, OBS_HEADING_UNSAFE_RE, PROJECT_ROOT, PROPERTY_BANDS, PROPERTY_KEYS, R4B_LINK,
                     REF_LABEL_RE, REFERENCES_APPENDIX, RELATIONSHIP_APPENDIX, RELATIONSHIPS_BASE, SECTION_NUMBER_RE,
                     SOURCE_URL, SPECIFIC_BAND_SUFFIX, SPLIT_LEVEL, STRIP_H1_PREFIXES, TYPE_NAME_LABEL,
                     V2_EXTRA_TOKENS, VOCAB_NAME_LABEL, VOCABULARIES_BASE, VOID_TAGS, WIDE_CSS_CLASS,
                     WIDE_TABLE_MIN_COLUMNS, ZWSP, md_url, norm_style, sanitise_name)

BLOCKS = frozenset({"p", "li", "h1", "h2", "h3", "h4", "h5"})
LINK_TARGET_RE = re.compile(r"\]\(((?:[^()\s]|\([^()]*\))*)\)")
IMAGE_RE = re.compile(r"!\[[^\]]*\]\(")
ANCHOR_LINE_RE = re.compile(r'^<a id="([^"]*)"></a>$')
SEC_LINE_RE = re.compile(r'^<a id="sec-(.+)"></a>$')
HEADING_LINE_RE = re.compile(r"^(#{1,6}) (.*)$")
UNESCAPE_RE = re.compile(r"\\([\\`*_{}\[\]()#+\-.!<>|&])")
MARKER_RE = re.compile(r"(?<!\\)[*`]+")
CONTENTS_RE = re.compile(r"^( *)- \[(.+)\]\(#sec-([^)]+)\) · L(\d+)-L(\d+)$")
TAG_RE = re.compile(r"(?<!\\)<(/?[A-Za-z][^<>]*)>")
CODE_SPAN_RE = re.compile(r"(`+)(?:(?!\1).)+?\1(?!`)")
FORBIDDEN = (NBSP, ZWSP, "\r", "&nbsp;", "MsoNormal", "<span", "<p ", "<p>", "<table", "<td", "â€")


@dataclass
class Check:
    id: str
    name: str
    passed: bool
    stats: dict = field(default_factory=dict)
    failures: list[str] = field(default_factory=list)


@dataclass
class Report:
    passed: bool
    checks: list[Check]


def tokens(s: str) -> list[str]:
    """Casefolded NFC word tokens."""
    return re.findall(r"\w+", unicodedata.normalize("NFC", s).casefold())


def _clean(s: str) -> str:
    return " ".join(s.replace(ZWSP, "").replace(NBSP, " ").split())


def _visible(s: str) -> bool:
    return bool(s.replace(ZWSP, "").strip())


def _check(cid: str, name: str, failures: list[str], **stats: object) -> Check:
    return Check(cid, name, not failures, {**stats, "failures": len(failures)}, failures[:20])


class _HtmlFacts(html.parser.HTMLParser):
    """Collects the HTML-side facts the checks need, independently of the converter."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[tuple[str, bool, bool, bool, bool]] = [("#root", False, False, False, False)]
        self.key, self.dropped, self.order = "cover", False, []
        self.sections: dict[str, dict] = {"cover": {"level": 0, "text": "", "dropped": False, "parts": [],
                                                    "anchors": []}}
        self.block: dict | None = None
        self.link: dict | None = None             # open link group
        self.links: list[str] = []                 # hrefs of kept link groups with visible text
        self.removed_anchors: set[str] = set()
        self.cover_label, self.stage_urls, self.abstract = "", [], []
        self.type_names, self.run, self.tables = [], [], []
        self.vocab_names: list[str] = []           # "Vocabulary Name:" and "Enumeration Name:" lines
        self.type_keys: list[str] = []             # the section of each type_names entry
        self.runs: list[tuple[str, int | None, bool]] = []  # (squashed code, table id or None, soft break)
        self.soft = False                          # the last run ended at an invisible code-line paragraph
        self.table: dict | None = None
        self.cell: dict | None = None
        # where each kept anchor lives (section key, or "ref-<slug>" for an Appendix E reference)
        self.anchor_home: dict[str, str] = {}
        self.pending_names: list[str] = []         # anchors waiting for the next visible block (or E label)
        self.in_refs = False                       # inside Appendix E
        self.internal: list[tuple[str, str, str]] = []   # (section key, anchor name, link text) of internal links
        self.emphasis_blocks: list[tuple[str, dict]] = []   # (section, block) of prose outside tables (V16)
        self._emphasis: dict[str, dict] | None = None       # section -> bold and italic tokens, list items

    def _break_link(self) -> None:
        if self.link is None and self.block is not None and self.block["links"]:
            self.block["links"][-1]["adjacent"] = False

    def handle_starttag(self, tag: str, attrs_list: list) -> None:
        """Track formatting, blocks, links and tables."""
        attrs = dict(attrs_list)
        if tag == "br" and self.block is not None:
            self.block["chunks"].append(("\n", True, True, False, None))
            self._break_link()
        if tag not in VOID_TAGS:
            _, bold, italic, code_line, skip = self.stack[-1]
            st = norm_style(attrs.get("style"))
            font = "font-family:" in st and any(f in st for f in CODE_FONTS)
            self.stack.append((tag, bold or tag in {"b", "strong"}, italic or tag in {"i", "em"},
                               code_line or (font and CODE_LINE_SIZE in st),
                               skip or LIST_SPACER in st or tag in {"head", "style", "script", "title"}))
        if tag == "a" and attrs.get("name") and (self.dropped or self.key == "cover"):
            self.removed_anchors.add(attrs["name"])
        elif tag == "a" and attrs.get("name"):
            self.pending_names.append(attrs["name"])
        if tag == "a" and attrs.get("name") and self.block is not None:
            self.block["names"].append(attrs["name"])   # the anchors inside the block's element
        if tag == "a" and attrs.get("href") is not None:
            href = attrs["href"].strip()
            last = self.block["links"][-1] if self.block is not None and self.block["links"] else None
            if last is not None and last["href"] == href and last["adjacent"]:
                self.link = last                   # Word splits one link into adjacent <a>s
            else:
                self.link = {"href": href, "text": [], "adjacent": True}
                if self.block is not None:
                    self.block["links"].append(self.link)
                else:
                    self._commit_links([self.link], code=False)
        if tag in BLOCKS:
            if self.block is not None:             # blocks never nest; the parser closes the outer one too
                self._end_block()
            self.block = {"tag": tag, "chunks": [], "links": [], "names": [], "style": norm_style(attrs.get("style"))}
        elif tag == "table":
            self._end_run()
            if not self.in_refs:
                self._home(self.key)
            self.table = {"id": len(self.tables), "key": self.key, "dropped": self.dropped, "rows": []}
        elif tag == "tr" and self.table is not None:
            self.table["rows"].append([])
        elif tag in {"td", "th"} and self.table is not None:
            spans = [int(s) if re.fullmatch(r"[0-9]+", s := (attrs.get(k) or "").strip()) and int(s) > 0 else 1
                     for k in ("colspan", "rowspan")]
            self.cell = {"text": [], "code_paras": 0, "colspan": spans[0], "rowspan": spans[1],
                         "header": HEADER_BG in norm_style(attrs.get("style"))}
            if not self.table["rows"]:
                self.table["rows"].append([])
            self.table["rows"][-1].append(self.cell)

    def handle_endtag(self, tag: str) -> None:
        """Close blocks, links and table parts."""
        if tag in BLOCKS and self.block is not None:
            self._end_block()
        elif tag == "a":
            self.link = None
        elif tag in {"td", "th"} and self.cell is not None:
            self._end_run()
            self.cell = None
        elif tag == "table" and self.table is not None:
            self._end_run()
            self.tables.append(self.table)
            self.table = None
        if tag not in VOID_TAGS:
            del self.stack[next((i for i in range(len(self.stack) - 1, 0, -1) if self.stack[i][0] == tag),
                                len(self.stack)):]

    def handle_data(self, data: str) -> None:
        """Collect visible text (spacer spans and head content skipped)."""
        _, bold, italic, code_line, skip = self.stack[-1]
        if skip:
            return
        text = data.replace("\r\n", " ").replace("\r", " ").replace("\n", " ").replace(ZWSP, "")
        if self.link is not None:
            self.link["text"].append(text)
        self._break_link()
        if self.block is not None:
            internal = self.link if self.link is not None and self.link["href"].startswith("#") else None
            self.block["chunks"].append((text, code_line, bold, italic, internal))
        else:
            self._add_text(text)

    def _add_text(self, text: str) -> None:
        self.sections[self.key]["parts"].append(text)
        if self.cell is not None:
            self.cell["text"].append(text)

    def _commit_links(self, links: list[dict], *, code: bool) -> list[str]:
        # links inside code lines cannot survive conversion, so they are not counted
        hrefs = [md_url(lk["href"]) for lk in links if _visible("".join(lk["text"]))]
        if not code and not self.dropped and self.key != "cover":
            self.links.extend(hrefs)
            self.internal += [(self.key, lk["href"].strip()[1:], _clean("".join(lk["text"]))) for lk in links
                              if _visible("".join(lk["text"])) and lk["href"].strip().startswith("#")]
        return hrefs

    def _block_emphasis(self, blk: dict, text: str) -> None:
        """Keep a prose block for V16 with whether the parser makes it a list item. Blocks in table cells stay
        with their cell; emphasis_by_section() renders them as the converter does."""
        info = {"text": text, "chunks": blk["chunks"],
                "list": blk["tag"] == "li" or (NEG_INDENT in blk["style"] and (
                    text[0] in (BULLET_L1, BULLET_L2) or bool(re.match(LETTER_MARKER_RE, text))))}
        if self.cell is not None:
            self.cell.setdefault("blocks", []).append(info)
        elif not self.dropped and self.key != "cover":
            self.emphasis_blocks.append((self.key, info))

    def _marked(self, chunks: list[tuple], index: int, kept: set[str]) -> Counter:
        """Tokens of the chunks with flag `index` (2 bold, 3 italic). Text of an internal link that resolves (R1,
        R2) has none: Obsidian writes it as a wikilink, whose alias is plain text."""
        def plain(link: dict | None) -> bool:
            text = _clean("".join(link["text"])) if link else ""
            return link is not None and (link["href"].strip()[1:] in self.anchor_home
                                         or bool(re.match(SECTION_NUMBER_RE, text)) and text in kept)
        return Counter(tokens("".join(c[0] if c[index] and not plain(c[4]) else " " for c in chunks)))

    def emphasis_by_section(self) -> dict[str, dict]:
        """Per section: bold tokens, italic tokens and list items as the Obsidian outputs should have them."""
        if self._emphasis is None:
            kept, self._emphasis = set(self.kept_keys()), {}
            for key, info in self.emphasis_blocks:
                em = self._emph(key)
                em["bold"] += self._marked(info["chunks"], 2, kept)
                em["italic"] += self._marked(info["chunks"], 3, kept)
                em["lists"] += info["list"]
            for table in self.tables:
                if not (table["dropped"] or table["key"] == "cover"):
                    self._table_emphasis(table, self._emph(table["key"]), kept)
        return self._emphasis

    def _emph(self, key: str) -> dict:
        return self._emphasis.setdefault(key, {"bold": Counter(), "italic": Counter(), "lists": 0})

    def _table_emphasis(self, table: dict, em: dict, kept: set[str]) -> None:
        """Emphasis and list items of a table as the converter writes them: grid cells keep their
        emphasis, header bands become **label**, list items in grid cells are inline; record tables write
        **header: first cell** and *header:* labels, and their list items as list lines."""
        def text(cell: dict) -> str:
            return _clean("".join(cell["text"]))

        def add(cell: dict, *, bold_all: bool = False, lists: bool = False) -> None:
            """A cell's emphasis; bold_all: the converter wraps its whole text (code spans too) in **…**."""
            if bold_all:
                em["bold"] += Counter(tokens(text(cell)))
            for b in cell.get("blocks", []):
                em["bold"] += Counter() if bold_all else self._marked(b["chunks"], 2, kept)
                em["italic"] += self._marked(b["chunks"], 3, kept)
                em["lists"] += lists and b["list"]

        rows = table["rows"]
        if any(c["code_paras"] >= CODE_CELL_FENCE_MIN_LINES for r in rows for c in r):       # record mode
            header = [text(c) for c in rows[0]] if rows and any(c["header"] for c in rows[0]) else None
            for row in (rows[1:] if header else rows):
                if not row:
                    continue
                em["bold"] += Counter(tokens(header[0])) if header else Counter()
                add(row[0], bold_all=True)
                for j, cell in enumerate(row[1:], start=1):
                    if header and j < len(header):
                        em["italic"] += Counter(tokens(header[j]))
                    add(cell, lists=True)
            return
        rows = [r for r in rows if any(text(c) for c in r)]
        ncols = max((sum(c["colspan"] for c in r) for r in rows), default=0)
        band = [ncols == 1 or (len(r) == 1 and r[0]["colspan"] == ncols) for r in rows]
        for i, row in enumerate(rows):
            for cell in row:
                add(cell, bold_all=band[i] and cell["header"])

    def _home(self, target: str) -> None:
        """The pending anchors live at target (a section key or an Appendix E reference id)."""
        if not self.dropped and self.key != "cover":
            for name in self.pending_names:
                self.anchor_home.setdefault(name, target)
        self.pending_names = []

    def link_targets(self) -> dict[str, list[str]]:
        """Per section, the targets of its internal links in order, by the converter's rules R1 (a kept anchor) and
        R2 (link text that is a kept section number); links into removed content (R4, R4b, R5) are left out."""
        kept = set(self.kept_keys())
        out: dict[str, list[str]] = {}
        for key, name, text in self.internal:
            target = self.anchor_home.get(name)
            if target is None and re.match(SECTION_NUMBER_RE, text) and text in kept:
                target = text
            if target is not None:
                out.setdefault(key, []).append(target)
        return out

    def _end_run(self, soft: bool = False) -> None:
        """End the code run; soft: the end is an invisible all-code-line paragraph."""
        if self.run and not self.dropped and self.key != "cover":   # the converter drops these runs
            self.runs.append(("".join(self.run), self.table["id"] if self.table else None, self.soft))
        self.soft = soft and (bool(self.run) or self.soft)
        self.run = []

    def _end_block(self) -> None:
        blk, self.block, self.link = self.block, None, None
        raw = "".join(c[0] for c in blk["chunks"])
        text = _clean(raw)
        is_code = _visible(raw) and all(c[1] for c in blk["chunks"] if _visible(c[0]))
        hrefs = self._commit_links(blk["links"], code=is_code)
        if blk["tag"][0] == "h" and text:
            self._end_run()
            self._open_section(int(blk["tag"][1]), text, blk["names"])
            self._home(self.key)
        elif is_code:
            self.run.append("".join(raw.replace(ZWSP, "").split()))
            if self.cell is not None:
                self.cell["code_paras"] += 1
            if not self.in_refs:
                self._home(self.key)
        else:
            self._end_run(soft=not _visible(raw) and bool(blk["chunks"]) and all(c[1] for c in blk["chunks"]))
            if text and not self.in_refs:
                self._home(self.key)
            elif text and blk["tag"] == "p" and (m := re.match(REF_LABEL_RE, text)):
                self._home(_ref_slug(m.group(1)))       # in Appendix E, earlier anchors wait for the next label
            if text:
                self._block_emphasis(blk, text)
            if self.key == "cover" and text:
                self._cover_para(text, all(c[2] for c in blk["chunks"] if _visible(c[0])), hrefs)
            elif not self.dropped and self.table is None and text.startswith(TYPE_NAME_LABEL):
                self.type_names.append(text[len(TYPE_NAME_LABEL):].strip())
                self.type_keys.append(self.key)
            elif not self.dropped and self.table is None and text.startswith((VOCAB_NAME_LABEL, ENUM_NAME_LABEL)):
                self.vocab_names.append(text.split(":", 1)[1].strip())
        self._add_text(" " + raw + " ")

    def _open_section(self, level: int, text: str, anchors: list[str]) -> None:
        """Start a section; anchors: the <a name>s inside its heading element. The anchor
        bookkeeping can't give them: an anchor opens while the previous section is still current."""
        number = next((m.group(1) for p in HEADING_RES if (m := re.match(p, text))), "")
        if level == 1:
            self.dropped = text.startswith(STRIP_H1_PREFIXES)
            self.in_refs = number == REFERENCES_APPENDIX
        self.key = number if number not in self.sections else f"{number}#{len(self.order)}"
        self.order.append(self.key)
        self.sections[self.key] = {"level": level, "text": text, "dropped": self.dropped, "parts": [],
                                   "anchors": list(anchors)}

    def _cover_para(self, text: str, bold: bool, hrefs: list[str]) -> None:
        if bold and re.match(COVER_LABEL_RE, text):
            self.cover_label = text
        elif self.cover_label in COVER_STAGE_LABELS:
            self.stage_urls.extend([h for h in hrefs if not h.startswith("#")][:1])
        elif self.cover_label == COVER_ABSTRACT_LABEL:
            self.abstract.append(text)

    def kept_keys(self) -> list[str]:
        """Section keys of kept sections in document order."""
        return [k for k in self.order if not self.sections[k]["dropped"]]

    def record_tables(self) -> set[int]:
        """Ids of tables with a cell holding >= CODE_CELL_FENCE_MIN_LINES code-line paragraphs."""
        return {t["id"] for t in self.tables
                if any(c["code_paras"] >= CODE_CELL_FENCE_MIN_LINES for r in t["rows"] for c in r)}


WIKILINK_RE = re.compile(r"\[\[([^\[\]|]*?)(?:\\?\|([^\[\]]*?))?\]\]")    # [[target]], [[t|a]], [[t\|a]]
BLOCK_ID_RE = re.compile(r" \^([a-z0-9-]+)$")
BARE_BLOCK_ID_RE = re.compile(r"^\^([a-z0-9-]+)$")                 # a line that is only ^id (V4)
DIAGRAM_ID_RE = re.compile(rf"^\^{DIAGRAM_BLOCK_PREFIX}([a-z0-9-]+)$")
OBS_UNESCAPE_RE = re.compile(r"\\([\\`*_{}\[\]()#+\-.!<>|&$=%^])")
OBS_UNSAFE_RE = re.compile(OBS_HEADING_UNSAFE_RE)
OBS_HAZARDS = (("$", re.compile(r"(?<!\\)\$")), ("==", re.compile(r"==")), ("%%", re.compile(r"%%")),
               ("#tag", re.compile(r"(?<!\S)#(?=[^\s#])")), ("[[", re.compile(r"(?<!\\)\[\[")),
               ("~~", re.compile(r"(?<!\\)~~")))
ESCAPED_RE = re.compile(r"\\[!-/:-@\[-`{-~]")                      # a backslash escape of ASCII punctuation
TAG_OVERVIEW = ("### Tag overview", "", "| Tag | Notes |", "| --- | --- |")   # heading and table head
TAG_ROW_RE = re.compile(r"^\| #(stix/[a-z-]+) \| (\d+) \|$")         # one Tag overview row
BLOCK_LABEL_RE = re.compile(r"^\*\*\\\[(.+)\\\]\*\* \^([a-z0-9-]+)$")   # **\[Label\]** ^ref-label (Appendix E)
ID_LINK_RE = re.compile(r"\[(`+)([^`]+)\1\]\(([^()\s]*)\)")   # [`name`](dest): identifier link
ATLAS_PATH = f"{ATLAS_NOTE}.md"              # relationship atlas note at the vault root
FORMATS = ("gfm",)                          # single-file formats; the vault has validate_vault


@dataclass
class _Md:
    fmt: str
    lines: list[str]
    front_end: int                                      # index of the first line after the front matter
    body_start: int
    sections: dict[str, list[str]] = field(default_factory=dict)
    order: list[str] = field(default_factory=list)
    headings: list[tuple[int, str]] = field(default_factory=list)
    fences: list[str] = field(default_factory=list)
    fenced: set[int] = field(default_factory=set)      # line indices of fences and fenced content
    targets: list[str] = field(default_factory=list)
    link_count: int = 0
    wikilinks: list[tuple[int, str]] = field(default_factory=list)   # (line index, target), whole file
    block_ids: set[str] = field(default_factory=set)
    heading_norms: set[str] = field(default_factory=set)
    heading_numbers: dict[str, str] = field(default_factory=dict)    # normalised heading -> section number
    id_links: list[tuple[int, str, str]] = field(default_factory=list)   # (line index, name, dest), body only
    section_links: dict[str, list[str]] = field(default_factory=dict)    # section -> internal targets, in order
    prose: dict[str, list[str]] = field(default_factory=dict)            # section -> lines outside fences/headings
    diagrams: list[tuple[int, int]] = field(default_factory=list)   # diagram blocks: (label line, block-id line)
    generated: set[int] = field(default_factory=set)                 # line indices inside diagram blocks
    stray_mermaid: list[int] = field(default_factory=list)          # mermaid fences outside a diagram block

    @property
    def unescape(self) -> re.Pattern:
        return UNESCAPE_RE if self.fmt == "gfm" else OBS_UNESCAPE_RE


def _obs_norm(text: str) -> str:
    """Obsidian heading-link comparison form: unsafe characters become spaces, spaces collapse."""
    return " ".join(OBS_UNSAFE_RE.sub(" ", text).split())


def _heading_number(text: str) -> str | None:
    return next((m.group(1) for p in HEADING_RES if (m := re.match(p, text))), None)


def _front_end(lines: list[str]) -> int:
    if lines and lines[0] == "---" and "---" in lines[1:]:
        return lines.index("---", 1) + 1
    return 0


def _body_start(lines: list[str], fmt: str, front_end: int) -> int:
    if fmt == "gfm":
        return next((i for i, ln in enumerate(lines) if ln.startswith('<a id="sec-')), len(lines))
    for i in range(front_end, len(lines)):
        h = HEADING_LINE_RE.match(lines[i])
        if h and len(h.group(1)) == 2 and _heading_number(OBS_UNESCAPE_RE.sub(r"\1", h.group(2))):
            return i
    return len(lines)


INTERNAL_LINK_RE = re.compile(r"\[\[([^\[\]|]*?)(?:\\?\|[^\[\]]*?)?\]\]|\]\(((?:[^()\s]|\([^()]*\))*)\)")


def _internal_targets(line: str, fmt: str = "obsidian") -> list[str]:
    """Raw internal link targets of a body line in order: `#…` Markdown targets and `[[`-prefixed wikilink targets
    (vault text only: gfm writes `[[RFC7493]](#ref-…)`). Identifier links (checked by V10) and code
    spans are left out."""
    out = []
    pattern = LINK_TARGET_RE if fmt == "gfm" else INTERNAL_LINK_RE
    for m in pattern.finditer(CODE_SPAN_RE.sub("", ID_LINK_RE.sub("", line))):
        if fmt == "gfm":
            out += [m.group(1)] if m.group(1).startswith("#") else []
            continue
        if m.group(1) is not None:
            out.append("[[" + m.group(1).rstrip("\\"))
        elif m.group(2).startswith("#"):
            out.append(m.group(2))
    return out


def _md_target(raw: str) -> str:
    """Section key or reference id (`ref-<slug>`) of a raw target in the GFM file; `?…` if unknown."""
    if raw.startswith("#sec-"):
        return raw[5:]
    if raw.startswith("#ref-"):
        return "ref-" + re.sub(r"[^a-z0-9]+", "-", raw[5:]).strip("-")
    return "?" + raw


def _link_target_fails(facts: "_HtmlFacts", got: dict[str, list[str]]) -> tuple[int, list[str]]:
    """(HTML targets, failures): per section, the Markdown link targets must equal the HTML's, in order."""
    want = facts.link_targets()
    fails = [f"sec {k}: link targets {got.get(k, [])[:5]} != html {want.get(k, [])[:5]}"
             for k in facts.kept_keys() if got.get(k, []) != want.get(k, [])]
    return sum(map(len, want.values())), fails


def _r4b_fails(md: "_Md") -> list[str]:
    """The link into removed content that rule R4b resolves must be the expected external link."""
    sec, text, url = R4B_LINK
    if sec in md.sections and f"[{text}]({url})" not in "\n".join(md.sections[sec]):
        return [f"sec {sec}: the R4b link [{text}]({url}) is missing"]
    return []


def _md_facts(md_text: str, fmt: str = "gfm", *, body_start: int | None = None) -> _Md:
    """Extract the Markdown-side facts (body_start overrides where the body begins). fmt "obsidian" reads
    vault text: the vault's notes, the virtual document of all of them, and the index note."""
    lines = md_text.split("\n")
    front = _front_end(lines)
    start = _body_start(lines, fmt, front) if body_start is None else body_start
    md = _Md(fmt, lines, front, start)
    fence: str | None = None
    current: list[str] = []
    section: list[str] = []
    links: list[str] = []
    prose: list[str] = []
    if fmt != "gfm":
        _obsidian_facts(md)
    skip = -1
    for n in range(md.body_start, len(lines)):
        ln = lines[n]
        if n <= skip:
            continue
        if fmt != "gfm" and fence is None and (end := _diagram_end(lines, n)) is not None:
            md.diagrams.append((n, end))                     # diagram block: no tokens, code block, links or prose
            md.generated.update(range(n, end + 1))
            skip = end
            continue
        if fence is not None or ln.startswith(("```", "~~~~")):
            md.fenced.add(n)
            if fence is None:
                if fmt != "gfm" and ln.startswith(DIAGRAM_FENCE):
                    md.stray_mermaid.append(n)
                fence, current = ("~~~~" if ln.startswith("~~~~") else "```"), []
            elif ln == fence:
                md.fences.append("".join("".join(current).replace(ZWSP, "").split()))
                fence = None
            else:
                current.append(ln)
                section.append(ln)
            continue
        if fmt == "gfm" and (m := SEC_LINE_RE.match(ln)):
            md.order.append(m.group(1))
            section = md.sections[m.group(1)] = []
            links = md.section_links[m.group(1)] = []
            prose = md.prose[m.group(1)] = []
            continue
        if h := HEADING_LINE_RE.match(ln):
            text = md.unescape.sub(r"\1", h.group(2))
            md.headings.append((len(h.group(1)), text))
            if fmt != "gfm" and (number := _heading_number(text)) is not None:
                md.order.append(number)
                section = md.sections[number] = []
                links = md.section_links[number] = []
                prose = md.prose[number] = []
        elif not ANCHOR_LINE_RE.match(ln):
            prose.append(ln)
        links += _internal_targets(ln, fmt)
        if not ANCHOR_LINE_RE.match(ln) and not ln.startswith("!["):
            section.append(ln)
        if fmt != "gfm":                     # identifier links are counted by V10, not as body links
            md.id_links.extend((n, m.group(2), m.group(3)) for m in ID_LINK_RE.finditer(ln))
            ln = ID_LINK_RE.sub(r"\1\2\1", ln)
        found = LINK_TARGET_RE.findall(ln)
        md.targets.extend(found)
        md.link_count += len(found) - len(IMAGE_RE.findall(ln))
        if fmt != "gfm":
            md.link_count += len(WIKILINK_RE.findall(ln))
    return md


def _diagram_end(lines: list[str], n: int) -> int | None:
    """Diagram block: the index of the block-id line if a diagram block starts at line n, else None.

    Exactly: the label, one blank line, a mermaid fence, its content, the closing fence, ^stix-rel-<type>.
    """
    if lines[n] != DIAGRAM_LABEL or lines[n + 1:n + 3] != ["", DIAGRAM_FENCE]:
        return None
    close = next((m for m in range(n + 3, len(lines)) if lines[m] == "```"), None)
    if close is None or close + 1 >= len(lines) or not DIAGRAM_ID_RE.match(lines[close + 1]):
        return None
    return close + 1


def _block_id(line: str) -> str | None:
    """The block id a line defines: a ` ^id` line ending, or a line that is exactly `^id` (a diagram's last line)."""
    m = BLOCK_ID_RE.search(line) or BARE_BLOCK_ID_RE.match(line)
    return m.group(1) if m else None


def _obsidian_facts(md: _Md) -> None:
    """Whole-file wikilinks, block ids and heading texts (outside fences and front matter)."""
    fence: str | None = None
    for n in range(md.front_end, len(md.lines)):
        ln = md.lines[n]
        if fence is not None or ln.startswith(("```", "~~~~")):
            if fence is None:
                fence = "~~~~" if ln.startswith("~~~~") else "```"
            elif ln == fence:
                fence = None
            continue
        md.wikilinks.extend((n, m.group(1)) for m in WIKILINK_RE.finditer(CODE_SPAN_RE.sub("", ln)))
        if (block := _block_id(ln)) is not None:
            md.block_ids.add(block)
        if h := HEADING_LINE_RE.match(ln):
            text = OBS_UNESCAPE_RE.sub(r"\1", h.group(2))
            md.heading_norms.add(_obs_norm(text))
            if (number := _heading_number(text)) is not None:
                md.heading_numbers[_obs_norm(text)] = number


def _wikitext(m: re.Match) -> str:
    if m.group(2) is not None:
        return m.group(2)
    note, _, frag = m.group(1).rstrip("\\").partition("#")
    return f"{note} {frag.lstrip('^')}"


def _md_tokens(line: str, fmt: str = "gfm") -> list[str]:
    """Tokens of a Markdown line: link targets and <br> dropped, markers zero-width, escapes undone.

    Obsidian: wikilinks count as their alias (or note and heading), identifier links as their code span,
    block-id suffixes are dropped.
    """
    if fmt != "gfm":
        line = BLOCK_ID_RE.sub("", WIKILINK_RE.sub(_wikitext, ID_LINK_RE.sub(r"\1\2\1", line)))
    text = LINK_TARGET_RE.sub("]", line).replace("<br>", " ")
    unescape = UNESCAPE_RE if fmt == "gfm" else OBS_UNESCAPE_RE
    return tokens(unescape.sub(r"\1", MARKER_RE.sub("", text)))


def _v1(facts: _HtmlFacts, md: _Md) -> Check:
    html_heads = [(facts.sections[k]["level"] + 1, facts.sections[k]["text"]) for k in facts.kept_keys()]
    fails = [f"#{i}: html {a!r} != md {b!r}" for i, (a, b) in enumerate(zip(html_heads, md.headings)) if a != b]
    if len(html_heads) != len(md.headings) or len(html_heads) != EXPECTED_KEPT_HEADINGS:
        fails.append(f"count html {len(html_heads)}, md {len(md.headings)}, expected {EXPECTED_KEPT_HEADINGS}")
    return _check("V1", "headings", fails, html=len(html_heads), md=len(md.headings))


def _v2(facts: _HtmlFacts, md: _Md) -> Check:
    keys, fails, extra = facts.kept_keys(), [], 0
    if set(keys) != set(md.order):
        fails.append(f"section keys differ: html-only {sorted(set(keys) - set(md.order))[:10]}, "
                     f"md-only {sorted(set(md.order) - set(keys))[:10]}")
    for k in keys:
        html_tok = Counter(tokens("".join(facts.sections[k]["parts"])))
        md_tok = Counter(tok for ln in md.sections.get(k, []) for tok in _md_tokens(ln, md.fmt))
        surplus, allowed = md_tok - html_tok, Counter(V2_EXTRA_TOKENS.get(k, {}))
        extra += sum(surplus.values())
        if missing := html_tok - md_tok:
            fails.append(f"sec {k}: missing {sorted(missing.elements())[:12]}")
        if surplus != allowed:                  # duplicated or copied content
            fails.append(f"sec {k}: extra tokens {sorted((surplus - allowed).elements())[:12]}, "
                         f"expected extra not found {sorted((allowed - surplus).elements())[:12]}")
    return _check("V2", "text coverage", fails, sections=len(keys), extra_md_tokens=extra)


def _v2c(facts: _HtmlFacts, md: _Md) -> Check:
    header = "\n".join(md.lines[:md.body_start])
    urls, abstract = facts.stage_urls, facts.abstract
    fails = [f"missing {s!r}" for s in (EXPECTED_TITLE, "OASIS Standard", "10 June 2021") if s not in header]
    if len(urls) != 9:
        fails.append(f"expected 9 stage URLs in HTML, found {len(urls)}")
    fails += [f"missing stage link {u}" for u in urls if f"]({u})" not in header]
    missing = Counter(tokens(" ".join(abstract))) - Counter(tokens(header))
    if not abstract or missing:
        fails.append(f"abstract tokens missing: {sorted(missing)[:12]}")
    return _check("V2c", "cover", fails, stage_urls=len(urls), abstract_paras=len(abstract))


def _bracket_depth(code: str) -> int:
    """{ and [ minus } and ], outside double-quoted strings (a backslash escapes inside a string)."""
    depth, in_string, escaped = 0, False, False
    for ch in code:
        if in_string:
            escaped, in_string = (False, True) if escaped else (ch == "\\", ch != '"')
        elif ch == '"':
            in_string = True
        elif ch in "{[":
            depth += 1
        elif ch in "}]":
            depth -= 1
    return depth


def _v3(facts: _HtmlFacts, md: _Md) -> Check:
    """The vault: a soft-broken run joins the one before while its bracket depth is above 0."""
    records = facts.record_tables()
    runs: list[str] = []
    for code, tid, soft in facts.runs:
        if tid is not None and tid not in records:
            continue
        if md.fmt != "gfm" and soft and runs and _bracket_depth(runs[-1]) > 0:
            runs[-1] += code
        else:
            runs.append(code)
    fails = [f"L{n + 1}: mermaid fence outside a diagram block" for n in md.stray_mermaid]   # none in the source
    fails += [f"count html {len(runs)} != md {len(md.fences)}"] if len(runs) != len(md.fences) else []
    fails += [f"block #{i}: html {a[:60]!r} != md {b[:60]!r}"      # the count first: only 20 failures are kept
              for i, (a, b) in enumerate(zip(runs, md.fences)) if a != b]
    return _check("V3", "code", fails, html=len(runs), md=len(md.fences))


def _v4(facts: _HtmlFacts, md: _Md) -> Check:
    external = Counter(h for h in facts.links if not h.startswith("#"))
    md_ext = Counter(t for t in md.targets if not t.startswith("#"))
    fails = [f"external link missing: {h} (x{n})" for h, n in (external - md_ext).items()]
    ids = {m.group(1) for ln in md.lines if (m := ANCHOR_LINE_RE.match(ln))}
    targets = {t for ln in md.lines for t in LINK_TARGET_RE.findall(ln) if t.startswith("#")}
    fails += [f"dangling target {t}" for t in sorted(targets) if t[1:] not in ids]
    into_removed = sum(1 for h in facts.links if h.startswith("#") and h[1:] in facts.removed_anchors)
    if md.link_count < len(facts.links) - into_removed:
        fails.append(f"md body links {md.link_count} < html links {len(facts.links) - into_removed}")
    targets, target_fails = _link_target_fails(
        facts, {k: [_md_target(t) for t in ts] for k, ts in md.section_links.items()})
    fails += target_fails + _r4b_fails(md)
    return _check("V4", "links", fails, html_links=len(facts.links), into_removed=into_removed,
                  md_links=md.link_count, html_external=sum(external.values()), targets=targets)


def _index_rows(lines: list[str], title: str) -> list[list[str]]:
    start = lines.index(title) + 1 if title in lines else len(lines)
    end = next((i for i in range(start, len(lines)) if lines[i].startswith("### ") or lines[i] == "---"), len(lines))
    rows = [ln for ln in lines[start:end] if ln.startswith("|") and not ln.startswith("| ---")]
    return [[c.strip() for c in ln.strip("|").split(" | ")] for ln in rows[1:]]


def _type_vocab(facts: _HtmlFacts, header: list[str], fails: list[str],
                derived: tuple[str, ...] = ()) -> tuple[int, int]:
    """Type and vocabulary index sizes; the vault adds the derived types."""
    types = _index_rows(header, "### Type index")
    names = {r[0].strip("`").strip() for r in types}
    want = set(facts.type_names) | set(derived)
    if len(types) != EXPECTED_TYPE_NAMES + len(derived) or names != want:
        fails.append(f"type index {len(types)} rows; differs from html: {sorted(names ^ want)[:10]}")
    vocabs = _index_rows(header, "### Vocabulary index")
    if len(vocabs) != EXPECTED_VOCABS:
        fails.append(f"vocabulary index has {len(vocabs)} rows")
    return len(types), len(vocabs)


def _v5(facts: _HtmlFacts, md: _Md) -> Check:
    lines, fails, header = md.lines, [], md.lines[:md.body_start]
    entries = [m for ln in header if (m := CONTENTS_RE.match(ln))]
    levels = [len(m.group(1)) // 2 + 1 for m in entries]
    for i, m in enumerate(entries):
        num, start, end = m.group(3), int(m.group(4)), int(m.group(5))
        nxt = next((int(e.group(4)) - 1 for e, lv in zip(entries[i + 1:], levels[i + 1:]) if lv <= levels[i]),
                   len(lines) - 1)
        h = HEADING_LINE_RE.match(lines[start]) if 0 < start < len(lines) else None
        if not 0 < start < len(lines) or lines[start - 1] != f'<a id="sec-{num}"></a>':
            fails.append(f"{num}: L{start} is not its anchor line")
        elif h is None or UNESCAPE_RE.sub(r"\1", h.group(2)) != m.group(2) or len(h.group(1)) != levels[i] + 1:
            fails.append(f"{num}: heading line mismatch {lines[start][:40]!r}")
        if end != nxt:
            fails.append(f"{num}: end L{end} != L{nxt}")
    if len(entries) != EXPECTED_KEPT_HEADINGS:
        fails.append(f"contents has {len(entries)} entries")
    types, vocabs = _type_vocab(facts, header, fails)
    return _check("V5", "index", fails, contents=len(entries), types=types, vocabs=vocabs)


def _index_target_errors(facts: _HtmlFacts, lines: list[str], resolve: Callable[[str], str],
                         homes: dict[str, str]) -> list[str]:
    """Type-index rows link to the identifier's home; vocabulary rows to the section of their number, whose
    title is their text."""
    fails = []
    for row in _index_rows(lines, "### Type index"):
        name, m = row[0].strip("`").strip(), WIKILINK_RE.search(row[-1])
        if (got := resolve(m.group(1).rstrip("\\")) if m else None) != homes.get(name):
            fails.append(f"type index {name!r} links to {got!r}, its home is {homes.get(name)!r}")
    for row in _index_rows(lines, "### Vocabulary index"):
        m = WIKILINK_RE.search(row[-1])
        number = m.group(2) if m and m.group(2) else ""
        title = facts.sections.get(number, {}).get("text", "").removeprefix(number + " ")
        got = resolve(m.group(1).rstrip("\\")) if m else None
        if got != number or OBS_UNESCAPE_RE.sub(r"\1", row[0]) != title:
            fails.append(f"vocabulary index row {row[0]!r} links to {got!r} as {number!r}, title {title!r}")
    return fails


def _punct(ch: str) -> bool:
    return unicodedata.category(ch)[0] in "PS"


def _literal_stars(text: str) -> int:
    """Asterisks that CommonMark's delimiter algorithm leaves as text (flanking runs, rule of 3)."""
    return _emphasis(text)[0]


def _emphasis(text: str) -> tuple[int, list[tuple[int, int, int]]]:
    """CommonMark's delimiter algorithm for `*`: (asterisks left as text, emphasis ranges (start, end, 1 for italic
    or 2 for bold) of the text between the delimiters used)."""
    runs: list[list] = []                                # [remaining, original, can_open, can_close, start]
    for m in re.finditer(r"\*+", text):
        before = text[m.start() - 1] if m.start() else " "
        after = text[m.end()] if m.end() < len(text) else " "
        left = not after.isspace() and (not _punct(after) or before.isspace() or _punct(before))
        right = not before.isspace() and (not _punct(before) or after.isspace() or _punct(after))
        runs.append([len(m.group()), len(m.group()), left, right, m.start()])
    ranges: list[tuple[int, int, int]] = []
    stack, pos = list(range(len(runs))), 0
    while pos < len(stack):
        closer = runs[stack[pos]]
        if not closer[3]:
            pos += 1
            continue
        k = pos - 1
        while k >= 0:
            opener = runs[stack[k]]
            odd = (closer[2] or opener[3]) and closer[1] % 3 != 0 and (opener[1] + closer[1]) % 3 == 0
            if opener[2] and not odd:
                break
            k -= 1
        if k < 0:                                        # no opener: a closer that cannot open is done
            if closer[2]:
                pos += 1
            else:
                del stack[pos]
            continue
        use = 2 if opener[0] >= 2 and closer[0] >= 2 else 1
        # an opener gives up its rightmost delimiters, a closer its leftmost: the text between them is emphasised
        ranges.append((opener[4] + opener[0], closer[4] + closer[1] - closer[0], use))
        opener[0] -= use
        closer[0] -= use
        del stack[k + 1:pos]
        pos = k + 1
        if opener[0] == 0:
            del stack[k]
            pos = k
        if closer[0] == 0:
            del stack[pos]
    return sum(r[0] for r in runs), ranges


STAR_TEXT = chr(0x2020)        # an escaped `*` in the emphasis text: punctuation that is no delimiter


def _emphasis_tokens(line: str) -> tuple[Counter, Counter]:
    """(bold tokens, italic tokens) of a Markdown prose line (vault text; V16). Code spans
    keep their text, links and wikilinks their link text; escapes are undone; table rows are parsed per cell."""
    bold, italic = Counter(), Counter()
    for cell in (_md_cells(line) if line.startswith("|") else [line]):
        cell = ID_LINK_RE.sub(r"\1\2\1", cell)                   # an identifier link counts as its code span
        text, at = "", 0
        for m in CODE_SPAN_RE.finditer(cell):
            text += _emphasis_text(cell[at:m.start()]) + "`" + m.group(0).strip("`").replace("*", " ") + "`"
            at = m.end()
        text += _emphasis_text(cell[at:])
        _, ranges = _emphasis(text)
        for kind, counter in ((2, bold), (1, italic)):
            mask = [False] * len(text)
            for start, end, use in ranges:
                if use == kind:
                    mask[start:end] = [True] * (end - start)
            emphasised = "".join("" if ch == "`" else ch if on and ch != "*" else " " for ch, on in zip(text, mask))
            counter += Counter(tokens(emphasised))         # `x`_hex is one word, as in the HTML
    return bold, italic


def _emphasis_text(part: str) -> str:
    """Text outside code spans as the emphasis parser sees it: link text kept, escapes undone (`\\*` stays text)."""
    part = LINK_TARGET_RE.sub("]", WIKILINK_RE.sub(lambda m: "[" + _wikitext(m) + "]", part))
    part = BLOCK_ID_RE.sub("", part).replace("<br>", " ")
    return re.sub(r"\\(.)", lambda m: STAR_TEXT if m.group(1) == "*" else m.group(1), part)


def _emphasis_prose(line: str) -> list[str]:
    """The line as the emphasis parser sees it: code spans, links, wikilinks and escapes become punctuation,
    and a table row is split into its cells."""
    prose = LINK_TARGET_RE.sub("]", WIKILINK_RE.sub("[w]", CODE_SPAN_RE.sub("`", line)))
    prose = ESCAPED_RE.sub("!", prose)
    return prose.split("|") if prose.startswith("|") else [prose]


LIST_LINE_RE = re.compile(r"^ *(?:- |\d+\. )")


def _v16(facts: _HtmlFacts, md: _Md) -> Check:
    """Emphasis weight and lists, the vault: per section, the bold tokens, the italic tokens
    and the number of list items equal the HTML's, with the converter's own emphasis (bands, records) applied."""
    fails: list[str] = []
    totals: Counter = Counter()
    empty = {"bold": Counter(), "italic": Counter(), "lists": 0}
    for key in facts.kept_keys():
        want = facts.emphasis_by_section().get(key, empty)
        bold, italic, lists = Counter(), Counter(), 0
        for ln in md.prose.get(key, []):
            b, i = _emphasis_tokens(ln)
            bold, italic, lists = bold + b, italic + i, lists + bool(LIST_LINE_RE.match(ln))
        for name, got, exp in (("bold", bold, want["bold"]), ("italic", italic, want["italic"])):
            if got != exp:
                fails.append(f"sec {key}: {name} tokens, extra {sorted((got - exp).elements())[:8]}, "
                             f"missing {sorted((exp - got).elements())[:8]}")
        if lists != want["lists"]:
            fails.append(f"sec {key}: {lists} list items, html {want['lists']}")
        totals.update(bold_tokens=sum(want["bold"].values()), italic_tokens=sum(want["italic"].values()),
                      list_items=want["lists"])
    return _check("V16", "emphasis and lists", fails, bold_tokens=totals["bold_tokens"],
                  italic_tokens=totals["italic_tokens"], list_items=totals["list_items"])


def _ref_slug(label: str) -> str:
    return "ref-" + re.sub(r"[^a-z0-9]+", "-", label.casefold()).strip("-")


def _tag_rows(lines: list[str]) -> dict[int, tuple[str, int]]:
    """Tag overview: line index -> (tag, count) of the rows `| #stix/<name> | <count> |` in the table right
    under `### Tag overview`, up to the next blank line. Only these rows may hold a word-initial `#`."""
    starts = [i for i in range(len(lines)) if tuple(lines[i:i + len(TAG_OVERVIEW)]) == TAG_OVERVIEW]
    rows: dict[int, tuple[str, int]] = {}
    for i in range(starts[0] + len(TAG_OVERVIEW), len(lines)) if len(starts) == 1 else ():
        if lines[i] == "":
            break
        if m := TAG_ROW_RE.match(lines[i]):
            rows[i] = (m.group(1), int(m.group(2)))
    return rows


def _tag_overview(index: "_Note | None") -> dict[str, int]:
    """The index's Tag overview as {tag: count}; empty without an index or overview."""
    return {tag: n for tag, n in _tag_rows(index.lines).values()} if index is not None else {}


def _v6_v7(md_text: str, md: _Md, tag_rows: frozenset[int] = frozenset()) -> list[Check]:
    """V6 hygiene and V7 stripped content; tag_rows: lines where a `#tag` is wanted (the index's Tag overview)."""
    obsidian = md.fmt != "gfm"
    emphasis_lines = 0
    fails = [f"contains {s!r}" for s in FORBIDDEN if s in md_text]
    if obsidian and "<a id=" in md_text:
        fails.append("contains '<a id='")
    for n, ln in enumerate(md.lines):
        if n in md.fenced or n in md.generated or (obsidian and n < md.front_end):
            continue
        prose = CODE_SPAN_RE.sub("", ln)
        for tag in TAG_RE.findall(prose):
            if not (tag == "br" or (not obsidian and (re.fullmatch(r'a id="[^"]*"', tag) or tag == "/a"))):
                fails.append(f"L{n + 1}: tag <{tag[:40]}>")
        if obsidian:
            prose = LINK_TARGET_RE.sub("]", WIKILINK_RE.sub(" ", prose))
            fails += [f"L{n + 1}: unescaped {name}" for name, rx in OBS_HAZARDS
                      if rx.search(prose) and not (name == "#tag" and n in tag_rows)]
            if stars := sum(_literal_stars(cell) for cell in _emphasis_prose(ln)):
                emphasis_lines += 1
                fails.append(f"L{n + 1}: {stars} literal '*' (not emphasis): {ln[:60]!r}")
            if BLOCK_ID_RE.search(ln) and not ((m := BLOCK_LABEL_RE.match(ln))
                                               and m.group(2) == _ref_slug(OBS_UNESCAPE_RE.sub(r"\1", m.group(1)))):
                fails.append(f"L{n + 1}: block id outside an Appendix E label: {ln[-40:]!r}")
            if BARE_BLOCK_ID_RE.match(ln):                 # only the last line of a diagram block
                fails.append(f"L{n + 1}: bare block id outside a diagram block: {ln[:40]!r}")
    if not md_text.endswith("\n") or md_text.endswith("\n\n"):
        fails.append("file must end with exactly one newline")
    stripped = [f"heading {t!r}" for _, t in md.headings if t.startswith(STRIP_H1_PREFIXES)]
    stripped += [f"contains {s!r}" for s in ("Table of Contents", "OASIS IPR Policy") if s in md_text]
    stats = {"emphasis_lines": emphasis_lines} if obsidian else {}
    return [_check("V6", "hygiene", fails, **stats), _check("V7", "stripped", stripped)]


TABLE_PIPE_RE = re.compile(r"(?<!\\)\|")


def _md_cells(line: str) -> list[str]:
    """Cells of a Markdown table row, split on unescaped pipes as GFM does (inside code spans too)."""
    return [c.strip() for c in TABLE_PIPE_RE.split(line.strip())[1:-1]]


def _md_tables(lines: list[str]) -> list[tuple[int, list[list[str]]]]:
    """(width of the delimiter row, rows of cells including the header row) of each Markdown table in lines."""
    tables, i = [], 0
    while i < len(lines):
        if lines[i].startswith("|") and i + 1 < len(lines) and TABLE_RULE_RE.match(lines[i + 1]):
            j = i + 2
            while j < len(lines) and lines[j].startswith("|"):
                j += 1
            tables.append((len(_md_cells(lines[i + 1])), [_md_cells(ln) for ln in [lines[i], *lines[i + 2:j]]]))
            i = j
        else:
            i += 1
    return tables


def _table_shape_fails(lines: list[str], where: str) -> list[str]:
    """Every row of every Markdown table has as many cells as its delimiter row."""
    return [f"{where}: table row with {len(row)} cells, the table has {width}: {' | '.join(row)[:60]!r}"
            for width, rows in _md_tables(lines) for row in rows if len(row) != width]


def _html_segments(table: dict) -> list[tuple[bool, list[list[str]]]]:
    """(header row?, rows of column texts) of each Markdown table the converter makes of an HTML grid table: band
    rows split segments, and a segment with ragged rows or a rowspan is laid out on the colspan grid."""
    rows = [r for r in table["rows"] if any(_clean("".join(c["text"])) for c in r)]
    ncols = max((sum(c["colspan"] for c in r) for r in rows), default=0)
    band = [ncols == 1 or (len(r) == 1 and r[0]["colspan"] == ncols) for r in rows]
    segments, i = [], 0
    while i < len(rows):
        j = i
        while j < len(rows) and not band[j]:
            j += 1
        if j > i:
            seg = rows[i:j]
            texts = [[_clean("".join(c["text"])) for c in r] for r in seg]
            if len({len(r) for r in seg}) > 1 or any(c["rowspan"] > 1 for r in seg for c in r):
                grid, taken = [], set()
                for r, (row, row_texts) in enumerate(zip(seg, texts)):
                    out, col = [""] * ncols, 0
                    for cell, text in zip(row, row_texts):
                        while (r, col) in taken:
                            col += 1
                        if col < ncols:
                            out[col] = text
                        taken |= {(r + dr, col + dc) for dr in range(cell["rowspan"]) for dc in range(cell["colspan"])}
                        col += cell["colspan"]
                    grid.append(out)
                texts = grid
            segments.append((any(c["header"] for c in seg[0]), texts))
        i = max(j, i + 1)
    return segments


def _column_fails(facts: _HtmlFacts, md: _Md) -> tuple[int, list[str]]:
    """(cells checked, failures): each HTML grid-table cell's tokens lie in the Markdown cell of its column."""
    records, checked, fails = facts.record_tables(), 0, []
    by_key: dict[str, list[tuple[bool, list[list[str]]]]] = {}
    for table in facts.tables:
        if not (table["dropped"] or table["key"] == "cover" or table["id"] in records):
            by_key.setdefault(table["key"], []).extend(_html_segments(table))
    for key, segments in by_key.items():
        md_tables = _md_tables(md.prose.get(key, []))
        if len(md_tables) != len(segments):
            fails.append(f"sec {key}: {len(segments)} HTML table segments, {len(md_tables)} Markdown tables")
            continue
        for (header, grid), (_, md_rows) in zip(segments, md_tables):
            for r, row in enumerate(grid):
                md_row = md_rows[r if header else r + 1] if (r if header else r + 1) < len(md_rows) else []
                for c, text in enumerate(row):
                    if not text:
                        continue
                    checked += 1
                    cell = md_row[c] if c < len(md_row) else ""
                    if Counter(tokens(text)) - Counter(_md_tokens(cell, md.fmt)):
                        fails.append(f"sec {key}: column {c + 1} {text[:40]!r} is not in {cell[:40]!r}")
    return checked, fails


def _v8(facts: _HtmlFacts, md: _Md, extra_lines: tuple[str, ...] = ()) -> Check:
    """Table rows (every multi-cell HTML row on one Markdown line), shape and columns."""
    records, fails, rows = facts.record_tables(), [], 0
    for table in facts.tables:
        if table["dropped"] or table["key"] == "cover" or table["id"] in records:
            continue
        md_rows = [_md_tokens(ln, md.fmt) for ln in md.sections.get(table["key"], []) if ln.startswith("|")]
        for row in table["rows"]:
            cells = [_clean("".join(c["text"])) for c in row]
            if sum(1 for c in cells if c) < 2:
                continue
            rows += 1
            need = tokens(" ".join(cells))
            if not any(all(tok in it for tok in need) for it in map(iter, md_rows)):
                fails.append(f"sec {table['key']}: row {' | '.join(cells)[:80]!r}")
    unfenced = [ln for n, ln in enumerate(md.lines) if n not in md.fenced]
    fails += _table_shape_fails(unfenced, "body") + _table_shape_fails(list(extra_lines), "index")
    checked, column_fails = _column_fails(facts, md)
    return _check("V8", "table rows", fails + column_fails, rows=rows, cells_checked=checked)


# --- identifier links (V10) ------------------------------------------------------------------------------

LABEL_LINE_RE = re.compile(r"^\*\*(Type|Vocabulary|Enumeration) Name:?\*\*:? `([^`]+)`$")
TYPE_ROW_RE = re.compile(r"^\| \*\*`?type`?\*\* \(required\) \|")
TYPE_MUST_RE = re.compile(r"MUST\s+be\s+\[?`([a-z0-9-]+)`")
REQUIRED_BAND_LINE = "**Required Common Properties:**"
INLINE_RE = re.compile(r"(?P<esc>\\.)|(?P<link>\[(?P<lt>`+)(?P<ltext>[^`]+)(?P=lt)\]\((?P<dest>[^()\s]*)\))"
                       r"|(?P<code>(?P<ct>`+)(?P<ctext>(?:(?!(?P=ct)).)+?)(?P=ct)(?!`))|(?P<stars>\*+)")
Resolver = Callable[[str], tuple[str | None, str | None]]   # destination -> (section number, error)


def _in_subtree(number: str, home: str) -> bool:
    return number == home or number.startswith(home + ".")


def _code_spans(line: str) -> list[tuple[str, bool, str | None]]:
    """(text, bold, identifier-link destination or None) of each code span of a Markdown line.

    Bold follows the flat emphasis groups of the Obsidian writer: a run of asterisks first closes the open group,
    and the rest of the run opens the next one (`*a****.***` is italic `a`, then bold italic `.`).
    """
    spans: list[tuple[str, bool, str | None]] = []
    state = 0                                           # asterisks of the open group: 0, 1, 2 or 3
    for m in INLINE_RE.finditer(line):
        if m.group("stars"):
            n = len(m.group("stars"))
            closed = min(n, state)
            state, n = state - closed, n - closed
            state = min(n, 3) if n else state
        elif m.group("link"):
            spans.append((m.group("ltext").strip(), state >= 2, m.group("dest")))
        elif m.group("code"):
            spans.append((m.group("ctext").strip(), state >= 2, None))
    return spans


@dataclass
class _MdIdents:
    homes: dict[str, str]                         # identifier -> home section
    kinds: dict[str, str]                         # identifier -> type | vocabulary | enumeration
    tables: dict[str, str]                        # section of a property table -> its object type
    fails: list[str]


def _md_idents(facts: _HtmlFacts, md: _Md) -> _MdIdents:
    """Identifier -> home section, rebuilt from the Markdown body: the label lines and the `type` rows of
    property tables, whose type has no label line for the two derived types. Also the failures."""
    homes: dict[str, str] = {}
    kinds: dict[str, str] = {}
    tables: dict[str, str] = {}
    fails: list[str] = []
    for number, lines in md.sections.items():
        for ln in lines:
            if m := LABEL_LINE_RE.match(ln):
                if m.group(2) in homes:
                    fails.append(f"identifier {m.group(2)!r} labelled in {homes[m.group(2)]} and {number}")
                homes[m.group(2)], kinds[m.group(2)] = number, m.group(1).lower()
    types = [n for n, k in kinds.items() if k == "type"]
    vocabs = [n for n, k in kinds.items() if k != "type"]
    if len(types) != EXPECTED_TYPE_NAMES or set(types) != set(facts.type_names):
        fails.append(f"{len(types)} type labels; differ from html: {sorted(set(types) ^ set(facts.type_names))[:10]}")
    if len(vocabs) != EXPECTED_VOCABS or set(vocabs) != set(facts.vocab_names):
        fails.append(f"{len(vocabs)} vocabulary labels; differ from html: "
                     f"{sorted(set(vocabs) ^ set(facts.vocab_names))[:10]}")
    derived = []
    for number, lines in md.sections.items():
        if not any(ln.startswith(REQUIRED_BAND_LINE) for ln in lines):
            continue
        row = next((ln for ln in lines if TYPE_ROW_RE.match(ln)), None)
        value = TYPE_MUST_RE.search(row.replace("**", "").replace("<br>", " ")) if row else None
        if value is None:
            fails.append(f"sec {number}: property table without a `type` row")
            continue
        name, home = value.group(1), number.rpartition(".")[0]
        tables[number] = name
        if name not in homes:
            homes[name], kinds[name] = home, "type"
            derived.append(name)
        elif homes[name] != home:
            fails.append(f"sec {number}: the property table of {name!r} is not under its label in {homes[name]}")
    if tuple(derived) != tuple(EXPECTED_DERIVED_TYPES):
        fails.append(f"derived types {derived}, expected {list(EXPECTED_DERIVED_TYPES)}")
    return _MdIdents(homes, kinds, tables, fails)


def _id_link_scan(lines: list[str], homes: dict[str, str], resolve: Resolver, where: str) -> tuple[int, list[str]]:
    """V10 (1)-(3) over body lines: count the identifier links, check where they lead, and check that every other
    identifier code span (not bold, outside fences and headings) is in its home subtree or in ID_LINK_SKIP."""
    count, fails, section, fence = 0, [], "", None
    for ln in lines:
        if fence is not None or ln.startswith(("```", "~~~~")):
            if fence is None:
                fence = "~~~~" if ln.startswith("~~~~") else "```"
            elif ln == fence:
                fence = None
            continue
        if h := HEADING_LINE_RE.match(ln):
            section = _heading_number(OBS_UNESCAPE_RE.sub(r"\1", h.group(2))) or section
            if ID_LINK_RE.search(ln):
                fails.append(f"{where}identifier link in heading {ln[:60]!r}")
            continue
        for text, bold, dest in _code_spans(ln):
            home = homes.get(text)
            skip = home is None or _in_subtree(section, home) or (section, text) in ID_LINK_SKIP
            if dest is None:
                if not skip and not bold:
                    fails.append(f"{where}sec {section}: `{text}` is not linked")
                continue
            count += 1
            if home is None:
                fails.append(f"{where}sec {section}: link text `{text}` is not an identifier")
                continue
            target, err = resolve(dest)
            if err or target != home:
                fails.append(f"{where}sec {section}: `{text}` → {dest}: {err or f'section {target}, not {home}'}")
            if skip:
                fails.append(f"{where}sec {section}: `{text}` links from inside its home {home} or ID_LINK_SKIP")
    return count, fails


def validate(html_text: str, md_text: str, fmt: str = "gfm") -> Report:
    """Run the independent checks on the GFM file and return a Report."""
    if fmt not in FORMATS:
        raise ValueError(f"unknown format {fmt!r}")
    facts = _HtmlFacts()
    facts.feed(html_text)
    facts.close()
    md = _md_facts(md_text, fmt)
    checks = [_v1(facts, md), _v2(facts, md), _v2c(facts, md), _v3(facts, md), _v4(facts, md),
              _v5(facts, md), *_v6_v7(md_text, md), _v8(facts, md)]
    return Report(all(c.passed for c in checks), checks)


def validate_corpus(html_text: str, corpus: dict[str, str], fmt: str, *, root: str | None = None) -> Report:
    """Validate the Corpus of one output format; root is the vault folder for path lengths."""
    if fmt == "vault":
        return validate_vault(html_text, corpus, root or str(PROJECT_ROOT / DEFAULT_VAULT))
    if fmt not in FORMATS:
        raise ValueError(f"no validator for format {fmt!r}")
    if len(corpus) != 1:
        raise ValueError(f"a {fmt} corpus has exactly one file, got {len(corpus)}")
    (text,) = corpus.values()
    return validate(html_text, text, fmt)


# --- vault (V9) --------------------------------------------------------------------------------------

VAULT_CONTENTS_RE = re.compile(r"^( *)- \[\[([^\[\]|#]+)(?:#([^\[\]|]+))?(?:\|([^\[\]]+))?\]\]$")
FRONT_LINE_RE = re.compile(r"^([a-z][a-z0-9_]*): (.+)$")
CHILD_ITEM_RE = re.compile(r"^- \[\[([^\[\]|]+)\]\]$")       # one line of a chapter note's child list
NOTE_KEYS = ("title", "section", "kind", "chapter", "tags", "aliases", "up", "source_url", "source_sha256")
TABLE_RULE_RE = re.compile(r"^\|(?: --- \|)+$")         # the delimiter row of a rendered table


def _widest_table(body: list[str]) -> int:
    """Columns of the widest table in note body lines, fenced code skipped."""
    widest, fence = 0, None
    for ln in body:
        if fence is not None or ln.startswith(("```", "~~~~")):
            if fence is None:
                fence = "~~~~" if ln.startswith("~~~~") else "```"
            elif ln == fence:
                fence = None
        elif TABLE_RULE_RE.match(ln):
            widest = max(widest, ln.count(" --- "))
    return widest


@dataclass
class _Note:
    path: str
    text: str
    lines: list[str]
    front: dict
    front_end: int
    errors: list[str]
    heading_norms: set[str] = field(default_factory=set)
    heading_numbers: dict[str, str] = field(default_factory=dict)    # normalised heading -> section number
    block_ids: set[str] = field(default_factory=set)
    wikilinks: list[tuple[int, str]] = field(default_factory=list)

    @property
    def name(self) -> str:
        return self.path.rsplit("/", 1)[-1].removesuffix(".md")


def _parse_front(lines: list[str]) -> tuple[dict, int, list[str]]:
    """Mini-YAML checker: every front-matter line is `key: <JSON value>`, keys unique."""
    if not lines or lines[0] != "---" or "---" not in lines[1:]:
        return {}, 0, ["no front matter"]
    end = lines.index("---", 1)
    front: dict = {}
    errors: list[str] = []
    for ln in lines[1:end]:
        if not (m := FRONT_LINE_RE.match(ln)):
            errors.append(f"front matter line {ln[:40]!r} is not 'key: value'")
            continue
        if m.group(1) in front:
            errors.append(f"front matter key {m.group(1)!r} repeated")
        try:
            front[m.group(1)] = json.loads(m.group(2))
        except ValueError:
            errors.append(f"front matter value of {m.group(1)!r} is not a JSON scalar or list")
    return front, end + 1, errors


def _read_note(path: str, text: str) -> _Note:
    lines = text.split("\n")
    front, front_end, errors = _parse_front(lines)
    note = _Note(path, text, lines, front, front_end, errors)
    fence: str | None = None
    for n in range(front_end, len(lines)):
        ln = lines[n]
        if fence is not None or ln.startswith(("```", "~~~~")):
            if fence is None:
                fence = "~~~~" if ln.startswith("~~~~") else "```"
            elif ln == fence:
                fence = None
            continue
        note.wikilinks.extend((n, m.group(1)) for m in WIKILINK_RE.finditer(CODE_SPAN_RE.sub("", ln)))
        if (block := _block_id(ln)) is not None:
            note.block_ids.add(block)
        if h := HEADING_LINE_RE.match(ln):
            text = OBS_UNESCAPE_RE.sub(r"\1", h.group(2))
            note.heading_norms.add(_obs_norm(text))
            if (number := _heading_number(text)) is not None:
                note.heading_numbers[_obs_norm(text)] = number
    return note


def _without_footer(note: _Note) -> tuple[list[str], str | None]:
    """The lines after the front matter without the NAV line and the footer; plus the NAV line (or None)."""
    rest = note.lines[note.front_end:]
    while rest and rest[0] == "":
        rest = rest[1:]
    nav = None
    if rest and rest[0].startswith(NAV_PREFIX):
        nav, rest = rest[0], rest[1:]
        end = len(rest)
        while end and rest[end - 1] == "":
            end -= 1
        if end and rest[end - 1] == nav:                 # the footer: a copy of the NAV line
            rest = rest[:end - 1]
    return rest, nav


def _child_list(rest: list[str]) -> tuple[int, list[str]] | None:
    """Child-note list: (label index, child names) when `rest` ends with exactly the label, one blank line and
    one or more `- [[name]]` lines; else None. A label anywhere else is content."""
    end = len(rest)
    while end and rest[end - 1] == "":
        end -= 1
    start = end
    while start and CHILD_ITEM_RE.match(rest[start - 1]):
        start -= 1
    if start == end or start < 2 or rest[start - 1] != "" or rest[start - 2] != CHILD_LIST_LABEL:
        return None
    return start - 2, [CHILD_ITEM_RE.match(ln).group(1) for ln in rest[start:end]]


def _note_body(note: _Note) -> tuple[list[str], str | None]:
    """Body lines without front matter, NAV line, child-note list and footer; plus the NAV line (or None)."""
    rest, nav = _without_footer(note)
    found = _child_list(rest)
    body = rest[:found[0]] if found else rest
    while body and body[0] == "":
        body = body[1:]
    while body and body[-1] == "":
        body = body[:-1]
    return body, nav


def _unshift(body: list[str], root_level: int) -> list[str]:
    """Rewrite note heading levels (own heading '#') to single-file levels (HTML level + 1)."""
    out, fence = [], None
    for ln in body:
        if fence is not None or ln.startswith(("```", "~~~~")):
            if fence is None:
                fence = "~~~~" if ln.startswith("~~~~") else "```"
            elif ln == fence:
                fence = None
        elif h := HEADING_LINE_RE.match(ln):
            ln = "#" * (len(h.group(1)) + root_level) + " " + h.group(2)
        out.append(ln)
    return out


@dataclass
class _Vault:
    notes: dict[str, _Note]                       # path -> note, every .md file including the index
    index: _Note | None
    by_name: dict[str, list[str]]                 # casefolded basename -> paths
    order: list[str]                              # note paths in index Contents order
    contents: list[re.Match]
    virtual: _Md


def _vault_link_error(vault: _Vault, target: str, current: str) -> str | None:
    note, sep, frag = target.rstrip("\\").partition("#")
    paths = vault.by_name.get(note.casefold(), []) if note else [current]
    if len(paths) != 1:
        return f"unknown note {note!r}" if not paths else f"ambiguous note {note!r}"
    if paths[0] not in vault.notes:                       # a Bases file: [[STIX Objects.base|…]]
        return None if not sep else f"link into a non-note file {note!r}"
    home = vault.notes[paths[0]]
    if not sep:
        return None
    if frag.startswith("^"):
        return None if frag[1:] in home.block_ids else f"missing block ^{frag[1:]} in {home.name!r}"
    return None if _obs_norm(frag) in home.heading_norms else f"missing heading {frag!r} in {home.name!r}"


def _read_vault(corpus: dict[str, str]) -> _Vault:
    notes = {p: _read_note(p, t) for p, t in corpus.items() if p.endswith(".md")}
    index = notes.get(f"{INDEX_NOTE}.md")
    by_name: dict[str, list[str]] = {}
    for path, note in notes.items():
        by_name.setdefault(note.name.casefold(), []).append(path)
    for path in corpus:                                   # wikilinks name a .base file with its suffix
        if path.endswith(BASE_SUFFIX):
            by_name.setdefault(path.rsplit("/", 1)[-1].casefold(), []).append(path)
    contents: list[re.Match] = []
    if index is not None:
        start = index.lines.index("### Contents") + 1 if "### Contents" in index.lines else len(index.lines)
        end = next((i for i in range(start, len(index.lines)) if index.lines[i].startswith("### ")), len(index.lines))
        contents = [m for ln in index.lines[start:end] if (m := VAULT_CONTENTS_RE.match(ln))]
    order = [p for m in contents if m.group(3) is None
             for p in by_name.get(m.group(2).casefold(), [])[:1]]
    virtual: list[str] = []
    for path in order:
        note = notes[path]
        body, _ = _note_body(note)
        virtual += [*_unshift(body, len(str(note.front.get("section", "")).split("."))), ""]
    return _Vault(notes, index, by_name, order, contents, _md_facts("\n".join(virtual), "obsidian"))


def _vault_target(vault: _Vault, target: str, current: str) -> str:
    """Section key or reference id (`ref-<slug>`) of a wikilink target seen from a note; `?…` if unknown."""
    name, _, frag = target.partition("#")
    paths = vault.by_name.get(name.casefold(), []) if name else [current]
    home = vault.notes.get(paths[0]) if len(paths) == 1 else None
    if home is None:
        return "?" + target
    if frag.startswith("^"):
        return frag[1:]
    return home.heading_numbers.get(_obs_norm(frag), "?" + target) if frag else str(home.front.get("section"))


def _vault_section_links(vault: _Vault) -> dict[str, list[str]]:
    """Per section, the targets of the internal links in the note bodies, in reading order."""
    out: dict[str, list[str]] = {}
    for path in vault.order:
        note = vault.notes[path]
        body, _ = _note_body(note)
        links = out.setdefault(str(note.front.get("section")), [])
        fence: str | None = None
        for ln in body:
            if fence is not None or ln.startswith(("```", "~~~~")):
                if fence is None:
                    fence = "~~~~" if ln.startswith("~~~~") else "```"
                elif ln == fence:
                    fence = None
                continue
            h = HEADING_LINE_RE.match(ln)
            if h and (number := _heading_number(OBS_UNESCAPE_RE.sub(r"\1", h.group(2)))) is not None:
                links = out.setdefault(number, [])
            links += [_vault_target(vault, t[2:], path) if t.startswith("[[") else "?" + t
                      for t in _internal_targets(ln)]
    return out


def _v4_vault(facts: _HtmlFacts, vault: _Vault) -> Check:
    md = vault.virtual
    external = Counter(h for h in facts.links if not h.startswith("#"))
    md_ext = Counter(t for t in md.targets if not t.startswith("#"))
    fails = [f"external link missing: {h} (x{n})" for h, n in (external - md_ext).items()]
    fails += [f"internal markdown link {t}" for t in md.targets if t.startswith("#")]
    count = 0
    for path, note in vault.notes.items():
        for n, target in note.wikilinks:
            count += 1
            if err := _vault_link_error(vault, target, path):
                fails.append(f"{path} L{n + 1}: [[{target}]] {err}")
    into_removed = sum(1 for h in facts.links if h.startswith("#") and h[1:] in facts.removed_anchors)
    if md.link_count < len(facts.links) - into_removed:
        fails.append(f"md body links {md.link_count} < html links {len(facts.links) - into_removed}")
    blocks = sum(len(n.block_ids) for n in vault.notes.values())
    targets, target_fails = _link_target_fails(facts, _vault_section_links(vault))
    fails += target_fails + _r4b_fails(md)
    return _check("V4", "links", fails, html_links=len(facts.links), into_removed=into_removed,
                  md_links=md.link_count, html_external=sum(external.values()), wikilinks=count, block_ids=blocks,
                  id_links=len(md.id_links), targets=targets)


def _v5_vault(facts: _HtmlFacts, vault: _Vault) -> Check:
    """Every note is linked from the index Contents; Contents follows the body headings; indexes complete."""
    fails: list[str] = []
    if vault.index is None:
        return _check("V5", "index", [f"no {INDEX_NOTE}.md"])
    for i, (m, (level, text)) in enumerate(zip(vault.contents, vault.virtual.headings)):
        alias = m.group(4) if m.group(4) is not None else (m.group(3) or m.group(2))
        if alias != text or len(m.group(1)) // 2 + 2 != level:
            fails.append(f"#{i}: contents {m.group(0).strip()[:60]!r} != heading {text!r} (level {level})")
    for m in vault.contents:
        target = m.group(2) + (f"#{m.group(3)}" if m.group(3) else "")
        if err := _vault_link_error(vault, target, vault.index.path):
            fails.append(f"contents [[{target}]]: {err}")
    if len(vault.contents) != EXPECTED_KEPT_HEADINGS or len(vault.contents) != len(vault.virtual.headings):
        fails.append(f"contents has {len(vault.contents)} entries, notes {len(vault.virtual.headings)} headings")
    overview = _tag_overview(vault.index)                             # Tag overview rows only
    if len(overview) != len(_tag_rows(vault.index.lines)):
        fails.append("Tag overview lists a tag twice")
    counted = Counter(t for p, n in vault.notes.items() if n is not vault.index and p != ATLAS_PATH
                      for t in n.front.get("tags", []))
    if overview != dict(counted):
        fails.append(f"Tag overview {sorted(overview.items())[:4]}… != tags of the notes {sorted(counted.items())[:4]}…")
    unlisted = sorted(set(vault.notes) - set(vault.order) - {vault.index.path, ATLAS_PATH})
    fails += [f"note not in the index contents: {p}" for p in unlisted]
    types, vocabs = _type_vocab(facts, vault.index.lines, fails, tuple(EXPECTED_DERIVED_TYPES))
    fails += _index_target_errors(facts, vault.index.lines, lambda t: _vault_target(vault, t, vault.index.path),
                                  _md_idents(facts, vault.virtual).homes)
    return _check("V5", "index", fails, contents=len(vault.contents), notes=len(vault.order),
                  types=types, vocabs=vocabs)


def _v6_v7_vault(vault: _Vault) -> list[Check]:
    hygiene: list[str] = []
    stripped: list[str] = []
    emphasis_lines = 0
    for path, note in vault.notes.items():
        rows = frozenset(_tag_rows(note.lines)) if note is vault.index else frozenset()
        v6, v7 = _v6_v7(note.text, _md_facts(note.text, "obsidian", body_start=note.front_end), rows)
        hygiene += [f"{path}: {f}" for f in v6.failures]
        stripped += [f"{path}: {f}" for f in v7.failures]
        emphasis_lines += v6.stats["emphasis_lines"]
    return [_check("V6", "hygiene", hygiene, emphasis_lines=emphasis_lines), _check("V7", "stripped", stripped)]


def _expected_notes(facts: _HtmlFacts, split_level: int) -> list[tuple[str, str, int, str | None]]:
    """(path, section, level, parent path) of every note the HTML calls for, in reading order."""
    out: list[tuple[str, str, int, str | None]] = []
    stack: list[tuple[int, str]] = []
    folder = ""
    for key in facts.kept_keys():
        sec = facts.sections[key]
        if sec["level"] > split_level:
            continue
        if sec["level"] == 1:
            folder = sanitise_name(sec["text"])
        while stack and stack[-1][0] >= sec["level"]:
            stack.pop()
        path = f"{folder}/{sanitise_name(sec['text'])}.md"
        out.append((path, key, sec["level"], stack[-1][1] if stack else None))
        stack.append((sec["level"], path))
    return out


def _link_value(path: str | None) -> str:
    return f"[[{(path or INDEX_NOTE).rsplit('/', 1)[-1].removesuffix('.md')}]]"


def _title(text: str) -> str:
    """Heading text without its number, as the parser splits it (HEADING_RES)."""
    return next((m.group(2) for p in HEADING_RES if (m := re.match(p, text))), text)


def _section_url(facts: _HtmlFacts, number: str) -> str | None:
    """source_url: the spec URL plus the anchor inside the section's heading; None unless it has exactly one."""
    anchors = facts.sections[number]["anchors"]
    return f"{SOURCE_URL}#{anchors[0]}" if len(anchors) == 1 else None


def _front_values(facts: _HtmlFacts, number: str, titles: Counter, types: list[str],
                  source_sha256: str | None) -> dict:
    """Properties every note gets: tags and aliases, chapter, stix_types, source."""
    text = facts.sections[number]["text"]
    title, chapter = _title(text), number.split(".")[0]
    tags = [CHAPTER_TAGS.get(chapter, APPENDIX_TAG)] + ([DEPRECATED_TAG] if "deprecated" in text.casefold() else [])
    aliases = [title] if titles[title] == 1 else []                    # a title that repeats (level 3) is no alias
    want = {"chapter": chapter, "tags": tags, "aliases": aliases + [t for t in types if t not in aliases],
            "stix_types": types or None, "source_url": _section_url(facts, number)}
    if source_sha256 is not None:
        want["source_sha256"] = source_sha256
    return want


def _note_types(facts: _HtmlFacts, vault: _Vault, expected: list[tuple[str, str, int, str | None]]) -> dict:
    """Per note path, its STIX type names: the HTML "Type Name:" lines in its sections, then the derived types
    whose home, from the Markdown as V10 finds it, is in the note."""
    roots, note_of, current = {e[1]: e[0] for e in expected}, {}, None
    for key in facts.kept_keys():
        current = roots.get(key, current)
        note_of[key] = current
    types: dict[str, list[str]] = {}
    for name, key in zip(facts.type_names, facts.type_keys):
        types.setdefault(note_of.get(key), []).append(name)
    homes = _md_idents(facts, vault.virtual).homes
    for name in EXPECTED_DERIVED_TYPES:
        if (path := note_of.get(homes.get(name, ""))) is not None and name not in types.get(path, []):
            types.setdefault(path, []).append(name)
    return types


def _nav_errors(nav: str, i: int, expected: list[tuple[str, str, int, str | None]]) -> list[str]:
    """The NAV line links Index › ancestors · ← prev · next →, each aliased by its section number."""
    parent, number = {e[0]: e[3] for e in expected}, {e[0]: e[1] for e in expected}

    def link(path: str | None) -> tuple[str, str]:
        return _link_value(path)[2:-2], "Index" if path is None else number[path]

    chain: list[str | None] = []
    up = expected[i][3]
    while up is not None:
        chain.insert(0, up)
        up = parent[up]
    want = ([link(None), *map(link, chain)], [link(expected[i - 1][0])] if i else [],
            [link(expected[i + 1][0])] if i + 1 < len(expected) else [])
    parts = nav.split(" · ")
    pairs = [[(m.group(1), m.group(2)) for p in sel for m in WIKILINK_RE.finditer(p)]
             for sel in (parts[:1], [p for p in parts[1:] if p.startswith("← ")],
                         [p for p in parts[1:] if p.endswith(" →")])]
    return [] if tuple(pairs) == want else [f"NAV line links {pairs} != {list(want)}"]


def _child_list_errors(note: _Note, i: int, expected: list[tuple[str, str, int, str | None]]) -> list[str]:
    """A note with child notes (a chapter note) ends with their list, in reading order; others have none."""
    path = expected[i][0]
    children = [_link_value(e[0])[2:-2] for e in expected if e[3] == path]
    found = _child_list(_without_footer(note)[0])
    if found is None:
        return [f"no child list {CHILD_LIST_LABEL}"] if children else []
    if found[1] != children:
        return [f"child list {found[1][:4]} != {children[:4]}"]
    return []


def _v9(facts: _HtmlFacts, vault: _Vault, corpus: dict[str, str], root: str,
        source_sha256: str | None = None) -> Check:
    """Vault structure: notes, names, Properties, up/prev/next chain, NAV lines, paths, manifest."""
    fails: list[str] = []
    try:
        manifest = json.loads(corpus.get(MANIFEST_NAME, ""))
        listed = {f["path"]: f for f in manifest["files"]}
        split_level = manifest["split_level"]
    except (ValueError, KeyError, TypeError) as exc:
        return _check("V9", "vault", [f"{MANIFEST_NAME} missing or unreadable: {exc}"])
    if split_level != SPLIT_LEVEL:                      # level 2 only
        return _check("V9", "vault", [f"manifest split_level {split_level!r}"])
    expected = _expected_notes(facts, split_level)
    names = [e[0] for e in expected]
    titles = Counter(_title(facts.sections[e[1]]["text"]) for e in expected)
    types = _note_types(facts, vault, expected)
    object_notes = _object_notes(facts, vault)
    actual = set(vault.notes) - {f"{INDEX_NOTE}.md", ATLAS_PATH}
    fails += [f"missing note {p}" for p in names if p not in vault.notes]
    fails += [f"extra note {p}" for p in sorted(actual - set(names))]
    fails += [f"note name {n!r} used {len(ps)} times" for n, ps in vault.by_name.items() if len(ps) > 1]
    if vault.order != [p for p in names if p in vault.notes]:
        fails.append("index contents order differs from the HTML reading order")
    wide = 0
    for i, (path, number, level, parent) in enumerate(expected):
        note = vault.notes.get(path)
        if note is None:
            continue
        f = note.front
        fails += [f"{path}: {e}" for e in note.errors]
        fails += [f"{path}: Properties lack {k!r}" for k in NOTE_KEYS if k not in f]
        if len(anchors := facts.sections[number]["anchors"]) != 1:
            fails.append(f"heading {number} has {len(anchors)} anchors")
        want = {"title": facts.sections[number]["text"], "section": number,
                "kind": "chapter" if level == 1 else "section", "up": _link_value(parent),
                **_front_values(facts, number, titles, types.get(path, []), source_sha256)}
        want["prev"] = _link_value(names[i - 1]) if i else None
        want["next"] = _link_value(names[i + 1]) if i + 1 < len(names) else None
        fails += [f"{path}: {k} is {f.get(k)!r}, expected {v!r}" for k, v in want.items() if f.get(k) != v]
        if not isinstance(f.get("tags"), list) or not isinstance(f.get("aliases"), list):
            fails.append(f"{path}: tags and aliases must be lists")
        body, nav = _note_body(note)
        if nav is None:
            fails.append(f"{path}: first line after the front matter is not the NAV line")
        else:
            fails += [f"{path}: {e}" for e in _nav_errors(nav, i, expected)]
            tail = note.lines[-4:]                                   # ..., "", footer, "" (final newline)
            if len(tail) < 4 or tail[1:] != ["", nav, ""] or tail[0] == "":
                fails.append(f"{path}: the last line is not a copy of the NAV line after one blank line (F7)")
        fails += [f"{path}: {e}" for e in _child_list_errors(note, i, expected)]
        is_wide = _widest_table(body) >= WIDE_TABLE_MIN_COLUMNS
        wide += is_wide
        want_css = [WIDE_CSS_CLASS] * is_wide + [OBJECT_CSS_CLASS] * (path in object_notes) or None
        if f.get("cssclasses") != want_css:
            fails.append(f"{path}: cssclasses is {f.get('cssclasses')!r}, expected {want_css!r}")
        elif want_css and list(f).index("cssclasses") != list(f).index("aliases") + 1:
            fails.append(f"{path}: cssclasses must follow aliases")
    index = vault.index
    if index is None or index.front.get("kind") != "index" or index.errors:
        fails.append(f"{INDEX_NOTE}.md missing, or its Properties are invalid")
    elif index.front.get("source_url") != SOURCE_URL:                       # not a section
        fails.append(f"{INDEX_NOTE}.md: source_url is {index.front.get('source_url')!r}, expected {SOURCE_URL!r}")
    if (atlas := vault.notes.get(ATLAS_PATH)) is not None:
        want_atlas = {"title": ATLAS_NOTE, "kind": "atlas", "source_url": SOURCE_URL,
                      "source_sha256": source_sha256 or atlas.front.get("source_sha256"), "aliases": ATLAS_ALIASES,
                      "tags": ATLAS_TAGS, "cssclasses": [ATLAS_CSS_CLASS]}           # cssclasses is the last key
        if atlas.errors or list(atlas.front.items()) != list(want_atlas.items()):
            fails.append(f"{ATLAS_PATH}: Properties {atlas.front!r} != {want_atlas!r}")
        if any(ln.startswith(NAV_PREFIX) for ln in atlas.lines):
            fails.append(f"{ATLAS_PATH}: the atlas has no NAV line")
    fails += [f"path longer than {MAX_PATH_CHARS}: {p}" for p in corpus if len(root) + 1 + len(p) > MAX_PATH_CHARS]
    if set(listed) != set(corpus) - {MANIFEST_NAME}:
        fails.append(f"manifest lists {sorted(set(listed) ^ (set(corpus) - {MANIFEST_NAME}))[:5]} wrongly")
    for path, text in corpus.items():
        data = text.encode("utf-8")
        if path in listed and (listed[path].get("sha256") != hashlib.sha256(data).hexdigest()
                               or listed[path].get("bytes") != len(data)):
            fails.append(f"manifest hash or size differs for {path}")
    return _check("V9", "vault", fails, notes=len(actual), expected=len(expected), split_level=split_level,
                  files=len(corpus), wide=wide)


def _v10_vault(facts: _HtmlFacts, vault: _Vault) -> Check:
    """Identifier links in the vault: a relative path to a note of the corpus, plus its heading when needed."""
    idents = _md_idents(facts, vault.virtual)
    homes, fails = idents.homes, list(idents.fails)
    count = 0
    for path, note in vault.notes.items():
        if note is vault.index:
            if ID_LINK_RE.search(note.text):
                fails.append(f"{path}: the index note has identifier links")
            continue

        def resolve(dest: str, note: _Note = note) -> tuple[str | None, str | None]:
            file, sep, frag = dest.partition("#")
            target = posixpath.normpath(posixpath.join(posixpath.dirname(note.path), unquote(file)))
            other = vault.notes.get(target) if file.endswith(".md") else None
            if other is None:
                return None, f"no note {target!r} in the vault"
            if not sep:
                return str(other.front.get("section")), None
            number = other.heading_numbers.get(_obs_norm(unquote(frag)))
            return (number, None) if number else (None, f"missing heading in {target!r}")

        body, _ = _note_body(note)
        found, more = _id_link_scan(body, homes, resolve, f"{path}: ")
        count += found
        fails += more
        if found != len(ID_LINK_RE.findall(note.text)):
            fails.append(f"{path}: identifier links outside the body (Properties, NAV line or child list)")
    return _check("V10", "identifier links", fails, idents=len(homes), id_links=count)


# --- Properties facts and Bases (V11, V12) ----------------------------------------------------------------

BAND_LINE_RE = re.compile(r"^\*\*(.+?):\*\* (.*)$")
FORWARD_HEADER_RE = re.compile(r"^\| \*\*Source\*\* \| \*\*(?:Relationship Type|Name)\*\* \| \*\*Target\*\* \|")
APPENDIX_B_HEADER = "| **Source** | **Type** | **Target** |"
VALUE_HEADER_RE = re.compile(r"^\| \*\*(?:Vocabulary Value|Enumeration Value|Value)\*\* \|")
REVERSE_LINE = "**Reverse Relationships**"
CELL_SPLIT_RE = re.compile(r"(?<!\\) \| ")
LIST_TOKEN_RE = re.compile(r"(?P<code>(?P<t>`+)(?P<text>(?:(?!(?P=t)).)+?)(?P=t)(?!`))|(?P<gap>[*\s]+)"
                           r"|(?P<sep>,|<br>|[()])|(?P<prose>.)")
OBJECT_KEYS = (*(f"{k}_properties" for k in PROPERTY_KEYS), "relationship_types", "relationship_targets",
               "vocabularies")
VOCABULARY_KEYS = ("vocabulary_name", "vocabulary_kind", "vocabulary_values", "used_by")
BASE_PROPERTY_RE = re.compile(r'^file\.hasProperty\("([^"]+)"\)$')
BASE_TAG_RE = re.compile(r'^file\.hasTag\("([^"]+)"\)$')
Triple = tuple[str, str, str]


def _md_list(text: str) -> list[str]:
    """Values named by the code spans of a rendered value list, up to the first prose.

    Code spans joined only by spaces and emphasis marks form one value (`<All` *`STIX Cyber-observable Objects`*`>`);
    commas, <br> and parentheses separate values, and a span's own comma list gives several.
    """
    runs: list[str] = []
    current: str | None = None
    gap = ""
    for m in LIST_TOKEN_RE.finditer(ID_LINK_RE.sub(r"\1\2\1", text)):
        if m.group("code"):
            current = m.group("text") if current is None else current + gap + m.group("text")
            gap = ""
        elif m.group("gap"):
            gap += m.group("gap").replace("*", "")
        elif m.group("prose"):
            break
        elif current is not None:
            runs.append(current)
            current, gap = None, ""
    if current is not None:
        runs.append(current)
    return [value for run in runs for part in run.split(",") if (value := " ".join(part.split()))]


def _table_rows(lines: list[str], header: Callable[[str], bool]) -> list[list[str]]:
    """Cells of the data rows of every Markdown table whose header line matches."""
    rows, inside = [], False
    for ln in lines:
        if header(ln):
            inside = True
        elif not ln.startswith("|"):
            inside = False
        elif inside and not ln.startswith("| ---"):
            rows.append(CELL_SPLIT_RE.split(ln[2:-2]))
    return rows


def _md_relationships(md: _Md) -> tuple[list[Triple], list[str], set[Triple]]:
    """Forward triples, the sections of the forward tables that give any, and the Appendix B triples."""
    triples: list[Triple] = []
    tables: dict[str, None] = {}
    for number, lines in md.sections.items():
        cut = lines.index(REVERSE_LINE) if REVERSE_LINE in lines else len(lines)   # nothing after it is forward
        for cells in _table_rows(lines[:cut], FORWARD_HEADER_RE.match):
            for triple in product(*(_md_list(c) for c in cells[:3])):
                if triple not in triples:
                    triples.append(triple)
                tables[number] = None
    b_rows = _table_rows(md.sections.get(RELATIONSHIP_APPENDIX, []), lambda ln: ln == APPENDIX_B_HEADER)
    appendix_b = {t for cells in b_rows for t in product(*(_md_list(c) for c in cells[:3]))}
    return triples, list(tables), appendix_b


def _note_sections(vault: _Vault) -> dict[str, _Note]:
    """Section number -> the note that contains its heading."""
    return {number: note for note in vault.notes.values() if note is not vault.index
            for number in note.heading_numbers.values()}


def _object_notes(facts: _HtmlFacts, vault: _Vault) -> set[str]:
    """Paths of the notes that are home to exactly one object, from the rendered property tables: the notes
    that get the object Properties keys and the `stix-object` class."""
    idents, sections = _md_idents(facts, vault.virtual), _note_sections(vault)
    owners = Counter(sections[home].path for type_ in idents.tables.values()
                     if (home := idents.homes.get(type_)) in sections)
    return {path for path, n in owners.items() if n == 1}


def _v11(facts: _HtmlFacts, vault: _Vault) -> Check:
    """Properties facts: rebuild each object's and vocabulary's Properties keys from the rendered body."""
    md = vault.virtual
    idents = _md_idents(facts, md)
    homes, fails = idents.homes, list(idents.fails)
    triples, forward_tables, appendix_b = _md_relationships(md)
    expected_counts = {"objects": sum(EXPECTED_OBJECTS.values()), "forward tables": EXPECTED_FORWARD_TABLES,
                       "forward triples": EXPECTED_TRIPLES, "relationship types": EXPECTED_REL_TYPES,
                       "Appendix B triples": EXPECTED_APPENDIX_B_TRIPLES}
    counts = {"objects": len(idents.tables), "forward tables": len(forward_tables), "forward triples": len(triples),
              "relationship types": len({r for _, r, _ in triples}), "Appendix B triples": len(appendix_b)}
    fails += [f"{what}: {counts[what]}, expected {n}" for what, n in expected_counts.items() if counts[what] != n]
    if appendix_b - set(triples) != set(APPENDIX_B_ONLY) or set(triples) - appendix_b != set(FORWARD_ONLY):
        fails.append(f"Appendix B differs from the object tables other than in OF5: "
                     f"{sorted(appendix_b ^ set(triples) ^ set(APPENDIX_B_ONLY) ^ set(FORWARD_ONLY))[:6]}")
    sections = _note_sections(vault)

    def link(section: str) -> str:
        note = sections.get(section)
        return f"[[{note.name}]]" if note is not None else f"[[? {section}]]"

    vocab_names = {n for n, k in homes.items() if idents.kinds.get(n) in ("vocabulary", "enumeration")}
    want: dict[str, dict] = {}                    # note path -> expected Properties keys
    owners: dict[str, list[str]] = {}             # note path -> object types / vocabularies homed there
    used_by: dict[str, list[str]] = {}
    for table_section, type_ in idents.tables.items():
        home = homes[type_]
        bands: dict[str, list[str]] = {}
        for ln in md.sections[table_section]:
            if (m := BAND_LINE_RE.match(ln)) and (m.group(1) in PROPERTY_BANDS
                                                 or m.group(1).endswith(SPECIFIC_BAND_SUFFIX)):
                bands[PROPERTY_BANDS.get(m.group(1), "specific")] = list(dict.fromkeys(_md_list(m.group(2))))
        mentioned: dict[str, None] = {}
        for number, lines in md.sections.items():
            if _in_subtree(number, home):
                mentioned.update((v.strip(), None) for ln in lines for text, _, _ in _code_spans(ln)
                                 for v in text.split(",") if v.strip() in vocab_names)
        for name in mentioned:
            used_by.setdefault(name, []).append(type_)
        rels = [(r, t) for s, r, t in triples if s == type_]
        props = {f"{k}_properties": bands.get(k, []) for k in PROPERTY_KEYS}
        props["relationship_types"] = list(dict.fromkeys(r for r, _ in rels))
        props["relationship_targets"] = list(dict.fromkeys(
            link(ALL_SCO_SECTION if t == ALL_SCO_TARGET else homes.get(t, "?")) for _, t in rels))
        props["vocabularies"] = [link(homes[v]) for v in mentioned]
        note = sections.get(home)
        if note is None:
            fails.append(f"{type_}: no note holds its home section {home}")
            continue
        owners.setdefault(note.path, []).append(type_)
        want[note.path] = {k: v for k, v in props.items() if v}
    vocab_owners: dict[str, list[str]] = {}
    vocab_want: dict[str, dict] = {}
    for name in sorted(vocab_names, key=lambda n: list(md.order).index(homes[n]) if homes[n] in md.order else 0):
        values: list[str] = []
        for cells in _table_rows(md.sections.get(homes[name], []), VALUE_HEADER_RE.match):
            found = _md_list(cells[0])
            if len(found) != 1:
                fails.append(f"{name}: value table row {cells[0][:40]!r} is not one value")
            values += found
        note = sections.get(homes[name])
        if note is None:
            continue
        vocab_owners.setdefault(note.path, []).append(name)
        props = {"vocabulary_name": name, "vocabulary_kind": idents.kinds[name], "vocabulary_values": values,
                 "used_by": list(dict.fromkeys(link(homes[t]) for t in used_by.get(name, [])))}
        vocab_want[note.path] = {k: v for k, v in props.items() if v}
    objects = vocabs = 0
    for path, note in vault.notes.items():
        if note is vault.index:
            continue
        expected = want[path] if len(owners.get(path, [])) == 1 else {}            # exactly one object
        expected |= vocab_want[path] if len(vocab_owners.get(path, [])) == 1 else {}
        objects += bool(expected) and len(owners.get(path, [])) == 1
        vocabs += bool(expected) and len(vocab_owners.get(path, [])) == 1
        actual = {k: v for k, v in note.front.items() if k in OBJECT_KEYS or k in VOCABULARY_KEYS}
        for key in sorted(set(actual) | set(expected)):
            if actual.get(key) != expected.get(key):
                fails.append(f"{path}: {key} is {actual.get(key)!r}, expected {expected.get(key)!r}")
        keys = list(note.front)
        if actual and "up" in keys and any(keys.index(k) > keys.index("up") for k in actual):
            fails.append(f"{path}: the Properties facts must come before 'up'")
        for target in note.front.get("used_by", []):
            if err := _vault_link_error(vault, target.strip("[]"), path):
                fails.append(f"{path}: used_by {target}: {err}")
    return _check("V11", "Properties facts", fails, objects=objects, forward_tables=len(forward_tables),
                  triples=len(triples), rel_types=counts["relationship types"], vocabularies=vocabs)


def _yaml_indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _yaml_scalar(text: str) -> object:
    """A JSON scalar (the to_yaml subset: strings, numbers, true/false/null), else a plain string."""
    try:
        return json.loads(text)
    except ValueError:
        return text


def _yaml_block(lines: list[str], i: int, indent: int) -> tuple[object, int]:
    """Parse block YAML as meta.to_yaml and the Bases writer emit it: 2-space dicts, '- ' lists, JSON or plain scalars."""
    if lines[i][indent:].startswith("- "):
        items: list = []
        while i < len(lines) and _yaml_indent(lines[i]) == indent and lines[i][indent:].startswith("- "):
            rest = lines[i][indent + 2:]
            if re.match(r"^[a-z_]+:( |$)", rest):           # a mapping item: its first key is on the dash line
                lines = [*lines[:i], " " * (indent + 2) + rest, *lines[i + 1:]]
                item, i = _yaml_block(lines, i, indent + 2)
            else:
                item, i = _yaml_scalar(rest), i + 1
            items.append(item)
        return items, i
    mapping: dict = {}
    while i < len(lines) and _yaml_indent(lines[i]) == indent and not lines[i][indent:].startswith("- "):
        key, sep, rest = lines[i][indent:].partition(":")
        if not sep or not re.fullmatch(r"[a-z_]+", key):
            raise ValueError(f"line {i + 1}: not 'key: value'")
        if rest.strip():
            mapping[key], i = _yaml_scalar(rest.strip()), i + 1
        elif i + 1 < len(lines) and _yaml_indent(lines[i + 1]) > indent:
            mapping[key], i = _yaml_block(lines, i + 1, _yaml_indent(lines[i + 1]))
        else:
            raise ValueError(f"line {i + 1}: {key!r} has no value")
    return mapping, i


def _read_base(text: str) -> object:
    lines = text.rstrip("\n").split("\n")
    value, end = _yaml_block(lines, 0, 0)
    if end != len(lines):
        raise ValueError(f"line {end + 1}: unexpected indentation")
    return value


def _strings(value: object) -> list[str]:
    if isinstance(value, dict):
        return [s for v in value.values() for s in _strings(v)]
    if isinstance(value, list):
        return [s for v in value for s in _strings(v)]
    return [value] if isinstance(value, str) else []


def _base_view(name: str, columns: list[str], **filters: list[str]) -> dict:
    return {"type": "table", "name": name, **({"filters": filters} if filters else {}),
            "order": ["file.name", *columns]}


_OBJECT_COLUMNS = ["stix_types", "required_properties", "specific_properties"]
BASES_STRUCTURE = {        # bare column ids, as Obsidian saves them; written here, not imported
    OBJECTS_BASE + BASE_SUFFIX: {"filters": {"and": ['file.hasProperty("specific_properties")']}, "views": [
        _base_view("All objects", _OBJECT_COLUMNS),
        _base_view("SDOs", _OBJECT_COLUMNS, **{"and": ['file.hasTag("stix/sdo")']}),
        _base_view("SCOs", [*_OBJECT_COLUMNS, "id_contributing_properties"], **{"and": ['file.hasTag("stix/sco")']}),
        _base_view("SROs and SMOs", _OBJECT_COLUMNS, **{"or": ['file.hasTag("stix/sro")', 'file.hasTag("stix/smo")']})]},
    RELATIONSHIPS_BASE + BASE_SUFFIX: {"filters": {"and": ['file.hasProperty("relationship_types")']}, "views": [
        _base_view("Relationships", ["stix_types", "relationship_types", "relationship_targets"])]},
    VOCABULARIES_BASE + BASE_SUFFIX: {"filters": {"and": ['file.hasProperty("vocabulary_name")']}, "views": [
        _base_view("Vocabularies", ["vocabulary_name", "vocabulary_kind", "used_by", "vocabulary_values"])]},
}


def _v12(vault: _Vault, corpus: dict[str, str], object_notes: int) -> Check:
    """Bases: the three files exist when object notes do, are in the manifest, parse, and use only known
    Properties (file.hasProperty, columns `<key>` or `note.<key>`) and tags of the Tag overview (file.hasTag)."""
    names = [n + BASE_SUFFIX for n in (OBJECTS_BASE, RELATIONSHIPS_BASE, VOCABULARIES_BASE)]
    present = [p for p in corpus if p.endswith(BASE_SUFFIX)]
    fails: list[str] = []
    if sorted(present) != (sorted(names) if object_notes else []):
        fails.append(f"Bases files {sorted(present)}, expected {sorted(names) if object_notes else []}")
    try:
        listed = {f["path"] for f in json.loads(corpus.get(MANIFEST_NAME, "{}")).get("files", [])}
    except (ValueError, AttributeError):
        listed = set()
    keys = {k for note in vault.notes.values() for k in note.front}
    tags = set(_tag_overview(vault.index))
    for path in present:
        if path not in listed:
            fails.append(f"{path} is not in the manifest")
        try:
            base = _read_base(corpus[path])
        except (ValueError, IndexError) as exc:
            fails.append(f"{path} does not parse: {exc}")
            continue
        if path in BASES_STRUCTURE and base != BASES_STRUCTURE[path]:
            fails.append(f"{path}: differs from the OBSIDIAN-1.1 §5.2 structure")
        views = base.get("views") if isinstance(base, dict) else None
        if not isinstance(views, list) or not views or not all(
                isinstance(v, dict) and v.get("type") == "table" and v.get("name") and v.get("order") for v in views):
            fails.append(f"{path}: needs a non-empty list of table views with name and order")
            continue
        for expr in _strings(base.get("filters")) + _strings([v.get("filters") for v in views]):
            if m := BASE_PROPERTY_RE.match(expr):
                fails += [f"{path}: filter on unknown property {m.group(1)!r}"] if m.group(1) not in keys else []
            elif m := BASE_TAG_RE.match(expr):
                fails += [f"{path}: filter on tag {m.group(1)!r}, not in the Tag overview"] if m.group(1) not in tags \
                    else []
            else:
                fails.append(f"{path}: filter {expr!r} uses more than hasProperty and hasTag")
        for column in _strings([v.get("order") for v in views]):
            if column != "file.name" and column.removeprefix("note.") not in keys:   # bare ids, as Obsidian saves them
                fails.append(f"{path}: column {column!r} is no note Property")
    return _check("V12", "Bases", fails, bases=len(present))


# --- how-to examples (V17) ----------------------------------------------------------------------------

# --- Relationship diagrams and the atlas note (V13) --------------------------------------------------------

MERMAID_NODE_RE = re.compile(r'^  (n\d+)\["([^"]+)"\]$')
MERMAID_EDGE_RE = re.compile(r'^  (n\d+) -->\|"([^"]+)"\| (n\d+)$')
MERMAID_HEAD = "flowchart LR"
MERMAID_SELF = ("  classDef self stroke-width:3px", "  class n0 self")


def _parse_diagram(content: list[str]) -> tuple[list[str], list[tuple[str, str, str]], list[str]]:
    """The fixed diagram shape: (node labels in id order, edges as (source label, types, target label), errors).

    `flowchart LR`, nodes n0…nk in order, edges, `classDef self`, `class n0 self`, one `class` line for n1…nk.
    """
    errors = [] if content[:1] == [MERMAID_HEAD] else [f"first line is not {MERMAID_HEAD!r}"]
    labels: dict[str, str] = {}
    edges: list[tuple[str, str, str]] = []
    i = 1
    while i < len(content) and (m := MERMAID_NODE_RE.match(content[i])):
        if m.group(1) != f"n{len(labels)}":
            errors.append(f"node id {m.group(1)} out of order")
        labels[m.group(1)] = m.group(2)
        i += 1
    while i < len(content) and (m := MERMAID_EDGE_RE.match(content[i])):
        if m.group(1) in labels and m.group(3) in labels:
            edges.append((labels[m.group(1)], m.group(2), labels[m.group(3)]))
        else:
            errors.append(f"edge {content[i].strip()!r} names an unknown node")
        i += 1
    tail = [*MERMAID_SELF]
    if len(labels) > 1:
        tail.append(f"  class {','.join(f'n{k}' for k in range(1, len(labels)))} internal-link")
    if content[i:] != tail:
        errors.append(f"unexpected lines {content[i:i + 2]!r}, expected {tail[:2]!r}")
    if len(set(labels.values())) != len(labels):
        errors.append("node labels repeat")
    return list(labels.values()), edges, errors


def _v13(facts: _HtmlFacts, vault: _Vault) -> Check:
    """Diagrams: one diagram block per object with edges, as the last block of its note, whose nodes and edges are the
    rendered relationship tables'; the atlas embeds every diagram once."""
    md = vault.virtual
    homes = _md_idents(facts, md).homes
    triples, _, _ = _md_relationships(md)
    sections = _note_sections(vault)
    rank = {number: i for i, number in enumerate(md.order)}
    fails: list[str] = []

    def home(name: str) -> str:
        return ALL_SCO_SECTION if name == ALL_SCO_TARGET else homes.get(name, "?")

    def label(name: str) -> str:
        note = sections.get(home(name))
        return note.name if note is not None else f"? {name}"

    pairs: dict[tuple[str, str], list[str]] = {}
    for source, rel, target in triples:
        pairs.setdefault((source, target), []).append(rel)
    objects = sorted({x for pair in pairs for x in pair} - {ALL_SCO_TARGET}, key=lambda x: rank.get(home(x), -1))
    want: dict[str, tuple[str, list[str], list[tuple[str, str, str]]]] = {}     # type -> (note path, nodes, edges)
    for obj in objects:
        out = [p for p in pairs if p[0] == obj]
        into = [p for p in pairs if p[1] == obj and p[0] != obj]
        neighbours = sorted({x for p in out + into for x in p} - {obj}, key=lambda x: rank.get(home(x), -1))
        note = sections.get(home(obj))
        if note is None:
            fails.append(f"{obj}: no note holds its home section {home(obj)}")
            continue
        want[obj] = (note.path, [label(obj), *map(label, neighbours)],
                     [(label(s), ", ".join(pairs[(s, t)]), label(t)) for s, t in out + into])
    seen: set[str] = set()
    edges = 0
    for path, note in vault.notes.items():
        if note is vault.index:
            continue
        lines = note.lines
        for start, end in _md_facts(note.text, "obsidian", body_start=note.front_end).diagrams:
            obj = DIAGRAM_ID_RE.match(lines[end]).group(1)
            where = f"{path} L{start + 1}"
            if obj not in want or want[obj][0] != path or obj in seen:
                fails.append(f"{where}: diagram ^{DIAGRAM_BLOCK_PREFIX}{obj} does not belong here")
                continue
            seen.add(obj)
            _, nav = _note_body(note)
            if lines[start - 1] != "" or lines[start - 2] == "" or lines[end + 1:] != ["", nav, ""]:
                fails.append(f"{where}: the diagram is not the last block before the footer (OD16)")
            nodes, got, errors = _parse_diagram(lines[start + 3:end - 1])
            fails += [f"{where}: {e}" for e in errors]
            if nodes != want[obj][1]:
                fails.append(f"{where}: nodes {nodes[:4]}… != {want[obj][1][:4]}…")
            if got != want[obj][2]:
                diff = next((a, b) for a, b in zip([*got, None], [*want[obj][2], None]) if a != b)
                fails.append(f"{where}: edge {diff[0]} != {diff[1]}")
            edges += len(got)
    fails += [f"{obj}: no diagram in {want[obj][0]}" for obj in want if obj not in seen]
    atlas = vault.notes.get(ATLAS_PATH)
    if not want:
        fails += [f"{ATLAS_PATH} without diagrams"] if atlas is not None else []
    elif atlas is None:
        fails.append(f"no {ATLAS_PATH}")
    else:
        blocks: list[str] = []
        for chapter, group in ATLAS_GROUPS.items():
            members = [obj for obj in want if home(obj).split(".")[0] == chapter]
            blocks += [f"## {group}"] if members else []
            for obj in members:
                name = sections[home(obj)].name
                blocks += [f"**[[{name}|{obj}]]**", f"![[{name}#^{DIAGRAM_BLOCK_PREFIX}{obj}]]"]
        fails += [f"{obj}: no atlas group for chapter {home(obj).split('.')[0]}" for obj in want
                  if home(obj).split(".")[0] not in ATLAS_GROUPS]
        expected = [ln for block in blocks for ln in (block, "")]
        body = atlas.lines[atlas.front_end:]
        start = next((i for i, ln in enumerate(body) if ln.startswith("## ")), len(body))
        if body[start:] != expected:
            diff = next((a, b) for a, b in zip([*body[start:], None], [*expected, None]) if a != b)
            fails.append(f"{ATLAS_PATH}: line {diff[0]!r} != {diff[1]!r}")
        for n, target in atlas.wikilinks:
            if err := _vault_link_error(vault, target, ATLAS_PATH):
                fails.append(f"{ATLAS_PATH} L{n + 1}: [[{target}]] {err}")
    return _check("V13", "diagrams", fails, diagrams=len(seen), edges=edges)


HOW_TO_HEADINGS = ("### How to use this vault", "### How to use this note")
SEARCH_PROPERTY_RE = re.compile(r"\[([a-z_]+):([^\]]+)\]")           # [key:value]
SEARCH_TAG_RE = re.compile(r"tag:#([\w/-]+)")
SEARCH_PATH_RE = re.compile(r'path:(?:"([^"]+)"|(\S+))')
LINK_CALL_RE = re.compile(r'link\("([^"]+)"\)')
SECTION_ONLY_RE = re.compile(r"(?:\d+|[A-Z])(?:\.\d+)*")
PROPERTY_EXAMPLE_RE = re.compile(r"([a-z][a-z_]*): (\S+)")      # `cssclasses: stix-wide`
SECTION_TITLE_RE = re.compile(r"(?:\d+|[A-Z])(?:\.\d+)* \S.*|#{2,6} (.+)")


@dataclass
class _HowToScope:
    """What the how-to examples of one output may name."""
    resolve: Callable[[str], str | None]          # wikilink target -> error or None
    fronts: list[dict]                            # Properties of every note
    tags: set[str]
    notes: set[str]                               # note names (link("…"), path:)
    sections: set[str]                            # section numbers that are notes (vault) or headings (single)
    headings: set[str]                            # normalised heading texts


def _how_to_lines(lines: list[str]) -> list[str] | None:
    start = next((i for i, ln in enumerate(lines) if ln in HOW_TO_HEADINGS), None)
    if start is None:
        return None
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("### ")), len(lines))
    return lines[start + 1:end]


def _property_has(value: object, wanted: str) -> bool:
    """Obsidian's [key:value] search: a case-insensitive substring of the value or of a list item."""
    values = value if isinstance(value, list) else [value]
    return any(wanted.casefold() in str(v).casefold() for v in values)


def _example_errors(span: str, scope: _HowToScope) -> tuple[int, list[str]]:
    """(examples checked, errors) for one code span of the how-to list."""
    count, errors = 0, []
    for m in WIKILINK_RE.finditer(span):
        count += 1
        errors += [f"{m.group(0)}: {err}"] if (err := scope.resolve(m.group(1))) else []
    for m in SEARCH_PROPERTY_RE.finditer(span):
        count += 1
        if not any(m.group(1) in f and _property_has(f[m.group(1)], m.group(2)) for f in scope.fronts):
            errors.append(f"{m.group(0)} matches no note")
    for m in SEARCH_TAG_RE.finditer(span):
        count += 1
        if not any(t == m.group(1) or t.startswith(m.group(1) + "/") for t in scope.tags):
            errors.append(f"tag:#{m.group(1)} is no tag of this output")
    for m in [*LINK_CALL_RE.finditer(span), *SEARCH_PATH_RE.finditer(span)]:
        count += 1
        name = next(g for g in m.groups() if g is not None)
        errors += [f"{m.group(0)} names no note"] if name not in scope.notes else []
    if m := PROPERTY_EXAMPLE_RE.fullmatch(span):            # a Property value that some note has
        count += 1
        if not any(m.group(2) in (f[m.group(1)] if isinstance(f[m.group(1)], list) else [f[m.group(1)]])
                   for f in scope.fronts if m.group(1) in f):
            errors.append(f"Property {span!r} is on no note")
    if SECTION_ONLY_RE.fullmatch(span):
        count += 1
        errors += [f"section {span!r} is no note here"] if span not in scope.sections else []
    elif "[[" not in span and (m := SECTION_TITLE_RE.fullmatch(span)):
        count += 1
        if _obs_norm(m.group(1) or span) not in scope.headings:
            errors.append(f"heading {span!r} does not exist")
    return count, errors


def _v17(lines: list[str], scope: _HowToScope) -> Check:
    """How-to examples: every example in a code span of the how-to list names something real,
    and no line explains the tool instead of the notes."""
    how_to = _how_to_lines(lines)
    if how_to is None:
        return _check("V17", "how-to examples", ["no how-to list"], examples=0)
    count, fails = 0, []
    for ln in how_to:
        fails += [f"tool usage {term!r} in the how-to (XD9): {ln[:50]!r}" for term in HOW_TO_TOOL_TERMS
                  if term in ln.casefold()]
        for m in CODE_SPAN_RE.finditer(ln):
            span = m.group(0)[len(m.group(1)):-len(m.group(1))]
            n, errors = _example_errors(span.strip(" "), scope)
            count += n
            fails += errors
    return _check("V17", "how-to examples", fails, examples=count)


def _v17_vault(vault: _Vault) -> Check:
    if vault.index is None:
        return _check("V17", "how-to examples", ["no index note"], examples=0)
    current = f"{INDEX_NOTE}.md"
    notes = [n for n in vault.notes.values() if n is not vault.index]
    tags = set(_tag_overview(vault.index))
    scope = _HowToScope(lambda t: _vault_link_error(vault, t, current), [n.front for n in notes], tags,
                        {n.name for n in vault.notes.values()}, {str(n.front.get("section")) for n in notes},
                        {h for n in notes for h in n.heading_norms})
    check = _v17(vault.index.lines, scope)
    atlas = vault.notes.get(ATLAS_PATH)            # the atlas text is vault text too
    fails = [f"{ATLAS_PATH}: tool usage {term!r} (XD9): {ln[:50]!r}" for ln in (atlas.lines[atlas.front_end:]
             if atlas else []) for term in HOW_TO_TOOL_TERMS if term in ln.casefold()]
    if not fails:
        return check
    return _check("V17", "how-to examples", check.failures + fails, examples=check.stats["examples"])


def validate_vault(html_text: str, corpus: dict[str, str], root: str) -> Report:
    """Validate a vault Corpus: V1-V8 on the notes in index order, plus V9, and V10-V12."""
    facts = _HtmlFacts()
    facts.feed(html_text)
    facts.close()
    vault = _read_vault(corpus)
    md = vault.virtual
    index_text = vault.index.text if vault.index else ""
    index_md = _md_facts(index_text, "obsidian", body_start=len(index_text.split("\n")))
    v11 = _v11(facts, vault)
    try:                                         # the decode is strict cp1252, so this gives the source bytes back
        source_sha256: str | None = hashlib.sha256(html_text.encode("cp1252")).hexdigest()
    except UnicodeEncodeError:
        source_sha256 = None
    checks = [_v1(facts, md), _v2(facts, md), _v2c(facts, index_md), _v3(facts, md), _v4_vault(facts, vault),
              _v5_vault(facts, vault), *_v6_v7_vault(vault),
              _v8(facts, md, tuple(vault.index.lines) if vault.index else ()),
              _v9(facts, vault, corpus, root, source_sha256),
              _v10_vault(facts, vault), v11, _v12(vault, corpus, v11.stats["objects"]), _v13(facts, vault),
              _v16(facts, md),
              _v17_vault(vault)]
    return Report(all(c.passed for c in checks), checks)
