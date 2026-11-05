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
    s = re.sub(r"\\([`'\"^~=.])\s*\{?([A-Za-z])\}?", lambda m: m.group(2) + ACCENTS[m.group(1)], s)
    s = re.sub(r"\\([cvuH])\s*\{([A-Za-z])\}", lambda m: m.group(2) + ACCENTS[m.group(1)], s)
    s = re.sub(r"\\(ss|o|O|l|L|aa|AA|ae|AE)\b", lambda m: {"ss": "ss", "o": "o", "O": "O", "l": "l", "L": "L",
               "aa": "a", "AA": "A", "ae": "ae", "AE": "AE"}[m.group(1)], s)
    s = s.replace("{", "").replace("}", "").replace("\\&", "&").replace("~", " ")
    return unicodedata.normalize("NFC", re.sub(r"\s+", " ", s)).strip()


def fold(s):
    s = unicodedata.normalize("NFKD", s.replace("ß", "ss"))
    return re.sub(r"[^a-z]", "", "".join(c for c in s if not unicodedata.combining(c)).lower())


def clean_title(t):
    t = re.sub(r"\$[^$]*\$", "", t)          # math, e.g. NVDS$^{+}$
    t = re.sub(r"\\[A-Za-z]+|\\.", "", t)    # leftover TeX commands
    return norm_title(t)


def parse_bib(text):
    """Return list of dicts with 'type', 'key' and lowercased field names."""
    out = []
    for m in re.finditer(r"^[ \t]*@(\w+)\s*\{\s*([^,\s]+)\s*,", text, re.M):
        if m.group(1).lower() in ("string", "comment", "preamble"):
            continue
        i, depth, body_start = m.end(), 1, m.end()
        while i < len(text) and depth:
            depth += {"{": 1, "}": -1}.get(text[i], 0)
            i += 1
        body = text[body_start:i - 1]
        e = {"type": m.group(1).lower(), "key": m.group(2)}
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


def bib_surnames(author_field):
    names = [a.strip() for a in re.split(r"\s+and\s+", detex(author_field)) if a.strip()]
    truncated = bool(names) and names[-1].lower() == "others"
    if truncated:
        names = names[:-1]
    sur = []
    for n in names:
        if "," in n:
            sur.append(n.split(",")[0].strip())
        else:
            sur.append(n.split()[-1])
    return sur, truncated


def name_surname(full):
    full = re.sub(r"\s+\d{4}$", "", full.strip())  # DBLP disambiguation suffix
    return full.split()[-1] if full else ""


def surname_match(a, b):
    a, b = fold(a), fold(b)
    return bool(a) and (a == b or a.endswith(b) or b.endswith(a))
