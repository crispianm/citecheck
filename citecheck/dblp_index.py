#!/usr/bin/env python3
"""Local searchable index of the DBLP XML dump (stdlib only).

  python3 -m citecheck.dblp_index build [--gz dblp.xml.gz] [--db dblp.sqlite] [--full] [--lite]
      First run: full build. Later runs: incremental (unchanged (key, mdate)
      skipped, new/changed upserted, gone ones deleted). --full rebuilds from
      scratch. --lite keeps only article/inproceedings/incollection/data/
      phdthesis and drops ee/publtype (much smaller).
  python3 -m citecheck.dblp_index search "paper title" [--author Surname] [-n 5]
  python3 -m citecheck.dblp_index stats

From Python:
  from citecheck import dblp_index; conn = dblp_index.connect(db); dblp_index.lookup(conn, title)
"""
import argparse
import gzip
import html.entities
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from difflib import SequenceMatcher
from pathlib import Path

DBLP_URL = "https://dblp.org/xml/dblp.xml.gz"
MD5_URL = DBLP_URL + ".md5"
LITE_TYPES = {"article", "inproceedings", "incollection", "data", "phdthesis"}
RECORD_TYPES = {"article", "inproceedings", "proceedings", "book", "incollection",
                "phdthesis", "mastersthesis", "data"}  # 'www' = person pages, skipped
COLS = ["key", "mdate", "type", "publtype", "title", "norm", "year", "venue",
        "volume", "pages", "authors", "doi", "ee"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS pubs (
  id INTEGER PRIMARY KEY, key TEXT UNIQUE NOT NULL, mdate TEXT, type TEXT,
  publtype TEXT, title TEXT, norm TEXT, year INTEGER, venue TEXT, volume TEXT,
  pages TEXT, authors TEXT, doi TEXT, ee TEXT);
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
"""
INDEXES = """
CREATE INDEX IF NOT EXISTS pubs_norm ON pubs(norm);
CREATE INDEX IF NOT EXISTS pubs_doi ON pubs(doi);
CREATE VIRTUAL TABLE IF NOT EXISTS fts USING fts5(
  title, authors, content='pubs', content_rowid='id',
  tokenize='unicode61 remove_diacritics 2');
"""
TRIGGERS = """
CREATE TRIGGER IF NOT EXISTS pubs_ai AFTER INSERT ON pubs BEGIN
  INSERT INTO fts(rowid, title, authors) VALUES (new.id, new.title, new.authors); END;
CREATE TRIGGER IF NOT EXISTS pubs_ad AFTER DELETE ON pubs BEGIN
  INSERT INTO fts(fts, rowid, title, authors) VALUES ('delete', old.id, old.title, old.authors); END;
CREATE TRIGGER IF NOT EXISTS pubs_au AFTER UPDATE ON pubs BEGIN
  INSERT INTO fts(fts, rowid, title, authors) VALUES ('delete', old.id, old.title, old.authors);
  INSERT INTO fts(rowid, title, authors) VALUES (new.id, new.title, new.authors); END;
"""

_ENT_RE = re.compile(r"&([A-Za-z][A-Za-z0-9]*);")
_XML_ENTS = {"amp", "lt", "gt", "quot", "apos"}


def _sub_entity(m):
    name = m.group(1)
    if name in _XML_ENTS:
        return m.group(0)
    cp = html.entities.name2codepoint.get(name)
    return chr(cp) if cp else m.group(0)


def norm_title(s):
    import unicodedata
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _open_stream(gz):
    """Decompressed byte stream; pigz on another core if available."""
    if shutil.which("pigz"):
        p = subprocess.Popen(["pigz", "-dc", str(gz)], stdout=subprocess.PIPE, bufsize=1 << 20)
        return p.stdout, p
    return gzip.open(gz, "rb"), None


def iter_records(gz, types=None):
    """Yield (key, mdate, element-or-None-builder). Element is valid until next yield."""
    stream, proc = _open_stream(gz)
    parser = ET.XMLPullParser(events=("start", "end"))
    depth, root, carry = 0, None, ""
    try:
        while True:
            chunk = stream.read(1 << 22)
            if not chunk:
                break
            text = carry + chunk.decode("iso-8859-1")
            cut = text.rfind("\n") + 1  # never split an entity across feeds
            text, carry = text[:cut], text[cut:]
            if root is None:  # drop the XML declaration and external DOCTYPE
                text = re.sub(r"^<\?xml[^>]*\?>\s*<!DOCTYPE[^>]*>", "", text)
            parser.feed(_ENT_RE.sub(_sub_entity, text))
            for ev, el in parser.read_events():
                if ev == "start":
                    depth += 1
                    if depth == 1:
                        root = el
                    continue
                depth -= 1
                if depth == 1:
                    if el.tag in (types or RECORD_TYPES):
                        yield el
                    root.clear()  # free memory as we go
        if carry:
            parser.feed(_ENT_RE.sub(_sub_entity, carry))
        parser.close()
    finally:
        if proc:
            proc.kill()
            proc.wait()


def _text(el):
    return re.sub(r"\s+", " ", "".join(el.itertext())).strip() if el is not None else ""


def to_row(el, lite=False):
    title = re.sub(r"\.$", "", _text(el.find("title")))
    authors = [re.sub(r"\s+\d{4}$", "", _text(a)) for a in el.findall("author")] \
        or [re.sub(r"\s+\d{4}$", "", _text(a)) for a in el.findall("editor")]
    ees = [_text(e) for e in el.findall("ee")]
    doi = next((re.sub(r"^https?://(dx\.)?doi\.org/", "", e).lower()
                for e in ees if "doi.org/" in e), None)
    year = _text(el.find("year"))
    return (el.get("key"), el.get("mdate"), el.tag, None if lite else el.get("publtype"), title,
            norm_title(title), int(year) if year.isdigit() else None,
            _text(el.find("journal")) or _text(el.find("booktitle")) or _text(el.find("publisher")),
            _text(el.find("volume")) or None, _text(el.find("pages")) or None,
            "; ".join(authors), doi, None if lite or not ees else ees[0])


def _progress(n, elapsed, extra=""):
    print(f"\r  {n:,} records  {n / max(elapsed, 1e-9):,.0f}/s  {extra}",
          end="", file=sys.stderr, flush=True)


def _db_mode(db):
    try:
        c = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            r = c.execute("SELECT v FROM meta WHERE k='mode'").fetchone()
        finally:
            c.close()
        return r[0] if r else "full"  # indexes built before 'mode' existed are full
    except sqlite3.Error:
        return None


def build(gz, db, full=False, lite=False, progress=None, log=None):
    """Build or update the index. progress(n_records, elapsed_s) is called every 200k records;
    log(message) gets the phase messages (default: stderr)."""
    gz = Path(gz)
    if not gz.exists():
        raise FileNotFoundError(f"no such file: {gz}")
    db = Path(db)
    db.parent.mkdir(parents=True, exist_ok=True)
    mode = "lite" if lite else "full"
    types = LITE_TYPES if lite else None
    # a lite/full switch needs a rebuild: the record sets differ
    incremental = db.exists() and not full and _db_mode(db) == mode
    t0 = time.time()
    prog = progress or (lambda n, el, extra="": _progress(n, el, extra))
    say = log or (lambda m: print(m, file=sys.stderr))
    if incremental:
        conn = sqlite3.connect(db)
        conn.executescript("PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL;" + TRIGGERS)
        say("loading existing keys...")
        old = dict(conn.execute("SELECT key, mdate FROM pubs"))
        seen, changed, added, n = set(), 0, 0, 0
        ph = ",".join("?" * len(COLS))
        upsert = (f"INSERT INTO pubs({','.join(COLS)}) VALUES ({ph}) ON CONFLICT(key) DO UPDATE SET "
                  + ",".join(f"{c}=excluded.{c}" for c in COLS[1:]))
        batch = []
        conn.execute("BEGIN")
        for el in iter_records(gz, types):
            n += 1
            k = el.get("key")
            seen.add(k)
            prev = old.get(k)
            if prev == el.get("mdate"):
                pass
            else:
                batch.append(to_row(el, lite))
                if prev is None:
                    added += 1
                else:
                    changed += 1
                if len(batch) >= 20000:
                    conn.executemany(upsert, batch)
                    batch.clear()
            if n % 200000 == 0:
                prog(n, time.time() - t0)
        conn.executemany(upsert, batch)
        gone = [k for k in old if k not in seen]
        conn.executemany("DELETE FROM pubs WHERE key=?", ((k,) for k in gone))
        say(f"\n  new {added:,}, changed {changed:,}, removed {len(gone):,}")
    else:
        tmp = Path(str(db) + ".building")
        tmp.unlink(missing_ok=True)
        conn = sqlite3.connect(tmp)
        conn.executescript("PRAGMA journal_mode=OFF; PRAGMA synchronous=OFF; "
                           "PRAGMA cache_size=-1000000;" + SCHEMA)
        ins = f"INSERT OR REPLACE INTO pubs({','.join(COLS)}) VALUES ({','.join('?' * len(COLS))})"
        batch, n = [], 0
        conn.execute("BEGIN")
        for el in iter_records(gz, types):
            batch.append(to_row(el, lite))
            n += 1
            if len(batch) >= 50000:
                conn.executemany(ins, batch)
                batch.clear()
            if n % 200000 == 0:
                prog(n, time.time() - t0)
        conn.executemany(ins, batch)
        conn.commit()
        say(f"\n  {n:,} records parsed; building indexes...")
        conn.executescript(INDEXES + "INSERT INTO fts(fts) VALUES ('rebuild');" + TRIGGERS)
    st = gz.stat()
    conn.executemany("INSERT OR REPLACE INTO meta VALUES (?, ?)", [
        ("source", str(gz.resolve())), ("source_size", str(st.st_size)),
        ("source_mtime", time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(st.st_mtime))),
        ("built", time.strftime("%Y-%m-%d %H:%M:%S")), ("mode", mode),
        ("records", str(conn.execute("SELECT count(*) FROM pubs").fetchone()[0]))])
    conn.commit()
    if not incremental:
        conn.execute("INSERT INTO fts(fts) VALUES ('optimize')")
        conn.commit()
        conn.close()
        os.replace(tmp, db)
    else:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.close()
    say(f"done in {(time.time() - t0) / 60:.1f} min -> {db}")


# --- lookup ---------------------------------------------------------------

def default_db():
    try:
        from . import config
        return config.db_path()
    except Exception:  # noqa: BLE001  (config.py is optional here)
        return Path("dblp.sqlite")


def connect(db=None):
    """Read-only connection, or None if there is no index."""
    db = db or default_db()
    if not Path(db).exists():
        return None
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def lookup(conn, title, author=None, limit=5):
    """Best DBLP matches for a title, as dicts with a 'score' (0-1)."""
    n = norm_title(title)
    rows = list(conn.execute("SELECT * FROM pubs WHERE norm=? LIMIT 20", (n,))) if n else []
    if not rows:
        words = [w for w in re.findall(r"[A-Za-z0-9]+", title) if len(w) > 2][:12]
        if not words:
            return []
        author = " ".join(re.findall(r"\w+", author or ""))  # no quotes: they break the MATCH syntax
        for op in (" AND ", " OR "):
            match = op.join(f'"{w}"' for w in words)
            if author:
                match = f"({match}) AND authors:\"{author}\"" if op == " AND " else match
            rows = list(conn.execute(
                "SELECT pubs.* FROM fts JOIN pubs ON pubs.id=fts.rowid "
                "WHERE fts MATCH ? ORDER BY bm25(fts) LIMIT 50", (match,)))
            if rows:
                break
    out = []
    for r in rows:
        d = dict(r)
        d["score"] = SequenceMatcher(None, n, r["norm"]).ratio()
        out.append(d)
    out.sort(key=lambda d: (d["score"], d["type"] != "proceedings",
                            not (d["key"] or "").startswith("journals/corr")), reverse=True)
    return out[:limit]


def stats(conn):
    """Meta rows plus 'types' (record counts by type). 'mode' defaults to 'full'."""
    d = {k: v for k, v in conn.execute("SELECT k, v FROM meta")}
    d.setdefault("mode", "full")
    d["types"] = {t: c for t, c in conn.execute("SELECT type, count(*) FROM pubs GROUP BY type ORDER BY 2 DESC")}
    return d


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    dd = str(default_db())
    b = sub.add_parser("build")
    b.add_argument("--gz", default="dblp.xml.gz")
    b.add_argument("--db", default=dd)
    b.add_argument("--full", action="store_true")
    b.add_argument("--lite", action="store_true")
    s = sub.add_parser("search")
    s.add_argument("title")
    s.add_argument("--author")
    s.add_argument("-n", type=int, default=5)
    s.add_argument("--db", default=dd)
    t = sub.add_parser("stats")
    t.add_argument("--db", default=dd)
    a = ap.parse_args()

    if a.cmd == "build":
        try:
            build(a.gz, a.db, a.full, a.lite)
        except FileNotFoundError as ex:
            sys.exit(str(ex))
        return
    conn = connect(a.db)
    if conn is None:
        sys.exit(f"no index at {a.db}; run: python3 -m citecheck.dblp_index build")
    if a.cmd == "stats":
        st = stats(conn)
        types = st.pop("types")
        for k, v in sorted(st.items()):
            print(f"{k:13} {v}")
        for t_, c in types.items():
            print(f"  {t_:14} {c:,}")
    else:
        for d in lookup(conn, a.title, a.author, a.n):
            print(f"{d['score']:.2f}  {d['year']}  {d['venue']}  [{d['key']}]\n      {d['title']}\n"
                  f"      {d['authors'][:100]}" + (f"\n      doi:{d['doi']}" if d['doi'] else ""))


if __name__ == "__main__":
    main()
