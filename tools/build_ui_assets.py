"""Genera los recursos visuales y sonoros de la interfaz a partir de "ASSETS COOL".

Uso (solo al cambiar el tema; la app usa los PNG/WAV ya generados en visual/):
    pip install -r tools/requirements-build.txt
    python tools/build_ui_assets.py "C:/ruta/a/ASSETS COOL"

Todo sale de assets existentes: los botones "pill" se arman estirando los orbes
de vidrio del Frutiger Aero Archive, los iconos son Heroicons rasterizados y los
sonidos son notas de "Tranquility" (Helton Yan) recortadas y normalizadas.
Las imagenes se generan en varias escalas (visual/ui/<porcentaje>/) para que se
vean nitidas con el escalado de pantalla de Windows.
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
# Escalas de Windows (100 % = 96 DPI). La app elige la mas cercana a la pantalla.
SCALES = (100, 125, 150, 175, 200)

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


def save(image: Image.Image, out_dir: Path, name: str) -> None:
    image.save(out_dir / f"{name}.png", optimize=True)


def build_images(src: Path, percent: int) -> None:
    """Genera todo el set de imagenes para una escala de pantalla (100 = 96 DPI).

    Cada escala se dibuja desde el original en alta resolucion: asi la app nunca
    estira bitmaps y se ve nitida a 125 %, 150 %, etc.
    """
    k = percent / 100
    out = UI_OUT / str(percent)
    out.mkdir(parents=True, exist_ok=True)

    def px(value: float) -> int:
        return max(1, round(value * k))

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
    header = cover(sky, px(1920), px(112), 0.0)
    header = gradient_tint(header, (109, 63, 196), 0.62, 0.0)
    save(gloss(header, 0.22), out, "header")

    status = cover(load(src, f"{archive}/skyboxes/skyboxes_6.png"), px(1920), px(30), 0.85)
    save(gradient_tint(gloss(status, 0.35), (109, 63, 196), 0.10, 0.0), out, "statusbar")

    bubbles = crop_alpha(load(src, f"{archive}/bubbles/bubbles_10.png"))
    save(resize(bubbles, (round(bubbles.width * px(104) / bubbles.height), px(104))), out, "header_bubbles")

    glow = crop_alpha(load(src, f"{archive}/flares/flares_19.png"))
    speaker = crop_alpha(load(src, f"{archive}/objects/objects_13.png"))
    logo = Image.new("RGBA", (px(96), px(96)), (0, 0, 0, 0))
    logo.alpha_composite(resize(square(glow), (px(96), px(96))))
    logo.alpha_composite(resize(square(speaker), (px(66), px(66))), (px(15), px(15)))
    save(logo, out, "logo")
    save(resize(square(speaker), (px(32), px(32))), out, "app_icon")
    if percent == 100:
        # Icono del ejecutable y de los instaladores (ICO real, multi-tamano).
        resize(square(speaker), (256, 256)).save(
            PROJECT / "visual" / "app.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
        )

    # Botones pill (normal / hover / pressed / disabled) a partir de los orbes.
    for name, source in (("blue", blue_orb), ("light", light_orb), ("purple", purple_orb)):
        base = pill_from_orb(source, px(120), px(32))
        save(base, out, f"pill_{name}")
        save(brightness(base, 1.12), out, f"pill_{name}_hover")
        save(brightness(base, 0.85), out, f"pill_{name}_pressed")
        save(disabled(base), out, f"pill_{name}_disabled")

    tab = pill_from_orb(light_orb, px(110), px(30))
    save(tab, out, "tab")
    save(brightness(tab, 1.08), out, "tab_hover")
    save(pill_from_orb(purple_orb, px(110), px(30)), out, "tab_selected")

    # Botones redondos de reproduccion: orbe + icono blanco.
    for action, icon_name, source in (("play", "play", blue_orb), ("pause", "pause", cyan_orb), ("stop", "stop", purple_orb)):
        face = overlay_center(orb(source, px(38)), svg_icon(src, icon_name, px(16), ICON_WHITE), dy=px(1))
        save(face, out, f"orb_{action}")
        save(brightness(face, 1.12), out, f"orb_{action}_hover")
        save(brightness(face, 0.85), out, f"orb_{action}_pressed")

    # Casillas: burbuja vacia / orbe azul con check.
    def with_gap(image: Image.Image) -> Image.Image:
        # Margen transparente a la derecha: separa la casilla de su texto.
        canvas = Image.new("RGBA", (image.width + px(6), image.height), (0, 0, 0, 0))
        canvas.alpha_composite(image)
        return canvas

    bubble = orb(load(src, f"{archive}/bubbles/bubbles_22.png"), px(20))
    save(with_gap(bubble), out, "check_off")
    save(with_gap(overlay_center(orb(blue_orb, px(20)), svg_icon(src, "check", px(13), ICON_WHITE))), out, "check_on")
    save(with_gap(brightness(bubble, 1.1)), out, "check_off_hover")

    # Slider: perilla de orbe morado y riel claro.
    save(orb(purple_orb, px(18)), out, "slider_thumb")
    save(brightness(orb(purple_orb, px(18)), 1.15), out, "slider_thumb_hover")
    # El riel va centrado en un lienzo de la altura de la perilla, asi ttk no lo deforma.
    trough = Image.new("RGBA", (px(60), px(18)), (0, 0, 0, 0))
    trough.alpha_composite(pill_from_orb(light_orb, px(60), px(8)), (0, px(5)))
    save(trough, out, "slider_trough")

    save(orb(green_orb, px(14)), out, "led_on")
    save(orb(red_orb, px(14)), out, "led_off")

    # Iconos de pestanas y botones.
    for name in ("musical-note", "sparkles", "command-line", "cog-6-tooth", "cpu-chip", "microphone", "speaker-wave",
                 "question-mark-circle"):
        save(svg_icon(src, name, px(18), ICON_DARK), out, f"icon_{name}")
    for name in ("arrow-path", "folder-plus", "plus", "arrow-down-tray", "check", "microphone", "bolt",
                 "arrow-top-right-on-square", "book-open", "heart", "exclamation-triangle", "folder-open",
                 "clipboard-document", "trash"):
        save(svg_icon(src, name, px(16), ICON_WHITE), out, f"icon_{name}_white")
        save(svg_icon(src, name, px(16), ICON_DARK), out, f"icon_{name}_dark")
    save(svg_icon(src, "microphone", px(18), ICON_PURPLE), out, "icon_microphone_purple")


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


def write_sound(audio: np.ndarray, rate: int, name: str, max_seconds: float, peak_db: float, fade_seconds: float) -> None:
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
    # Fundido exponencial: la nota "se apaga" en vez de cortarse en seco.
    fade = min(len(audio), int(fade_seconds * 44100))
    audio[-fade:] *= (np.linspace(1, 0, fade) ** 2)[:, None]
    audio *= (10 ** (peak_db / 20)) / max(np.abs(audio).max(), 1e-6)
    with wave.open(str(SOUND_OUT / f"{name}.wav"), "wb") as handle:
        handle.setnchannels(2)
        handle.setsampwidth(2)
        handle.setframerate(44100)
        handle.writeframes((audio * 32767).astype("<i2").tobytes())


TRANQUILITY = "Audio/Helton Yan's Tranquility/MUSCMisc_"

SOUNDS = {
    # Todo sale de "Tranquility" (Helton Yan, estetica Frutiger Aero). Cada muestra es
    # una frase larga: se toma solo la primera nota, recortada y con fundido.
    # nombre: (archivo, duracion maxima s, pico dBFS, fundido s)
    "click": (TRANQUILITY + "Major-Aqua Pluck_HY_Trnqlty.wav", 0.35, -18.0, 0.18),
    "toggle_on": (TRANQUILITY + "Major-Glassy Gloss_HY_Trnqlty.wav", 0.40, -17.0, 0.20),
    "toggle_off": (TRANQUILITY + "Minor-Underwater Pluck_HY_Trnqlty.wav", 0.35, -17.0, 0.18),
    "confirm": (TRANQUILITY + "Minor-Lone Bell_HY_Trnqlty.wav", 0.70, -16.0, 0.35),
    "open": (TRANQUILITY + "Major-Short Hover_HY_Trnqlty.wav", 0.80, -17.0, 0.35),
    "close": (TRANQUILITY + "Major-Glassy Pluck_HY_Trnqlty.wav", 0.45, -18.0, 0.25),
    "error": (TRANQUILITY + "Major-Bell Dissonance_HY_Trnqlty.wav", 0.80, -15.0, 0.35),
    "startup": (TRANQUILITY + "Major-Breathy Startup_HY_Trnqlty.wav", 2.60, -15.0, 0.90),
    "notify": (TRANQUILITY + "Major-Tweeting Notification_HY_Trnqlty.wav", 0.75, -15.0, 0.25),
}


def build_sounds(src: Path) -> None:
    SOUND_OUT.mkdir(parents=True, exist_ok=True)
    for name, (relative, max_seconds, peak_db, fade_seconds) in SOUNDS.items():
        audio, rate = read_wav(src / relative)
        write_sound(audio, rate, name, max_seconds, peak_db, fade_seconds)


def main() -> None:
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else PROJECT.parent / "ASSETS COOL"
    for stale in UI_OUT.glob("*.png"):  # formato anterior (una sola escala)
        stale.unlink()
    for percent in SCALES:
        build_images(src, percent)
    build_sounds(src)
    print(f"Listo: {len(list(UI_OUT.rglob('*.png')))} imagenes en {len(SCALES)} escalas y {len(list(SOUND_OUT.glob('*.wav')))} sonidos.")


if __name__ == "__main__":
    main()
