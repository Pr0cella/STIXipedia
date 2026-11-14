"""GFM provenance: the output equals the development reference once its generator line is restored."""
import hashlib
import json
import re
import unittest
from pathlib import Path

from stixmd import config
from stixmd.anchors import AnchorMap
from stixmd.assemble import assemble_single
from stixmd.flavor import GfmFlavor
from stixmd.layout import SingleLayout
from stixmd.model import Heading
from stixmd.parser import decode_html, parse_html
from stixmd.regions import apply_regions

DEVELOPMENT_REFERENCE_GENERATOR_LINE = 'generator: "scripts/stix2md.py 1.1.0"'    # the development reference value; lives only here
DEVELOPMENT_REFERENCE_FINGERPRINT = "3fe8d1f437d2bf423f5d75ca26e02ac8223f0198f14f53b1101064b1fad1f81e"


def build_gfm() -> str:
    """GFM built in memory from the committed source, with `generated_utc` blank."""
    raw = (config.PROJECT_ROOT / config.DEFAULT_INPUT).read_bytes()
    doc = parse_html(decode_html(raw))
    removed = apply_regions(doc)
    amap = AnchorMap.build(doc.blocks, removed, doc.link_pos)
    out = Path(config.DEFAULT_OUTPUT)
    layout = SingleLayout([b for b in doc.blocks if isinstance(b, Heading)], out.stem)
    corpus = assemble_single(doc, GfmFlavor(amap, layout), name=out.name,
                             source_sha256=hashlib.sha256(raw).hexdigest(), generated_utc="")
    return corpus[out.name]


class FingerprintTests(unittest.TestCase):
    def test_generator_format(self) -> None:
        self.assertRegex(config.GENERATOR, r"^stix2md \d+\.\d+(\.\d+)?$")

    def test_gfm_fingerprint(self) -> None:
        with self.assertLogs("stixmd", level="WARNING") as logs:
            text = build_gfm()
        self.assertEqual(len(logs.records), 1, logs.output)      # the one R5 link in 2.13
        line = f"generator: {json.dumps(config.GENERATOR)}"
        lines = text.split("\n")
        self.assertEqual(lines.count(line), 1)
        self.assertEqual(sum(config.GENERATOR in ln for ln in lines), 1)
        self.assertNotIn("scripts/stix2md.py", text)
        restored = "\n".join(DEVELOPMENT_REFERENCE_GENERATOR_LINE if ln == line else ln for ln in lines)
        self.assertIsNone(re.search(r'(?m)^generated_utc: "[^"]', restored))
        self.assertEqual(hashlib.sha256(restored.encode("utf-8")).hexdigest(), DEVELOPMENT_REFERENCE_FINGERPRINT)


if __name__ == "__main__":
    unittest.main()
