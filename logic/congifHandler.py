
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class configHandler:
    def __init__(self, config_dir: Path | str | None = None) -> None:
        base_dir = Path(config_dir) if config_dir is not None else Path(__file__).resolve().parents[1] / "data" / "config"
        self.config_dir = base_dir
        self.config_path = self.config_dir / "audio_config.json"
        self.config = self.load_default_config()
        self.readConfig()

    def load_default_config(self) -> dict[str, Any]:
        return {
            "volume_sfx": 1.0,
            "volume_music": 1.0,
            "volume_mic": 1.0,
            "mic_active": False,
            "loop_sfx": False,
            "loop_music": True,
            "monitor_audio": False,
            "monitor_output_device": None,
            "mic_input_device": None,
            "output_device": None,
            "default_output_device": None,
            "max_channels": 8,
            "sample_rate": 44100,
            "buffer_size": 1024,
            "hotkeys": {
                "refresh_library": "Ctrl+R",
                "toggle_mic": "Ctrl+M",
                "play_music": "Ctrl+Shift+M",
                "stop_music": "Ctrl+Alt+M",
                "pause_music": "Ctrl+P",
                "play_sfx": "Ctrl+E",
                "stop_sfx": "Ctrl+Alt+E",
                "pause_sfx": "Ctrl+Shift+E",
                "save_settings": "Ctrl+S",
            },
        }

    def _clamp_float(self, value: Any) -> float:
        try:
            return max(0.0, min(1.0, float(value)))
        except (TypeError, ValueError):
            return 1.0

    def _normalize_config(self, new_config: dict[str, Any] | None) -> dict[str, Any]:
        merged = self.load_default_config()
        if new_config:
            merged.update(new_config)

        hotkeys = dict(self.load_default_config()["hotkeys"])
        if isinstance(merged.get("hotkeys"), dict):
            hotkeys.update(merged["hotkeys"])
        merged["hotkeys"] = hotkeys

        merged["volume_sfx"] = self._clamp_float(merged.get("volume_sfx", 1.0))
        merged["volume_music"] = self._clamp_float(merged.get("volume_music", 1.0))
        merged["volume_mic"] = self._clamp_float(merged.get("volume_mic", merged.get("mic_volume", 1.0)))
        merged["mic_active"] = bool(merged.get("mic_active", False))
        merged["loop_sfx"] = bool(merged.get("loop_sfx", False))
        merged["loop_music"] = bool(merged.get("loop_music", True))
        merged["monitor_audio"] = bool(merged.get("monitor_audio", False))
        merged["monitor_output_device"] = merged.get("monitor_output_device") or None
        merged["mic_input_device"] = merged.get("mic_input_device") or None
        merged["output_device"] = merged.get("output_device") or None
        merged["default_output_device"] = merged.get("default_output_device") or merged["output_device"]
        merged["max_channels"] = int(merged.get("max_channels", 8))
        merged["sample_rate"] = int(merged.get("sample_rate", 44100))
        merged["buffer_size"] = int(merged.get("buffer_size", 1024))
        return merged

    def readConfig(self, config_path: Path | str | None = None) -> dict[str, Any]:
        path = Path(config_path) if config_path is not None else self.config_path
        if path.exists():
            try:
                with path.open("r", encoding="utf-8") as handle:
                    loaded_config = json.load(handle)
            except (OSError, json.JSONDecodeError):
                loaded_config = {}
            self.config = self._normalize_config(loaded_config)
        else:
            self.config = self._normalize_config({})
        return self.config

    def updateConfig(self, new_config: dict[str, Any]) -> dict[str, Any]:
        self.config = self._normalize_config({**self.config, **new_config})
        return self.config

    def saveConfig(self, config_path: Path | str | None = None) -> Path:
        path = Path(config_path) if config_path is not None else self.config_path
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as handle:
            json.dump(self.config, handle, ensure_ascii=False, indent=2)
        return path

    def as_dict(self) -> dict[str, Any]:
        return dict(self.config)

    def volume_sfx(self) -> float:
        return float(self.config.get("volume_sfx", 1.0))

    def volume_music(self) -> float:
        return float(self.config.get("volume_music", 1.0))

    def mic_volume(self) -> float:
        return float(self.config.get("volume_mic", 1.0))

    def mic_active(self) -> bool:
        return bool(self.config.get("mic_active", False))

    def loop_sfx(self) -> bool:
        return bool(self.config.get("loop_sfx", False))

    def loop_music(self) -> bool:
        return bool(self.config.get("loop_music", True))

    def mic_input_device(self):
        return self.config.get("mic_input_device")

    def output_device(self):
        return self.config.get("output_device")

    def default_output_device(self):
        return self.config.get("default_output_device")

    def hotkeys(self) -> dict[str, str]:
        defaults = self.load_default_config()["hotkeys"]
        current = self.config.get("hotkeys", {})
        if not isinstance(current, dict):
            current = {}
        merged = dict(defaults)
        merged.update(current)
        return merged

    def max_channels(self) -> int:
        return int(self.config.get("max_channels", 8))

    def sample_rate(self) -> int:
        return int(self.config.get("sample_rate", 44100))

    def buffer_size(self) -> int:
        return int(self.config.get("buffer_size", 1024))