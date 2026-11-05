"""BibTeX parsing and name/title helpers (stdlib only)."""
import re
import unicodedata

from .dblp_index import norm_title

ACCENTS = {"'": "\u0301", "`": "\u0300", '"': "\u0308", "^": "\u0302", "~": "\u0303",
           "c": "\u0327", "v": "\u030c", "u": "\u0306", "=": "\u0304", ".": "\u0307", "H": "\u030b"}
_FIELD = re.compile(r"\s*([\w-]+)\s*=\s*")
_END = re.compile(r"[,\n]")
_COMMA = re.compile(r"\s*,")


def detex(s):
    s = re.sub(r"\\([ij])(?![A-Za-z])", r"\1", s)  # dotless \i \j, so {\'\i} -> {\'i}
    s = re.sub(r"\\([`'\"^~=.])\s*\{?([A-Za-z])\}?", lambda m: m.group(2) + ACCENTS[m.group(1)], s)
    s = re.sub(r"\\([cvuH])\s*\{([A-Za-z])\}", lambda m: m.group(2) + ACCENTS[m.group(1)], s)
    s = re.sub(r"\\(ss|o|O|l|L|aa|AA|ae|AE)\b", lambda m: {"ss": "ss", "o": "o", "O": "O", "l": "l", "L": "L",
               "aa": "a", "AA": "A", "ae": "ae", "AE": "AE"}[m.group(1)], s)
    s = re.sub(r"\\[A-Za-z]+\s*\{", "{", s)  # \emph{in situ} -> in situ (not "\emphin")
    s = s.replace("{", "").replace("}", "").replace("\\&", "&").replace("~", " ")
    return unicodedata.normalize("NFC", re.sub(r"\s+", " ", s)).strip()


def fold(s):
    s = unicodedata.normalize("NFKD", s.replace("ß", "ss"))
    return re.sub(r"[^a-z]", "", "".join(c for c in s if not unicodedata.combining(c)).lower())


def clean_title(t):
    t = re.sub(r"\$[^$]*\$", "", t)          # math, e.g. NVDS$^{+}$
    t = re.sub(r"\\[A-Za-z]+|\\.", "", t)    # leftover TeX commands
    return norm_title(t)


_ENTRY = re.compile(r"^[ \t]*(@(\w+)\s*([{(])\s*([^,\s{}()]+)\s*,)", re.M)


def entry_spans(text):
    """[(type, key, start, body_start, end)] for each @type{key, ...} or @type(key, ...) entry.
    `start` is the '@'; `end` is just past the closing delimiter."""
    out = []
    for m in _ENTRY.finditer(text):
        if m.group(2).lower() in ("string", "comment", "preamble"):
            continue
        paren, i, depth, quote = m.group(3) == "(", m.end(), 0, False
        while i < len(text):
            ch = text[i]
            if ch == "{":
                depth += 1
            elif ch == "}":
                if depth == 0 and not paren:
                    break
                depth -= 1
            elif ch == '"' and depth == 0 and paren:
                quote = not quote
            elif ch == ")" and depth == 0 and not quote and paren:
                break
            i += 1
        out.append((m.group(2).lower(), m.group(4), m.start(1), m.end(), min(i + 1, len(text))))
    return out


def parse_bib(text):
    """Return list of dicts with 'type', 'key' and lowercased field names."""
    out = []
    for typ, key, _, body_start, end in entry_spans(text):
        body = text[body_start:end - 1]
        e = {"type": typ, "key": key}
        j = 0
        while True:
            fm = _FIELD.match(body, j)
            if not fm or fm.end() >= len(body):
                break
            j = fm.end()
            if body[j] == "{":
                d, k = 1, j + 1
                while d and k < len(body):
                    d += {"{": 1, "}": -1}.get(body[k], 0)
                    k += 1
                val, j = body[j + 1:k - 1], k
            elif body[j] == '"':
                d, k = 0, j + 1
                while k < len(body) and not (body[k] == '"' and d == 0):
                    d += {"{": 1, "}": -1}.get(body[k], 0)
                    k += 1
                val, j = body[j + 1:k], k + 1
            else:
                em = _END.search(body, j)
                k = em.start() if em else len(body)
                val, j = body[j:k].strip(), k
            e[fm.group(1).lower()] = val
            cm = _COMMA.match(body, j)
            if not cm:
                break
            j = cm.end()
        out.append(e)
    return out


def _bib_names(author_field):
    """[(surname, given)] and whether the list is truncated by 'and others'."""
    names = [a.strip() for a in re.split(r"\s+and\s+", detex(author_field), flags=re.I) if a.strip()]
    truncated = bool(names) and names[-1].lower() == "others"
    if truncated:
        names = names[:-1]
    out = []
    for n in names:
        if "," in n:
            sur, _, given = n.partition(",")
            out.append((sur.strip(), given.split(",")[-1].strip()))
        else:
            parts = n.split()
            out.append((parts[-1], " ".join(parts[:-1])))
    return out, truncated


def bib_surnames(author_field):
    names, truncated = _bib_names(author_field)
    return [s for s, _ in names], truncated


def bib_givens(author_field):
    return [g for _, g in _bib_names(author_field)[0]]


def name_given(full):
    full = re.sub(r"\s+\d{4}$", "", full.strip())
    return " ".join(full.split()[:-1])


def name_surname(full):
    full = re.sub(r"\s+\d{4}$", "", full.strip())  # DBLP disambiguation suffix
    return full.split()[-1] if full else ""


def surname_match(a, b):
    """Same surname after folding; one may also be the other's tail at a word/hyphen/apostrophe
    boundary (van den Berg / Berg, Al-Hashimi / Hashimi), but not a bare suffix (Li / Ali)."""
    if not fold(a) or not fold(b):
        return False
    if fold(a) == fold(b):
        return True
    ta, tb = [[fold(t) for t in re.split(r"[\s\-'\u2019]+", x) if fold(t)] for x in (a, b)]
    short, long_ = sorted((ta, tb), key=len)
    return len(short) < len(long_) and long_[-len(short):] == short
