"""Genera los recursos visuales y sonoros de la interfaz a partir de "ASSETS COOL".

Uso (solo al cambiar el tema; la app usa los PNG/WAV ya generados en visual/):
    pip install -r tools/requirements-build.txt
    python tools/build_ui_assets.py "C:/ruta/a/ASSETS COOL"

Todo sale de assets existentes: los botones "pill" se arman estirando los orbes
de vidrio del Frutiger Aero Archive, los iconos son Heroicons rasterizados y los
sonidos son SOUNDICONS / Helton Yan recortados y normalizados.
"""

from __future__ import annotations

import struct
import sys
import wave
from pathlib import Path

import pymupdf as fitz  # solo para rasterizar los SVG de Heroicons
import numpy as np
from PIL import Image, ImageEnhance, ImageOps

PROJECT = Path(__file__).resolve().parents[1]
UI_OUT = PROJECT / "visual" / "ui"
SOUND_OUT = PROJECT / "visual" / "sounds"
SUPERSAMPLE = 4

# Paleta Windows 7 / Frutiger: azul cielo y morado.
ICON_DARK = "#1e3a6e"
ICON_WHITE = "#ffffff"
ICON_PURPLE = "#6d3fc4"


def load(src: Path, rel: str) -> Image.Image:
    return Image.open(src / rel).convert("RGBA")


def crop_alpha(image: Image.Image) -> Image.Image:
    bbox = image.getchannel("A").point(lambda a: 255 if a > 8 else 0).getbbox()
    return image.crop(bbox) if bbox else image


def square(image: Image.Image) -> Image.Image:
    size = max(image.size)
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    canvas.paste(image, ((size - image.width) // 2, (size - image.height) // 2))
    return canvas


def shift_hue(image: Image.Image, target_hue: float, saturation: float = 1.0) -> Image.Image:
    """Cambia el tono conservando luces y brillos (para pasar el orbe magenta a morado)."""
    rgba = np.asarray(image).astype(np.float32) / 255.0
    rgb = rgba[..., :3]
    maxc, minc = rgb.max(-1), rgb.min(-1)
    value = maxc
    sat = np.where(maxc > 0, (maxc - minc) / np.maximum(maxc, 1e-6), 0) * saturation
    h = np.full(value.shape, target_hue)
    i = np.floor(h * 6).astype(int) % 6
    f = h * 6 - np.floor(h * 6)
    p, q, t = value * (1 - sat), value * (1 - sat * f), value * (1 - sat * (1 - f))
    choices = [(value, t, p), (q, value, p), (p, value, t), (p, q, value), (t, p, value), (value, p, q)]
    out = np.zeros_like(rgb)
    for index, (r, g, b) in enumerate(choices):
        mask = i == index
        out[..., 0][mask], out[..., 1][mask], out[..., 2][mask] = r[mask], g[mask], b[mask]
    result = np.dstack([out, rgba[..., 3:]])
    return Image.fromarray((np.clip(result, 0, 1) * 255).astype(np.uint8), "RGBA")


def lighten(image: Image.Image, amount: float) -> Image.Image:
    rgba = np.asarray(image).astype(np.float32)
    rgba[..., :3] = rgba[..., :3] + (255 - rgba[..., :3]) * amount
    return Image.fromarray(rgba.clip(0, 255).astype(np.uint8), "RGBA")


def brightness(image: Image.Image, factor: float) -> Image.Image:
    alpha = image.getchannel("A")
    rgb = ImageEnhance.Brightness(image.convert("RGB")).enhance(factor)
    rgb.putalpha(alpha)
    return rgb


def disabled(image: Image.Image) -> Image.Image:
    alpha = image.getchannel("A").point(lambda a: int(a * 0.55))
    gray = ImageOps.grayscale(image.convert("RGB")).convert("RGB")
    gray = lighten(gray.convert("RGBA"), 0.35)
    gray.putalpha(alpha)
    return gray


def resize(image: Image.Image, size: tuple[int, int]) -> Image.Image:
    return image.resize(size, Image.LANCZOS)


def orb(image: Image.Image, diameter: int) -> Image.Image:
    return resize(square(crop_alpha(image)), (diameter, diameter))


def pill_from_orb(orb_image: Image.Image, width: int, height: int) -> Image.Image:
    """Corta el orbe por la mitad y estira la franja central: un boton glossy estilo Aero."""
    big_h = height * SUPERSAMPLE
    big_w = width * SUPERSAMPLE
    ball = orb(orb_image, big_h)
    half = big_h // 2
    pill = Image.new("RGBA", (big_w, big_h), (0, 0, 0, 0))
    pill.paste(ball.crop((0, 0, half, big_h)), (0, 0))
    center = ball.crop((half - 1, 0, half + 1, big_h)).resize((max(1, big_w - 2 * half), big_h), Image.BILINEAR)
    pill.paste(center, (half, 0))
    pill.paste(ball.crop((big_h - half, 0, big_h, big_h)), (big_w - half, 0))
    return resize(pill, (width, height))


def svg_icon(src: Path, name: str, size: int, color: str, style: str = "solid") -> Image.Image:
    svg = (src / "Graphic" / "SVG" / "Heroicons" / style / f"{name}.svg").read_text(encoding="utf-8")
    svg = svg.replace("currentColor", color)
    document = fitz.open(stream=svg.encode("utf-8"), filetype="svg")
    zoom = size * SUPERSAMPLE / document[0].rect.width
    pixmap = document[0].get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=True)
    image = Image.frombytes("RGBA", (pixmap.width, pixmap.height), pixmap.samples)
    return resize(image, (size, size))


def overlay_center(base: Image.Image, top: Image.Image, dy: int = 0) -> Image.Image:
    out = base.copy()
    out.alpha_composite(top, ((base.width - top.width) // 2, (base.height - top.height) // 2 + dy))
    return out


def cover(image: Image.Image, width: int, height: int, y_anchor: float) -> Image.Image:
    scale = max(width / image.width, height / image.height)
    scaled = resize(image, (max(width, round(image.width * scale)), max(height, round(image.height * scale))))
    top = int((scaled.height - height) * y_anchor)
    left = (scaled.width - width) // 2
    return scaled.crop((left, top, left + width, top + height))


def gradient_tint(image: Image.Image, color: tuple[int, int, int], left_alpha: float, right_alpha: float) -> Image.Image:
    width, height = image.size
    alpha = np.linspace(left_alpha, right_alpha, width, dtype=np.float32)[None, :].repeat(height, 0)
    layer = np.zeros((height, width, 4), np.uint8)
    layer[..., :3] = color
    layer[..., 3] = (alpha * 255).astype(np.uint8)
    out = image.copy()
    out.alpha_composite(Image.fromarray(layer, "RGBA"))
    return out


def gloss(image: Image.Image, strength: float) -> Image.Image:
    """Brillo superior de vidrio (como la barra de titulo de Windows 7)."""
    width, height = image.size
    alpha = np.zeros((height, width), np.float32)
    alpha[: height // 2] = np.linspace(strength, strength * 0.4, height // 2)[:, None]
    layer = np.zeros((height, width, 4), np.uint8)
    layer[..., :3] = 255
    layer[..., 3] = (alpha * 255).astype(np.uint8)
    out = image.copy()
    out.alpha_composite(Image.fromarray(layer, "RGBA"))
    return out


def save(image: Image.Image, name: str) -> None:
    image.save(UI_OUT / f"{name}.png", optimize=True)


def build_images(src: Path) -> None:
    UI_OUT.mkdir(parents=True, exist_ok=True)
    archive = "Graphic/frutigeraeroarchive"
    blue_orb = load(src, f"{archive}/metro/metro_52.png")
    # El orbe cian se lleva a azul cielo para la paleta azul claro / morado.
    cyan_orb = shift_hue(load(src, f"{archive}/metro/metro_53.png"), 0.56, 0.95)
    green_orb = load(src, f"{archive}/metro/metro_54.png")
    red_orb = load(src, f"{archive}/metro/metro_58.png")
    purple_orb = shift_hue(load(src, f"{archive}/metro/metro_57.png"), 0.74, 0.9)
    light_orb = lighten(cyan_orb, 0.42)

    # Cabecera: cielo con nubes, velo morado a la izquierda y brillo de vidrio.
    sky = load(src, f"{archive}/skyboxes/skyboxes_47.png")
    header = cover(sky, 1920, 112, 0.0)
    header = gradient_tint(header, (109, 63, 196), 0.62, 0.0)
    save(gloss(header, 0.22), "header")

    status = cover(load(src, f"{archive}/skyboxes/skyboxes_6.png"), 1920, 30, 0.85)
    save(gradient_tint(gloss(status, 0.35), (109, 63, 196), 0.10, 0.0), "statusbar")

    bubbles = crop_alpha(load(src, f"{archive}/bubbles/bubbles_10.png"))
    save(resize(bubbles, (round(bubbles.width * 104 / bubbles.height), 104)), "header_bubbles")

    glow = crop_alpha(load(src, f"{archive}/flares/flares_19.png"))
    speaker = crop_alpha(load(src, f"{archive}/objects/objects_13.png"))
    logo = Image.new("RGBA", (96, 96), (0, 0, 0, 0))
    logo.alpha_composite(resize(square(glow), (96, 96)))
    logo.alpha_composite(resize(square(speaker), (66, 66)), (15, 15))
    save(logo, "logo")
    save(resize(square(speaker), (32, 32)), "app_icon")

    # Botones pill (normal / hover / pressed / disabled) a partir de los orbes.
    for name, source in (("blue", blue_orb), ("light", light_orb), ("purple", purple_orb)):
        base = pill_from_orb(source, 120, 32)
        save(base, f"pill_{name}")
        save(brightness(base, 1.12), f"pill_{name}_hover")
        save(brightness(base, 0.85), f"pill_{name}_pressed")
        save(disabled(base), f"pill_{name}_disabled")

    tab = pill_from_orb(light_orb, 110, 30)
    save(tab, "tab")
    save(brightness(tab, 1.08), "tab_hover")
    save(pill_from_orb(purple_orb, 110, 30), "tab_selected")

    # Botones redondos de reproduccion: orbe + icono blanco.
    for action, icon_name, source in (("play", "play", blue_orb), ("pause", "pause", cyan_orb), ("stop", "stop", purple_orb)):
        ball = orb(source, 38)
        icon = svg_icon(src, icon_name, 16, ICON_WHITE)
        face = overlay_center(ball, icon, dy=1)
        save(face, f"orb_{action}")
        save(brightness(face, 1.12), f"orb_{action}_hover")
        save(brightness(face, 0.85), f"orb_{action}_pressed")

    # Casillas: burbuja vacia / orbe azul con check.
    def with_gap(image: Image.Image, gap: int = 6) -> Image.Image:
        # Margen transparente a la derecha: separa la casilla de su texto.
        canvas = Image.new("RGBA", (image.width + gap, image.height), (0, 0, 0, 0))
        canvas.alpha_composite(image)
        return canvas

    bubble = orb(load(src, f"{archive}/bubbles/bubbles_22.png"), 20)
    save(with_gap(bubble), "check_off")
    save(with_gap(overlay_center(orb(blue_orb, 20), svg_icon(src, "check", 13, ICON_WHITE))), "check_on")
    save(with_gap(brightness(bubble, 1.1)), "check_off_hover")

    # Slider: perilla de orbe morado y riel claro.
    save(orb(purple_orb, 18), "slider_thumb")
    save(brightness(orb(purple_orb, 18), 1.15), "slider_thumb_hover")
    # El riel va centrado en un lienzo de la altura de la perilla, asi ttk no lo deforma.
    trough = Image.new("RGBA", (60, 18), (0, 0, 0, 0))
    trough.alpha_composite(pill_from_orb(light_orb, 60, 8), (0, 5))
    save(trough, "slider_trough")

    save(orb(green_orb, 14), "led_on")
    save(orb(red_orb, 14), "led_off")

    # Iconos de pestanas y botones.
    for name in ("musical-note", "sparkles", "command-line", "cog-6-tooth", "cpu-chip", "microphone", "speaker-wave"):
        save(svg_icon(src, name, 18, ICON_DARK), f"icon_{name}")
    for name in ("arrow-path", "folder-plus", "plus", "arrow-down-tray", "check", "microphone", "bolt", "arrow-top-right-on-square"):
        save(svg_icon(src, name, 16, ICON_WHITE), f"icon_{name}_white")
        save(svg_icon(src, name, 16, ICON_DARK), f"icon_{name}_dark")
    save(svg_icon(src, "microphone", 18, ICON_PURPLE), "icon_microphone_purple")


# ---------------------------------------------------------------- sonidos

def read_wav(path: Path) -> tuple[np.ndarray, int]:
    """Lector WAV minimo: PCM 16/24/32 y float32 (el modulo wave no lee float)."""
    data = path.read_bytes()
    position, fmt, samples = 12, None, None
    while position + 8 <= len(data):
        chunk_id, size = data[position:position + 4], struct.unpack("<I", data[position + 4:position + 8])[0]
        body = data[position + 8:position + 8 + size]
        if chunk_id == b"fmt ":
            fmt = struct.unpack("<HHIIHH", body[:16])
        elif chunk_id == b"data":
            samples = body
        position += 8 + size + (size % 2)
    if fmt is None or samples is None:
        raise ValueError(f"WAV invalido: {path}")
    audio_format, channels, rate, _byte_rate, _align, bits = fmt
    if audio_format == 0xFFFE:
        audio_format = 3 if bits == 32 and b"\x03\x00\x00\x00\x00\x00\x10\x00" in data[:200] else 1
    if audio_format == 3:
        array = np.frombuffer(samples, "<f4" if bits == 32 else "<f8").astype(np.float32)
    elif bits == 16:
        array = np.frombuffer(samples, "<i2").astype(np.float32) / 32768
    elif bits == 24:
        raw = np.frombuffer(samples[: len(samples) // 3 * 3], np.uint8).reshape(-1, 3)
        ints = (raw[:, 0].astype(np.int32) | (raw[:, 1].astype(np.int32) << 8) | (raw[:, 2].astype(np.int32) << 16))
        array = (np.where(ints >= 1 << 23, ints - (1 << 24), ints)).astype(np.float32) / (1 << 23)
    elif bits == 32:
        array = np.frombuffer(samples, "<i4").astype(np.float32) / (1 << 31)
    else:
        raise ValueError(f"Formato no soportado ({bits} bits): {path}")
    return array[: len(array) // channels * channels].reshape(-1, channels), rate


def write_sound(audio: np.ndarray, rate: int, name: str, max_seconds: float, peak_db: float) -> None:
    if audio.shape[1] == 1:
        audio = np.repeat(audio, 2, axis=1)
    audio = audio[:, :2]
    if rate != 44100:
        positions = np.arange(0, len(audio) - 1, rate / 44100)
        audio = np.stack([np.interp(positions, np.arange(len(audio)), audio[:, c]) for c in range(2)], axis=1)
    envelope = np.abs(audio).max(1)
    loud = np.nonzero(envelope > envelope.max() * 0.01)[0]
    end = min(len(audio), (loud[-1] if len(loud) else len(audio)) + int(0.06 * 44100), int(max_seconds * 44100))
    start = loud[0] if len(loud) else 0
    audio = audio[start:end]
    fade = min(len(audio), int(0.08 * 44100))
    audio[-fade:] *= np.linspace(1, 0, fade)[:, None]
    audio *= (10 ** (peak_db / 20)) / max(np.abs(audio).max(), 1e-6)
    with wave.open(str(SOUND_OUT / f"{name}.wav"), "wb") as handle:
        handle.setnchannels(2)
        handle.setsampwidth(2)
        handle.setframerate(44100)
        handle.writeframes((audio * 32767).astype("<i2").tobytes())


SOUNDS = {
    # nombre: (ruta relativa, duracion maxima, pico en dBFS)
    "click": ("Audio/SOUNDICONS/UI-UX/SELECT/Select0001.wav", 0.4, -16.0),
    "toggle_on": ("Audio/SOUNDICONS/UI-UX/TURN/Turn0041.wav", 0.5, -15.0),
    "toggle_off": ("Audio/SOUNDICONS/UI-UX/CANCEL/Cancel0001.wav", 0.4, -15.0),
    "confirm": ("Audio/SOUNDICONS/UI-UX/DECISION/Decision0157.wav", 0.6, -13.0),
    "open": ("Audio/SOUNDICONS/UI-UX/OPEN/Open0043.wav", 0.7, -14.0),
    "close": ("Audio/SOUNDICONS/UI-UX/CLOSE/Close0042.wav", 0.5, -14.0),
    "error": ("Audio/SOUNDICONS/UI-UX/ALARM/Alarm0095.wav", 0.6, -13.0),
    "startup": ("Audio/Helton Yan's Tranquility/MUSCMisc_Minor-Sleek Startup_HY_Trnqlty.wav", 2.8, -14.0),
    "notify": ("Audio/Helton Yan's Tranquility/MUSCMisc_Major-Tweeting Notification_HY_Trnqlty.wav", 1.8, -14.0),
}


def build_sounds(src: Path) -> None:
    SOUND_OUT.mkdir(parents=True, exist_ok=True)
    for name, (relative, max_seconds, peak_db) in SOUNDS.items():
        audio, rate = read_wav(src / relative)
        write_sound(audio, rate, name, max_seconds, peak_db)


def main() -> None:
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else PROJECT.parent / "ASSETS COOL"
    build_images(src)
    build_sounds(src)
    print(f"Listo: {len(list(UI_OUT.glob('*.png')))} imagenes y {len(list(SOUND_OUT.glob('*.wav')))} sonidos.")


if __name__ == "__main__":
    main()
