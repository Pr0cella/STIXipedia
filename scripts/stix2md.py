"""CLI: convert the STIX 2.1 HTML spec to Markdown and an Obsidian vault."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import argparse  # noqa: E402
import hashlib  # noqa: E402
import json  # noqa: E402
import logging  # noqa: E402
import platform  # noqa: E402
import traceback  # noqa: E402
from datetime import UTC, datetime  # noqa: E402

from stixmd import config  # noqa: E402
from stixmd.anchors import AnchorMap, walk  # noqa: E402
from stixmd.assemble import assemble_single, assemble_vault  # noqa: E402
from stixmd.config import EXIT_OK, EXIT_PARSE, EXIT_USAGE, EXIT_VALIDATION, StixMdError  # noqa: E402
from stixmd.fetch import fetch_spec  # noqa: E402
from stixmd.facts import SpecFacts, build_facts  # noqa: E402
from stixmd.flavor import GfmFlavor  # noqa: E402
from stixmd.inline import plain_text  # noqa: E402
from stixmd.layout import SingleLayout, VaultLayout  # noqa: E402
from stixmd.meta import sha256_file, write_meta  # noqa: E402
from stixmd.model import CodeBlock, Document, Heading, Image, ListItem, Paragraph, Table  # noqa: E402
from stixmd.parser import decode_html, parse_html  # noqa: E402
from stixmd.regions import apply_regions  # noqa: E402
from stixmd.safepath import (VaultDirs, atomic_write_text, backup_file, check_vault_dirs, derived,  # noqa: E402
                             read_tree, reject_vault, resolve_in_project, resolve_vault_dir, safe_rmtree,
                             swap_vault, write_tree)
from stixmd.seed import obsidian_seed  # noqa: E402
from stixmd.validate import Report, validate_corpus  # noqa: E402

FORMATS = ("gfm", "vault")                # processing order for --format all
FORMAT_CHOICES = (*FORMATS, "all")
DEFAULT_OUTPUTS = {"gfm": config.DEFAULT_OUTPUT, "vault": config.DEFAULT_VAULT}
SinglePaths = tuple[Path, Path, Path, Path]             # input, output, backup, rejected
Target = SinglePaths | VaultDirs

log = logging.getLogger("stixmd")


def setup(verbose: bool) -> str:
    """Configure logging and replacement-safe console output; return the run timestamp."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    logging.basicConfig(level=logging.DEBUG if verbose else logging.INFO, stream=sys.stderr,
                        format="%(levelname)s %(message)s")
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def rel(path: Path) -> str:
    """Path relative to the project root, POSIX style."""
    try:
        return path.relative_to(config.PROJECT_ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def print_report(report: Report) -> None:
    """Print one line per check plus up to 20 indented failures."""
    for check in report.checks:
        stats = " ".join(f"{k}={v}" for k, v in check.stats.items())
        print(f"{'PASS' if check.passed else 'FAIL'} {check.id} {check.name} {stats}")
        for failure in check.failures[:20]:
            print(f"    {failure}")


def resolve_paths(inp_arg: str, out_arg: str) -> SinglePaths:
    """Validate input/output paths and derive backup and rejected paths."""
    inp = resolve_in_project(inp_arg, subdir=config.INPUT_DIR, suffixes=config.INPUT_SUFFIXES)
    out = resolve_in_project(out_arg, subdir=config.OUTPUT_DIR, suffixes=config.OUTPUT_SUFFIXES)
    return inp, out, derived(out, out.stem + ".bak.md"), derived(out, out.stem + ".rejected.md")


def resolve_targets(fmt: str, input_arg: str, output_arg: str | None, *, preflight: bool) -> dict[str, Target]:
    """Validate every output path of the selected formats before anything is read or written."""
    formats = list(FORMATS) if fmt == "all" else [fmt]
    if output_arg and len(formats) > 1:
        raise StixMdError(EXIT_USAGE, "--output is only allowed when exactly one format is selected")
    targets: dict[str, Target] = {}
    for f in formats:
        if f == "vault":
            dirs = resolve_vault_dir(output_arg or config.DEFAULT_VAULT)
            if preflight:
                check_vault_dirs(dirs)
            targets[f] = dirs
        else:
            targets[f] = resolve_paths(input_arg, output_arg or DEFAULT_OUTPUTS[f])
    return targets


def counts(doc: Document) -> dict:
    """Block counts of the kept document for META.md."""
    allb = list(walk(doc.blocks))
    heads = [b for b in allb if isinstance(b, Heading)]
    types = [b for b in doc.blocks
             if isinstance(b, Paragraph) and plain_text(b.spans).startswith(config.TYPE_NAME_LABEL)]
    return {"headings": len(heads), "tables": sum(isinstance(b, Table) for b in allb),
            "code_blocks": sum(isinstance(b, CodeBlock) for b in allb),
            "images": sum(isinstance(b, Image) for b in allb),
            "list_items": sum(isinstance(b, ListItem) for b in allb), "type_names": len(types),
            "vocabularies": sum(h.level == 2 and h.number.startswith(config.VOCAB_CHAPTER_PREFIX)
                                for h in heads)}


def file_info(path: Path, **extra: object) -> dict:
    """Path, size and hash of a file (None values if it is missing)."""
    return {"path": rel(path), "bytes": path.stat().st_size if path.is_file() else None,
            "sha256": sha256_file(path), **extra}


def vault_info(folder: Path) -> dict:
    """META entry of a vault folder: file count, total bytes and the manifest hash."""
    files = [p for p in folder.rglob("*") if p.is_file() and config.OBSIDIAN_SETTINGS_DIR not in p.parts]
    return {"path": rel(folder), "files": len(files), "bytes": sum(p.stat().st_size for p in files),
            "sha256": sha256_file(folder / config.MANIFEST_NAME)}


def backup_info(path: Path, made: bool) -> dict:
    """META entry of a backup: a vault backup folder as vault_info, else as file_info."""
    return {**(vault_info(path) if path.is_dir() else file_info(path)), "made": made}


def base_info(run_utc: str, inp: Path | None) -> dict:
    """META fields known before conversion."""
    sidecar = inp.with_suffix(".fetch.json") if inp else None
    fetched = json.loads(sidecar.read_text("utf-8")) if sidecar and sidecar.is_file() else {}
    root = config.PROJECT_ROOT / "scripts"
    scripts = sorted((p.relative_to(config.PROJECT_ROOT).as_posix(), p) for p in root.rglob("*.py")
                     if "__pycache__" not in p.parts and "evals" not in p.parts)    # evals/: local, not shipped
    return {"schema": "stix2md-meta/4", "run_utc": run_utc, "status": "failed", "exit_code": EXIT_PARSE,
            "generator": config.GENERATOR, "python": platform.python_version(),
            "scripts": [{"path": name, "sha256": sha256_file(p)} for name, p in scripts],
            "source": {**(file_info(inp) if inp else {"path": None, "bytes": None, "sha256": None}),
                       **{k: fetched.get(k) for k in ("url", "retrieved_utc", "http_last_modified", "etag")}},
            "outputs": {}}


def write_single(args: argparse.Namespace, fmt: str, paths: SinglePaths, doc: Document, amap: AnchorMap,
                 html: str, meta: dict, facts: SpecFacts | None) -> tuple[str, int, Report, dict, str]:
    """Assemble, validate and write the GFM file; return status, code, report, META, message."""
    _, out, bak, rej = paths
    heads = [b for b in doc.blocks if isinstance(b, Heading)]
    layout = SingleLayout(heads, out.stem)
    corpus = assemble_single(doc, GfmFlavor(amap, layout), name=out.name, **meta)
    md = corpus[out.name]
    report = validate_corpus(html, corpus, fmt)
    made = False
    if report.passed or args.force:
        made = backup_file(out, bak)
        atomic_write_text(out, md)
        rej.unlink(missing_ok=True)
        status, code = ("success" if report.passed else "forced"), EXIT_OK
    else:
        atomic_write_text(rej, md)
        status, code = "failed", EXIT_VALIDATION
    lines = out.read_text("utf-8").count("\n") if out.is_file() else None
    entry = {"output": file_info(out, lines=lines),
             "rejected": {"path": rel(rej), "sha256": sha256_file(rej)} if rej.is_file() else None,
             "backup": backup_info(bak, made)}
    target = rel(out) if status != "failed" else rel(rej)
    return status, code, report, entry, f"{len(heads)} sections → {target} (backup: {'made' if made else 'none'})"


def write_vault(args: argparse.Namespace, fmt: str, dirs: VaultDirs, doc: Document, amap: AnchorMap,
                html: str, meta: dict, facts: SpecFacts | None) -> tuple[str, int, Report, dict, str]:
    """Assemble the vault into tmp, validate it as read back from disk, then swap or reject."""
    split = config.SPLIT_LEVEL
    check_vault_dirs(dirs)
    corpus = assemble_vault(doc, amap, split, facts=facts, **meta)
    seed = obsidian_seed(VaultLayout((b for b in doc.blocks if isinstance(b, Heading)), split))
    safe_rmtree(dirs.tmp, dirs)
    write_tree(dirs.tmp, corpus)
    report = validate_corpus(html, read_tree(dirs.tmp), "vault", root=str(dirs.out))
    changes: list[dict] = []
    seeded: list[dict] = []
    made = dirs.out.is_dir()
    if report.passed or args.force:
        changes, seeded = swap_vault(dirs, seed=seed)
        safe_rmtree(dirs.rejected, dirs)
        status, code = ("success" if report.passed else "forced"), EXIT_OK
    else:
        reject_vault(dirs)
        made = False
        status, code = "failed", EXIT_VALIDATION
    entry = {"output": vault_info(dirs.out) if dirs.out.is_dir() else {"path": rel(dirs.out)},
             "rejected": vault_info(dirs.rejected) if dirs.rejected.is_dir() else None,
             "backup": backup_info(dirs.bak, made),
             "split_level": split, "user_files": changes, "obsidian_seed": seeded}
    for change in changes:
        log.warning("user file %s in the old vault was not carried over; it is kept in %s",
                    change["path"] + (" (edited)" if change["change"] == "edited" else ""), rel(dirs.bak))
    target = rel(dirs.out) if status != "failed" else rel(dirs.rejected)
    hubs = {f"{config.INDEX_NOTE}.md", f"{config.ATLAS_NOTE}.md"}
    notes = sum(p.endswith(".md") and p not in hubs for p in corpus)   # chapter and section notes only
    message = (f"{notes} notes (split level {split}), {len(corpus)} files "
               f"→ {target} (backup: {'made' if made else 'none'})")
    return status, code, report, entry, "\n".join([message, *seed_lines(dirs, seeded)])


def seed_lines(dirs: VaultDirs, seeded: list[dict]) -> list[str]:
    """Run-report lines: what happened to each file of the vault's .obsidian/."""
    backup = f"{rel(dirs.bak)}/{config.OBSIDIAN_SETTINGS_DIR}"
    forms = (("written", "seeded written: ", ""), ("replaced", "seeded replaced: ", f" (previous copies in {backup})"),
             ("unchanged", "seeded unchanged: ", ""), ("dropped", f"settings not carried over (kept in {backup}): ", ""))
    return [f"{head}{', '.join(paths)}{tail}" for status, head, tail in forms
            if (paths := [s["path"] for s in seeded if s["status"] == status])]


def _checks(report: Report) -> dict:
    return {"passed": report.passed, "checks": {
        c.id: "pass" if c.passed else f"fail: {c.stats.get('failures', len(c.failures))} failures"
        for c in report.checks}}


def overall_status(statuses: list[str]) -> str:
    """failed if any output failed, else forced if any was forced, else success."""
    return next((s for s in ("failed", "forced") if s in statuses), "success")


def convert(args: argparse.Namespace, run_utc: str, info: dict, inp: Path, targets: dict[str, Target]) -> int:
    """Parse once, then validate and write each format separately; write one META.md."""
    raw = inp.read_bytes()
    html = decode_html(raw)
    doc = parse_html(html)
    removed = apply_regions(doc)
    amap = AnchorMap.build(doc.blocks, removed, doc.link_pos)
    meta = {"source_sha256": hashlib.sha256(raw).hexdigest(), "generated_utc": run_utc}
    info["counts"] = counts(doc)
    facts = build_facts(doc) if "vault" in targets else None          # vault only
    several = len(targets) > 1
    for fmt, target in targets.items():
        amap.reset_stats()
        writer = write_vault if isinstance(target, VaultDirs) else write_single
        if several:
            print(f"== {fmt}")
        try:
            status, code, report, entry, message = writer(args, fmt, target, doc, amap, html, meta, facts)
        except StixMdError as exc:                       # e.g. a locked vault: report it, go on with the next
            print(f"error: {exc}", file=sys.stderr)
            info["outputs"][fmt] = {"status": "failed", "exit_code": exc.exit_code, "error": str(exc)}
            continue
        info["outputs"][fmt] = {"status": status, "exit_code": code, **entry, "validation": _checks(report),
                                "links": amap.stats}
        print_report(report)
        print(f"{status.upper() if status != 'success' else 'OK'} {fmt}: {message}")
    outputs = info["outputs"].values()
    info["status"] = overall_status([o["status"] for o in outputs])
    info["exit_code"] = max(o["exit_code"] for o in outputs)
    write_meta(config.PROJECT_ROOT / config.META_FILE, info)
    return info["exit_code"]


def validate_target(fmt: str, inp: Path, target: Target) -> Report:
    """Validate the existing output of one format; write nothing."""
    html = decode_html(inp.read_bytes())
    if isinstance(target, VaultDirs):
        if not target.out.is_dir():
            raise StixMdError(EXIT_USAGE, f"{rel(target.out)} does not exist")
        return validate_corpus(html, read_tree(target.out), "vault", root=str(target.out))
    out = target[1]
    if not out.is_file():
        raise StixMdError(EXIT_USAGE, f"{rel(out)} does not exist")
    return validate_corpus(html, {out.name: out.read_text("utf-8")}, fmt)


def run_validate_only(fmt: str, input_arg: str, output_arg: str | None) -> int:
    """Validate the existing output(s) of fmt ("all" for every format) against the input."""
    inp = resolve_in_project(input_arg, subdir=config.INPUT_DIR, suffixes=config.INPUT_SUFFIXES)
    targets = resolve_targets(fmt, input_arg, output_arg, preflight=False)
    code = EXIT_OK
    for f, target in targets.items():
        if len(targets) > 1:
            print(f"== {f}")
        report = validate_target(f, inp, target)
        print_report(report)
        code = max(code, EXIT_OK if report.passed else EXIT_VALIDATION)
    return code


def main(argv: list[str] | None = None) -> int:
    """Parse arguments, run, and map errors to exit codes."""
    ap = argparse.ArgumentParser(description="Convert the STIX 2.1 HTML spec to Markdown and an Obsidian vault.")
    ap.add_argument("--format", choices=FORMAT_CHOICES, default="gfm",
                    help="output format; all = gfm and vault (default: gfm)")
    ap.add_argument("--input", default=config.DEFAULT_INPUT)
    ap.add_argument("--output", help="output file, or folder for vault; only with a single format "
                                     "(default: per format, e.g. markdown/STIX2.1.md)")
    ap.add_argument("--fetch", action="store_true", help="download the spec from --url first")
    ap.add_argument("--url", default=config.SOURCE_URL)
    ap.add_argument("--force", action="store_true", help="write the output even if validation fails")
    ap.add_argument("--validate-only", action="store_true", help="validate the existing output only")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)
    if args.fetch and args.validate_only:
        ap.error("--fetch cannot be used with --validate-only")
    run_utc = setup(args.verbose)
    info: dict = {}
    try:
        inp = resolve_in_project(args.input, subdir=config.INPUT_DIR, suffixes=config.INPUT_SUFFIXES)
        targets = resolve_targets(args.format, args.input, args.output, preflight=not args.validate_only)
        if args.fetch:
            fetch_spec(args.url, inp)
        if args.validate_only:
            return run_validate_only(args.format, args.input, args.output)
        info = base_info(run_utc, inp)
        return convert(args, run_utc, info, inp, targets)
    except StixMdError as exc:
        print(f"error: {exc}", file=sys.stderr)
        code = exc.exit_code
    except Exception:  # noqa: BLE001 - last-resort handler maps to EXIT_PARSE
        traceback.print_exc()
        code = EXIT_PARSE
    if not args.validate_only:
        try:
            write_meta(config.PROJECT_ROOT / config.META_FILE,
                       {**(info or base_info(run_utc, None)), "status": "failed", "exit_code": code})
        except Exception as exc:  # noqa: BLE001 - META on the error path is best effort
            log.warning("could not write META.md: %s", exc)
    return code


if __name__ == "__main__":
    sys.exit(main())
