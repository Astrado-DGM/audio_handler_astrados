"""Tema visual estilo Windows 7 / Frutiger Aero (azul cielo y morado).

Los controles ttk se dibujan con imagenes de visual/ui (orbes de vidrio, pills,
burbujas), generadas por tools/build_ui_assets.py a partir de "ASSETS COOL".
"""

from __future__ import annotations

import tkinter as tk
from pathlib import Path
from tkinter import ttk

PAGE_BG = "#e7f1fc"
CARD_BG = "#ffffff"
CARD_ALT_BG = "#f4f8ff"
BORDER = "#a9c8ec"
TEXT = "#1e3a6e"
MUTED = "#5b6f94"
PURPLE = "#6d3fc4"
BLUE = "#2f78d6"
OK_GREEN = "#2e9d57"
WARN = "#b7791f"
ERROR_RED = "#c0392b"
FONT = "Segoe UI"


SCALES = (100, 125, 150, 175, 200)


def enable_dpi_awareness() -> None:
    """Llamar ANTES de crear tk.Tk(): sin esto Windows estira la ventana y se ve borrosa."""
    try:
        import ctypes

        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)  # system aware (Tk no maneja per-monitor)
        except (AttributeError, OSError):
            ctypes.windll.user32.SetProcessDPIAware()
    except (AttributeError, OSError, ImportError):
        pass


class Theme:
    def __init__(self, root: tk.Tk, ui_dir: Path) -> None:
        self.root = root
        # Escala de la pantalla (1.25 a 120 DPI) y el set de imagenes dibujado para ella.
        self.scale = root.winfo_fpixels("1i") / 96.0
        self.percent = min(SCALES, key=lambda value: abs(value / 100 - self.scale))
        self.ui_dir = Path(ui_dir) / str(self.percent)
        # Tk libera las imagenes sin referencia: se guardan todas aqui.
        self.images: dict[str, tk.PhotoImage] = {}
        for path in sorted(self.ui_dir.glob("*.png")):
            self.images[path.stem] = tk.PhotoImage(master=root, file=str(path))
        self.style = ttk.Style(root)
        self._create_theme()

    def px(self, value: float) -> int:
        """Distancia en pixeles escalada igual que las imagenes."""
        return max(1, round(value * self.percent / 100))

    def image(self, name: str) -> tk.PhotoImage:
        return self.images[name]

    def button_images(self, base: str) -> tuple:
        """Especificacion de imagen por estado para un ttk.Button con imagen."""
        return (self.images[base], "pressed", self.images[f"{base}_pressed"], "active", self.images[f"{base}_hover"])

    def _create_theme(self) -> None:
        style = self.style
        images = self.images
        px = self.px
        if "aero" not in style.theme_names():
            style.theme_create("aero", parent="clam")
        style.theme_use("aero")

        style.configure(".", background=PAGE_BG, foreground=TEXT, font=(FONT, 9), bordercolor=BORDER,
                        lightcolor=CARD_BG, darkcolor=BORDER, troughcolor=PAGE_BG, focuscolor=PURPLE,
                        selectbackground=BLUE, selectforeground="white", fieldbackground=CARD_BG)

        # Botones pill: el borde izquierdo/derecho del orbe no se estira, el centro si.
        for variant, fg in (("Blue", "white"), ("Light", TEXT), ("Purple", "white")):
            element = f"Aero{variant}.Button.border"
            key = f"pill_{variant.lower()}"
            style.element_create(
                element, "image", images[key],
                ("disabled", images[f"{key}_disabled"]),
                ("pressed", images[f"{key}_pressed"]),
                ("active", images[f"{key}_hover"]),
                border=(px(16), 0, px(16), 0), sticky="nsew",
            )
            style.layout(f"{variant}.TButton", [
                (element, {"sticky": "nsew", "children": [
                    ("Button.padding", {"sticky": "nsew", "children": [("Button.label", {"sticky": "nsew"})]}),
                ]}),
            ])
            style.configure(f"{variant}.TButton", foreground=fg, padding=(px(16), px(7), px(16), px(7)), anchor="center",
                            font=(FONT, 9, "bold"))
            style.map(f"{variant}.TButton", foreground=[("disabled", "#8a96aa")])
        style.layout("TButton", style.layout("Light.TButton"))
        style.configure("TButton", foreground=TEXT, padding=(px(16), px(7), px(16), px(7)), anchor="center", font=(FONT, 9, "bold"))

        # Botones redondos (solo imagen) sobre tarjetas blancas.
        style.layout("Orb.TButton", [("Button.padding", {"sticky": "nsew", "children": [("Button.label", {"sticky": "nsew"})]})])
        style.configure("Orb.TButton", background=CARD_BG, padding=1, relief="flat", borderwidth=0)
        style.map("Orb.TButton", background=[("active", CARD_BG)])

        # Pestanas con pills celestes; la seleccionada es morada.
        style.element_create("Aero.Notebook.tab", "image", images["tab"],
                             ("selected", images["tab_selected"]), ("active", images["tab_hover"]),
                             border=(px(15), 0, px(15), 0), sticky="nsew")
        style.layout("TNotebook.Tab", [
            ("Aero.Notebook.tab", {"sticky": "nsew", "children": [
                ("Notebook.padding", {"side": "top", "sticky": "nsew", "children": [
                    ("Notebook.label", {"side": "top", "sticky": ""}),
                ]}),
            ]}),
        ])
        style.configure("TNotebook", background=PAGE_BG, borderwidth=0, tabmargins=(0, 0, 0, px(6)))
        style.configure("TNotebook.Tab", padding=(px(18), px(6), px(18), px(6)), foreground=TEXT, font=(FONT, 10, "bold"))
        style.map("TNotebook.Tab", foreground=[("selected", "white")], expand=[("selected", (0, 0, 0, 0))])

        # Casillas: burbuja vacia / orbe azul con check.
        style.element_create("Aero.Checkbutton.indicator", "image", images["check_off"],
                             ("selected", images["check_on"]), ("active", images["check_off_hover"]),
                             sticky="")
        style.layout("TCheckbutton", [
            ("Checkbutton.padding", {"sticky": "nsew", "children": [
                ("Aero.Checkbutton.indicator", {"side": "left", "sticky": ""}),
                ("Checkbutton.label", {"side": "left", "sticky": "nsew"}),
            ]}),
        ])
        style.configure("TCheckbutton", background=CARD_BG, foreground=TEXT, padding=(px(2), px(3)), font=(FONT, 9))
        style.map("TCheckbutton", background=[("active", CARD_BG)], foreground=[("disabled", MUTED)])

        # Sliders: riel celeste y perilla de orbe morado.
        style.element_create("Aero.Horizontal.Scale.trough", "image", images["slider_trough"],
                             border=(px(5), 0, px(5), 0), sticky="ew")
        style.element_create("Aero.Horizontal.Scale.slider", "image", images["slider_thumb"],
                             ("active", images["slider_thumb_hover"]), sticky="")
        style.layout("Horizontal.TScale", [
            ("Aero.Horizontal.Scale.trough", {"sticky": "ew", "children": [
                ("Aero.Horizontal.Scale.slider", {"side": "left", "sticky": ""}),
            ]}),
        ])
        style.configure("Horizontal.TScale", background=CARD_BG)

        # Campos y listas.
        style.configure("TEntry", fieldbackground=CARD_BG, bordercolor=BORDER, lightcolor=CARD_BG, padding=4)
        style.map("TEntry", bordercolor=[("focus", PURPLE)])
        style.configure("TCombobox", fieldbackground=CARD_BG, background=CARD_ALT_BG, bordercolor=BORDER,
                        arrowcolor=PURPLE, padding=3)
        style.map("TCombobox", fieldbackground=[("readonly", CARD_BG)], bordercolor=[("focus", PURPLE)],
                  selectbackground=[("readonly", CARD_BG)], selectforeground=[("readonly", TEXT)])
        root = self.root
        root.option_add("*TCombobox*Listbox.background", CARD_BG)
        root.option_add("*TCombobox*Listbox.foreground", TEXT)
        root.option_add("*TCombobox*Listbox.selectBackground", BLUE)
        root.option_add("*TCombobox*Listbox.font", (FONT, 9))
        style.configure("Vertical.TScrollbar", background=CARD_ALT_BG, troughcolor=PAGE_BG, bordercolor=BORDER,
                        arrowcolor=PURPLE, lightcolor=CARD_BG, darkcolor=BORDER)

        # Contenedores y textos.
        style.configure("TFrame", background=PAGE_BG)
        style.configure("Card.TFrame", background=CARD_BG)
        style.configure("TLabelframe", background=CARD_BG, bordercolor=BORDER, lightcolor=CARD_BG, darkcolor=BORDER,
                        relief="solid", borderwidth=1)
        style.configure("TLabelframe.Label", background=CARD_BG, foreground=PURPLE, font=(FONT, 10, "bold"))
        style.configure("TLabel", background=PAGE_BG, foreground=TEXT)
        style.configure("Title.TLabel", background=PAGE_BG, foreground=TEXT, font=(FONT, 15, "bold"))
        style.configure("Muted.TLabel", background=PAGE_BG, foreground=MUTED)
        style.configure("Card.TLabel", background=CARD_BG, foreground=TEXT)
        style.configure("CardBold.TLabel", background=CARD_BG, foreground=TEXT, font=(FONT, 10, "bold"))
        style.configure("CardMuted.TLabel", background=CARD_BG, foreground=MUTED, font=(FONT, 8))
        style.configure("Meter.TLabel", background=CARD_BG, foreground=PURPLE, font=("Consolas", 9))
        style.configure("Status.TLabel", background=CARD_BG, foreground=WARN, font=(FONT, 9, "bold"))


def card(parent: tk.Misc) -> tk.Frame:
    """Tarjeta blanca con borde celeste fino (un tk.Frame, para poder colorear el borde)."""
    return tk.Frame(parent, bg=CARD_BG, highlightthickness=1, highlightbackground=BORDER, highlightcolor=BORDER)
