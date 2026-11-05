"""Regression tests for review findings (one class per item)."""
import unittest

from citecheck import bib, cli


class Item11SplitEntries(unittest.TestCase):
    def test_paren_delimiter(self):
        t = "@article(k1, title={Foo}, year=2020)\n@article{k2,\n title={Bar}\n}\n"
        sp = cli.split_entries(t)
        self.assertEqual([k for k, _, _ in sp], ["k1", "k2"])
        self.assertEqual(t[sp[0][1]:sp[0][2]], "@article(k1, title={Foo}, year=2020)")
        self.assertEqual([e["key"] for e in bib.parse_bib(t)], ["k1", "k2"])
        self.assertEqual(bib.parse_bib(t)[0]["title"], "Foo")

    def test_quoted_paren_entry_does_not_run_to_eof(self):
        t = '@article(k1, title="Foo (bar)", year="2020")\n@article{k2, title={B}}\n'
        sp = cli.split_entries(t)
        self.assertEqual(t[sp[0][1]:sp[0][2]], '@article(k1, title="Foo (bar)", year="2020")')

    def test_commented_entry_ignored(self):
        t = "% @article{old, title={X}}\n@article{k, title={Y}}\n"
        self.assertEqual([k for k, _, _ in cli.split_entries(t)], ["k"])
        self.assertEqual(cli.split_entries("  @misc{a, title={X}}")[0][1], 2)  # control: indented is fine


if __name__ == "__main__":
    unittest.main()
