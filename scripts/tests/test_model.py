"""Unit tests for stixmd.model."""
import unittest

from stixmd import model


class ModelTests(unittest.TestCase):
    def test_heading_defaults(self) -> None:
        self.assertEqual(model.Heading(level=1, number="1", title="x", text="1 x").anchors, [])

    def test_keyword_only(self) -> None:
        with self.assertRaises(TypeError):
            model.Span("text")  # type: ignore[misc]

    def test_mutable_defaults_are_not_shared(self) -> None:
        a, b = model.Cell(), model.Cell()
        a.blocks.append(model.Image(src="x"))
        self.assertEqual(b.blocks, [])
        self.assertEqual(model.Table().rows, [])

    def test_span_defaults(self) -> None:
        span = model.Span(text="x")
        self.assertEqual((span.bold, span.italic, span.code, span.code_line, span.href),
                         (False, False, False, False, None))


if __name__ == "__main__":
    unittest.main()
