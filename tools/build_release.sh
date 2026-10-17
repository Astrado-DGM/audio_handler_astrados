#!/usr/bin/env bash
# Genera la release de Astrados Audio Handler: ejecutable, setup.exe, MSI y ZIP portable.
#
# Uso (desde Git Bash, en Windows):
#   tools/build_release.sh                 # todo
#   tools/build_release.sh --skip-setup    # sin el setup.exe (Inno Setup)
#   tools/build_release.sh --skip-msi      # sin el MSI (WiX)
#
# Requisitos:
#   - Python 3 en el PATH (se crea un venv aislado en build/venv)
#   - Inno Setup 6.3+ para el setup.exe:  winget install JRSoftware.InnoSetup
#   - WiX Toolset 5 para el MSI:          dotnet tool install --global wix --version 5.0.2
#
# Salida: dist/release/
#   AstradosAudioHandler-<version>-setup.exe
#   AstradosAudioHandler-<version>-x64.msi
#   AstradosAudioHandler-<version>-portable.zip
#   SHA256SUMS.txt

set -euo pipefail

BUILD_SETUP=1
BUILD_MSI=1
for arg in "$@"; do
    case "$arg" in
        --skip-setup) BUILD_SETUP=0 ;;
        --skip-msi) BUILD_MSI=0 ;;
        -h|--help) sed -n '2,18p' "$0"; exit 0 ;;
        *) echo "Opcion desconocida: $arg" >&2; exit 2 ;;
    esac
done

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

winpath() { cygpath -w "$1"; }
step() { printf '\n==> %s\n' "$*"; }

PYTHON="${PYTHON:-python}"
APP_VERSION="$("$PYTHON" -c 'import appinfo; print(appinfo.APP_VERSION)')"
APP_NAME="$("$PYTHON" -c 'import appinfo; print(appinfo.APP_NAME)')"
APP_EXE="$("$PYTHON" -c 'import appinfo; print(appinfo.APP_EXE_NAME)')"

BUILD_DIR="$ROOT/build"
VENV="$BUILD_DIR/venv"
PYI_DIR="$BUILD_DIR/pyinstaller"
APP_DIR="$PYI_DIR/dist/$APP_EXE"
RELEASE_DIR="$ROOT/dist/release"
BASENAME="$APP_EXE-$APP_VERSION"

echo "$APP_NAME v$APP_VERSION"

step "Preparando entorno de build"
if [[ ! -x "$VENV/Scripts/python.exe" ]]; then
    "$PYTHON" -m venv "$VENV"
fi
VPY="$VENV/Scripts/python.exe"
"$VPY" -m pip install --quiet --upgrade pip
"$VPY" -m pip install --quiet -r requirements.txt "pyinstaller>=6.16"

step "Generando ejecutable con PyInstaller"
rm -rf "$PYI_DIR" "$RELEASE_DIR"
mkdir -p "$PYI_DIR" "$RELEASE_DIR"

# Recurso de version de Windows (Propiedades > Detalles del .exe).
IFS=. read -r V_MAJOR V_MINOR V_PATCH <<< "$APP_VERSION"
cat > "$PYI_DIR/version_info.txt" <<EOF
VSVersionInfo(
  ffi=FixedFileInfo(filevers=($V_MAJOR, $V_MINOR, $V_PATCH, 0), prodvers=($V_MAJOR, $V_MINOR, $V_PATCH, 0)),
  kids=[
    StringFileInfo([StringTable('040A04B0', [
      StringStruct('CompanyName', 'Astrados'),
      StringStruct('FileDescription', '$APP_NAME'),
      StringStruct('FileVersion', '$APP_VERSION'),
      StringStruct('ProductName', '$APP_NAME'),
      StringStruct('ProductVersion', '$APP_VERSION'),
      StringStruct('OriginalFilename', '$APP_EXE.exe')])]),
    VarFileInfo([VarStruct('Translation', [0x040A, 1200])])
  ]
)
EOF

"$VPY" -m PyInstaller \
    --noconfirm \
    --clean \
    --windowed \
    --name "$APP_EXE" \
    --icon "$(winpath "$ROOT/visual/app.ico")" \
    --version-file "$(winpath "$PYI_DIR/version_info.txt")" \
    --add-data "$(winpath "$ROOT/visual");visual" \
    --distpath "$(winpath "$PYI_DIR/dist")" \
    --workpath "$(winpath "$PYI_DIR/work")" \
    --specpath "$(winpath "$PYI_DIR")" \
    "$(winpath "$ROOT/main.py")"

[[ -f "$APP_DIR/$APP_EXE.exe" ]] || { echo "PyInstaller no genero $APP_EXE.exe" >&2; exit 1; }

step "Creando ZIP portable"
"$VPY" - "$APP_DIR" "$RELEASE_DIR/$BASENAME-portable.zip" <<'PY'
import sys, zipfile, pathlib
src, out = pathlib.Path(sys.argv[1]), sys.argv[2]
with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
    for path in sorted(src.rglob("*")):
        archive.write(path, pathlib.Path(src.name) / path.relative_to(src))
PY

if (( BUILD_SETUP )); then
    step "Generando setup.exe con Inno Setup"
    ISCC=""
    for candidate in "$(command -v iscc 2>/dev/null || true)" "$(command -v ISCC 2>/dev/null || true)" \
        "/c/Program Files (x86)/Inno Setup 6/ISCC.exe" "/c/Program Files/Inno Setup 6/ISCC.exe" \
        "$(cygpath -u "${LOCALAPPDATA:-C:/}")/Programs/Inno Setup 6/ISCC.exe"; do
        if [[ -n "$candidate" && -x "$candidate" ]]; then ISCC="$candidate"; break; fi
    done
    if [[ -z "$ISCC" ]]; then
        echo "No se encontro Inno Setup (ISCC.exe). Instalalo con: winget install JRSoftware.InnoSetup" >&2
        echo "o vuelve a ejecutar con --skip-setup." >&2
        exit 1
    fi
    # MSYS convierte argumentos que empiezan con "/" en rutas; los /D de ISCC deben pasar tal cual.
    MSYS2_ARG_CONV_EXCL="*" "$ISCC" /Q \
        "/DAppVersion=$APP_VERSION" \
        "/DSourceDir=$(winpath "$APP_DIR")" \
        "/DOutputDir=$(winpath "$RELEASE_DIR")" \
        "/DIconFile=$(winpath "$ROOT/visual/app.ico")" \
        "$(winpath "$ROOT/installer/AstradosAudioHandler.iss")"
fi

if (( BUILD_MSI )); then
    step "Generando MSI con WiX"
    if ! command -v wix >/dev/null 2>&1; then
        echo "No se encontro WiX. Instalalo con: dotnet tool install --global wix --version 5.0.2" >&2
        echo "o vuelve a ejecutar con --skip-msi." >&2
        exit 1
    fi
    wix build \
        -arch x64 \
        -d "Version=$APP_VERSION" \
        -d "SourceDir=$(winpath "$APP_DIR")" \
        -d "IconFile=$(winpath "$ROOT/visual/app.ico")" \
        -o "$(winpath "$RELEASE_DIR/$BASENAME-x64.msi")" \
        "$(winpath "$ROOT/installer/AstradosAudioHandler.wxs")"
    rm -f "$RELEASE_DIR"/*.wixpdb
fi

step "Calculando checksums"
(cd "$RELEASE_DIR" && sha256sum -- * | grep -v SHA256SUMS > SHA256SUMS.txt)

step "Release lista en dist/release"
ls -lh "$RELEASE_DIR"
