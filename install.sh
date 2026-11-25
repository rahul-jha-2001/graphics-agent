#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"
profile=all
model=""
version=0.21.1
while (($#)); do
    case "$1" in
        --profile) profile="$2"; shift 2 ;;
        --model) model="$2"; shift 2 ;;
        --effectcraft-version) version="$2"; shift 2 ;;
        --help|-h) echo 'bash install.sh [--profile engine|inference|training|all] [--model ID] [--effectcraft-version 0.21.1]'; exit 0 ;;
        *) echo "Unknown option $1" >&2; exit 2 ;;
    esac
done
case "$profile" in engine|inference|training|all) ;; *) echo 'Unknown profile' >&2; exit 2 ;; esac
[[ "$version" == 0.21.1 ]] || { echo "No locked release for $version" >&2; exit 1; }
[[ "$(uname -sm)" == "Linux x86_64" ]] || { echo 'This dependency lock targets Linux x86_64' >&2; exit 1; }
export GRAPHICS_HOME="${GRAPHICS_HOME:-/workspace/graphics-agent}"
export HF_HOME="${HF_HOME:-/workspace/.cache/huggingface}"
export GRAPHICS_RUNS="${GRAPHICS_RUNS:-/workspace/graphics-agent-runs}"
python_bin="${PYTHON_BIN:-python3.11}"
"$python_bin" -c 'import sys; assert sys.version_info[:2] == (3,11), "Use CPython 3.11 with venv support"'
mkdir -p "$GRAPHICS_HOME"/{bin,cache,envs} "$GRAPHICS_RUNS" "$HF_HOME"
exec 9>"$GRAPHICS_HOME/install.lock"
flock 9
if [[ "$profile" != engine ]]; then
    nvidia-smi -L
fi
df -h "$GRAPHICS_HOME" "$HF_HOME"
archive="effectcraft-$version-linux-x86_64"
expected=ec39afa51fb0a2c0b58a8c1102325e2355016fe754563d24075fa29b3e1c5315
engine="$GRAPHICS_HOME/bin/effectcraft-cli"
stamp="$GRAPHICS_HOME/bin/effectcraft-cli.sha256"
if [[ -f "$stamp" && -x "$engine" ]]; then
    (cd "$GRAPHICS_HOME/bin" && sha256sum -c effectcraft-cli.sha256)
else
    tarball="${EFFECTCRAFT_ARCHIVE:-$GRAPHICS_HOME/cache/$archive.tar.gz}"
    if [[ ! -f "$tarball" ]]; then
        curl --retry 3 -fL "https://github.com/storytold/effectcraft/releases/download/v$version/$archive.tar.gz" -o "$tarball.partial"
        mv "$tarball.partial" "$tarball"
    fi
    printf '%s  %s\n' "$expected" "$tarball" | sha256sum -c -
    tar --no-same-owner -xOzf "$tarball" "$archive/bin/effectcraft-cli" > "$engine.new"
    chmod 755 "$engine.new"
    mv "$engine.new" "$engine"
    (cd "$GRAPHICS_HOME/bin" && sha256sum effectcraft-cli > effectcraft-cli.sha256)
fi
export EFFECTCRAFT_CLI="$engine"
envdir="$GRAPHICS_HOME/envs/$profile"
if [[ -d "$envdir" ]] && ! "$envdir/bin/python" -c 'import encodings' >/dev/null 2>&1; then
    mv "$envdir" "$envdir.incomplete.$(date +%s)"
fi
if [[ ! -x "$envdir/bin/python" ]]; then
    if command -v uv >/dev/null; then
        uv venv --python "$python_bin" "$envdir"
    else
        "$python_bin" -m venv "$envdir"
    fi
fi
case "$profile" in
    engine) package="." ;;
    all) package=".[inference,training]" ;;
    *) package=".[$profile]" ;;
esac
if command -v uv >/dev/null; then
    uv pip install --python "$envdir/bin/python" -c constraints/linux-x86_64-py311.txt -e "$package"
else
    "$envdir/bin/python" -m pip install --disable-pip-version-check -c constraints/linux-x86_64-py311.txt -e "$package"
fi
export MSWEA_SILENT_STARTUP=1 MSWEA_GLOBAL_CONFIG_DIR="$GRAPHICS_HOME/mini-swe"
"$envdir/bin/python" scripts/install_manifest.py "$profile" "$model"
activate="$GRAPHICS_HOME/activate-$profile"
{
    printf 'source %q\n' "$envdir/bin/activate"
    for var in GRAPHICS_HOME HF_HOME GRAPHICS_RUNS EFFECTCRAFT_CLI MSWEA_SILENT_STARTUP MSWEA_GLOBAL_CONFIG_DIR; do
        printf 'export %s=%q\n' "$var" "${!var}"
    done
    printf 'export GRAPHICS_MODEL_CONFIG=%q\n' "$GRAPHICS_HOME/model-$profile.yaml"
} > "$activate"
"$envdir/bin/python" -m graphics_agent verify
printf 'Ready. Run: source %q\n' "$activate"
