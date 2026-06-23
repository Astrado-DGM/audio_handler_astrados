from __future__ import annotations

import time
from pathlib import Path
from typing import Any
import re
import unicodedata

import numpy as np

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


def _resolve_output_device_value(device_name: str | int | None) -> str | int | None:
    if sd is None or device_name is None:
        return device_name

    if isinstance(device_name, int):
        return device_name

    prefixed_index = _extract_device_index(device_name)
    if prefixed_index is not None:
        return prefixed_index

    normalized_target = _normalize_name(device_name)
    fallback_index: int | None = None

    try:
        devices = sd.query_devices()
    except Exception:
        return device_name

    for index, device in enumerate(devices):
        if device.get("max_output_channels", 0) <= 0:
            continue

        normalized_name = _normalize_name(str(device.get("name", "")))
        if normalized_name == normalized_target:
            return index
        if fallback_index is None and normalized_target and (
            normalized_target in normalized_name or normalized_name in normalized_target
        ):
            fallback_index = index

    return fallback_index if fallback_index is not None else device_name


def _resolve_device_for_kind(device_name: str | int | None, kind: str) -> str | int | None:
    if sd is None or device_name is None:
        return device_name

    resolved_index = _extract_device_index(device_name) if not isinstance(device_name, int) else device_name
    if resolved_index is None:
        resolved_index = None

    try:
        devices = sd.query_devices()
    except Exception:
        return device_name

    def has_kind(index: int) -> bool:
        device = devices[index]
        if kind == "input":
            return int(device.get("max_input_channels", 0) or 0) > 0
        return int(device.get("max_output_channels", 0) or 0) > 0

    if resolved_index is not None and 0 <= int(resolved_index) < len(devices) and has_kind(int(resolved_index)):
        return int(resolved_index)

    normalized_target = _normalize_name(str(device_name))
    token_variants = [normalized_target]
    if "output" in normalized_target:
        token_variants.append(normalized_target.replace("output", "input"))
    if "input" in normalized_target:
        token_variants.append(normalized_target.replace("input", "output"))

    best_match: int | None = None
    for index, device in enumerate(devices):
        if not has_kind(index):
            continue

        normalized_name = _normalize_name(str(device.get("name", "")))
        if normalized_name in token_variants or any(variant in normalized_name for variant in token_variants if variant):
            return index
        if best_match is None:
            best_match = index

    return best_match if best_match is not None else device_name


def _pygame_devicename_from_value(device_name: str | int | None) -> str | None:
    if sd is None or device_name is None:
        return None

    if isinstance(device_name, int):
        try:
            return str(sd.query_devices(device_name).get("name", "")) or None
        except Exception:
            return None

    prefixed_index = _extract_device_index(device_name)
    if prefixed_index is not None:
        try:
            return str(sd.query_devices(prefixed_index).get("name", "")) or None
        except Exception:
            return None

    stripped_name = _strip_device_label(device_name)
    return stripped_name or str(device_name)


def _pygame_output_candidates(device_name: str | int | None) -> list[str]:
    if sd is None or device_name is None:
        return []

    candidate_indices: list[int] = []
    resolved_index = _extract_device_index(device_name) if not isinstance(device_name, int) else device_name

    try:
        devices = sd.query_devices()
    except Exception:
        resolved_name = _pygame_devicename_from_value(device_name)
        return [resolved_name] if resolved_name else []

    normalized_target = _normalize_name(_strip_device_label(device_name))
    if resolved_index is not None and 0 <= int(resolved_index) < len(devices):
        candidate_indices.append(int(resolved_index))

    for index, device in enumerate(devices):
        if device.get("max_output_channels", 0) <= 0:
            continue

        normalized_name = _normalize_name(str(device.get("name", "")))
        if normalized_target and (
            normalized_name == normalized_target
            or normalized_target in normalized_name
            or normalized_name in normalized_target
        ):
            if index not in candidate_indices:
                candidate_indices.append(index)

    candidate_names: list[str] = []
    for index in candidate_indices:
        try:
            candidate_name = str(devices[index].get("name", "")).strip()
        except Exception:
            continue
        if candidate_name and candidate_name not in candidate_names:
            candidate_names.append(candidate_name)

    resolved_name = _pygame_devicename_from_value(device_name)
    if resolved_name and resolved_name not in candidate_names:
        candidate_names.append(resolved_name)

    return candidate_names


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

        resolved_device = _resolve_device_for_kind(self.device, "output")

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
        except Exception:
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

        resolved_device = _resolve_device_for_kind(self.device, "output")

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
        except Exception:
            self.stream = None
            self.active = False

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
        self.paused = False
        self.monitor_enabled = False
        self.monitor_device: str | None = None
        self._mirror = _OutputMirror(output_device, 44100)
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
            candidate_names = _pygame_output_candidates(output_device)
            init_error: Exception | None = None

            for candidate_name in candidate_names:
                mixer_kwargs["devicename"] = candidate_name
                try:
                    pygame.mixer.init(**mixer_kwargs)
                    break
                except (TypeError, pygame.error) as error:
                    init_error = error
                    if pygame.mixer.get_init():
                        pygame.mixer.quit()
                    continue
            else:
                mixer_kwargs.pop("devicename", None)
                try:
                    pygame.mixer.init(**mixer_kwargs)
                except pygame.error:
                    if init_error is not None:
                        raise init_error
                    pygame.mixer.init()

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
        if soundFile is None:
            self.sound_file = None
            self.sound = None
            self._mirror.stop()
            return

        self.sound_file = Path(soundFile)
        if pygame is None:
            self.sound = None
            return

        self.sound = pygame.mixer.Sound(str(self.sound_file))
        self.sound.set_volume(self.volume)

    def _clamp_volume(self, volume: Any) -> float:
        try:
            return max(0.0, min(1.0, float(volume)))
        except (TypeError, ValueError):
            return 1.0

    def apply_config(self, config: dict[str, Any]) -> None:
        self.volume = self._clamp_volume(config.get("volume", config.get("volume_music", config.get("volume_sfx", 1.0))))
        self.loop = bool(config.get("loop", config.get("loop_music", config.get("loop_sfx", False))))
        self.monitor_enabled = bool(config.get("monitor_enabled", config.get("monitor_audio", False)))
        self.monitor_device = config.get("monitor_output_device") or None
        if self.sound is not None:
            self.sound.set_volume(self.volume)
        if self.channel is not None and getattr(self.channel, "get_busy", lambda: False)():
            self.channel.set_volume(self.volume)
        if self.channel is not None and getattr(self.channel, "get_busy", lambda: False)() and self.monitor_enabled:
            self._mirror.configure(
                _sound_to_float32_array(self.sound),
                start_seconds=0.0,
                loop=self.loop,
                volume=self.volume,
                device=self.monitor_device,
            )
        elif not self.monitor_enabled:
            self._mirror.stop()

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
        if self.channel is not None:
            self.channel.set_volume(self.volume)
        if self.monitor_enabled:
            self._mirror.configure(
                _sound_to_float32_array(self.sound),
                start_seconds=0.0,
                loop=loop_enabled,
                volume=self.volume,
                device=self.monitor_device,
            )
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
        if self.sound is not None:
            self.sound.set_volume(self.volume)
        if self.channel is not None and getattr(self.channel, "get_busy", lambda: False)():
            self.channel.set_volume(self.volume)

    def is_playing(self) -> bool:
        return self.channel is not None and getattr(self.channel, "get_busy", lambda: False)()

    def is_paused(self) -> bool:
        return self.paused

    def set_loop(self, loop: bool) -> None:
        self.loop = bool(loop)
        if self.channel is not None and getattr(self.channel, "get_busy", lambda: False)():
            self.channel.stop()
            self.channel = self.sound.play(loops=-1 if self.loop else 0) if self.sound is not None else None
            if self.monitor_enabled:
                self._mirror.configure(
                    _sound_to_float32_array(self.sound),
                    start_seconds=0.0,
                    loop=self.loop,
                    volume=self.volume,
                    device=self.monitor_device,
                )


class SeekableMusicHandler:
    _mixer_ready = False
    _current_device: str | None = None

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
        self.paused = False
        self.monitor_enabled = False
        self.monitor_device: str | None = None
        self._mirror = _OutputMirror(None, 44100)
        self._play_started_at: float | None = None
        self._base_position_seconds = 0.0
        self._paused_position_seconds = 0.0
        self._ensure_mixer(output_device)
        self.apply_config(config or {})

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
            if output_device:
                mixer_kwargs["devicename"] = output_device
            try:
                pygame.mixer.init(**mixer_kwargs)
            except (TypeError, pygame.error):
                mixer_kwargs.pop("devicename", None)
                try:
                    pygame.mixer.init(**mixer_kwargs)
                except pygame.error:
                    pygame.mixer.init()

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

    def _current_position_seconds(self) -> float:
        if self.paused:
            position = self._paused_position_seconds
        elif self._play_started_at is None:
            position = self._base_position_seconds
        else:
            position = self._base_position_seconds + max(0.0, time.monotonic() - self._play_started_at)

        if self.duration_seconds > 0:
            if self.loop:
                return position % self.duration_seconds
            return min(position, self.duration_seconds)
        return max(0.0, position)

    def _current_monitor_device(self) -> str | None:
        return self.monitor_device

    def _start_monitor(self, start_seconds: float) -> None:
        if not self.monitor_enabled:
            self._mirror.stop()
            return

        self._mirror.configure(
            _sound_to_float32_array(self.playback_sound),
            start_seconds=start_seconds,
            loop=self.loop,
            volume=self.volume,
            device=self._current_monitor_device(),
        )

    def _build_playback_sound(self, start_seconds: float = 0.0) -> Any | None:
        if self.sound is None or self.samples is None:
            return None

        start_frame = max(0, min(int(float(start_seconds) * 44100), int(self.samples.shape[0])))
        sliced_samples = np.ascontiguousarray(self.samples[start_frame:])
        if sliced_samples.size == 0:
            return None

        try:
            return pygame.sndarray.make_sound(sliced_samples)
        except Exception:
            return self.sound

    def set_source(self, soundFile: str | Path | None) -> None:
        if soundFile is None:
            self.sound_file = None
            self.sound = None
            self.samples = None
            self.playback_sound = None
            self.channel = None
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
        self.monitor_enabled = bool(config.get("monitor_enabled", config.get("monitor_audio", False)))
        self.monitor_device = config.get("monitor_output_device") or None
        if pygame is not None and pygame.mixer.get_init():
            pass
        if self.sound is not None:
            self.sound.set_volume(self.volume)
        if self.playback_sound is not None and hasattr(self.playback_sound, "set_volume"):
            self.playback_sound.set_volume(self.volume)
        if self.paused:
            self._paused_position_seconds = self._current_position_seconds()

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

        self.channel = self.playback_sound.play(loops=-1 if loop_enabled else 0)
        if self.channel is not None:
            self.channel.set_volume(self.volume)
        self._base_position_seconds = start_seconds
        self._play_started_at = time.monotonic()
        self._paused_position_seconds = start_seconds
        self.paused = False
        self._start_monitor(start_seconds)

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
        if self.sound is not None:
            self.sound.set_volume(self.volume)
        if self.channel is not None and getattr(self.channel, "get_busy", lambda: False)():
            self.channel.set_volume(self.volume)

    def set_loop(self, loop: bool) -> None:
        self.loop = bool(loop)

    def seek(self, seconds: float) -> None:
        if self.sound_file is None:
            return

        keep_paused = self.paused
        self.play(self.sound_file, loop=self.loop, start_seconds=seconds)
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

    def update(self, current_config: dict[str, Any]) -> None:
        self.apply_config(current_config)
        if self.loop and self.sound is not None and (self.channel is None or not getattr(self.channel, "get_busy", lambda: False)()) and not self.paused:
            self.play(self.sound_file, loop=self.loop, start_seconds=self._current_position_seconds())


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

    def _resolve_device_index(self, device_name: str | None, kind: str, preferred_hostapi: int | None = None) -> int | None:
        if sd is None:
            return None

        if device_name is None:
            return None

        if isinstance(device_name, int):
            return device_name

        prefixed_index = _extract_device_index(device_name)
        if prefixed_index is not None:
            return prefixed_index

        devices = sd.query_devices()
        normalized_target = _normalize_name(device_name)
        fallback_same_kind: int | None = None
        fallback_exact: int | None = None

        for index, device in enumerate(devices):
            if kind == "input" and device.get("max_input_channels", 0) <= 0:
                continue
            if kind == "output" and device.get("max_output_channels", 0) <= 0:
                continue

            normalized_name = _normalize_name(str(device.get("name", "")))
            exact_match = normalized_name == normalized_target
            partial_match = normalized_target and (
                normalized_target in normalized_name or normalized_name in normalized_target
            )

            if preferred_hostapi is not None and int(device.get("hostapi", -1)) == int(preferred_hostapi):
                if exact_match or partial_match:
                    return index
                if fallback_same_kind is None:
                    fallback_same_kind = index
                continue

            if exact_match or partial_match:
                if fallback_exact is None:
                    fallback_exact = index

        if fallback_same_kind is not None:
            return fallback_same_kind
        return fallback_exact

    def _resolve_fallback_device(self, kind: str, preferred_hostapi: int | None = None) -> int | None:
        if sd is None:
            return None

        devices = sd.query_devices()
        fallback_index: int | None = None
        for index, device in enumerate(devices):
            if kind == "input" and device.get("max_input_channels", 0) <= 0:
                continue
            if kind == "output" and device.get("max_output_channels", 0) <= 0:
                continue

            if preferred_hostapi is not None and int(device.get("hostapi", -1)) == int(preferred_hostapi):
                return index

            if fallback_index is None:
                fallback_index = index

        return fallback_index

    def _selected_device_pair(self) -> tuple[int | None, int | None]:
        output_index = self._resolve_device_index(self.output_device, "output")
        output_info = self._device_info(output_index)
        preferred_hostapi = int(output_info.get("hostapi", -1)) if output_info is not None else None

        input_index = self._resolve_device_index(self.mic_input_device, "input", preferred_hostapi=preferred_hostapi)

        if output_index is None:
            output_index = self._resolve_fallback_device("output", preferred_hostapi=preferred_hostapi)
            output_info = self._device_info(output_index)
            preferred_hostapi = int(output_info.get("hostapi", -1)) if output_info is not None else None

        if input_index is None:
            input_index = self._resolve_fallback_device("input", preferred_hostapi=preferred_hostapi)

        if input_index is None or output_index is None:
            return input_index, output_index

        input_info = self._device_info(input_index)
        output_info = self._device_info(output_index)
        if input_info is None or output_info is None:
            return input_index, output_index

        if int(input_info.get("hostapi", -1)) != int(output_info.get("hostapi", -1)):
            preferred_hostapi = int(output_info.get("hostapi", -1))
            hostapi_input = self._resolve_fallback_device("input", preferred_hostapi=preferred_hostapi)
            hostapi_output = self._resolve_fallback_device("output", preferred_hostapi=preferred_hostapi)
            if hostapi_input is not None and hostapi_output is not None:
                return hostapi_input, hostapi_output

        return input_index, output_index

    def _mic_callback(self, indata, outdata, frames, time_info, status):
        if status:
            print(status)

        outdata.fill(0)
        if indata.size == 0:
            return

        data = indata * float(self.mic_volume)
        if getattr(data, "ndim", 2) == 1:
            data = data[:, None]

        input_channels = data.shape[1]
        output_channels = outdata.shape[1]
        channels_to_copy = min(input_channels, output_channels)
        outdata[:, :channels_to_copy] = data[:, :channels_to_copy]

        if output_channels > channels_to_copy and channels_to_copy > 0:
            outdata[:, channels_to_copy:] = data[:, :1]

    def _open_mic_stream(self) -> None:
        if sd is None:
            raise RuntimeError("sounddevice no esta disponible en este entorno.")

        input_index, output_index = self._selected_device_pair()
        if input_index is None or output_index is None:
            raise ValueError("No se pudo resolver el dispositivo de entrada o salida para el microfono.")

        input_device = sd.query_devices(input_index)
        output_device = sd.query_devices(output_index)
        if int(input_device.get("hostapi", -1)) != int(output_device.get("hostapi", -1)):
            raise ValueError(
                f"Los dispositivos seleccionados usan host APIs distintas: entrada={input_device.get('hostapi')} salida={output_device.get('hostapi')}."
            )

        input_channels = max(1, int(input_device.get("max_input_channels", 1)))
        output_channels = max(1, int(output_device.get("max_output_channels", 2)))

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
            if hasattr(self.config_manager, "updateConfig"):
                self.config_manager.updateConfig({"mic_active": False})
            print(f"No se pudo iniciar el microfono: {error}")
            return False

        self.mic_active = True
        if hasattr(self.config_manager, "updateConfig"):
            self.config_manager.updateConfig({"mic_active": True})
        return True

    def stop_mic(self) -> None:
        if self.mic_stream is not None:
            self.mic_stream.stop()
            self.mic_stream.close()
            self.mic_stream = None
        self.mic_active = False
        if hasattr(self.config_manager, "updateConfig"):
            self.config_manager.updateConfig({"mic_active": False})

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