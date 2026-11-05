"""Tiny terminal UI helpers (stdlib only): colors, spinner, progress bar, prompts."""
import itertools
import os
import shutil
import sys
import threading
import time

_BLOCKS = " ▏▎▍▌▋▊▉█"
CODES = {"bold": "1", "dim": "2", "red": "31", "green": "32", "yellow": "33",
         "blue": "34", "magenta": "35", "cyan": "36", "orange": "38;5;208"}


def use_color(stream=None):
    stream = stream or sys.stdout
    return not os.environ.get("NO_COLOR") and getattr(stream, "isatty", lambda: False)()


def c(name, text, stream=None):
    if not use_color(stream):
        return text
    return "\033[%sm%s\033[0m" % ("".join(CODES.get(n, "") + ";" for n in name.split("+")).rstrip(";"), text)


def _utf8():
    return "utf" in (getattr(sys.stdout, "encoding", "") or "").lower()


def sym(kind):
    u = {"ok": "✓", "fail": "✗", "arrow": "→", "warn": "!"}
    a = {"ok": "+", "fail": "x", "arrow": "->", "warn": "!"}
    return (u if _utf8() else a)[kind]


def say(s=""):
    print(s, flush=True)


def banner(title="citecheck", subtitle="check your .bib against DBLP, offline"):
    say()
    say("  " + c("orange+bold", "◆ " if _utf8() else "* ") + c("bold", title) + c("dim", "  " + subtitle))
    say()


def step(n, total, title):
    say("\n" + c("cyan+bold", "%d/%d" % (n, total)) + " " + c("bold", title))


def ok(s):
    say("  " + c("green", sym("ok")) + " " + s)


def fail(s):
    say("  " + c("red", sym("fail")) + " " + s)


def info(s):
    say("  " + c("blue", sym("arrow")) + " " + s)


def warn(s):
    say("  " + c("yellow", sym("warn")) + " " + s)


def box(lines, title=""):
    """Rounded box around plain-text lines (ANSI inside lines is not measured, keep them plain)."""
    w = max([len(x) for x in lines] + [len(title) + 2])
    tl, tr, bl, br, h, v = ("╭", "╮", "╰", "╯", "─", "│") if _utf8() else ("+", "+", "+", "+", "-", "|")
    say("  " + c("green", tl + (h + " %s " % title).ljust(w + 2, h) + tr))
    for x in lines:
        say("  " + c("green", v) + " " + x.ljust(w) + " " + c("green", v))
    say("  " + c("green", bl + h * (w + 2) + br))


# --- spinner ----------------------------------------------------------------

class Spinner(object):
    """with Spinner("cloning"): ...   Animates on a tty, prints one line otherwise."""
    FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

    def __init__(self, msg, stream=None):
        self.msg, self.stream = msg, stream or sys.stdout
        self._stop = threading.Event()
        self._t = None
        self.live = use_color(self.stream) and _utf8()

    def _spin(self):
        for f in itertools.cycle(self.FRAMES):
            if self._stop.wait(0.1):
                break
            self.stream.write("\r  %s %s" % (c("cyan", f), self.msg))
            self.stream.flush()

    def __enter__(self):
        if self.live:
            self._t = threading.Thread(target=self._spin, daemon=True)
            self._t.start()
        else:
            self.stream.write("  ... %s\n" % self.msg)
            self.stream.flush()
        return self

    def __exit__(self, et, ev, tb):
        self._stop.set()
        if self._t:
            self._t.join()
            self.stream.write("\r" + " " * (len(self.msg) + 8) + "\r")
        mark = c("green", sym("ok")) if et is None else c("red", sym("fail"))
        self.stream.write("  %s %s\n" % (mark, self.msg))
        self.stream.flush()
        return False


# --- progress bar -----------------------------------------------------------

def fmt_bytes(n):
    for u in ("B", "KB", "MB", "GB"):
        if n < 1024 or u == "GB":
            return ("%d %s" % (n, u)) if u == "B" else "%.1f %s" % (n, u)
        n /= 1024.0


def fmt_time(s):
    s = int(max(s, 0))
    return "%d:%02d:%02d" % (s // 3600, s % 3600 // 60, s % 60) if s >= 3600 else "%d:%02d" % (s // 60, s % 60)


def bar_str(frac, width):
    frac = min(max(frac, 0.0), 1.0)
    cells = frac * width
    full = int(cells)
    s = "█" * full
    if full < width:
        s += _BLOCKS[int((cells - full) * 8)]
    return s.ljust(width)


class ProgressBar(object):
    """Single-line bar. total=0/None => unknown total (no percent/ETA).
    unit "bytes" or "count". Redraws at most 10 Hz; plain line per 10% when not a tty."""

    def __init__(self, total, unit="bytes", label="", stream=None, start=0):
        self.total, self.unit, self.label = total or 0, unit, label
        self.stream = stream or sys.stdout
        self.t0, self.start, self.n = time.time(), start, start
        self._last, self._decile = 0.0, -1
        self.tty = getattr(self.stream, "isatty", lambda: False)()

    def _val(self, n):
        return fmt_bytes(n) if self.unit == "bytes" else format(int(n), ",")

    def render(self, n=None, width=None):
        n = self.n if n is None else n
        el = max(time.time() - self.t0, 1e-9)
        rate = (n - self.start) / el
        cols = width or shutil.get_terminal_size((80, 20)).columns
        rate_s = (fmt_bytes(rate) + "/s") if self.unit == "bytes" else format(int(rate), ",") + "/s"
        if self.total > 0:
            frac = n / float(self.total)
            eta = (self.total - n) / rate if rate > 0 else 0
            tail = " %3d%%  %s / %s  %s  ETA %s" % (min(frac * 100, 100), self._val(n),
                                                   self._val(self.total), rate_s, fmt_time(eta))
        else:
            frac = 0.0
            tail = "  %s  %s  %s" % (self._val(n), rate_s, fmt_time(el))
        head = "  " + (self.label + " " if self.label else "")
        bw = max(10, min(40, cols - len(head) - len(tail) - 3))
        bar = bar_str(frac, bw) if self.total > 0 else "·" * bw
        line = "%s%s%s" % (head, c("cyan", "▕" if _utf8() else "|", self.stream) + c("cyan", bar, self.stream)
                           + c("cyan", "▏" if _utf8() else "|", self.stream), tail)
        return line

    def update(self, n, force=False):
        self.n = n
        now = time.time()
        if self.tty:
            if force or now - self._last >= 0.1:
                self._last = now
                self.stream.write("\r" + self.render(n) + "\033[K")
                self.stream.flush()
        elif self.total > 0:
            d = int(10 * n / self.total)
            if d > self._decile:
                self._decile = d
                self.stream.write(self.render(n) + "\n")
                self.stream.flush()

    def close(self):
        if self.tty:
            self.update(self.n, force=True)
            self.stream.write("\n")
            self.stream.flush()


# --- prompts ----------------------------------------------------------------

def _input(prompt):
    try:
        return input(prompt).strip()
    except EOFError:
        return ""


def text(prompt, default=""):
    d = c("dim", " [%s]" % default) if default else ""
    return _input("  %s %s%s " % (c("cyan", "?"), prompt, d)) or default


def yesno(prompt, default=True):
    r = _input("  %s %s %s " % (c("cyan", "?"), prompt, c("dim", "[Y/n]" if default else "[y/N]"))).lower()
    return default if not r else r.startswith("y")


def select(prompt, options, default):
    """options: list of (value, description). Returns the chosen value; Enter = default."""
    say("  %s %s" % (c("cyan", "?"), prompt))
    for i, (v, desc) in enumerate(options, 1):
        mark = c("green+bold", "●") if v == default else " "
        say("    %s %d) %s  %s" % (mark if _utf8() else ("*" if v == default else " "), i,
                                    c("bold", v) if v == default else v, c("dim", desc)))
    vals = [v for v, _ in options]
    while True:
        r = _input("    choose [%s]: " % default)
        if not r:
            return default
        if r in vals:
            return r
        if r.isdigit() and 1 <= int(r) <= len(vals):
            return vals[int(r) - 1]
        warn("please pick one of: " + ", ".join(vals))
