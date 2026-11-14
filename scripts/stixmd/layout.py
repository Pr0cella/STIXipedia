"""Where sections and anchors live: one file, or one note per section."""
from abc import ABC, abstractmethod
from collections.abc import Iterable
from dataclasses import dataclass

from .config import EXIT_PARSE, EXIT_USAGE, INDEX_NOTE, SPLIT_LEVEL, StixMdError, sanitise_name
from .model import Heading
from .safepath import check_name

NoteId = str                  # note path relative to the output root, without ".md"


class Layout(ABC):
    """Answers where each section and block anchor of the kept document is written."""

    def __init__(self, headings: Iterable[Heading]) -> None:
        self.headings: dict[str, Heading] = {h.number: h for h in headings}

    def heading(self, number: str) -> Heading:
        """The kept heading with this section number."""
        if number not in self.headings:
            raise StixMdError(EXIT_PARSE, f"no kept section {number!r}")
        return self.headings[number]

    def heading_of(self, number: str) -> str:
        """Heading text of a section, as written in the output."""
        return self.heading(number).text

    @abstractmethod
    def note_of(self, number: str) -> NoteId:
        """The note that contains the section."""

    @abstractmethod
    def is_note_root(self, number: str) -> bool:
        """True when the section's heading is its note's own title heading."""


class SingleLayout(Layout):
    """Everything in one file: every section and anchor lives in the same note."""

    def __init__(self, headings: Iterable[Heading], note: NoteId = "") -> None:
        super().__init__(headings)
        self.note = note

    def note_of(self, number: str) -> NoteId:
        self.heading(number)
        return self.note

    def is_note_root(self, number: str) -> bool:
        self.heading(number)
        return False


@dataclass(frozen=True)
class Note:
    """One vault note: its id (folder/name), its root heading, its parent note and chapter."""
    id: NoteId
    heading: Heading
    parent: NoteId | None     # None for chapter notes (their parent is the index note)
    chapter: Heading

    @property
    def name(self) -> str:
        """Basename, which is what wikilinks use (unique in the vault)."""
        return self.id.rsplit("/", 1)[-1]


def note_name(text: str) -> str:
    """Sanitised, Windows-safe note or folder name for heading text."""
    name = sanitise_name(text)
    try:
        return check_name(name)
    except StixMdError as exc:
        raise StixMdError(EXIT_PARSE, f"heading {text!r} gives no usable note name: {exc}") from exc


class VaultLayout(Layout):
    """One note per heading of level <= split_level, in one folder per chapter."""

    def __init__(self, headings: Iterable[Heading], split_level: int) -> None:
        super().__init__(headings)
        if split_level != SPLIT_LEVEL:                  # one note per chapter and section only
            raise StixMdError(EXIT_USAGE, f"split level must be {SPLIT_LEVEL}")
        self.split_level = split_level
        self.notes: list[Note] = []
        self._note_of: dict[str, NoteId] = {}
        self._roots: set[str] = set()
        seen = {INDEX_NOTE.casefold(): "the index note"}  # casefolded name -> section number that took it
        stack: list[Note] = []                          # open notes, outermost first
        for head in self.headings.values():
            if head.level <= split_level:
                while stack and stack[-1].heading.level >= head.level:
                    stack.pop()
                if head.level == 1:
                    stack = []
                elif not stack:
                    raise StixMdError(EXIT_PARSE, f"section {head.number} comes before any chapter")
                chapter = stack[0].heading if stack else head
                name = note_name(head.text)
                if (other := seen.setdefault(name.casefold(), head.number)) != head.number:
                    raise StixMdError(EXIT_PARSE, f"note name collision: section {head.number} and {other} -> {name!r}")
                note = Note(id=f"{note_name(chapter.text)}/{name}", heading=head,
                            parent=stack[-1].id if stack else None, chapter=chapter)
                self.notes.append(note)
                self._roots.add(head.number)
                stack.append(note)
            elif not stack:
                raise StixMdError(EXIT_PARSE, f"section {head.number} comes before any chapter")
            self._note_of[head.number] = stack[-1].id
        self.by_id = {n.id: n for n in self.notes}

    def note_of(self, number: str) -> NoteId:
        self.heading(number)
        return self._note_of[number]

    def is_note_root(self, number: str) -> bool:
        self.heading(number)
        return number in self._roots

    def children(self, note: Note) -> list[Note]:
        """Direct child notes, in reading order."""
        return [n for n in self.notes if n.parent == note.id]

    def ancestors(self, note: Note) -> list[Note]:
        """Parent chain from the chapter note down to the parent of `note`."""
        chain: list[Note] = []
        while note.parent is not None:
            note = self.by_id[note.parent]
            chain.insert(0, note)
        return chain
