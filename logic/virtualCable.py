"""Deteccion del cable de audio virtual que Discord usa como microfono.

Windows solo deja crear dispositivos de entrada a un driver, asi que la app no
puede "ser" un microfono por si sola: necesita VB-Cable (o Voicemeeter). Este
modulo detecta si esta instalado y cual es la salida donde hay que mandar la mezcla.
"""

from __future__ import annotations

from dataclasses import dataclass

try:
    import sounddevice as sd
except ImportError:
    sd = None


VB_CABLE_DOWNLOAD_URL = "https://vb-audio.com/Cable/"

# (prefijo de la salida donde la app escribe, nombre de la entrada que se elige en Discord)
KNOWN_CABLES: tuple[tuple[str, str], ...] = (
    ("CABLE Input", "CABLE Output"),
    ("CABLE-A Input", "CABLE-A Output"),
    ("CABLE-B Input", "CABLE-B Output"),
    ("VoiceMeeter Input", "Voicemeeter Out B1"),
    ("VoiceMeeter Aux Input", "Voicemeeter Out B2"),
)

# MME primero: es la API que el resto de la app usa por defecto.
_HOSTAPI_PREFERENCE = ("MME", "Windows WASAPI", "Windows DirectSound", "Windows WDM-KS")


@dataclass(frozen=True)
class VirtualCableStatus:
    installed: bool
    playback_index: int | None = None
    playback_name: str = ""
    discord_input_name: str = ""

    @property
    def summary(self) -> str:
        if not self.installed:
            return "Cable virtual: NO detectado. Discord no podra recibir la mezcla."
        return f"Cable virtual: detectado. La app sale por '{self.playback_name}'; en Discord elige '{self.discord_input_name}'."


def rescan_devices() -> None:
    """PortAudio solo enumera dispositivos al iniciar; esto lo reinicia para ver un cable recien instalado.

    Todos los streams de sounddevice deben estar cerrados antes de llamarla.
    """
    if sd is None:
        return
    sd._terminate()
    sd._initialize()


def detect_virtual_cable() -> VirtualCableStatus:
    if sd is None:
        return VirtualCableStatus(installed=False)

    try:
        devices = sd.query_devices()
        hostapis = sd.query_hostapis()
    except Exception:
        return VirtualCableStatus(installed=False)

    def hostapi_rank(device: dict) -> int:
        name = str(hostapis[int(device.get("hostapi", 0))].get("name", ""))
        return _HOSTAPI_PREFERENCE.index(name) if name in _HOSTAPI_PREFERENCE else len(_HOSTAPI_PREFERENCE)

    for playback_prefix, discord_input in KNOWN_CABLES:
        matches = [
            (index, device)
            for index, device in enumerate(devices)
            if int(device.get("max_output_channels", 0) or 0) > 0
            and str(device.get("name", "")).casefold().startswith(playback_prefix.casefold())
        ]
        if matches:
            index, device = min(matches, key=lambda item: hostapi_rank(item[1]))
            return VirtualCableStatus(
                installed=True,
                playback_index=index,
                playback_name=str(device.get("name", "")),
                discord_input_name=discord_input,
            )

    return VirtualCableStatus(installed=False)


def is_virtual_cable_name(device_label: str | None) -> bool:
    if not device_label:
        return False
    # Las etiquetas de la UI llevan "[indice] " al inicio.
    text = device_label.split("] ", 1)[-1].casefold()
    return any(text.startswith(prefix.casefold()) for prefix, _discord_input in KNOWN_CABLES)
