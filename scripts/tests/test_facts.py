"""Unit tests for stixmd.facts: identifiers, objects, relationships, vocabularies."""
import unittest

from stixmd import config
from stixmd.config import ALL_SCO_TARGET, APPENDIX_B_ONLY, EXIT_PARSE, FORWARD_ONLY, NBSP, StixMdError
from stixmd.facts import build_facts, code_values, collect_facts
from stixmd.model import Cell, Document, Heading, Paragraph, Span, Table
from stixmd.parser import decode_html, parse_html
from stixmd.regions import apply_regions


def H(level: int, number: str, title: str) -> Heading:
    text = f"Appendix {number}. {title}" if number.isalpha() else f"{number} {title}"
    return Heading(level=level, number=number, title=title, text=text)


def S(text: str) -> Span:
    return Span(text=text)


def C(text: str, **flags: bool) -> Span:
    return Span(text=text, code=True, **flags)


def BS(text: str) -> Span:
    return Span(text=text, bold=True)


def P(*spans: Span) -> Paragraph:
    return Paragraph(spans=list(spans))


def cell(*paragraphs: Paragraph, header: bool = False, colspan: int = 1) -> Cell:
    return Cell(blocks=list(paragraphs), is_header=header, colspan=colspan)


def band(label: str) -> list[Cell]:
    return [cell(P(BS(label)), header=True, colspan=3)]


def listed(*names: str) -> Paragraph:
    spans: list[Span] = []
    for name in names:
        spans += [S(", ")] if spans else []
        spans.append(C(name, bold=True))
    return P(*spans)


def label(text: str, name: str) -> Paragraph:
    return P(BS(text), S(" "), C(name))


def type_row(name: str, *, code: bool = True) -> list[Cell]:
    first = C("type", bold=True) if code else BS("type")
    return [cell(P(first, S(" (required)"))), cell(P(C("string"))),
            cell(P(S("The value of this property "), BS("MUST"), S(" be "), C(name), S(".")))]


def property_table(specific: str, *, id_contributing: list[Paragraph] | None = None,
                   rows: list[list[Cell]] = ()) -> Table:
    table = [band("Required Common   Properties"), [cell(P(C("type, id", bold=True)))],
             band("Optional Common Properties"), [cell(listed("spec_version"))],
             band("Not Applicable Common Properties"), [cell(listed("defanged"))],
             band(f"{specific} Specific Properties"), [cell(listed("name"))]]
    if id_contributing is not None:
        table += [band("ID Contributing Properties"), [cell(*id_contributing)]]
    return Table(rows=[*table, *rows])


def rel_header(middle: str = "Relationship Type") -> list[Cell]:
    return [cell(P(BS(text)), header=True) for text in ("Source", middle, "Target ", "Description")]


def rel_row(*cells: list[Paragraph]) -> list[Cell]:
    return [cell(*paragraphs) for paragraphs in cells] + [cell(P(S("Text.")))]


def sample() -> Document:
    """A small spec with every variation build_facts must handle."""
    malware_props = property_table("Malware", rows=[
        [cell(P(BS("Property Name")), header=True), cell(P(BS("Type")), header=True), cell(P(BS("Description")))],
        type_row("malware", code=False),                                     # 6.3.1: bold, not code
        [cell(P(C("malware_types", bold=True))), cell(P(C("list"))),
         cell(P(S("Values come from the "), C("malware-type-ov"), S(" vocabulary.")))]])
    malware_props.rows[7] = [cell(P(C("name", bold=True), S(", "), C("architecture_", bold=True),
                                    C("execution_envs", bold=True)))]        # adjacent spans are one value
    malware_rels = Table(rows=[
        band("Embedded Relationships"), [cell(listed("created_by_ref")), cell(P(C("identity")))],
        rel_header("Name"),                                                  # 4.12.2 header variant
        rel_row([P(C("malware"))], [P(C("uses, targets"))], [P(C("tool"), S(", ")), P(C("location"))]),
        rel_row([P(C("malware"))], [P(C("consists-of"))],
                [P(C("<All "), C("STIX Cyber-observable Objects", italic=True), C(">"))]),
        band("Reverse Relationships"),
        rel_row([P(C("tool"))], [P(C("drops"))], [P(C("malware"))])])
    tool_props = property_table("Tool", id_contributing=[
        P(C("name", bold=True), S(", "), C("version", bold=True), S(" (all items "), BS("MUST"), S(" be included)")),
        P(S("If "), C("name", bold=True), S(" is present, …"))], rows=[type_row("tool")])
    ext_props = property_table("Extension Definition")
    ext_types = Table(rows=[[cell(P(BS("Property Name"))), cell(P(BS("Data Type"))), cell(P(BS("Details")))],
                            type_row("extension-definition")])
    vocab_table = Table(rows=[[cell(P(BS("Vocabulary   Summary")), header=True, colspan=2)],
                              [cell(P(C("a"), S(", "), C("b")), colspan=2)],              # the summary isn't used
                              [cell(P(BS("Vocabulary   Value")), header=True), cell(P(BS("Description")), header=True)],
                              *([cell(P(C(v))), cell(P(S("Text.")))] for v in ("a", "b", "b-1", "b-2"))])
    enum_table = Table(rows=[[cell(P(BS("Value")), header=True), cell(P(BS("Description")), header=True)],
                             *([cell(P(C(v))), cell(P(S("Text.")))] for v in ("x", "y"))])
    summary_b = Table(rows=[[cell(P(BS(t)), header=True) for t in ("Source", "Type", "Target")],
                            [cell(P(C("malware"))), cell(P(C("uses"))), cell(P(C("tool")))],
                            [cell(P(C("malware"))), cell(P(C("exfiltrate-to"))), cell(P(C("tool")))]])
    return Document(blocks=[
        P(S("cover text")),
        H(1, "4", "STIX Domain Objects"),
        H(2, "4.1", "Malware"), label("Type Name:", "malware"),
        H(3, "4.1.1", "Properties"), malware_props,
        H(3, "4.1.2", "Relationships"), malware_rels,
        H(2, "4.2", "Tool"), label("Type Name:", "tool"),
        H(3, "4.2.1", "Properties"), tool_props,
        H(3, "4.2.2", "Relationships"),
        Table(rows=[rel_header(), rel_row([P(S("—"))], [P(S("—"))], [P(S("—"))])]),
        H(1, "7", "STIX Meta Objects"),
        H(2, "7.3", "Extension Definition"), P(S("No label line here.")),
        H(3, "7.3.1", "Extension Definition Properties"), ext_props, ext_types,
        H(1, "10", "STIX Vocabularies"),
        H(2, "10.1", "Malware Type Vocabulary"), label("Vocabulary Name:", "malware-type-ov"), vocab_table,
        H(2, "10.2", "X Enumeration"), label("Enumeration Name:", "x-enum"), enum_table,
        H(1, "B", "Relationship Summary Table"), summary_b])


class CodeValueTests(unittest.TestCase):
    def test_runs_and_comma_lists(self) -> None:
        self.assertEqual(code_values([C("<All "), C("STIX Cyber-observable Objects", italic=True), C(">")]),
                         [ALL_SCO_TARGET])
        self.assertEqual(code_values([C("location, vulnerability")]), ["location", "vulnerability"])
        self.assertEqual(code_values([C("a"), S(", "), C(NBSP), S(" and "), C("tool ")]), ["a", "tool"])
        self.assertEqual(code_values([S("no code")]), [])


class CollectTests(unittest.TestCase):
    def setUp(self) -> None:
        self.facts = collect_facts(sample())

    def test_identifiers(self) -> None:
        got = [(i.name, i.kind, i.section, i.derived) for i in self.facts.idents.values()]
        self.assertEqual(got, [("malware", "type", "4.1", False), ("tool", "type", "4.2", False),
                               ("extension-definition", "type", "7.3", True),         # derived: from the `type` row
                               ("malware-type-ov", "vocabulary", "10.1", False),
                               ("x-enum", "enumeration", "10.2", False)])

    def test_objects(self) -> None:
        malware, tool, ext = (self.facts.objects[t] for t in ("malware", "tool", "extension-definition"))
        self.assertEqual(list(self.facts.objects), ["malware", "tool", "extension-definition"])
        self.assertEqual((malware.section, malware.required, malware.optional, malware.not_applicable),
                         ("4.1", ["type", "id"], ["spec_version"], ["defanged"]))
        self.assertEqual(malware.specific, ["name", "architecture_execution_envs"])
        self.assertEqual(malware.id_contributing, [])
        self.assertEqual(tool.id_contributing, ["name", "version"])            # prose after the list isn't a value
        self.assertEqual(ext.section, "7.3")

    def test_relationships(self) -> None:
        self.assertEqual(self.facts.objects["malware"].relationships,
                         [("uses", "tool"), ("uses", "location"), ("targets", "tool"), ("targets", "location"),
                          ("consists-of", ALL_SCO_TARGET)])                    # the reverse row is ignored
        self.assertEqual(self.facts.objects["tool"].relationships, [])
        self.assertEqual(self.facts.forward_tables, ["4.1.2"])                 # a "—" table isn't one
        self.assertEqual(self.facts.triples()[0], ("malware", "uses", "tool"))

    def test_vocabularies(self) -> None:
        vocab, enum = self.facts.vocabularies["malware-type-ov"], self.facts.vocabularies["x-enum"]
        self.assertEqual((vocab.kind, vocab.values, vocab.used_by),
                         ("vocabulary", ["a", "b", "b-1", "b-2"], ["malware"]))
        self.assertEqual((enum.kind, enum.values, enum.used_by), ("enumeration", ["x", "y"], []))
        self.assertEqual(self.facts.objects["malware"].vocabularies, ["malware-type-ov"])

    def test_appendix_b(self) -> None:
        self.assertEqual(self.facts.appendix_b, {("malware", "uses", "tool"), ("malware", "exfiltrate-to", "tool")})

    def test_counts_are_checked(self) -> None:
        with self.assertRaises(StixMdError) as cm:
            build_facts(sample())
        self.assertEqual(cm.exception.exit_code, EXIT_PARSE)
        self.assertIn("OBSIDIAN-1.1 §3", str(cm.exception))


class ErrorTests(unittest.TestCase):
    def fails(self, doc: Document, text: str) -> None:
        with self.assertRaises(StixMdError) as cm:
            collect_facts(doc)
        self.assertEqual(cm.exception.exit_code, EXIT_PARSE)
        self.assertIn(text, str(cm.exception))

    def test_prose_outside_id_contributing(self) -> None:
        doc = sample()
        doc.blocks[5].rows[3] = [cell(P(C("spec_version", bold=True), S(" (see below)")))]
        self.fails(doc, "is not a list of code spans")

    def test_label_line_elsewhere(self) -> None:
        doc = sample()
        doc.blocks[3] = P(S("Malware text."))
        doc.blocks[9] = label("Type Name:", "malware")                         # in 4.2, not in 4.1
        self.fails(doc, "label line in 4.2")

    def test_forward_source_must_be_the_object(self) -> None:
        doc = sample()
        doc.blocks[7].rows[3][0] = cell(P(C("tool")))
        self.fails(doc, "is not the object")


class SpecTests(unittest.TestCase):
    """The real spec reproduces every expected count in config (build_facts checks them) and these spot values."""

    @classmethod
    def setUpClass(cls) -> None:
        doc = parse_html(decode_html((config.PROJECT_ROOT / config.DEFAULT_INPUT).read_bytes()))
        apply_regions(doc)
        cls.facts = build_facts(doc)

    def test_identifiers(self) -> None:
        idents = self.facts.idents
        self.assertEqual(len(idents), 109)
        self.assertEqual(idents["marking-definition"].section, "7.2.1")
        self.assertEqual([(i.name, i.section) for i in idents.values() if i.derived],
                         [("language-content", "7.1"), ("extension-definition", "7.3")])
        self.assertEqual(idents["malware-type-ov"].section, "10.15")

    def test_objects(self) -> None:
        objects = self.facts.objects
        self.assertEqual(len(objects), 42)
        self.assertEqual((objects["marking-definition"].section, objects["relationship"].section), ("7.2.1", "5.1"))
        self.assertIn("architecture_execution_envs", objects["malware"].specific)
        self.assertEqual(objects["file"].id_contributing, ["hashes", "name", "extensions", "parent_directory_ref"])
        self.assertEqual(objects["windows-registry-key"].id_contributing, ["key", "values"])
        self.assertEqual(objects["extension-definition"].required,
                         ["type", "spec_version", "id", "created", "modified", "created_by_ref"])
        self.assertEqual(objects["directory"].type, "directory")              # bold, non-code `type` row

    def test_relationships(self) -> None:
        self.assertEqual(self.facts.forward_tables, ["4.1.2", "4.2.2", "4.3.2", "4.5.2", "4.7.2", "4.8.2", "4.9.2",
                                                     "4.11.2", "4.12.2", "4.17.2", "4.18.2", "6.4.2", "6.8.2",
                                                     "6.9.2"])
        triples = self.facts.triples()
        self.assertEqual(len(triples), 120)
        self.assertIn(("infrastructure", "consists-of", ALL_SCO_TARGET), triples)
        self.assertEqual(len(self.facts.objects["malware"].relationships), 26)
        self.assertEqual(self.facts.appendix_b - set(triples), APPENDIX_B_ONLY)
        self.assertEqual(set(triples) - self.facts.appendix_b, FORWARD_ONLY)

    def test_vocabularies(self) -> None:
        vocabs = self.facts.vocabularies
        self.assertEqual(len(vocabs), 32)
        self.assertEqual(vocabs["malware-type-ov"].used_by, ["malware"])
        self.assertEqual(vocabs["opinion-enum"].values, ["strongly-disagree", "disagree", "neutral", "agree",
                                                         "strongly-agree"])           # "Enumeration Value" header
        self.assertIn("emergency-services", vocabs["industry-sector-ov"].values)    # sub-values are rows too
        self.assertEqual(self.facts.objects["malware"].vocabularies,
                         ["malware-type-ov", "processor-architecture-ov", "implementation-language-ov",
                          "malware-capabilities-ov"])

    def test_values_come_from_the_value_tables(self) -> None:
        # The summaries of 10.12 and 10.17 differ from their tables; the tables win.
        infra = self.facts.vocabularies["infrastructure-type-ov"].values
        self.assertEqual(len(infra), 15)
        self.assertTrue({"control-system", "firewall", "routers-switches", "workstation"} <= set(infra))
        socket = self.facts.vocabularies["network-socket-type-enum"].values
        self.assertEqual(socket, ["SOCK_STREAM", "SOCK_DGRAM", "SOCK_RAW", "SOCK_RDM", "SOCK_SEQPACKET"])


if __name__ == "__main__":
    unittest.main()
