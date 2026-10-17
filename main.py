"""Interfaz principal de Astrados Audio Handler.

La app organiza musica, SFX y microfono en una sola salida local y permite:
- reproducir pistas individuales desde botones Play
- configurar hotkeys globales editables desde la interfaz
- persistir configuracion local (data/config, o %LOCALAPPDATA% en la version instalada)
"""

from __future__ import annotations

import ctypes
import logging
import queue
import re
import os
import shutil
import sys
import threading
import webbrowser
from dataclasses import dataclass
from ctypes import wintypes
from pathlib import Path
from typing import Any, Callable

import sounddevice as sd
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from tkinter import font as tkfont

from appinfo import APP_NAME, APP_VERSION
from logic import errorLog, uiTheme
from logic.congifHandler import DEFAULT_HOTKEYS, configHandler
from logic.soundMotor import resolve_device, soundMotor
from logic.uiSounds import UiSounds
from logic.virtualCable import (
    VB_CABLE_DOWNLOAD_URL,
    VirtualCableStatus,
    detect_virtual_cable,
    is_virtual_cable_name,
    rescan_devices,
)


APP_TITLE = APP_NAME
BASE_DIR = Path(__file__).resolve().parent
FROZEN = bool(getattr(sys, "frozen", False))
# Instalada (PyInstaller) los recursos van junto al exe y Program Files no es
# escribible: la musica, los SFX y la config del usuario viven en %LOCALAPPDATA%.
RESOURCE_DIR = Path(getattr(sys, "_MEIPASS", BASE_DIR))
DATA_DIR = (Path(os.environ.get("LOCALAPPDATA", Path.home())) / APP_NAME) if FROZEN else BASE_DIR / "data"
MUSIC_DIR = DATA_DIR / "music"
SFX_DIR = DATA_DIR / "sfx"
RECORDINGS_DIR = DATA_DIR / "recordings"
CONFIG_DIR = DATA_DIR / "config"
VISUAL_DIR = RESOURCE_DIR / "visual"
DOCS_DIR = RESOURCE_DIR / "docs"
LOG_DIR = DATA_DIR / "logs"
TRACK_EXTENSIONS = {".mp3"}

log = logging.getLogger("astrados")

WM_HOTKEY = 0x0312
WM_QUIT = 0x0012
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000
VK_F1 = 0x70
VK_SPECIAL_KEYS = {
    "SPACE": 0x20,
    "TAB": 0x09,
    "ENTER": 0x0D,
    "RETURN": 0x0D,
    "ESC": 0x1B,
    "ESCAPE": 0x1B,
    "UP": 0x26,
    "DOWN": 0x28,
    "LEFT": 0x25,
    "RIGHT": 0x27,
    "HOME": 0x24,
    "END": 0x23,
    "PAGEUP": 0x21,
    "PAGEDOWN": 0x22,
    "INSERT": 0x2D,
    "DELETE": 0x2E,
    "BACKSPACE": 0x08,
}

HOTKEY_ACTIONS: dict[str, tuple[str, str]] = {
    "refresh_library": ("Actualizar biblioteca", "Recarga las pistas locales"),
    "toggle_mic": ("Alternar microfono", "Activa o desactiva la entrada del microfono"),
    "play_music": ("Play musica", "Reproduce la musica marcada o la primera disponible"),
    "stop_music": ("Stop musica", "Detiene la musica actual"),
    "pause_music": ("Pause musica", "Pausa o reanuda la musica actual"),
    "play_sfx": ("Play SFX", "Reproduce el SFX marcado o el primero disponible"),
    "stop_sfx": ("Stop SFX", "Detiene el SFX actual"),
    "pause_sfx": ("Pause SFX", "Pausa o reanuda el SFX actual"),
    "save_settings": ("Guardar ajustes", "Guarda la configuracion local en disco"),
}


def default_hotkeys() -> dict[str, str]:
    return dict(DEFAULT_HOTKEYS)


@dataclass(frozen=True)
class AudioAsset:
    category: str
    path: Path

    @property
    def name(self) -> str:
        return self.path.stem


def ensure_project_directories() -> None:
    for folder in (DATA_DIR, MUSIC_DIR, SFX_DIR, RECORDINGS_DIR, CONFIG_DIR, LOG_DIR):
        folder.mkdir(parents=True, exist_ok=True)


def scan_local_assets(folder: Path, category: str) -> list[AudioAsset]:
    assets: list[AudioAsset] = []
    if not folder.exists():
        return assets

    for path in sorted(folder.glob("**/*")):
        if path.is_file() and path.suffix.lower() in TRACK_EXTENSIONS:
            assets.append(AudioAsset(category=category, path=path))
    return assets


def format_device_label(index: int, device: dict[str, Any], hostapi_name: str | None = None) -> str:
    name = str(device.get("name", "Unknown device"))
    input_channels = int(device.get("max_input_channels", 0) or 0)
    output_channels = int(device.get("max_output_channels", 0) or 0)
    details = [f"[{index}]", name, f"(in:{input_channels} out:{output_channels})"]
    if hostapi_name:
        details.append(f"({hostapi_name})")
    return " ".join(details)


def parse_hotkey_spec(spec: str) -> tuple[int, int] | None:
    cleaned = spec.strip().replace("-", "+")
    if not cleaned:
        return None

    modifiers = 0
    key_token = ""
    for token in [piece.strip() for piece in cleaned.split("+") if piece.strip()]:
        token_upper = token.upper()
        if token_upper in {"CTRL", "CONTROL"}:
            modifiers |= MOD_CONTROL
        elif token_upper == "ALT":
            modifiers |= MOD_ALT
        elif token_upper == "SHIFT":
            modifiers |= MOD_SHIFT
        elif token_upper in {"WIN", "WINDOWS", "SUPER"}:
            modifiers |= MOD_WIN
        else:
            key_token = token_upper

    if not key_token:
        return None

    if len(key_token) == 1 and key_token.isalnum():
        vk_code = ord(key_token)
    elif key_token in VK_SPECIAL_KEYS:
        vk_code = VK_SPECIAL_KEYS[key_token]
    else:
        match = re.fullmatch(r"F(\d{1,2})", key_token)
        if not match:
            return None
        function_number = int(match.group(1))
        if not 1 <= function_number <= 24:
            return None
        vk_code = VK_F1 + function_number - 1

    return modifiers, vk_code


def format_hotkey_spec(spec: str) -> str:
    return spec.strip() or "Desactivado"


def hotkey_spec_from_event(event: tk.Event) -> str | None:
    keysym = str(getattr(event, "keysym", "")).strip()
    if not keysym:
        return None

    modifier_keys = {
        "SHIFT_L",
        "SHIFT_R",
        "CONTROL_L",
        "CONTROL_R",
        "ALT_L",
        "ALT_R",
        "META_L",
        "META_R",
        "SUPER_L",
        "SUPER_R",
    }
    if keysym.upper() in modifier_keys:
        return None

    modifiers: list[str] = []
    state = int(getattr(event, "state", 0))
    if state & 0x0004:
        modifiers.append("Ctrl")
    if state & 0x0001:
        modifiers.append("Shift")
    if state & 0x0008 or state & 0x20000:
        modifiers.append("Alt")

    normalized = keysym.upper()
    special_keys = {
        "SPACE": "SPACE",
        "TAB": "TAB",
        "RETURN": "ENTER",
        "ENTER": "ENTER",
        "ESC": "ESC",
        "ESCAPE": "ESC",
        "BACKSPACE": "BACKSPACE",
        "DELETE": "DELETE",
        "INSERT": "INSERT",
        "HOME": "HOME",
        "END": "END",
        "PAGE_UP": "PAGEUP",
        "PAGE_DOWN": "PAGEDOWN",
        "UP": "UP",
        "DOWN": "DOWN",
        "LEFT": "LEFT",
        "RIGHT": "RIGHT",
    }

    if len(normalized) == 1 and normalized.isalnum():
        key_token = normalized
    elif re.fullmatch(r"F\d{1,2}", normalized):
        key_token = normalized
    else:
        key_token = special_keys.get(normalized, "")

    if not key_token:
        return None

    parts = modifiers + [key_token]
    return "+".join(parts)


def center_over(window: tk.Toplevel, parent: tk.Misc) -> None:
    """Centra un dialogo sobre la ventana principal (si no, Windows lo deja en una esquina)."""
    window.update_idletasks()
    x = parent.winfo_rootx() + (parent.winfo_width() - window.winfo_reqwidth()) // 2
    y = parent.winfo_rooty() + (parent.winfo_height() - window.winfo_reqheight()) // 3
    window.geometry(f"+{max(0, x)}+{max(0, y)}")


class HotkeyCaptureDialog(tk.Toplevel):
    def __init__(self, parent: tk.Misc, label: str, current_value: str) -> None:
        super().__init__(parent)
        self.result: str | None = None
        self.configure(bg=uiTheme.PAGE_BG)
        self.title(f"Capturar {label}")
        self.resizable(False, False)
        self.transient(parent.winfo_toplevel())
        self.grab_set()

        self.status_var = tk.StringVar(value=f"Actual: {format_hotkey_spec(current_value)}")

        container = uiTheme.card(self)
        container.pack(fill="both", expand=True, padx=12, pady=12)
        body = ttk.Frame(container, style="Card.TFrame", padding=18)
        body.pack(fill="both", expand=True)
        ttk.Label(body, text=f"Pulsa la combinacion para {label}", style="CardBold.TLabel").pack(anchor="w")
        ttk.Label(body, text="Se guardara al presionar una tecla valida. Escape cancela.", style="CardMuted.TLabel").pack(anchor="w", pady=(4, 12))
        ttk.Label(body, textvariable=self.status_var, style="Meter.TLabel").pack(anchor="w", pady=(0, 12))
        ttk.Button(body, text="Cancelar", style="Light.TButton", command=self._cancel).pack(anchor="e")

        self.bind("<KeyPress>", self._on_key_press)
        self.bind("<Escape>", self._cancel)
        center_over(self, parent.winfo_toplevel())
        self.after(50, self.focus_force)

    def _on_key_press(self, event: tk.Event) -> None:
        spec = hotkey_spec_from_event(event)
        if spec is None:
            return
        self.result = spec
        self.status_var.set(f"Capturado: {spec}")
        self.after(80, self.destroy)

    def _cancel(self, _event: tk.Event | None = None) -> None:
        self.result = None
        self.destroy()


class ScrollableFrame(ttk.Frame):
    """Contenedor con scroll vertical; el contenido va en self.inner."""

    def __init__(self, parent: tk.Misc) -> None:
        super().__init__(parent)
        self.canvas = tk.Canvas(self, bg=uiTheme.PAGE_BG, highlightthickness=0)
        scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)

        self.inner = ttk.Frame(self.canvas)
        self._inner_id = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.inner.bind("<Configure>", lambda _event: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda event: self.canvas.itemconfigure(self._inner_id, width=event.width))
        # La rueda solo desplaza el panel que esta bajo el puntero.
        self.canvas.bind("<Enter>", lambda _event: self.canvas.bind_all("<MouseWheel>", self._on_wheel))
        self.canvas.bind("<Leave>", lambda _event: self.canvas.unbind_all("<MouseWheel>"))

    def _on_wheel(self, event: tk.Event) -> None:
        if self.canvas.yview() != (0.0, 1.0):
            self.canvas.yview_scroll(int(-event.delta / 120), "units")


class GlobalHotkeyManager:
    """Hotkeys globales de Windows en un hilo propio.

    Con hWnd=NULL, RegisterHotKey entrega WM_HOTKEY a la cola del hilo que lo
    registro, por eso el registro, la espera de mensajes y el desregistro ocurren
    todos dentro del mismo hilo de trabajo.
    """

    def __init__(self, dispatch: Callable[[str], None]) -> None:
        self._dispatch = dispatch
        self._queue: queue.Queue[str] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._thread_id: int | None = None
        self._current: dict[str, str] | None = None

    def set_hotkeys(self, hotkeys: dict[str, str]) -> list[str]:
        if self._current == hotkeys:
            return []

        self.stop()
        self._current = dict(hotkeys)
        errors: list[str] = []
        parsed_hotkeys: list[tuple[str, str, int, int]] = []
        for action, spec in hotkeys.items():
            parsed = parse_hotkey_spec(spec)
            if parsed is None:
                if spec.strip():
                    errors.append(f"{action}: hotkey invalido ({spec})")
                continue
            parsed_hotkeys.append((action, spec, parsed[0], parsed[1]))

        if not parsed_hotkeys:
            return errors

        ready = threading.Event()
        registration_errors: list[str] = []
        self._thread = threading.Thread(
            target=self._message_loop,
            args=(parsed_hotkeys, ready, registration_errors),
            daemon=True,
        )
        self._thread.start()
        if not ready.wait(timeout=2.0):
            errors.append("No se pudo iniciar el hilo de hotkeys globales.")
        return errors + registration_errors

    def _message_loop(
        self,
        parsed_hotkeys: list[tuple[str, str, int, int]],
        ready: threading.Event,
        errors: list[str],
    ) -> None:
        user32 = ctypes.windll.user32
        msg = wintypes.MSG()
        # Fuerza la creacion de la cola de mensajes antes de publicar el id del hilo,
        # para que PostThreadMessageW(WM_QUIT) desde stop() no se pierda.
        user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 0)
        self._thread_id = ctypes.windll.kernel32.GetCurrentThreadId()

        registered: dict[int, str] = {}
        for hotkey_id, (action, spec, modifiers, vk_code) in enumerate(parsed_hotkeys, start=1):
            if user32.RegisterHotKey(None, hotkey_id, modifiers | MOD_NOREPEAT, vk_code):
                registered[hotkey_id] = action
            else:
                errors.append(f"{action}: no se pudo registrar {spec} (otra app ya lo usa)")
        ready.set()

        try:
            while True:
                result = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
                if result <= 0:
                    break
                if msg.message == WM_HOTKEY:
                    action = registered.get(int(msg.wParam))
                    if action:
                        self._queue.put(action)
        finally:
            for hotkey_id in registered:
                user32.UnregisterHotKey(None, hotkey_id)

    def poll(self) -> list[str]:
        actions: list[str] = []
        while True:
            try:
                actions.append(self._queue.get_nowait())
            except queue.Empty:
                break
        return actions

    def stop(self) -> None:
        self._current = None
        if self._thread_id is not None:
            ctypes.windll.user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
            self._thread_id = None

        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=1.0)
        self._thread = None


class DocumentView(ttk.Frame):
    """Visor de texto con formato minimo de Markdown (titulos, vinetas, **negrita**, `codigo`)."""

    INLINE = re.compile(r"(\*\*.+?\*\*|`.+?`)")

    def __init__(self, parent: tk.Misc, theme: uiTheme.Theme) -> None:
        super().__init__(parent, style="Card.TFrame")
        px = theme.px
        self.text = tk.Text(
            self, wrap="word", relief="flat", borderwidth=0, highlightthickness=0,
            bg=uiTheme.CARD_BG, fg=uiTheme.TEXT, font=(uiTheme.FONT, 10),
            padx=px(20), pady=px(14), cursor="arrow", spacing1=px(2), spacing3=px(2),
            selectbackground=uiTheme.BLUE, selectforeground="white",
        )
        scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.text.yview)
        self.text.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        self.text.pack(side="left", fill="both", expand=True)

        text = self.text
        text.tag_configure("h1", font=(uiTheme.FONT, 17, "bold"), foreground=uiTheme.TEXT, spacing1=px(4), spacing3=px(8))
        text.tag_configure("h2", font=(uiTheme.FONT, 12, "bold"), foreground=uiTheme.PURPLE, spacing1=px(14), spacing3=px(4))
        text.tag_configure("bold", font=(uiTheme.FONT, 10, "bold"))
        text.tag_configure("code", font=("Consolas", 9), background=uiTheme.CARD_ALT_BG, foreground=uiTheme.PURPLE)
        text.tag_configure("item", lmargin1=px(14), lmargin2=px(30))
        text.tag_configure("item2", lmargin1=px(38), lmargin2=px(54))
        text.tag_configure("cont", lmargin1=px(30), lmargin2=px(30))
        text.tag_configure("log", font=("Consolas", 9), foreground=uiTheme.TEXT)
        text.tag_configure("log_warning", foreground=uiTheme.WARN)
        text.tag_configure("log_error", foreground=uiTheme.ERROR_RED)
        text.tag_configure("muted", foreground=uiTheme.MUTED)
        # Linea en blanco del Markdown: un respiro corto, no una linea entera.
        text.tag_configure("gap", font=(uiTheme.FONT, 3))
        text.configure(state="disabled")

    def _insert_inline(self, line: str, tags: tuple[str, ...]) -> None:
        for piece in self.INLINE.split(line):
            if not piece:
                continue
            if piece.startswith("**") and piece.endswith("**"):
                self.text.insert("end", piece[2:-2], tags + ("bold",))
            elif piece.startswith("`") and piece.endswith("`"):
                self.text.insert("end", piece[1:-1], tags + ("code",))
            else:
                self.text.insert("end", piece, tags)

    def show_markdown(self, markdown: str) -> None:
        text = self.text
        text.configure(state="normal")
        text.delete("1.0", "end")
        for raw in markdown.splitlines():
            stripped = raw.strip()
            indent = len(raw) - len(raw.lstrip(" "))
            if raw.startswith("# "):
                text.insert("end", raw[2:] + "\n", ("h1",))
            elif raw.startswith("## "):
                text.insert("end", raw[3:] + "\n", ("h2",))
            elif stripped.startswith("- "):
                tag = "item2" if indent >= 2 else "item"
                text.insert("end", "\u2022  ", (tag,))
                self._insert_inline(stripped[2:], (tag,))
                text.insert("end", "\n", (tag,))
            elif re.match(r"^\d+\. ", stripped):
                number, rest = stripped.split(" ", 1)
                text.insert("end", number + "  ", ("item", "bold"))
                self._insert_inline(rest, ("item",))
                text.insert("end", "\n", ("item",))
            elif not stripped:
                text.insert("end", "\n", ("gap",))
            elif indent >= 2:
                self._insert_inline(stripped, ("cont",))
                text.insert("end", "\n", ("cont",))
            else:
                self._insert_inline(stripped, ())
                text.insert("end", "\n")
        text.configure(state="disabled")
        text.yview_moveto(0)

    def show_log(self, content: str, path: Path | None) -> None:
        text = self.text
        text.configure(state="normal")
        text.delete("1.0", "end")
        text.insert("end", "Log de errores\n", ("h1",))
        text.insert("end", f"{path}\n\n" if path else "\n", ("muted",))
        if not content.strip():
            text.insert("end", "Sin errores registrados.\n", ("log",))
        for line in content.splitlines():
            tags: tuple[str, ...] = ("log",)
            if "[ERROR]" in line or "[CRITICAL]" in line:
                tags += ("log_error",)
            elif "[WARNING]" in line:
                tags += ("log_warning",)
            text.insert("end", line + "\n", tags)
        text.configure(state="disabled")
        text.see("end")


class AudioLibraryPanel(ttk.Frame):
    def __init__(
        self,
        parent: tk.Widget,
        theme: uiTheme.Theme,
        title: str,
        description: str,
        icon: str,
        show_progress: bool = False,
    ) -> None:
        super().__init__(parent, padding=(16, 14, 16, 8))
        self.theme = theme
        self.title = title
        self.description = description
        self.icon = icon
        self.show_progress = show_progress
        self.assets: list[AudioAsset] = []
        self.play_handler: Callable[[AudioAsset], None] | None = None
        self.pause_handler: Callable[[], None] | None = None
        self.stop_handler: Callable[[], None] | None = None
        self.seek_handler: Callable[[AudioAsset, float], None] | None = None
        self.search_var = tk.StringVar(value="")
        self._row_state: dict[Path, dict[str, Any]] = {}

        self._build()

    def _build(self) -> None:
        heading = ttk.Frame(self)
        heading.pack(fill="x")
        ttk.Label(heading, text=f"  {self.title}", image=self.theme.image(self.icon), compound="left", style="Title.TLabel").pack(side="left")

        search_bar = ttk.Frame(heading)
        search_bar.pack(side="right")
        ttk.Label(search_bar, text="Filtro:").pack(side="left", padx=(0, 6))
        ttk.Entry(search_bar, textvariable=self.search_var, width=32).pack(side="left")
        self.search_var.trace_add("write", lambda *_: self._render())

        ttk.Label(self, text=self.description, style="Muted.TLabel").pack(anchor="w", pady=(2, 10))

        self.scroller = ScrollableFrame(self)
        self.scroller.pack(fill="both", expand=True)
        self.content = self.scroller.inner

    def set_assets(self, assets: list[AudioAsset]) -> None:
        self.assets = list(assets)
        self._render()

    def set_play_handler(self, handler: Callable[[AudioAsset], None]) -> None:
        self.play_handler = handler

    def set_action_handlers(
        self,
        play_handler: Callable[[AudioAsset], None] | None = None,
        pause_handler: Callable[[], None] | None = None,
        stop_handler: Callable[[], None] | None = None,
    ) -> None:
        self.play_handler = play_handler
        self.pause_handler = pause_handler
        self.stop_handler = stop_handler

    def set_seek_handler(self, handler: Callable[[AudioAsset, float], None] | None) -> None:
        self.seek_handler = handler

    def update_progress(
        self,
        asset: AudioAsset | None,
        current_seconds: float,
        duration_seconds: float,
        is_playing: bool,
        is_paused: bool,
    ) -> None:
        active_path = asset.path if asset is not None else None
        for path, state in self._row_state.items():
            is_active = active_path is not None and path == active_path and (is_playing or is_paused)
            state["row"].configure(highlightbackground=uiTheme.PURPLE if is_active else uiTheme.BORDER,
                                   highlightthickness=2 if is_active else 1)
            if not self.show_progress or state.get("dragging"):
                continue

            progress_var = state["progress_var"]
            time_var = state["time_var"]
            if not is_active or duration_seconds <= 0:
                progress_var.set(0.0)
                time_var.set("0:00 / 0:00")
                continue

            percentage = max(0.0, min(100.0, (current_seconds / duration_seconds) * 100.0))
            progress_var.set(percentage)
            time_var.set(f"{self._format_time(current_seconds)} / {self._format_time(duration_seconds)}")

    def _format_time(self, seconds: float) -> str:
        total_seconds = max(0, int(seconds))
        minutes, remaining_seconds = divmod(total_seconds, 60)
        return f"{minutes}:{remaining_seconds:02d}"

    def _render(self) -> None:
        for child in self.content.winfo_children():
            child.destroy()
        self._row_state.clear()

        filter_text = self.search_var.get().strip().lower()
        filtered_assets = [
            asset for asset in self.assets
            if not filter_text or filter_text in asset.path.name.lower() or filter_text in asset.path.stem.lower()
        ]

        if not filtered_assets:
            empty = uiTheme.card(self.content)
            empty.pack(fill="x", pady=6)
            ttk.Label(empty, text="No hay archivos para mostrar. Usa los botones de arriba para agregar MP3.",
                      style="CardMuted.TLabel", padding=16).pack(anchor="w")
            return

        for asset in filtered_assets:
            row = uiTheme.card(self.content)
            row.pack(fill="x", pady=4, padx=(0, 6))
            row.columnconfigure(0, minsize=self.theme.px(260))
            row.columnconfigure(1, weight=1)

            text_frame = ttk.Frame(row, style="Card.TFrame")
            text_frame.grid(row=0, column=0, sticky="w", padx=(14, 10), pady=10)
            ttk.Label(text_frame, text=asset.path.stem, style="CardBold.TLabel").pack(anchor="w")
            ttk.Label(text_frame, text=f"{asset.path.parent.name}  -  {asset.path.suffix[1:].upper()}", style="CardMuted.TLabel").pack(anchor="w")

            progress_var = tk.DoubleVar(value=0.0)
            time_var = tk.StringVar(value="0:00 / 0:00")
            state: dict[str, Any] = {"row": row, "progress_var": progress_var, "time_var": time_var, "dragging": False}
            if self.show_progress:
                progress_frame = ttk.Frame(row, style="Card.TFrame")
                progress_frame.grid(row=0, column=1, sticky="ew", padx=8, pady=10)
                progress_frame.columnconfigure(0, weight=1)
                scale = ttk.Scale(progress_frame, from_=0, to=100, orient="horizontal", variable=progress_var)
                scale.grid(row=0, column=0, sticky="ew")
                # Mientras se arrastra no se pisa la posicion con el refresco periodico.
                scale.bind("<ButtonPress-1>", lambda _event, current=state: current.update(dragging=True))
                scale.bind(
                    "<ButtonRelease-1>",
                    lambda _event, current_asset=asset, current=state: self._end_drag(current_asset, current),
                )
                ttk.Label(progress_frame, textvariable=time_var, style="CardMuted.TLabel").grid(row=1, column=0, sticky="w", pady=(2, 0))

            action_frame = ttk.Frame(row, style="Card.TFrame")
            action_frame.grid(row=0, column=2, sticky="e", padx=(8, 12), pady=8)
            for orb_name, command in (
                ("orb_play", lambda current_asset=asset: self._play(current_asset)),
                ("orb_pause", self._pause),
                ("orb_stop", self._stop),
            ):
                ttk.Button(action_frame, image=self.theme.button_images(orb_name), style="Orb.TButton",
                           command=command, cursor="hand2", takefocus=False).pack(side="left", padx=2)

            self._row_state[asset.path] = state

    def _end_drag(self, asset: AudioAsset, state: dict[str, Any]) -> None:
        state["dragging"] = False
        self._seek(asset, float(state["progress_var"].get()))

    def _play(self, asset: AudioAsset) -> None:
        if self.play_handler is not None:
            self.play_handler(asset)

    def _pause(self) -> None:
        if self.pause_handler is not None:
            self.pause_handler()

    def _stop(self) -> None:
        if self.stop_handler is not None:
            self.stop_handler()

    def _seek(self, asset: AudioAsset, percentage: float) -> None:
        if self.seek_handler is not None:
            self.seek_handler(asset, max(0.0, min(100.0, percentage)))


class AudioHandlerApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title(f"{APP_TITLE} v{APP_VERSION}")
        self.theme = uiTheme.Theme(root, VISUAL_DIR / "ui")
        px = self.theme.px
        # La ventana inicial no debe pasarse del area de trabajo con escalados altos.
        width = min(px(1200), root.winfo_screenwidth() - px(40))
        height = min(px(820), root.winfo_screenheight() - px(120))
        self.root.geometry(f"{width}x{height}")
        self.root.minsize(min(px(1060), width), min(px(700), height))
        self.root.iconphoto(True, self.theme.image("app_icon"))
        self._ui_ready = False

        self.status_text = tk.StringVar(value="Listo para configurar la biblioteca local.")
        self.device_warnings: list[str] = []
        self.cable_status = VirtualCableStatus(installed=False)
        self.cable_status_text = tk.StringVar(value="Buscando cable virtual...")
        self.music_assets: list[AudioAsset] = []
        self.sfx_assets: list[AudioAsset] = []
        self.active_music_asset: AudioAsset | None = None
        self.active_sfx_asset: AudioAsset | None = None

        self.config_manager = configHandler(CONFIG_DIR)
        self.ui_sounds = UiSounds(VISUAL_DIR / "sounds", bool(self.config_manager.as_dict().get("ui_sounds", True)))
        self.audio_motor = soundMotor(self.config_manager)
        self.hotkey_queue: queue.Queue[str] = queue.Queue()
        self.hotkey_manager = GlobalHotkeyManager(self._queue_hotkey_action)

        self.volume_vars = {
            "volume_music": tk.DoubleVar(value=self.config_manager.volume_music()),
            "volume_sfx": tk.DoubleVar(value=self.config_manager.volume_sfx()),
            "volume_mic": tk.DoubleVar(value=self.config_manager.mic_volume()),
        }
        self.bool_vars = {
            "mic_active": tk.BooleanVar(value=self.config_manager.mic_active()),
            "loop_music": tk.BooleanVar(value=self.config_manager.loop_music()),
            "loop_sfx": tk.BooleanVar(value=self.config_manager.loop_sfx()),
            "monitor_audio": tk.BooleanVar(value=bool(self.config_manager.as_dict().get("monitor_audio", False))),
            "mic_noise_suppression": tk.BooleanVar(value=bool(self.config_manager.as_dict().get("mic_noise_suppression", False))),
            "mic_noise_gate": tk.BooleanVar(value=bool(self.config_manager.as_dict().get("mic_noise_gate", False))),
            "mic_highpass": tk.BooleanVar(value=bool(self.config_manager.as_dict().get("mic_highpass", True))),
            "mic_ducking": tk.BooleanVar(value=bool(self.config_manager.as_dict().get("mic_ducking", True))),
            "ui_sounds": tk.BooleanVar(value=self.ui_sounds.enabled),
        }
        self.mic_filter_vars = {
            "mic_suppression_strength": tk.DoubleVar(value=float(self.config_manager.as_dict().get("mic_suppression_strength", 0.6))),
            "mic_gate_threshold_db": tk.DoubleVar(value=float(self.config_manager.as_dict().get("mic_gate_threshold_db", -45.0))),
            "mic_ducking_level": tk.DoubleVar(value=float(self.config_manager.as_dict().get("mic_ducking_level", 0.4))),
        }
        self.mic_level_text = tk.StringVar(value="Microfono inactivo.")
        self.device_vars = {
            "output_device": tk.StringVar(value=self.config_manager.output_device() or self.config_manager.default_output_device() or ""),
            "mic_input_device": tk.StringVar(value=self.config_manager.mic_input_device() or ""),
            "monitor_output_device": tk.StringVar(value=self.config_manager.as_dict().get("monitor_output_device") or self.config_manager.output_device() or self.config_manager.default_output_device() or ""),
        }
        self.hotkey_vars = {
            action: tk.StringVar(value=self.config_manager.hotkeys().get(action, default_hotkeys()[action]))
            for action in HOTKEY_ACTIONS
        }

        self._build_ui()
        self.refresh_library()
        self._load_device_lists()
        self._autoselect_virtual_cable()
        self.apply_settings(persist=False)
        self._update_cable_status()
        self._register_hotkeys()
        self.root.after(600, self._check_virtual_cable_on_startup)
        self.root.after(100, self._poll_hotkey_events)
        self.root.after(150, self._poll_playback_state)
        self.root.after(50, self._tick_audio)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.report_callback_exception = self._report_tk_error
        self._bind_ui_sounds()
        self._ui_ready = True
        self.root.after(250, lambda: self.ui_sounds.play("startup"))

    def _bind_ui_sounds(self) -> None:
        # Al presionar (antes del comando): si el comando tiene su propio sonido, lo reemplaza.
        def click(event: tk.Event) -> None:
            if event.widget.instate(["!disabled"]):
                self.ui_sounds.play("click")

        def toggle(event: tk.Event) -> None:
            if event.widget.instate(["!disabled"]):
                self.ui_sounds.play("toggle_off" if event.widget.instate(["selected"]) else "toggle_on")

        self.root.bind_class("TButton", "<ButtonPress-1>", click, add="+")
        self.root.bind_class("TCheckbutton", "<ButtonPress-1>", toggle, add="+")

    def _toggle_ui_sounds(self) -> None:
        self.ui_sounds.enabled = bool(self.bool_vars["ui_sounds"].get())
        self.config_manager.updateConfig({"ui_sounds": self.ui_sounds.enabled})

    def _apply_from_button(self) -> None:
        self.apply_settings(persist=False)
        self.ui_sounds.play("confirm")

    def _build_ui(self) -> None:
        self.root.configure(bg=uiTheme.PAGE_BG)
        self._build_header()
        self._build_status_bar()

        action_bar = ttk.Frame(self.root, padding=(16, 10, 16, 4))
        action_bar.pack(fill="x")
        images = self.theme.images
        ttk.Button(action_bar, text=" Actualizar biblioteca", image=images["icon_arrow-path_dark"], compound="left",
                   style="Light.TButton", command=self.refresh_library).pack(side="left")
        ttk.Button(action_bar, text=" Agregar musica", image=images["icon_folder-plus_white"], compound="left",
                   style="Blue.TButton", command=lambda: self._pick_file_and_copy("music")).pack(side="left", padx=8)
        ttk.Button(action_bar, text=" Agregar efecto", image=images["icon_plus_white"], compound="left",
                   style="Blue.TButton", command=lambda: self._pick_file_and_copy("sfx")).pack(side="left")
        ttk.Button(action_bar, text=" Guardar ajustes", image=images["icon_arrow-down-tray_white"], compound="left",
                   style="Purple.TButton", command=self.save_settings).pack(side="right")
        self.mic_button = ttk.Button(action_bar, text=" Microfono", image=images["icon_microphone_dark"], compound="left",
                                     style="Light.TButton", command=self.toggle_microphone)
        self.mic_button.pack(side="right", padx=8)

        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill="both", expand=True, padx=16, pady=(6, 8))

        self.music_panel = AudioLibraryPanel(self.notebook, self.theme, "Musica", "Pistas locales que suenan en loop de fondo.", "icon_musical-note", show_progress=True)
        self.sfx_panel = AudioLibraryPanel(self.notebook, self.theme, "Efectos", "Sonidos disparados por evento, sin silenciar las otras fuentes.", "icon_sparkles")
        self.hotkeys_tab = self._build_hotkeys_tab(self.notebook)
        self.config_tab = self._build_config_tab(self.notebook)
        self.engine_tab = self._build_engine_tab(self.notebook)
        self.help_tab = self._build_help_tab(self.notebook)

        for widget, text, icon in (
            (self.music_panel, "Musica", "icon_musical-note"),
            (self.sfx_panel, "Efectos", "icon_sparkles"),
            (self.hotkeys_tab, "Atajos", "icon_command-line"),
            (self.config_tab, "Config", "icon_cog-6-tooth"),
            (self.engine_tab, "Motor", "icon_cpu-chip"),
            (self.help_tab, "Ayuda", "icon_question-mark-circle"),
        ):
            self.notebook.add(widget, text=f" {text}", image=images[icon], compound="left")
        self.notebook.bind("<<NotebookTabChanged>>", lambda _event: self._ui_ready and self.ui_sounds.play("click"))

        self.music_panel.set_action_handlers(self.play_music_asset, self.toggle_music_pause, self.stop_music)
        self.music_panel.set_seek_handler(self.seek_music_asset)
        self.sfx_panel.set_action_handlers(self.play_sfx_asset, self.toggle_sfx_pause, self.stop_sfx)

    def _build_header(self) -> None:
        images = self.theme.images
        px = self.theme.px
        header = tk.Canvas(self.root, height=px(112), highlightthickness=0, bg=uiTheme.PAGE_BG)
        header.pack(fill="x", side="top")
        header.create_image(0, 0, image=images["header"], anchor="nw")
        bubbles = header.create_image(px(1200), px(4), image=images["header_bubbles"], anchor="ne")
        header.create_image(px(14), px(8), image=images["logo"], anchor="nw")
        for offset, color in ((px(2), "#2a1660"), (0, "white")):
            header.create_text(px(120) + offset, px(40) + offset, text=APP_TITLE, anchor="w", fill=color, font=(uiTheme.FONT, 24, "bold"))
        header.create_text(px(122), px(76), anchor="w", fill="#eef4ff", font=(uiTheme.FONT, 10),
                           text="Musica, SFX y microfono mezclados en una sola salida para Discord.")
        header.bind("<Configure>", lambda event: header.coords(bubbles, event.width - px(18), px(4)))

    def _build_status_bar(self) -> None:
        images = self.theme.images
        px = self.theme.px
        bar = tk.Canvas(self.root, height=px(30), highlightthickness=0, bg=uiTheme.PAGE_BG)
        bar.pack(fill="x", side="bottom")
        bar.create_image(0, 0, image=images["statusbar"], anchor="nw")
        bar.create_line(0, 0, 4000, 0, fill=uiTheme.BORDER)
        status_font = tkfont.Font(root=self.root, family=uiTheme.FONT, size=9)
        status_item = bar.create_text(px(14), px(15), anchor="w", fill=uiTheme.TEXT, font=status_font, text=self.status_text.get())
        self._mic_led_item = bar.create_image(px(1000), px(15), image=images["led_off"], anchor="e")
        self._mic_led_text = bar.create_text(px(1000), px(15), anchor="e", fill=uiTheme.TEXT, font=(uiTheme.FONT, 9, "bold"), text="Microfono apagado")
        self._status_canvas = bar

        def refresh_text(*_args: Any) -> None:
            led_left = bar.bbox(self._mic_led_item)[0] if bar.bbox(self._mic_led_item) else bar.winfo_width()
            available = max(px(80), led_left - px(30))
            text = self.status_text.get()
            if status_font.measure(text) > available:
                while text and status_font.measure(text + "...") > available:
                    text = text[:-1]
                text += "..."
            bar.itemconfigure(status_item, text=text)

        def relayout(_event: tk.Event | None = None) -> None:
            width = bar.winfo_width()
            bar.coords(self._mic_led_text, width - px(14), px(15))
            text_box = bar.bbox(self._mic_led_text)
            bar.coords(self._mic_led_item, width - px(20) - (text_box[2] - text_box[0]), px(15))
            refresh_text()

        self._relayout_status = relayout
        bar.bind("<Configure>", relayout)
        self.status_text.trace_add("write", refresh_text)

    def _sync_mic_indicator(self) -> None:
        active = self.audio_motor.mic_active
        bar = self._status_canvas
        bar.itemconfigure(self._mic_led_item, image=self.theme.image("led_on" if active else "led_off"))
        label = "Microfono activo" if active else "Microfono apagado"
        if bar.itemcget(self._mic_led_text, "text") != label:
            bar.itemconfigure(self._mic_led_text, text=label)
            self._relayout_status()
        self.mic_button.configure(style="Purple.TButton" if active else "Light.TButton",
                                  image=self.theme.image("icon_microphone_white" if active else "icon_microphone_dark"))

    def _page_heading(self, parent: tk.Misc, title: str, description: str) -> None:
        ttk.Label(parent, text=title, style="Title.TLabel").pack(anchor="w")
        ttk.Label(parent, text=description, style="Muted.TLabel").pack(anchor="w", pady=(2, 10))

    def _build_hotkeys_tab(self, parent: ttk.Notebook) -> ttk.Frame:
        page = ScrollableFrame(parent)
        inner = ttk.Frame(page.inner, padding=(16, 14, 16, 8))
        inner.pack(fill="both", expand=True)
        self._page_heading(inner, "Atajos globales", "Funcionan aun con la ventana minimizada. Usa combinaciones poco comunes (Ctrl+Alt+Shift) para no pisar otras apps.")

        holder = uiTheme.card(inner)
        holder.pack(fill="x")
        body = ttk.Frame(holder, style="Card.TFrame", padding=(14, 8))
        body.pack(fill="x")
        body.columnconfigure(1, weight=1)

        row_index = 0
        for action, (label, description) in HOTKEY_ACTIONS.items():
            ttk.Label(body, text=label, style="CardBold.TLabel").grid(row=row_index, column=0, sticky="w", pady=(8, 0))
            ttk.Label(body, text=description, style="CardMuted.TLabel").grid(row=row_index + 1, column=0, sticky="w", pady=(0, 6))
            ttk.Entry(body, textvariable=self.hotkey_vars[action], width=28).grid(row=row_index, column=1, rowspan=2, sticky="ew", padx=12)
            ttk.Button(body, text="Capturar", style="Light.TButton",
                       command=lambda current_action=action: self.capture_hotkey(current_action)).grid(row=row_index, column=2, rowspan=2, sticky="e")
            row_index += 2

        button_row = ttk.Frame(inner, padding=(0, 12, 0, 0))
        button_row.pack(fill="x")
        ttk.Button(button_row, text=" Aplicar hotkeys", image=self.theme.image("icon_check_white"), compound="left",
                   style="Blue.TButton", command=self.apply_hotkeys).pack(side="left")
        ttk.Button(button_row, text="Restaurar hotkeys", style="Light.TButton", command=self.reset_hotkeys).pack(side="left", padx=8)
        ttk.Button(button_row, text=" Guardar ajustes", image=self.theme.image("icon_arrow-down-tray_white"), compound="left",
                   style="Purple.TButton", command=self.save_settings).pack(side="right")
        return page

    def _slider(self, parent: tk.Misc, row: int, text: str, variable: tk.DoubleVar, from_: float, to: float,
                fmt: Callable[[float], str], command: Callable[[], None] | None = None) -> None:
        ttk.Label(parent, text=text, style="Card.TLabel").grid(row=row, column=0, sticky="w", pady=6)
        ttk.Scale(parent, from_=from_, to=to, orient="horizontal", variable=variable,
                  command=(lambda _value: command()) if command else None).grid(row=row, column=1, sticky="ew", padx=10, pady=6)
        value_label = ttk.Label(parent, text=fmt(variable.get()), style="Meter.TLabel", width=8)
        value_label.grid(row=row, column=2, sticky="e")
        variable.trace_add("write", lambda *_: value_label.configure(text=fmt(variable.get())))

    def _build_config_tab(self, parent: ttk.Notebook) -> ttk.Frame:
        page = ScrollableFrame(parent)
        inner = ttk.Frame(page.inner, padding=(16, 14, 16, 8))
        inner.pack(fill="both", expand=True)
        self._page_heading(inner, "Configuracion de audio", "Volumenes, dispositivos y filtro del microfono.")

        columns = ttk.Frame(inner)
        columns.pack(fill="both", expand=True)
        columns.columnconfigure(0, weight=1, uniform="cols")
        columns.columnconfigure(1, weight=1, uniform="cols")
        left = ttk.Frame(columns)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 8))
        right = ttk.Frame(columns)
        right.grid(row=0, column=1, sticky="nsew", padx=(8, 0))

        percent = lambda value: f"{float(value) * 100:3.0f}%"
        volumes = ttk.LabelFrame(left, text=" Volumen y reproduccion ", padding=12)
        volumes.pack(fill="x")
        volumes.columnconfigure(1, weight=1)
        self._slider(volumes, 0, "Musica", self.volume_vars["volume_music"], 0, 1, percent)
        self._slider(volumes, 1, "SFX", self.volume_vars["volume_sfx"], 0, 1, percent)
        self._slider(volumes, 2, "Microfono", self.volume_vars["volume_mic"], 0, 1, percent)
        switches = ttk.Frame(volumes, style="Card.TFrame")
        switches.grid(row=3, column=0, columnspan=3, sticky="w", pady=(8, 0))
        for index, (key, text) in enumerate((
            ("mic_active", "Microfono activo"),
            ("loop_music", "Loop musica"),
            ("loop_sfx", "Loop SFX"),
            ("monitor_audio", "Escucharme localmente (monitor)"),
        )):
            ttk.Checkbutton(switches, text=text, variable=self.bool_vars[key]).grid(row=index // 2, column=index % 2, sticky="w", padx=(0, 24))
        ttk.Checkbutton(switches, text="Sonidos de interfaz", variable=self.bool_vars["ui_sounds"],
                        command=self._toggle_ui_sounds).grid(row=2, column=0, sticky="w")

        devices = ttk.LabelFrame(left, text=" Dispositivos ", padding=12)
        devices.pack(fill="x", pady=(12, 0))
        devices.columnconfigure(1, weight=1)
        ttk.Label(devices, text="Salida objetivo", style="Card.TLabel").grid(row=0, column=0, sticky="w", padx=(0, 8), pady=5)
        self.output_device_combo = ttk.Combobox(devices, textvariable=self.device_vars["output_device"], state="readonly")
        self.output_device_combo.grid(row=0, column=1, sticky="ew", pady=5)
        ttk.Label(devices, text="Entrada microfono", style="Card.TLabel").grid(row=1, column=0, sticky="w", padx=(0, 8), pady=5)
        self.mic_device_combo = ttk.Combobox(devices, textvariable=self.device_vars["mic_input_device"], state="readonly")
        self.mic_device_combo.grid(row=1, column=1, sticky="ew", pady=5)
        ttk.Label(devices, text="Salida monitor", style="Card.TLabel").grid(row=2, column=0, sticky="w", padx=(0, 8), pady=5)
        self.monitor_device_combo = ttk.Combobox(devices, textvariable=self.device_vars["monitor_output_device"], state="readonly")
        self.monitor_device_combo.grid(row=2, column=1, sticky="ew", pady=5)

        self.cable_status_label = ttk.Label(devices, textvariable=self.cable_status_text, style="Status.TLabel", wraplength=self.theme.px(470), justify="left")
        self.cable_status_label.grid(row=3, column=0, columnspan=2, sticky="w", pady=(10, 6))
        cable_buttons = ttk.Frame(devices, style="Card.TFrame")
        cable_buttons.grid(row=4, column=0, columnspan=2, sticky="w")
        self.use_cable_button = ttk.Button(cable_buttons, text=" Usar cable", image=self.theme.image("icon_bolt_white"), compound="left",
                                           style="Purple.TButton", command=self.use_virtual_cable)
        self.use_cable_button.pack(side="left")
        ttk.Button(cable_buttons, text="Buscar de nuevo", image=self.theme.image("icon_arrow-path_dark"), compound="left",
                   style="Light.TButton", command=self.rescan_audio_devices).pack(side="left", padx=6)
        ttk.Button(cable_buttons, text="Guia", style="Light.TButton", command=self.show_cable_install_guide).pack(side="left")

        apply_row = ttk.Frame(left, padding=(0, 12, 0, 0))
        apply_row.pack(fill="x")
        ttk.Button(apply_row, text=" Aplicar ajustes", image=self.theme.image("icon_check_white"), compound="left",
                   style="Blue.TButton", command=self._apply_from_button).pack(side="left")
        ttk.Button(apply_row, text=" Guardar ajustes", image=self.theme.image("icon_arrow-down-tray_white"), compound="left",
                   style="Purple.TButton", command=self.save_settings).pack(side="left", padx=8)

        self._build_mic_filter_section(right)
        return page

    def _build_mic_filter_section(self, parent: tk.Widget) -> None:
        section = ttk.LabelFrame(parent, text=" Filtro de microfono (nativo) ", padding=12)
        section.pack(fill="both", expand=True)
        section.columnconfigure(1, weight=1)

        ttk.Checkbutton(section, text="Pasa-altos 80 Hz (quita zumbidos y golpes)", variable=self.bool_vars["mic_highpass"],
                        command=self.apply_mic_filter).grid(row=0, column=0, columnspan=3, sticky="w")
        ttk.Checkbutton(section, text="Supresion de ruido de fondo", variable=self.bool_vars["mic_noise_suppression"],
                        command=self.apply_mic_filter).grid(row=1, column=0, columnspan=3, sticky="w", pady=(6, 0))
        self._slider(section, 2, "   Intensidad", self.mic_filter_vars["mic_suppression_strength"], 0, 1,
                     lambda value: f"{float(value) * 100:3.0f}%", self.apply_mic_filter)
        ttk.Checkbutton(section, text="Puerta de ruido", variable=self.bool_vars["mic_noise_gate"],
                        command=self.apply_mic_filter).grid(row=3, column=0, columnspan=3, sticky="w", pady=(6, 0))
        self._slider(section, 4, "   Umbral de voz", self.mic_filter_vars["mic_gate_threshold_db"], -80, -10,
                     lambda value: f"{float(value):4.0f} dB", self.apply_mic_filter)
        ttk.Checkbutton(section, text="Bajar musica y SFX al hablar (ducking)", variable=self.bool_vars["mic_ducking"],
                        command=self.apply_mic_filter).grid(row=5, column=0, columnspan=3, sticky="w", pady=(6, 0))
        self._slider(section, 6, "   Volumen al hablar", self.mic_filter_vars["mic_ducking_level"], 0, 1,
                     lambda value: f"{float(value) * 100:3.0f}%", self.apply_mic_filter)

        meter = tk.Frame(section, bg=uiTheme.CARD_ALT_BG, highlightthickness=1, highlightbackground=uiTheme.BORDER)
        meter.grid(row=7, column=0, columnspan=3, sticky="ew", pady=(10, 6))
        ttk.Label(meter, image=self.theme.image("icon_microphone_purple"), style="Meter.TLabel", background=uiTheme.CARD_ALT_BG).pack(side="left", padx=(8, 4), pady=6)
        ttk.Label(meter, textvariable=self.mic_level_text, style="Meter.TLabel", background=uiTheme.CARD_ALT_BG,
                  wraplength=self.theme.px(430), justify="left").pack(side="left", pady=6)

        ttk.Label(
            section,
            text=(
                "En Discord > Voz y video: entrada = CABLE Output y desactiva Supresion de ruido, "
                "Cancelacion de eco y Control automatico de ganancia; asi la musica y los SFX no se cortan.\n"
                "El umbral decide cuando hay voz (para la puerta y el ducking): ponlo un poco por encima "
                "del nivel de salida en silencio."
            ),
            style="CardMuted.TLabel",
            wraplength=self.theme.px(470),
            justify="left",
        ).grid(row=8, column=0, columnspan=3, sticky="w")

    def _collect_mic_filter_settings(self) -> dict[str, Any]:
        return {
            "mic_noise_suppression": bool(self.bool_vars["mic_noise_suppression"].get()),
            "mic_suppression_strength": float(self.mic_filter_vars["mic_suppression_strength"].get()),
            "mic_noise_gate": bool(self.bool_vars["mic_noise_gate"].get()),
            "mic_gate_threshold_db": float(self.mic_filter_vars["mic_gate_threshold_db"].get()),
            "mic_highpass": bool(self.bool_vars["mic_highpass"].get()),
            "mic_ducking": bool(self.bool_vars["mic_ducking"].get()),
            "mic_ducking_level": float(self.mic_filter_vars["mic_ducking_level"].get()),
        }

    def apply_mic_filter(self) -> None:
        # Se aplica en vivo sobre el stream del microfono, sin reabrir dispositivos.
        self.config_manager.updateConfig(self._collect_mic_filter_settings())
        self.audio_motor.update_mic_filter(self.config_manager.as_dict())

    def _sync_mic_levels(self) -> None:
        levels = self.audio_motor.get_mic_levels()
        if levels is None:
            self.mic_level_text.set("Microfono inactivo.")
            return
        gate = "abierta" if levels["gate_open"] else "cerrada"
        voice = "voz" if levels["voice"] else "silencio"
        self.mic_level_text.set(
            f"Entrada {levels['input_db']:6.1f} dB  ->  salida {levels['output_db']:6.1f} dB   |   puerta {gate}"
            f"   |   {voice}, musica/SFX al {levels['duck_gain'] * 100:3.0f}%"
        )

    def _build_engine_tab(self, parent: ttk.Notebook) -> ttk.Frame:
        page = ScrollableFrame(parent)
        inner = ttk.Frame(page.inner, padding=(16, 14, 16, 8))
        inner.pack(fill="both", expand=True)
        self._page_heading(inner, "Motor de audio", "Microfono, musica y SFX se mezclan hacia el cable virtual que Discord usa como microfono.")

        flow = ttk.LabelFrame(inner, text=" Como viaja el audio ", padding=12)
        flow.pack(fill="x")
        for step in (
            "Musica y SFX (pygame)  ->  salida objetivo (CABLE Input)",
            "Microfono  ->  filtro nativo (pasa-altos, supresion, puerta)  ->  CABLE Input",
            "Al hablar, la musica y los SFX bajan solos (ducking)",
            "Discord escucha CABLE Output como si fuera un microfono",
            "Los sonidos de esta interfaz salen por tus altavoces, nunca por el cable",
        ):
            ttk.Label(flow, text=f"-  {step}", style="Card.TLabel").pack(anchor="w", pady=2)

        ttk.Button(inner, text=" Ver creditos de recursos", image=self.theme.image("icon_heart_dark"), compound="left",
                   style="Light.TButton", command=lambda: self._open_help("credits")).pack(anchor="w", pady=(12, 0))
        return page

    def _build_help_tab(self, parent: ttk.Notebook) -> ttk.Frame:
        page = ttk.Frame(parent, padding=(16, 14, 16, 8))
        self._page_heading(page, "Ayuda", "Manual de uso, creditos de los recursos y registro de errores.")

        body = ttk.Frame(page)
        body.pack(fill="both", expand=True)
        nav = ttk.Frame(body)
        nav.pack(side="left", fill="y", padx=(0, 12))
        holder = uiTheme.card(body)
        holder.pack(side="left", fill="both", expand=True)

        self.help_toolbar = ttk.Frame(holder, style="Card.TFrame", padding=(12, 8, 12, 0))
        images = self.theme.images
        for text, icon, command in (
            (" Actualizar", "icon_arrow-path_dark", lambda: self._show_help("log")),
            (" Copiar", "icon_clipboard-document_dark", self._copy_log),
            (" Abrir carpeta", "icon_folder-open_dark", self._open_log_folder),
            (" Limpiar", "icon_trash_dark", self._clear_log),
        ):
            ttk.Button(self.help_toolbar, text=text, image=images[icon], compound="left", style="Light.TButton",
                       command=command).pack(side="left", padx=(0, 6))
        self.help_view = DocumentView(holder, self.theme)
        self.help_view.pack(fill="both", expand=True)

        self.help_buttons: dict[str, ttk.Button] = {}
        for key, text, icon in (
            ("manual", " Manual de uso", "book-open"),
            ("credits", " Creditos", "heart"),
            ("log", " Log de errores", "exclamation-triangle"),
        ):
            button = ttk.Button(nav, text=text, compound="left", command=lambda current=key: self._show_help(current))
            button.pack(fill="x", pady=(0, 8))
            self.help_buttons[key] = button
        self._help_icons = {"manual": "book-open", "credits": "heart", "log": "exclamation-triangle"}
        self._show_help("manual")
        return page

    def _show_help(self, key: str) -> None:
        for name, button in self.help_buttons.items():
            selected = name == key
            icon = self._help_icons[name]
            button.configure(style="Purple.TButton" if selected else "Light.TButton",
                             image=self.theme.image(f"icon_{icon}_{'white' if selected else 'dark'}"))
        if key == "log":
            self.help_toolbar.pack(fill="x", before=self.help_view)
            self.help_view.show_log(errorLog.read_log(), errorLog.log_path())
            return
        self.help_toolbar.pack_forget()
        filename = "manual.md" if key == "manual" else "creditos.md"
        try:
            content = (DOCS_DIR / filename).read_text(encoding="utf-8")
        except OSError as error:
            log.error("No se pudo abrir %s: %s", filename, error)
            content = f"# No se encontro {filename}\n\nReinstala la aplicacion."
        self.help_view.show_markdown(content)

    def _copy_log(self) -> None:
        self.root.clipboard_clear()
        self.root.clipboard_append(errorLog.read_log())
        self.ui_sounds.play("confirm")
        self._set_status("Log copiado al portapapeles.")

    def _open_log_folder(self) -> None:
        path = errorLog.log_path()
        if path is not None:
            os.startfile(path.parent)

    def _clear_log(self) -> None:
        if messagebox.askyesno("Log de errores", "Se borrara el registro de errores. Continuar?", parent=self.root):
            errorLog.clear_log()
            self._show_help("log")
            self._set_status("Log de errores limpiado.")

    def _report_tk_error(self, exc_type, exc_value, exc_traceback) -> None:
        log.error("Error en la interfaz", exc_info=(exc_type, exc_value, exc_traceback))
        self.ui_sounds.play("error")
        self._set_status("Ocurrio un error inesperado. Detalles en Ayuda > Log de errores.")

    def _open_help(self, key: str) -> None:
        self.notebook.select(self.help_tab)
        self._show_help(key)

    def _set_status(self, message: str) -> None:
        self.status_text.set(message)

    def refresh_library(self) -> None:
        self.music_assets = scan_local_assets(MUSIC_DIR, "music")
        self.sfx_assets = scan_local_assets(SFX_DIR, "sfx")
        self.music_panel.set_assets(self.music_assets)
        self.sfx_panel.set_assets(self.sfx_assets)
        self._set_status(f"Biblioteca actualizada: {len(self.music_assets)} pistas de musica y {len(self.sfx_assets)} SFX.")

    def _load_device_lists(self) -> None:
        try:
            devices = sd.query_devices()
            hostapis = sd.query_hostapis()
        except Exception as error:
            self.output_device_combo["values"] = []
            self.mic_device_combo["values"] = []
            self.monitor_device_combo["values"] = []
            self._set_status(f"No se pudo leer la lista de dispositivos de audio: {error}")
            return

        input_devices = [
            format_device_label(index, device, str(hostapis[int(device.get("hostapi", 0))].get("name", "")) if hostapis else None)
            for index, device in enumerate(devices)
            if device.get("max_input_channels", 0) > 0
        ]
        output_devices = [
            format_device_label(index, device, str(hostapis[int(device.get("hostapi", 0))].get("name", "")) if hostapis else None)
            for index, device in enumerate(devices)
            if device.get("max_output_channels", 0) > 0
        ]

        self.output_device_combo["values"] = output_devices
        self.mic_device_combo["values"] = input_devices
        self.monitor_device_combo["values"] = output_devices

        def pick_label(current_value: str, choices: list[str], kind: str) -> str | None:
            if not current_value:
                return None
            if current_value in choices:
                return current_value
            # El indice guardado puede haber cambiado: se busca por nombre y host API.
            index = resolve_device(current_value, kind)
            if index is None:
                return None
            return next((choice for choice in choices if choice.startswith(f"[{index}] ")), None)

        self.device_warnings = []
        selections = (
            ("output_device", output_devices, "output", "salida objetivo"),
            ("mic_input_device", input_devices, "input", "microfono"),
            ("monitor_output_device", output_devices, "output", "salida monitor"),
        )
        for key, choices, kind, label in selections:
            saved_value = self.device_vars[key].get()
            picked = pick_label(saved_value, choices, kind)
            if picked is not None:
                self.device_vars[key].set(picked)
            elif saved_value:
                # No se elige otro dispositivo a ciegas: podria mandar la voz a los altavoces.
                self.device_warnings.append(f"No se encontro la {label} guardada ({saved_value}); eligela de nuevo en Config.")
                self.device_vars[key].set("")
            elif choices and key != "monitor_output_device":
                self.device_vars[key].set(choices[0])

        if not self.device_vars["monitor_output_device"].get():
            self.device_vars["monitor_output_device"].set(self.device_vars["output_device"].get())

    def _cable_output_label(self) -> str | None:
        if not self.cable_status.installed or self.cable_status.playback_index is None:
            return None
        prefix = f"[{self.cable_status.playback_index}] "
        return next((label for label in self.output_device_combo["values"] if label.startswith(prefix)), None)

    def _autoselect_virtual_cable(self) -> None:
        # Solo si no hay salida elegida: nunca se pisa una eleccion del usuario.
        self.cable_status = detect_virtual_cable()
        current = self.device_vars["output_device"].get()
        saved_output_missing = any("salida objetivo" in warning for warning in self.device_warnings)
        nothing_saved = not self.config_manager.output_device()
        if is_virtual_cable_name(current) or (current and not saved_output_missing and not nothing_saved):
            return
        label = self._cable_output_label()
        if label is not None:
            self.device_vars["output_device"].set(label)
            self.device_warnings = [warning for warning in self.device_warnings if "salida objetivo" not in warning]

    def _update_cable_status(self) -> None:
        status = self.cable_status
        text = status.summary
        color = uiTheme.OK_GREEN
        use_enabled = False
        if not status.installed:
            color = uiTheme.ERROR_RED
        elif not is_virtual_cable_name(self.device_vars["output_device"].get()):
            text += " La salida objetivo NO es el cable: Discord no escuchara la mezcla."
            color = uiTheme.WARN
            use_enabled = True
        self.cable_status_text.set(text)
        self.cable_status_label.configure(foreground=color)
        self.use_cable_button.state(["!disabled"] if use_enabled else ["disabled"])

    def use_virtual_cable(self) -> None:
        label = self._cable_output_label()
        if label is None:
            self.show_cable_install_guide()
            return
        self.device_vars["output_device"].set(label)
        self.apply_settings(persist=False)
        self._update_cable_status()
        self._set_status(f"Salida objetivo: {label}. En Discord elige '{self.cable_status.discord_input_name}' como microfono.")

    def rescan_audio_devices(self) -> None:
        mic_was_active = self.audio_motor.release_portaudio_streams()
        try:
            rescan_devices()
        except Exception as error:
            self._set_status(f"No se pudo re-escanear los dispositivos: {error}")
        was_installed = self.cable_status.installed
        self._load_device_lists()
        self._autoselect_virtual_cable()
        if self.cable_status.installed and not was_installed:
            # Lo acaba de instalar justamente para esto: se usa sin preguntar.
            label = self._cable_output_label()
            if label is not None:
                self.device_vars["output_device"].set(label)
        if mic_was_active:
            self.bool_vars["mic_active"].set(True)
        self.apply_settings(persist=False)
        self._update_cable_status()
        if self.cable_status.installed and not was_installed:
            self.ui_sounds.play("notify")
            messagebox.showinfo(
                "Cable virtual detectado",
                f"Se detecto '{self.cable_status.playback_name}'.\n\n"
                f"En Discord > Voz y video, elige '{self.cable_status.discord_input_name}' como dispositivo de entrada.",
            )
        elif not self.cable_status.installed:
            self.ui_sounds.play("error")
            self._set_status("El cable virtual sigue sin aparecer. Si recien lo instalaste, reinicia Windows.")

    def _check_virtual_cable_on_startup(self) -> None:
        if not self.cable_status.installed and not self.config_manager.as_dict().get("skip_cable_check", False):
            self.show_cable_install_guide()

    def show_cable_install_guide(self) -> None:
        self.ui_sounds.play("open")
        dialog = tk.Toplevel(self.root)
        dialog.title("Cable de audio virtual")
        dialog.configure(bg=uiTheme.PAGE_BG)
        dialog.resizable(False, False)
        dialog.transient(self.root)

        holder = uiTheme.card(dialog)
        holder.pack(fill="both", expand=True, padx=12, pady=12)
        body = ttk.Frame(holder, style="Card.TFrame", padding=(20, 16))
        body.pack(fill="both", expand=True)
        heading = ttk.Frame(body, style="Card.TFrame")
        heading.pack(fill="x")
        ttk.Label(heading, image=self.theme.image("logo"), style="Card.TLabel").pack(side="left")
        ttk.Label(heading, text="Discord necesita un microfono virtual", style="CardBold.TLabel",
                  font=(uiTheme.FONT, 14, "bold")).pack(side="left", padx=10)
        ttk.Label(
            body,
            text=(
                "Windows no permite que una app normal cree un microfono; para eso se usa VB-Cable,\n"
                "un driver gratuito. La app manda la mezcla a 'CABLE Input' y Discord la escucha\n"
                "desde 'CABLE Output'.\n\n"
                "1. Abre la pagina de descarga y baja el paquete para Windows (ZIP).\n"
                "2. Extrae el ZIP y ejecuta VBCABLE_Setup_x64.exe como administrador > Install Driver.\n"
                "3. Reinicia Windows (el driver suele aparecer recien despues de reiniciar).\n"
                "4. Vuelve aqui y pulsa 'Buscar de nuevo'.\n"
                "5. En Discord > Voz y video: entrada = CABLE Output; desactiva Supresion de ruido,\n"
                "    Cancelacion de eco y Control automatico de ganancia (usa el filtro de esta app)."
            ),
            style="Card.TLabel",
            justify="left",
        ).pack(anchor="w", pady=(10, 12))

        skip_var = tk.BooleanVar(value=bool(self.config_manager.as_dict().get("skip_cable_check", False)))
        ttk.Checkbutton(body, text="No volver a mostrar al iniciar", variable=skip_var).pack(anchor="w")

        def close() -> None:
            self.ui_sounds.play("close")
            self.config_manager.updateConfig({"skip_cable_check": bool(skip_var.get())})
            self.config_manager.saveConfig()
            dialog.destroy()

        def rescan() -> None:
            close()
            self.rescan_audio_devices()

        buttons = ttk.Frame(body, style="Card.TFrame", padding=(0, 14, 0, 0))
        buttons.pack(fill="x")
        ttk.Button(buttons, text=" Abrir pagina de descarga", image=self.theme.image("icon_arrow-top-right-on-square_white"),
                   compound="left", style="Blue.TButton", command=lambda: webbrowser.open(VB_CABLE_DOWNLOAD_URL)).pack(side="left")
        ttk.Button(buttons, text="Buscar de nuevo", style="Light.TButton", command=rescan).pack(side="left", padx=8)
        ttk.Button(buttons, text="Cerrar", style="Light.TButton", command=close).pack(side="right")
        dialog.protocol("WM_DELETE_WINDOW", close)
        center_over(dialog, self.root)

    def _collect_settings(self) -> dict[str, Any]:
        return {
            "volume_music": float(self.volume_vars["volume_music"].get()),
            "volume_sfx": float(self.volume_vars["volume_sfx"].get()),
            "volume_mic": float(self.volume_vars["volume_mic"].get()),
            "mic_active": bool(self.bool_vars["mic_active"].get()),
            "loop_music": bool(self.bool_vars["loop_music"].get()),
            "loop_sfx": bool(self.bool_vars["loop_sfx"].get()),
            "monitor_audio": bool(self.bool_vars["monitor_audio"].get()),
            "output_device": self.device_vars["output_device"].get() or None,
            "default_output_device": self.device_vars["output_device"].get() or None,
            "mic_input_device": self.device_vars["mic_input_device"].get() or None,
            "monitor_output_device": self.device_vars["monitor_output_device"].get() or None,
            "hotkeys": self._collect_hotkeys(),
            "ui_sounds": bool(self.bool_vars["ui_sounds"].get()),
            **self._collect_mic_filter_settings(),
        }

    def _collect_hotkeys(self) -> dict[str, str]:
        return {action: self.hotkey_vars[action].get().strip() for action in HOTKEY_ACTIONS}

    def capture_hotkey(self, action: str) -> None:
        label, _description = HOTKEY_ACTIONS[action]
        # Sin esto, una combinacion ya registrada la consume Windows y el dialogo no la ve.
        self.hotkey_manager.stop()
        dialog = HotkeyCaptureDialog(self.root, label, self.hotkey_vars[action].get())
        self.root.wait_window(dialog)
        if dialog.result:
            self.hotkey_vars[action].set(dialog.result)
            self.apply_hotkeys()
            self.ui_sounds.play("confirm")
            self._set_status(f"Hotkey capturada para {label}: {dialog.result}")
        else:
            self._register_hotkeys()

    def apply_settings(self, persist: bool = False) -> None:
        settings = self._collect_settings()
        self.config_manager.updateConfig(settings)
        self.audio_motor.apply_config(self.config_manager.as_dict())
        self._register_hotkeys()
        self._sync_music_progress()
        warnings = list(self.device_warnings)
        for warning in warnings:
            log.warning(warning)
        if self.audio_motor.playback_warning:
            warnings.append(self.audio_motor.playback_warning)
            log.warning(self.audio_motor.playback_warning)
        if self.bool_vars["mic_active"].get() and not self.audio_motor.mic_active:
            self.bool_vars["mic_active"].set(False)
            warnings.append(f"No se pudo iniciar el microfono: {self.audio_motor.mic_error}")
            self.ui_sounds.play("error")
        if persist:
            self.config_manager.saveConfig()
            message = "Ajustes guardados y aplicados."
        else:
            message = "Ajustes aplicados al motor de audio."
        self._set_status(" | ".join([message, *warnings]) if warnings else message)
        if hasattr(self, "cable_status_label"):
            self._update_cable_status()

    def save_settings(self) -> None:
        self.apply_settings(persist=True)
        self.ui_sounds.play("confirm")

    def apply_hotkeys(self) -> None:
        self.config_manager.updateConfig({"hotkeys": self._collect_hotkeys()})
        self._register_hotkeys()
        self._set_status("Hotkeys globales actualizadas.")

    def reset_hotkeys(self) -> None:
        defaults = default_hotkeys()
        for action, spec in defaults.items():
            self.hotkey_vars[action].set(spec)
        self.apply_hotkeys()

    def _register_hotkeys(self) -> None:
        errors = self.hotkey_manager.set_hotkeys(self._collect_hotkeys())
        if errors:
            messagebox.showwarning("Hotkeys", "\n".join(errors))

    def _queue_hotkey_action(self, action: str) -> None:
        self.hotkey_queue.put(action)

    def _poll_hotkey_events(self) -> None:
        for action in self.hotkey_manager.poll():
            self._handle_hotkey_action(action)
        self.root.after(100, self._poll_hotkey_events)

    def _tick_audio(self) -> None:
        self.audio_motor.tick()
        self.root.after(50, self._tick_audio)

    def _poll_playback_state(self) -> None:
        self._sync_music_progress()
        self._sync_mic_levels()
        self._sync_mic_indicator()
        self.root.after(150, self._poll_playback_state)

    def _sync_music_progress(self) -> None:
        state = self.audio_motor.get_music_state()
        source = state.get("source")
        asset = None
        if source is not None:
            source_path = Path(source)
            for current_asset in self.music_assets:
                if current_asset.path == source_path:
                    asset = current_asset
                    break

        self.music_panel.update_progress(
            asset,
            float(state.get("position_seconds", 0.0)),
            float(state.get("duration_seconds", 0.0)),
            bool(state.get("is_playing", False)),
            bool(state.get("is_paused", False)),
        )

    def seek_music_asset(self, asset: AudioAsset, percentage: float) -> None:
        duration = float(self.audio_motor.get_music_state().get("duration_seconds", 0.0))
        if duration <= 0:
            self.play_music_asset(asset)
            return

        target_seconds = max(0.0, min(duration, (percentage / 100.0) * duration))
        self.active_music_asset = asset
        self.play_music_asset(asset, start_seconds=target_seconds)
        self._set_status(f"Buscando en musica: {asset.path.name} al {int(percentage)}%")

    def _handle_hotkey_action(self, action: str) -> None:
        handlers: dict[str, Callable[[], None]] = {
            "refresh_library": self.refresh_library,
            "toggle_mic": self.toggle_microphone,
            "play_music": self.play_selected_music,
            "stop_music": self.stop_music,
            "pause_music": self.toggle_music_pause,
            "play_sfx": self.play_selected_sfx,
            "stop_sfx": self.stop_sfx,
            "pause_sfx": self.toggle_sfx_pause,
            "save_settings": self.save_settings,
        }
        handler = handlers.get(action)
        if handler is not None:
            handler()

    def _fill_listbox(self, listbox: tk.Listbox, assets: list[AudioAsset], folder: Path) -> None:
        listbox.delete(0, tk.END)
        if not assets:
            listbox.insert(tk.END, f"No hay archivos mp3 en {folder.name}.")
            return

        for asset in assets:
            listbox.insert(tk.END, asset.path.name)

    def _pick_file_and_copy(self, category: str) -> None:
        target_folder = MUSIC_DIR if category == "music" else SFX_DIR
        selected_path = filedialog.askopenfilename(
            title=f"Seleccionar archivo para {category}",
            filetypes=[("Audio MP3", "*.mp3"), ("Todos los archivos", "*.*")],
        )
        if not selected_path:
            return

        source = Path(selected_path)
        destination = target_folder / source.name
        try:
            if source.resolve() != destination.resolve():
                shutil.copy2(source, destination)
        except OSError as error:
            self.ui_sounds.play("error")
            messagebox.showerror("Error", f"No se pudo copiar el archivo:\n{error}")
            return

        self.ui_sounds.play("confirm")
        self.refresh_library()
        self._set_status(f"Archivo agregado a {category}: {destination.name}")

    def _first_asset(self, assets: list[AudioAsset]) -> AudioAsset | None:
        return assets[0] if assets else None

    def play_music_asset(self, asset: AudioAsset, start_seconds: float = 0.0) -> None:
        self.active_music_asset = asset
        self.audio_motor.play_music(asset.path, start_seconds=start_seconds)
        self._set_status(f"Reproduciendo musica: {asset.path.name}")

    def play_sfx_asset(self, asset: AudioAsset) -> None:
        self.active_sfx_asset = asset
        self.audio_motor.play_sfx(asset.path)
        self._set_status(f"Reproduciendo SFX: {asset.path.name}")

    def play_selected_music(self) -> None:
        asset = self.active_music_asset or self._first_asset(self.music_assets)
        if asset is None:
            self._set_status("No hay musica local para reproducir.")
            return
        self.play_music_asset(asset)

    def play_selected_sfx(self) -> None:
        asset = self.active_sfx_asset or self._first_asset(self.sfx_assets)
        if asset is None:
            self._set_status("No hay SFX local para reproducir.")
            return
        self.play_sfx_asset(asset)

    def stop_music(self) -> None:
        self.audio_motor.stop_music()
        self._set_status("Musica detenida.")

    def toggle_music_pause(self) -> None:
        self.audio_motor.toggle_music_pause()
        self._set_status("Musica en pausa." if self.audio_motor.music.is_paused() else "Musica reanudada.")

    def stop_sfx(self) -> None:
        self.audio_motor.stop_sfx()
        self._set_status("SFX detenido.")

    def toggle_sfx_pause(self) -> None:
        self.audio_motor.toggle_sfx_pause()
        self._set_status("SFX en pausa." if self.audio_motor.sfx.is_paused() else "SFX reanudado.")

    def toggle_microphone(self) -> None:
        new_state = not bool(self.bool_vars["mic_active"].get())
        self.bool_vars["mic_active"].set(new_state)
        self.apply_settings(persist=False)
        if new_state and not self.audio_motor.mic_active:
            return  # apply_settings ya mostro el error y desmarco la casilla.
        self.ui_sounds.play("toggle_on" if new_state else "toggle_off")
        self._set_status("Microfono activado." if new_state else "Microfono desactivado.")

    def _show_engine_placeholder(self) -> None:
        messagebox.showinfo(
            "Motor base",
            "La interfaz esta lista. El siguiente paso es integrar el motor de mezcla,\n"
            "la salida virtual y la gestion simultanea de canales.",
        )

    def _on_close(self) -> None:
        try:
            self.hotkey_manager.stop()
            self.audio_motor.shutdown()
        finally:
            self.root.destroy()


def main() -> None:
    ensure_project_directories()
    errorLog.setup_logging(LOG_DIR)
    log.info("Inicio de %s v%s (%s)", APP_NAME, APP_VERSION, "instalado" if FROZEN else "desarrollo")
    # Sin esto la barra de tareas agrupa la ventana bajo el icono de Python.
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("Astrados.AudioHandler")
    except (AttributeError, OSError):
        pass
    uiTheme.enable_dpi_awareness()
    root = tk.Tk()
    AudioHandlerApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()