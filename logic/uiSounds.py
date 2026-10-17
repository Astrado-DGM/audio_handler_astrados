"""Sonidos de la interfaz (clics, confirmaciones, avisos).

Se reproducen con winsound por la salida predeterminada de Windows, NUNCA por el
mixer de pygame: ese va al cable virtual y Discord escucharia cada clic.
"""

from __future__ import annotations

from pathlib import Path

try:
    import winsound
except ImportError:  # fuera de Windows simplemente no suenan
    winsound = None


class UiSounds:
    def __init__(self, sounds_dir: Path, enabled: bool = True) -> None:
        self.sounds_dir = Path(sounds_dir)
        self.enabled = enabled

    def play(self, name: str) -> None:
        if not self.enabled or winsound is None:
            return
        path = self.sounds_dir / f"{name}.wav"
        if not path.exists():
            return
        try:
            # SND_ASYNC corta el sonido anterior: un clic seguido de "confirm" no se superpone.
            winsound.PlaySound(str(path), winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
        except RuntimeError:
            pass
