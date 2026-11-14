"""Path safety, atomic writes and backups plus vault directories."""
import hashlib
import json
import os
import shutil
import stat
import time
from dataclasses import dataclass
from pathlib import Path

from .config import (EXIT_USAGE, EXIT_VALIDATION, MANIFEST_NAME, MAX_PATH_CHARS, OBSIDIAN_SETTINGS_DIR,
                     OUTPUT_DIR, PROJECT_ROOT, RENAME_PAUSE_S, RENAME_RETRIES, WINDOWS_RESERVED, StixMdError)

BAD_NAME_CHARS = frozenset('<>:"|?*') | frozenset(chr(i) for i in range(32))


def _fail(value: object, why: str) -> StixMdError:
    return StixMdError(EXIT_USAGE, f"unsafe path {str(value)!r}: {why}")


def _check_links(path: Path) -> None:
    """Reject if any existing component from PROJECT_ROOT down to path is a symlink/junction."""
    root = Path(os.path.normpath(PROJECT_ROOT))
    current = root
    for part in Path(os.path.normpath(path)).relative_to(root).parts:
        current = current / part
        if not os.path.lexists(current):
            break
        if current.is_symlink() or os.path.isjunction(current):
            raise _fail(path, f"{current} is a symlink or junction")


def _check(value: str | Path, subdir: str, suffixes: tuple[str, ...] | None, tmp_ok: bool = False) -> Path:
    """Validate a path; suffixes=None means a directory, whose name needs no suffix."""
    text = str(value)
    if not text or "\x00" in text:
        raise _fail(value, "empty or contains NUL")
    path = Path(text)
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    allowed = (PROJECT_ROOT / subdir).resolve()
    resolved = path.resolve(strict=False)
    lexical = Path(os.path.normpath(path))
    if not resolved.is_relative_to(allowed) or not lexical.is_relative_to(allowed):
        raise _fail(value, f"not inside {subdir or '.'}/")
    _check_links(lexical)
    _check_links(resolved)
    name = text.replace("\\", "/").split("/")[-1]       # as typed: Windows resolve() drops trailing dots
    if any(ch in BAD_NAME_CHARS for ch in name) or name != name.rstrip(" ."):
        raise _fail(value, "file name has a character Windows forbids (<>:\"|?* or control) or ends in '.'/' '")
    if not tmp_ok:
        if suffixes is not None and Path(name).suffix.lower() not in suffixes:
            raise _fail(value, f"suffix must be one of {suffixes}")
        if name.startswith("."):
            raise _fail(value, "hidden file names are not allowed")
    if name.lstrip(".").split(".")[0].strip(" ").lower() in WINDOWS_RESERVED:
        raise _fail(value, "Windows reserved device name")
    return resolved


def resolve_in_project(value: str | Path, *, subdir: str, suffixes: tuple[str, ...]) -> Path:
    """Validate a user-supplied path and return it resolved."""
    return _check(value, subdir, suffixes)


def derived(path: Path, new_name: str) -> Path:
    """Build a sibling of a validated path and validate it again."""
    if not new_name or Path(new_name).name != new_name:
        raise _fail(new_name, "derived name must be a bare file name")
    if not path.parent.is_relative_to(PROJECT_ROOT):
        raise _fail(path, "derived paths must stay inside the project")
    subdir = path.parent.relative_to(PROJECT_ROOT).as_posix()
    subdir = "" if subdir == "." else subdir
    tmp_ok = new_name == "." + path.name + ".tmp"
    return _check(path.parent / new_name, subdir, (path.suffix.lower(),), tmp_ok)


def atomic_write_text(path: Path, text: str) -> None:
    """Write UTF-8 text with LF endings to a temp sibling, then os.replace it into place."""
    tmp = derived(path, "." + path.name + ".tmp")
    try:
        tmp.unlink(missing_ok=True)             # stale temp file from a crashed run
        with open(tmp, "x", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def backup_file(src: Path, dst: Path) -> bool:
    """Copy src to dst, overwriting; return False if src does not exist."""
    if not src.is_file():
        return False
    shutil.copyfile(src, dst)
    return True


# --- vault directories -----------------------------------------------------------------------------

DIR_FORBIDDEN_ENDINGS = (".md", ".html", ".htm", ".bak", ".rejected", ".tmp")


@dataclass(frozen=True)
class VaultDirs:
    """The four sibling folders of one vault output, all direct children of markdown/."""
    out: Path
    bak: Path
    rejected: Path
    tmp: Path

    def names(self) -> set[str]:
        return {self.out.name, self.bak.name, self.rejected.name, self.tmp.name}


def _markdown_dir() -> Path:
    return (PROJECT_ROOT / OUTPUT_DIR).resolve()


def resolve_vault_dir(value: str | Path) -> VaultDirs:
    """Validate a vault folder path (a direct child of markdown/, no file suffix) and derive its siblings."""
    out = _check(value, OUTPUT_DIR, None)
    if out.parent != _markdown_dir():
        raise _fail(value, f"a vault must be a direct child of {OUTPUT_DIR}/")
    if out.name.lower().endswith(DIR_FORBIDDEN_ENDINGS):
        raise _fail(value, f"a vault folder name must not end in {DIR_FORBIDDEN_ENDINGS}")
    names = (out.name + ".bak", out.name + ".rejected", "." + out.name + ".tmp")
    bak, rejected, tmp = (_check(out.parent / n, OUTPUT_DIR, None, tmp_ok=n.startswith(".")) for n in names)
    return VaultDirs(out, bak, rejected, tmp)


def check_name(name: str, *, hidden_ok: bool = False) -> str:
    """Validate one generated folder or file name component (no separators, Windows-safe)."""
    if not name or name in (".", "..") or "/" in name or "\\" in name:
        raise _fail(name, "empty, dot or contains a separator")
    if any(ch in BAD_NAME_CHARS for ch in name) or name != name.rstrip(" ."):
        raise _fail(name, "name has a character Windows forbids or ends in '.'/' '")
    if name.startswith(".") and not hidden_ok:
        raise _fail(name, "hidden names are not allowed")
    if name.lstrip(".").split(".")[0].strip(" ").lower() in WINDOWS_RESERVED:
        raise _fail(name, "Windows reserved device name")
    return name


def _refuse(path: Path, why: str) -> StixMdError:
    return StixMdError(EXIT_USAGE, f"refusing to touch {path}: {why}")


def _is_link(path: Path) -> bool:
    return path.is_symlink() or os.path.isjunction(path)


def _assert_generated(path: Path, dirs: VaultDirs, *, need_manifest: bool) -> None:
    """Raise unless path is one of dirs, is free of links, and (if asked) carries the manifest."""
    lexical = Path(os.path.normpath(path))
    if lexical.parent != _markdown_dir() or lexical.resolve().parent != _markdown_dir():
        raise _refuse(path, f"not a direct child of {OUTPUT_DIR}/")
    if lexical.name not in dirs.names():
        raise _refuse(path, "not a vault, backup, rejected or temp folder of this output")
    if _is_link(lexical) or not lexical.is_dir():
        raise _refuse(path, "is a symlink, junction or not a folder")
    for root, dnames, fnames in os.walk(lexical, followlinks=False):
        for n in (*dnames, *fnames):
            if _is_link(Path(root) / n):
                raise _refuse(path, f"contains a symlink or junction: {Path(root) / n}")
    if need_manifest and not (lexical / MANIFEST_NAME).is_file():
        raise _refuse(path, f"has no {MANIFEST_NAME}, so stix2md did not generate it")


def _clear_readonly(func, path, exc) -> None:  # noqa: ANN001 - shutil.rmtree onexc signature
    os.chmod(path, stat.S_IWRITE)
    func(path)


def safe_rmtree(path: Path, dirs: VaultDirs) -> None:
    """Delete a folder this tool generated; a missing folder is a no-op, anything else refuses."""
    if not os.path.lexists(path):
        return
    _assert_generated(path, dirs, need_manifest=path != dirs.tmp)
    shutil.rmtree(path, onexc=_clear_readonly)


def write_tree(root: Path, corpus: dict[str, str]) -> None:
    """Write corpus {posix relpath: text} into a new folder root as UTF-8/LF; names are re-validated here."""
    root = Path(os.path.normpath(root))
    seen: set[str] = set()
    for rel in corpus:
        parts = rel.split("/")
        for part in parts:
            check_name(part, hidden_ok=rel == MANIFEST_NAME)
        if rel.casefold() in seen:
            raise _fail(rel, "two vault paths differ only in case")
        seen.add(rel.casefold())
        path = root.joinpath(*parts)
        if not Path(os.path.normpath(path)).is_relative_to(root) or path == root:
            raise _fail(rel, "escapes the vault folder")
        if len(str(path)) > MAX_PATH_CHARS:
            raise _fail(rel, f"full path is longer than {MAX_PATH_CHARS} characters")
    root.mkdir(exist_ok=False)
    for rel, text in corpus.items():
        path = root.joinpath(*rel.split("/"))
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "x", encoding="utf-8", newline="\n") as fh:
            fh.write(text)


def read_tree(root: Path) -> dict[str, str]:
    """Read every file under root (except .obsidian/) as {posix relpath: text}, sorted, newlines kept."""
    out: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root)
        if rel.parts[0] == OBSIDIAN_SETTINGS_DIR:
            continue
        if _is_link(path):
            raise _refuse(path, "symlink or junction inside a vault")
        if path.is_file():
            with open(path, encoding="utf-8", newline="") as fh:
                out[rel.as_posix()] = fh.read()
    return out


def changed_user_files(vault: Path) -> list[dict]:
    """Files of a generated vault that its manifest does not list, or whose hash differs."""
    try:
        manifest = json.loads((vault / MANIFEST_NAME).read_text("utf-8"))
        known = {f["path"]: f["sha256"] for f in manifest["files"]}
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise _refuse(vault, f"unreadable {MANIFEST_NAME}: {exc}") from exc
    changes = []
    for path in sorted(vault.rglob("*")):
        rel = path.relative_to(vault).as_posix()
        if not path.is_file() or rel == MANIFEST_NAME or rel.split("/")[0] == OBSIDIAN_SETTINGS_DIR:
            continue
        if rel not in known:
            changes.append({"path": rel, "change": "added"})
        elif hashlib.sha256(path.read_bytes()).hexdigest() != known[rel]:
            changes.append({"path": rel, "change": "edited"})
    return changes


def _rename(src: Path, dst: Path) -> None:
    """os.rename with retries: on Windows, Obsidian may briefly hold files open."""
    for attempt in range(RENAME_RETRIES + 1):
        try:
            os.rename(src, dst)
            return
        except OSError:
            if attempt == RENAME_RETRIES:
                raise
            time.sleep(RENAME_PAUSE_S)


def check_vault_dirs(dirs: VaultDirs) -> None:
    """Pre-flight before any write: every existing vault, backup or rejected folder must be one we generated."""
    for path in (dirs.out, dirs.bak, dirs.rejected):
        if os.path.lexists(path):
            _assert_generated(path, dirs, need_manifest=True)


def write_seed(settings: Path, seed: dict[str, str]) -> None:
    """Write the seed files {relpath under .obsidian/: text} into a new settings folder."""
    settings = Path(os.path.normpath(settings))
    for rel, text in seed.items():
        path = settings.joinpath(*(check_name(part) for part in rel.split("/")))
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "x", encoding="utf-8", newline="\n") as fh:
            fh.write(text)


def settings_report(old: Path, seed: dict[str, str]) -> list[dict]:
    """{path, status} of each settings file against the old vault's `.obsidian/`: seed files are
    "written", "unchanged" or "replaced", then every other old file is "dropped". `old` is link-free (pre-flight)."""
    found = {(Path(root) / n).relative_to(old).as_posix(): Path(root) / n
             for root, _, names in os.walk(old) for n in names}
    report = []
    for rel, text in seed.items():
        if rel in found:
            status = "unchanged" if found.pop(rel).read_bytes() == text.encode("utf-8") else "replaced"
        else:
            status = "replaced" if os.path.lexists(old.joinpath(*rel.split("/"))) else "written"
        report.append({"path": f"{OBSIDIAN_SETTINGS_DIR}/{rel}", "status": status})
    return report + [{"path": f"{OBSIDIAN_SETTINGS_DIR}/{rel}", "status": "dropped"} for rel in sorted(found)]


def swap_vault(dirs: VaultDirs, seed: dict[str, str] | None = None) -> tuple[list[dict], list[dict]]:
    """Move the validated tmp folder into place.

    Nothing of the old `.obsidian/` is carried over: the new one holds only the `seed` files, and the old one goes to
    the backup with the rest of the old vault. Returns the old vault's user changes and the
    settings report.
    """
    changes: list[dict] = []
    had_vault = os.path.lexists(dirs.out)
    if had_vault:
        _assert_generated(dirs.out, dirs, need_manifest=True)
        changes = changed_user_files(dirs.out)
    seeded = settings_report(dirs.out / OBSIDIAN_SETTINGS_DIR, seed or {})
    if seed:
        write_seed(dirs.tmp / OBSIDIAN_SETTINGS_DIR, seed)
    safe_rmtree(dirs.bak, dirs)
    if had_vault:
        try:
            _rename(dirs.out, dirs.bak)
        except OSError as exc:
            raise StixMdError(EXIT_VALIDATION, f"could not move {dirs.out.name} aside ({exc}); "
                              f"the new vault is kept in {dirs.tmp.name}") from exc
    try:
        _rename(dirs.tmp, dirs.out)
    except OSError as exc:
        if had_vault:
            _rename(dirs.bak, dirs.out)     # roll back: never leave the vault half-replaced
        raise StixMdError(EXIT_VALIDATION, f"could not move {dirs.tmp.name} into place ({exc}); "
                          f"the new vault is kept in {dirs.tmp.name}") from exc
    return changes, seeded


def reject_vault(dirs: VaultDirs) -> None:
    """Keep a failed vault for inspection as the .rejected folder."""
    safe_rmtree(dirs.rejected, dirs)
    _rename(dirs.tmp, dirs.rejected)
