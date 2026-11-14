"""Starter files for the vault's .obsidian/ folder: graph, CSS snippet, appearance, bookmarks,
and app settings.

Every run writes them into a fresh .obsidian/. They are not part of the Corpus or the manifest.
"""
import json

from .config import (ATLAS_CSS_CLASS, ATLAS_NOTE, BOOKMARK_CTIME, BOOKMARK_GROUP, BOOKMARK_SECTIONS, CHAPTER_TAGS, CSS_SNIPPET,
                     GRAPH_COLORS, GRAPH_HIDDEN_CHAPTERS, INDEX_NOTE, OBJECT_CSS_CLASS, WIDE_CSS_CLASS)
from .flavor import heading_target
from .layout import VaultLayout

GRAPH_FILE = "graph.json"
SNIPPET_FILE = f"snippets/{CSS_SNIPPET}.css"
APPEARANCE_FILE = "appearance.json"
BOOKMARKS_FILE = "bookmarks.json"
APP_FILE = "app.json"

WIDE_CSS_RULE = f".{WIDE_CSS_CLASS} {{ --file-line-width: 1600px; }}"
W = f".{WIDE_CSS_CLASS}"
A = f".{ATLAS_CSS_CLASS}"
SNIPPET_CSS = f"""\
/* STIX 2.1 vault: wide notes, property tables, identifier links, atlas */

/* F4: wider notes */
{WIDE_CSS_RULE}

/* F6.1: identifier links keep the link colour (reading view) */
a.internal-link > code {{ color: var(--link-color); }}
a.internal-link:hover > code {{ color: var(--link-color-hover); text-decoration: underline; }}

/* F6.2: tables in wide notes */
{W} tbody tr:nth-child(even) {{ background-color: var(--background-secondary); }}
{W} td:first-child {{ min-width: 12em; }}

/* F6.3: long Properties blocks on object notes scroll */
.{OBJECT_CSS_CLASS} .metadata-container {{ max-height: 16em; overflow-y: auto; }}

/* F11: the relationship atlas uses the full width; a diagram that is still too wide shrinks to fit */
{A} {{ --file-line-width: 100%; }}
{A} .markdown-embed {{ border: none; padding: 0; }}
{A} .markdown-embed-content {{ max-height: none; }}
{A} .mermaid svg {{ max-width: 100%; height: auto; }}
"""                                                         # tool-free: the vault is used standalone


def _json(value: object) -> str:
    return json.dumps(value, indent=2, ensure_ascii=False) + "\n"


def _folder(layout: VaultLayout, chapter: str) -> str:
    return layout.note_of(chapter).split("/", 1)[0]


def graph_settings(layout: VaultLayout) -> dict:
    """Hide the hub notes and the atlas, colour the notes by chapter tag; Obsidian fills in its other defaults."""
    tags = {tag.removeprefix("stix/"): tag for tag in CHAPTER_TAGS.values()}
    search = " ".join([*(f'-path:"{_folder(layout, c)}"' for c in GRAPH_HIDDEN_CHAPTERS), f'-file:"{INDEX_NOTE}"',
                       f'-file:"{ATLAS_NOTE}"'])                    # the atlas, like the index
    groups = [{"query": f"tag:#{tags[key]}", "color": {"a": 1, "rgb": int(colour.lstrip("#"), 16)}}
              for key, colour in GRAPH_COLORS.items()]
    return {"search": search, "colorGroups": groups}


def bookmark(layout: VaultLayout, section: str) -> dict:
    """A file bookmark of the note that contains the section, plus the heading as `subpath` unless the section is
    the note root. Obsidian has no "heading" bookmark type: a heading bookmark is a file bookmark with a subpath."""
    item: dict = {"type": "file", "ctime": BOOKMARK_CTIME, "path": layout.note_of(section) + ".md"}
    if not layout.is_note_root(section):
        item["subpath"] = "#" + heading_target(layout.heading_of(section))
    return item


def bookmarks(layout: VaultLayout) -> dict:
    items = [bookmark(layout, s) for s in BOOKMARK_SECTIONS]
    return {"items": [{"type": "group", "ctime": BOOKMARK_CTIME, "items": items, "title": BOOKMARK_GROUP}]}


def obsidian_seed(layout: VaultLayout) -> dict[str, str]:
    """Seed files {path relative to .obsidian/: text}, in the order they are reported."""
    return {GRAPH_FILE: _json(graph_settings(layout)),
            SNIPPET_FILE: SNIPPET_CSS,
            APPEARANCE_FILE: _json({"enabledCssSnippets": [CSS_SNIPPET]}),
            BOOKMARKS_FILE: _json(bookmarks(layout)),
            APP_FILE: _json({"defaultViewMode": "preview"})}          # notes open in reading view
