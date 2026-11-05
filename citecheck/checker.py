"""Check a .bib against DBLP (and online fallbacks). Pure library: no printing, no exit."""
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional, Set, Tuple

from . import dblp_index
from .bib import parse_bib
from .fix import to_bibtex
from .lint import lint_all
from .match import NO_PAGES_STYLE, bib_venue_raw, canon, compare, find
from .sources import Sources

STATUSES = ("mismatch", "retracted", "manual", "published", "note", "ok")


@dataclass
class EntryResult:
    key: str
    entry_type: str
    status: str = "ok"
    source: str = ""
    record_key: str = ""
    venue: str = ""
    doi: str = ""
    title: str = ""
    url: str = ""
    issues: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)
    suggested_bibtex: Optional[str] = None
    fill_only: bool = False  # suggestion only adds missing pages/volume/doi (status stays note)


@dataclass
class Result:
    bib: str
    entries: List[EntryResult] = field(default_factory=list)
    missing_keys: List[str] = field(default_factory=list)
    unused_keys: List[str] = field(default_factory=list)
    duplicate_keys: List[str] = field(default_factory=list)
    duplicate_titles: List[Tuple[str, str]] = field(default_factory=list)
    index_info: dict = field(default_factory=dict)

    def counts(self):
        out = {}
        for e in self.entries:
            out[e.status] = out.get(e.status, 0) + 1
        return out


def _url(rec):
    if rec["source"] in ("dblp", "dblp+s2", "dblp via s2") and (rec.get("key") or "").count("/"):
        return "https://dblp.org/rec/" + rec["key"]
    if rec["source"] == "s2" and rec.get("key"):
        return "https://www.semanticscholar.org/paper/" + rec["key"]
    return "https://doi.org/" + rec["doi"] if rec.get("doi") else ""


def _fills(e, rec, bv):
    """True if the record has pages/volume/doi that the bib entry lacks."""
    miss = lambda f: rec.get(f) and not (e.get(f) or "").strip()  # noqa: E731
    return bool(miss("volume") or (miss("pages") and bv not in NO_PAGES_STYLE)
                or (miss("doi") and "arxiv" not in rec["doi"].lower()))


def check_entry(conn, src, e):
    """EntryResult (without lint notes) for one parsed bib entry."""
    rec = find(conn, src, e)
    r = EntryResult(e["key"], e["type"])
    bv = canon(bib_venue_raw(e))
    published = None
    if rec is None:
        r.status, r.issues = "manual", ["no match in DBLP, Semantic Scholar, Crossref or OpenAlex"]
    elif rec.get("loose"):
        r.status = "manual"
        r.issues = [f"no DBLP match; S2 has a loose match only: '{rec['title']}' ({rec['raw_venue']} {rec['year']})"]
    elif rec["venue"] == "arXiv" and bv != "arXiv":
        r.status = "manual"
        r.issues = [f"only the arXiv preprint found; {bv} version not in DBLP or S2"]
    else:
        errs, notes, published = compare(e, rec, src.crossref)
        r.issues = errs + ([published] if published else [])
        r.notes = notes + rec.get("filled", [])
        r.status = "mismatch" if errs else ("published" if published else ("note" if notes else "ok"))
    if rec:
        r.source, r.record_key, r.venue = rec["source"], rec.get("key") or "", rec["venue"]
        r.doi, r.title, r.url = rec.get("doi") or "", rec["title"], _url(rec)
        if r.status in ("mismatch", "published") and rec["authors"]:
            r.suggested_bibtex = to_bibtex(e["key"], e["type"], rec, e)
        elif r.status == "note" and rec["authors"] and _fills(e, rec, bv) and \
                not any("spelled" in n or "accepted" in n for n in r.notes):  # variants stay as written
            r.suggested_bibtex, r.fill_only = to_bibtex(e["key"], e["type"], rec, e), True
    # retraction: only with a DOI, and only if a source answers (offline = cache only)
    why = src.retraction((e.get("doi") or "").strip() or r.doi)
    if not why and rec and rec.get("retracted"):
        why = "source marks this work as retracted"
    if why:
        r.status = "retracted"
        r.issues.insert(0, "retracted: " + why)
    return r


def check(bib_path, *, db=None, cite_keys=None, offline=False, no_s2=False, lint=True,
          only=None, cache_dir=None, s2_api_key=None, progress=None):
    bib_path = Path(bib_path)
    parsed = parse_bib(bib_path.read_text(errors="replace"))
    res = Result(bib=str(bib_path))
    all_keys = {e["key"] for e in parsed}
    lint_notes, res.duplicate_keys, res.duplicate_titles = lint_all(parsed)
    if cite_keys is not None:
        res.missing_keys = sorted(set(cite_keys) - all_keys)
        res.unused_keys = sorted(all_keys - set(cite_keys))
        parsed = [e for e in parsed if e["key"] in cite_keys]
    if only:
        parsed = [e for e in parsed if e["key"] in only]
    conn = dblp_index.connect(db) if db else None  # missing index => online only
    if conn is not None:
        res.index_info = dblp_index.stats(conn)
    src = Sources(cache_dir, offline=offline, no_s2=no_s2, s2_api_key=s2_api_key)
    seen = set()
    for i, e in enumerate(parsed):
        if e["key"] in seen:  # duplicate key: checked once
            continue
        seen.add(e["key"])
        r = check_entry(conn, src, e)
        if lint:
            r.notes += lint_notes.get(e["key"], [])
        res.entries.append(r)
        if progress:
            progress(i + 1, len(parsed), e["key"], r.status)
    return res


def exit_code(result, strict=False):
    bad = {"mismatch", "retracted"} | ({"manual"} if strict else set())
    return 1 if result.missing_keys or any(e.status in bad for e in result.entries) else 0
