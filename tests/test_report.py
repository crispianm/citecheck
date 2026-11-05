import csv
import io
import os
import tempfile
import unittest
from types import SimpleNamespace as NS

from citecheck import cli, report


def entry(key, status, **kw):
    d = dict(key=key, entry_type="inproceedings", status=status, source="dblp", record_key="conf/x/" + key,
             venue="CVPR", doi="10.1/x", title="A title", url="https://dblp.org/rec/conf/x/" + key,
             issues=[], notes=[], suggested_bibtex=None)
    d.update(kw)
    return NS(**d)


def fake():
    es = [entry("ok1", "ok"),
          entry("bad", "mismatch", issues=["year differs"], suggested_bibtex="@inproceedings{bad,\n  title={X}\n}"),
          entry("evil", "manual", title="<script>alert(1)</script>", issues=["<b>x</b> | y"]),
          entry("nn", "note", notes=["accepted diff"]),
          entry("ret", "retracted", issues=["retracted"])]
    return NS(bib="refs.bib", entries=es, missing_keys=["gone"], unused_keys=["spare"],
              duplicate_keys=[], duplicate_titles=[("a", "b")], index_info={},
              counts=lambda: {})


class ReportTests(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()

    def p(self, n):
        return os.path.join(self.d, n)

    def test_md(self):
        report.write_md(fake(), self.p("r.md"))
        t = open(self.p("r.md")).read()
        self.assertIn("| bad | mismatch", t)
        self.assertIn("```bibtex", t)
        self.assertIn("`gone`", t)

    def test_csv(self):
        report.write_csv(fake(), self.p("r.csv"))
        rows = list(csv.reader(open(self.p("r.csv"), newline="")))
        self.assertEqual(rows[0][:3], ["key", "status", "source"])
        self.assertEqual(len(rows), 6)
        self.assertEqual(rows[3][7], "<b>x</b> | y")

    def test_html_escapes(self):
        report.write_html(fake(), self.p("r.html"))
        t = open(self.p("r.html")).read()
        self.assertNotIn("<script>alert", t)  # forced-fail control: raw tag must be absent
        self.assertIn("&lt;script&gt;alert(1)", t)
        self.assertEqual(t.count("<script>"), 1)  # only our own script block
        self.assertNotIn("src=", t)  # no external resources
        self.assertIn("prefers-color-scheme", t)

    def test_terminal_nocolor(self):
        buf = io.StringIO()
        report.print_terminal(fake(), color=False, file=buf)
        t = buf.getvalue()
        self.assertNotIn("\033", t)
        self.assertLess(t.index("retracted"), t.index("mismatch"))
        self.assertIn("1 entries have notes only", t)
        self.assertIn("gone", t)
        buf = io.StringIO()
        report.print_terminal(fake(), color=True, file=buf)
        self.assertIn("\033[", buf.getvalue())  # control: color on does emit escapes

    def test_split_and_fixed(self):
        bib = self.p("a.bib")
        open(bib, "w").write("@article{bad,\n title={Old {B}},\n}\n@article{keep,\n title={K}\n}\n")
        out = cli.write_fixed(bib, fake())
        t = open(out).read()
        self.assertIn("title={X}", t)
        self.assertIn("@article{keep", t)
        self.assertNotIn("Old", t)
        self.assertIsNone(cli.write_fixed(bib, fake()))  # exists: refuse
        self.assertIsNotNone(cli.write_fixed(bib, fake(), force=True))


class CliTests(unittest.TestCase):
    def test_help_exits_zero(self):
        with self.assertRaises(SystemExit) as cm:
            cli.build_parser().parse_args(["check", "--help"])
        self.assertEqual(cm.exception.code, 0)

    def test_bad_arg_fails(self):  # control
        with self.assertRaises(SystemExit) as cm:
            cli.build_parser().parse_args(["check"])
        self.assertNotEqual(cm.exception.code, 0)

    def test_parse(self):
        a = cli.build_parser().parse_args(["check", "x.bib", "--only", "a", "b", "--strict", "-v"])
        self.assertEqual((a.only, a.strict, a.verbose), (["a", "b"], True, True))


if __name__ == "__main__":
    unittest.main()
