"""Spec facts for the vault: identifiers, objects, relationships, vocabularies.

Built from the parsed Document, independent of any output format. The spec is frozen, so every count is checked
against the expected values in config, and a difference raises EXIT_PARSE: it means a parser regression.
"""
import re
from collections import Counter
from dataclasses import dataclass, field
from itertools import product, takewhile
from typing import Literal

from .anchors import walk
from .config import (ALL_SCO_TARGET, APPENDIX_B_ONLY, ENUM_NAME_LABEL, EXIT_PARSE, EXPECTED_APPENDIX_B_TRIPLES,
                     EXPECTED_DERIVED_TYPES, EXPECTED_FORWARD_TABLES, EXPECTED_ID_CONTRIBUTING, EXPECTED_IDENTS,
                     EXPECTED_OBJECTS, EXPECTED_REL_TYPES, EXPECTED_VALUE_TABLES, EXPECTED_TRIPLES, FORWARD_ONLY,
                     PROPERTY_BANDS, REL_TYPE_HEADERS, RELATIONSHIP_APPENDIX, REVERSE_BAND, SPECIFIC_BAND_SUFFIX,
                     TYPE_NAME_LABEL, VALUE_HEADER_SUFFIX, VOCAB_NAME_LABEL, StixMdError)
from .inline import plain_text
from .model import Block, Cell, Document, Heading, ListItem, Paragraph, Span, Table

LABELS = {TYPE_NAME_LABEL: "type", VOCAB_NAME_LABEL: "vocabulary", ENUM_NAME_LABEL: "enumeration"}
TYPE_VALUE_RE = re.compile(r"\bMUST be ([a-z0-9-]+)")     # `type` row: "The value of this property MUST be `malware`."
B_HEADER = ["Source", "Type", "Target"]
SEPARATORS = str.maketrans("", "", ",()")                 # list punctuation between the code spans of a value row

Triple = tuple[str, str, str]                             # (source type, relationship type, target)


@dataclass(frozen=True, kw_only=True)
class Ident:
    name: str                                   # "malware-type-ov"
    kind: Literal["type", "vocabulary", "enumeration"]
    section: str                                # home section number, "10.15"
    derived: bool = False                       # True for the two derived types


@dataclass(kw_only=True)
class StixObject:
    type: str
    section: str                                # home section
    required: list[str] = field(default_factory=list)
    optional: list[str] = field(default_factory=list)
    not_applicable: list[str] = field(default_factory=list)
    specific: list[str] = field(default_factory=list)
    id_contributing: list[str] = field(default_factory=list)
    relationships: list[tuple[str, str]] = field(default_factory=list)  # (type, target), forward tables, first seen
    vocabularies: list[str] = field(default_factory=list)  # vocabulary/enumeration identifiers in the home subtree


@dataclass(kw_only=True)
class Vocabulary:
    name: str
    kind: Literal["vocabulary", "enumeration"]
    section: str
    values: list[str] = field(default_factory=list)  # from the value table; empty if there is none
    used_by: list[str] = field(default_factory=list)  # object types whose home subtree mentions this vocabulary


@dataclass(kw_only=True)
class SpecFacts:
    idents: dict[str, Ident]                    # reading order of the home sections
    objects: dict[str, StixObject]              # keyed by type, reading order
    vocabularies: dict[str, Vocabulary]
    appendix_b: set[Triple]
    forward_tables: list[str] = field(default_factory=list)   # sections of the forward relationship tables

    def triples(self) -> list[Triple]:
        """Every forward triple, by source object, in first-seen order."""
        return [(obj.type, rel, target) for obj in self.objects.values() for rel, target in obj.relationships]


def _norm(text: str) -> str:
    return " ".join(text.split())


def code_values(spans: list[Span]) -> list[str]:
    """The values named by the code spans of one paragraph, in order.

    Adjacent code spans form one value (`<All ` + `STIX Cyber-observable Objects` + `>`), and a span holding a comma
    list (`location, vulnerability`) gives several values.
    """
    runs: list[str] = []
    current: list[str] = []
    for span in spans:
        if span.code:
            current.append(span.text)
        elif current:
            runs.append("".join(current))
            current = []
    if current:
        runs.append("".join(current))
    return [value for run in runs for part in run.split(",") if (value := _norm(part))]


def _paragraphs(cell: Cell) -> list[list[Span]]:
    return [b.spans for b in cell.blocks if isinstance(b, (Paragraph, ListItem)) and plain_text(b.spans)]


def _text(cell: Cell) -> str:
    return _norm(" ".join(plain_text(spans) for spans in _paragraphs(cell)))


def _values(cell: Cell) -> list[str]:
    return [value for spans in _paragraphs(cell) for value in code_values(spans)]


def _is_list_span(span: Span) -> bool:
    """A code span, or a separator of commas, spaces and parentheses (10.11, 10.21: `government` (`…`, `…`))."""
    return span.code or not _norm(span.text.translate(SEPARATORS))


def parent(number: str) -> str:
    return number.rpartition(".")[0]


def in_subtree(number: str, home: str) -> bool:
    return number == home or number.startswith(home + ".")


def _fail(message: str) -> StixMdError:
    return StixMdError(EXIT_PARSE, f"spec facts: {message}")


def _expect(what: str, got: object, want: object) -> None:
    if got != want:
        raise _fail(f"{what} is {got!r}, expected {want!r} (OBSIDIAN-1.1 §3)")


def _sections(doc: Document) -> dict[str, list[Block]]:
    """The top-level blocks directly under each heading (not under its subsections), by section number."""
    sections: dict[str, list[Block]] = {}
    current: list[Block] | None = None
    for block in doc.blocks:
        if isinstance(block, Heading):
            if block.number in sections:
                raise _fail(f"duplicate section number {block.number!r}")
            current = sections[block.number] = []
        elif current is not None:
            current.append(block)
    return sections


def _tables(blocks: list[Block]) -> list[Table]:
    return [b for b in blocks if isinstance(b, Table)]


def _bands(table: Table) -> dict[str, list[Cell]]:
    """Property band labels of one table, each with the value row that follows it."""
    bands: dict[str, list[Cell]] = {}
    for row, value_row in zip(table.rows, table.rows[1:]):
        if len(row) != 1:
            continue
        label = _text(row[0])
        if label in PROPERTY_BANDS or label.endswith(SPECIFIC_BAND_SUFFIX):
            if label in bands:
                raise _fail(f"band {label!r} twice in one table")
            bands[label] = value_row
    return bands


def _band_values(label: str, row: list[Cell], where: str) -> list[str]:
    """The values of a band's value row: its code spans up to the first prose.

    Only the ID Contributing row continues with prose, in the same paragraph (6.17.1) or in the next ones (6.7.1).
    """
    values: list[str] = []
    prose = False
    for spans in (spans for cell in row for spans in _paragraphs(cell)):
        listed = list(takewhile(_is_list_span, spans))
        values += code_values(listed)
        if len(listed) < len(spans):
            prose = True
            break
    if not values or (prose and PROPERTY_BANDS.get(label) != "id_contributing"):
        raise _fail(f"{where}: the value row of {label!r} is not a list of code spans")
    return list(dict.fromkeys(values))


def _label_idents(sections: dict[str, list[Block]]) -> dict[str, Ident]:
    """Identifiers from the "Type Name:", "Vocabulary Name:" and "Enumeration Name:" lines."""
    idents: dict[str, Ident] = {}
    for number, blocks in sections.items():
        for block in blocks:
            if not isinstance(block, Paragraph):
                continue
            text = plain_text(block.spans)
            for label, kind in LABELS.items():
                if text.startswith(label):
                    name = text[len(label):].strip()
                    if name in idents:
                        raise _fail(f"identifier {name!r} in {idents[name].section} and {number}")
                    idents[name] = Ident(name=name, kind=kind, section=number)
    return idents


def _object_type(tables: list[Table]) -> str | None:
    """The type named by the first `type` property row: its description says "MUST be `<type>`".

    The row is found by the first word of its name cell, since 6.3.1 writes the name bold but not as code.
    """
    for table in tables:
        for row in table.rows:
            if (len(row) >= 3 and _text(row[0]).split(" ")[0] == "type"
                    and (found := TYPE_VALUE_RE.search(_text(row[-1])))):
                return found.group(1)
    return None


def _objects(sections: dict[str, list[Block]], idents: dict[str, Ident]) -> dict[str, StixObject]:
    """One object per property table; home = parent of the table's section. Adds the derived types."""
    objects: dict[str, StixObject] = {}
    for number, blocks in sections.items():
        tables = _tables(blocks)
        for i, table in enumerate(tables):
            bands = _bands(table)
            if not any(PROPERTY_BANDS.get(label) == "required" for label in bands):
                continue
            where = f"section {number}"
            kinds = {PROPERTY_BANDS.get(label, "specific" if label.endswith(SPECIFIC_BAND_SUFFIX) else label)
                     for label in bands}
            if not {"required", "optional", "not_applicable", "specific"} <= kinds:
                raise _fail(f"{where}: property table without all four bands ({sorted(kinds)})")
            type_ = _object_type(tables[i:])
            if type_ is None:
                raise _fail(f"{where}: property table without a `type` row")
            if type_ in objects:
                raise _fail(f"{where}: second property table for {type_!r}")
            obj = objects[type_] = StixObject(type=type_, section=parent(number))
            for label, row in bands.items():
                values = _band_values(label, row, where)
                setattr(obj, PROPERTY_BANDS.get(label, "specific"), values)
            ident = idents.get(type_)
            if ident is None:
                idents[type_] = Ident(name=type_, kind="type", section=obj.section, derived=True)
            elif ident.kind != "type" or ident.section != obj.section:
                raise _fail(f"{type_!r}: label line in {ident.section}, but its property table is in {number}")
    return objects


def _forward_triples(sections: dict[str, list[Block]],
                     objects: dict[str, StixObject]) -> tuple[list[Triple], list[str]]:
    """Triples of the forward relationship tables, and the sections of the tables that give any.

    Everything after a Reverse Relationships band is ignored. Seven more objects have a forward header whose only
    row is "—"; they have no forward table in this sense.
    """
    homes = {obj.section: obj.type for obj in objects.values()}
    triples: list[Triple] = []
    tables_found: dict[str, None] = {}
    for number, blocks in sections.items():
        reverse = False
        for table in _tables(blocks):
            forward = False
            for row in table.rows:
                texts = [_text(cell) for cell in row]
                if len(row) == 1:
                    reverse = reverse or texts[0] == REVERSE_BAND
                    forward = False
                elif len(row) >= 4 and texts[0] == "Source" and texts[1] in REL_TYPE_HEADERS and texts[2] == "Target":
                    forward = not reverse
                    if forward and parent(number) not in homes:
                        raise _fail(f"section {number}: forward relationship table outside an object")
                elif forward:
                    for triple in product(*(_values(cell) for cell in row[:3])):
                        if triple[0] != homes[parent(number)]:
                            raise _fail(f"section {number}: forward source {triple[0]!r} is not the object")
                        if triple in triples:
                            raise _fail(f"section {number}: duplicate triple {triple}")
                        triples.append(triple)
                        tables_found[number] = None
    return triples, list(tables_found)


def _appendix_b(sections: dict[str, list[Block]]) -> set[Triple]:
    """Triples of the Appendix B summary table, the `<All …>` target counted once."""
    triples: set[Triple] = set()
    for table in _tables(sections.get(RELATIONSHIP_APPENDIX, [])):
        if table.rows and [_text(cell) for cell in table.rows[0]] == B_HEADER:
            for row in table.rows[1:]:
                triples.update(product(*(_values(cell) for cell in row[:3])))
    return triples


def _value_table(tables: list[Table], section: str) -> list[str]:
    """The values of a vocabulary's value table: the code span in the first cell of each row after the header row
    (`Vocabulary Value`, `Enumeration Value` or `Value`). The summary band above it is not used: in 10.12 and
    10.17 it differs from the table."""
    for table in tables:
        for i, row in enumerate(table.rows):
            if len(row) >= 2 and _text(row[0]).endswith(VALUE_HEADER_SUFFIX):
                values: list[str] = []
                for value_row in table.rows[i + 1:]:
                    found = _values(value_row[0])
                    if len(value_row) < 2 or len(found) != 1 or found[0] in values:
                        raise _fail(f"section {section}: value table row {_text(value_row[0])!r} is not one new value")
                    values += found
                return values
    return []


def _vocabularies(sections: dict[str, list[Block]], idents: dict[str, Ident],
                  objects: dict[str, StixObject]) -> dict[str, Vocabulary]:
    """Vocabulary and enumeration notes: values from the value tables and the objects that mention them."""
    vocabularies: dict[str, Vocabulary] = {}
    for name, ident in idents.items():
        if ident.kind == "type":
            continue
        vocabularies[name] = Vocabulary(name=name, kind=ident.kind, section=ident.section,
                                        values=_value_table(_tables(sections[ident.section]), ident.section))
    for obj in objects.values():
        mentioned: dict[str, None] = {}
        for number, blocks in sections.items():
            if in_subtree(number, obj.section):
                for block in walk(blocks):
                    if isinstance(block, (Paragraph, ListItem)):
                        mentioned.update((v, None) for v in code_values(block.spans) if v in vocabularies)
        obj.vocabularies = list(mentioned)
        for name in mentioned:
            vocabularies[name].used_by.append(obj.type)
    return vocabularies


def collect_facts(doc: Document) -> SpecFacts:
    """Identifiers, objects, relationships and vocabularies of the kept document, without the count checks."""
    sections = _sections(doc)
    idents = _label_idents(sections)
    objects = _objects(sections, idents)
    order = {number: i for i, number in enumerate(sections)}
    idents = dict(sorted(idents.items(), key=lambda item: order[item[1].section]))
    triples, forward_tables = _forward_triples(sections, objects)
    for source, rel, target in triples:
        objects[source].relationships.append((rel, target))
    return SpecFacts(idents=idents, objects=objects, vocabularies=_vocabularies(sections, idents, objects),
                     appendix_b=_appendix_b(sections), forward_tables=forward_tables)


def check_facts(facts: SpecFacts) -> None:
    """Raise EXIT_PARSE unless every expected count in config holds (the spec is frozen)."""
    idents, objects, triples = facts.idents, facts.objects, facts.triples()
    _expect("the label identifiers per kind", dict(Counter(i.kind for i in idents.values() if not i.derived)),
            EXPECTED_IDENTS)
    _expect("the derived types", tuple(n for n, i in idents.items() if i.derived), EXPECTED_DERIVED_TYPES)
    _expect("the objects per chapter", dict(Counter(o.section.split(".")[0] for o in objects.values())),
            EXPECTED_OBJECTS)
    _expect("the objects with ID Contributing Properties", sum(bool(o.id_contributing) for o in objects.values()),
            EXPECTED_ID_CONTRIBUTING)
    _expect("the forward relationship tables", len(facts.forward_tables), EXPECTED_FORWARD_TABLES)
    _expect("the forward triples", len(triples), EXPECTED_TRIPLES)
    _expect("the relationship types", len({rel for _, rel, _ in triples}), EXPECTED_REL_TYPES)
    unknown = sorted({t for _, _, t in triples if t not in objects and t != ALL_SCO_TARGET})
    _expect("the targets that are neither an object nor the SCO wildcard", unknown, [])
    _expect(f"the triples with target {ALL_SCO_TARGET!r}", sum(t == ALL_SCO_TARGET for _, _, t in triples), 1)
    _expect("the Appendix B triples", len(facts.appendix_b), EXPECTED_APPENDIX_B_TRIPLES)
    forward = set(triples)
    _expect("the triples only in Appendix B", facts.appendix_b - forward, set(APPENDIX_B_ONLY))
    _expect("the triples only in the object tables", forward - facts.appendix_b, set(FORWARD_ONLY))
    _expect("the vocabularies", len(facts.vocabularies), sum(n for k, n in EXPECTED_IDENTS.items() if k != "type"))
    _expect("the vocabularies with a value table", sum(bool(v.values) for v in facts.vocabularies.values()),
            EXPECTED_VALUE_TABLES)


def build_facts(doc: Document) -> SpecFacts:
    """The spec facts of the kept document, checked against the expected counts in config."""
    facts = collect_facts(doc)
    check_facts(facts)
    return facts
