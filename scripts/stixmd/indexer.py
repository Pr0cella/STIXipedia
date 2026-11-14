"""Front matter, cover, index and note text: GFM and the vault."""
import json
import re
from collections import Counter
from dataclasses import replace

from .config import (ALL_SCO_SECTION, ALL_SCO_TARGET, APPENDIX_TAG, ATLAS_ALIASES, ATLAS_CSS_CLASS, ATLAS_GROUPS,
                     ATLAS_NOTE, ATLAS_TAGS, BASE_SUFFIX, BASES_EXAMPLE_TYPE, BOOKMARK_GROUP,
                     BOOKMARK_SECTIONS, CHAPTER_TAGS, CHILD_LIST_LABEL, CSS_SNIPPET, DEPRECATED_TAG,
                     DIAGRAM_BLOCK_PREFIX, EXIT_PARSE,
                     GENERATOR, GRAPH_HIDDEN_CHAPTERS, HOW_TO_CITATION, HOW_TO_IDENTIFIER, HOW_TO_SECTION, INDEX_NOTE,
                     NAV_PREFIX, OBJECT_CSS_CLASS, OBJECTS_BASE, PROPERTY_KEYS, RELATIONSHIPS_BASE, SOURCE_URL,
                     TYPE_NAME_LABEL, VOCAB_CHAPTER_PREFIX, VOCABULARIES_BASE, WIDE_CSS_CLASS, StixMdError, md_url)
from .diagrams import diagram_types
from .facts import Ident, SpecFacts, StixObject, Vocabulary
from .flavor import ObsidianFlavor, obsidian_escape, wikilink
from .inline import InlineWriter, code_span, md_link_writer, plain_text, render_spans
from .layout import Layout, Note, VaultLayout
from .model import Document, Heading, Paragraph, Target

HOW_TO_SEARCH = [
    '- Every spec section starts with an anchor line `<a id="sec-NUMBER"></a>` followed by its '
    'heading, e.g. `<a id="sec-4.1"></a>` / `### 4.1 Attack Pattern`. To jump to a section, grep the '
    'anchored regex `^<a id="sec-4\\.1">` (the `^` keeps it from matching this line).',
    "- Heading levels: chapter `##`, x.y `###`, x.y.z `####`, x.y.z.w `#####`, deeper `######`.",
    "- `Lstart-Lend` ranges below are 1-based line numbers covering the section including its "
    "subsections; read them with offset=start, limit=end-start+1.",
    "- Citations such as [RFC8259] link to `#ref-<label>` anchors in Appendix E.",
    "- Code examples are fenced; JSON examples are tagged `json`.",
]


OBSIDIAN_ALIASES = ["STIX 2.1", "STIX v2.1"]


def _example_section(layout: Layout) -> str:
    """The section the how-to examples use: HOW_TO_SECTION, else the first x.y section."""
    if HOW_TO_SECTION in layout.headings:
        return HOW_TO_SECTION
    return next((n for n, h in layout.headings.items() if h.level == 2), next(iter(layout.headings)))


def _example_ref(flavor: ObsidianFlavor) -> Target | None:
    refs = list(flavor.amap.refs.values())
    return next((t for t in refs if t.key == HOW_TO_CITATION), refs[0] if refs else None)


def _example_ident(facts: SpecFacts | None) -> Ident | None:
    if facts is None or not facts.idents:
        return None
    return facts.idents.get(HOW_TO_IDENTIFIER) or next(iter(facts.idents.values()))


def _citation(flavor: ObsidianFlavor, note: str, ref: Target, *, alias: bool) -> str:
    dest = flavor.dest(ref, note)
    return f"[[{dest}|{ref.key.upper()}]]" if alias else f"[[{dest}]]"


def _external_only(href: str, text: str) -> str | None:
    return href.strip() if not href.startswith("#") and href.strip() and text.strip() else None


def _ranges(heads: list[tuple[Heading, int]], offset: int, total: int) -> list[tuple[int, int]]:
    """R-Range: 1-based (start, end) per heading."""
    out = []
    for k, (head, idx) in enumerate(heads):
        end = total
        for later, later_idx in heads[k + 1:]:
            if later.level <= head.level:
                end = offset + later_idx
                break
        out.append((offset + idx + 1, end))
    return out


def _type_rows(doc: Document, facts: SpecFacts | None = None) -> list[tuple[str, Heading]]:
    """(type name, home heading) per "Type Name:" line; with facts, also the two derived types (77 rows)."""
    if facts is not None:
        heads = {b.number: b for b in doc.blocks if isinstance(b, Heading)}
        return [(i.name, heads[i.section]) for i in facts.idents.values() if i.kind == "type"]
    rows: list[tuple[str, Heading]] = []
    current: Heading | None = None
    for block in doc.blocks:
        if isinstance(block, Heading):
            current = block
        elif isinstance(block, Paragraph) and current is not None:
            text = plain_text(block.spans)
            if text.startswith(TYPE_NAME_LABEL):
                rows.append((text[len(TYPE_NAME_LABEL):].strip(), current))
    return rows


def _cover_lines(doc: Document, writer: InlineWriter) -> list[str]:
    cover = doc.cover
    if cover is None:
        raise StixMdError(EXIT_PARSE, "document has no cover")
    lines = [f"# {cover.title}", "", f"{cover.stage} · {cover.date_text}", ""]
    for label, links in cover.stage_links:
        lines += [f"**{label}**", ""]
        lines += [f"- [{url}]({md_url(url)})" + (f" {note}" if note else "") for url, note in links]
        lines.append("")
    lines += ["**Abstract:**", ""]
    for para in cover.abstract:
        lines += [render_spans(para, writer), ""]
    return lines


def _front(doc: Document, *, source_sha256: str, generated_utc: str) -> dict:
    """Front-matter keys shared by all formats, in SAP-format order."""
    cover = doc.cover
    assert cover is not None
    return {"title": cover.title, "stage": cover.stage, "date": cover.date_iso,
            "source_url": SOURCE_URL, "source_sha256": source_sha256,
            "generator": GENERATOR, "generated_utc": generated_utc}


def _header(doc: Document, heads: list[tuple[Heading, int]], ranges: list[tuple[int, int]],
            *, source_sha256: str, generated_utc: str) -> list[str]:
    front = _front(doc, source_sha256=source_sha256, generated_utc=generated_utc)
    lines = ["---", *(f"{k}: {json.dumps(v, ensure_ascii=False)}" for k, v in front.items()),
             f"sections: {len(heads)}", "---", ""]
    lines += _cover_lines(doc, md_link_writer(_external_only))
    lines += ["## Index", "", "### How to search this file", "", *HOW_TO_SEARCH, "", "### Contents", ""]
    by_number = {}
    for (head, _), (start, end) in zip(heads, ranges):
        by_number[head.number] = (start, end)
        lines.append(f"{'  ' * (head.level - 1)}- [{head.text}](#sec-{head.number}) · L{start}-L{end}")
    lines += ["", "### Type index", "", "| Type name | Section | Lines |", "| --- | --- | --- |"]
    for name, head in _type_rows(doc):
        start, end = by_number[head.number]
        lines.append(f"| {code_span(name)} | [{head.text}](#sec-{head.number}) | L{start}-L{end} |")
    lines += ["", "### Vocabulary index", "", "| Vocabulary | Section | Lines |", "| --- | --- | --- |"]
    for head, _ in heads:
        if head.level == 2 and head.number.startswith(VOCAB_CHAPTER_PREFIX):
            start, end = by_number[head.number]
            lines.append(f"| {head.title} | [{head.number}](#sec-{head.number}) | L{start}-L{end} |")
    lines += ["", "---", ""]
    return lines


def build_markdown(doc: Document, body: list[str], heads: list[tuple[Heading, int]], *,
                   source_sha256: str, generated_utc: str) -> str:
    """Return the complete Markdown file text (front matter, cover, index, body)."""
    meta = {"source_sha256": source_sha256, "generated_utc": generated_utc}
    placeholder = [(0, 0)] * len(heads)
    header = _header(doc, heads, placeholder, **meta)
    offset = len(header)
    ranges = _ranges(heads, offset, offset + len(body))
    header = _header(doc, heads, ranges, **meta)
    assert len(header) == offset, "header length changed between passes"
    return "\n".join(header + body) + "\n"


# --- vault -------------------------------------------------------------------------------------------

def _yaml_lines(front: dict) -> list[str]:
    return ["---", *(f"{k}: {json.dumps(v, ensure_ascii=False)}" for k, v in front.items()), "---", ""]


def note_tags(note: Note) -> list[str]:
    """Chapter tag plus stix/deprecated when the note's heading says so."""
    tags = [CHAPTER_TAGS.get(note.chapter.number, APPENDIX_TAG)]
    if "deprecated" in note.heading.text.casefold():
        tags.append(DEPRECATED_TAG)
    return tags


def nav_line(layout: VaultLayout, note: Note, prev: Note | None, nxt: Note | None) -> str:
    """Index › ancestors · ← prev · next →; always the first line after the front matter."""
    line = " › ".join([NAV_PREFIX, *(f"[[{a.name}|{a.heading.number}]]" for a in layout.ancestors(note))])
    if prev is not None:
        line += f" · ← [[{prev.name}|{prev.heading.number}]]"
    if nxt is not None:
        line += f" · [[{nxt.name}|{nxt.heading.number}]] →"
    return line


def note_link(layout: VaultLayout, section: str) -> str:
    """[[note]] of the note that contains the section."""
    return f"[[{layout.by_id[layout.note_of(section)].name}]]"


def object_properties(layout: VaultLayout, facts: SpecFacts, obj: StixObject) -> dict:
    """Properties of an object note, in the spec's order; empty lists are left out."""
    front: dict = {f"{kind}_properties": getattr(obj, kind) for kind in PROPERTY_KEYS}
    front["relationship_types"] = list(dict.fromkeys(rel for rel, _ in obj.relationships))
    front["relationship_targets"] = list(dict.fromkeys(
        note_link(layout, ALL_SCO_SECTION if target == ALL_SCO_TARGET else facts.idents[target].section)
        for _, target in obj.relationships))
    front["vocabularies"] = [note_link(layout, facts.vocabularies[v].section) for v in obj.vocabularies]
    return {key: value for key, value in front.items() if value}


def vocabulary_properties(layout: VaultLayout, facts: SpecFacts, vocab: Vocabulary) -> dict:
    """Properties of a vocabulary or enumeration note."""
    front = {"vocabulary_name": vocab.name, "vocabulary_kind": vocab.kind, "vocabulary_values": vocab.values,
             "used_by": list(dict.fromkeys(note_link(layout, facts.objects[t].section) for t in vocab.used_by))}
    return {key: value for key, value in front.items() if value}


_YAML_INDICATORS = "-?:,[]{}#&*!|>'\"%@`"
_YAML_SPECIAL_RE = re.compile(r"y|n|yes|no|true|false|on|off|~|null|[-+.]?\d[\w.+-]*|[-+]?\.inf|\.nan", re.IGNORECASE)


def _plain(value: object) -> str:
    """A scalar as YAML writes it without quotes; refuses one that would need them."""
    if (not isinstance(value, str) or not value or value != value.strip(" ") or value[0] in _YAML_INDICATORS
            or ": " in value or " #" in value or value.endswith(":") or _YAML_SPECIAL_RE.fullmatch(value)):
        raise ValueError(f"{value!r} is not safe as a plain YAML scalar")
    return value


def plain_yaml(value: object, indent: int = 0) -> list[str]:
    """Block YAML with plain scalars, the style Obsidian saves Bases files in."""
    pad = " " * indent
    lines: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            lines += ([f"{pad}{key}:", *plain_yaml(item, indent + 2)] if isinstance(item, (dict, list))
                      else [f"{pad}{key}: {_plain(item)}"])
    elif isinstance(value, list):
        for item in value:
            if isinstance(item, (dict, list)):
                sub = plain_yaml(item, indent + 2)
                lines += [f"{pad}- {sub[0].lstrip()}", *sub[1:]]
            else:
                lines.append(f"{pad}- {_plain(item)}")
    else:
        lines.append(pad + _plain(value))
    return lines


def _base_view(name: str, columns: list[str], **filters: list[str]) -> dict:
    view: dict = {"type": "table", "name": name}
    if filters:
        view["filters"] = filters
    view["order"] = ["file.name", *columns]               # note Properties as bare ids, as Obsidian writes them
    return view


def vault_bases() -> dict[str, str]:
    """The three Bases files at the vault root in the style Obsidian saves them: plain block
    YAML, note Properties as bare column ids; filters use only hasProperty and hasTag."""
    tag = {chapter: f'file.hasTag("{CHAPTER_TAGS[chapter]}")' for chapter in ("4", "5", "6", "7")}
    columns = ["stix_types", "required_properties", "specific_properties"]
    specs = {
        OBJECTS_BASE: {"filters": {"and": ['file.hasProperty("specific_properties")']}, "views": [
            _base_view("All objects", columns),
            _base_view("SDOs", columns, **{"and": [tag["4"]]}),
            _base_view("SCOs", [*columns, "id_contributing_properties"], **{"and": [tag["6"]]}),
            _base_view("SROs and SMOs", columns, **{"or": [tag["5"], tag["7"]]})]},
        RELATIONSHIPS_BASE: {"filters": {"and": ['file.hasProperty("relationship_types")']}, "views": [
            _base_view("Relationships", ["stix_types", "relationship_types", "relationship_targets"])]},
        VOCABULARIES_BASE: {"filters": {"and": ['file.hasProperty("vocabulary_name")']}, "views": [
            _base_view("Vocabularies", ["vocabulary_name", "vocabulary_kind", "used_by", "vocabulary_values"])]},
    }
    return {name + BASE_SUFFIX: "\n".join(plain_yaml(spec)) + "\n" for name, spec in specs.items()}


def build_vault_note(layout: VaultLayout, note: Note, body: list[str], *, prev: Note | None, nxt: Note | None,
                     aliases: list[str], stix_types: list[str], source_sha256: str,
                     properties: dict | None = None, cssclasses: list[str] | None = None,
                     diagram: list[str] | None = None) -> str:
    """One chapter or section note: Properties, NAV line, body, the list of child notes, the relationship diagram,
    then the NAV line again, so the footer always comes last.

    `cssclasses` go after `aliases`; `properties` after `stix_types` and before `up`.
    """
    head = note.heading
    parent = layout.by_id[note.parent].name if note.parent else INDEX_NOTE
    front: dict = {"title": head.text, "section": head.number, "kind": "chapter" if head.level == 1 else "section",
                   "chapter": note.chapter.number, "tags": note_tags(note), "aliases": aliases}
    if cssclasses:
        front["cssclasses"] = cssclasses
    if stix_types:
        front["stix_types"] = stix_types
    front |= properties or {}
    front["up"] = f"[[{parent}]]"
    if prev is not None:
        front["prev"] = f"[[{prev.name}]]"
    if nxt is not None:
        front["next"] = f"[[{nxt.name}]]"
    if len(head.anchors) != 1:                                  # no silent wrong link
        raise StixMdError(EXIT_PARSE, f"heading {head.number} has {len(head.anchors)} anchors, expected 1")
    front |= {"source_url": f"{SOURCE_URL}#{head.anchors[0]}", "source_sha256": source_sha256}
    nav = nav_line(layout, note, prev, nxt)
    lines = [*_yaml_lines(front), nav, "", *body]
    if children := layout.children(note):
        if head.level != 1:                                     # split level 2: only chapter notes have children
            raise StixMdError(EXIT_PARSE, f"section note {note.name} has child notes")
        lines += ["", CHILD_LIST_LABEL, "", *(f"- [[{c.name}]]" for c in children)]
    while lines[-1] == "":
        lines.pop()
    if diagram:
        lines += ["", *diagram]
    lines += ["", nav]                                              # footer: a copy of the NAV line
    return "\n".join(lines) + "\n"


def _join(items: list[str]) -> str:
    return " and ".join(items) if len(items) < 3 else ", ".join(items[:-1]) + " and " + items[-1]


def _vault_how_to(layout: VaultLayout, flavor: ObsidianFlavor, *, bases: bool, atlas: bool = False,
                  css: frozenset[str] | set[str] = frozenset()) -> list[str]:
    """How-to bullets of the index note; one note per chapter and section.

    Every example that names a note, heading, block id, Property or tag is built from the Layout, so V17 can
    resolve it. With `bases`, the Catalogs bullets link the three Bases files; with `atlas`, a
    bullet describes the relationship diagrams and links the atlas note, which the graph hides too.
    The CSS bullet names only the `css` classes that notes carry, so each of its examples exists (V17).
    """
    facts = flavor.facts
    section = _example_section(layout)
    note = layout.by_id[layout.note_of(section)].heading     # the note of 4.1: 4.1 Attack Pattern
    types = [i.name for i in facts.idents.values() if i.kind == "type" and i.section == section] if facts else []
    lines = [
        "- **About this vault.** The notes reproduce the OASIS STIX 2.1 specification as a read-only reference. "
        "Keep your own notes in a separate folder or vault and link to these notes from there.",
        "- Each chapter and each section (x.y) is a note; subsections (x.y.z) and deeper are headings inside their "
        "note.",
        "- **Navigate.** Each note starts with a NAV line (Index › parent notes · ← previous · next →) and ends with "
        "a copy of it, so the next note is one click away after reading; its Properties `up`, `prev` and `next` hold "
        f"the same links. Chapter notes list their section notes under {CHILD_LIST_LABEL}, before the last line.",
    ]
    lines += [
        "- The Outline pane shows the headings inside a note. Hovering over a link shows a preview (Page preview "
        "core plugin; in Live Preview hold Ctrl, or Cmd on macOS).",
        f"- **Find.** The quick switcher (Ctrl+O) finds notes by section number (`{note.number}`)"
        + (f", by title (`{note.title}`) and by STIX type name ({code_span(types[0])}), an alias of the note"
           if types else f" and by title (`{note.title}`)") + ". The Contents, Type index, Vocabulary index and Tag overview below list every note, type and vocabulary."
        " A click on a tag in the Tag overview searches for its notes; the counts leave out this index, which carries"
        " every tag it lists.",
        f"- Search (Ctrl+Shift+F) examples: `tag:#{note_tags(layout.by_id[layout.note_of(section)])[0]}`, `[section:{note.number}]`, `[kind:chapter]`"
        + (f", `[stix_types:{types[0]}]`" if types else "") + ".",
        "- **Follow references.** Cross-references are links to notes and headings, e.g. "
        f"`[[{flavor.section_dest(section, INDEX_NOTE)}|{section}]]`.",
    ]
    if ref := _example_ref(flavor):
        lines[-1] += (f" Citations such as \\[{ref.key.upper()}\\] link to block ids in Appendix E, e.g. "
                      f"`{_citation(flavor, INDEX_NOTE, ref, alias=False)}`.")
    lines[-1] += " The `source_url` Property of a note opens its section in the published specification."
    if ident := _example_ident(facts):
        lines.append(f"- Identifier links: a code-styled type, vocabulary or enumeration name such as "
                     f"{code_span(ident.name)} opens its definition. The Backlinks pane of a definition lists every "
                     f"note that uses it, e.g. for `[[{flavor.section_dest(ident.section, INDEX_NOTE)}]]`.")
    if bases and facts is not None:
        example = layout.by_id[layout.note_of(facts.objects[BASES_EXAMPLE_TYPE].section)].name
        keys = [f"`{k}_properties`" for k in PROPERTY_KEYS] + ["`relationship_types`", "`relationship_targets`",
                                                               "`vocabularies`"]
        lines += [
            f"- **Catalogs** (Bases core plugin, Obsidian 1.9 or later): [[{OBJECTS_BASE}{BASE_SUFFIX}|Object catalog]], "
            f"[[{RELATIONSHIPS_BASE}{BASE_SUFFIX}|Relationship catalog]], "
            f"[[{VOCABULARIES_BASE}{BASE_SUFFIX}|Vocabulary catalog]]. Object notes carry {_join(keys)} as "
            "Properties; vocabulary notes carry `vocabulary_name`, `vocabulary_kind`, `vocabulary_values` and "
            "`used_by`.",
            f"- What can target a {BASES_EXAMPLE_TYPE}? Add the filter "
            f'`relationship_targets.contains(link("{example}"))` to a view of the relationship catalog.',
        ]
    if atlas and facts is not None:
        drawn = diagram_types(facts)
        shown = types[0] if types and types[0] in drawn else drawn[0]     # 4.1: attack-pattern
        home = layout.by_id[layout.note_of(facts.objects[shown].section)].name
        lines.append("- **Relationship diagrams.** Object notes with relationships end with a diagram of the "
                     "relationships from and to other objects, as the relationship tables of the specification list "
                     "them, just before the last line: arrows point from source to target and name the relationship "
                     f"types, and a click on a node opens its note. [[{ATLAS_NOTE}]] collects all diagrams. To show "
                     f"one in another note, embed it by its block id, e.g. `![[{home}#^{DIAGRAM_BLOCK_PREFIX}{shown}]]`.")
    hubs = [f"`{layout.heading_of(c)}`" for c in GRAPH_HIDDEN_CHAPTERS if c in layout.headings]
    marks = [f"`[[{flavor.section_dest(s, INDEX_NOTE)}]]`" for s in BOOKMARK_SECTIONS if s in layout.headings]
    lines += [
        "- **Graph.** The graph view (Ctrl+G) shows the STIX object model through the identifier links"
        + (" and the `relationship_targets` Properties" if bases else "") + ". The starter graph settings colour "
        "notes by tag and hide the hub notes: "
        + _join(["this index", *(["the relationship atlas"] if atlas else []),
                 *([f"the notes in {_join(hubs)}"] if hubs else [])]) + ". Clear the filter in the graph settings "
        "to see them. The command \"Open local graph\" (command palette, Ctrl+P) shows the neighbourhood of one note.",
        f"- **Starter settings.** The vault's `.obsidian/` folder holds starter settings: `graph.json` (the graph "
        f"settings above), `appearance.json`, which enables the CSS snippet `snippets/{CSS_SNIPPET}.css`, "
        f"`bookmarks.json` with the bookmark group `{BOOKMARK_GROUP}`" + (f": {_join(marks)}" if marks else "")
        + ", and `app.json`, which opens notes in reading view (Ctrl+E switches to editing). They are "
        "ordinary Obsidian settings; change them as you like.",
    ]
    effects = [f"widens notes with tables of three or more columns (Property `cssclasses: {WIDE_CSS_CLASS}`) and "
               "shades every other row of their tables"] if WIDE_CSS_CLASS in css else []
    effects += ["keeps identifier links in the link colour in reading view"] if facts is not None else []
    effects += [f"lets the long Properties block of object notes (`cssclasses: {OBJECT_CSS_CLASS}`) scroll"
                ] if OBJECT_CSS_CLASS in css else []
    effects += [f"gives the relationship atlas (`cssclasses: {ATLAS_CSS_CLASS}`) the full window width, shrinking "
                "diagrams that are still too wide"] if ATLAS_CSS_CLASS in css else []
    # the items contain "and" themselves, so three or more of them get a serial comma
    said = ", ".join(effects[:-1]) + ", and " + effects[-1] if len(effects) > 2 else " and ".join(effects)
    lines.append("- **CSS.** " + (f"The `{CSS_SNIPPET}` snippet {said}. " if effects else "")
                 + f"If notes look unstyled, enable `{CSS_SNIPPET}` under Settings › Appearance › CSS snippets.")
    return lines


def build_vault_index(doc: Document, layout: VaultLayout, flavor: ObsidianFlavor, *,
                      source_sha256: str, generated_utc: str, bases: bool = False, atlas: bool = False,
                      css: frozenset[str] | set[str] = frozenset()) -> str:
    """The index note (MOC): Properties, cover, how-to, contents tree, type, vocabulary and tag indexes.

    With `bases`, the how-to also links the three Bases files; with `atlas`, the atlas note;
    `css` are the classes the notes carry. The atlas is not a spec section, so it is in neither
    the Contents nor the Tag overview.
    """
    if doc.cover is None:
        raise StixMdError(EXIT_PARSE, "document has no cover")
    spec = _front(doc, source_sha256=source_sha256, generated_utc=generated_utc)
    del spec["generated_utc"]           # only the manifest carries the run time, so notes are byte-stable
    front = {"title": INDEX_NOTE, "kind": "index", "spec_title": spec.pop("title"), **spec,
             "notes": len(layout.notes),                     # no split_level: there is one
             "aliases": OBSIDIAN_ALIASES, "tags": ["stix", "stix/index"]}
    lines = _yaml_lines(front)
    lines += _cover_lines(doc, replace(md_link_writer(_external_only, obsidian_escape), merge_emphasis=True))
    how_to = _vault_how_to(layout, flavor, bases=bases, atlas=atlas, css=css)
    lines += ["## Index", "", "### How to use this vault", "", *how_to, "", "### Contents", ""]
    for head in layout.headings.values():
        dest = flavor.section_dest(head.number, INDEX_NOTE)
        link = f"[[{dest}]]" if dest == head.text else wikilink(dest, head.text)
        lines.append(f"{'  ' * (head.level - 1)}- {link}")
    lines += ["", "### Type index", "", "| Type name | Section |", "| --- | --- |"]
    for name, head in _type_rows(doc, flavor.facts):
        link = wikilink(flavor.section_dest(head.number, INDEX_NOTE), head.text).replace("|", r"\|")
        lines.append(f"| {code_span(name)} | {link} |")
    lines += ["", "### Vocabulary index", "", "| Vocabulary | Section |", "| --- | --- |"]
    for head in layout.headings.values():
        if head.level == 2 and head.number.startswith(VOCAB_CHAPTER_PREFIX):
            link = wikilink(flavor.section_dest(head.number, INDEX_NOTE), head.number).replace("|", r"\|")
            lines.append(f"| {flavor.escape(head.title)} | {link} |")
    tags = Counter(tag for note in layout.notes for tag in note_tags(note))
    lines += ["", "### Tag overview", "", "| Tag | Notes |", "| --- | --- |",
              *(f"| #{tag} | {count} |" for tag, count in sorted(tags.items()))]   # real tags, unescaped
    return "\n".join(lines) + "\n"


ATLAS_INTRO = ("Relationship diagrams of the STIX objects, from the relationship tables in their notes. Each arrow "
               "points from source to target and names the relationship types; a click on a node opens its note. "
               "Each diagram also sits at the end of its object's note.")


def build_atlas(layout: VaultLayout, facts: SpecFacts, types: list[str], *, source_sha256: str) -> str:
    """The atlas note: every diagram embedded by its block id, grouped by object chapter, in
    reading order. A group gets its heading only when it has diagrams (chapter 5 has none)."""
    front = {"title": ATLAS_NOTE, "kind": "atlas", "source_url": SOURCE_URL, "source_sha256": source_sha256,
             "aliases": ATLAS_ALIASES, "tags": ATLAS_TAGS, "cssclasses": [ATLAS_CSS_CLASS]}   # cssclasses last
    lines = [*_yaml_lines(front), ATLAS_INTRO]
    chapters = {t: facts.objects[t].section.split(".")[0] for t in types}
    if missing := sorted(t for t, chapter in chapters.items() if chapter not in ATLAS_GROUPS):
        raise StixMdError(EXIT_PARSE, f"no atlas group for {missing}")
    for chapter, group in ATLAS_GROUPS.items():
        members = [t for t in types if chapters[t] == chapter]
        lines += ["", f"## {group}"] if members else []
        for t in members:
            name = layout.by_id[layout.note_of(facts.objects[t].section)].name
            lines += ["", f"**[[{name}|{t}]]**", "", f"![[{name}#^{DIAGRAM_BLOCK_PREFIX}{t}]]"]
    return "\n".join(lines) + "\n"
