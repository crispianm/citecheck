"""Reports: terminal, Markdown, CSV and self-contained HTML."""
import csv
import html
import os
import sys

ORDER = ["retracted", "mismatch", "published", "manual", "note", "ok"]
TITLES = {
    "retracted": "Retracted", "mismatch": "Mismatch", "published": "Published version exists",
    "manual": "Needs manual check", "note": "Notes", "ok": "OK",
}
COLORS = {"retracted": "31;1", "mismatch": "31", "published": "33", "manual": "35",
          "note": "36", "ok": "32"}


def _by_status(result):
    d = {s: [] for s in ORDER}
    for e in result.entries:
        d.setdefault(e.status, []).append(e)
    return d


def _counts(result):
    return {s: len(v) for s, v in _by_status(result).items()}


def _summary_line(result):
    c = _counts(result)
    parts = ["%d %s" % (c[s], s) for s in ORDER if c.get(s)]
    return "%d entries: %s" % (len(result.entries), ", ".join(parts) or "none")


# ---------------------------------------------------------------- terminal
def _use_color(color):
    if color is None:
        return sys.stdout.isatty() and "NO_COLOR" not in os.environ
    return bool(color)


def print_terminal(result, color=None, verbose=False, file=None):
    out = file or sys.stdout
    col = _use_color(color)

    def c(code, s):
        return "\033[%sm%s\033[0m" % (code, s) if col else s

    def p(s=""):
        print(s, file=out)

    def entry(e):
        head = "%s  [%s]" % (c("1", e.key), c(COLORS.get(e.status, "0"), e.status))
        if e.source:
            head += "  via " + e.source
        p("  " + head)
        for i in e.issues:
            p("    - " + i)
        if verbose or e.status == "note":
            for n in e.notes:
                p("    . " + n)
        if e.url:
            p("    record: " + e.url)
        if e.suggested_bibtex:
            p("    suggested:")
            for ln in e.suggested_bibtex.splitlines():
                p("      " + ln)

    p(c("1", "citecheck: " + str(result.bib)))
    p(_summary_line(result))
    for k in ("missing_keys", "duplicate_keys", "unused_keys"):
        if getattr(result, k):
            p("%d %s" % (len(getattr(result, k)), k.replace("_", " ")))
    p()
    g = _by_status(result)
    for s in ("retracted", "mismatch", "published"):
        if g[s]:
            p(c(COLORS[s], "== %s (%d) ==" % (TITLES[s], len(g[s]))))
            for e in g[s]:
                entry(e)
            p()
    if result.missing_keys:
        p(c("31", "== Cited but missing from the bib (%d) ==" % len(result.missing_keys)))
        for k in result.missing_keys:
            p("  " + k)
        p()
    if g["manual"]:
        p(c(COLORS["manual"], "== %s (%d) ==" % (TITLES["manual"], len(g["manual"]))))
        for e in g["manual"]:
            entry(e)
        p()
    if g["note"]:
        if verbose:
            p(c(COLORS["note"], "== Notes (%d) ==" % len(g["note"])))
            for e in g["note"]:
                entry(e)
        else:
            p("%d entries have notes only (use -v to list)" % len(g["note"]))
        p()
    if result.duplicate_keys or result.duplicate_titles:
        p(c("33", "== Duplicates =="))
        for k in result.duplicate_keys:
            p("  duplicate key: " + k)
        for a, b in result.duplicate_titles:
            p("  same title: %s / %s" % (a, b))
        p()
    if result.unused_keys:
        p("== Unused keys (%d) ==" % len(result.unused_keys))
        p("  " + ", ".join(result.unused_keys))
        p()


# ---------------------------------------------------------------- markdown
def _md(s):
    return str(s).replace("|", "\\|").replace("\n", " ")


def write_md(result, path):
    L = ["# citecheck report", "", "File: `%s`" % result.bib, "", _summary_line(result), ""]
    L += ["| key | status | source | venue | issues |", "|---|---|---|---|---|"]
    for s in ORDER:
        for e in _by_status(result)[s]:
            L.append("| %s | %s | %s | %s | %s |" % (
                _md(e.key), e.status, _md(e.source), _md(e.venue or ""), _md("; ".join(e.issues))))
    for title, items in (("Missing keys", result.missing_keys), ("Duplicate keys", result.duplicate_keys),
                         ("Unused keys", result.unused_keys)):
        if items:
            L += ["", "## %s" % title, ""] + ["- `%s`" % k for k in items]
    if result.duplicate_titles:
        L += ["", "## Duplicate titles", ""] + ["- `%s` / `%s`" % t for t in result.duplicate_titles]
    sugg = [e for e in result.entries if e.suggested_bibtex]
    if sugg:
        L += ["", "## Suggested entries", ""]
        for e in sugg:
            L += ["### %s (%s)" % (e.key, e.status), "", "```bibtex", e.suggested_bibtex, "```", ""]
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")


# ---------------------------------------------------------------- csv
def write_csv(result, path):
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["key", "status", "source", "venue", "record_key", "doi", "url", "issues", "notes"])
        for e in result.entries:
            w.writerow([e.key, e.status, e.source, e.venue or "", e.record_key or "", e.doi or "",
                        e.url or "", " | ".join(e.issues), " | ".join(e.notes)])


# ---------------------------------------------------------------- html
_CSS = """
:root{--bg:#fff;--fg:#1c1e21;--mut:#666;--card:#f6f7f9;--bd:#d8dce1;--code:#eef0f3}
@media(prefers-color-scheme:dark){:root{--bg:#16181b;--fg:#e6e8eb;--mut:#9aa0a6;--card:#1f2226;--bd:#33373d;--code:#262a2f}}
body{font:15px/1.5 system-ui,sans-serif;background:var(--bg);color:var(--fg);max-width:960px;margin:0 auto;padding:16px}
h1{font-size:1.4em}.mut{color:var(--mut)}
.bar{display:flex;height:14px;border-radius:7px;overflow:hidden;margin:12px 0}
.s-ok{--c:#2e9e5b}.s-note{--c:#2a8fb0}.s-manual{--c:#9b59b6}.s-published{--c:#d9a400}
.s-mismatch{--c:#e0533d}.s-retracted{--c:#a31515}
.bar span{background:var(--c)}
button{font:inherit;border:1px solid var(--bd);background:var(--card);color:var(--fg);border-radius:6px;padding:2px 10px;cursor:pointer}
button.on{border-color:var(--fg);font-weight:600}
#q{font:inherit;padding:4px 8px;border:1px solid var(--bd);border-radius:6px;background:var(--card);color:var(--fg);margin-left:8px}
details{background:var(--card);border:1px solid var(--bd);border-radius:8px;margin:6px 0;padding:6px 12px}
summary{cursor:pointer}.tag{display:inline-block;border-radius:4px;padding:0 7px;color:#fff;background:var(--c);font-size:.85em;margin-left:6px}
pre{background:var(--code);padding:8px;border-radius:6px;overflow-x:auto;white-space:pre-wrap}
a{color:#3b82f6}.hide{display:none}
"""
_JS = """
var cur='all';
function flt(){var q=document.getElementById('q').value.toLowerCase();
document.querySelectorAll('details.e').forEach(function(d){
var ok=(cur=='all'||d.dataset.s==cur)&&d.dataset.t.indexOf(q)>=0;d.classList.toggle('hide',!ok);});}
document.querySelectorAll('button[data-f]').forEach(function(b){b.onclick=function(){
cur=b.dataset.f;document.querySelectorAll('button[data-f]').forEach(function(x){x.classList.toggle('on',x==b)});flt();};});
document.getElementById('q').oninput=flt;
document.querySelectorAll('button.cp').forEach(function(b){b.onclick=function(){
var t=b.nextElementSibling.textContent;
if(navigator.clipboard){navigator.clipboard.writeText(t)}else{var r=document.createRange();r.selectNode(b.nextElementSibling);
getSelection().removeAllRanges();getSelection().addRange(r);document.execCommand('copy')}
b.textContent='copied';setTimeout(function(){b.textContent='copy'},1200);};});
"""


def write_html(result, path):
    h = html.escape
    g = _by_status(result)
    cnt = _counts(result)
    n = max(len(result.entries), 1)
    P = ['<!doctype html><html lang="en"><head><meta charset="utf-8">',
         '<meta name="viewport" content="width=device-width,initial-scale=1">',
         '<title>citecheck report</title><style>%s</style></head><body>' % _CSS,
         '<h1>citecheck report</h1><div class="mut">%s</div>' % h(str(result.bib)),
         '<p>%s</p>' % h(_summary_line(result)), '<div class="bar">']
    for s in ORDER:
        if cnt.get(s):
            P.append('<span class="s-%s" style="width:%.2f%%" title="%s: %d"></span>'
                     % (s, 100.0 * cnt[s] / n, s, cnt[s]))
    P.append('</div><div><button data-f="all" class="on">all (%d)</button>' % len(result.entries))
    for s in ORDER:
        if cnt.get(s):
            P.append(' <button data-f="%s">%s (%d)</button>' % (s, s, cnt[s]))
    P.append('<input id="q" type="search" placeholder="search key, title, issue"></div>')
    for title, items in (("Missing keys", result.missing_keys), ("Duplicate keys", result.duplicate_keys),
                         ("Unused keys", result.unused_keys)):
        if items:
            P.append('<details><summary>%s (%d)</summary><p>%s</p></details>'
                     % (title, len(items), h(", ".join(items))))
    if result.duplicate_titles:
        P.append('<details><summary>Duplicate titles (%d)</summary><ul>%s</ul></details>' % (
            len(result.duplicate_titles),
            "".join("<li>%s / %s</li>" % (h(a), h(b)) for a, b in result.duplicate_titles)))
    for s in ORDER:
        for e in g[s]:
            text = " ".join([e.key, e.title or "", e.venue or "", " ".join(e.issues), " ".join(e.notes)]).lower()
            P.append('<details class="e s-%s" data-s="%s" data-t="%s"><summary><b>%s</b>'
                     '<span class="tag">%s</span> <span class="mut">%s</span></summary>'
                     % (s, s, h(text, quote=True), h(e.key), h(e.status), h(e.title or "")))
            if e.source or e.venue:
                P.append('<p class="mut">%s %s</p>' % (h(e.source or ""), h(e.venue or "")))
            if e.issues:
                P.append("<ul>%s</ul>" % "".join("<li>%s</li>" % h(i) for i in e.issues))
            if e.notes:
                P.append('<ul class="mut">%s</ul>' % "".join("<li>%s</li>" % h(i) for i in e.notes))
            if e.url and e.url.startswith(("http://", "https://")):
                P.append('<p><a href="%s" rel="noopener">%s</a></p>' % (h(e.url, quote=True), h(e.url)))
            elif e.url:
                P.append("<p>%s</p>" % h(e.url))
            if e.suggested_bibtex:
                P.append('<button class="cp">copy</button><pre>%s</pre>' % h(e.suggested_bibtex))
            P.append("</details>")
    P.append("<script>%s</script></body></html>" % _JS)
    with open(path, "w", encoding="utf-8") as f:
        f.write("".join(P))
