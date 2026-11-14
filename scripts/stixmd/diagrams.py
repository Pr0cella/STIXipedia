"""Relationship diagrams: one Mermaid flowchart per object with relationships."""
from .config import (ALL_SCO_SECTION, ALL_SCO_TARGET, DIAGRAM_BLOCK_PREFIX, DIAGRAM_FENCE, DIAGRAM_LABEL, EXIT_PARSE,
                     StixMdError)
from .facts import SpecFacts
from .layout import VaultLayout


def _home(facts: SpecFacts, name: str) -> str:
    return ALL_SCO_SECTION if name == ALL_SCO_TARGET else facts.idents[name].section


def pairs(facts: SpecFacts) -> dict[tuple[str, str], list[str]]:
    """(source, target) -> relationship types, both in first-seen order of the forward triples."""
    out: dict[tuple[str, str], list[str]] = {}
    for source, rel, target in facts.triples():
        out.setdefault((source, target), []).append(rel)
    return out


def diagram_types(facts: SpecFacts) -> list[str]:
    """Objects with at least one edge, in reading order of their home sections (21 in the spec)."""
    ends = {x for pair in pairs(facts) for x in pair}
    return [t for t in facts.objects if t in ends]


def diagram_lines(facts: SpecFacts, layout: VaultLayout, type_: str) -> list[str]:
    """Label, blank line, mermaid fence, body, closing fence and the block id ^stix-rel-<type>; [] without edges.

    Node n0 is the object, then its neighbours in reading order of their home sections; every node is labelled with
    its note's basename, and every node but n0 gets `internal-link` (split level 2). Edges: outgoing pairs, then
    incoming ones, each in first-seen order; a self-loop is outgoing.
    """
    by_pair = pairs(facts)
    out = [p for p in by_pair if p[0] == type_]
    into = [p for p in by_pair if p[1] == type_ and p[0] != type_]
    if not out and not into:
        return []
    order = {number: i for i, number in enumerate(layout.headings)}
    neighbours = sorted({x for p in out + into for x in p} - {type_}, key=lambda x: order[_home(facts, x)])
    ids = {name: f"n{i}" for i, name in enumerate([type_, *neighbours])}
    labels = {name: layout.by_id[layout.note_of(_home(facts, name))].name for name in ids}
    if len(set(labels.values())) != len(labels) or any('"' in label for label in labels.values()):
        raise StixMdError(EXIT_PARSE, f"diagram of {type_}: node labels repeat or contain a quote")
    body = ["flowchart LR", *(f'  {ids[name]}["{labels[name]}"]' for name in ids)]
    body += [f'  {ids[s]} -->|"{", ".join(by_pair[(s, t)])}"| {ids[t]}' for s, t in out + into]
    body += ["  classDef self stroke-width:3px", "  class n0 self"]
    if neighbours:
        body.append(f"  class {','.join(ids[name] for name in neighbours)} internal-link")
    return [DIAGRAM_LABEL, "", DIAGRAM_FENCE, *body, "```", f"^{DIAGRAM_BLOCK_PREFIX}{type_}"]
