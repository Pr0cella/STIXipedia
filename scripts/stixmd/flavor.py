"""How things are written: escaping, links, anchors and headings per output flavour."""
import logging
import posixpath
import re
from abc import ABC, abstractmethod
from dataclasses import replace

from .anchors import AnchorMap
from .config import EXIT_PARSE, ID_LINK_SKIP, OBS_HEADING_UNSAFE_RE, StixMdError, md_url
from .facts import SpecFacts, in_subtree
from .inline import InlineWriter, escape_text, render_spans
from .layout import Layout, NoteId, SingleLayout
from .model import Heading, Span, Target

log = logging.getLogger("stixmd")
BS = chr(92)                   # backslash
GFM_ID_PREFIX = {"section": "sec-", "ref": "ref-", "block": "p-"}
OBS_HEADING_UNSAFE = re.compile(OBS_HEADING_UNSAFE_RE)
_OBS_TAG_RE = re.compile(r"(?<!\S)#")      # any word-initial '#': callers may append text after it
_OBS_CARET_RE = re.compile(r"(?<!\S)\^(?=[A-Za-z0-9-])")
_OBS_ALIAS_DROP_RE = re.compile(r"[\[\]|]")


def _no_link(href: str, inner: str, plain: str) -> str:
    return inner


class Flavor(ABC):
    """Base class: resolves links through the AnchorMap and places them through the Layout."""
    name: str
    line_ranges: bool          # whether the index carries Lstart-Lend ranges
    merge_emphasis = False     # GFM keeps the SAP adjacent-emphasis artefact, Obsidian merges
    merge_code_blocks = False  # GFM keeps the split JSON examples, Obsidian joins them

    def __init__(self, amap: AnchorMap, layout: Layout) -> None:
        self.amap = amap
        self.layout = layout

    @abstractmethod
    def escape(self, text: str) -> str:
        """Escape non-code text."""

    @abstractmethod
    def link(self, target: Target, inner: str, plain: str, note: NoteId) -> str:
        """Write a resolved link; inner is the rendered link text, plain its plain text."""

    @abstractmethod
    def anchor_lines(self, targets: list[Target]) -> list[str]:
        """Lines emitted before a block that carries these targets."""

    @abstractmethod
    def anchor_suffix(self, targets: list[Target]) -> str:
        """Text appended to the line of a paragraph or list item that carries these targets."""

    def writer(self, note: NoteId, section: str = "") -> InlineWriter:
        """Inline writer for text of `section` inside `note`: unresolved links stay plain text."""
        def link(href: str, inner: str, plain: str) -> str:
            target = self.amap.resolve(href, plain)
            return inner if target is None else self.link(target, inner, plain, note)
        return InlineWriter(self.escape, link, self.merge_emphasis)

    def heading_line(self, heading: Heading, level: int) -> str:
        """A Markdown heading line at the given level with the escaped heading text."""
        return "#" * level + " " + render_spans([Span(text=heading.text)], InlineWriter(self.escape, _no_link))


class GfmFlavor(Flavor):
    """SAP format: <a id> anchor lines and [text](#id) links; byte-identical to the SAP reference output."""
    name = "gfm"
    line_ranges = True

    def escape(self, text: str) -> str:
        return escape_text(text)

    def link(self, target: Target, inner: str, plain: str, note: NoteId) -> str:
        dest = target.key if target.kind == "url" else "#" + gfm_id(target)
        return f"[{inner}]({md_url(dest)})"

    def anchor_lines(self, targets: list[Target]) -> list[str]:
        return [f'<a id="{gfm_id(t)}"></a>' for t in targets]

    def anchor_suffix(self, targets: list[Target]) -> str:
        return ""


def gfm_id(target: Target) -> str:
    """The SAP-format anchor id of a non-URL Target: sec-4.1, ref-x.509, p-<slug>."""
    return GFM_ID_PREFIX[target.kind] + target.key


def obsidian_escape(text: str) -> str:
    """SAP escaping plus the Obsidian hazards (non-code text only)."""
    text = escape_text(text, all_brackets=True)
    text = text.replace("$", BS + "$")                          # math
    text = re.sub(r"=(?==)", lambda m: "=" + BS, text)          # "==" -> "=\=" (highlight)
    text = re.sub(r"%(?=%)", lambda m: "%" + BS, text)          # "%%" -> "%\%" (comment)
    text = _OBS_TAG_RE.sub(lambda m: BS + "#", text)            # "#tag" -> "\#tag"
    return _OBS_CARET_RE.sub(lambda m: BS + "^", text)          # " ^id" -> " \^id" (block id)


def heading_target(text: str) -> str:
    """Heading-link target for heading text: characters unsafe in heading links become spaces, spaces collapse."""
    return " ".join(OBS_HEADING_UNSAFE.sub(" ", text).split())


def obsidian_block_id(target: Target) -> str:
    """Block id of a ref or block Target: [a-z0-9-] only, so x.509 -> ref-x-509."""
    return GFM_ID_PREFIX[target.kind] + target.key.replace(".", "-")


def wikilink(dest: str, plain: str) -> str:
    """[[dest|alias]] with the bracket rule: a [label] alias is written as \\[[[dest|label]]\\]."""
    alias, pre, post = plain.strip(), "", ""
    if len(alias) >= 2 and alias.startswith("[") and alias.endswith("]"):
        alias, pre, post = alias[1:-1], BS + "[", BS + "]"
    alias = " ".join(_OBS_ALIAS_DROP_RE.sub("", alias).split())
    return f"{pre}[[{dest}|{alias}]]{post}" if alias else f"{pre}[[{dest}]]{post}"


class ObsidianFlavor(Flavor):
    """Wikilinks to headings and block ids, no <a id> lines, Obsidian escaping."""
    name = "obsidian"
    line_ranges = False
    merge_emphasis = True
    merge_code_blocks = True

    def __init__(self, amap: AnchorMap, layout: Layout, facts: SpecFacts | None = None) -> None:
        super().__init__(amap, layout)
        self.facts = facts                  # identifier links only when given
        owners: dict[str, Target] = {}
        targets = [*amap.refs.values(), *(Target(kind="block", key=k) for k in amap.anchored)]
        for target in targets:
            bid = obsidian_block_id(target)
            if owners.setdefault(bid, target) != target:
                raise StixMdError(EXIT_PARSE, f"Obsidian block id collision: ^{bid} ({owners[bid]} vs {target})")

    def escape(self, text: str) -> str:
        return obsidian_escape(text)

    def _basename(self, note: NoteId) -> str:
        return note.rsplit("/", 1)[-1]

    def section_dest(self, number: str, note: NoteId) -> str:
        """Link destination of a section seen from `note`."""
        home = self.layout.note_of(number)
        if self.layout.is_note_root(number):
            return self._basename(home)
        prefix = "" if home == note else self._basename(home)
        return f"{prefix}#{heading_target(self.layout.heading_of(number))}"

    def dest(self, target: Target, note: NoteId) -> str:
        """Link destination of a section, ref or block Target seen from `note`."""
        if target.kind == "section":
            return self.section_dest(target.key, note)
        section = self.amap.section_of(target)
        if target.kind == "block" and not self.amap.is_line_block(target.key):
            log.info("block anchor %s is not on a paragraph or list item; linking to section %s",
                     target.key, section)
            return self.section_dest(section, note)
        home = self.layout.note_of(section)
        prefix = "" if home == note else self._basename(home)
        return f"{prefix}#^{obsidian_block_id(target)}"

    def link(self, target: Target, inner: str, plain: str, note: NoteId) -> str:
        if target.kind == "url":
            return f"[{inner}]({md_url(target.key)})"
        return wikilink(self.dest(target, note), plain)

    def writer(self, note: NoteId, section: str = "") -> InlineWriter:
        base = super().writer(note, section)
        if self.facts is None:
            return base
        return replace(base, code_link=lambda name: self.code_dest(name, note, section))

    def code_dest(self, name: str, note: NoteId, section: str) -> str | None:
        """Markdown link destination for an identifier code span in `section` of `note`, or None.

        Vault: the relative path to the home note, plus the heading when the home isn't the note root
        (`../7%20STIX%20Meta%20Objects/7.2%20Data%20Markings.md#7.2.1%20Marking%20Definition`); single note: the
        heading only (`#2.12%20List`). No link inside the identifier's home subtree or for ID_LINK_SKIP.
        """
        assert self.facts is not None
        ident = self.facts.idents.get(name)
        if ident is None or in_subtree(section, ident.section) or (section, name) in ID_LINK_SKIP:
            return None
        home = ident.section
        fragment = "" if self.layout.is_note_root(home) else "#" + md_url(heading_target(self.layout.heading_of(home)))
        if isinstance(self.layout, SingleLayout):
            return fragment
        path = posixpath.relpath(self.layout.note_of(home) + ".md", posixpath.dirname(note) or ".")
        return md_url(path) + fragment

    def anchor_lines(self, targets: list[Target]) -> list[str]:
        return []

    def anchor_suffix(self, targets: list[Target]) -> str:
        ids = [obsidian_block_id(t) for t in targets if t.kind in ("ref", "block")]
        if len(ids) > 1:
            raise StixMdError(EXIT_PARSE, f"one block carries several Obsidian block ids: {ids}")
        return f" ^{ids[0]}" if ids else ""
