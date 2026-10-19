"""Biblioteca de musica y efectos: archivos, carpetas (categorias) y papelera."""

from __future__ import annotations

import ctypes
import re
from ctypes import wintypes
from dataclasses import dataclass
from pathlib import Path

AUDIO_EXTENSIONS = {".mp3", ".wav", ".ogg", ".flac"}
ALL_CATEGORIES = "Todas"
ROOT_CATEGORY = "General"
FILE_DIALOG_TYPES = [("Audio (MP3, WAV, OGG, FLAC)", "*.mp3 *.wav *.ogg *.flac"), ("Todos los archivos", "*.*")]


@dataclass(frozen=True)
class AudioAsset:
    category: str  # "music" o "sfx"
    path: Path
    root: Path

    @property
    def name(self) -> str:
        return self.path.stem

    @property
    def key(self) -> str:
        """Ruta relativa estable (se usa para guardar atajos por efecto)."""
        return self.path.relative_to(self.root).as_posix()

    @property
    def folder(self) -> str:
        """Carpeta relativa dentro de la biblioteca ("" = raiz)."""
        parent = self.path.parent.relative_to(self.root).as_posix()
        return "" if parent == "." else parent


def scan(root: Path, category: str) -> list[AudioAsset]:
    if not root.exists():
        return []
    files = [path for path in root.rglob("*") if path.is_file() and path.suffix.lower() in AUDIO_EXTENSIONS]
    # Primero por carpeta y luego por nombre, sin distinguir mayusculas.
    files.sort(key=lambda path: (path.parent.relative_to(root).as_posix().casefold(), path.name.casefold()))
    return [AudioAsset(category, path, root) for path in files]


def list_folders(root: Path) -> list[str]:
    if not root.exists():
        return []
    return sorted((path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_dir()), key=str.casefold)


def folder_label(folder: str) -> str:
    return folder or ROOT_CATEGORY


def folder_from_label(label: str) -> str | None:
    """None = todas las carpetas."""
    if label == ALL_CATEGORIES:
        return None
    return "" if label == ROOT_CATEGORY else label


def in_folder(asset: AudioAsset, folder: str | None) -> bool:
    """folder None = todas; "" = solo los archivos sueltos en la raiz; si no, la carpeta y sus subcarpetas."""
    if folder is None:
        return True
    if folder == "":
        return asset.folder == ""
    return asset.folder == folder or asset.folder.startswith(folder + "/")


def filter_assets(assets: list[AudioAsset], folder: str | None, text: str = "") -> list[AudioAsset]:
    text = text.strip().casefold()
    return [
        asset for asset in assets
        if in_folder(asset, folder) and (not text or text in asset.path.name.casefold())
    ]


def sanitize_folder_name(name: str) -> str | None:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", name).strip().strip(".")
    return cleaned or None


def create_folder(root: Path, name: str, parent: str = "") -> Path:
    cleaned = sanitize_folder_name(name)
    if cleaned is None:
        raise ValueError("El nombre de la carpeta no es valido.")
    folder = (root / parent / cleaned) if parent else root / cleaned
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def unique_destination(folder: Path, filename: str) -> Path:
    destination = folder / filename
    counter = 2
    while destination.exists():
        destination = folder / f"{Path(filename).stem} ({counter}){Path(filename).suffix}"
        counter += 1
    return destination


class _SHFILEOPSTRUCTW(ctypes.Structure):
    _fields_ = [
        ("hwnd", wintypes.HWND),
        ("wFunc", wintypes.UINT),
        ("pFrom", wintypes.LPCWSTR),
        ("pTo", wintypes.LPCWSTR),
        ("fFlags", wintypes.WORD),
        ("fAnyOperationsAborted", wintypes.BOOL),
        ("hNameMappings", ctypes.c_void_p),
        ("lpszProgressTitle", wintypes.LPCWSTR),
    ]


def move_to_recycle_bin(path: Path) -> None:
    """Envia el archivo a la Papelera de reciclaje (recuperable), no lo borra definitivamente."""
    FO_DELETE, FOF_SILENT, FOF_NOCONFIRMATION, FOF_ALLOWUNDO, FOF_NOERRORUI = 3, 0x4, 0x10, 0x40, 0x400
    operation = _SHFILEOPSTRUCTW(
        hwnd=None,
        wFunc=FO_DELETE,
        pFrom=str(Path(path).resolve()) + "\0",  # lista terminada en doble NUL
        pTo=None,
        fFlags=FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT | FOF_NOERRORUI,
    )
    result = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(operation))
    if result != 0 or operation.fAnyOperationsAborted:
        raise OSError(f"No se pudo mover a la papelera (codigo {result}).")
