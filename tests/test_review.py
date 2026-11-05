"""Regression tests for review findings (one class per item)."""
import os
import unittest

try:
    from test_core import Base
except ImportError:  # run as tests.test_review
    from tests.test_core import Base

from citecheck import bib, cli, dblp_index, fix, latex, lint, match


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


class Item16Surnames(unittest.TestCase):
    def test_and_case_insensitive(self):
        self.assertEqual(bib.bib_surnames("A Smith AND B Jones And C Lee")[0], ["Smith", "Jones", "Lee"])

    def test_empty_matches_nothing(self):
        self.assertFalse(bib.surname_match("Smith", ""))
        self.assertFalse(bib.surname_match("", "Smith"))

    def test_short_suffix_not_a_match(self):
        self.assertFalse(bib.surname_match("Li", "Ali"))
        self.assertFalse(bib.surname_match("Yu", "Ryu"))
        self.assertFalse(bib.surname_match("Ali", "Li"))

    def test_particles_still_match(self):  # controls
        self.assertTrue(bib.surname_match("van den Berg", "Berg"))
        self.assertTrue(bib.surname_match("Berg", "van den Berg"))
        self.assertTrue(bib.surname_match("Al-Hashimi", "Hashimi"))
        self.assertTrue(bib.surname_match("M\u00fcller", "Muller"))
        self.assertTrue(bib.surname_match("McDonald", "Mc Donald"))


class Item17FtsQuote(Base):
    def test_quote_in_author_or_title(self):
        conn = dblp_index.connect(self.full)
        dblp_index.lookup(conn, "Attention Is All You Need Really", author='O"Neil')
        dblp_index.lookup(conn, 'Attention "Is" All You Need Really', author="Vaswani")


class Item21FillMissing(Base):
    BIB = ("@inproceedings{nopages,\n author={Kaiming He and Xiangyu Zhang and Shaoqing Ren and Jian Sun},\n"
           " title={Deep Residual Learning for Image Recognition},\n booktitle={CVPR},\n year={2016}}\n")

    def test_note_gets_suggestion_applied_only_on_request(self):
        e = self.run_check(self.BIB).entries[0]
        self.assertEqual(e.status, "note")
        self.assertIn("pages     = {770--778}", e.suggested_bibtex or "")
        p = self.dir / "fm.bib"
        p.write_text(self.BIB)
        res = self.run_check(self.BIB)
        t = cli.write_fixed(p, res, force=True).read_text()
        self.assertNotIn("770", t)  # default: only mismatch/published are applied
        t = cli.write_fixed(p, res, force=True, fill_missing=True).read_text()
        self.assertIn("770--778", t)

    def test_spelling_variant_not_filled(self):  # control: accepted variants are not rewritten
        e = self.run_check(self.BIB.replace("Kaiming He", "Kaiming Hee")).entries[0]
        self.assertEqual(e.status, "note")
        self.assertIsNone(e.suggested_bibtex)

    def test_parser_flag(self):
        self.assertTrue(cli.build_parser().parse_args(["check", "x.bib", "--fill-missing"]).fill_missing)


class Item22EntryType(Base):
    HE = " author={Kaiming He and Xiangyu Zhang and Shaoqing Ren and Jian Sun},\n title={Deep Residual Learning for Image Recognition},\n"

    def test_article_citing_conference_paper(self):
        e = self.run_check("@article{a,\n" + self.HE + " journal={CVPR},\n pages={770--778},\n year={2016}}").entries[0]
        self.assertEqual(e.status, "note")
        self.assertTrue(any("conference paper" in n for n in e.notes), e.notes)

    def test_inproceedings_citing_journal_article(self):
        e = self.run_check("@inproceedings{a,\n author={J\\\"urgen M\\\"uller and Sam Smith},\n"
                           " title={Sparse Depth Completion in the Wild},\n booktitle={IEEE Trans. Pattern Anal. Mach. Intell.},\n"
                           " year={2020}, volume={42}, pages={100--110}}").entries[0]
        self.assertTrue(any("journal article" in n for n in e.notes), e.notes)

    def test_matching_types_no_note(self):  # control
        e = self.run_check("@inproceedings{a,\n" + self.HE + " booktitle={CVPR},\n pages={770--778},\n year={2016}}").entries[0]
        self.assertEqual((e.status, e.notes), ("ok", []))


class Item23GivenNames(unittest.TestCase):
    def notes(self, bib_authors, rec_authors):
        rec = {"title": "T", "venue": "CVPR", "raw_venue": "CVPR", "source": "dblp", "key": "conf/x/Y", "year": 2020,
               "pages": None, "volume": None, "doi": None, "authors": rec_authors}
        e = {"type": "inproceedings", "title": "T", "booktitle": "CVPR", "year": "2020", "author": bib_authors}
        errs, notes, _ = match.compare(e, rec, None)
        self.assertEqual(errs, [])
        return [n for n in notes if "given" in n]

    def test_wrong_initial(self):
        n = self.notes("Wang, Y. and Li, Z.", ["Xintao Wang", "Zhe Li"])
        self.assertEqual(len(n), 1)
        self.assertIn("#1", n[0])

    def test_matching_forms_no_note(self):  # controls
        for b, r in [("Wang, X.", "Xintao Wang"), ("Xintao Wang", "Xintao Wang"), ("W. Wang", "Wei Wang 0001"),
                     ("Wang", "Wei Wang"), ("Smith, J.-P.", "Jean-Pierre Smith"), ("Jean-Pierre Smith", "J.-P. Smith"),
                     ("\\'{E}mile Zola", "Emile Zola"), ("van den Berg, Jan", "Jan van den Berg"),
                     ("Wang, X. Y.", "Xintao Wang")]:
            self.assertEqual(self.notes(b, [r]), [], (b, r))


class Item24Math(unittest.TestCase):
    def test_math_alphanumerics_kept(self):
        ct = lambda s: bib.clean_title(bib.detex(s))  # noqa: E731
        self.assertEqual(ct("$L_2$-Net: Deep Learning"), ct("L2-Net: Deep Learning"))
        self.assertEqual(ct("Voronoi of $\\mathbb{R}^3$"), ct("Voronoi of $\\mathbb{R}^3$"))
        self.assertEqual(ct("Voronoi of $\\mathbb{R}^3$"), ct("Voronoi of R3"))
        self.assertEqual(ct("$\\alpha$-nDCG"), ct("\u03b1-nDCG"))
        self.assertEqual(ct("NVDS$^{+}$"), ct("NVDS+"))

    def test_different_titles_still_differ(self):  # control
        self.assertNotEqual(bib.clean_title("$L_2$-Net"), bib.clean_title("L3-Net"))


if __name__ == "__main__":
    unittest.main()
