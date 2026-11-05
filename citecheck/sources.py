"""Online sources (Semantic Scholar, Crossref, OpenAlex) with JSON caches.

Polite by design: identifying User-Agent (no e-mail), rate limits, retries with
backoff, cache-only when offline. Tests replace `fetch`.
"""
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

UA = "citecheck/0.1 (+https://github.com/crispianm/citecheck)"
S2_URL = "https://api.semanticscholar.org/graph/v1/paper/search/match"
S2_FIELDS = "title,authors,year,venue,publicationVenue,externalIds,journal"
CR_URL = "https://api.crossref.org/works"
OA_URL = "https://api.openalex.org/works"


def fetch(url, headers=None, timeout=30):
    """GET url, return parsed JSON. Raises urllib errors."""
    hdr = {"User-Agent": UA, "Accept": "application/json"}
    hdr.update(headers or {})
    with urllib.request.urlopen(urllib.request.Request(url, headers=hdr), timeout=timeout) as r:
        return json.load(r)


class Cache:
    """dict persisted as JSON; in memory only if path is None."""
    def __init__(self, path):
        self.path = Path(path) if path else None
        self.d = {}
        if self.path and self.path.exists():
            try:
                self.d = json.loads(self.path.read_text())
            except (OSError, ValueError):
                self.d = {}

    def __contains__(self, k):
        return k in self.d

    def get(self, k):
        return self.d.get(k)

    def put(self, k, v):
        self.d[k] = v
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self.d, indent=1, ensure_ascii=False))


class Client:
    name = ""
    interval = 1.0   # seconds between requests

    def __init__(self, enabled=True):
        self.enabled = enabled  # False = cache only
        self.last = 0.0
        self.fails = 0          # consecutive give-ups; client is switched off after 3

    def call(self, url, headers=None):
        """(done, data). done=False means no answer (disabled, or gave up); data None = not found."""
        if not self.enabled or self.fails >= 3:
            return False, None
        for attempt in range(3):
            wait = self.interval - (time.time() - self.last)
            if wait > 0:
                time.sleep(wait)
            self.last = time.time()
            try:
                data = fetch(url, headers)
                self.fails = 0
                return True, data
            except urllib.error.HTTPError as ex:
                if ex.code == 404:
                    self.fails = 0
                    return True, None
                if ex.code == 429 or ex.code >= 500:
                    time.sleep(3 * (attempt + 1))
                    continue
                break
            except (urllib.error.URLError, OSError, ValueError):
                time.sleep(1 + attempt)
        self.fails += 1
        print(f"  {self.name} unavailable for {url[:90]}" +
              ("; off for the rest of this run" if self.fails >= 3 else ""), file=sys.stderr, flush=True)
        return False, None


class S2(Client):
    name, interval = "Semantic Scholar", 1.1

    def __init__(self, cache_dir=None, enabled=True, api_key=None):
        super().__init__(enabled)
        self.cache = Cache(Path(cache_dir) / "s2_cache.json" if cache_dir else None)
        self.key = api_key or os.environ.get("S2_API_KEY")

    def match(self, title):
        """Best S2 paper for a title (dict) or None. Cached by title."""
        if title in self.cache:
            return self.cache.get(title)
        url = S2_URL + "?" + urllib.parse.urlencode({"query": title, "fields": S2_FIELDS})
        done, data = self.call(url, {"x-api-key": self.key} if self.key else None)
        if not done:
            return None  # not cached, so a rerun retries
        res = (data.get("data") or [None])[0] if data else None
        self.cache.put(title, res)
        return res

    def by_id(self, ident):
        """S2 paper (dict) for 'ARXIV:1512.03385' or 'DOI:10.x/y', or None. Cached."""
        k = "id:" + ident.lower()
        if k in self.cache:
            return self.cache.get(k)
        url = S2_URL.replace("/search/match", "/" + urllib.parse.quote(ident, safe=":/")) + "?" + \
            urllib.parse.urlencode({"fields": S2_FIELDS})
        done, data = self.call(url, {"x-api-key": self.key} if self.key else None)
        if not done:
            return None
        res = data if data and data.get("title") else None
        self.cache.put(k, res)
        return res


def _first(v):
    return v[0] if isinstance(v, list) and v else (v if isinstance(v, str) else "")


def _cr_summary(m):
    upd = [u for u in (m.get("updated-by") or []) if "retract" in (u.get("type") or "").lower()]
    notice = any("retract" in (u.get("type") or "").lower() for u in m.get("update-to") or []) \
        or _first(m.get("title")).lower().startswith(("retraction", "retracted"))
    dp = ((m.get("issued") or m.get("published") or {}).get("date-parts") or [[None]])[0][0]
    return {"title": _first(m.get("title")), "container": _first(m.get("container-title")),
            "authors": [(f"{a.get('given', '')} {a.get('family', '')}".strip() or a.get("name", ""))
                        for a in m.get("author") or []],
            "page": m.get("page"), "volume": m.get("volume"), "issue": m.get("issue"),
            "year": dp, "type": m.get("type"), "doi": (m.get("DOI") or "").lower() or None,
            "retracted": bool(upd or "is-retracted-by" in (m.get("relation") or {})),
            "retraction_notice": bool(notice)}


class Crossref(Client):
    name, interval = "Crossref", 0.5

    def __init__(self, cache_dir=None, enabled=True):
        super().__init__(enabled)
        d = Path(cache_dir) if cache_dir else None
        self.cache = Cache(d / "crossref_cache.json" if d else None)          # by DOI
        self.tcache = Cache(d / "crossref_title_cache.json" if d else None)   # by title

    def get(self, doi, need_retraction=False):
        """Summary dict for a DOI. Entries cached before retraction support lack 'retracted'."""
        doi = doi.lower()
        c = self.cache.get(doi)
        if doi in self.cache and (not need_retraction or c is None or "retracted" in c):
            return c
        done, data = self.call(CR_URL + "/" + urllib.parse.quote(doi))
        if not done:
            return c
        res = _cr_summary(data["message"]) if data and data.get("message") else None
        self.cache.put(doi, res)
        return res

    def search(self, title):
        """Top 3 Crossref matches for a title (list of summaries)."""
        if title in self.tcache:
            return self.tcache.get(title) or []
        url = CR_URL + "?" + urllib.parse.urlencode({"query.bibliographic": title, "rows": 3})
        done, data = self.call(url)
        if not done:
            return []
        res = [_cr_summary(m) for m in ((data or {}).get("message") or {}).get("items") or []]
        self.tcache.put(title, res)
        return res


def _oa_summary(w):
    loc = (w.get("primary_location") or {}).get("source") or {}
    bib = w.get("biblio") or {}
    fp, lp = bib.get("first_page"), bib.get("last_page")
    doi = (w.get("doi") or "")
    for p in ("https://doi.org/", "http://doi.org/"):
        doi = doi[len(p):] if doi.startswith(p) else doi
    return {"title": w.get("title") or w.get("display_name") or "", "id": w.get("id"),
            "authors": [(a.get("author") or {}).get("display_name", "") for a in w.get("authorships") or []],
            "year": w.get("publication_year"), "container": loc.get("display_name") or "",
            "doi": doi.lower() or None, "type": w.get("type"),
            "page": (f"{fp}-{lp}" if fp and lp and fp != lp else fp) or None,
            "volume": bib.get("volume"), "issue": bib.get("issue"),
            "retracted": bool(w.get("is_retracted"))}


class OpenAlex(Client):
    name, interval = "OpenAlex", 0.3

    def __init__(self, cache_dir=None, enabled=True):
        super().__init__(enabled)
        self.cache = Cache(Path(cache_dir) / "openalex_cache.json" if cache_dir else None)

    def search(self, title):
        k = "t:" + title
        if k in self.cache:
            return self.cache.get(k) or []
        url = OA_URL + "?" + urllib.parse.urlencode({"search": title, "per-page": 3})
        done, data = self.call(url)
        if not done:
            return []
        res = [_oa_summary(w) for w in (data or {}).get("results") or []]
        self.cache.put(k, res)
        return res

    def by_doi(self, doi):
        k = "d:" + doi.lower()
        if k in self.cache:
            return self.cache.get(k)
        done, data = self.call(OA_URL + "/doi:" + urllib.parse.quote(doi.lower(), safe="/"))
        if not done:
            return None
        res = _oa_summary(data) if data else None
        self.cache.put(k, res)
        return res


class Sources:
    """The three online clients. offline => cache only; no_s2 => S2 cache only."""
    def __init__(self, cache_dir=None, offline=False, no_s2=False, s2_api_key=None):
        self.offline = offline
        self.s2 = S2(cache_dir, not (offline or no_s2), s2_api_key)
        self.crossref = Crossref(cache_dir, not offline)
        self.openalex = OpenAlex(cache_dir, not offline)

    def retraction(self, doi):
        """Text if OpenAlex or Crossref says the DOI is retracted, else None. Offline: cache only."""
        if not doi or doi.lower().startswith("10.48550/"):  # arXiv DOIs are never retracted records
            return None
        cr = self.crossref.get(doi, need_retraction=True)
        if cr and cr.get("retracted"):
            return "Crossref lists a retraction notice for this DOI"
        if cr and cr.get("retraction_notice"):
            return "this DOI is itself a retraction notice"
        oa = self.openalex.by_doi(doi)
        if oa and oa.get("retracted"):
            return "OpenAlex marks this work as retracted"
        return None
