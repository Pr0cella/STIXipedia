"""The outputs of the real spec, built once per test run and shared by the test modules that need them."""
import functools
import hashlib
import logging
from types import SimpleNamespace

from stixmd import config
from stixmd import validate as v
from stixmd.anchors import AnchorMap
from stixmd.assemble import assemble_single, assemble_vault
from stixmd.facts import build_facts
from stixmd.flavor import GfmFlavor
from stixmd.layout import SingleLayout
from stixmd.model import Heading
from stixmd.parser import decode_html, parse_html
from stixmd.regions import apply_regions

GENERATED_UTC = "2026-10-18T00:00:00Z"


@functools.cache
def outputs() -> SimpleNamespace:
    """html, sha (the real source hash; V9 checks it), doc, gfm, vault2 (the vault, split level 2), facts (validator)."""
    raw = (config.PROJECT_ROOT / config.DEFAULT_INPUT).read_bytes()
    out = SimpleNamespace(html=decode_html(raw), sha=hashlib.sha256(raw).hexdigest())
    out.doc = parse_html(out.html)
    removed = apply_regions(out.doc)
    logging.disable(logging.WARNING)                                 # the one R5 warning of the real spec
    try:
        amap = AnchorMap.build(out.doc.blocks, removed, out.doc.link_pos)
    finally:
        logging.disable(logging.NOTSET)
    heads = [b for b in out.doc.blocks if isinstance(b, Heading)]
    meta = {"source_sha256": out.sha, "generated_utc": GENERATED_UTC}
    out.gfm = assemble_single(out.doc, GfmFlavor(amap, SingleLayout(heads, "STIX2.1")), name="STIX2.1.md",
                              **meta)["STIX2.1.md"]
    amap.reset_stats()
    out.vault2 = assemble_vault(out.doc, amap, config.SPLIT_LEVEL, facts=build_facts(out.doc), **meta)
    out.facts = v._HtmlFacts()
    out.facts.feed(out.html)
    out.facts.close()
    return out
