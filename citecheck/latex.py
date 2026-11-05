"""Cited keys from .aux / .tex / a project directory."""
import re
from pathlib import Path

_CITE = re.compile(r"\\([A-Za-z]*cite[A-Za-z]*|nocite)\*?((?:\s*(?:\[[^\]]*\]|\([^)]*\)))*)\s*((?:\{[^{}]*\}\s*)+)")
_AUX = re.compile(r"\\citation\{([^}]*)\}|\\abx@aux@cite\{[^}]*\}\{([^}]*)\}")
_INPUT = re.compile(r"\\(?:input|include|subfile|subfileinclude)\s*\{([^}]*)\}")
_NOT_CITES = {"citestyle", "citeindexfalse"}


def _strip_comments(s):
    return re.sub(r"(?<!\\)((?:\\\\)*)%.*", r"\1", s)  # % after an even run of backslashes


def _split(keys):
    return [k.strip() for k in keys.split(",") if k.strip()]


def _from_aux(text):
    out = set()
    for m in _AUX.finditer(text):
        out.update(_split(m.group(1) or m.group(2) or ""))
    return out


def _from_tex(text):
    out = set()
    for m in _CITE.finditer(_strip_comments(text)):
        if m.group(1) in _NOT_CITES:
            continue
        groups = re.findall(r"\{([^{}]*)\}", m.group(3))
        if not m.group(1).endswith("s"):  # \cites{a}{b} has several groups; \cite{a} one
            groups = groups[:1]
        for g in groups:
            out.update(_split(g))
    return out


def _tex_files(path):
    """The .tex files reachable from path, following \\input / \\include."""
    seen, todo = [], [Path(path)]
    while todo:
        f = todo.pop()
        if f in seen or not f.is_file():
            continue
        seen.append(f)
        try:
            text = _strip_comments(f.read_text(errors="replace"))
        except OSError:
            continue
        for m in _INPUT.finditer(text):
            n = m.group(1).strip()
            for cand in (f.parent / n, f.parent / (n + ".tex"), Path(path).parent / n,
                         Path(path).parent / (n + ".tex")):
                if cand.is_file():
                    todo.append(cand)
                    break
    return seen


def cited_keys(path):
    """Set of cited keys; None means \\nocite{*} (everything is cited)."""
    p = Path(path)
    if p.is_dir():
        auxes = sorted(p.glob("*.aux"))
        if auxes:
            return _merge([a.read_text(errors="replace") for a in auxes], aux=True)
        tex = sorted(p.rglob("*.tex"))
        roots = [t for t in tex if "\\documentclass" in t.read_text(errors="replace")]
        files = []
        for r in roots:
            files += [f for f in _tex_files(r) if f not in files]
        return _merge([f.read_text(errors="replace") for f in (files or tex)], aux=False)
    if p.suffix == ".aux":
        return _merge([p.read_text(errors="replace")], aux=True)
    return _merge([f.read_text(errors="replace") for f in _tex_files(p)], aux=False)


def _merge(texts, aux):
    out = set()
    for t in texts:
        out |= _from_aux(t) if aux else _from_tex(t)
    return None if "*" in out else out


def find_bibs(d):
    """All .bib files under d (not .fixed.bib outputs, not hidden dirs)."""
    d = Path(d)
    return sorted(b for b in d.rglob("*.bib")
                  if not b.name.endswith(".fixed.bib")
                  and not any(part.startswith(".") for part in b.relative_to(d).parts))
