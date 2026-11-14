"""Constants, StixMdError, norm_style, md_url and sanitise_name."""
import re
from pathlib import Path

GENERATOR = "stix2md 0.4"     # written as is in every output and META.md
PROJECT_ROOT = Path(__file__).resolve().parents[2]          # .../STIX

SOURCE_URL = "https://docs.oasis-open.org/cti/stix/v2.1/os/stix-v2.1-os.html"
BASE_URL = SOURCE_URL                                        # for urljoin of images
ALLOWED_FETCH_HOST = "docs.oasis-open.org"
FETCH_TIMEOUT_S = 60                                         # per socket operation
FETCH_DEADLINE_S = 300                                       # whole download
FETCH_MAX_BYTES = 20 * 1024 * 1024
SOURCE_ENCODING = "cp1252"
EXPECTED_TITLE = "STIX Version 2.1"

INPUT_DIR = "html"
OUTPUT_DIR = "markdown"
DEFAULT_INPUT = "html/stix-v2.1-os.html"
DEFAULT_OUTPUT = "markdown/STIX2.1.md"
META_FILE = "META.md"
INPUT_SUFFIXES = (".html", ".htm")
OUTPUT_SUFFIXES = (".md",)
DEFAULT_VAULT = "markdown/STIX2.1-vault"
MANIFEST_NAME = ".stix2md-manifest.json"  # marker + manifest of a generated vault
OBSIDIAN_SETTINGS_DIR = ".obsidian"       # written afresh by every run, old copy in the backup
MAX_PATH_CHARS = 240                      # full path length limit for vault files
SPLIT_LEVEL = 2                           # vault: one note per chapter and section
INDEX_NOTE = "STIX 2.1 Index"             # vault root note
NAV_PREFIX = f"[[{INDEX_NOTE}|Index]]"    # every note's NAV line starts with this
CHILD_LIST_LABEL = "**Sections in this chapter**"   # label of a chapter note's child-note list
VAULT_SCHEMA = "stix2md-vault/1"
CHAPTER_TAGS = {"1": "stix/intro", "2": "stix/data-type", "3": "stix/concept", "4": "stix/sdo", "5": "stix/sro",
                "6": "stix/sco", "7": "stix/smo", "8": "stix/bundle", "9": "stix/patterning",
                "10": "stix/vocabulary", "11": "stix/customization", "12": "stix/conformance"}
APPENDIX_TAG = "stix/appendix"
DEPRECATED_TAG = "stix/deprecated"
RENAME_RETRIES = 3                        # directory swap: Obsidian may hold files open on Windows
RENAME_PAUSE_S = 0.5
WINDOWS_RESERVED = {"con", "prn", "aux", "nul", "conin$", "conout$",
                    *(f"com{i}" for i in [*range(1, 10), "¹", "²", "³"]),
                    *(f"lpt{i}" for i in [*range(1, 10), "¹", "²", "³"])}

HEADER_BG = "background:#073763"          # compare against normalised style
CODE_FONTS = ("consolas", "courier")      # substring of normalised font-family value
CODE_LINE_SIZE = "font-size:9.0pt"
LIST_SPACER = "font:7.0pt"
BULLET_L1 = "\u25cf"                      # ●
BULLET_L2 = "\u25cb"                      # ○
LEVEL2_MARGIN = "margin-left:1.0in"
NEG_INDENT = "text-indent:-"
LETTER_MARKER_RE = r"^([a-z])\)\s*"
ZWSP = "\u200b"
NBSP = "\u00a0"
VOID_TAGS = frozenset({"br", "img", "hr", "meta", "base", "link", "input", "col", "wbr", "area"})

HEADING_RES = (                           # tried in this order → (number, title)
    r"^Appendix ([A-Z])\.\s+(.+)$",
    r"^([A-Z](?:\.\d+)+)\s+(.+)$",
    r"^(\d+(?:\.\d+)*)\s+(.+)$",
)
OBS_HEADING_UNSAFE_RE = r"%%|\[\[|\]\]|[#|^:]"   # unsafe in Obsidian heading links; replaced by spaces
SECTION_NUMBER_RE =r"^(?:\d+(?:\.\d+)*|[A-Z](?:\.\d+)*)$"
STRIP_H1_PREFIXES = ("Notices", "Appendix F.", "Appendix G.")
COVER_LABEL_RE = r"^[A-Z][A-Za-z ]*:$"
COVER_STAGE_LABELS = ("This stage:", "Previous stage:", "Latest stage:")
COVER_ABSTRACT_LABEL = "Abstract:"
REF_LABEL_RE = r"^\[([^\]]+)\]$"
REFERENCES_APPENDIX = "E"
MAX_SPAN = 64                             # larger colspan/rowspan is treated as corrupt input
CODE_CELL_FENCE_MIN_LINES = 5             # a table with a code cell this long is rendered as records
TYPE_NAME_LABEL = "Type Name:"
VOCAB_CHAPTER_PREFIX = "10."
EXPECTED_KEPT_HEADINGS = 309
EXPECTED_TYPE_NAMES = 75                  # 4.14 has <b>Type Name</b>: too
EXPECTED_VOCABS = 32
# V2: the only sections whose Markdown has tokens beyond the HTML's (repeated table cells), the
# same in every format; any other surplus is duplicated content
V2_EXTRA_TOKENS = {
    "1.3.1": {"1": 1, "2": 1, "3": 1, "4": 1, "5": 1, "6": 1, "7": 1, "8": 1, "9": 1},
    "3.6.2": {"action": 14, "creator": 6, "object": 20, "received": 4, "recipient": 4, "step": 14, "stix": 14,
              "user": 4},
    "7.3": {"1": 1, "2": 1, "3": 1},
    "12.1": {"1": 2, "2": 2, "3": 2, "4": 2, "5": 1, "6": 1, "7": 1},
}
R4B_LINK = ("1.2.12", "TAXII", "https://docs.oasis-open.org/cti/taxii/v2.1/taxii-v2.1.html")   # V4

# Spec facts for the vault: labels, bands and the counts build_facts must reproduce
VOCAB_NAME_LABEL = "Vocabulary Name:"
ENUM_NAME_LABEL = "Enumeration Name:"
PROPERTY_BANDS = {"Required Common Properties": "required", "Optional Common Properties": "optional",
                  "Not Applicable Common Properties": "not_applicable", "ID Contributing Properties": "id_contributing"}
SPECIFIC_BAND_SUFFIX = "Specific Properties"          # "<Object> Specific Properties"
VALUE_HEADER_SUFFIX = "Value"                         # "Vocabulary Value", "Enumeration Value", "Value"
REL_TYPE_HEADERS = ("Relationship Type", "Name")      # 4.12.2 says "Name"
REVERSE_BAND = "Reverse Relationships"
ALL_SCO_TARGET = "<All STIX Cyber-observable Objects>"
RELATIONSHIP_APPENDIX = "B"
EXPECTED_IDENTS = {"type": 75, "vocabulary": 23, "enumeration": 9}
EXPECTED_DERIVED_TYPES = ("language-content", "extension-definition")       # derived types: no "Type Name:" line
EXPECTED_OBJECTS = {"4": 19, "5": 2, "6": 18, "7": 3}                        # per chapter: SDO, SRO, SCO, SMO
EXPECTED_ID_CONTRIBUTING = 18
EXPECTED_FORWARD_TABLES = 14
EXPECTED_TRIPLES = 120
EXPECTED_REL_TYPES = 32
EXPECTED_APPENDIX_B_TRIPLES = 110
EXPECTED_VALUE_TABLES = 32                            # one value table per vocabulary and enumeration
APPENDIX_B_ONLY = frozenset({("malware", "exfiltrate-to", "infrastructure")})               # a typo in Appendix B
FORWARD_ONLY = frozenset({
    ("course-of-action", "remediates", "malware"), ("course-of-action", "remediates", "vulnerability"),
    ("domain-name", "resolves-to", "domain-name"), ("domain-name", "resolves-to", "ipv4-addr"),
    ("domain-name", "resolves-to", "ipv6-addr"), ("ipv4-addr", "belongs-to", "autonomous-system"),
    ("ipv4-addr", "resolves-to", "mac-addr"), ("ipv6-addr", "belongs-to", "autonomous-system"),
    ("ipv6-addr", "resolves-to", "mac-addr"), ("malware", "exfiltrates-to", "infrastructure"),
    ("tool", "uses", "infrastructure")})
ID_LINK_SKIP: frozenset[tuple[str, str]] = frozenset()   # (section, identifier) spans that name a property
ALL_SCO_SECTION = "6"                                     # the note that ALL_SCO_TARGET links to
PROPERTY_KEYS = ("required", "optional", "not_applicable", "specific", "id_contributing")   # -> <kind>_properties
OBJECTS_BASE, RELATIONSHIPS_BASE, VOCABULARIES_BASE = "STIX Objects", "STIX Relationships", "STIX Vocabularies"
BASE_SUFFIX = ".base"                                     # Bases core plugin, Obsidian >= 1.9
BASES_EXAMPLE_TYPE = "location"                           # the index's filter example: what can target a location?
HOW_TO_SECTION = "4.1"                                    # how-to examples: the note holding 4.1,
HOW_TO_CITATION = "rfc8259"                               # the citation [RFC8259] and the identifier `identifier`;
HOW_TO_IDENTIFIER = "identifier"                          # every example is built from the Layout at run time
# The vault is used standalone (e.g. in its own git repository): its how-to explains the
# notes, never the tool. V17 rejects a how-to line containing any of these (case-insensitive).
HOW_TO_TOOL_TERMS = ("stix2md", "--split-level", "regenerat", ".bak", "run report", "each run", "seeded", "hint:",
                     "readme", "split level", "split_level")    # one split level only

# Wide notes and .obsidian/ seed files
WIDE_CSS_CLASS = "stix-wide"                              # not "wide", which some themes define
WIDE_TABLE_MIN_COLUMNS = 3                                # a vault note with a table this wide gets WIDE_CSS_CLASS
OBJECT_CSS_CLASS = "stix-object"                          # the notes that own one object
ATLAS_CSS_CLASS = "stix-atlas"                            # full width, diagrams shrink to fit
CSS_SNIPPET = "stix"                                      # .obsidian/snippets/stix.css
GRAPH_HIDDEN_CHAPTERS = ("2", "B", "E")                   # hub folders the graph filter hides (with the index note)
GRAPH_COLORS = {"sdo": "#e8590c", "sro": "#7048e8", "sco": "#1c7ed6", "smo": "#2f9e44",
                "vocabulary": "#f59f00", "patterning": "#d6336c"}                  # tag stix/<key> -> colour
BOOKMARK_GROUP = "STIX 2.1"
BOOKMARK_SECTIONS = ("3.2", "2.9", "1.2.7")               # Common Properties, Identifier, ANTLR grammar
BOOKMARK_CTIME = 1623283200000                            # the spec date 2021-06-10, so the seed is deterministic

# Relationship diagrams and the atlas note
DIAGRAM_LABEL = "**Relationship diagram**"                # diagram: label, blank line, mermaid fence, block-id line
DIAGRAM_FENCE = "```mermaid"
DIAGRAM_BLOCK_PREFIX = "stix-rel-"                        # block id ^stix-rel-<type> after the closing fence
ATLAS_NOTE = "STIX Relationship Atlas"                    # vault root, next to the index note
ATLAS_ALIASES = ["Relationship Atlas"]
ATLAS_TAGS = ["stix", "stix/atlas"]
ATLAS_GROUPS = {"4": "Domain objects", "5": "Relationship objects", "6": "Cyber-observable objects"}
#   one heading per object chapter, written only when it has diagrams (chapter 5 has none)

EXIT_OK, EXIT_VALIDATION, EXIT_USAGE, EXIT_FETCH, EXIT_PARSE = 0, 1, 2, 3, 4


class StixMdError(Exception):
    """Error carrying the process exit code."""
    def __init__(self, exit_code: int, message: str) -> None:
        super().__init__(message)
        self.exit_code = exit_code


def norm_style(style: str | None) -> str:
    """Remove all whitespace from a CSS style string and lowercase it."""
    return "".join((style or "").split()).lower()


def md_url(href: str) -> str:
    """Markdown link target: href stripped, with space, '(' and ')' percent-encoded."""
    return href.strip().replace(" ", "%20").replace("(", "%28").replace(")", "%29")


def sanitise_name(text: str) -> str:
    """Vault folder/note name from heading text; callers reject an empty result."""
    text = text.replace("/", "-").replace("\\", "-").replace(":", " -")
    text = re.sub(r'[*"<>|?#^\[\]]', "", text)
    return " ".join(text.split()).rstrip(" .")
