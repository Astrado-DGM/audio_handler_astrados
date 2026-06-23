"""Interfaz principal de Audio Handler PV.

La app organiza musica, SFX y microfono en una sola salida local y permite:
- reproducir pistas individuales desde botones Play
- configurar hotkeys globales editables desde la interfaz
- persistir configuracion local en data/config
"""

from __future__ import annotations

import ctypes
import queue
import re
import shutil
import threading
from dataclasses import dataclass
from ctypes import wintypes
from pathlib import Path
from typing import Any, Callable

import sounddevice as sd
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from logic.congifHandler import configHandler
from logic.soundMotor import soundMotor


APP_TITLE = "Audio Handler PV"
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
MUSIC_DIR = DATA_DIR / "music"
SFX_DIR = DATA_DIR / "sfx"
RECORDINGS_DIR = DATA_DIR / "recordings"
CONFIG_DIR = DATA_DIR / "config"
TRACK_EXTENSIONS = {".mp3"}

WM_HOTKEY = 0x0312
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
    return {
        "refresh_library": "Ctrl+R",
        "toggle_mic": "Ctrl+M",
        "play_music": "Ctrl+Shift+M",
        "stop_music": "Ctrl+Alt+M",
        "pause_music": "Ctrl+P",
        "play_sfx": "Ctrl+E",
        "stop_sfx": "Ctrl+Alt+E",
        "pause_sfx": "Ctrl+Shift+E",
        "save_settings": "Ctrl+S",
    }


@dataclass(frozen=True)
class AudioAsset:
    category: str
    path: Path

    @property
    def name(self) -> str:
        return self.path.stem


def ensure_project_directories() -> None:
    for folder in (DATA_DIR, MUSIC_DIR, SFX_DIR, RECORDINGS_DIR, CONFIG_DIR):
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


class HotkeyCaptureDialog(tk.Toplevel):
    def __init__(self, parent: tk.Misc, label: str, current_value: str) -> None:
        super().__init__(parent)
        self.result: str | None = None
        self.configure(bg="#111318")
        self.title(f"Capturar {label}")
        self.resizable(False, False)
        self.transient(parent.winfo_toplevel())
        self.grab_set()

        self.status_var = tk.StringVar(value=f"Actual: {format_hotkey_spec(current_value)}")

        container = tk.Frame(self, bg="#111318", padx=18, pady=18)
        container.pack(fill="both", expand=True)
        tk.Label(container, text=f"Pulsa la combinacion para {label}", bg="#111318", fg="#f4f7fb", font=("Segoe UI", 12, "bold")).pack(anchor="w")
        tk.Label(container, text="Se guardara al presionar una tecla valida. Escape cancela.", bg="#111318", fg="#b9c1cc").pack(anchor="w", pady=(4, 12))
        tk.Label(container, textvariable=self.status_var, bg="#111318", fg="#7dd3fc").pack(anchor="w", pady=(0, 12))
        ttk.Button(container, text="Cancelar", command=self._cancel).pack(anchor="e")

        self.bind("<KeyPress>", self._on_key_press)
        self.bind("<Escape>", self._cancel)
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


class GlobalHotkeyManager:
    def __init__(self, dispatch: Callable[[str], None]) -> None:
        self._dispatch = dispatch
        self._queue: queue.Queue[str] = queue.Queue()
        self._registered: dict[int, str] = {}
        self._thread: threading.Thread | None = None
        self._thread_id: int | None = None
        self._running = False
        self._next_id = 1

    def set_hotkeys(self, hotkeys: dict[str, str]) -> list[str]:
        self.stop()
        errors: list[str] = []
        self._registered = {}
        self._next_id = 1

        for action, spec in hotkeys.items():
            parsed = parse_hotkey_spec(spec)
            if parsed is None:
                if spec.strip():
                    errors.append(f"{action}: hotkey invalido ({spec})")
                continue

            modifiers, vk_code = parsed
            hotkey_id = self._next_id
            self._next_id += 1

            if not ctypes.windll.user32.RegisterHotKey(None, hotkey_id, modifiers | MOD_NOREPEAT, vk_code):
                errors.append(f"{action}: no se pudo registrar {spec}")
                continue

            self._registered[hotkey_id] = action

        if self._registered:
            self._start_thread()

        return errors

    def _start_thread(self) -> None:
        if self._running:
            return

        self._running = True
        self._thread = threading.Thread(target=self._message_loop, daemon=True)
        self._thread.start()

    def _message_loop(self) -> None:
        self._thread_id = ctypes.windll.kernel32.GetCurrentThreadId()
        msg = wintypes.MSG()
        while self._running:
            result = ctypes.windll.user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if result <= 0:
                break
            if msg.message == WM_HOTKEY:
                action = self._registered.get(int(msg.wParam))
                if action:
                    self._queue.put(action)

    def poll(self) -> list[str]:
        actions: list[str] = []
        while True:
            try:
                actions.append(self._queue.get_nowait())
            except queue.Empty:
                break
        return actions

    def stop(self) -> None:
        for hotkey_id in list(self._registered):
            ctypes.windll.user32.UnregisterHotKey(None, hotkey_id)

        self._registered.clear()
        self._running = False
        if self._thread_id is not None:
            ctypes.windll.user32.PostThreadMessageW(self._thread_id, 0x0012, 0, 0)
            self._thread_id = None

        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=0.2)
        self._thread = None


class AudioLibraryPanel(tk.Frame):
    def __init__(self, parent: tk.Widget, title: str, description: str, show_progress: bool = False) -> None:
        super().__init__(parent, bg="#171a20")
        self.title = title
        self.description = description
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
        tk.Label(self, text=self.title, font=("Segoe UI", 16, "bold"), bg="#171a20", fg="#f4f7fb").pack(anchor="w", padx=16, pady=(16, 4))
        tk.Label(self, text=self.description, font=("Segoe UI", 10), bg="#171a20", fg="#b9c1cc").pack(anchor="w", padx=16)

        search_bar = tk.Frame(self, bg="#171a20")
        search_bar.pack(fill="x", padx=16, pady=(12, 8))
        tk.Label(search_bar, text="Filtro:", bg="#171a20", fg="#dbe2ee").pack(side="left")
        search_entry = ttk.Entry(search_bar, textvariable=self.search_var)
        search_entry.pack(side="left", fill="x", expand=True, padx=8)
        self.search_var.trace_add("write", lambda *_: self._render())

        outer = tk.Frame(self, bg="#171a20")
        outer.pack(fill="both", expand=True, padx=16, pady=(0, 16))

        self.canvas = tk.Canvas(outer, bg="#171a20", highlightthickness=0)
        scrollbar = ttk.Scrollbar(outer, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=scrollbar.set)

        scrollbar.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)

        self.content = tk.Frame(self.canvas, bg="#171a20")
        self.content_id = self.canvas.create_window((0, 0), window=self.content, anchor="nw")
        self.content.bind("<Configure>", self._sync_scrollregion)
        self.canvas.bind("<Configure>", self._resize_content)

    def _sync_scrollregion(self, _event: tk.Event) -> None:
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _resize_content(self, event: tk.Event) -> None:
        self.canvas.itemconfigure(self.content_id, width=event.width)

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
        if not self.show_progress:
            return

        active_path = asset.path if asset is not None else None
        for path, state in self._row_state.items():
            progress_var = state.get("progress_var")
            time_var = state.get("time_var")
            scale = state.get("scale")
            if progress_var is None or time_var is None or scale is None:
                continue

            if active_path is None or path != active_path or duration_seconds <= 0:
                progress_var.set(0.0)
                time_var.set("0:00 / 0:00")
                continue

            percentage = max(0.0, min(100.0, (current_seconds / duration_seconds) * 100.0))
            progress_var.set(percentage)
            time_var.set(f"{self._format_time(current_seconds)} / {self._format_time(duration_seconds)}")
            scale.state(["!disabled"] if is_playing or is_paused else ["disabled"])

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
            tk.Label(self.content, text="No hay archivos locales para mostrar.", bg="#171a20", fg="#b9c1cc", pady=14).pack(anchor="w")
            return

        for asset in filtered_assets:
            row = tk.Frame(self.content, bg="#111318")
            row.pack(fill="x", pady=6)
            row.columnconfigure(0, weight=0)
            row.columnconfigure(1, weight=1)
            row.columnconfigure(2, weight=0)

            text_frame = tk.Frame(row, bg="#111318")
            text_frame.grid(row=0, column=0, sticky="ew", padx=(12, 8), pady=10)
            tk.Label(text_frame, text=asset.path.name, bg="#111318", fg="#f4f7fb", anchor="w").pack(anchor="w")
            tk.Label(text_frame, text=asset.path.parent.name, bg="#111318", fg="#94a3b8", anchor="w", font=("Segoe UI", 9)).pack(anchor="w")

            progress_frame = tk.Frame(row, bg="#111318")
            progress_frame.grid(row=0, column=1, sticky="ew", padx=8, pady=10)
            progress_frame.columnconfigure(0, weight=1)

            progress_var = tk.DoubleVar(value=0.0)
            time_var = tk.StringVar(value="0:00 / 0:00")
            scale: ttk.Scale | None = None
            if self.show_progress:
                scale = ttk.Scale(progress_frame, from_=0, to=100, orient="horizontal", variable=progress_var)
                scale.grid(row=0, column=0, sticky="ew")
                scale.bind("<ButtonRelease-1>", lambda event, current_asset=asset, current_var=progress_var: self._seek(current_asset, float(current_var.get())))
                tk.Label(progress_frame, textvariable=time_var, bg="#111318", fg="#94a3b8", font=("Segoe UI", 8)).grid(row=1, column=0, sticky="w", pady=(4, 0))
            else:
                progress_frame.grid_remove()

            action_frame = tk.Frame(row, bg="#111318")
            action_frame.grid(row=0, column=2, sticky="e", padx=(8, 12), pady=10)

            ttk.Button(action_frame, text="Play", command=lambda current_asset=asset: self._play(current_asset), width=8).pack(side="left", padx=(0, 6))
            ttk.Button(action_frame, text="Pause", command=self._pause, width=8).pack(side="left", padx=(0, 6))
            ttk.Button(action_frame, text="Stop", command=self._stop, width=8).pack(side="left")

            self._row_state[asset.path] = {
                "progress_var": progress_var,
                "time_var": time_var,
                "scale": scale,
            }

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
        self.root.title(APP_TITLE)
        self.root.geometry("1200x800")
        self.root.minsize(1000, 680)

        self.status_text = tk.StringVar(value="Listo para configurar la biblioteca local.")
        self.music_assets: list[AudioAsset] = []
        self.sfx_assets: list[AudioAsset] = []
        self.active_music_asset: AudioAsset | None = None
        self.active_sfx_asset: AudioAsset | None = None

        self.config_manager = configHandler(CONFIG_DIR)
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
        }
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
        self._bind_shortcuts()
        self.refresh_library()
        self._load_device_lists()
        self.apply_settings(persist=False)
        self._register_hotkeys()
        self.root.after(100, self._poll_hotkey_events)
        self.root.after(150, self._poll_playback_state)
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_ui(self) -> None:
        self.root.configure(bg="#111318")

        header = tk.Frame(self.root, bg="#111318")
        header.pack(fill="x", padx=16, pady=(16, 10))

        title = tk.Label(header, text="Audio Handler PV", font=("Segoe UI", 22, "bold"), fg="#f4f7fb", bg="#111318")
        title.pack(anchor="w")

        subtitle = tk.Label(
            header,
            text="Musica, SFX y microfono en una sola salida local, con controles visuales y hotkeys globales.",
            font=("Segoe UI", 10),
            fg="#b9c1cc",
            bg="#111318",
        )
        subtitle.pack(anchor="w", pady=(4, 0))

        action_bar = tk.Frame(self.root, bg="#111318")
        action_bar.pack(fill="x", padx=16, pady=(0, 10))

        ttk.Button(action_bar, text="Actualizar biblioteca", command=self.refresh_library).pack(side="left")
        ttk.Button(action_bar, text="Agregar musica", command=lambda: self._pick_file_and_copy("music")).pack(side="left", padx=8)
        ttk.Button(action_bar, text="Agregar efecto", command=lambda: self._pick_file_and_copy("sfx")).pack(side="left")
        ttk.Button(action_bar, text="Guardar ajustes", command=self.save_settings).pack(side="right")

        notebook = ttk.Notebook(self.root)
        notebook.pack(fill="both", expand=True, padx=16, pady=10)

        self.music_panel = AudioLibraryPanel(notebook, "Musica", "Pistas locales que suenan en loop de fondo", show_progress=True)
        self.sfx_panel = AudioLibraryPanel(notebook, "Efectos", "Archivos disparados por evento sin silenciar otras fuentes")
        self.hotkeys_tab = self._build_hotkeys_tab(notebook)
        self.config_tab = self._build_config_tab(notebook)
        self.engine_tab = self._build_engine_tab(notebook)

        notebook.add(self.music_panel, text="Musica")
        notebook.add(self.sfx_panel, text="Efectos")
        notebook.add(self.hotkeys_tab, text="Atajos")
        notebook.add(self.config_tab, text="Config")
        notebook.add(self.engine_tab, text="Motor")

        self.music_panel.set_action_handlers(self.play_music_asset, self.toggle_music_pause, self.stop_music)
        self.music_panel.set_seek_handler(self.seek_music_asset)
        self.sfx_panel.set_action_handlers(self.play_sfx_asset, self.toggle_sfx_pause, self.stop_sfx)

        status_bar = tk.Label(self.root, textvariable=self.status_text, anchor="w", bg="#1a1d24", fg="#dbe2ee", padx=12, pady=8)
        status_bar.pack(fill="x", side="bottom")

    def _build_hotkeys_tab(self, parent: ttk.Notebook) -> tk.Frame:
        frame = tk.Frame(parent, bg="#171a20")
        tk.Label(frame, text="Atajos globales", font=("Segoe UI", 16, "bold"), bg="#171a20", fg="#f4f7fb").pack(anchor="w", padx=16, pady=(16, 4))
        tk.Label(
            frame,
            text="Edita las combinaciones y pulsa Aplicar. Funcionan aun minimizado o sin foco en Windows.",
            font=("Segoe UI", 10),
            bg="#171a20",
            fg="#b9c1cc",
        ).pack(anchor="w", padx=16)

        body = tk.Frame(frame, bg="#111318")
        body.pack(fill="both", expand=True, padx=16, pady=16)
        body.columnconfigure(1, weight=1)

        row_index = 0
        for action, (label, description) in HOTKEY_ACTIONS.items():
            tk.Label(body, text=label, bg="#111318", fg="#f4f7fb", anchor="w").grid(row=row_index, column=0, sticky="w", padx=12, pady=(10, 4))
            tk.Label(body, text=description, bg="#111318", fg="#94a3b8", anchor="w").grid(row=row_index + 1, column=0, columnspan=2, sticky="w", padx=12, pady=(0, 4))
            entry = ttk.Entry(body, textvariable=self.hotkey_vars[action])
            entry.grid(row=row_index, column=1, sticky="ew", padx=12, pady=(10, 4))
            ttk.Button(body, text="Capturar", command=lambda current_action=action: self.capture_hotkey(current_action)).grid(row=row_index, column=2, sticky="e", padx=(0, 12), pady=(10, 4))
            row_index += 2

        button_row = tk.Frame(body, bg="#111318")
        button_row.grid(row=row_index, column=0, columnspan=2, sticky="ew", padx=12, pady=(18, 8))
        ttk.Button(button_row, text="Aplicar hotkeys", command=self.apply_hotkeys).pack(side="left")
        ttk.Button(button_row, text="Restaurar hotkeys", command=self.reset_hotkeys).pack(side="left", padx=8)
        ttk.Button(button_row, text="Guardar ajustes", command=self.save_settings).pack(side="right")

        return frame

    def _build_config_tab(self, parent: ttk.Notebook) -> tk.Frame:
        frame = tk.Frame(parent, bg="#171a20")
        tk.Label(frame, text="Configuracion de audio", font=("Segoe UI", 16, "bold"), bg="#171a20", fg="#f4f7fb").pack(anchor="w", padx=16, pady=(16, 4))
        tk.Label(frame, text="Ajusta volumenes, dispositivos y estado del motor local.", font=("Segoe UI", 10), bg="#171a20", fg="#b9c1cc").pack(anchor="w", padx=16)

        controls = tk.Frame(frame, bg="#111318")
        controls.pack(fill="both", expand=True, padx=16, pady=16)

        self._add_slider(controls, "Volumen musica", "volume_music", 0)
        self._add_slider(controls, "Volumen SFX", "volume_sfx", 1)
        self._add_slider(controls, "Volumen microfono", "volume_mic", 2)

        switch_row = tk.Frame(controls, bg="#111318")
        switch_row.grid(row=3, column=0, columnspan=2, sticky="ew", padx=12, pady=(8, 4))
        tk.Checkbutton(switch_row, text="Microfono activo", variable=self.bool_vars["mic_active"], bg="#111318", fg="#e5e7eb", selectcolor="#111318", activebackground="#111318", activeforeground="#e5e7eb").pack(anchor="w")
        tk.Checkbutton(switch_row, text="Loop musica", variable=self.bool_vars["loop_music"], bg="#111318", fg="#e5e7eb", selectcolor="#111318", activebackground="#111318", activeforeground="#e5e7eb").pack(anchor="w")
        tk.Checkbutton(switch_row, text="Loop SFX", variable=self.bool_vars["loop_sfx"], bg="#111318", fg="#e5e7eb", selectcolor="#111318", activebackground="#111318", activeforeground="#e5e7eb").pack(anchor="w")
        tk.Checkbutton(switch_row, text="Escucha local duplicada", variable=self.bool_vars["monitor_audio"], bg="#111318", fg="#e5e7eb", selectcolor="#111318", activebackground="#111318", activeforeground="#e5e7eb").pack(anchor="w")

        device_frame = tk.Frame(controls, bg="#111318")
        device_frame.grid(row=4, column=0, columnspan=2, sticky="ew", padx=12, pady=(10, 4))
        device_frame.columnconfigure(1, weight=1)
        device_frame.columnconfigure(3, weight=1)

        tk.Label(device_frame, text="Salida objetivo", bg="#111318", fg="#dbe2ee").grid(row=0, column=0, sticky="w", padx=(0, 8), pady=6)
        self.output_device_combo = ttk.Combobox(device_frame, textvariable=self.device_vars["output_device"], state="readonly")
        self.output_device_combo.grid(row=0, column=1, sticky="ew", pady=6)

        tk.Label(device_frame, text="Entrada microfono", bg="#111318", fg="#dbe2ee").grid(row=1, column=0, sticky="w", padx=(0, 8), pady=6)
        self.mic_device_combo = ttk.Combobox(device_frame, textvariable=self.device_vars["mic_input_device"], state="readonly")
        self.mic_device_combo.grid(row=1, column=1, sticky="ew", pady=6)

        tk.Label(device_frame, text="Salida monitor", bg="#111318", fg="#dbe2ee").grid(row=2, column=0, sticky="w", padx=(0, 8), pady=6)
        self.monitor_device_combo = ttk.Combobox(device_frame, textvariable=self.device_vars["monitor_output_device"], state="readonly")
        self.monitor_device_combo.grid(row=2, column=1, sticky="ew", pady=6)

        ttk.Button(device_frame, text="Aplicar ajustes", command=self.apply_settings).grid(row=0, column=2, rowspan=3, padx=16, sticky="ns")
        ttk.Button(device_frame, text="Guardar ajustes", command=self.save_settings).grid(row=0, column=3, rowspan=3, sticky="ns")

        info = tk.Label(
            controls,
            text="Los canales logicos son: microfono, musica y SFX. La mezcla local se apoya en el motor existente de pygame y sounddevice.",
            bg="#111318",
            fg="#b9c1cc",
            wraplength=900,
            justify="left",
        )
        info.grid(row=5, column=0, columnspan=2, sticky="w", padx=12, pady=(14, 8))
        return frame

    def _build_engine_tab(self, parent: ttk.Notebook) -> tk.Frame:
        frame = tk.Frame(parent, bg="#171a20")
        tk.Label(frame, text="Motor de audio", font=("Segoe UI", 16, "bold"), bg="#171a20", fg="#f4f7fb").pack(anchor="w", padx=16, pady=(16, 4))
        tk.Label(frame, text="Microfono, musica y SFX se encaminan a una sola salida local seleccionable.", font=("Segoe UI", 10), bg="#171a20", fg="#b9c1cc").pack(anchor="w", padx=16)

        text = (
            "Fase 1: lista de archivos locales\n"
            "Fase 2: Play de musica y SFX por boton\n"
            "Fase 3: hotkeys globales editables\n"
            "Fase 4: guardado local de configuracion\n"
            "Fase 5: pruebas de latencia y estabilidad"
        )
        tk.Label(frame, text=text, justify="left", bg="#111318", fg="#e5e7eb", font=("Consolas", 10), padx=16, pady=16).pack(fill="x", padx=16, pady=16)
        return frame

    def _add_slider(self, parent: tk.Widget, label: str, key: str, row: int) -> None:
        tk.Label(parent, text=label, bg="#111318", fg="#dbe2ee").grid(row=row, column=0, sticky="w", padx=12, pady=10)
        scale = tk.Scale(
            parent,
            from_=0,
            to=1,
            resolution=0.01,
            orient="horizontal",
            variable=self.volume_vars[key],
            bg="#111318",
            fg="#dbe2ee",
            highlightthickness=0,
            troughcolor="#243041",
            activebackground="#3b82f6",
        )
        scale.grid(row=row, column=1, sticky="ew", padx=12, pady=10)
        parent.columnconfigure(1, weight=1)

    def _bind_shortcuts(self) -> None:
        self.root.bind("<Control-r>", lambda _event: self.refresh_library())
        self.root.bind("<Control-m>", lambda _event: self.toggle_microphone())
        self.root.bind("<Control-e>", lambda _event: self.play_selected_sfx())
        self.root.bind("<Control-p>", lambda _event: self.toggle_music_pause())
        self.root.bind("<Control-s>", lambda _event: self.save_settings())

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

        all_devices = [
            format_device_label(index, device, str(hostapis[int(device.get("hostapi", 0))].get("name", "")) if hostapis else None)
            for index, device in enumerate(devices)
        ]
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

        self.output_device_combo["values"] = all_devices
        self.mic_device_combo["values"] = input_devices
        self.monitor_device_combo["values"] = output_devices

        def pick_label(current_value: str, choices: list[str]) -> str | None:
            if not current_value:
                return None
            if current_value in choices:
                return current_value
            for choice in choices:
                if current_value in choice:
                    return choice
            return None

        current_output = pick_label(self.device_vars["output_device"].get(), all_devices)
        if current_output is not None:
            self.device_vars["output_device"].set(current_output)
        elif all_devices:
            self.device_vars["output_device"].set(all_devices[0])

        current_input = pick_label(self.device_vars["mic_input_device"].get(), input_devices)
        if current_input is not None:
            self.device_vars["mic_input_device"].set(current_input)
        elif input_devices:
            self.device_vars["mic_input_device"].set(input_devices[0])

        current_monitor = pick_label(self.device_vars["monitor_output_device"].get(), output_devices)
        if current_monitor is not None:
            self.device_vars["monitor_output_device"].set(current_monitor)
        elif output_devices:
            self.device_vars["monitor_output_device"].set(self.device_vars["output_device"].get() or output_devices[0])

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
        }

    def _collect_hotkeys(self) -> dict[str, str]:
        return {action: self.hotkey_vars[action].get().strip() for action in HOTKEY_ACTIONS}

    def capture_hotkey(self, action: str) -> None:
        label, _description = HOTKEY_ACTIONS[action]
        dialog = HotkeyCaptureDialog(self.root, label, self.hotkey_vars[action].get())
        self.root.wait_window(dialog)
        if dialog.result:
            self.hotkey_vars[action].set(dialog.result)
            self.apply_hotkeys()
            self._set_status(f"Hotkey capturada para {label}: {dialog.result}")

    def apply_settings(self, persist: bool = False) -> None:
        settings = self._collect_settings()
        self.config_manager.updateConfig(settings)
        self.audio_motor.apply_config(self.config_manager.as_dict())
        self._register_hotkeys()
        self._sync_music_progress()
        if persist:
            self.config_manager.saveConfig()
            self._set_status("Ajustes guardados y aplicados.")
        else:
            self._set_status("Ajustes aplicados al motor de audio.")

    def save_settings(self) -> None:
        self.apply_settings(persist=True)

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

    def _poll_playback_state(self) -> None:
        self._sync_music_progress()
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
            messagebox.showerror("Error", f"No se pudo copiar el archivo:\n{error}")
            return

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
        finally:
            self.root.destroy()


def main() -> None:
    ensure_project_directories()
    root = tk.Tk()
    AudioHandlerApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()