from __future__ import annotations

import logging
import math
import threading
import time
from pathlib import Path
from typing import Any
import re
import unicodedata

import numpy as np

from logic.errorLog import RateLimitedLogger
from logic.micFilter import MicFilterSettings, MicProcessor

log = logging.getLogger("astrados.audio")

# Pseudo-dispositivos de PortAudio que significan "la salida predeterminada de Windows".
WINDOWS_DEFAULT_OUTPUTS = {
    "asignadordesonidomicrosoftoutput",
    "microsoftsoundmapperoutput",
    "controladorprimariodesonido",
    "primarysounddriver",
}
_stream_log = RateLimitedLogger(log)
MUSIC_CHANNEL = 0
MUSIC_MODES = ("playlist", "shuffle", "repeat_one", "once")

try:
    import pygame
except ImportError:
    pygame = None

try:
    import sounddevice as sd
except ImportError:
    sd = None


def _normalize_name(value: str | None) -> str:
    if not value:
        return ""

    normalized = unicodedata.normalize("NFKD", value)
    normalized = "".join(character for character in normalized if not unicodedata.combining(character))
    return re.sub(r"[^a-z0-9]+", "", normalized.casefold())


def _extract_device_index(device_name: str | int | None) -> int | None:
    if device_name is None:
        return None

    if isinstance(device_name, int):
        return device_name

    match = re.match(r"^\[(\d+)\]", str(device_name).strip())
    if match:
        return int(match.group(1))

    return None


def _strip_device_label(device_name: str | int | None) -> str:
    if device_name is None:
        return ""

    if isinstance(device_name, int):
        return str(device_name)

    text = str(device_name).strip()
    text = re.sub(r"^\[\d+\]\s*", "", text)
    metadata_index = text.find(" (in:")
    if metadata_index != -1:
        text = text[:metadata_index]
    return text.strip()


def _parse_device_label(device_name: str | int | None) -> tuple[int | None, str, str | None]:
    """Separa "[5] CABLE Input (VB-Audio Virtual C (in:0 out:16) (MME)" en (5, nombre, host API)."""
    if device_name is None:
        return None, "", None
    if isinstance(device_name, int):
        return device_name, "", None

    text = str(device_name).strip()
    index = _extract_device_index(text)
    hostapi_name: str | None = None
    metadata_index = text.find(" (in:")
    if metadata_index != -1:
        hostapi_match = re.search(r"\(in:\d+ out:\d+\)\s*\(([^()]*)\)\s*$", text)
        if hostapi_match:
            hostapi_name = hostapi_match.group(1).strip() or None
    return index, _strip_device_label(text), hostapi_name


def _device_has_kind(device: dict[str, Any], kind: str) -> bool:
    key = "max_input_channels" if kind == "input" else "max_output_channels"
    return int(device.get(key, 0) or 0) > 0


def _hostapi_name(hostapi_index: int) -> str:
    try:
        return str(sd.query_hostapis(hostapi_index).get("name", ""))
    except Exception:
        return ""


def _names_match(target: str, candidate: str) -> bool:
    # MME recorta los nombres a 31 caracteres, por eso se acepta un prefijo.
    return bool(target) and (target == candidate or candidate.startswith(target) or target.startswith(candidate))


def resolve_device(device_name: str | int | None, kind: str, hostapi: int | None = None) -> int | None:
    """Resuelve un dispositivo guardado por nombre (y host API), no por indice.

    Los indices de PortAudio cambian al conectar o quitar dispositivos, asi que el
    indice guardado solo se usa para desempatar entre dispositivos del mismo nombre.
    Devuelve None si no hay coincidencia, en lugar de adivinar otro dispositivo.
    """
    if sd is None or device_name is None:
        return None

    try:
        devices = sd.query_devices()
    except Exception:
        return None

    saved_index, name, hostapi_name = _parse_device_label(device_name)
    if not name:
        if saved_index is not None and 0 <= saved_index < len(devices) and _device_has_kind(devices[saved_index], kind):
            return saved_index
        return None

    target = _normalize_name(name)
    matches = [
        index
        for index, device in enumerate(devices)
        if _device_has_kind(device, kind) and _names_match(target, _normalize_name(str(device.get("name", ""))))
    ]
    if not matches:
        return None

    if hostapi is not None:
        same_hostapi = [index for index in matches if int(devices[index].get("hostapi", -1)) == int(hostapi)]
        if not same_hostapi:
            return None
        matches = same_hostapi
    elif hostapi_name:
        same_hostapi = [index for index in matches if _hostapi_name(int(devices[index].get("hostapi", -1))) == hostapi_name]
        matches = same_hostapi or matches

    if saved_index in matches:
        return saved_index
    exact = [index for index in matches if _normalize_name(str(devices[index].get("name", ""))) == target]
    return (exact or matches)[0]


def _pygame_output_candidates(device_name: str | int | None) -> list[str]:
    """Nombres a probar con pygame/SDL, que usa los nombres completos (no los de MME)."""
    if sd is None or device_name is None:
        return []

    _saved_index, name, _hostapi_name = _parse_device_label(device_name)
    if not name:
        resolved = resolve_device(device_name, "output")
        if resolved is None:
            return []
        name = str(sd.query_devices(resolved).get("name", ""))

    target = _normalize_name(name)
    try:
        devices = sd.query_devices()
    except Exception:
        return [name]

    candidate_names = {
        str(device.get("name", "")).strip()
        for device in devices
        if _device_has_kind(device, "output") and _names_match(target, _normalize_name(str(device.get("name", ""))))
    }
    candidate_names.add(name)
    # Primero los nombres mas largos: son los completos que reporta WASAPI/DirectSound.
    return sorted((candidate for candidate in candidate_names if candidate), key=len, reverse=True)


def _sound_to_float32_array(sound: Any) -> np.ndarray | None:
    if pygame is None or sound is None:
        return None

    try:
        array = pygame.sndarray.array(sound)
    except Exception:
        return None

    if array.ndim == 1:
        array = array[:, None]

    if np.issubdtype(array.dtype, np.integer):
        max_value = float(max(abs(np.iinfo(array.dtype).min), np.iinfo(array.dtype).max))
        if max_value <= 0:
            max_value = 1.0
        array = array.astype(np.float32) / max_value
    else:
        array = array.astype(np.float32)

    return array


def music_mode_from_config(config: dict[str, Any]) -> str:
    mode = config.get("music_mode")
    return mode if mode in MUSIC_MODES else "playlist"


class _Voice:
    __slots__ = ("samples", "position", "loop", "volume", "paused")

    def __init__(self, samples: np.ndarray, position: int, loop: bool, volume: float) -> None:
        self.samples = samples
        self.position = position
        self.loop = loop
        self.volume = volume
        self.paused = False


class MonitorMixer:
    """Una sola salida de monitor que suma todas las voces (musica y cada SFX).

    Antes cada reproductor abria su propio stream; al cambiar de dispositivo los
    reproductores viejos se reemplazaban sin cerrarlos y seguian sonando para siempre.
    Aqui hay un unico stream: cambiar de dispositivo lo cierra y lo reabre.
    """

    def __init__(self, sample_rate: int = 44100) -> None:
        self.sample_rate = sample_rate
        self.device: str | None = None
        self.enabled = False
        self.stream: Any | None = None
        self._voices: dict[int, _Voice] = {}
        self._lock = threading.Lock()
        self._next_id = 1

    def configure(self, enabled: bool, device: str | None) -> bool:
        """Devuelve True si el stream se (re)abrio y las voces anteriores se descartaron."""
        if not enabled or device is None or sd is None:
            self.close()
            self.enabled = False
            self.device = device
            return False
        if self.stream is not None and self.enabled and device == self.device:
            return False
        self.close()
        self.device = device
        self.enabled = True
        self._open()
        return True

    def _open(self) -> None:
        resolved = resolve_device(self.device, "output")
        if resolved is None:
            log.warning("Monitor: no se encontro el dispositivo de salida %r.", self.device)
            return
        try:
            self.stream = sd.OutputStream(device=resolved, samplerate=self.sample_rate, channels=2,
                                          dtype="float32", callback=self._callback)
            self.stream.start()
        except Exception as error:
            log.error("Monitor: no se pudo abrir la salida %r: %s", self.device, error)
            self.stream = None

    def close(self) -> None:
        stream, self.stream = self.stream, None
        if stream is not None:
            try:
                stream.stop()
            finally:
                stream.close()
        with self._lock:
            self._voices.clear()

    @property
    def active(self) -> bool:
        return self.stream is not None

    def add(self, samples: np.ndarray | None, start_seconds: float = 0.0, loop: bool = False, volume: float = 1.0) -> int | None:
        if self.stream is None or samples is None or samples.size == 0:
            return None
        position = max(0, int(start_seconds * self.sample_rate))
        if not loop and position >= samples.shape[0]:
            return None
        with self._lock:
            voice_id = self._next_id
            self._next_id += 1
            self._voices[voice_id] = _Voice(samples, position % samples.shape[0], loop, volume)
        return voice_id

    def remove(self, voice_id: int | None) -> None:
        if voice_id is not None:
            with self._lock:
                self._voices.pop(voice_id, None)

    def set_volume(self, voice_id: int | None, volume: float) -> None:
        voice = self._voices.get(voice_id) if voice_id is not None else None
        if voice is not None:
            voice.volume = volume

    def set_paused(self, voice_id: int | None, paused: bool) -> None:
        voice = self._voices.get(voice_id) if voice_id is not None else None
        if voice is not None:
            voice.paused = paused

    def set_loop(self, voice_id: int | None, loop: bool) -> None:
        voice = self._voices.get(voice_id) if voice_id is not None else None
        if voice is not None:
            voice.loop = loop

    def voice_count(self) -> int:
        return len(self._voices)

    def _callback(self, outdata, frames, time_info, status):
        if status:
            _stream_log.warning("monitor", f"Monitor: {status}")
        outdata.fill(0)
        with self._lock:
            voices = list(self._voices.items())
        finished: list[int] = []
        for voice_id, voice in voices:
            if voice.paused:
                continue
            samples = voice.samples
            total = samples.shape[0]
            start = voice.position
            if voice.loop:
                chunk = samples[np.arange(start, start + frames) % total]
                voice.position = (start + frames) % total
            else:
                chunk = samples[start:start + frames]
                voice.position = start + chunk.shape[0]
                if voice.position >= total:
                    finished.append(voice_id)
            outdata[: chunk.shape[0]] += chunk * voice.volume
        np.clip(outdata, -1.0, 1.0, out=outdata)
        if finished:
            with self._lock:
                for voice_id in finished:
                    self._voices.pop(voice_id, None)


def _stereo_float(sound: Any) -> np.ndarray | None:
    array = _sound_to_float32_array(sound)
    if array is None:
        return None
    if array.shape[1] == 1:
        array = np.repeat(array, 2, axis=1)
    return np.ascontiguousarray(array[:, :2])


class PygameOutput:
    """Inicializacion del mixer global de pygame sobre la salida objetivo."""

    _mixer_ready = False
    _current_device: str | None = None
    # Nombre del dispositivo que pygame abrio realmente, o None si uso el de Windows.
    opened_device: str | None = None
    fallback_warning: str | None = None
    CHANNELS = 32  # musica + muchos SFX superpuestos

    @classmethod
    def ensure(cls, output_device: str | None) -> None:
        if pygame is None:
            cls._mixer_ready = False
            cls._current_device = output_device
            return

        if not pygame.mixer.get_init():
            mixer_kwargs: dict[str, Any] = {"frequency": 44100, "size": -16, "channels": 2, "buffer": 1024}
            cls.opened_device = None
            cls.fallback_warning = None
            # Los alias de "salida predeterminada" de Windows no existen para SDL:
            # se abre la predeterminada directamente y no es un error.
            uses_default = _normalize_name(_strip_device_label(output_device)) in WINDOWS_DEFAULT_OUTPUTS
            candidates = [] if uses_default else _pygame_output_candidates(output_device)
            for candidate_name in candidates:
                try:
                    pygame.mixer.init(devicename=candidate_name, **mixer_kwargs)
                    cls.opened_device = candidate_name
                    break
                except (TypeError, pygame.error):
                    if pygame.mixer.get_init():
                        pygame.mixer.quit()

            if cls.opened_device is None:
                try:
                    pygame.mixer.init(**mixer_kwargs)
                except pygame.error:
                    pygame.mixer.init()
                if output_device and not uses_default:
                    log.warning("pygame no pudo abrir %r; se usa la salida predeterminada.", output_device)
                    cls.fallback_warning = (
                        f"No se pudo abrir {_strip_device_label(output_device)!r} para musica/SFX; "
                        "suenan por la salida predeterminada de Windows."
                    )

        pygame.mixer.set_num_channels(cls.CHANNELS)
        # El canal 0 queda reservado para la musica: los SFX nunca se lo quitan.
        pygame.mixer.set_reserved(1)
        cls._mixer_ready = True
        cls._current_device = output_device

    @classmethod
    def reinitialize(cls, output_device: str | None) -> None:
        if pygame is None:
            cls._mixer_ready = False
            cls._current_device = output_device
            return
        if pygame.mixer.get_init():
            pygame.mixer.stop()
            pygame.mixer.quit()
        cls._mixer_ready = False
        cls.ensure(output_device)


class _SfxVoice:
    __slots__ = ("path", "sound", "channel", "loop", "monitor_id")

    def __init__(self, path: Path, sound: Any, channel: Any, loop: bool, monitor_id: int | None) -> None:
        self.path = path
        self.sound = sound
        self.channel = channel
        self.loop = loop
        self.monitor_id = monitor_id


class SfxPlayer:
    """Efectos superpuestos: cada Play suena una vez (o en loop) sin cortar a los demas."""

    def __init__(self, monitor: MonitorMixer, volume: float = 1.0) -> None:
        self.monitor = monitor
        self.volume = volume
        self.duck_gain = 1.0
        self.paused = False
        self._cache: dict[Path, tuple[Any, np.ndarray | None]] = {}
        self._voices: list[_SfxVoice] = []

    def effective_volume(self) -> float:
        return self.volume * self.duck_gain

    def _load(self, path: Path) -> tuple[Any, np.ndarray | None]:
        cached = self._cache.get(path)
        if cached is None:
            sound = pygame.mixer.Sound(str(path))
            cached = (sound, None)
            self._cache[path] = cached
        return cached

    def _monitor_samples(self, path: Path) -> np.ndarray | None:
        sound, samples = self._cache[path]
        if samples is None:
            samples = _stereo_float(sound)
            self._cache[path] = (sound, samples)
        return samples

    def clear_cache(self) -> None:
        """Tras reiniciar el mixer los Sound anteriores dejan de ser validos."""
        self.stop()
        self._cache.clear()

    def forget(self, path: Path) -> None:
        self.stop(path)
        self._cache.pop(Path(path), None)

    def play(self, path: str | Path, loop: bool = False) -> bool:
        if pygame is None:
            return False
        path = Path(path)
        sound, _samples = self._load(path)
        if loop:
            self.stop(path)  # un mismo efecto en loop no se duplica
        channel = pygame.mixer.find_channel(True)  # True: si no hay libres, reutiliza el mas antiguo
        if channel is None:
            return False
        channel.play(sound, loops=-1 if loop else 0)
        channel.set_volume(self.effective_volume())
        monitor_id = None
        if self.monitor.active:
            monitor_id = self.monitor.add(self._monitor_samples(path), loop=loop, volume=self.effective_volume())
        self._voices.append(_SfxVoice(path, sound, channel, loop, monitor_id))
        self.paused = False
        return True

    def _alive(self, voice: _SfxVoice) -> bool:
        channel = voice.channel
        return channel.get_busy() and channel.get_sound() is voice.sound

    def tick(self) -> None:
        if self.paused:
            return
        alive = []
        for voice in self._voices:
            if self._alive(voice):
                alive.append(voice)
            else:
                self.monitor.remove(voice.monitor_id)
        self._voices = alive

    def stop(self, path: str | Path | None = None) -> None:
        target = Path(path) if path is not None else None
        remaining = []
        for voice in self._voices:
            if target is None or voice.path == target:
                if self._alive(voice):
                    voice.channel.stop()
                self.monitor.remove(voice.monitor_id)
            else:
                remaining.append(voice)
        self._voices = remaining
        if not self._voices:
            self.paused = False

    def toggle_pause(self) -> None:
        if not self._voices:
            return
        self.paused = not self.paused
        for voice in self._voices:
            if voice.channel.get_sound() is voice.sound:
                voice.channel.pause() if self.paused else voice.channel.unpause()
            self.monitor.set_paused(voice.monitor_id, self.paused)

    def is_paused(self) -> bool:
        return self.paused

    def is_playing(self) -> bool:
        return any(self._alive(voice) for voice in self._voices)

    def active_paths(self) -> set[Path]:
        return {voice.path for voice in self._voices if self._alive(voice)}

    def looping_paths(self) -> set[Path]:
        return {voice.path for voice in self._voices if voice.loop and self._alive(voice)}

    def _apply_volume(self) -> None:
        volume = self.effective_volume()
        for voice in self._voices:
            if voice.channel.get_sound() is voice.sound:
                voice.channel.set_volume(volume)
            self.monitor.set_volume(voice.monitor_id, volume)

    def set_volume(self, volume: float) -> None:
        self.volume = max(0.0, min(1.0, float(volume)))
        self._apply_volume()

    def set_duck_gain(self, gain: float) -> None:
        self.duck_gain = max(0.0, min(1.0, float(gain)))
        self._apply_volume()

    def drop_monitor_voices(self) -> None:
        for voice in self._voices:
            voice.monitor_id = None


class SeekableMusicHandler:
    """Musica con seek y loop.

    Tras un seek se reproduce un Sound recortado (desde el punto elegido) y, si hay
    loop, se encola la pista completa justo antes de que termine. Asi el loop vuelve
    al inicio de la cancion y la posicion es simplemente (inicio + transcurrido) % duracion.
    Sin loop, al terminar la pista se marca `finished` para que la app pase a la siguiente.
    """

    # Margen para encolar la siguiente vuelta; tick() se llama varias veces por segundo.
    QUEUE_AHEAD_SECONDS = 1.5

    def __init__(self, monitor: MonitorMixer, volume: float = 1.0, loop: bool = False) -> None:
        self.monitor = monitor
        self.sound_file: Path | None = None
        self.sound: Any = None
        self.samples: np.ndarray | None = None
        self.playback_sound: Any = None
        self.channel: Any = None
        self.duration_seconds = 0.0
        self.loop = loop
        self.volume = volume
        self.duck_gain = 1.0
        self.paused = False
        self.finished = False
        self._monitor_samples: np.ndarray | None = None
        self._monitor_id: int | None = None
        self._play_started_at: float | None = None
        self._base_position_seconds = 0.0
        self._paused_position_seconds = 0.0

    def _elapsed_position_seconds(self) -> float:
        if self.paused:
            return self._paused_position_seconds
        if self._play_started_at is None:
            return self._base_position_seconds
        return self._base_position_seconds + max(0.0, time.monotonic() - self._play_started_at)

    def _current_position_seconds(self) -> float:
        position = self._elapsed_position_seconds()
        if self.duration_seconds > 0:
            if self.loop:
                return position % self.duration_seconds
            return min(position, self.duration_seconds)
        return max(0.0, position)

    def effective_volume(self) -> float:
        return self.volume * self.duck_gain

    def _apply_volume(self) -> None:
        if self.channel is not None:
            self.channel.set_volume(self.effective_volume())
        self.monitor.set_volume(self._monitor_id, self.effective_volume())

    def restart_monitor(self) -> None:
        """Vuelve a crear la voz de monitor (tras cambiar de dispositivo o activarlo)."""
        self.monitor.remove(self._monitor_id)
        self._monitor_id = None
        if not self.monitor.active or self.sound is None or self.channel is None:
            return
        if self._monitor_samples is None:
            self._monitor_samples = _stereo_float(self.sound)
        # La voz de monitor recorre la pista completa (no el tramo recortado), con su propio loop.
        self._monitor_id = self.monitor.add(self._monitor_samples, start_seconds=self._current_position_seconds(),
                                            loop=self.loop, volume=self.effective_volume())
        self.monitor.set_paused(self._monitor_id, self.paused)

    def _build_playback_sound(self, start_seconds: float = 0.0) -> Any | None:
        if self.sound is None or self.samples is None:
            return None

        start_frame = max(0, min(int(float(start_seconds) * 44100), int(self.samples.shape[0])))
        if start_frame == 0:
            return self.sound
        sliced_samples = np.ascontiguousarray(self.samples[start_frame:])
        if sliced_samples.size == 0:
            return None

        try:
            return pygame.sndarray.make_sound(sliced_samples)
        except Exception:
            return self.sound

    def set_source(self, soundFile: str | Path | None) -> None:
        self._monitor_samples = None
        if soundFile is None:
            self.stop()
            self.sound_file = None
            self.sound = None
            self.samples = None
            self.playback_sound = None
            self.duration_seconds = 0.0
            return

        self.sound_file = Path(soundFile)
        if pygame is None:
            self.sound = None
            self.samples = None
            self.playback_sound = None
            self.duration_seconds = 0.0
            return

        self.sound = pygame.mixer.Sound(str(self.sound_file))
        self.samples = pygame.sndarray.array(self.sound)
        self.playback_sound = self.sound
        self.duration_seconds = float(self.sound.get_length())

    def play(self, soundFile: str | Path | None = None, loop: bool | None = None, start_seconds: float = 0.0) -> None:
        if soundFile is not None:
            self.set_source(soundFile)
        if self.sound is None or pygame is None:
            return

        if loop is not None:
            self.loop = bool(loop)
        start_seconds = max(0.0, float(start_seconds))
        if self.duration_seconds > 0:
            start_seconds = min(start_seconds, self.duration_seconds)

        self.playback_sound = self._build_playback_sound(start_seconds)
        if self.playback_sound is None:
            return

        if self.channel is not None and self.channel.get_busy():
            self.channel.stop()

        # Siempre loops=0: el loop se arma encolando la pista completa en tick().
        self.channel = pygame.mixer.Channel(MUSIC_CHANNEL)
        self.channel.play(self.playback_sound, loops=0)
        self._base_position_seconds = start_seconds
        self._play_started_at = time.monotonic()
        self._paused_position_seconds = start_seconds
        self.paused = False
        self.finished = False
        self._apply_volume()
        self.restart_monitor()
        self.tick()

    def tick(self) -> None:
        """Encola la siguiente vuelta del loop, o detecta el final de la pista."""
        if self.paused or self.channel is None or self.sound is None or self.duration_seconds <= 0:
            return
        sounding = self.channel.get_busy() and self.channel.get_sound() in (self.playback_sound, self.sound)
        if not self.loop:
            if not sounding:
                self.monitor.remove(self._monitor_id)
                self._monitor_id = None
                self.channel = None
                self._play_started_at = None
                self._base_position_seconds = self.duration_seconds
                self.finished = True
            return
        if not sounding:
            # Se perdio la ventana para encolar (UI bloqueada): se retoma desde el inicio.
            self.play(loop=True, start_seconds=0.0)
            return
        remaining = self.duration_seconds - self._current_position_seconds()
        if remaining <= self.QUEUE_AHEAD_SECONDS and self.channel.get_queue() is None:
            self.channel.queue(self.sound)

    def consume_finished(self) -> bool:
        finished, self.finished = self.finished, False
        return finished

    def stop(self) -> None:
        if self.channel is not None and self.channel.get_sound() in (self.playback_sound, self.sound):
            self.channel.stop()
        self.monitor.remove(self._monitor_id)
        self._monitor_id = None
        self.paused = False
        self.finished = False
        self.channel = None
        self._play_started_at = None
        self._base_position_seconds = 0.0
        self._paused_position_seconds = 0.0

    def pause(self) -> None:
        if pygame is None or self.channel is None or not self.is_playing():
            return
        self._paused_position_seconds = self._current_position_seconds()
        self.channel.pause()
        self.monitor.set_paused(self._monitor_id, True)
        self.paused = True

    def resume(self) -> None:
        if pygame is None or self.sound is None or self.channel is None or not self.paused:
            return
        self.channel.unpause()
        self._base_position_seconds = self._paused_position_seconds
        self._play_started_at = time.monotonic()
        self.paused = False
        self.restart_monitor()

    def toggle_pause(self) -> None:
        if self.paused:
            self.resume()
        else:
            self.pause()

    def set_volume(self, volume: float) -> None:
        self.volume = max(0.0, min(1.0, float(volume)))
        self._apply_volume()

    def set_duck_gain(self, gain: float) -> None:
        self.duck_gain = max(0.0, min(1.0, float(gain)))
        self._apply_volume()

    def set_loop(self, loop: bool) -> None:
        loop = bool(loop)
        if loop == self.loop:
            return
        # La posicion se calcula distinto con y sin loop: se fija antes de cambiar el modo.
        position = self._current_position_seconds()
        self._base_position_seconds = position
        self._paused_position_seconds = position
        if self._play_started_at is not None:
            self._play_started_at = time.monotonic()
        self.loop = loop
        self.monitor.set_loop(self._monitor_id, self.loop)

    def seek(self, seconds: float) -> None:
        if self.sound is None:
            return
        keep_paused = self.paused
        # Sin pasar la ruta: re-decodificar el MP3 completo en cada seek era muy lento.
        self.play(start_seconds=seconds)
        if keep_paused:
            self.pause()

    def is_playing(self) -> bool:
        if pygame is None:
            return False
        return self.channel is not None and bool(self.channel.get_busy()) and not self.paused

    def is_paused(self) -> bool:
        return self.paused

    def get_state(self) -> dict[str, Any]:
        return {
            "source": self.sound_file,
            "position_seconds": self._current_position_seconds(),
            "duration_seconds": self.duration_seconds,
            "is_playing": self.is_playing(),
            "is_paused": self.paused,
            "loop": self.loop,
        }


class soundMotor:
    def __init__(self, config_manager) -> None:
        self.config_manager = config_manager
        self.config = config_manager.as_dict() if hasattr(config_manager, "as_dict") else dict(config_manager)
        self.max_channels = max(1, min(8, int(self.config.get("max_channels", 8))))
        self.sample_rate = int(self.config.get("sample_rate", 44100))
        self.buffer_size = int(self.config.get("buffer_size", 1024))
        self.output_device = self.config.get("output_device") or self.config.get("default_output_device")
        self.monitor_output_device = self.config.get("monitor_output_device") or self.output_device
        self.monitor_audio = bool(self.config.get("monitor_audio", False))
        self.mic_input_device = self.config.get("mic_input_device")
        self.mic_active = False
        self.mic_stream: Any | None = None
        self.mic_volume = float(self.config.get("volume_mic", 1.0))
        self.mic_filter_settings = MicFilterSettings.from_config(self.config)
        self.mic_processor: MicProcessor | None = None
        self.mic_error: str | None = None
        self.ducking_enabled = bool(self.config.get("mic_ducking", True))
        self.ducking_level = float(self.config.get("mic_ducking_level", 0.4))
        self._duck_gain = 1.0
        self._last_tick: float | None = None
        self.music_source: Path | None = None
        self.music_mode = music_mode_from_config(self.config)

        PygameOutput.reinitialize(self.output_device)
        self.monitor = MonitorMixer()
        self.monitor.configure(self.monitor_audio, self.monitor_output_device)
        self.music = SeekableMusicHandler(self.monitor, float(self.config.get("volume_music", 1.0)),
                                          loop=self.music_mode == "repeat_one")
        self.sfx = SfxPlayer(self.monitor, float(self.config.get("volume_sfx", 1.0)))

    def _device_info(self, index: int | None) -> dict[str, Any] | None:
        if index is None or sd is None:
            return None

        try:
            return dict(sd.query_devices(index))
        except Exception:
            return None

    def _selected_device_pair(self) -> tuple[int, int]:
        if sd is None:
            raise RuntimeError("sounddevice no esta disponible en este entorno.")

        output_index = resolve_device(self.output_device, "output")
        if output_index is None:
            raise ValueError(f"No se encontro la salida {_strip_device_label(self.output_device) or '(sin seleccionar)'!r}.")

        if self.mic_input_device:
            input_index = resolve_device(self.mic_input_device, "input")
            if input_index is None:
                raise ValueError(f"No se encontro el microfono {_strip_device_label(self.mic_input_device)!r}.")
        else:
            input_index = int(sd.default.device[0])
            if input_index < 0:
                raise ValueError("No hay microfono seleccionado ni microfono predeterminado en Windows.")

        output_hostapi = int(sd.query_devices(output_index).get("hostapi", -1))
        input_hostapi = int(sd.query_devices(input_index).get("hostapi", -1))
        if input_hostapi == output_hostapi:
            return input_index, output_index

        # sd.Stream exige la misma host API: se busca el MISMO dispositivo en la otra
        # API en lugar de caer al primero que aparezca (que podian ser los altavoces).
        same_input = resolve_device(self.mic_input_device or sd.query_devices(input_index)["name"], "input", hostapi=output_hostapi)
        if same_input is not None:
            return same_input, output_index
        same_output = resolve_device(self.output_device, "output", hostapi=input_hostapi)
        if same_output is not None:
            return input_index, same_output

        raise ValueError(
            f"El microfono ({_hostapi_name(input_hostapi)}) y la salida ({_hostapi_name(output_hostapi)}) "
            "usan host APIs distintas y no hay una version comun; elige ambos en la misma API (ej. MME o WASAPI)."
        )

    def _mic_callback(self, indata, outdata, frames, time_info, status):
        if status:
            _stream_log.warning("mic", f"Microfono: {status}")

        outdata.fill(0)
        if indata.size == 0:
            return

        # El microfono es mono en la practica: se promedia y se filtra una sola vez.
        mono = indata.mean(axis=1) if indata.ndim == 2 else indata
        processor = self.mic_processor
        if processor is not None:
            mono = processor.process(mono)

        outdata[:] = (mono * float(self.mic_volume))[:, None]

    def _open_mic_stream(self) -> None:
        input_index, output_index = self._selected_device_pair()
        input_device = sd.query_devices(input_index)
        output_device = sd.query_devices(output_index)
        if int(input_device.get("hostapi", -1)) != int(output_device.get("hostapi", -1)):
            raise ValueError(
                f"Los dispositivos seleccionados usan host APIs distintas: entrada={input_device.get('hostapi')} salida={output_device.get('hostapi')}."
            )

        # Basta con 2 canales: CABLE Input reporta hasta 16 en MME y abrirlos todos
        # solo duplica la voz en canales que Discord ignora.
        input_channels = max(1, min(2, int(input_device.get("max_input_channels", 1))))
        output_channels = max(1, min(2, int(output_device.get("max_output_channels", 2))))
        self.mic_processor = MicProcessor(self.sample_rate, self.mic_filter_settings)

        self.mic_stream = sd.Stream(
            device=(input_index, output_index),
            samplerate=self.sample_rate,
            blocksize=self.buffer_size,
            dtype="float32",
            channels=(input_channels, output_channels),
            callback=self._mic_callback,
        )

    def start_mic(self) -> bool:
        if self.mic_stream is not None:
            return True

        try:
            self._open_mic_stream()
            assert self.mic_stream is not None
            self.mic_stream.start()
        except Exception as error:
            if self.mic_stream is not None:
                try:
                    self.mic_stream.close()
                finally:
                    self.mic_stream = None
            self.mic_active = False
            self.mic_error = str(error)
            if hasattr(self.config_manager, "updateConfig"):
                self.config_manager.updateConfig({"mic_active": False})
            log.error("No se pudo iniciar el microfono: %s", error)
            return False

        self.mic_error = None
        self.mic_active = True
        if hasattr(self.config_manager, "updateConfig"):
            self.config_manager.updateConfig({"mic_active": True})
        return True

    def stop_mic(self) -> None:
        if self.mic_stream is not None:
            try:
                self.mic_stream.stop()
            finally:
                self.mic_stream.close()
                self.mic_stream = None
        self.mic_active = False
        if hasattr(self.config_manager, "updateConfig"):
            self.config_manager.updateConfig({"mic_active": False})

    def update_mic_filter(self, current_config: dict[str, Any]) -> None:
        self.mic_filter_settings = MicFilterSettings.from_config(current_config)
        if self.mic_processor is not None:
            self.mic_processor.update_settings(self.mic_filter_settings)
        self.ducking_enabled = bool(current_config.get("mic_ducking", True))
        self.ducking_level = max(0.0, min(1.0, float(current_config.get("mic_ducking_level", 0.4))))

    @property
    def playback_warning(self) -> str | None:
        return PygameOutput.fallback_warning

    def is_voice_active(self) -> bool:
        processor = self.mic_processor
        return bool(self.mic_active and processor is not None and processor.voice_detected)

    def tick(self) -> None:
        """Llamar periodicamente (~50 ms) desde la UI: loop de musica y ducking."""
        now = time.monotonic()
        dt = 0.05 if self._last_tick is None else min(0.5, now - self._last_tick)
        self._last_tick = now

        self.music.tick()
        self.sfx.tick()

        target = self.ducking_level if self.ducking_enabled and self.is_voice_active() else 1.0
        # Baja rapido al empezar a hablar (~60 ms) y vuelve suave al callar (~400 ms).
        time_constant = 0.06 if target < self._duck_gain else 0.4
        self._duck_gain += (target - self._duck_gain) * (1.0 - math.exp(-dt / time_constant))
        if abs(self._duck_gain - target) < 0.005:
            self._duck_gain = target
        for handler in (self.music, self.sfx):
            if abs(handler.duck_gain - self._duck_gain) > 0.001:
                handler.set_duck_gain(self._duck_gain)

    def release_portaudio_streams(self) -> bool:
        """Cierra todo stream de sounddevice (para re-escanear dispositivos). Devuelve si el mic estaba activo."""
        mic_was_active = self.mic_active
        self.stop_mic()
        self.monitor.close()
        self.sfx.drop_monitor_voices()
        return mic_was_active

    def shutdown(self) -> None:
        self.stop_mic()
        self.music.stop()
        self.sfx.stop()
        self.monitor.close()
        if pygame is not None and pygame.mixer.get_init():
            pygame.mixer.quit()

    def get_mic_levels(self) -> dict[str, Any] | None:
        processor = self.mic_processor
        if processor is None or not self.mic_active:
            return None
        return {
            "input_db": processor.input_level_db,
            "output_db": processor.output_level_db,
            "gate_open": processor.gate_open,
            "voice": processor.voice_detected,
            "duck_gain": self._duck_gain,
        }

    def set_music_source(self, sound_file: str | Path | None) -> None:
        self.music_source = Path(sound_file) if sound_file is not None else None
        self.music.set_source(self.music_source)

    def define_output_device(self, device_name: str | None) -> None:
        self.output_device = device_name or self.config.get("default_output_device")
        self.config["output_device"] = self.output_device
        if hasattr(self.config_manager, "updateConfig"):
            self.config_manager.updateConfig({"output_device": self.output_device})
        self._rebuild_playback_engine()

    def define_input_device(self, device_name: str | None) -> None:
        self.mic_input_device = device_name
        self.config["mic_input_device"] = device_name
        if hasattr(self.config_manager, "updateConfig"):
            self.config_manager.updateConfig({"mic_input_device": device_name})
        if self.mic_active:
            self.stop_mic()
            self.start_mic()

    def _rebuild_playback_engine(self) -> None:
        """Reabre pygame en otra salida conservando la musica (los SFX en curso se cortan)."""
        music_state = self.music.get_state()
        music_was_active = bool(music_state.get("is_playing") or music_state.get("is_paused"))
        music_position = float(music_state.get("position_seconds", 0.0))
        music_paused = bool(music_state.get("is_paused"))

        # Primero se detiene todo lo viejo: antes quedaba sonando y se acumulaba.
        self.music.stop()
        self.sfx.clear_cache()
        PygameOutput.reinitialize(self.output_device)

        if self.music_source is not None:
            self.music.set_source(self.music_source)
            if music_was_active:
                self.music.play(start_seconds=music_position)
                if music_paused:
                    self.music.pause()

    def set_music_mode(self, mode: str) -> None:
        self.music_mode = mode if mode in MUSIC_MODES else "playlist"
        self.music.set_loop(self.music_mode == "repeat_one")

    def apply_config(self, current_config: dict[str, Any]) -> None:
        self.config = dict(current_config)
        self.sample_rate = int(self.config.get("sample_rate", 44100))
        self.buffer_size = int(self.config.get("buffer_size", 1024))
        self.mic_volume = float(self.config.get("volume_mic", 1.0))
        self.monitor_audio = bool(self.config.get("monitor_audio", False))
        self.update_mic_filter(self.config)
        self.set_music_mode(music_mode_from_config(self.config))
        self.music.set_volume(float(self.config.get("volume_music", 1.0)))
        self.sfx.set_volume(float(self.config.get("volume_sfx", 1.0)))

        output_device = self.config.get("output_device") or self.config.get("default_output_device")
        monitor_output_device = self.config.get("monitor_output_device") or output_device
        input_device = self.config.get("mic_input_device")

        if output_device != self.output_device:
            self.output_device = output_device
            self._rebuild_playback_engine()

        self.monitor_output_device = monitor_output_device
        if self.monitor.configure(self.monitor_audio, monitor_output_device):
            # El mezclador se reabrio: la musica vuelve a sonar en el monitor nuevo, en su posicion.
            self.sfx.drop_monitor_voices()
            self.music.restart_monitor()
        elif not self.monitor.active:
            self.sfx.drop_monitor_voices()

        if input_device != self.mic_input_device:
            self.mic_input_device = input_device
            if self.mic_active:
                self.stop_mic()
                self.start_mic()

        if self.config.get("mic_active", False) and not self.mic_active:
            self.start_mic()
        elif not self.config.get("mic_active", False) and self.mic_active:
            self.stop_mic()

    def update(self, current_config: dict[str, Any]) -> None:
        self.apply_config(current_config)

    def play_sfx(self, sound_file: str | Path, loop: bool = False) -> bool:
        return self.sfx.play(sound_file, loop=loop)

    def play_music(self, sound_file: str | Path | None = None, start_seconds: float = 0.0) -> None:
        if sound_file is not None:
            self.set_music_source(sound_file)
        self.music.play(start_seconds=start_seconds)

    def music_finished(self) -> bool:
        return self.music.consume_finished()

    def seek_music(self, seconds: float) -> None:
        self.music.seek(seconds)

    def stop_music(self) -> None:
        self.music.stop()

    def pause_music(self) -> None:
        self.music.pause()

    def resume_music(self) -> None:
        self.music.resume()

    def toggle_music_pause(self) -> None:
        self.music.toggle_pause()

    def stop_sfx(self, sound_file: str | Path | None = None) -> None:
        self.sfx.stop(sound_file)

    def toggle_sfx_pause(self) -> None:
        self.sfx.toggle_pause()

    def forget_file(self, sound_file: str | Path) -> None:
        """Antes de borrar un archivo: dejar de usarlo en cualquier reproductor."""
        path = Path(sound_file)
        self.sfx.forget(path)
        if self.music_source == path:
            self.music.stop()
            self.set_music_source(None)

    def get_music_state(self) -> dict[str, Any]:
        return self.music.get_state()

    def get_available_output_devices(self) -> list[str]:
        if sd is None:
            return []
        devices = sd.query_devices()
        return [device["name"] for device in devices if device.get("max_output_channels", 0) > 0]

    def get_available_input_devices(self) -> list[str]:
        if sd is None:
            return []
        devices = sd.query_devices()
        return [device["name"] for device in devices if device.get("max_input_channels", 0) > 0]