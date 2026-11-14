"""Unit tests for stixmd.layout."""
import unittest

from stixmd.config import StixMdError
from stixmd.layout import SingleLayout
from stixmd.model import Heading


class SingleLayoutTests(unittest.TestCase):
    def test_everything_in_one_note(self) -> None:
        heads = [Heading(level=1, number="4", title="SDOs", text="4 SDOs"),
                 Heading(level=2, number="4.1", title="Attack Pattern", text="4.1 Attack Pattern")]
        layout = SingleLayout(heads, "STIX2.1")
        self.assertEqual(layout.note_of("4.1"), "STIX2.1")
        self.assertEqual(layout.heading_of("4.1"), "4.1 Attack Pattern")
        self.assertFalse(layout.is_note_root("4"))
        with self.assertRaises(StixMdError):
            layout.note_of("9.9")


if __name__ == "__main__":
    unittest.main()
