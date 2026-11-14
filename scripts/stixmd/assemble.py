"""Assemblers: turn the kept document into a Corpus for one output format."""
import hashlib
import json
from collections import Counter

from .anchors import AnchorMap
from .config import (ATLAS_CSS_CLASS, ATLAS_NOTE, EXIT_PARSE, GENERATOR, INDEX_NOTE, MANIFEST_NAME, OBJECT_CSS_CLASS, TYPE_NAME_LABEL,
                     VAULT_SCHEMA, WIDE_CSS_CLASS, WIDE_TABLE_MIN_COLUMNS, StixMdError)
from .diagrams import diagram_lines, diagram_types
from .facts import SpecFacts, StixObject, Vocabulary
from .flavor import Flavor, GfmFlavor, ObsidianFlavor
from .indexer import (build_atlas, build_markdown, build_vault_index, build_vault_note, object_properties, vault_bases,
                      vocabulary_properties)
from .inline import plain_text
from .layout import NoteId, SingleLayout, VaultLayout
from .model import Block, Corpus, Document, Heading, Paragraph
from .render import render_body
from .tables import widest_table


def assemble_single(doc: Document, flavor: Flavor, *, name: str, source_sha256: str,
                    generated_utc: str) -> Corpus:
    """The GFM file: front matter, cover, index, then the body, exactly in the SAP format. The Obsidian
    dialect is written only into the vault."""
    if not isinstance(flavor.layout, SingleLayout):
        raise StixMdError(EXIT_PARSE, "assemble_single needs a SingleLayout")
    meta = {"source_sha256": source_sha256, "generated_utc": generated_utc}
    body, heads = render_body(doc.blocks, flavor, note=flavor.layout.note)
    if isinstance(flavor, GfmFlavor):
        return {name: build_markdown(doc, body, heads, **meta)}
    raise StixMdError(EXIT_PARSE, f"no single-file assembler for flavour {flavor.name!r}")


def _split(doc: Document, layout: VaultLayout) -> dict[NoteId, list[Block]]:
    """Top-level blocks per note: a note-root heading starts its note's block list."""
    groups: dict[NoteId, list[Block]] = {}
    current: list[Block] | None = None
    for block in doc.blocks:
        if isinstance(block, Heading) and layout.is_note_root(block.number):
            current = groups[layout.note_of(block.number)] = []
        if current is None:
            raise StixMdError(EXIT_PARSE, "content before the first chapter heading")
        current.append(block)
    return groups


def _type_names(blocks: list[Block]) -> list[str]:
    names = []
    for block in blocks:
        if isinstance(block, Paragraph) and (text := plain_text(block.spans)).startswith(TYPE_NAME_LABEL):
            names.append(text[len(TYPE_NAME_LABEL):].strip())
    return list(dict.fromkeys(names))


def manifest_text(corpus: Corpus, *, split_level: int, source_sha256: str, generated_utc: str) -> str:
    """The vault manifest and generated-by marker: every other file with its hash and size."""
    files = []
    for path, text in corpus.items():
        data = text.encode("utf-8")
        files.append({"path": path, "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)})
    manifest = {"schema": VAULT_SCHEMA, "generator": GENERATOR,
                "generated_utc": generated_utc, "source_sha256": source_sha256, "split_level": split_level,
                "files": files}
    return json.dumps(manifest, indent=2, ensure_ascii=False) + "\n"


def assemble_vault(doc: Document, amap: AnchorMap, split_level: int, *, source_sha256: str,
                   generated_utc: str, facts: SpecFacts | None = None) -> Corpus:
    """Index note, atlas note, one note per chapter/section in reading order, Bases files, manifest.

    With `facts`, identifier code spans link to their definitions, object notes get their
    relationship diagram and the atlas note collects them.
    """
    layout = VaultLayout((b for b in doc.blocks if isinstance(b, Heading)), split_level)
    flavor = ObsidianFlavor(amap, layout, facts)
    groups = _split(doc, layout)
    titles = Counter(n.heading.title for n in layout.notes)
    properties = _note_properties(layout, facts) if facts is not None else {}
    diagrammed = diagram_types(facts) if facts is not None else []
    diagrams = {layout.note_of(facts.objects[t].section): diagram_lines(facts, layout, t) for t in diagrammed}
    if len(diagrams) != len(diagrammed):
        raise StixMdError(EXIT_PARSE, "two objects with relationship diagrams share a note")
    notes_text: Corpus = {}
    used_css: set[str] = set()
    derived = [i for i in facts.idents.values() if i.derived] if facts is not None else []
    homes = Counter(layout.note_of(o.section) for o in facts.objects.values()) if facts is not None else Counter()
    notes = layout.notes
    for i, note in enumerate(notes):
        blocks = groups[note.id]
        body, _ = render_body(blocks, flavor, note=note.id, shift=1 - note.heading.level)
        types = _type_names(blocks)
        types += [d.name for d in derived if layout.note_of(d.section) == note.id and d.name not in types]
        aliases = [note.heading.title] if titles[note.heading.title] == 1 else []
        aliases += [t for t in types if t not in aliases]
        css = [WIDE_CSS_CLASS] if widest_table(body) >= WIDE_TABLE_MIN_COLUMNS else []
        css += [OBJECT_CSS_CLASS] if homes[note.id] == 1 else []         # notes that own one object
        used_css.update(css)
        notes_text[note.id + ".md"] = build_vault_note(
            layout, note, body, prev=notes[i - 1] if i else None, nxt=notes[i + 1] if i + 1 < len(notes) else None,
            aliases=aliases, cssclasses=css, stix_types=types, source_sha256=source_sha256,
            properties=properties.get(note.id), diagram=diagrams.get(note.id))
    if diagrams:
        used_css.add(ATLAS_CSS_CLASS)                                  # the atlas note carries it
    corpus: Corpus = {f"{INDEX_NOTE}.md": build_vault_index(doc, layout, flavor, source_sha256=source_sha256,
                                                            generated_utc=generated_utc, bases=bool(properties),
                                                            atlas=bool(diagrams), css=used_css)}
    if diagrams:
        corpus[f"{ATLAS_NOTE}.md"] = build_atlas(layout, facts, diagrammed, source_sha256=source_sha256)
    corpus |= notes_text                       # the index (built last: it names the classes in use) comes first
    if properties:
        corpus |= vault_bases()
    corpus[MANIFEST_NAME] = manifest_text(corpus, split_level=split_level, source_sha256=source_sha256,
                                          generated_utc=generated_utc)
    return corpus


def _note_properties(layout: VaultLayout, facts: SpecFacts) -> dict[NoteId, dict]:
    """Properties per note. A note gets them only when it is home to exactly one object or vocabulary."""
    objects: dict[NoteId, list[StixObject]] = {}
    for obj in facts.objects.values():
        objects.setdefault(layout.note_of(obj.section), []).append(obj)
    vocabs: dict[NoteId, list[Vocabulary]] = {}
    for vocab in facts.vocabularies.values():
        vocabs.setdefault(layout.note_of(vocab.section), []).append(vocab)
    out = {note: object_properties(layout, facts, objs[0]) for note, objs in objects.items() if len(objs) == 1}
    out |= {note: vocabulary_properties(layout, facts, vs[0]) for note, vs in vocabs.items() if len(vs) == 1}
    return out
