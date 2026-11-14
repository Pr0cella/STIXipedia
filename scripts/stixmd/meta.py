"""META.md writer and file hashing."""
import hashlib
import json
import re
from pathlib import Path

from .config import EXIT_USAGE, StixMdError
from .safepath import atomic_write_text


def sha256_file(path: Path) -> str | None:
    """Return the hex SHA-256 of a file, or None if it does not exist."""
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _scalar(value: object) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, (dict, list)):        # only reached when empty
        return "{}" if isinstance(value, dict) else "[]"
    return json.dumps(str(value), ensure_ascii=False)


def _nested(value: object) -> bool:
    return isinstance(value, (dict, list)) and bool(value)


def to_yaml(value: object, indent: int = 0) -> list[str]:
    """Serialise dicts (2-space indent), lists ('- ') and scalars as block YAML lines."""
    pad = " " * indent
    lines: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            if _nested(item):
                lines += [f"{pad}{key}:", *to_yaml(item, indent + 2)]
            else:
                lines.append(f"{pad}{key}: {_scalar(item)}")
    elif isinstance(value, list):
        for item in value:
            if _nested(item):
                sub = to_yaml(item, indent + 2)
                lines += [f"{pad}- {sub[0].lstrip()}", *sub[1:]]
            else:
                lines.append(f"{pad}- {_scalar(item)}")
    else:
        lines.append(pad + _scalar(value))
    return lines


def _cell(value: object) -> str:
    return "" if value is None else str(value).replace("|", "\\|")


def _table(head: tuple[str, ...], rows: list[tuple]) -> list[str]:
    return ["| " + " | ".join(head) + " |", "| " + " | ".join("---" for _ in head) + " |",
            *("| " + " | ".join(_cell(v) for v in row) + " |" for row in rows)]


def _check_order(name: str) -> tuple[int, str]:
    match = re.fullmatch(r"V(\d+)(.*)", name)
    return (int(match[1]), match[2]) if match else (1 << 30, name)


def _matrix(outputs: dict, key: str) -> list[tuple]:
    """Rows of one table with a column per output format, e.g. validation checks or link rules."""
    names: dict[str, None] = {}
    for out in outputs.values():
        names.update(dict.fromkeys(((out.get(key) or {}).get("checks") if key == "validation"
                                    else out.get(key)) or {}))
    if key == "validation":                 # by check number, then suffix: V2, V2c, V3 ... V9, V10
        names = dict.fromkeys(sorted(names, key=_check_order))
    def cell(out: dict, name: str) -> object:
        data = (out.get(key) or {}).get("checks") if key == "validation" else out.get(key)
        return (data or {}).get(name)
    return [(name, *(cell(out, name) for out in outputs.values())) for name in names]


def render_meta(info: dict) -> str:
    """Return META.md text: YAML front matter plus readable tables, one column or row per output."""
    outputs: dict = info.get("outputs") or {}
    run_rows = [("run_utc", info.get("run_utc")), ("status", info.get("status")),
                ("exit_code", info.get("exit_code")),
                ("generator", f"{info.get('generator')} (Python {info.get('python')})")]
    run_rows += [(f"{fmt} status", f"{out.get('status')} (exit {out.get('exit_code')})"
                  + (f": {out['error']}" if out.get("error") else "")) for fmt, out in outputs.items()]
    src = info.get("source") or {}
    files = [("source", src.get("path"), src.get("bytes"), src.get("sha256"))]
    for fmt, out in outputs.items():
        for name in ("output", "backup", "rejected"):
            if entry := out.get(name):
                count = f" ({entry['files']} files)" if "files" in entry else ""
                files.append((f"{fmt} {name}{count}", entry.get("path"), entry.get("bytes"), entry.get("sha256")))
    user_files = [(fmt, f["path"], f["change"]) for fmt, out in outputs.items() for f in out.get("user_files") or []]
    seeds = [(fmt, f["path"], f["status"]) for fmt, out in outputs.items() for f in out.get("obsidian_seed") or []]
    head = tuple(outputs)
    lines = ["---", *to_yaml(info), "---", "", "# Run metadata", "", "## Run", "",
             *_table(("key", "value"), run_rows), "", "## Files", "",
             *_table(("file", "path", "bytes", "sha256"), files), "", "## Validation", "",
             *_table(("check", *head), _matrix(outputs, "validation")), "", "## Link resolution", "",
             *_table(("rule", *head), _matrix(outputs, "links"))]
    if user_files:
        lines += ["", "## User files not carried over (kept in the backup)", "",
                  *_table(("format", "path", "change"), user_files)]
    if seeds:
        lines += ["", "## Obsidian settings (written by every run)", "",
                  *_table(("format", "path", "status"), seeds)]
    return "\n".join(lines) + "\n"


def write_meta(path: Path, info: dict) -> None:
    """Write META.md (refuses a symlink target)."""
    if path.is_symlink():
        raise StixMdError(EXIT_USAGE, f"{path} is a symlink; refusing to write META")
    atomic_write_text(path, render_meta(info))
