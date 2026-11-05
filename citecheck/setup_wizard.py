"""Interactive first-run setup: choose a data dir, download DBLP, build the index.

  python3 -m citecheck.setup_wizard [--yes] [--index full|lite|none]
         [--data-dir DIR] [--keep-gz] [--no-download]
"""
import argparse
import hashlib
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

from . import config, ui

DBLP_URL = "https://dblp.org/xml/dblp.xml.gz"
MD5_URL = DBLP_URL + ".md5"
S2_URL = "https://www.semanticscholar.org/product/api"
GZ_GB, FULL_INDEX_GB = 1.1, 5.5
LITE_INDEX_GB = 5.0  # measured on the Nov 2026 dump (8.76M records)
EST_RECORDS = 8_900_000
GB = 1024 ** 3
STEPS = 8


# --- checksum ---------------------------------------------------------------

def md5_file(path, progress=None):
    h, done = hashlib.md5(), 0
    size = max(Path(path).stat().st_size, 1)
    with open(str(path), "rb") as f:
        while True:
            b = f.read(1 << 22)
            if not b:
                break
            h.update(b)
            done += len(b)
            if progress:
                progress(done, size)
    return h.hexdigest()


def verify_md5(path, expected, show=False):
    """True if the file's md5 equals `expected` (case-insensitive)."""
    bar = ui.ProgressBar(Path(path).stat().st_size, "bytes", "verifying") if show else None
    got = md5_file(path, (lambda d, s: bar.update(d)) if bar else None)
    if bar:
        bar.close()
    return got.lower() == expected.strip().lower()


def fetch_md5(url=MD5_URL):
    with urllib.request.urlopen(url, timeout=30) as r:
        txt = r.read().decode("ascii", "replace").split()
    return txt[0] if txt else ""


# --- download ---------------------------------------------------------------

def aria_hint():
    if platform.system() == "Darwin":
        return "brew install aria2"
    for tool, cmd in (("apt-get", "sudo apt install aria2"), ("dnf", "sudo dnf install aria2"),
                      ("pacman", "sudo pacman -S aria2")):
        if shutil.which(tool):
            return cmd
    return "install 'aria2' with your package manager"


def remote_size(url):
    try:
        req = urllib.request.Request(url, method="HEAD")
        with urllib.request.urlopen(req, timeout=30) as r:
            return int(r.headers.get("Content-Length") or 0)
    except Exception:
        return 0


def disk_bytes(path, sparse=False):
    """Bytes downloaded so far. aria2 pre-extends nothing but writes at offsets, so
    for it count allocated blocks rather than the apparent size."""
    try:
        st = os.stat(str(path))
    except OSError:
        return 0
    if sparse and hasattr(st, "st_blocks"):
        return min(st.st_size, st.st_blocks * 512)
    return st.st_size


def download(url, dest, label="downloading"):
    """Resumable download to `dest` with a live progress bar. True on success."""
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    total = remote_size(url)
    ctl = Path(str(dest) + ".aria2")
    if total and dest.exists() and dest.stat().st_size == total and not ctl.exists():
        return True
    aria = shutil.which("aria2c")
    if aria:
        cmd = [aria, "-x8", "-s8", "-c", "--file-allocation=none", "--summary-interval=0",
               "--console-log-level=warn", "--download-result=hide",
               "-d", str(dest.parent), "-o", dest.name, url]
    elif shutil.which("curl"):
        ui.info("aria2c is faster (parallel + resumable). Install: " + ui.c("bold", aria_hint()))
        cmd = ["curl", "-fsSL", "-C", "-", "-o", str(dest), url]
    else:
        ui.fail("neither aria2c nor curl found; download %s to %s yourself" % (url, dest))
        return False
    start = disk_bytes(dest, bool(aria))
    if start:
        ui.info("resuming from %s" % ui.fmt_bytes(start))
    bar = ui.ProgressBar(total, "bytes", label, start=start)
    err = tempfile.TemporaryFile()
    try:
        p = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=err)
        while p.poll() is None:
            bar.update(disk_bytes(dest, bool(aria)))
            time.sleep(0.2)
    except KeyboardInterrupt:
        p.terminate()
        p.wait()
        bar.close()
        raise
    bar.update(disk_bytes(dest, False) if p.returncode == 0 else disk_bytes(dest, bool(aria)), force=True)
    bar.close()
    err.seek(0)
    msg = err.read().decode("utf-8", "replace").strip()[-300:]
    err.close()
    if p.returncode != 0:
        ui.fail("download failed: " + msg)
        return False
    return True


# --- index building ---------------------------------------------------------

def build_index(gz, db, lite, full):
    from . import dblp_index
    bar = ui.ProgressBar(EST_RECORDS, "count", "indexing")
    t0 = time.time()

    state = {"n": 0, "spin": None, "done": False}

    def cb(n, elapsed):
        state["n"] = n
        bar.update(min(n, EST_RECORDS))

    def finish_bar():
        if not state["done"]:
            state["done"] = True
            bar.total = max(state["n"], 1)
            bar.update(bar.total, force=True)
            bar.close()

    def log(msg):
        msg = msg.strip()
        if "records parsed" in msg:  # parsing done; the search index build takes a few minutes
            state["n"] = int(msg.split()[0].replace(",", ""))
            finish_bar()
            state["spin"] = ui.Spinner("building the search index (a few minutes, no progress to show)")
            state["spin"].__enter__()
        elif msg.startswith("new "):
            finish_bar()
            ui.info(msg)
    bar.t0 = t0
    try:
        dblp_index.build(gz, db, full=full, lite=lite, progress=cb, log=log)
    finally:
        if state["spin"]:
            state["spin"].__exit__(None, None, None)
    finish_bar()


def _md5_cache(gz):
    return Path(str(gz) + ".md5")


def free_gb(path):
    p = Path(path)
    while not p.exists() and p != p.parent:
        p = p.parent
    return shutil.disk_usage(str(p)).free / GB


def needs_gb(mode):
    return {"full": GZ_GB + FULL_INDEX_GB, "lite": GZ_GB + LITE_INDEX_GB}.get(mode, 0.0)


def default_mode(free):
    for m in ("full", "lite"):
        if free >= needs_gb(m):
            return m
    return "none"


# --- the walkthrough --------------------------------------------------------

def run(argv=None):
    ap = argparse.ArgumentParser(prog="citecheck setup", description="Guided first-run setup.")
    ap.add_argument("--yes", "-y", action="store_true", help="accept defaults, no questions")
    ap.add_argument("--index", choices=["full", "lite", "none"])
    ap.add_argument("--data-dir")
    ap.add_argument("--keep-gz", action="store_true", help="keep dblp.xml.gz after building")
    ap.add_argument("--no-download", action="store_true", help="use an existing dblp.xml.gz")
    a = ap.parse_args(argv)
    try:
        return _run(a)
    except KeyboardInterrupt:
        ui.say("\n")
        ui.warn("Interrupted. Re-run `citecheck setup`: a partial download resumes where it stopped.")
        return 130


def _run(a):
    y = a.yes
    cfg = config.load()
    ui.banner()
    ui.say("  Sets up an offline copy of the DBLP computer-science bibliography so your")
    ui.say("  .bib files can be checked fast, with no rate limits. Ctrl-C is safe at any point.")

    ui.step(1, STEPS, "Data directory")
    dd = Path(a.data_dir or (str(config.data_dir(cfg)) if y else
                             ui.text("Where should the data live?", str(config.data_dir(cfg))))).expanduser()
    dd.mkdir(parents=True, exist_ok=True)
    cfg["data_dir"] = str(dd)
    if a.data_dir:
        cfg.pop("db", None)
        cfg.pop("gz", None)
    ui.ok(str(dd))

    ui.step(2, STEPS, "Disk space")
    free = free_gb(dd)
    ui.info("free here: %.1f GB" % free)
    ui.info("full: ~%.1f GB download + ~%.1f GB index (~%.1f GB peak)" % (GZ_GB, FULL_INDEX_GB, needs_gb("full")))
    ui.info("lite: ~%.1f GB download + ~%.1f GB index (~%.1f GB peak)" % (GZ_GB, LITE_INDEX_GB, needs_gb("lite")))

    db = config.db_path(cfg)
    existing = db.exists()
    ui.step(3, STEPS, "Index type")
    mode, rebuild, keep = a.index, False, False
    if existing:
        old = cfg.get("mode") if cfg.get("mode") in ("full", "lite") else "full"
        ui.info("an index already exists at %s (mode: %s)" % (db, old))
        if mode is None:
            act = "keep" if y else ui.select("What now?", [
                ("keep", "use it as is"), ("update", "incremental update from a fresh dump"),
                ("rebuild", "start from scratch")], "keep")
            keep, rebuild, mode = act == "keep", act == "rebuild", old
        elif mode == "none" or mode == old:
            keep, mode = True, old
        else:
            rebuild = True
    if mode is None:
        mode = default_mode(free) if y else ui.select("Which index?", [
            ("full", "every DBLP record type, best coverage"),
            ("lite", "papers, theses and datasets only; ~10% smaller"),
            ("none", "no local index: online lookups only (slower, rate-limited)")],
            default_mode(free))
    build = mode in ("full", "lite") and not keep
    if build and free < needs_gb(mode):
        ui.warn("only %.1f GB free but %s needs ~%.1f GB" % (free, mode, needs_gb(mode)))
        if y or not ui.yesno("Continue anyway?", False):
            mode, build = "none", False
    ui.ok("keeping the existing index" if keep else "index: " + mode)
    if not existing or build:
        cfg["mode"] = mode

    gz = Path(cfg.get("gz") or dd / "dblp.xml.gz")
    ui.step(4, STEPS, "Download DBLP")
    if not build:
        ui.info("skipped")
    elif a.no_download:
        if not gz.exists():
            ui.fail("--no-download but %s does not exist" % gz)
            return 1
        ui.ok("using %s" % gz)
    else:
        want = None
        try:
            want = fetch_md5()
        except Exception as e:  # offline or DBLP unreachable
            ui.warn("could not fetch the checksum (%s)" % e)
        if not download(DBLP_URL, gz):
            ui.warn("Re-run `citecheck setup` to resume.")
            return 1
        ui.ok("downloaded " + ui.fmt_bytes(gz.stat().st_size))
        ui.step(5, STEPS, "Verify checksum")
        if want:
            if not verify_md5(gz, want, True):
                ui.fail("md5 mismatch: the file is damaged. Delete %s and re-run." % gz)
                return 1
            ui.ok("md5 " + want)
            _md5_cache(gz).write_text(want + "\n")
        else:
            ui.warn("skipped (no checksum available)")

    if not build or a.no_download:
        ui.step(5, STEPS, "Verify checksum")
        ui.info("skipped")
    ui.step(6, STEPS, "Build the index")
    if build:
        ui.info("this takes a while; you can leave it running")
        try:
            build_index(gz, db, mode == "lite", full=rebuild or not existing)
        except SystemExit as e:
            ui.fail(str(e))
            return 1
        cfg["gz"], cfg["db"] = str(gz), str(db)
        ui.ok("built " + str(db))
        if gz.exists():
            ui.info("%s (%.1f GB) is only needed for future updates; deleting it means"
                    % (gz.name, gz.stat().st_size / GB))
            ui.info("`citecheck update` will download it again.")
            if not a.keep_gz and not y and ui.yesno("Delete %s now?" % gz.name, False):
                gz.unlink()
                ui.ok("deleted")
    else:
        ui.info("skipped")

    ui.step(7, STEPS, "Semantic Scholar API key (optional)")
    ui.info("only used for online lookups; free key at " + S2_URL)
    if cfg.get("s2_api_key"):
        ui.ok("a key is already saved")
    else:
        k = "" if y else ui.text("API key (Enter to skip)")
        if k:
            cfg["s2_api_key"] = k
    config.save(cfg)
    ui.ok("saved " + str(config.config_path()))

    ui.step(8, STEPS, "Test")
    records = size = ""
    if db.exists():
        from . import dblp_index
        conn = dblp_index.connect(db)
        hits = dblp_index.lookup(conn, "Attention is all you need", limit=1) if conn else []
        if hits:
            h = hits[0]
            ui.ok("found: %s (%s, %s)" % (h["title"], h["venue"], h["year"]))
        else:
            ui.warn("test search found nothing; the index may be incomplete")
        try:
            records = format(conn.execute("SELECT count(*) FROM pubs").fetchone()[0], ",")
        except Exception:
            records = "?"
        size = "%.1f GB" % (db.stat().st_size / GB)
    else:
        ui.info("no local index; online lookups will be used")
    lines = ["index   " + (str(db) if db.exists() else "none (online only)"),
             "mode    " + cfg.get("mode", "none")]
    if records:
        lines += ["size    %s, %s records" % (size, records)]
    lines += ["", "Next:", "  citecheck check paper/      check a LaTeX project",
              "  citecheck check refs.bib    check one .bib",
              "  citecheck update            refresh the index monthly"]
    ui.say()
    ui.box(lines, "All set")
    return 0


def update_index(cfg=None):
    """Refresh the index from a fresh dump. Skips if the remote md5 is unchanged. Returns 0/1."""
    cfg = config.load() if cfg is None else cfg
    db = config.db_path(cfg)
    if cfg.get("mode") == "none" or not db.exists():
        ui.warn("No local index. Run `citecheck setup` first.")
        return 1
    gz = Path(cfg.get("gz") or config.data_dir(cfg) / "dblp.xml.gz")
    try:
        want = fetch_md5()
    except Exception as e:
        ui.fail("could not reach DBLP (%s)" % e)
        return 1
    seen = _md5_cache(gz)
    if want and seen.exists() and seen.read_text().strip() == want:
        ui.ok("index is up to date (DBLP dump unchanged)")
        return 0
    try:
        if not download(DBLP_URL, gz):
            ui.warn("re-run `citecheck update` to resume")
            return 1
        if want and not verify_md5(gz, want, True):
            ui.fail("md5 mismatch; delete %s and retry" % gz)
            return 1
        if want:
            seen.write_text(want + "\n")
        build_index(gz, db, cfg.get("mode") == "lite", full=False)
    except KeyboardInterrupt:
        ui.warn("interrupted; re-run `citecheck update` to resume")
        return 130
    cfg["gz"] = str(gz)
    config.save(cfg)
    ui.ok("index updated")
    return 0


if __name__ == "__main__":
    sys.exit(run(sys.argv[1:]))
