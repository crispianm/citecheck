"""Style/consistency lint for .bib entries. Results are notes, never errors."""
import re

from .bib import clean_title, detex
from .match import bib_venue_raw, canon

REQUIRED = {  # "a|b" = either field
    "article": ["author", "title", "journal|journaltitle", "year|date"],
    "inproceedings": ["author", "title", "booktitle", "year|date"],
    "incollection": ["author", "title", "booktitle", "publisher", "year|date"],
    "book": ["author|editor", "title", "publisher", "year|date"],
    "inbook": ["author|editor", "title", "chapter|pages", "publisher", "year|date"],
    "proceedings": ["title", "year|date"],
    "phdthesis": ["author", "title", "school|institution", "year|date"],
    "mastersthesis": ["author", "title", "school|institution", "year|date"],
    "techreport": ["author", "title", "institution", "year|date"],
    "unpublished": ["author", "title", "note"],
    "manual": ["title"],
}
_ALLCAPS = re.compile(r"^[A-Z][A-Z0-9]*[A-Z][A-Z0-9]*$")      # CNN, GANs excluded (has lowercase)
_CAMEL = re.compile(r"^[A-Za-z]*[a-z][A-Z][A-Za-z]*$|^[A-Z][a-z]+[A-Z][A-Za-z]*$")  # ImageNet, PyTorch, iPhone


def _unprotected_words(title):
    """Words of a title that sit outside any {} group (split on spaces and hyphens)."""
    t = title.strip()
    if t.startswith("{"):
        d = 0
        for i, ch in enumerate(t):  # whole title in one brace group => protected
            d += {"{": 1, "}": -1}.get(ch, 0)
            if d == 0:
                if i == len(t) - 1:
                    return []
                break
    out, depth, cur = [], 0, ""
    for ch in t + " ":
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth = max(0, depth - 1)
        elif depth == 0 and not ch.isspace() and ch != "-":
            cur += ch
            continue
        if cur:
            out.append(cur)
            cur = ""
    return out


def check_title(title):
    bad = []
    for w in _unprotected_words(title):
        w = w.strip(".,:;!?()\"'")
        if "\\" in w or "$" in w or len(w) < 2:
            continue
        if _ALLCAPS.match(w) or _CAMEL.match(w):
            bad.append(w)
    return list(dict.fromkeys(bad))


def lint_entry(e):
    notes, t = [], e["type"]
    missing = [f.replace("|", " or ") for f in REQUIRED.get(t, [])
               if not any(e.get(x, "").strip() for x in f.split("|"))]
    if missing:
        notes.append(f"lint: @{t} is missing {', '.join(missing)}")
    bad = check_title(e.get("title", ""))
    if bad:
        notes.append("lint: unprotected in title (wrap in {}): " + ", ".join(bad))
    if re.search(r"\bet\.?\s+al\b", e.get("author", ""), re.I):
        notes.append("lint: 'et al' in author field; use 'and others'")
    vtext = " ".join(e.get(f, "") for f in ("journal", "journaltitle", "howpublished", "booktitle", "publisher"))
    is_arxiv = bool(re.search(r"arxiv|\bcorr\b", vtext, re.I)) or e.get("archiveprefix", "").lower() == "arxiv"
    if t in ("article", "misc") and is_arxiv:
        blob = " ".join(str(v) for v in e.values())
        if not e.get("eprint") and not re.search(r"\d{4}\.\d{4,5}|[a-z\-]+/\d{7}", blob):
            notes.append("lint: arXiv entry has no eprint or arXiv id")
    doi = e.get("doi", "")
    if doi and re.search(r"https?://|doi\.org", doi, re.I):
        notes.append("lint: doi is written as a URL; give the bare DOI (10.xxxx/...)")
    return notes


def lint_all(entries):
    """(notes by key, duplicate keys, duplicate titles). Entries as from bib.parse_bib."""
    notes, seen, dup_keys = {}, {}, []
    for e in entries:
        notes.setdefault(e["key"], []).extend(lint_entry(e))
        if e["key"] in seen and e["key"] not in dup_keys:
            dup_keys.append(e["key"])
        seen[e["key"]] = e
    # same canonical venue written differently
    raws = {}
    for e in entries:
        raw = re.sub(r"\s+", " ", bib_venue_raw(e)).strip()
        c = canon(raw)
        if raw and c and c != "arXiv":
            raws.setdefault(c, {}).setdefault(raw, 0)
            raws[c][raw] += 1
    for e in entries:
        raw = re.sub(r"\s+", " ", bib_venue_raw(e)).strip()
        v = raws.get(canon(raw)) if raw else None
        if v and len(v) > 1:
            msg = "lint: venue written %d ways: %s" % (len(v), "; ".join("'%s'" % detex(r) for r in sorted(v)))
            if msg not in notes[e["key"]]:
                notes[e["key"]].append(msg)
    titles, dup_titles = {}, []
    for e in entries:
        ct = clean_title(e.get("title", ""))
        if ct and ct in titles and titles[ct] != e["key"]:
            dup_titles.append((titles[ct], e["key"]))
        titles.setdefault(ct, e["key"])
    return notes, dup_keys, dup_titles
