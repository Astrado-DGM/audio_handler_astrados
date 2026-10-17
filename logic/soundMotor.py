from __future__ import annotations

import math
import time
from pathlib import Path
from typing import Any
import re
import unicodedata

import numpy as np

from logic.micFilter import MicFilterSettings, MicProcessor

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


class _OutputMirror:
    def __init__(self, device: str | None, sample_rate: int) -> None:
        self.device = device
        self.sample_rate = sample_rate
        self.samples: np.ndarray | None = None
        self.loop = False
        self.volume = 1.0
        self.frame_index = 0
        self.stream: Any | None = None
        self.active = False

    def configure(
        self,
        samples: np.ndarray | None,
        *,
        start_seconds: float = 0.0,
        loop: bool = False,
        volume: float = 1.0,
        device: str | None = None,
    ) -> None:
        self.stop()
        self.samples = samples
        self.loop = bool(loop)
        self.volume = max(0.0, min(1.0, float(volume)))
        self.frame_index = max(0, int(float(start_seconds) * self.sample_rate))
        self.device = device or self.device

        if sd is None or self.samples is None or self.device is None:
            return

        self._open_stream()

    def _open_stream(self) -> None:
        if sd is None or self.samples is None:
            return

        resolved_device = resolve_device(self.device, "output")
        if resolved_device is None:
            print(f"Monitor: no se encontro el dispositivo de salida {self.device!r}.")
            return

        try:
            self.stream = sd.OutputStream(
                device=resolved_device,
                samplerate=self.sample_rate,
                channels=int(self.samples.shape[1]),
                dtype="float32",
                callback=self._callback,
            )
            assert self.stream is not None
            self.stream.start()
            self.active = True
        except Exception as error:
            print(f"Monitor: no se pudo abrir la salida: {error}")
            self.stream = None
            self.active = False

    def _callback(self, outdata, frames, time_info, status):
        if status:
            print(status)

        outdata.fill(0)
        if self.samples is None or self.samples.size == 0:
            callback_stop = getattr(sd, "CallbackStop", None)
            if callback_stop is not None:
                raise callback_stop()
            return

        total_frames = int(self.samples.shape[0])
        channels = int(self.samples.shape[1])
        start_frame = int(self.frame_index)

        if self.loop and total_frames > 0:
            indices = (np.arange(start_frame, start_frame + frames) % total_frames).astype(np.int64)
            chunk = self.samples[indices]
            outdata[:] = chunk * self.volume
            self.frame_index = (start_frame + frames) % total_frames
            return

        end_frame = min(total_frames, start_frame + frames)
        chunk = self.samples[start_frame:end_frame]
        if chunk.size > 0:
            outdata[: chunk.shape[0], :channels] = chunk * self.volume

        self.frame_index = end_frame
        if end_frame >= total_frames:
            callback_stop = getattr(sd, "CallbackStop", None)
            if callback_stop is not None:
                raise callback_stop()

    def pause(self) -> None:
        if self.stream is not None:
            self.stream.stop()
            self.stream.close()
        self.stream = None
        self.active = False

    def resume(self) -> None:
        if self.samples is None or sd is None or self.device is None or self.active:
            return
        self._open_stream()

    def seek(self, start_seconds: float) -> None:
        self.frame_index = max(0, int(float(start_seconds) * self.sample_rate))

    def stop(self) -> None:
        if self.stream is not None:
            try:
                self.stream.stop()
            finally:
                self.stream.close()
        self.stream = None
        self.active = False


class singleSoundHandler:
    _mixer_ready = False
    _current_device: str | None = None
    # Nombre del dispositivo que pygame abrio realmente, o None si uso el de Windows.
    opened_device: str | None = None
    fallback_warning: str | None = None

    def __init__(
        self,
        soundFile: str | Path | None = None,
        config: dict[str, Any] | None = None,
        output_device: str | None = None,
        max_channels: int = 8,
    ) -> None:
        self.sound_file: Path | None = None
        self.sound: Any = None
        self.channel: Any = None
        self.loop = False
        self.volume = 1.0
        self.duck_gain = 1.0
        self.paused = False
        self.monitor_enabled = False
        self.monitor_device: str | None = None
        self._mirror = _OutputMirror(output_device, 44100)
        self._mirror_samples: np.ndarray | None = None
        self.max_channels = max(1, min(8, int(max_channels)))
        self._ensure_mixer(output_device)
        self.apply_config(config or {})
        if soundFile is not None:
            self.set_source(soundFile)

    @classmethod
    def _ensure_mixer(cls, output_device: str | None) -> None:
        if pygame is None:
            cls._mixer_ready = False
            cls._current_device = output_device
            return

        if not pygame.mixer.get_init():
            mixer_kwargs: dict[str, Any] = {
                "frequency": 44100,
                "size": -16,
                "channels": 2,
                "buffer": 1024,
            }
            cls.opened_device = None
            cls.fallback_warning = None
            for candidate_name in _pygame_output_candidates(output_device):
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
                if output_device:
                    cls.fallback_warning = (
                        f"No se pudo abrir {_strip_device_label(output_device)!r} para musica/SFX; "
                        "suenan por la salida predeterminada de Windows."
                    )

        pygame.mixer.set_num_channels(8)
        cls._mixer_ready = True
        cls._current_device = output_device

    @classmethod
    def reinitialize_mixer(cls, output_device: str | None) -> None:
        if pygame is None:
            cls._mixer_ready = False
            cls._current_device = output_device
            return

        if pygame.mixer.get_init():
            pygame.mixer.stop()
            pygame.mixer.quit()
        cls._mixer_ready = False
        cls._ensure_mixer(output_device)

    def set_source(self, soundFile: str | Path | None) -> None:
        self._mirror_samples = None
        if soundFile is None:
            self.sound_file = None
            self.sound = None
            self._mirror.stop()
            return

        self.sound_file = Path(soundFile)
        if pygame is None:
            self.sound = None
            return

        # El volumen se aplica solo en el Channel; Sound queda en 1.0 para no elevarlo al cuadrado.
        self.sound = pygame.mixer.Sound(str(self.sound_file))

    def _clamp_volume(self, volume: Any) -> float:
        try:
            return max(0.0, min(1.0, float(volume)))
        except (TypeError, ValueError):
            return 1.0

    def effective_volume(self) -> float:
        return self.volume * self.duck_gain

    def _apply_volume(self) -> None:
        if self.channel is not None:
            self.channel.set_volume(self.effective_volume())
        self._mirror.volume = self.effective_volume()

    def _monitor_samples(self) -> np.ndarray | None:
        if self._mirror_samples is None:
            self._mirror_samples = _sound_to_float32_array(self.sound)
        return self._mirror_samples

    def _start_monitor(self, loop: bool) -> None:
        self._mirror.configure(
            self._monitor_samples(),
            start_seconds=0.0,
            loop=loop,
            volume=self.effective_volume(),
            device=self.monitor_device,
        )

    def apply_config(self, config: dict[str, Any]) -> None:
        self.volume = self._clamp_volume(config.get("volume", config.get("volume_music", config.get("volume_sfx", 1.0))))
        self.loop = bool(config.get("loop", config.get("loop_music", config.get("loop_sfx", False))))
        monitor_enabled = bool(config.get("monitor_enabled", config.get("monitor_audio", False)))
        self.monitor_device = config.get("monitor_output_device") or None
        self._apply_volume()
        # Solo se (re)abre el espejo al activarlo; aplicar ajustes ya no lo reinicia desde 0.
        if monitor_enabled and not self.monitor_enabled and self.is_playing() and not self.paused:
            self.monitor_enabled = True
            self._start_monitor(self.loop)
        elif not monitor_enabled:
            self._mirror.stop()
        self.monitor_enabled = monitor_enabled

    def set_monitor(self, enabled: bool, device: str | None) -> None:
        self.monitor_enabled = bool(enabled)
        self.monitor_device = device
        if not self.monitor_enabled:
            self._mirror.stop()

    def play(self, soundFile: str | Path | None = None, loop: bool | None = None) -> None:
        if soundFile is not None:
            self.set_source(soundFile)
        if self.sound is None:
            return

        if self.channel is not None and getattr(self.channel, "get_busy", lambda: False)():
            self.channel.stop()

        loop_enabled = self.loop if loop is None else bool(loop)
        self.channel = self.sound.play(loops=-1 if loop_enabled else 0)
        self._apply_volume()
        if self.monitor_enabled:
            self._start_monitor(loop_enabled)
        self.paused = False

    def stop(self) -> None:
        if self.channel is not None:
            self.channel.stop()
        self.paused = False
        self._mirror.stop()

    def pause(self) -> None:
        if self.channel is not None and getattr(self.channel, "get_busy", lambda: False)():
            self.channel.pause()
            self.paused = True
            self._mirror.pause()

    def resume(self) -> None:
        if self.channel is not None and self.paused:
            self.channel.unpause()
            self.paused = False
            self._mirror.resume()

    def toggle_pause(self) -> None:
        if self.paused:
            self.resume()
        else:
            self.pause()

    def set_volume(self, volume: float) -> None:
        self.volume = self._clamp_volume(volume)
        self._apply_volume()

    def set_duck_gain(self, gain: float) -> None:
        self.duck_gain = max(0.0, min(1.0, float(gain)))
        self._apply_volume()

    def is_playing(self) -> bool:
        return self.channel is not None and getattr(self.channel, "get_busy", lambda: False)()

    def is_paused(self) -> bool:
        return self.paused

    def set_loop(self, loop: bool) -> None:
        self.loop = bool(loop)
        if self.channel is not None and getattr(self.channel, "get_busy", lambda: False)():
            self.channel.stop()
            self.channel = self.sound.play(loops=-1 if self.loop else 0) if self.sound is not None else None
            self._apply_volume()
            if self.monitor_enabled:
                self._start_monitor(self.loop)


class SeekableMusicHandler:
    """Musica con seek y loop.

    Tras un seek se reproduce un Sound recortado (desde el punto elegido) y, si hay
    loop, se encola la pista completa justo antes de que termine. Asi el loop vuelve
    al inicio de la cancion y la posicion es simplemente (inicio + transcurrido) % duracion.
    """

    # Margen para encolar la siguiente vuelta; tick() se llama varias veces por segundo.
    QUEUE_AHEAD_SECONDS = 1.5

    def __init__(
        self,
        config: dict[str, Any] | None = None,
        output_device: str | None = None,
    ) -> None:
        self.sound_file: Path | None = None
        self.sound: Any = None
        self.samples: np.ndarray | None = None
        self.playback_sound: Any = None
        self.channel: Any = None
        self.duration_seconds = 0.0
        self.loop = False
        self.volume = 1.0
        self.duck_gain = 1.0
        self.paused = False
        self.monitor_enabled = False
        self.monitor_device: str | None = None
        self._mirror = _OutputMirror(None, 44100)
        self._mirror_samples: np.ndarray | None = None
        self._play_started_at: float | None = None
        self._base_position_seconds = 0.0
        self._paused_position_seconds = 0.0
        self._ensure_mixer(output_device)
        self.apply_config(config or {})

    @classmethod
    def _ensure_mixer(cls, output_device: str | None) -> None:
        # El mixer de pygame es global: se comparte la logica de seleccion de dispositivo.
        singleSoundHandler._ensure_mixer(output_device)

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
        self._mirror.volume = self.effective_volume()

    def _start_monitor(self, start_seconds: float) -> None:
        if not self.monitor_enabled:
            self._mirror.stop()
            return

        # El espejo recorre la pista completa (no el tramo recortado), con su propio loop.
        if self._mirror_samples is None:
            self._mirror_samples = _sound_to_float32_array(self.sound)
        self._mirror.configure(
            self._mirror_samples,
            start_seconds=start_seconds,
            loop=self.loop,
            volume=self.effective_volume(),
            device=self.monitor_device,
        )

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
        self._mirror_samples = None
        if soundFile is None:
            self.sound_file = None
            self.sound = None
            self.samples = None
            self.playback_sound = None
            self.duration_seconds = 0.0
            self.stop()
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

    def _clamp_volume(self, volume: Any) -> float:
        try:
            return max(0.0, min(1.0, float(volume)))
        except (TypeError, ValueError):
            return 1.0

    def apply_config(self, config: dict[str, Any]) -> None:
        self.volume = self._clamp_volume(config.get("volume", config.get("volume_music", 1.0)))
        self.loop = bool(config.get("loop", config.get("loop_music", True)))
        self._mirror.loop = self.loop
        monitor_enabled = bool(config.get("monitor_enabled", config.get("monitor_audio", False)))
        self.monitor_device = config.get("monitor_output_device") or None
        self._apply_volume()
        if monitor_enabled and not self.monitor_enabled and self.is_playing():
            self.monitor_enabled = True
            self._start_monitor(self._current_position_seconds())
        elif not monitor_enabled:
            self._mirror.stop()
        self.monitor_enabled = monitor_enabled

    def play(self, soundFile: str | Path | None = None, loop: bool | None = None, start_seconds: float = 0.0) -> None:
        if soundFile is not None:
            self.set_source(soundFile)
        if self.sound is None or pygame is None:
            return

        loop_enabled = self.loop if loop is None else bool(loop)
        self.loop = loop_enabled
        start_seconds = max(0.0, float(start_seconds))
        if self.duration_seconds > 0:
            start_seconds = min(start_seconds, self.duration_seconds)

        self.playback_sound = self._build_playback_sound(start_seconds)
        if self.playback_sound is None:
            return

        if self.channel is not None and getattr(self.channel, "get_busy", lambda: False)():
            self.channel.stop()

        # Siempre loops=0: el loop se arma encolando la pista completa en tick().
        self.channel = self.playback_sound.play(loops=0)
        self._apply_volume()
        self._base_position_seconds = start_seconds
        self._play_started_at = time.monotonic()
        self._paused_position_seconds = start_seconds
        self.paused = False
        self._start_monitor(start_seconds)
        self.tick()

    def tick(self) -> None:
        """Encola la siguiente vuelta del loop cuando se acerca el final."""
        if not self.loop or self.paused or self.channel is None or self.sound is None or self.duration_seconds <= 0:
            return
        if not getattr(self.channel, "get_busy", lambda: False)():
            # Se perdio la ventana para encolar (UI bloqueada): se retoma desde el inicio.
            self.play(loop=True, start_seconds=0.0)
            return
        remaining = self.duration_seconds - self._current_position_seconds()
        if remaining <= self.QUEUE_AHEAD_SECONDS and self.channel.get_queue() is None:
            self.channel.queue(self.sound)

    def stop(self) -> None:
        if self.channel is not None:
            self.channel.stop()
        self._mirror.stop()
        self.paused = False
        self.channel = None
        self._play_started_at = None
        self._base_position_seconds = 0.0
        self._paused_position_seconds = 0.0

    def pause(self) -> None:
        if pygame is None or self.channel is None:
            return

        if not self.is_playing():
            return

        self._paused_position_seconds = self._current_position_seconds()
        self.channel.pause()
        self._mirror.pause()
        self.paused = True

    def resume(self) -> None:
        if pygame is None or self.sound is None or self.channel is None:
            return

        if not self.paused:
            return

        self.channel.unpause()
        self._base_position_seconds = self._paused_position_seconds
        self._play_started_at = time.monotonic()
        self.paused = False
        self._start_monitor(self._paused_position_seconds)

    def toggle_pause(self) -> None:
        if self.paused:
            self.resume()
        else:
            self.pause()

    def set_volume(self, volume: float) -> None:
        self.volume = self._clamp_volume(volume)
        self._apply_volume()

    def set_duck_gain(self, gain: float) -> None:
        self.duck_gain = max(0.0, min(1.0, float(gain)))
        self._apply_volume()

    def set_loop(self, loop: bool) -> None:
        self.loop = bool(loop)
        self._mirror.loop = self.loop

    def seek(self, seconds: float) -> None:
        if self.sound is None:
            return

        keep_paused = self.paused
        # Sin pasar la ruta: re-decodificar el MP3 completo en cada seek era muy lento.
        self.play(loop=self.loop, start_seconds=seconds)
        if keep_paused:
            self.pause()

    def is_playing(self) -> bool:
        if pygame is None:
            return False
        return self.channel is not None and bool(getattr(self.channel, "get_busy", lambda: False)()) and not self.paused

    def is_paused(self) -> bool:
        return self.paused

    def get_state(self) -> dict[str, Any]:
        position = self._current_position_seconds()
        return {
            "source": self.sound_file,
            "position_seconds": position,
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
        self.sfx_source: Path | None = None

        singleSoundHandler.reinitialize_mixer(self.output_device)
        self.music = SeekableMusicHandler(
            config={
                "volume": self.config.get("volume_music", 1.0),
                "loop": self.config.get("loop_music", True),
                "monitor_audio": self.monitor_audio,
                "monitor_output_device": self.monitor_output_device,
            },
            output_device=self.output_device,
        )
        self.sfx = singleSoundHandler(
            config={
                "volume": self.config.get("volume_sfx", 1.0),
                "loop": self.config.get("loop_sfx", False),
                "monitor_audio": self.monitor_audio,
                "monitor_output_device": self.monitor_output_device,
            },
            output_device=self.output_device,
            max_channels=self.max_channels,
        )

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
            print(status)

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
            print(f"No se pudo iniciar el microfono: {error}")
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
        return singleSoundHandler.fallback_warning

    def is_voice_active(self) -> bool:
        processor = self.mic_processor
        return bool(self.mic_active and processor is not None and processor.voice_detected)

    def tick(self) -> None:
        """Llamar periodicamente (~50 ms) desde la UI: loop de musica y ducking."""
        now = time.monotonic()
        dt = 0.05 if self._last_tick is None else min(0.5, now - self._last_tick)
        self._last_tick = now

        self.music.tick()

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
        self.music._mirror.stop()
        self.sfx._mirror.stop()
        return mic_was_active

    def shutdown(self) -> None:
        self.stop_mic()
        self.music.stop()
        self.sfx.stop()
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

    def set_sfx_source(self, sound_file: str | Path | None) -> None:
        self.sfx_source = Path(sound_file) if sound_file is not None else None
        self.sfx.set_source(self.sfx_source)

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
        music_source = self.music_source
        sfx_source = self.sfx_source
        music_state = self.music.get_state() if hasattr(self.music, "get_state") else {}
        sfx_is_playing = self.sfx.is_playing()
        sfx_is_paused = self.sfx.is_paused()
        music_is_paused = bool(music_state.get("is_paused", False))
        music_position = float(music_state.get("position_seconds", 0.0))
        sfx_is_playing = self.sfx.is_playing()

        singleSoundHandler.reinitialize_mixer(self.output_device)
        self.music = SeekableMusicHandler(
            config={
                "volume": self.config_manager.volume_music(),
                "loop": self.config_manager.loop_music(),
                "monitor_audio": self.monitor_audio,
                "monitor_output_device": self.monitor_output_device,
            },
            output_device=self.output_device,
        )
        self.sfx = singleSoundHandler(
            config={
                "volume": self.config_manager.volume_sfx(),
                "loop": self.config_manager.loop_sfx(),
                "monitor_audio": self.monitor_audio,
                "monitor_output_device": self.monitor_output_device,
            },
            output_device=self.output_device,
            max_channels=self.max_channels,
        )

        if music_source is not None:
            self.music.set_source(music_source)
            if music_state.get("is_playing", False) or music_is_paused:
                self.music.play(loop=self.config_manager.loop_music(), start_seconds=music_position)
                if music_is_paused:
                    self.music.pause()
        if sfx_source is not None:
            self.sfx.set_source(sfx_source)
            if sfx_is_playing:
                self.sfx.play(loop=self.config_manager.loop_sfx())
                if sfx_is_paused:
                    self.sfx.pause()

    def apply_config(self, current_config: dict[str, Any]) -> None:
        self.config = dict(current_config)
        self.max_channels = max(1, min(8, int(self.config.get("max_channels", 8))))
        self.sample_rate = int(self.config.get("sample_rate", 44100))
        self.buffer_size = int(self.config.get("buffer_size", 1024))
        self.mic_volume = float(self.config.get("volume_mic", 1.0))
        self.monitor_audio = bool(self.config.get("monitor_audio", False))
        self.update_mic_filter(self.config)

        output_device = self.config.get("output_device") or self.config.get("default_output_device")
        monitor_output_device = self.config.get("monitor_output_device") or output_device
        input_device = self.config.get("mic_input_device")

        if output_device != self.output_device or monitor_output_device != self.monitor_output_device:
            self.output_device = output_device
            self.monitor_output_device = monitor_output_device
            self._rebuild_playback_engine()
        else:
            self.music.apply_config(
                {
                    "volume": self.config.get("volume_music", 1.0),
                    "loop": self.config.get("loop_music", True),
                    "monitor_audio": self.monitor_audio,
                    "monitor_output_device": self.monitor_output_device,
                }
            )
            self.sfx.apply_config(
                {
                    "volume": self.config.get("volume_sfx", 1.0),
                    "loop": self.config.get("loop_sfx", False),
                    "monitor_audio": self.monitor_audio,
                    "monitor_output_device": self.monitor_output_device,
                }
            )

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

    def play_sfx(self, sound_file: str | Path | None = None) -> None:
        if sound_file is not None:
            self.set_sfx_source(sound_file)
        self.sfx.play(loop=self.config_manager.loop_sfx())

    def play_music(self, sound_file: str | Path | None = None, start_seconds: float = 0.0) -> None:
        if sound_file is not None:
            self.set_music_source(sound_file)
        self.music.play(loop=self.config_manager.loop_music(), start_seconds=start_seconds)

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

    def stop_sfx(self) -> None:
        self.sfx.stop()

    def pause_sfx(self) -> None:
        self.sfx.pause()

    def resume_sfx(self) -> None:
        self.sfx.resume()

    def toggle_sfx_pause(self) -> None:
        self.sfx.toggle_pause()

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