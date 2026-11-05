"""citecheck command line."""
import argparse
import datetime

import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

EPILOG = """\
examples:
  citecheck setup                      build the offline DBLP index (once)
  citecheck check refs.bib             check every entry in a bib file
  citecheck check ~/paper              check the bib in a LaTeX project, cited keys only
  citecheck check refs.bib --out rep   also write rep/report.{md,csv,html}
  citecheck check refs.bib --write-fixed   write refs.fixed.bib with suggested fixes
  citecheck search "attention is all you need"
  citecheck update                     pull the latest code and refresh the index
  citecheck doctor                     diagnose the install
"""
STALE_DAYS = 60
ROOT = Path(__file__).resolve().parent.parent


def _version():
    try:
        from citecheck import __version__
        return __version__
    except Exception:
        return "0.1.0"


def _err(*a):
    print(*a, file=sys.stderr)


def _cfg():
    from citecheck import config
    try:
        return config.load()
    except Exception:
        return {}


def build_parser():
    p = argparse.ArgumentParser(
        prog="citecheck", description="Check a .bib file against an offline DBLP index.",
        epilog=EPILOG, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--version", action="version", version="citecheck " + _version())
    sub = p.add_subparsers(dest="cmd", metavar="COMMAND")

    s = sub.add_parser("setup", help="interactive setup (download and build the DBLP index)",
                       add_help=False)
    s.add_argument("rest", nargs=argparse.REMAINDER)

    c = sub.add_parser("check", help="check a .bib file or LaTeX project directory",
                       formatter_class=argparse.RawDescriptionHelpFormatter)
    c.add_argument("path", metavar="PATH", help=".bib file, or a directory containing the project")
    c.add_argument("--aux", metavar="F", help="use cited keys from this .aux/.tex/dir instead")
    c.add_argument("--all", action="store_true", help="check every entry, not just cited ones")
    c.add_argument("--out", metavar="DIR", help="write report.md, report.csv, report.html here")
    c.add_argument("--offline", action="store_true", help="no network: index and cache only")
    c.add_argument("--no-s2", action="store_true", help="skip Semantic Scholar")
    c.add_argument("--no-lint", action="store_true", help="skip style lint notes")
    c.add_argument("--only", nargs="+", metavar="KEY", help="check only these keys")
    c.add_argument("--strict", action="store_true", help="manual entries also fail (exit 1)")
    c.add_argument("--write-fixed", action="store_true",
                   help="write <bib>.fixed.bib with suggested entries (never overwrites the original)")
    c.add_argument("--force", action="store_true", help="overwrite an existing .fixed.bib")
    c.add_argument("--db", metavar="F", help="DBLP SQLite index (default: from config)")
    c.add_argument("-v", "--verbose", action="store_true", help="list entries that only have notes")

    q = sub.add_parser("search", help="search the DBLP index by title")
    q.add_argument("title")
    q.add_argument("--author")
    q.add_argument("--limit", type=int, default=5)
    q.add_argument("--db", metavar="F")

    st = sub.add_parser("stats", help="show index statistics")
    st.add_argument("--db", metavar="F")

    sub.add_parser("update", help="update the code (git) and the DBLP index")

    d = sub.add_parser("doctor", help="diagnose the installation")
    d.add_argument("--offline", action="store_true", help="skip network checks")
    return p


# ---------------------------------------------------------------- helpers
def _index_age_days(built):
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", str(built or ""))
    if not m:
        return None
    try:
        return (datetime.date.today() - datetime.date(*map(int, m.groups()))).days
    except ValueError:
        return None


def split_entries(text):
    """[(key, start, end)] for each @type{key, ...} block, by brace matching."""
    out = []
    for m in re.finditer(r"@(\w+)\s*([{(])\s*([^,\s{}]+)\s*,", text):
        if m.group(1).lower() in ("comment", "string", "preamble"):
            continue
        depth, i = 0, m.start(2)
        while i < len(text):
            ch = text[i]
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    break
            i += 1
        out.append((m.group(3), m.start(), min(i + 1, len(text))))
    return out


def write_fixed(bib, result, force=False):
    bib = Path(bib)
    target = bib.with_name(bib.stem + ".fixed.bib")
    if target.exists() and not force:
        _err("refusing to overwrite %s (use --force)" % target)
        return None
    sugg = {e.key: e.suggested_bibtex for e in result.entries if e.suggested_bibtex}
    text = bib.read_text(encoding="utf-8", errors="replace")
    for key, a, b in reversed(split_entries(text)):
        if key in sugg:
            text = text[:a] + sugg[key].strip() + text[b:]
    target.write_text(text, encoding="utf-8")
    return target


# ---------------------------------------------------------------- commands
def cmd_check(a):
    from citecheck import checker, config, latex, report
    cfg = _cfg()
    path = Path(a.path).expanduser()
    if not path.exists():
        _err("no such path: %s" % path)
        return 2
    keys = None
    if path.is_dir():
        bibs = latex.find_bibs(path)
        if not bibs:
            _err("no .bib file found in %s" % path)
            return 2
        if len(bibs) > 1:
            _err("several .bib files found; checking: " + ", ".join(b.name for b in bibs))
        if not a.all and not a.aux:
            keys = latex.cited_keys(path)
    else:
        bibs = [path]
    if a.aux and not a.all:
        keys = latex.cited_keys(Path(a.aux).expanduser())
    if a.all:
        keys = None
    db = Path(a.db).expanduser() if a.db else config.db_path(cfg)
    if not db.exists():
        if a.db:
            _err("index not found: %s" % db)
            return 2
        db = None
        _err("warning: no DBLP index found (run `citecheck setup`); %s"
             % ("offline with no index: using the cache only" if a.offline
                else "checking online only (slower, less complete)"))
    cache = config.cache_dir(cfg)
    tty = sys.stderr.isatty()

    def progress(i, n, key, status):
        if tty:
            sys.stderr.write("\r\033[K[%d/%d] %s: %s" % (i, n, key, status))
            sys.stderr.flush()

    code = 0
    for bib in bibs:
        res = checker.check(
            str(bib), db=str(db) if db else None, cite_keys=keys, offline=a.offline, no_s2=a.no_s2,
            lint=not a.no_lint, only=set(a.only) if a.only else None, cache_dir=str(cache),
            s2_api_key=cfg.get("s2_api_key") or os.environ.get("S2_API_KEY"), progress=progress)
        if tty:
            sys.stderr.write("\r\033[K")
        age = _index_age_days((res.index_info or {}).get("built"))
        if age is not None and age > STALE_DAYS:
            _err("warning: DBLP index is %d days old (run `citecheck update`)" % age)
        report.print_terminal(res, verbose=a.verbose)
        if a.out:
            out = Path(a.out).expanduser()
            out.mkdir(parents=True, exist_ok=True)
            stem = "report" if len(bibs) == 1 else "report_" + bib.stem
            report.write_md(res, out / (stem + ".md"))
            report.write_csv(res, out / (stem + ".csv"))
            report.write_html(res, out / (stem + ".html"))
            _err("reports written to %s" % out)
        if a.write_fixed:
            t = write_fixed(bib, res, a.force)
            if t:
                _err("wrote %s" % t)
            else:
                code = max(code, 2)
        code = max(code, checker.exit_code(res, strict=a.strict))
    return code


def _open_db(a):
    from citecheck import config, dblp_index
    db = Path(a.db).expanduser() if a.db else config.db_path(_cfg())
    conn = dblp_index.connect(str(db)) if db.exists() else None
    if conn is None:
        _err("no index at %s (run `citecheck setup`)" % db)
    return dblp_index, conn


def cmd_search(a):
    dblp_index, conn = _open_db(a)
    if conn is None:
        return 2
    rows = dblp_index.lookup(conn, a.title, a.author, a.limit)
    for r in rows:
        print(json.dumps(r, ensure_ascii=False, default=str))
    return 0 if rows else 1


def cmd_stats(a):
    dblp_index, conn = _open_db(a)
    if conn is None:
        return 2
    for k, v in dblp_index.stats(conn).items():
        print("%s: %s" % (k, v))
    return 0


def cmd_update(a):
    from citecheck import config, setup_wizard
    if (ROOT / ".git").exists() and shutil.which("git"):
        r = subprocess.run(["git", "-C", str(ROOT), "pull", "--ff-only"])
        if r.returncode:
            _err("git pull failed; continuing with the index update")
    else:
        print("Not a git checkout: re-run the installer to update the code.")
    return setup_wizard.update_index(_cfg()) or 0


def _reach(url):
    try:
        req = urllib.request.Request(url, method="HEAD", headers={"User-Agent": "citecheck-doctor"})
        urllib.request.urlopen(req, timeout=5).close()
        return True
    except urllib.error.HTTPError:
        return True  # server answered
    except Exception:
        return False


def cmd_doctor(a):
    ok_all = True

    def line(ok, msg):
        nonlocal ok_all
        ok_all = ok_all and ok
        print(("✓ " if ok else "✗ ") + msg)

    line(sys.version_info >= (3, 9), "python %s" % sys.version.split()[0])
    try:
        sqlite3.connect(":memory:").execute("create virtual table t using fts5(x)")
        line(True, "sqlite FTS5 available")
    except Exception:
        line(False, "sqlite FTS5 missing (install a Python built with FTS5)")
    cfg = _cfg()
    from citecheck import config
    line(Path(config.CONFIG_PATH).exists(), "config: %s" % config.CONFIG_PATH)
    db = config.db_path(cfg)
    if db.exists():
        info = {}
        try:
            from citecheck import dblp_index
            info = dblp_index.stats(dblp_index.connect(str(db)))
        except Exception:
            pass
        age = _index_age_days(info.get("built"))
        line(age is None or age <= STALE_DAYS,
             "index: %s (mode %s, built %s%s)" % (db, info.get("mode", "?"), info.get("built", "?"),
                                                 ", %d days old" % age if age is not None else ""))
    else:
        line(False, "index missing: %s (run `citecheck setup`)" % db)
    probe = db if db.exists() else db.parent
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    free = shutil.disk_usage(str(probe)).free / 1e9
    line(free > 5, "disk free: %.1f GB" % free)
    line(bool(shutil.which("aria2c")), "aria2c %s" % ("found" if shutil.which("aria2c")
                                                       else "not found (curl will be used; slower)"))
    if a.offline:
        print("- network checks skipped (--offline)")
    else:
        for host in ("dblp.org", "api.semanticscholar.org", "api.crossref.org", "api.openalex.org"):
            line(_reach("https://" + host), "network: " + host)
    return 0 if ok_all else 1


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "setup":
        from citecheck import setup_wizard
        return setup_wizard.run(argv[1:])
    p = build_parser()
    a = p.parse_args(argv)
    if not a.cmd:
        p.print_help()
        return 0
    try:
        return {"check": cmd_check, "search": cmd_search, "stats": cmd_stats,
                "update": cmd_update, "doctor": cmd_doctor}[a.cmd](a)
    except KeyboardInterrupt:
        _err("interrupted")
        return 130
    except BrokenPipeError:
        return 0


if __name__ == "__main__":
    sys.exit(main())
