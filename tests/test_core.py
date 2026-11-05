import gzip
import os
import shutil
import tempfile
import unittest
import urllib.error
from pathlib import Path

from citecheck import bib, checker, dblp_index, fix, latex, lint, match, sources

FIX = Path(__file__).parent / "fixtures"
TMP = os.environ.get("TMPDIR")


def http_error(code):
    return urllib.error.HTTPError("http://x", code, "err", {}, None)


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = Path(tempfile.mkdtemp(dir=TMP))
        cls.gz = cls.dir / "dblp.xml.gz"
        with open(FIX / "dblp_fixture.xml", "rb") as f, gzip.open(cls.gz, "wb") as g:
            g.write(f.read())
        cls.full = cls.dir / "full.sqlite"
        cls.lite = cls.dir / "lite.sqlite"
        quiet = lambda n, el, extra="": None  # noqa: E731
        dblp_index.build(cls.gz, cls.full, progress=quiet)
        dblp_index.build(cls.gz, cls.lite, lite=True, progress=quiet)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.dir, ignore_errors=True)

    def setUp(self):
        self._iv = (sources.S2.interval, sources.Crossref.interval, sources.OpenAlex.interval)
        sources.S2.interval = sources.Crossref.interval = sources.OpenAlex.interval = 0
        self._fetch = sources.fetch

    def tearDown(self):
        sources.S2.interval, sources.Crossref.interval, sources.OpenAlex.interval = self._iv
        sources.fetch = self._fetch

    def run_check(self, text, **kw):
        p = self.dir / "t.bib"
        p.write_text(text)
        kw.setdefault("db", self.full)
        kw.setdefault("offline", True)
        return checker.check(p, **kw)


class TestIndex(Base):
    def test_full_lookup_and_entity(self):
        conn = dblp_index.connect(self.full)
        d = dblp_index.lookup(conn, "Sparse Depth Completion in the Wild", author="Müller")[0]
        self.assertGreater(d["score"], 0.99)
        self.assertIn("Jürgen Müller", d["authors"])
        self.assertNotIn("0001", d["authors"])  # disambiguation suffix stripped
        st = dblp_index.stats(conn)
        self.assertEqual(st["mode"], "full")
        self.assertIn("data", st["types"])
        self.assertIn("proceedings", st["types"])
        self.assertNotIn("www", st["types"])
        self.assertIsNotNone(conn.execute("SELECT ee FROM pubs WHERE key='conf/cvpr/HeZRS16'").fetchone()[0])

    def test_lite(self):
        conn = dblp_index.connect(self.lite)
        st = dblp_index.stats(conn)
        self.assertEqual(st["mode"], "lite")
        self.assertNotIn("proceedings", st["types"])
        self.assertNotIn("book", st["types"])
        self.assertIn("data", st["types"])
        self.assertEqual(conn.execute("SELECT count(*) FROM pubs WHERE ee IS NOT NULL OR publtype IS NOT NULL").fetchone()[0], 0)
        r = dblp_index.lookup(conn, "Deep Residual Learning for Image Recognition", author="He")
        self.assertTrue(r and r[0]["score"] > 0.99)
        self.assertIn("10.1109/cvpr.2016.90", [x["doi"] for x in r])

    def test_incremental_and_mode_switch(self):
        db = self.dir / "inc.sqlite"
        quiet = lambda n, el, extra="": None  # noqa: E731
        dblp_index.build(self.gz, db, lite=True, progress=quiet)
        dblp_index.build(self.gz, db, lite=True, progress=quiet)   # incremental, no change
        self.assertEqual(dblp_index.stats(dblp_index.connect(db))["mode"], "lite")
        dblp_index.build(self.gz, db, lite=False, progress=quiet)  # switch => rebuild
        self.assertEqual(dblp_index.stats(dblp_index.connect(db))["mode"], "full")

    def test_connect_missing(self):
        self.assertIsNone(dblp_index.connect(self.dir / "nope.sqlite"))


class TestCheck(Base):
    def setUp(self):
        super().setUp()
        self.res = checker.check(FIX / "refs_fixture.bib", db=self.full, offline=True)
        self.by = {e.key: e for e in self.res.entries}

    def test_ok(self):
        for k in ("he16", "vaswani_ok", "muller20"):
            self.assertEqual(self.by[k].status, "ok", (k, self.by[k].issues))
        self.assertEqual(self.by["he16"].source, "dblp")
        self.assertEqual(self.by["he16"].url, "https://dblp.org/rec/conf/cvpr/HeZRS16")

    def test_wrong_year_forced_fail(self):
        e = self.by["wrongyear"]
        self.assertEqual(e.status, "mismatch")
        self.assertTrue(any(i.startswith("year:") for i in e.issues))
        self.assertIn("year      = {2017}", e.suggested_bibtex)
        self.assertEqual(checker.exit_code(self.res), 1)

    def test_clean_bib_exit_zero(self):
        text = (FIX / "refs_fixture.bib").read_text().split("@inproceedings{wrongyear")[0]
        res = self.run_check(text)  # he16, vaswani_ok, muller20
        self.assertEqual(set(res.counts()), {"ok"})
        self.assertEqual(checker.exit_code(res), 0)

    def test_wrong_author(self):
        e = self.by["wrongauthor"]
        self.assertEqual(e.status, "mismatch")
        self.assertTrue(e.issues[0].startswith("authors: #1 bib 'Smyth'"), e.issues)

    def test_published(self):
        e = self.by["arxivcite"]
        self.assertEqual(e.status, "published")
        self.assertIn("cited as arXiv, published at CVPR", e.issues[0])
        self.assertIn("@inproceedings{arxivcite", e.suggested_bibtex)
        self.assertIn("booktitle = {CVPR}", e.suggested_bibtex)
        self.assertIn("pages     = {770--778}", e.suggested_bibtex)
        self.assertEqual(len(e.issues), 1)  # year/pages are not compared for a published upgrade
        self.assertEqual(checker.exit_code(self.res), 1)  # (other mismatches); published alone is not an error
        solo = self.run_check("@article{a,\n author={Kaiming He and Xiangyu Zhang and Shaoqing Ren and Jian Sun},\n"
                              " title={Deep Residual Learning for Image Recognition},\n journal={arXiv preprint arXiv:1512.03385},\n year={2015}}")
        self.assertEqual(checker.exit_code(solo), 0)

    def test_unknown_manual_offline(self):
        e = self.by["unknown"]
        self.assertEqual(e.status, "manual")
        self.assertEqual(checker.exit_code(self.run_check("@misc{u,\n author={Zed Nobody},\n title={A Completely Unknown Paper About Kalman Filtering},\n year=2020}")), 0)
        self.assertEqual(checker.exit_code(self.run_check("@misc{u,\n author={Zed Nobody},\n title={A Completely Unknown Paper About Kalman Filtering},\n year=2020}"), strict=True), 1)

    def test_lint_notes(self):
        n = self.by["lintme"].notes
        self.assertTrue(any("missing year or date" in x for x in n), n)
        self.assertTrue(any("CNN" in x and "ImageNet" in x for x in n), n)
        self.assertTrue(any("et al" in x for x in n), n)
        self.assertTrue(any("doi is written as a URL" in x for x in n), n)
        self.assertTrue(any("venue written 2 ways" in x for x in n), n)  # CVPR vs long name
        self.assertTrue(any("venue written 2 ways" in x for x in self.by["lintme2"].notes))
        self.assertFalse(any("unprotected" in x for x in self.by["lintme2"].notes))  # {CNN} is protected
        self.assertTrue(any("no eprint" in x for x in self.by["lintarxiv"].notes))
        self.assertFalse(self.by["lintme"].issues and any("lint" in i for i in self.by["lintme"].issues))
        off = self.run_check((FIX / "refs_fixture.bib").read_text(), lint=False)
        self.assertFalse(any("lint:" in x for e in off.entries for x in e.notes))

    def test_lint_unit(self):
        self.assertEqual(lint.check_title("Deep {CNN} for ImageNet and PyTorch"), ["ImageNet", "PyTorch"])
        self.assertEqual(lint.check_title("{Deep CNN All Protected}"), [])
        self.assertEqual(lint.check_title("Learning 3D Shapes with GANs"), [])
        n = lint.lint_entry({"type": "article", "key": "k", "title": "t", "year": "2000"})
        self.assertIn("missing author, journal or journaltitle", n[0])

    def test_duplicates_and_cite_keys(self):
        text = ("@misc{a,\n title={Same Title Here},\n author={X Y}}\n@misc{a,\n title={Other},\n author={X Y}}\n"
                "@misc{b,\n title={Same Title Here!},\n author={X Y}}\n@misc{c,\n title={Unused},\n author={X Y}}\n")
        res = self.run_check(text, cite_keys={"a", "b", "ghost"})
        self.assertEqual(res.duplicate_keys, ["a"])
        self.assertIn(("a", "b"), res.duplicate_titles)
        self.assertEqual(res.missing_keys, ["ghost"])
        self.assertEqual(res.unused_keys, ["c"])
        self.assertEqual([e.key for e in res.entries], ["a", "b"])
        self.assertEqual(checker.exit_code(res), 1)  # missing key fails
        self.assertEqual([e.key for e in self.run_check(text, only=["c"]).entries], ["c"])

    def test_progress_and_index_info(self):
        seen = []
        res = self.run_check("@misc{a,\n title={Zzz},\n author={X Y}}", progress=lambda i, n, k, s: seen.append((i, n, k, s)))
        self.assertEqual(seen, [(1, 1, "a", "manual")])
        self.assertEqual(res.index_info["mode"], "full")
        self.assertEqual(self.run_check("@misc{a,\n title={Zzz},\n author={X Y}}", db=None).index_info, {})


class TestOnline(Base):
    """Mocked network: no real requests."""

    def mock(self, routes):
        calls = []

        def fake(url, headers=None, timeout=30):
            calls.append(url)
            for frag, payload in routes:
                if frag in url:
                    if isinstance(payload, int):
                        raise http_error(payload)
                    return payload
            raise http_error(404)
        sources.fetch = fake
        return calls

    BIB = ("@article{x,\n author={Ada Quark and Bo Zed},\n title={Quantum Fixtures of Marine Biology},\n"
           " journal={Journal of Marine Fixtures},\n year={2020},\n pages={1--9},\n doi={10.1234/marine.5}}")
    NODOI = BIB.replace(",\n doi={10.1234/marine.5}", "")
    CR_ITEM = {"title": ["Quantum Fixtures of Marine Biology"], "container-title": ["Journal of Marine Fixtures"],
               "author": [{"given": "Ada", "family": "Quark"}, {"given": "Bo", "family": "Zed"}],
               "issued": {"date-parts": [[2020]]}, "DOI": "10.1234/marine.5", "type": "journal-article", "page": "1-9"}

    def test_s2_fallback(self):
        calls = self.mock([("semanticscholar", {"data": [{"paperId": "abc", "title": "Quantum Fixtures of Marine Biology",
                           "authors": [{"name": "Ada Quark"}, {"name": "Bo Zed"}], "year": 2020,
                           "venue": "Journal of Marine Fixtures", "externalIds": {"DOI": "10.1234/marine.5"}}]}),
                          ("api.openalex.org/works/doi", 404), ("api.crossref.org/works/10.1234", 404)])
        res = self.run_check(self.BIB, offline=False, cache_dir=self.dir / "c1")
        e = res.entries[0]
        self.assertEqual((e.source, e.status), ("s2", "ok"), e.issues)
        self.assertTrue(e.url.startswith("https://www.semanticscholar.org/paper/abc"))
        self.assertTrue((self.dir / "c1" / "s2_cache.json").exists())
        n = len(calls)
        self.run_check(self.BIB, offline=False, cache_dir=self.dir / "c1")  # all cached now
        self.assertEqual(len(calls), n)

    def test_crossref_title_fallback(self):
        self.mock([("semanticscholar", 404), ("api.crossref.org/works?query.bibliographic", {"message": {"items": [self.CR_ITEM]}}),
                   ("api.crossref.org/works/10.1234", {"message": self.CR_ITEM}), ("openalex", 404)])
        res = self.run_check(self.NODOI, offline=False)
        e = res.entries[0]
        self.assertEqual((e.source, e.status), ("crossref", "ok"), e.issues)
        self.assertEqual(e.doi, "10.1234/marine.5")

    def test_openalex_fallback(self):
        oa = {"results": [{"id": "https://openalex.org/W1", "title": "Quantum Fixtures of Marine Biology", "publication_year": 2020,
                           "doi": "https://doi.org/10.1234/marine.5", "authorships": [{"author": {"display_name": "Ada Quark"}},
                           {"author": {"display_name": "Bo Zed"}}], "primary_location": {"source": {"display_name": "J. Marine Fixtures"}},
                           "biblio": {"first_page": "1", "last_page": "9"}, "is_retracted": False}]}
        self.mock([("semanticscholar", 404), ("api.crossref.org", 404), ("api.openalex.org/works?search", oa),
                   ("api.openalex.org/works/doi", 404)])
        res = self.run_check(self.NODOI, offline=False)
        e = res.entries[0]
        self.assertEqual(e.source, "openalex")
        self.assertIn(e.status, ("ok", "note"), e.issues)  # venue name differs only by abbreviation

    def test_wrong_year_via_crossref_is_mismatch(self):
        self.mock([("semanticscholar", 404), ("api.crossref.org/works?query", {"message": {"items": [self.CR_ITEM]}}),
                   ("api.crossref.org/works/10.1234", {"message": self.CR_ITEM}), ("openalex", 404)])
        res = self.run_check(self.NODOI.replace("2020", "2015"), offline=False)
        self.assertEqual(res.entries[0].status, "mismatch")

    def test_loose_s2_is_manual(self):
        self.mock([("semanticscholar", {"data": [{"paperId": "z", "title": "Quantum Fixtures of Marine Biology Revisited Again",
                   "authors": [{"name": "Ada Quark"}], "year": 2020, "venue": "X"}]}), ("openalex", 404), ("crossref", 404)])
        res = self.run_check(self.NODOI, offline=False)
        self.assertEqual(res.entries[0].status, "manual")

    def test_retracted_openalex(self):
        self.mock([("semanticscholar", 404), ("api.crossref.org/works?query", {"message": {"items": [self.CR_ITEM]}}),
                   ("api.crossref.org/works/10.1234", {"message": self.CR_ITEM}),
                   ("api.openalex.org/works/doi", {"id": "W1", "is_retracted": True, "title": "x"}), ("openalex", 404)])
        res = self.run_check(self.BIB, offline=False)
        e = res.entries[0]
        self.assertEqual(e.status, "retracted")
        self.assertIn("OpenAlex", e.issues[0])
        self.assertEqual(checker.exit_code(res), 1)

    def test_retracted_crossref(self):
        item = dict(self.CR_ITEM, **{"updated-by": [{"type": "retraction", "DOI": "10.1234/ret"}]})
        self.mock([("semanticscholar", 404), ("api.crossref.org/works?query", {"message": {"items": [item]}}),
                   ("api.crossref.org/works/10.1234", {"message": item}), ("openalex", 404)])
        self.assertEqual(self.run_check(self.BIB, offline=False).entries[0].status, "retracted")

    def test_not_retracted_control(self):
        self.mock([("semanticscholar", 404), ("api.crossref.org/works?query", {"message": {"items": [self.CR_ITEM]}}),
                   ("api.crossref.org/works/10.1234", {"message": self.CR_ITEM}),
                   ("api.openalex.org/works/doi", {"id": "W1", "is_retracted": False}), ("openalex", 404)])
        self.assertEqual(self.run_check(self.BIB, offline=False).entries[0].status, "ok")

    def test_offline_makes_no_requests(self):
        calls = self.mock([("", {"data": []})])
        res = self.run_check(self.BIB, offline=True)
        self.assertEqual(calls, [])
        self.assertEqual(res.entries[0].status, "manual")

    def test_user_agent_has_no_email(self):
        self.assertNotIn("@", sources.UA)
        self.assertTrue(sources.UA.startswith("citecheck/0.1"))


class TestLatexFix(Base):
    def test_latex_keys(self):
        d = self.dir / "proj"
        (d / "sec").mkdir(parents=True, exist_ok=True)
        (d / "main.tex").write_text("\\documentclass{article}\n\\begin{document}\\cite{a,b} \\citep[see][p.~3]{c}\n"
                                    "% \\cite{commented}\n\\input{sec/one}\\end{document}")
        (d / "sec" / "one.tex").write_text("\\textcite{d} \\autocite*{e} \\parencite{f}\\nocite{g}")
        (d / "orphan.tex").write_text("\\cite{orphan}")
        self.assertEqual(latex.cited_keys(d), set("abcdefg"))
        self.assertEqual(latex.cited_keys(d / "main.tex"), set("abcdefg"))
        (d / "main.aux").write_text("\\citation{a,b}\n\\abx@aux@cite{0}{z}\n")
        self.assertEqual(latex.cited_keys(d), {"a", "b", "z"})
        self.assertEqual(latex.cited_keys(d / "main.aux"), {"a", "b", "z"})
        (d / "all.tex").write_text("\\nocite{*}")
        self.assertIsNone(latex.cited_keys(d / "all.tex"))
        (d / "r.bib").write_text("")
        (d / "r.fixed.bib").write_text("")
        self.assertEqual([b.name for b in latex.find_bibs(d)], ["r.bib"])

    def test_to_bibtex(self):
        rec = {"source": "dblp", "key": "conf/cvpr/HeZRS16", "title": "Deep Residual Learning for Image Recognition.",
               "authors": ["Kaiming He", "Jian Sun"], "year": 2016, "venue": "CVPR", "raw_venue": "CVPR", "pages": "770-778",
               "volume": None, "number": None, "doi": "10.1109/cvpr.2016.90"}
        s = fix.to_bibtex("mykey", "article", rec, {"title": "Deep {Residual} Learning", "booktitle": "Proc. CVPR"})
        self.assertTrue(s.startswith("@inproceedings{mykey,"))
        self.assertIn("title     = {Deep {Residual} Learning for Image Recognition}", s)
        self.assertIn("booktitle = {Proc. CVPR}", s)  # same canonical venue: user's spelling kept
        self.assertIn("pages     = {770--778}", s)
        self.assertIn("author    = {Kaiming He and Jian Sun}", s)
        pre = {"source": "dblp", "key": "journals/corr/abs-1512-03385", "title": "T.", "authors": ["A B"], "year": 2015,
               "venue": "arXiv", "raw_venue": "CoRR", "pages": None, "volume": "abs/1512.03385", "doi": None}
        s = fix.to_bibtex("k", "article", pre, {})
        self.assertTrue(s.startswith("@misc{k,"))
        self.assertIn("eprint", s)
        self.assertIn("archivePrefix = {arXiv}", s)

    def test_venues(self):
        for raw, want in [("Findings of the Association for Computational Linguistics: EMNLP 2021", "ACL-Findings"),
                          ("Proceedings of the 2020 Conference on Empirical Methods in Natural Language Processing", "EMNLP"),
                          ("J. Mach. Learn. Res.", "JMLR"), ("Trans. Mach. Learn. Res.", "TMLR"),
                          ("Int. J. Comput. Vis.", "IJCV"), ("IEEE Trans. Image Process.", "TIP"),
                          ("ICML", "ICML"), ("AAAI", "AAAI"), ("MICCAI", "MICCAI"), ("BMVC", "BMVC"),
                          ("Proc. ACL", "ACL"), ("NAACL-HLT", "NAACL"), ("KDD", "KDD"), ("WACV", "WACV"), ("IJCAI", "IJCAI"),
                          ("CVPR Workshops", "CVPR-W")]:
            self.assertEqual(match.canon(raw), want, raw)


if __name__ == "__main__":
    unittest.main()
