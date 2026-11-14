# STIXipedia

![Python 3.12+](https://img.shields.io/badge/Python-3.12%2B-3776AB?logo=python&logoColor=white) ![STIX 2.1](https://img.shields.io/badge/STIX-2.1-005A9C) [![Code license: MIT](https://img.shields.io/badge/Code_license-MIT-green)](LICENSE)

The STIX 2.1 specification, easier to read and search. STIXipedia converts the
official OASIS HTML into an indexed Markdown file or an Obsidian vault, using
only the Python standard library.

## Purpose and function

The converter rebuilds headings, tables, examples and cross-references from the
source HTML, then checks the result against the source before saving it.

| Output | What you get |
| --- | --- |
| `markdown/STIX2.1.md` | One Markdown file with section anchors, type and vocabulary indexes, and line ranges for scripted lookups. |
| `markdown/STIX2.1-vault/` | An Obsidian vault with linked section notes, object and vocabulary catalogs, and relationship diagrams. |

Both include chapters 1–12 and Appendices A–E. Acknowledgments and revision
history are omitted; the source notices are provided separately in
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). `META.md` records source and
output hashes and validation results.

Features include:

- Section, type and vocabulary indexes; searchable line ranges in Markdown.
- Linked Obsidian notes with aliases, tags, navigation and definition links.
- Object, relationship and vocabulary catalogs, plus diagrams and a relationship atlas.
- Starter Obsidian graph settings, bookmarks and CSS for wide tables.
- Source-to-output validation, previous-output backups and rejected-candidate retention.
- Offline conversion, optional OASIS downloads and per-run provenance records.

## Usage

Requires **Python 3.12 or newer**. No packages to install. The source HTML is
included, so conversion works offline. Run these commands from the project root:

```sh
python -I scripts/stix2md.py                       # Markdown
python -I scripts/stix2md.py --format vault        # Obsidian
python -I scripts/stix2md.py --format all          # Both
python -I scripts/stix2md.py --format all --validate-only
```

To download a fresh copy from OASIS, add `--fetch` to a conversion command.
A failed validation leaves the existing output in place and saves the candidate
as `.rejected`, unless `--force` is used. With `--format all`, each format is
validated and saved independently.

For Obsidian, open `markdown/STIX2.1-vault/` as a vault and start with
`STIX 2.1 Index`. Catalogs use the **Bases** core plugin.

**Keep your working vault in a separate copy.** Regeneration replaces its notes
and settings. Only the previous output is retained in `.bak`.

### Converter flags

`python -I scripts/stix2md.py [options]`

| Flag | Purpose |
| --- | --- |
| `--format {gfm,vault,all}` | Select Markdown, Obsidian or both. Default: `gfm`. |
| `--input PATH` | Source HTML within `html/`, ending in `.html` or `.htm`. Default: `html/stix-v2.1-os.html`. |
| `--output PATH` | Override one output: a `.md` file within `markdown/`, or a vault folder directly under `markdown/`. Cannot be combined with `--format all`. Defaults are listed above. |
| `--fetch` | Download the source before conversion. Cannot be combined with `--validate-only`. |
| `--url URL` | Download URL used by `--fetch`. Default: the [OASIS source](https://docs.oasis-open.org/cti/stix/v2.1/os/stix-v2.1-os.html). Only HTTPS on `docs.oasis-open.org`, port 443, with an `.html` path is accepted; credentials, queries, fragments and redirects are refused. |
| `--validate-only` | Check existing outputs against the source without writing files. |
| `--force` | Save outputs despite failed validation and return success. Does not bypass other errors. |
| `-v`, `--verbose` | Enable debug logging. |
| `-h`, `--help` | Show command help. |

### Standalone validator

```sh
python -I scripts/validate_md.py --format all
```

This command only checks existing outputs. It accepts `--format`, `--input`,
`-v` / `--verbose` and `-h` / `--help` as above. Use **`--md PATH`** to select
a Markdown file or vault folder instead of `--output`; it requires a single
format. It has no download or force option.

Exit codes: `0` success (including forced output), `1` validation or vault-swap
failure, `2` usage/path error, `3` download error, `4` parse or unexpected error.
With both formats selected, the highest exit code is returned.

## Search programmatically

The single Markdown file is intended for scripts and agent tools. Its Contents,
Type and Vocabulary indexes give **1-based, inclusive line ranges**. Each section
also has a stable anchor such as `sec-4.1`; its range includes subsections.

This Python example searches by keyword, then retrieves section 4.1 using its
index entry:

```python
import re
from pathlib import Path

text = Path("markdown/STIX2.1.md").read_text(encoding="utf-8")
lines = text.splitlines()
query = "indicator"
for number, line in enumerate(lines, 1):
    if query.casefold() in line.casefold():
        print(f"{number}: {line}")

section = "4.1"
entry = re.search(
    rf"(?m)^\s*- \[[^\n]+\]\(#sec-{re.escape(section)}\) · L(\d+)-L(\d+)$",
    text,
)
if entry is None:
    raise LookupError(f"Section {section} not found")
start, end = map(int, entry.groups())
print("\n".join(lines[start - 1:end]))
```

Read ranges from the current file after each regeneration. In the vault, notes
also expose `section`, `stix_types` and vocabulary information in front matter.

## Checks

```sh
python -I -m unittest discover -s scripts/tests -t scripts -v
```

The converter is specific to the published STIX 2.1 HTML, rather than a general
HTML-to-Markdown tool. One broken cross-reference in source section 2.13 is left
as plain text and reported as a warning.

## License and source

Original project code and documentation use the [MIT license](LICENSE).
The specification and content derived from it retain the terms in
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md); keep that file with redistributed
conversions.

STIXipedia is an independent reading aid. The [OASIS STIX 2.1 specification](https://docs.oasis-open.org/cti/stix/v2.1/os/stix-v2.1-os.html) is the source of record.
