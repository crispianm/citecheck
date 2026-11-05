#!/usr/bin/env bash
# citecheck installer.
#   curl -fsSL https://raw.githubusercontent.com/crispianm/citecheck/main/install.sh | bash
#   ... | bash -s -- --yes --index lite
# Options: --yes  --no-modify-path  --uninstall  (everything else goes to `citecheck setup`)
# Env: CITECHECK_HOME (default ~/.citecheck), CITECHECK_REPO, CITECHECK_BRANCH
set -euo pipefail

REPO="${CITECHECK_REPO:-https://github.com/crispianm/citecheck}"
BRANCH="${CITECHECK_BRANCH:-main}"
HOME_DIR="${CITECHECK_HOME:-$HOME/.citecheck}"
BIN_DIR="$HOME/.local/bin"
SHIM="$BIN_DIR/citecheck"

YES=0; MODIFY_PATH=1; UNINSTALL=0; PASS=()
for a in "$@"; do
  case "$a" in
    --yes|-y) YES=1; PASS+=("$a") ;;
    --no-modify-path) MODIFY_PATH=0 ;;
    --uninstall) UNINSTALL=1 ;;
    *) PASS+=("$a") ;;
  esac
done

if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
  B=$'\033[1m'; D=$'\033[2m'; R=$'\033[31m'; G=$'\033[32m'; Y=$'\033[33m'; C=$'\033[36m'; O=$'\033[38;5;208m'; N=$'\033[0m'
else
  B=""; D=""; R=""; G=""; Y=""; C=""; O=""; N=""
fi
ok()   { printf '  %s✓%s %s\n' "$G" "$N" "$*"; }
fail() { printf '  %s✗%s %s\n' "$R" "$N" "$*" >&2; }
info() { printf '  %s→%s %s\n' "$C" "$N" "$*"; }
warn() { printf '  %s!%s %s\n' "$Y" "$N" "$*"; }
step() { printf '\n%s%s%s %s%s%s\n' "$C$B" "$1" "$N" "$B" "$2" "$N"; }
die()  { fail "$*"; exit 1; }

# Ask a yes/no question on the terminal (works under curl | bash). Default $2 (y/n).
ask() {
  local reply=""
  if [ "$YES" = 1 ] || ! { : </dev/tty; } 2>/dev/null; then [ "${2:-y}" = y ]; return; fi
  printf '  %s?%s %s %s[%s]%s ' "$C" "$N" "$1" "$D" "$([ "${2:-y}" = y ] && echo Y/n || echo y/N)" "$N" >/dev/tty
  read -r reply </dev/tty || true
  case "${reply:-${2:-y}}" in y|Y|yes|YES) return 0 ;; *) return 1 ;; esac
}

# Run a command with a spinner (plain line when not a tty). Usage: spin "message" cmd args...
spin() {
  local msg="$1"; shift
  local log; log="$(mktemp)"
  if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
    "$@" >"$log" 2>&1 &
    local pid=$! i=0 f='⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏'
    while kill -0 "$pid" 2>/dev/null; do
      printf '\r  %s%s%s %s' "$C" "${f:i++%10:1}" "$N" "$msg"; sleep 0.1
    done
    printf '\r\033[K'
    if wait "$pid"; then ok "$msg"; rm -f "$log"; else fail "$msg"; cat "$log" >&2; rm -f "$log"; return 1; fi
  else
    if "$@" >"$log" 2>&1; then ok "$msg"; rm -f "$log"; else fail "$msg"; cat "$log" >&2; rm -f "$log"; return 1; fi
  fi
}

printf '\n  %s◆%s %scitecheck%s  %scheck your .bib against DBLP, offline%s\n' "$O$B" "$N" "$B" "$N" "$D" "$N"

# ---------------------------------------------------------------- uninstall
if [ "$UNINSTALL" = 1 ]; then
  step "1/1" "Uninstall"
  rm -f "$SHIM" && ok "removed $SHIM"
  if [ -d "$HOME_DIR" ]; then rm -rf "$HOME_DIR" && ok "removed $HOME_DIR"; fi
  DATA="${XDG_DATA_HOME:-$HOME/.local/share}/citecheck"
  CONF="${XDG_CONFIG_HOME:-$HOME/.config}/citecheck"
  if [ -d "$DATA" ]; then
    SZ="$(du -sh "$DATA" 2>/dev/null | cut -f1)"
    if ask "Also delete the DBLP index and data in $DATA ($SZ)?" n; then rm -rf "$DATA" && ok "removed $DATA"; else info "kept $DATA"; fi
  fi
  [ -d "$CONF" ] && { rm -rf "$CONF"; ok "removed $CONF"; }
  info "any PATH line added to your shell rc is harmless; remove it by hand if you like"
  exit 0
fi

# ------------------------------------------------------------------ python
step "1/4" "Checking Python"
PY=""
for cand in python3 python; do
  command -v "$cand" >/dev/null 2>&1 || continue
  if "$cand" -c 'import sys; sys.exit(sys.version_info < (3, 9))' 2>/dev/null; then PY="$cand"; break; fi
done
hints() {
  fail "$1"
  case "$(uname -s)" in
    Darwin) info "macOS:   brew install python" ;;
    MINGW*|MSYS*|CYGWIN*) info "Windows: use WSL (https://learn.microsoft.com/windows/wsl/install), then run this inside it" ;;
    *) info "Debian/Ubuntu: sudo apt install python3"
       info "Fedora:        sudo dnf install python3"
       info "Arch:          sudo pacman -S python" ;;
  esac
  exit 1
}
[ -n "$PY" ] || hints "Python 3.9 or newer was not found"
"$PY" -c "import sqlite3; sqlite3.connect(':memory:').execute('create virtual table t using fts5(x)')" 2>/dev/null \
  || hints "this Python's sqlite3 lacks FTS5 (needed for the index); install a newer or distro Python"
PYBIN="$(command -v "$PY")"
ok "$("$PY" -V 2>&1) with sqlite FTS5 ($PYBIN)"

# ----------------------------------------------------------------- fetch
step "2/4" "Getting citecheck"
if command -v git >/dev/null 2>&1; then
  if [ -d "$HOME_DIR/.git" ]; then
    spin "updating $HOME_DIR" git -C "$HOME_DIR" pull --ff-only -q
  else
    [ -e "$HOME_DIR" ] && [ -n "$(ls -A "$HOME_DIR" 2>/dev/null)" ] && die "$HOME_DIR exists and is not a citecheck clone"
    spin "cloning $REPO" git clone -q --depth 1 --branch "$BRANCH" "$REPO" "$HOME_DIR"
  fi
else
  command -v curl >/dev/null 2>&1 || die "need git or curl"
  command -v tar >/dev/null 2>&1 || die "need git or tar"
  mkdir -p "$HOME_DIR"
  spin "downloading tarball" sh -c "curl -fsSL '$REPO/archive/refs/heads/$BRANCH.tar.gz' | tar -xz --strip-components=1 -C '$HOME_DIR'"
fi

# ------------------------------------------------------------------- shim
step "3/4" "Installing the citecheck command"
mkdir -p "$BIN_DIR"
cat >"$SHIM" <<SHIMEOF
#!/bin/sh
PYTHONPATH="$HOME_DIR\${PYTHONPATH:+:\$PYTHONPATH}" exec "$PYBIN" -m citecheck "\$@"
SHIMEOF
chmod +x "$SHIM"
ok "wrote $SHIM"

case ":$PATH:" in
  *":$BIN_DIR:"*) ok "$BIN_DIR is on your PATH" ;;
  *)
    # single quotes on purpose: the rc line must expand at shell start, not now
    # shellcheck disable=SC2016
    case "$(basename "${SHELL:-bash}")" in
      zsh)  RC="$HOME/.zshrc";  LINE='export PATH="$HOME/.local/bin:$PATH"' ;;
      fish) RC="$HOME/.config/fish/config.fish"; LINE='fish_add_path $HOME/.local/bin' ;;
      *)    RC="$HOME/.bashrc"; LINE='export PATH="$HOME/.local/bin:$PATH"' ;;
    esac
    if [ "$MODIFY_PATH" = 0 ]; then
      warn "$BIN_DIR is not on your PATH. Add this to $RC:"; printf '      %s\n' "$LINE"
    elif [ -f "$RC" ] && grep -qF "$LINE" "$RC" 2>/dev/null; then
      ok "$RC already adds $BIN_DIR (open a new shell)"
    elif ask "Add $BIN_DIR to your PATH in $RC?" y; then
      mkdir -p "$(dirname "$RC")"; printf '\n# added by citecheck\n%s\n' "$LINE" >>"$RC"
      ok "appended to $RC (open a new shell or: $LINE)"
    else
      warn "not modified. Add this to $RC yourself:"; printf '      %s\n' "$LINE"
    fi
    export PATH="$BIN_DIR:$PATH" ;;
esac

# ------------------------------------------------------------------ setup
step "4/4" "Running setup"
if [ "${#PASS[@]}" -eq 0 ] || [ "$YES" = 0 ]; then
  if ! { : </dev/tty; } 2>/dev/null; then PASS+=("--yes"); info "no terminal: using defaults"; fi
fi
if { : </dev/tty; } 2>/dev/null; then
  sh "$SHIM" setup ${PASS[@]+"${PASS[@]}"} </dev/tty
else
  sh "$SHIM" setup ${PASS[@]+"${PASS[@]}"} </dev/null
fi
