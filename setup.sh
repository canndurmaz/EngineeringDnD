#!/usr/bin/env bash
#
# Critical Path — one-command bootstrap.
#
# Takes a fresh clone to a running game: a virtualenv at ./venv, the core
# dependencies, the directories the app writes to, and a test smoke check.
# Optionally the local LLM narrator (compiles llama-cpp-python, downloads a
# ~0.8 GB GGUF). Safe to run repeatedly.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_ROOT"

VENV="$REPO_ROOT/venv"
PY="$VENV/bin/python"
MODEL_DIR="$REPO_ROOT/models"
MIN_MODEL_BYTES=$((600 * 1024 * 1024))   # a valid Q4_K_M 1B is ~0.8 GB

# ---------------------------------------------------------------- output ----

if [ -t 1 ]; then
    C_RESET=$'\033[0m'; C_BOLD=$'\033[1m'; C_DIM=$'\033[2m'
    C_RED=$'\033[31m'; C_GREEN=$'\033[32m'; C_YELLOW=$'\033[33m'; C_BLUE=$'\033[34m'
else
    C_RESET=''; C_BOLD=''; C_DIM=''
    C_RED=''; C_GREEN=''; C_YELLOW=''; C_BLUE=''
fi

step() { printf '\n%s==>%s %s%s%s\n' "$C_BLUE" "$C_RESET" "$C_BOLD" "$*" "$C_RESET"; }
info() { printf '    %s\n' "$*"; }
dim()  { printf '    %s%s%s\n' "$C_DIM" "$*" "$C_RESET"; }
ok()   { printf '    %s✓%s %s\n' "$C_GREEN" "$C_RESET" "$*"; }
warn() { printf '%s!%s  %s\n' "$C_YELLOW" "$C_RESET" "$*" >&2; }
die()  { printf '\n%serror:%s %s\n' "$C_RED" "$C_RESET" "$*" >&2; exit 1; }

# ----------------------------------------------------------------- flags ----

WITH_LLM=0
LLM_ONLY=0
SKIP_TESTS=0

usage() {
    cat <<'EOF'
Usage: ./setup.sh [OPTIONS]

Bootstraps Critical Path: creates ./venv, installs requirements.txt, creates the
instance/ and rooms/ directories, and runs the test suite as a smoke check.
Idempotent — re-running reuses a working venv instead of rebuilding it.

Options:
  (no flags)     Core install only. Takes 1-3 minutes.
  --with-llm     Also install requirements-llm.txt and download the model.
                 llama-cpp-python has no prebuilt wheel for common Linux
                 targets, so pip COMPILES it from source: 10-20 minutes on a
                 slow machine. Needs gcc, g++, cmake, make and the Python
                 development headers, all checked before the compile starts.
                 The model is a ~0.8 GB GGUF download into models/.
  --llm-only     Do only the LLM half — for someone who ran the default
                 earlier and changed their mind.
  --skip-tests   Skip the test-suite smoke check.
  -h, --help     Show this help and exit.

The game is fully playable without the LLM: the narrator falls back to
template-generated prose and the UI shows a "DM: template" badge.

After setup, everything runs through the venv's interpreter:

    ./venv/bin/python app.py
EOF
}

while [ $# -gt 0 ]; do
    case "$1" in
        --with-llm)   WITH_LLM=1 ;;
        --llm-only)   LLM_ONLY=1; WITH_LLM=1 ;;
        --skip-tests) SKIP_TESTS=1 ;;
        -h|--help)    usage; exit 0 ;;
        *)            printf 'unknown option: %s\n\n' "$1" >&2; usage >&2; exit 2 ;;
    esac
    shift
done

# ------------------------------------------------------------- preflight ----

preflight_core() {
    step "Preflight: checking the system Python"

    command -v python3 >/dev/null 2>&1 || die \
"python3 was not found on PATH.
    Install it with:  sudo apt-get install -y python3 python3-venv python3-pip"

    local version
    version="$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null)" \
        || die "could not run python3 to determine its version."

    if ! python3 -c 'import sys; raise SystemExit(0 if sys.version_info[:2] >= (3, 10) else 1)'; then
        die \
"Python 3.10 or newer is required; python3 is $version.
    Install a newer interpreter, e.g.:  sudo apt-get install -y python3.12 python3.12-venv"
    fi
    ok "python3 $version (3.10+ required)"

    if ! python3 -c 'import venv' >/dev/null 2>&1; then
        die \
"the venv module is missing from this Python.
    Install it with:  sudo apt-get install -y python3-venv"
    fi
    ok "python3 -m venv is available"

    python3 -c 'import sqlite3' >/dev/null 2>&1 \
        || die "the stdlib sqlite3 module is missing; the game stores every room in SQLite."
    ok "sqlite3 (stdlib) is available"
}

preflight_llm() {
    step "Preflight: checking the LLM build toolchain"
    if [ -x "$PY" ] && "$PY" -c 'import llama_cpp' >/dev/null 2>&1; then
        ok "llama_cpp is already installed — no compile needed, skipping the toolchain check"
        return
    fi
    dim "llama-cpp-python ships no prebuilt wheel for common Linux targets, so"
    dim "pip compiles it from source. Checking the toolchain now, before the"
    dim "compile, rather than failing 18 minutes in."

    local missing=()
    local tool
    for tool in gcc g++ cmake make; do
        if command -v "$tool" >/dev/null 2>&1; then
            ok "$tool"
        else
            printf '    %s✗%s %s\n' "$C_RED" "$C_RESET" "$tool"
            missing+=("$tool")
        fi
    done

    # Python.h — the development headers the extension compiles against.
    local inc
    inc="$(python3 -c 'import sysconfig; print(sysconfig.get_paths()["include"])' 2>/dev/null || true)"
    if [ -n "$inc" ] && [ -f "$inc/Python.h" ]; then
        ok "Python development headers ($inc/Python.h)"
    else
        printf '    %s✗%s Python development headers (Python.h)\n' "$C_RED" "$C_RESET"
        missing+=("python3-dev")
    fi

    if [ ${#missing[@]} -gt 0 ]; then
        die \
"missing build prerequisites: ${missing[*]}
    Install them with:

        sudo apt-get update && sudo apt-get install -y build-essential cmake python3-dev

    Then re-run:  ./setup.sh --llm-only
    Or skip the LLM entirely — the game plays fine with the template narrator."
    fi
}

# ------------------------------------------------------------------ venv ----

venv_is_healthy() {
    [ -x "$PY" ] || return 1
    "$PY" -c 'import sys; raise SystemExit(0 if sys.version_info[:2] >= (3, 10) else 1)' >/dev/null 2>&1 || return 1
    "$PY" -m pip --version >/dev/null 2>&1 || return 1
    return 0
}

ensure_venv() {
    step "Virtualenv at ./venv"
    if venv_is_healthy; then
        ok "existing venv works, reusing it ($("$PY" --version 2>&1))"
        return
    fi
    if [ -e "$VENV" ]; then
        warn "./venv exists but is not usable (no working interpreter or pip); rebuilding it."
        rm -rf "$VENV"
    fi
    info "creating it (a few seconds)..."
    python3 -m venv "$VENV" || die \
"python3 -m venv failed.
    On Debian/Ubuntu this usually means:  sudo apt-get install -y python3-venv"
    venv_is_healthy || die "the new venv has no working pip. Try: sudo apt-get install -y python3-venv"
    ok "created ($("$PY" --version 2>&1))"
}

# -------------------------------------------------------------- installs ----

install_core() {
    step "Core dependencies (Flask, segno, huggingface_hub, pytest, python-avatars)"
    info "pip install -r requirements.txt — 1-2 minutes on a first run, seconds if cached"
    "$PY" -m pip install --quiet --upgrade pip setuptools wheel \
        || die "could not upgrade pip inside the venv. Is the network reachable?"
    "$PY" -m pip install --quiet -r "$REPO_ROOT/requirements.txt" \
        || die "pip install -r requirements.txt failed (see the output above)."
    ok "installed"
}

install_llm() {
    step "LLM dependencies (llama-cpp-python)"
    if "$PY" -c 'import llama_cpp' >/dev/null 2>&1; then
        ok "llama_cpp is already importable, skipping the compile"
    else
        warn "This COMPILES from source and takes 10-20 minutes on a slow machine."
        warn "Reason: llama-cpp-python publishes no prebuilt wheel for common Linux"
        warn "targets, so pip builds the C++ extension locally. Leave it running."
        info "pip install -r requirements-llm.txt ..."
        "$PY" -m pip install -r "$REPO_ROOT/requirements-llm.txt" \
            || die \
"llama-cpp-python failed to build.
    The usual cause is a missing toolchain piece:

        sudo apt-get update && sudo apt-get install -y build-essential cmake python3-dev

    The game still runs without it, using the template narrator."
        ok "compiled and installed"
    fi
}

model_file() {
    find -L "$MODEL_DIR" -maxdepth 1 -type f -name '*.gguf' -size +"$((MIN_MODEL_BYTES / 1024))"k \
        2>/dev/null | head -n 1
}

download_model() {
    step "The narrator model (~0.8 GB GGUF into models/)"
    local existing
    existing="$(model_file)"
    if [ -n "$existing" ]; then
        ok "already present: $existing"
        return
    fi
    info "downloading — a few minutes on a typical connection"
    "$PY" "$REPO_ROOT/scripts/setup_model.py" \
        || die \
"the model download failed.
    Re-run ./setup.sh --llm-only once the network is back, or run
    ./venv/bin/python scripts/setup_model.py by hand. The game plays fine
    without it, using the template narrator."
    ok "downloaded"
}

# ----------------------------------------------------------- directories ----

ensure_dirs() {
    step "Writable directories"
    mkdir -p "$REPO_ROOT/instance" "$REPO_ROOT/rooms" "$MODEL_DIR"
    ok "instance/  — the signed-session secret key (the app creates it at 0600 on first run)"
    ok "rooms/     — one SQLite database per room"
    ok "models/    — the GGUF narrator model, when installed"
}

# ----------------------------------------------------------------- tests ----

run_tests() {
    step "Smoke check: the test suite"
    info "./venv/bin/python -m pytest — roughly a minute"
    if "$PY" -m pytest; then
        ok "all tests pass"
    else
        die \
"the test suite failed. The install itself may still be fine, but something is
    wrong. Re-run the suite on its own to see the detail:

        ./venv/bin/python -m pytest"
    fi
}

# --------------------------------------------------------------- summary ----

primary_ip() {
    local ip=''
    if command -v hostname >/dev/null 2>&1; then
        ip="$(hostname -I 2>/dev/null | awk '{print $1}' || true)"
    fi
    printf '%s' "$ip"
}

summary() {
    local model llm_mod ip
    model="$(model_file)"
    llm_mod=0
    if [ -x "$PY" ] && "$PY" -c 'import llama_cpp' >/dev/null 2>&1; then
        llm_mod=1
    fi
    ip="$(primary_ip)"

    printf '\n%s%s Critical Path is ready. %s\n\n' "$C_BOLD" "$C_GREEN" "$C_RESET"
    printf '  Start the server:\n\n'
    printf '      %s./venv/bin/python app.py%s\n\n' "$C_BOLD" "$C_RESET"
    printf '  Then open:\n\n'
    printf '      http://localhost:5000\n'
    if [ -n "$ip" ]; then
        printf '      http://%s:5000   (share this one with the LAN)\n' "$ip"
    else
        printf '      %s(could not determine this machine LAN IP; try: hostname -I)%s\n' "$C_DIM" "$C_RESET"
    fi
    printf '\n  Narrator: '
    if [ "$llm_mod" -eq 1 ] && [ -n "$model" ]; then
        printf '%sLLM active%s — llama_cpp installed and a model is in models/.\n' "$C_GREEN" "$C_RESET"
    else
        printf '%stemplate fallback%s' "$C_YELLOW" "$C_RESET"
        if [ "$llm_mod" -eq 0 ] && [ -z "$model" ]; then
            printf ' — llama_cpp is not installed and models/ has no GGUF.\n'
        elif [ "$llm_mod" -eq 0 ]; then
            printf ' — a model is present but llama_cpp is not installed.\n'
        else
            printf ' — llama_cpp is installed but models/ has no GGUF.\n'
        fi
        printf '            The game is fully playable this way; the UI shows a "DM: template" badge.\n'
        printf '            To upgrade: %s./setup.sh --llm-only%s (compiles for 10-20 minutes).\n' "$C_BOLD" "$C_RESET"
    fi
    printf '\n  Internet is needed only during setup. Once this has finished, everything —\n'
    printf '  model inference, avatars, SQLite, the LAN server — runs entirely offline.\n'
    printf '\n  Optional admin pane: start the server with %sCP_ADMIN_PASSWORD=...%s set,\n' "$C_BOLD" "$C_RESET"
    printf '  then open /admin to list, create and delete rooms. Unset, /admin does not exist.\n'
    printf '\n  Everything in this project runs through the venv interpreter —\n'
    printf '  use %s./venv/bin/python ...%s, or activate it first with %s. venv/bin/activate%s.\n\n' \
        "$C_BOLD" "$C_RESET" "$C_BOLD" "$C_RESET"
}

# ------------------------------------------------------------------ main ----

printf '%s%sCritical Path — setup%s\n' "$C_BOLD" "$C_BLUE" "$C_RESET"

if [ "$LLM_ONLY" -eq 1 ]; then
    dim "mode: --llm-only (llama-cpp-python + model; core install untouched)"
    preflight_core
    venv_is_healthy || die \
"--llm-only needs an existing venv at ./venv, and there is not a working one.
    Run ./setup.sh first."
    preflight_llm
    ensure_dirs
    install_llm
    download_model
else
    if [ "$WITH_LLM" -eq 1 ]; then
        dim "mode: --with-llm (core install, then llama-cpp-python + model)"
    else
        dim "mode: core install (no LLM; add --with-llm for the local narrator)"
    fi
    preflight_core
    if [ "$WITH_LLM" -eq 1 ]; then preflight_llm; fi
    ensure_venv
    install_core
    ensure_dirs
    if [ "$WITH_LLM" -eq 1 ]; then
        install_llm
        download_model
    fi
    if [ "$SKIP_TESTS" -eq 1 ]; then
        step "Smoke check skipped (--skip-tests)"
    else
        run_tests
    fi
fi

summary
