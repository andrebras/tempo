import unittest

from tempo.tui.text import cell_width, clip, plain, rich, wrap


class TextTest(unittest.TestCase):
    def test_paragraphs_lists_and_marks_read_as_on_the_page(self):
        text = "Pick **one** of `a` or `b`.\n\n- First\n- Second\n1. Numbered"
        lines = rich(text, 40)
        self.assertEqual(
            [plain(line) for line in lines],
            ["Pick one of a or b.", "", "• First", "• Second", "1. Numbered"],
        )
        self.assertIn(("one", "bold"), lines[0])
        self.assertIn(("a", "code"), lines[0])

    def test_wrapping_keeps_to_the_width_and_indents_list_items(self):
        text = "- " + "word " * 20 + "\n\nA paragraph that goes on for a while, longer than a line."
        lines = [plain(line) for line in rich(text, 20)]
        self.assertTrue(all(cell_width(line) <= 20 for line in lines))
        self.assertTrue(lines[0].startswith("• word"))
        self.assertTrue(lines[1].startswith("  word"))
        self.assertFalse(any(line.endswith(" ") for line in lines))

    def test_a_word_longer_than_a_line_is_cut(self):
        lines = [plain(line) for line in wrap([("x" * 25, "")], 10)]
        self.assertEqual(lines, ["x" * 10, "x" * 10, "x" * 5])

    def test_wide_characters_take_two_columns(self):
        self.assertEqual(cell_width("数据库"), 6)
        self.assertEqual(clip("数据库", 5), "数据…")
        self.assertEqual(clip("short", 10), "short")
