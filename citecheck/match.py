"""Find the record for a bib entry (DBLP, then S2, Crossref, OpenAlex) and compare."""
import re
from difflib import SequenceMatcher

from . import dblp_index
from .bib import bib_surnames, clean_title, detex, fold, name_surname, surname_match

TITLE_MIN = 0.92
LOOSE_MIN = 0.75  # S2 title similarity that counts as a "loose" match worth re-querying DBLP with
NO_PAGES_STYLE = {"NeurIPS", "ICLR", "TMLR", "arXiv"}  # bib never gives pages for these

# Canonical venue names. Order matters: workshops before their main conference,
# Findings before ACL, EMNLP/NAACL/TACL before ACL, JMLR before TMLR.
VENUES = [
    ("CVPR-W", r"cvpr\s*workshops?|computer vision and pattern recognition workshops?"),
    ("ICCV-W", r"iccv\s*workshops?"),
    ("ECCV-W", r"eccv\s*workshops?"),
    ("arXiv", r"\bcorr\b|arxiv"),
    ("CVPR", r"\bcvpr\b|computer vision and pattern recognition"),
    ("ICCV", r"\biccv\b|international conference on computer vision"),
    ("ECCV", r"\beccv\b|european conference on computer vision"),
    ("NeurIPS", r"neurips|\bnips\b|neural information processing"),
    ("ICLR", r"\biclr\b|learning representations"),
    ("ICML", r"\bicml\b|international conference on machine learning"),
    ("AAAI", r"\baaai\b|advancement of artificial intelligence"),
    ("IJCAI", r"ijcai|joint conference on artificial intelligence"),
    ("ACL-Findings", r"findings"),
    ("EMNLP", r"\bemnlp\b|empirical methods in natural language"),
    ("NAACL", r"naacl|north american chapter"),
    ("TACL", r"\btacl\b|transactions of the association"),
    ("ACL", r"\bacl\b|association for computational linguistics"),
    ("KDD", r"\bkdd\b|knowledge discovery and data mining"),
    ("WACV", r"\bwacv\b|winter conference on applications of computer vision"),
    ("BMVC", r"\bbmvc\b|british machine vision"),
    ("MICCAI", r"miccai|medical image computing"),
    ("JMLR", r"\bjmlr\b|j\.? mach\.? learn\.? res|journal of machine learning research"),
    ("TMLR", r"\btmlr\b|trans\.? mach\.? learn\.? res|transactions on machine learning research"),
    ("TPAMI", r"tpami|pattern anal|pattern analysis and machine intelligence"),
    ("IJCV", r"\bijcv\b|int\.? j\.? comput\.? vis|international journal of computer vision"),
    ("TIP", r"\btip\b|trans\.? image process|transactions on image processing"),
    ("TOG", r"trans\.? graph|transactions on graphics"),
    ("TCSVT", r"circuits syst\.? video|circuits and systems for video"),
    ("TMM", r"trans\.? multim|transactions on multimedia"),
    ("RA-L", r"robotics autom\.? lett|robotics and automation letters"),
    ("ICRA", r"\bicra\b|international conference on robotics and automation"),
    ("IROS", r"\biros\b|intelligent robots and systems"),
    ("3DV", r"\b3dv\b|3d vision"),
    ("ICIP", r"\bicip\b|international conference on image processing"),
    ("ICASSP", r"icassp|acoustics,? speech"),
    ("ICME", r"\bicme\b|multimedia and expo"),
    ("ACMMM", r"acm multimedia|acm international conference on multimedia|\bacm mm\b"),
    ("CHI", r"\bchi\b|human factors in computing"),
]
KNOWN = {n for n, _ in VENUES}
JOURNALS = {"TPAMI", "TOG", "TCSVT", "TMM", "RA-L", "TMLR", "JMLR", "TIP", "IJCV", "TACL"}


def canon(v):
    v = detex(v or "").lower()
    for name, pat in VENUES:
        if re.search(pat, v):
            return name
    return re.sub(r"[^a-z]", "", v) or ""


def bib_venue_raw(e):
    return e.get("booktitle") or e.get("journal") or e.get("journaltitle") or e.get("howpublished") or ""


def arxiv_of(rec):
    if rec.get("arxiv"):
        return rec["arxiv"]
    m = re.match(r"journals/corr/abs-(\d{4})-(\d{4,5})", rec.get("key") or "")
    return f"{m.group(1)}.{m.group(2)}" if m else None


def title_score(a, b):
    return SequenceMatcher(None, dblp_index.norm_title(a), dblp_index.norm_title(b)).ratio()


# --- record constructors ----------------------------------------------------

def s2_venue(p):
    pv = p.get("publicationVenue") or {}
    names = [p.get("venue"), pv.get("name"), (p.get("journal") or {}).get("name")] + list(pv.get("alternate_names") or [])
    for n in names:
        cv = canon(n)
        if cv and cv in KNOWN:
            return cv
    first = next((n for n in names if n and n.lower() != "arxiv"), "")
    if not first and (p.get("externalIds") or {}).get("ArXiv"):
        return "arXiv"
    return canon(first)


def s2_rec(p):
    ext = p.get("externalIds") or {}
    j = p.get("journal") or {}
    return {"source": "s2", "key": p.get("paperId"), "title": p.get("title") or "",
            "authors": [a.get("name", "") for a in p.get("authors") or []],
            "year": p.get("year"), "venue": s2_venue(p), "raw_venue": p.get("venue") or "",
            "pages": None, "volume": (j.get("volume") or "").strip() or None, "number": None,
            "doi": ext.get("DOI"), "arxiv": ext.get("ArXiv")}


def dblp_rec(d, number=None):
    return {"source": "dblp", "key": d["key"], "title": d["title"] or "",
            "authors": [a for a in (d["authors"] or "").split("; ") if a],
            "year": d["year"], "venue": canon(d["venue"]), "raw_venue": d["venue"] or "",
            "pages": d["pages"], "volume": d["volume"], "number": number, "doi": d["doi"],
            "score": d["score"]}


def _alt_rec(s, source):
    """Record from a Crossref or OpenAlex summary."""
    doi = s.get("doi")
    venue = canon(s.get("container"))
    if not venue and (s.get("type") in ("posted-content", "preprint") or (doi or "").startswith("10.48550/")):
        venue = "arXiv"
    kind = {"journal-article": "article", "proceedings-article": "inproceedings", "article": "article",
            "posted-content": "misc", "preprint": "misc"}.get(s.get("type"))
    return {"source": source, "key": doi or s.get("id"), "title": s.get("title") or "",
            "authors": [a for a in s.get("authors") or [] if a], "year": s.get("year"),
            "venue": venue, "raw_venue": s.get("container") or "", "pages": (s.get("page") or None),
            "volume": s.get("volume"), "number": s.get("issue"), "doi": doi, "kind": kind,
            "retracted": bool(s.get("retracted"))}


def cr_rec(s):
    return _alt_rec(s, "crossref")


def oa_rec(s):
    return _alt_rec(s, "openalex")


# --- finding ---------------------------------------------------------------

def first_ok(rec, first):
    """First author matches, allowing transliteration variants (Bochkovskii / Bochkovskiy)."""
    if not first:
        return True
    if not rec["authors"]:
        return False
    r = name_surname(rec["authors"][0])
    return surname_match(r, first) or SequenceMatcher(None, fold(r), fold(first)).ratio() >= 0.8


def dblp_search(conn, title, first, dataset=False, surs=()):
    """(published record, preprint record) from DBLP; either may be None.
    A near-exact title whose first author differs is still taken (so compare() reports the
    author error) if another bib author appears in the record."""
    pub = pre = None
    if conn is None:
        return None, None
    wrong = []
    skip = ("proceedings",) if dataset else ("proceedings", "data")  # data records only for @misc datasets
    for d in dblp_index.lookup(conn, title, author=first, limit=10):
        if d["score"] < TITLE_MIN or d["type"] in skip:
            continue
        r = dblp_rec(d, None)
        if not first_ok(r, first):
            rs = [name_surname(a) for a in r["authors"]]
            if d["score"] >= 0.98 and any(surname_match(s, x) for s in surs for x in rs):
                wrong.append(r)
            continue
        if r["venue"] == "arXiv":
            pre = pre or r
        else:
            pub = pub or r
    if not (pub or pre):
        for r in wrong:
            if r["venue"] == "arXiv":
                pre = pre or r
            else:
                pub = pub or r
    return pub, pre


def fill_gaps(rec, e, src):
    """S2 fills only fields DBLP leaves fully empty, and only ones the bib uses."""
    need = [f for f in ("doi", "volume") if e.get(f) and not rec.get(f)]
    if not need:
        return rec
    p = src.s2.match(detex(e.get("title", "")))
    if p:
        sr = s2_rec(p)
        for f in need:
            if sr.get(f):
                rec[f] = sr[f]
                rec.setdefault("filled", []).append(f"{f} from S2")
    return rec


def _fallback(src, title, first, doi):
    """Crossref (by DOI if the bib has one, else by title), then OpenAlex. Accepted record or None."""
    cands = []
    s = src.crossref.get(doi) if doi and "arxiv" not in doi.lower() else None
    if s and s.get("title"):
        cands.append(cr_rec(s))
    else:
        cands += [cr_rec(s) for s in src.crossref.search(title)]
    for r in _accept(cands, title, first):
        return r
    for r in _accept([oa_rec(s) for s in src.openalex.search(title)], title, first):
        return r
    return None


def _accept(cands, title, first):
    for r in cands:
        r["score"] = title_score(title, r["title"])
        if r["score"] >= TITLE_MIN and first_ok(r, first):
            yield r


def find(conn, src, e):
    """DBLP first. Then S2 (a loose match is fed back into DBLP), then Crossref, then OpenAlex."""
    title = detex(e.get("title", ""))
    surs, _ = bib_surnames(e.get("author", ""))
    first = surs[0] if surs else None
    bv = canon(bib_venue_raw(e))

    pub, pre = dblp_search(conn, title, first, dataset=e["type"] == "misc", surs=surs)
    if pub:
        return fill_gaps(pub, e, src)
    if pre and bv == "arXiv":
        return pre
    if pre:
        # DBLP knows only the preprint, so the published venue is a gap: S2 may fill it.
        p = src.s2.match(title)
        sr = s2_rec(p) if p else None
        if sr and sr["venue"] not in ("", "arXiv") and title_score(title, sr["title"]) >= TITLE_MIN:
            rec = dict(pre, venue=sr["venue"], raw_venue=sr["raw_venue"], year=sr["year"],
                       doi=sr["doi"] or pre["doi"], pages=None, source="dblp+s2")
            rec["filled"] = [f"venue/year from S2 ({sr['raw_venue']}); DBLP has only {pre['key']}"]
            return rec
        return pre

    # No DBLP match at all: ask S2.
    p = src.s2.match(title)
    sr = s2_rec(p) if p else None
    if sr:
        sr["score"] = title_score(title, sr["title"])
        if sr["score"] >= LOOSE_MIN:
            # Re-query DBLP with S2's title and first author: the miss may be a typo in the bib.
            s2_first = name_surname(sr["authors"][0]) if sr["authors"] else None
            pub, pre = dblp_search(conn, sr["title"], s2_first)
            hit = pub or pre
            if hit:
                hit["score"] = title_score(title, hit["title"])
                hit["source"] = "dblp via s2"
                hit["filled"] = ["DBLP found only via S2's title; bib title or author likely has a typo"]
                return hit
            if sr["score"] >= TITLE_MIN and first_ok(sr, first):
                return sr  # DBLP has nothing: S2 fills the whole gap
    r = _fallback(src, title, first, (e.get("doi") or "").strip())
    if r:
        return r
    if sr and sr["score"] >= LOOSE_MIN:
        sr["loose"] = True
        return sr
    return None


# --- comparing ---------------------------------------------------------------

def pages_close(a, b, tol=3):
    """Both page ranges numeric and each end within tol (e.g. CVF vs IEEE numbering)."""
    na, nb = re.findall(r"\d+", a), re.findall(r"\d+", b)
    return len(na) == len(nb) and 0 < len(na) <= 2 and all(abs(int(x) - int(y)) <= tol for x, y in zip(na, nb))


def venue_same(bv, rv, source):
    if bv == rv:
        return True
    if source in ("crossref", "openalex") and bv not in KNOWN and rv not in KNOWN and bv and rv:
        # journal names vary a lot (abbreviations); accept near matches from these sources
        return bv in rv or rv in bv or SequenceMatcher(None, bv, rv).ratio() >= 0.75
    return False


def compare(e, rec, cr):
    """Return (errors, notes, published). `published` is the issue text when the bib cites the
    arXiv preprint but the record has a venue version; then year/pages/volume/doi are not compared."""
    diffs, notes, published = [], [], None
    bt = detex(e.get("title", ""))
    if clean_title(bt) != clean_title(rec["title"]):
        diffs.append(f"title: bib '{bt}' vs '{rec['title'].rstrip('.')}'")
    braw = bib_venue_raw(e)
    bv = canon(braw)
    if rec["venue"] and bv == "arXiv" and rec["venue"] != "arXiv":
        published = f"venue: cited as arXiv, published at {rec['venue']} ({rec['raw_venue']})"
    elif rec["venue"] and not venue_same(bv, rec["venue"], rec["source"]):
        diffs.append(f"venue: bib '{detex(braw)}' vs '{rec['raw_venue'] or rec['venue']}'")
    if bv == "arXiv":
        bid = re.search(r"(\d{4}\.\d{4,5})", e.get("journal") or e.get("eprint") or "")
        rid = arxiv_of(rec)
        if bid and rid and bid.group(1) != rid and not published:
            diffs.append(f"arXiv id: bib {bid.group(1)} vs {rid}")
    if not published:
        try:
            by = int(e.get("year", "0"))
        except ValueError:
            by = 0
        if rec["year"] and by != int(rec["year"]):
            diffs.append(f"year: bib {by} vs {rec['year']}" + (" (S2 year may be the arXiv year)" if rec["source"] == "s2" else ""))
        bp = (e.get("pages") or "").replace("--", "-").strip()
        if rec["pages"] and bp != rec["pages"] and bv not in NO_PAGES_STYLE:
            crp = None
            doi = e.get("doi") or rec.get("doi")
            if doi and "arxiv" not in doi.lower():
                x = cr.get(doi)
                crp = x and x.get("page")
            who = {"dblp": "DBLP"}.get(rec["source"], rec["source"])
            if bp and pages_close(bp, rec["pages"]):
                notes.append(f"pages off by a few: bib {bp} vs {who} {rec['pages']} (accepted)")
            elif bp:
                diffs.append(f"pages: bib {bp} vs {who} {rec['pages']}" + (f", Crossref {crp}" if crp else ", no Crossref pages"))
            else:
                notes.append(f"pages missing: {who} {rec['pages']}" + (f", Crossref {crp}" if crp else ", no Crossref pages"))
        bvol = (e.get("volume") or "").strip()
        if bvol and rec["volume"] and bv not in ("ECCV", "arXiv") and bvol != rec["volume"]:
            diffs.append(f"volume: bib {bvol} vs {rec['volume']}")
        bdoi = (e.get("doi") or "").strip().lower()
        if bdoi and rec["doi"] and bdoi != rec["doi"].lower() and "arxiv" not in rec["doi"].lower():
            diffs.append(f"doi: bib {bdoi} vs {rec['doi']}")
    surs, truncated = bib_surnames(e.get("author", ""))
    rs = [name_surname(a) for a in rec["authors"]]
    for i, s in enumerate(surs):
        if i < len(rs) and not surname_match(s, rs[i]) and \
                SequenceMatcher(None, fold(s), fold(rs[i])).ratio() >= 0.8:
            notes.append(f"author #{i + 1} spelled '{s}' in bib, '{rs[i]}' in record")
            continue
        if i >= len(rs) or not surname_match(s, rs[i]):
            diffs.append(f"authors: #{i + 1} bib '{s}' vs '{rs[i] if i < len(rs) else '(none)'}' "
                         f"(record: {'; '.join(rec['authors'])})")
            break
    else:
        if not truncated and len(surs) != len(rs) and rs:
            diffs.append(f"authors: bib lists {len(surs)}, record has {len(rs)} "
                         f"(record: {'; '.join(rec['authors'])})")
    return diffs, notes, published
