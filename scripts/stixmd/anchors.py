"""Anchor map and internal link resolution; returns Target."""
import logging
import re
from collections.abc import Iterator

from .config import EXIT_PARSE, REF_LABEL_RE, REFERENCES_APPENDIX, SECTION_NUMBER_RE, StixMdError
from .inline import link_groups, plain_text
from .model import Block, Heading, ListItem, Paragraph, Table, Target

log = logging.getLogger("stixmd")
RULES = ("R1", "R2", "R3", "R4", "R4b", "R5")


def slug(s: str) -> str:
    """Lowercase, replace runs outside [a-z0-9.] with '-', trim '-'."""
    return re.sub(r"[^a-z0-9.]+", "-", s.lower()).strip("-")


def walk(blocks: list[Block]) -> Iterator[Block]:
    """Yield blocks in document order, descending into table cells."""
    for block in blocks:
        yield block
        if isinstance(block, Table):
            for row in block.rows:
                for cell in row:
                    yield from walk(cell.blocks)


def _external(href: str | None) -> str | None:
    href = (href or "").strip()
    return href if href and not href.startswith("#") else None


def _first_external(block: Block) -> str | None:
    """First external href of a span whose text is not blank (Word leaves invisible links)."""
    for span in getattr(block, "spans", []):
        if (href := _external(span.href)) is not None and plain_text([span]):
            return href
    return None


class AnchorMap:
    """Maps Word bookmark names to Targets and resolves hrefs."""

    def __init__(self) -> None:
        self.names: dict[str, Target] = {}
        self.sections: dict[str, Heading] = {}
        self.refs: dict[str, Target] = {}           # casefolded label -> ref Target
        self.ref_blocks: dict[int, Target] = {}     # id(label paragraph) -> ref Target
        self.anchored: dict[str, Block] = {}        # block slug -> first block carrying it
        self.block_section: dict[str, str] = {}     # block slug -> number of the enclosing section
        self.ref_section: dict[str, str] = {}       # ref slug -> number of the enclosing section (E.1/E.2)
        self.top_level: set[int] = set()            # id() of kept top-level blocks (not inside tables)
        self.removed_names: dict[str, Block] = {}
        self.removed: list[Block] = []
        self.used: set[Target] = set()              # block Targets that some link resolves to
        self.warned: set[tuple[str, str]] = set()
        self.link_pos: dict[str, list[tuple[int, int]]] = {}   # href -> source (line, col0), diagnostics
        self._section = ""                                      # section being pre-scanned, diagnostics
        self._stats: dict[str, int] = dict.fromkeys(RULES, 0)

    @classmethod
    def build(cls, kept: list[Block], removed: list[Block],
              link_pos: dict[str, list[tuple[int, int]]] | None = None) -> "AnchorMap":
        """Build the anchor and link table from kept and removed blocks (link_pos: Document.link_pos)."""
        amap = cls()
        amap.link_pos = link_pos or {}
        amap._map_kept(kept)
        for block in walk(removed):
            for name in block.anchors:
                amap.removed_names.setdefault(name, block)
        amap.removed = list(walk(removed))
        for block in walk(kept):                # pre-scan: learn which block anchors links use
            if isinstance(block, Heading):
                amap._section = block.number
            elif isinstance(block, (Paragraph, ListItem)):
                for href, text in link_groups(block.spans):
                    amap.resolve(href, text)
        amap._section = ""
        amap._stats = dict.fromkeys(RULES, 0)
        return amap

    def _where(self, href: str) -> str:
        """Source position(s) and section of a link, without repeating the link itself."""
        pos = self.link_pos.get(href, [])
        at = ", ".join(f"line {line} col {col + 1}" for line, col in pos[:3]) + (" …" if len(pos) > 3 else "")
        parts = [f"at source HTML {at}" if at else "at an unknown source position"]
        if self._section:
            parts.append(f"in section {self._section}")
        return " ".join(parts)

    def _map_kept(self, kept: list[Block]) -> None:
        in_refs = False
        pending: list[str] = []
        labels: dict[str, str] = {}
        section = ""
        self.top_level = {id(b) for b in kept}
        for block in walk(kept):
            if isinstance(block, Heading):
                if block.level == 1:
                    in_refs = block.number == REFERENCES_APPENDIX
                section = block.number
                self.sections[block.number] = block
                for name in block.anchors:
                    self.names[name] = Target(kind="section", key=block.number)
                continue
            label = re.match(REF_LABEL_RE, plain_text(block.spans))                 if in_refs and isinstance(block, Paragraph) else None
            if label:
                rid = slug(label.group(1))
                if rid in labels.values() and labels.get(label.group(1)) != rid:
                    raise StixMdError(EXIT_PARSE, f"reference id collision: ref-{rid}")
                labels[label.group(1)] = rid
                target = Target(kind="ref", key=rid)
                for name in [*pending, *block.anchors]:
                    self.names[name] = target
                pending = []
                self.refs[label.group(1).casefold()] = target
                self.ref_blocks[id(block)] = target
                self.ref_section[rid] = section
            elif in_refs:
                pending.extend(block.anchors)
            else:
                for name in block.anchors:
                    key = slug(name)
                    self.names[name] = Target(kind="block", key=key)
                    self.anchored.setdefault(key, block)
                    self.block_section.setdefault(key, section)

    def _hit(self, rule: str, target: Target | None) -> Target | None:
        self._stats[rule] += 1
        return target

    def resolve(self, href: str, link_text: str) -> Target | None:
        """Return the Target for href, or None to leave the text unlinked."""
        t = link_text.strip()
        if not href.startswith("#"):
            return Target(kind="url", key=href.strip()) if href.strip() and t else None
        name = href[1:]
        if name in self.names:
            target = self.names[name]
            if target.kind == "block":
                self.used.add(target)
            return self._hit("R1", target)
        if re.match(SECTION_NUMBER_RE, t) and t in self.sections:
            return self._hit("R2", Target(kind="section", key=t))
        if t.strip("[]").casefold() in self.refs:
            return self._hit("R3", self.refs[t.strip("[]").casefold()])
        if name in self.removed_names and (href2 := _first_external(self.removed_names[name])):
            return self._hit("R4", Target(kind="url", key=href2))
        for block in self.removed:
            if isinstance(block, (Paragraph, ListItem)) and                     plain_text(block.spans).startswith(t + " ") and (href2 := _first_external(block)):
                return self._hit("R4b", Target(kind="url", key=href2))
        if (href, t) not in self.warned:
            self.warned.add((href, t))
            why = ("its target is in stripped content and no rule matched" if name in self.removed_names
                   else "its target bookmark does not exist and no rule matched")
            log.warning("unresolved internal link %s: %s; left as plain text", self._where(href), why)
        return self._hit("R5", None)

    def targets_for(self, block: Block) -> list[Target]:
        """Return the Targets anchored on block (heading, ref label, or linked-to block anchors)."""
        if isinstance(block, Heading):
            return [Target(kind="section", key=block.number)]
        if id(block) in self.ref_blocks:
            return [self.ref_blocks[id(block)]]
        out: list[Target] = []
        for name in block.anchors:
            target = self.names.get(name)
            if target and target.kind == "block" and target in self.used and target not in out:
                out.append(target)
        return out

    def section_of(self, target: Target) -> str:
        """Number of the section that contains a section, ref or block Target."""
        if target.kind == "section":
            return target.key
        if target.kind == "ref":
            return self.ref_section[target.key]
        if target.kind == "block":
            return self.block_section[target.key]
        raise ValueError(f"a {target.kind} target has no section")

    def is_line_block(self, key: str) -> bool:
        """True when the block anchor sits on a top-level paragraph or list item (one output line)."""
        block = self.anchored.get(key)
        return isinstance(block, (Paragraph, ListItem)) and id(block) in self.top_level

    def reset_stats(self) -> None:
        """Zero the rule counters, so each output format reports its own link statistics."""
        self._stats = dict.fromkeys(RULES, 0)

    @property
    def stats(self) -> dict[str, int]:
        """Count of links resolved by each rule R1-R5 (and R4b)."""
        return dict(self._stats)
