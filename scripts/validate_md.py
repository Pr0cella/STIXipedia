"""CLI: validate an existing output (file or vault folder) against the HTML spec."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import argparse  # noqa: E402
import traceback  # noqa: E402

from stix2md import FORMAT_CHOICES, run_validate_only, setup  # noqa: E402
from stixmd import config  # noqa: E402
from stixmd.config import EXIT_PARSE, StixMdError  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    """Validate an existing Markdown output; equivalent to stix2md.py --validate-only."""
    ap = argparse.ArgumentParser(description="Validate the Markdown output against the HTML spec.")
    ap.add_argument("--format", choices=FORMAT_CHOICES, default="gfm", help="output format; all = every format (default: gfm)")
    ap.add_argument("--input", default=config.DEFAULT_INPUT)
    ap.add_argument("--md", help="Markdown file, or vault folder, to check (default: per format)")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)
    setup(args.verbose)
    try:
        return run_validate_only(args.format, args.input, args.md)
    except StixMdError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return exc.exit_code
    except Exception:  # noqa: BLE001 - last-resort handler maps to EXIT_PARSE
        traceback.print_exc()
        return EXIT_PARSE


if __name__ == "__main__":
    sys.exit(main())
