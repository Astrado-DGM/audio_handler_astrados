from __future__ import annotations

from pathlib import Path
from typing import Any

import pygame
import sounddevice as sd


class singleSoundHandler:
    _mixer_ready = False
    _current_device: str | None = None

    def __init__(self, soundFile: str | Path | None = None, config: dict[str, Any] | None = None, output_device: str | None = None, max_channels: int = 8):
        self.sound_file: Path | None = None
        self.sound: pygame.mixer.Sound | None = None
        self.channel = None
        self.loop = False
        self.volume = 1.0
        self.paused = False
        self.max_channels = max(1, min(8, int(max_channels)))
        self._ensure_mixer(output_device)
        self.apply_config(config or {})
        if soundFile is not None:
            self.set_source(soundFile)

    @classmethod
    def _ensure_mixer(cls, output_device: str | None) -> None:
        if not pygame.mixer.get_init():
            mixer_kwargs = {
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
        if pygame.mixer.get_init():
            pygame.mixer.stop()
            pygame.mixer.quit()
        cls._mixer_ready = False
        cls._ensure_mixer(output_device)

    def set_source(self, soundFile: str | Path | None) -> None:
        if soundFile is None:
            self.sound_file = None
            self.sound = None
            return

        self.sound_file = Path(soundFile)
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
        if self.sound is not None:
            self.sound.set_volume(self.volume)
        if self.channel is not None and self.channel.get_busy():
            self.channel.set_volume(self.volume)

    def play(self, soundFile: str | Path | None = None, loop: bool | None = None) -> None:
        if soundFile is not None:
            self.set_source(soundFile)
        if self.sound is None:
            return

        if self.channel is not None and self.channel.get_busy():
            self.channel.stop()

        loop_enabled = self.loop if loop is None else bool(loop)
        self.channel = self.sound.play(loops=-1 if loop_enabled else 0)
        if self.channel is not None:
            self.channel.set_volume(self.volume)
        self.paused = False

    def stop(self) -> None:
        if self.channel is not None:
            self.channel.stop()
        self.paused = False

    def pause(self) -> None:
        if self.channel is not None and self.channel.get_busy():
            self.channel.pause()
            self.paused = True

    def resume(self) -> None:
        if self.channel is not None and self.paused:
            self.channel.unpause()
            self.paused = False

    def toggle_pause(self) -> None:
        if self.paused:
            self.resume()
        else:
            self.pause()

    def set_volume(self, volume: float) -> None:
        self.volume = self._clamp_volume(volume)
        if self.sound is not None:
            self.sound.set_volume(self.volume)
        if self.channel is not None and self.channel.get_busy():
            self.channel.set_volume(self.volume)

    def is_playing(self) -> bool:
        return self.channel is not None and self.channel.get_busy()

    def is_paused(self) -> bool:
        return self.paused

    def set_loop(self, loop: bool) -> None:
        self.loop = bool(loop)
        if self.channel is not None and self.channel.get_busy():
            self.channel.stop()
            self.channel = self.sound.play(loops=-1 if self.loop else 0) if self.sound is not None else None

    def update(self, current_config: dict[str, Any]) -> None:
        self.apply_config(current_config)
        if self.loop and self.sound is not None and (self.channel is None or not self.channel.get_busy()) and not self.paused:
            self.channel = self.sound.play(loops=-1)
            if self.channel is not None:
                self.channel.set_volume(self.volume)


class soundMotor:
    def __init__(self, config_manager) -> None:
        self.config_manager = config_manager
        self.config = config_manager.as_dict() if hasattr(config_manager, "as_dict") else dict(config_manager)
        self.max_channels = max(1, min(8, int(self.config.get("max_channels", 8))))
        self.sample_rate = int(self.config.get("sample_rate", 44100))
        self.buffer_size = int(self.config.get("buffer_size", 1024))
        self.output_device = self.config.get("output_device") or self.config.get("default_output_device")
        self.mic_input_device = self.config.get("mic_input_device")
        self.mic_active = False
        self.mic_stream: sd.Stream | None = None
        self.mic_volume = float(self.config.get("volume_mic", 1.0))
        self.music_source: Path | None = None
        self.sfx_source: Path | None = None

        singleSoundHandler.reinitialize_mixer(self.output_device)
        self.music = singleSoundHandler(config={"volume": self.config.get("volume_music", 1.0), "loop": self.config.get("loop_music", True)}, output_device=self.output_device, max_channels=self.max_channels)
        self.sfx = singleSoundHandler(config={"volume": self.config.get("volume_sfx", 1.0), "loop": self.config.get("loop_sfx", False)}, output_device=self.output_device, max_channels=self.max_channels)

    def _resolve_device_index(self, device_name: str | None, kind: str) -> int | None:
        if device_name is None:
            return None

        if isinstance(device_name, int):
            return device_name

        devices = sd.query_devices()
        for index, device in enumerate(devices):
            if device_name == device.get("name"):
                if kind == "input" and device.get("max_input_channels", 0) > 0:
                    return index
                if kind == "output" and device.get("max_output_channels", 0) > 0:
                    return index
        return None

    def _selected_device_pair(self) -> tuple[int | None, int | None]:
        input_index = self._resolve_device_index(self.mic_input_device, "input")
        output_index = self._resolve_device_index(self.output_device, "output")
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
        input_index, output_index = self._selected_device_pair()
        if input_index is None or output_index is None:
            raise ValueError("No se pudo resolver el dispositivo de entrada o salida para el microfono.")

        input_device = sd.query_devices(input_index)
        output_device = sd.query_devices(output_index)
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

    def start_mic(self) -> None:
        if self.mic_stream is not None:
            return

        self._open_mic_stream()
        self.mic_stream.start()
        self.mic_active = True

    def stop_mic(self) -> None:
        if self.mic_stream is not None:
            self.mic_stream.stop()
            self.mic_stream.close()
            self.mic_stream = None
        self.mic_active = False

    def set_music_source(self, sound_file: str | Path | None) -> None:
        self.music_source = Path(sound_file) if sound_file is not None else None
        self.music.set_source(self.music_source)

    def set_sfx_source(self, sound_file: str | Path | None) -> None:
        self.sfx_source = Path(sound_file) if sound_file is not None else None
        self.sfx.set_source(self.sfx_source)

    def define_output_device(self, device_name: str | None) -> None:
        self.output_device = device_name or self.config.get("default_output_device")
        self.config["output_device"] = self.output_device
        self.config_manager.updateConfig({"output_device": self.output_device})
        self._rebuild_playback_engine()

    def define_input_device(self, device_name: str | None) -> None:
        self.mic_input_device = device_name
        self.config["mic_input_device"] = device_name
        self.config_manager.updateConfig({"mic_input_device": device_name})
        if self.mic_active:
            self.stop_mic()
            self.start_mic()

    def _rebuild_playback_engine(self) -> None:
        music_source = self.music_source
        sfx_source = self.sfx_source
        music_config = {"volume": self.config_manager.volume_music(), "loop": self.config_manager.loop_music()}
        sfx_config = {"volume": self.config_manager.volume_sfx(), "loop": self.config_manager.loop_sfx()}
        music_is_playing = self.music.is_playing()
        sfx_is_playing = self.sfx.is_playing()

        singleSoundHandler.reinitialize_mixer(self.output_device)
        self.music = singleSoundHandler(config=music_config, output_device=self.output_device, max_channels=self.max_channels)
        self.sfx = singleSoundHandler(config=sfx_config, output_device=self.output_device, max_channels=self.max_channels)

        if music_source is not None:
            self.music.set_source(music_source)
            if music_is_playing:
                self.music.play(loop=self.config_manager.loop_music())
        if sfx_source is not None:
            self.sfx.set_source(sfx_source)
            if sfx_is_playing:
                self.sfx.play(loop=self.config_manager.loop_sfx())

    def apply_config(self, current_config: dict[str, Any]) -> None:
        self.config = dict(current_config)
        self.max_channels = max(1, min(8, int(self.config.get("max_channels", 8))))
        self.sample_rate = int(self.config.get("sample_rate", 44100))
        self.buffer_size = int(self.config.get("buffer_size", 1024))
        self.mic_volume = float(self.config.get("volume_mic", 1.0))

        output_device = self.config.get("output_device") or self.config.get("default_output_device")
        input_device = self.config.get("mic_input_device")

        if output_device != self.output_device:
            self.output_device = output_device
            self._rebuild_playback_engine()
        else:
            self.music.update({"volume": self.config.get("volume_music", 1.0), "loop": self.config.get("loop_music", True)})
            self.sfx.update({"volume": self.config.get("volume_sfx", 1.0), "loop": self.config.get("loop_sfx", False)})

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

    def play_music(self, sound_file: str | Path | None = None) -> None:
        if sound_file is not None:
            self.set_music_source(sound_file)
        self.music.play(loop=self.config_manager.loop_music())

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

    def get_available_output_devices(self) -> list[str]:
        devices = sd.query_devices()
        return [device["name"] for device in devices if device.get("max_output_channels", 0) > 0]

    def get_available_input_devices(self) -> list[str]:
        devices = sd.query_devices()
        return [device["name"] for device in devices if device.get("max_input_channels", 0) > 0]