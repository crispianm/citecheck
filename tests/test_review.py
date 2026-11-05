"""Regression tests for review findings (one class per item)."""
import unittest

from citecheck import bib, cli, fix, latex, lint


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


class Item12FixTitle(unittest.TestCase):
    def test_group_not_restored_inside_word(self):
        t = fix._title("Contrastive Learning with C", "Contrastive Learning with {C}")
        self.assertEqual(t, "Contrastive Learning with {C}")

    def test_suggested_entry_passes_own_lint(self):
        rec = {"authors": ["A B"], "title": "ImageNet Classification with CNN and GANs", "venue": "CVPR",
               "raw_venue": "CVPR", "year": 2020, "key": "conf/cvpr/x"}
        out = bib.parse_bib(fix.to_bibtex("k", "inproceedings", rec, {"title": "ImageNet Classification"}))[0]
        self.assertEqual(lint.check_title(out["title"]), [])
        self.assertIn("{ImageNet}", out["title"])


class Item13Comments(unittest.TestCase):
    def test_percent_after_double_backslash_is_comment(self):
        self.assertEqual(latex._from_tex("a\\\\% \\cite{old}\n\\cite{new}"), {"new"})

    def test_escaped_percent_is_not_comment(self):  # control
        self.assertEqual(latex._from_tex("50\\% \\cite{a}"), {"a"})


class Item14TextMacros(unittest.TestCase):
    def test_emph_keeps_first_word(self):
        self.assertEqual(bib.detex("Imaging \\emph{in situ} cells"), "Imaging in situ cells")
        self.assertEqual(bib.clean_title(bib.detex("\\textbf{Fast} \\emph{in situ}")), bib.clean_title("Fast in situ"))


class Item15DotlessI(unittest.TestCase):
    def test_dotless(self):
        for src in ("Garc{\\'\\i}a", "Garc\\'{\\i}a", "Garc{\\'i}a"):
            self.assertEqual(bib.fold(bib.detex(src)), "garcia", src)
        self.assertEqual(bib.detex("Na\\\"{\\i}ve"), "Na\u00efve")
        self.assertEqual(bib.detex("\\^{\\i}le"), "\u00eele")
        self.assertEqual(bib.detex("\\v{\\j}"), "\u01f0")


if __name__ == "__main__":
    unittest.main()
