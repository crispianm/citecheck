"""Build a clean BibTeX entry from a matched record."""
import re

from .bib import detex
from .match import JOURNALS, arxiv_of, bib_venue_raw, canon


def _esc(s):
    return re.sub(r"(?<!\\)&", r"\\&", s or "")


def _title(rec_title, orig):
    """Record title with the user's {} protection kept where the same text appears."""
    t = _esc((rec_title or "").rstrip("."))
    orig = (orig or "").strip()
    groups = re.findall(r"\{([^{}]+)\}", orig)
    whole = orig.startswith("{") and orig.endswith("}") and re.fullmatch(r"\{[^{}]*\}", orig)
    if whole:
        return "{" + t + "}"
    for g in dict.fromkeys(groups):
        g = _esc(g)
        if g in t and "{" + g + "}" not in t:
            t = t.replace(g, "{" + g + "}", 1)
    return t


def _kind(rec, hint):
    if rec.get("venue") == "arXiv":
        return "misc"
    k = rec.get("key") or ""
    if rec.get("source") == "dblp" and not k.startswith("journals/corr"):
        if k.startswith("journals/"):
            return "article"
        if k.startswith("conf/"):
            return "inproceedings"
    if rec.get("kind") in ("article", "inproceedings"):
        return rec["kind"]
    if rec.get("venue"):
        return "article" if rec["venue"] in JOURNALS else "inproceedings"
    return hint if hint in ("article", "inproceedings", "misc") else "misc"


def to_bibtex(key, entry_type_hint, record, original_entry):
    e = original_entry or {}
    kind = _kind(record, entry_type_hint)
    f = []
    if record.get("authors"):
        f.append(("author", " and ".join(_esc(a) for a in record["authors"])))
    f.append(("title", _title(record.get("title"), e.get("title"))))
    if kind != "misc":
        old = bib_venue_raw(e)
        venue = old if old and canon(old) == record.get("venue") else _esc(record.get("raw_venue") or record.get("venue") or "")
        if venue:
            f.append(("journal" if kind == "article" else "booktitle", venue))
        if record.get("volume"):
            f.append(("volume", record["volume"]))
        num = record.get("number") or (e.get("number") if e.get("volume") == record.get("volume") else None)
        if num:
            f.append(("number", num))
        pages = record.get("pages") or e.get("pages")
        if pages:
            f.append(("pages", re.sub(r"(?<!-)-(?!-)", "--", pages.replace("–", "-"))))
    else:
        aid = arxiv_of(record)
        if aid:
            f += [("eprint", aid), ("archivePrefix", "arXiv")]
    if record.get("year"):
        f.append(("year", str(record["year"])))
    doi = record.get("doi")
    if doi and not (kind == "misc" and "arxiv" in doi.lower()):
        f.append(("doi", doi))
    w = max(len(n) for n, _ in f)
    return "@%s{%s,\n%s\n}" % (kind, key, ",\n".join("  %s = {%s}" % (n.ljust(w), v) for n, v in f))
