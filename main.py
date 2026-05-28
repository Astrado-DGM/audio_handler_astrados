"""Base de arranque para el proyecto de mezcla de audio local.

Este archivo deja preparada la estructura inicial del programa:
- organiza carpetas locales para musica, efectos, grabaciones y configuracion
- muestra una interfaz visual basica
- lista archivos mp3 locales detectados
- reserva puntos de extension para el motor de audio real y atajos de teclado

La reproduccion simultanea de microfono, musica y efectos se implementara en
una siguiente fase con una libreria de audio dedicada y un dispositivo de
salida virtual.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import shutil
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import sounddevice as sd

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


@dataclass(frozen=True)
class AudioAsset:
    category: str
    path: Path

    @property
    def name(self) -> str:
        return self.path.stem


def ensure_project_directories() -> None:
    """Create the local folder layout used by the application."""

    for folder in (DATA_DIR, MUSIC_DIR, SFX_DIR, RECORDINGS_DIR, CONFIG_DIR):
        folder.mkdir(parents=True, exist_ok=True)


def scan_local_assets(folder: Path, category: str) -> list[AudioAsset]:
    """Return the local mp3 files found in a category folder."""

    assets: list[AudioAsset] = []
    if not folder.exists():
        return assets

    for path in sorted(folder.glob("**/*")):
        if path.is_file() and path.suffix.lower() in TRACK_EXTENSIONS:
            assets.append(AudioAsset(category=category, path=path))
    return assets


class AudioHandlerApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title(APP_TITLE)
        self.root.geometry("1120x760")
        self.root.minsize(960, 640)

        self.status_text = tk.StringVar(value="Listo para configurar la biblioteca local.")
        self.music_assets: list[AudioAsset] = []
        self.sfx_assets: list[AudioAsset] = []
        self.config_manager = configHandler(CONFIG_DIR)
        self.audio_motor = soundMotor(self.config_manager)

        self.volume_vars = {
            "volume_music": tk.DoubleVar(value=self.config_manager.volume_music()),
            "volume_sfx": tk.DoubleVar(value=self.config_manager.volume_sfx()),
            "volume_mic": tk.DoubleVar(value=self.config_manager.mic_volume()),
        }
        self.bool_vars = {
            "mic_active": tk.BooleanVar(value=self.config_manager.mic_active()),
            "loop_music": tk.BooleanVar(value=self.config_manager.loop_music()),
            "loop_sfx": tk.BooleanVar(value=self.config_manager.loop_sfx()),
        }
        self.device_vars = {
            "output_device": tk.StringVar(value=self.config_manager.output_device() or self.config_manager.default_output_device() or ""),
            "mic_input_device": tk.StringVar(value=self.config_manager.mic_input_device() or ""),
        }

        self._build_ui()
        self._bind_shortcuts()
        self.refresh_library()
        self._load_device_lists()
        self._sync_motor_from_ui()

    def _build_ui(self) -> None:
        self.root.configure(bg="#111318")

        header = tk.Frame(self.root, bg="#111318")
        header.pack(fill="x", padx=16, pady=(16, 10))

        title = tk.Label(
            header,
            text="Audio Handler PV",
            font=("Segoe UI", 22, "bold"),
            fg="#f4f7fb",
            bg="#111318",
        )
        title.pack(anchor="w")

        subtitle = tk.Label(
            header,
            text="Base local para musica en loop, efectos de sonido y entrada de microfono en una sola salida.",
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
        ttk.Button(action_bar, text="Probar motor", command=self._show_engine_placeholder).pack(side="right")

        notebook = ttk.Notebook(self.root)
        notebook.pack(fill="both", expand=True, padx=16, pady=10)

        self.music_tab = self._build_library_tab(notebook, "Musica", "Pistas locales para loop de fondo")
        self.sfx_tab = self._build_library_tab(notebook, "Efectos", "Archivos disparados por evento")
        self.hotkeys_tab = self._build_hotkeys_tab(notebook)
        self.engine_tab = self._build_engine_tab(notebook)
        self.config_tab = self._build_config_tab(notebook)

        notebook.add(self.music_tab, text="Musica")
        notebook.add(self.sfx_tab, text="Efectos")
        notebook.add(self.hotkeys_tab, text="Atajos")
        notebook.add(self.engine_tab, text="Motor")
        notebook.add(self.config_tab, text="Config")

        status_bar = tk.Label(
            self.root,
            textvariable=self.status_text,
            anchor="w",
            bg="#1a1d24",
            fg="#dbe2ee",
            padx=12,
            pady=8,
        )
        status_bar.pack(fill="x", side="bottom")

    def _build_library_tab(self, parent: ttk.Notebook, title: str, description: str) -> tk.Frame:
        frame = tk.Frame(parent, bg="#171a20")

        tk.Label(frame, text=title, font=("Segoe UI", 16, "bold"), bg="#171a20", fg="#f4f7fb").pack(anchor="w", padx=16, pady=(16, 4))
        tk.Label(frame, text=description, font=("Segoe UI", 10), bg="#171a20", fg="#b9c1cc").pack(anchor="w", padx=16)

        search_bar = tk.Frame(frame, bg="#171a20")
        search_bar.pack(fill="x", padx=16, pady=12)

        tk.Label(search_bar, text="Filtro local:", bg="#171a20", fg="#dbe2ee").pack(side="left")
        entry = ttk.Entry(search_bar)
        entry.pack(side="left", fill="x", expand=True, padx=8)

        listbox = tk.Listbox(
            frame,
            height=18,
            bg="#0f1217",
            fg="#f4f7fb",
            selectbackground="#3b82f6",
            selectforeground="#ffffff",
            relief="flat",
            highlightthickness=0,
        )
        listbox.pack(fill="both", expand=True, padx=16, pady=(0, 12))

        setattr(self, f"{title.lower()}_listbox", listbox)
        setattr(self, f"{title.lower()}_filter_entry", entry)
        return frame

    def _build_hotkeys_tab(self, parent: ttk.Notebook) -> tk.Frame:
        frame = tk.Frame(parent, bg="#171a20")
        tk.Label(frame, text="Atajos rapidos", font=("Segoe UI", 16, "bold"), bg="#171a20", fg="#f4f7fb").pack(anchor="w", padx=16, pady=(16, 4))
        tk.Label(frame, text="Reservado para iniciar, detener y lanzar efectos sin usar el mouse.", font=("Segoe UI", 10), bg="#171a20", fg="#b9c1cc").pack(anchor="w", padx=16)

        shortcuts = [
            ("Ctrl+R", "Recargar biblioteca local"),
            ("Ctrl+M", "Activar o silenciar microfono"),
            ("Ctrl+Shift+M", "Seleccionar musica de fondo"),
            ("Ctrl+E", "Disparar efecto seleccionado"),
            ("Espacio", "Pausar o reanudar musica"),
        ]

        box = tk.Frame(frame, bg="#111318")
        box.pack(fill="both", expand=True, padx=16, pady=16)

        for key, action in shortcuts:
            row = tk.Frame(box, bg="#111318")
            row.pack(fill="x", padx=12, pady=8)
            tk.Label(row, text=key, width=16, anchor="w", bg="#111318", fg="#7dd3fc", font=("Segoe UI", 10, "bold")).pack(side="left")
            tk.Label(row, text=action, anchor="w", bg="#111318", fg="#e5e7eb").pack(side="left")

        return frame

    def _build_engine_tab(self, parent: ttk.Notebook) -> tk.Frame:
        frame = tk.Frame(parent, bg="#171a20")
        tk.Label(frame, text="Motor de audio", font=("Segoe UI", 16, "bold"), bg="#171a20", fg="#f4f7fb").pack(anchor="w", padx=16, pady=(16, 4))
        tk.Label(frame, text="El motor combina microfono en tiempo real, musica y SFX en una salida objetivo seleccionable.", font=("Segoe UI", 10), bg="#171a20", fg="#b9c1cc").pack(anchor="w", padx=16)

        text = (
            "Fase 1: escaneo de archivos locales\n"
            "Fase 2: mezcla de un SFX a la vez\n"
            "Fase 3: microfono en tiempo real hacia la salida\n"
            "Fase 4: control visual, dispositivos y guardado\n"
            "Fase 5: pruebas de latencia y estabilidad"
        )
        tk.Label(frame, text=text, justify="left", bg="#111318", fg="#e5e7eb", font=("Consolas", 10), padx=16, pady=16).pack(fill="x", padx=16, pady=16)
        return frame

    def _build_config_tab(self, parent: ttk.Notebook) -> tk.Frame:
        frame = tk.Frame(parent, bg="#171a20")
        tk.Label(frame, text="Configuracion de audio", font=("Segoe UI", 16, "bold"), bg="#171a20", fg="#f4f7fb").pack(anchor="w", padx=16, pady=(16, 4))
        tk.Label(frame, text="Ajusta volumenes, dispositivos, loop y estado del microfono. Los cambios se guardan en data/config.", font=("Segoe UI", 10), bg="#171a20", fg="#b9c1cc").pack(anchor="w", padx=16)

        controls = tk.Frame(frame, bg="#111318")
        controls.pack(fill="both", expand=True, padx=16, pady=16)

        self._add_slider(controls, "Volumen musica", "volume_music", 0)
        self._add_slider(controls, "Volumen SFX", "volume_sfx", 1)
        self._add_slider(controls, "Volumen microfono", "volume_mic", 2)

        switches = tk.Frame(controls, bg="#111318")
        switches.grid(row=3, column=0, columnspan=2, sticky="ew", padx=12, pady=(8, 4))
        tk.Checkbutton(switches, text="Microfono activo", variable=self.bool_vars["mic_active"], bg="#111318", fg="#e5e7eb", selectcolor="#111318", activebackground="#111318", activeforeground="#e5e7eb").pack(anchor="w")
        tk.Checkbutton(switches, text="Loop musica", variable=self.bool_vars["loop_music"], bg="#111318", fg="#e5e7eb", selectcolor="#111318", activebackground="#111318", activeforeground="#e5e7eb").pack(anchor="w")
        tk.Checkbutton(switches, text="Loop SFX", variable=self.bool_vars["loop_sfx"], bg="#111318", fg="#e5e7eb", selectcolor="#111318", activebackground="#111318", activeforeground="#e5e7eb").pack(anchor="w")

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

        ttk.Button(device_frame, text="Guardar ajustes", command=self.save_settings).grid(row=0, column=2, rowspan=2, padx=16, sticky="ns")
        ttk.Button(device_frame, text="Aplicar ajustes", command=self.apply_settings).grid(row=0, column=3, rowspan=2, sticky="ns")

        info = tk.Label(
            controls,
            text="Los canales logicos son: microfono, musica y SFX. El motor limita SFX simultaneos a uno y no excede 8 canales de mezcla.",
            bg="#111318",
            fg="#b9c1cc",
            wraplength=860,
            justify="left",
        )
        info.grid(row=5, column=0, columnspan=2, sticky="w", padx=12, pady=(14, 8))

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
        self.root.bind("<Control-r>", lambda event: self.refresh_library())
        self.root.bind("<Control-m>", lambda event: self._set_status("Atajo reservado para alternar el microfono."))
        self.root.bind("<Control-e>", lambda event: self._set_status("Atajo reservado para lanzar el efecto seleccionado."))
        self.root.bind("<space>", lambda event: self._set_status("Atajo reservado para pausar o reanudar musica."))

    def _set_status(self, message: str) -> None:
        self.status_text.set(message)

    def refresh_library(self) -> None:
        self.music_assets = scan_local_assets(MUSIC_DIR, "music")
        self.sfx_assets = scan_local_assets(SFX_DIR, "sfx")

        self._fill_listbox(self.musica_listbox, self.music_assets, MUSIC_DIR)
        self._fill_listbox(self.efectos_listbox, self.sfx_assets, SFX_DIR)

        self._set_status(
            f"Biblioteca local actualizada: {len(self.music_assets)} pistas de musica y {len(self.sfx_assets)} efectos."
        )

    def _load_device_lists(self) -> None:
        devices = sd.query_devices()
        output_devices = [device["name"] for device in devices if device.get("max_output_channels", 0) > 0]
        input_devices = [device["name"] for device in devices if device.get("max_input_channels", 0) > 0]

        self.output_device_combo["values"] = output_devices
        self.mic_device_combo["values"] = input_devices

        if not self.device_vars["output_device"].get() and output_devices:
            self.device_vars["output_device"].set(output_devices[0])
        if not self.device_vars["mic_input_device"].get() and input_devices:
            self.device_vars["mic_input_device"].set(input_devices[0])

    def _sync_motor_from_ui(self) -> None:
        self.apply_settings()

    def _collect_settings(self) -> dict[str, object]:
        return {
            "volume_music": float(self.volume_vars["volume_music"].get()),
            "volume_sfx": float(self.volume_vars["volume_sfx"].get()),
            "volume_mic": float(self.volume_vars["volume_mic"].get()),
            "mic_active": bool(self.bool_vars["mic_active"].get()),
            "loop_music": bool(self.bool_vars["loop_music"].get()),
            "loop_sfx": bool(self.bool_vars["loop_sfx"].get()),
            "output_device": self.device_vars["output_device"].get() or None,
            "default_output_device": self.device_vars["output_device"].get() or None,
            "mic_input_device": self.device_vars["mic_input_device"].get() or None,
        }

    def apply_settings(self) -> None:
        settings = self._collect_settings()
        self.config_manager.updateConfig(settings)
        self.audio_motor.apply_config(self.config_manager.as_dict())
        self._set_status("Ajustes aplicados al motor de audio.")

    def save_settings(self) -> None:
        self.apply_settings()
        saved_path = self.config_manager.saveConfig()
        self._set_status(f"Configuracion guardada en {saved_path.name}.")

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

    def _show_engine_placeholder(self) -> None:
        messagebox.showinfo(
            "Motor base",
            "La interfaz esta lista. El siguiente paso es integrar el motor de mezcla,\n"
            "la salida virtual y la gestion simultanea de canales.",
        )


def main() -> None:
    ensure_project_directories()
    root = tk.Tk()
    AudioHandlerApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()