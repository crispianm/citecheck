"""User config: plain `key = value` lines in $XDG_CONFIG_HOME/citecheck/config."""
import os
from pathlib import Path

KEYS = ("data_dir", "db", "gz", "mode", "s2_api_key")


def _xdg(var, fallback):
    return Path(os.environ.get(var) or Path.home() / fallback)


def config_path():
    return _xdg("XDG_CONFIG_HOME", ".config") / "citecheck" / "config"


def default_data_dir():
    return _xdg("XDG_DATA_HOME", ".local/share") / "citecheck"


# Module-level constants as in the interface (evaluated at import time).
CONFIG_PATH = config_path()
DEFAULT_DATA_DIR = default_data_dir()


def load():
    d = {}
    try:
        text = config_path().read_text(encoding="utf-8")
    except OSError:
        return d
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip()
        if k and v:
            d[k] = v
    return d


def save(d):
    p = config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# citecheck config (written by `citecheck setup`)"]
    lines += ["%s = %s" % (k, d[k]) for k in sorted(d) if d[k] not in (None, "")]
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    if d.get("s2_api_key"):
        try:
            p.chmod(0o600)
        except OSError:
            pass


def data_dir(cfg=None):
    cfg = load() if cfg is None else cfg
    return Path(cfg.get("data_dir") or default_data_dir()).expanduser()


def db_path(cfg=None):
    if os.environ.get("CITECHECK_DB"):
        return Path(os.environ["CITECHECK_DB"]).expanduser()
    cfg = load() if cfg is None else cfg
    if cfg.get("db"):
        return Path(cfg["db"]).expanduser()
    return data_dir(cfg) / "dblp.sqlite"


def cache_dir(cfg=None):
    p = data_dir(cfg) / "cache"
    p.mkdir(parents=True, exist_ok=True)
    return p
