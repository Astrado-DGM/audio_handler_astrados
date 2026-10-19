"""Filtrado nativo del microfono en tiempo real.

Cadena de procesamiento (todo en numpy, sin dependencias extra):
1. Filtro pasa-altos (Butterworth de 2do orden aplicado en frecuencia) que quita
   zumbidos graves, golpes al escritorio y ruido de ventilador.
2. Supresion de ruido espectral: estima el piso de ruido de forma adaptativa por
   banda y atenua lo que no sobresale de el (ventiladores, teclado suave, hiss).
3. Puerta de ruido con histeresis: silencia el microfono cuando no se habla.

La idea es limpiar el microfono ANTES de mezclarlo, para que en Discord se pueda
desactivar su supresion de ruido (Krisp), que destruye la musica y los SFX.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np


@dataclass(frozen=True)
class MicFilterSettings:
    noise_suppression: bool = False
    suppression_strength: float = 0.6
    noise_gate: bool = False
    gate_threshold_db: float = -45.0
    highpass: bool = True
    highpass_hz: float = 80.0

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> "MicFilterSettings":
        return cls(
            noise_suppression=bool(config.get("mic_noise_suppression", False)),
            suppression_strength=max(0.0, min(1.0, float(config.get("mic_suppression_strength", 0.6)))),
            noise_gate=bool(config.get("mic_noise_gate", False)),
            gate_threshold_db=max(-90.0, min(0.0, float(config.get("mic_gate_threshold_db", -45.0)))),
            highpass=bool(config.get("mic_highpass", True)),
            highpass_hz=max(20.0, min(300.0, float(config.get("mic_highpass_hz", 80.0)))),
        )

    @property
    def any_enabled(self) -> bool:
        return self.noise_suppression or self.noise_gate or self.highpass


class MicProcessor:
    """Procesa bloques mono float32 de tamano arbitrario.

    Usa STFT con solape del 50% (ventana sqrt-Hann en analisis y sintesis), lo que
    agrega una latencia fija de FRAME_SIZE muestras (~11 ms a 44.1/48 kHz).
    """

    FRAME_SIZE = 512
    HOP_SIZE = FRAME_SIZE // 2

    def __init__(self, sample_rate: int, settings: MicFilterSettings | None = None) -> None:
        self.sample_rate = int(sample_rate)
        self.settings = settings or MicFilterSettings()
        self._window = np.sqrt(np.hanning(self.FRAME_SIZE + 1)[:-1]).astype(np.float32)
        self._freqs = np.fft.rfftfreq(self.FRAME_SIZE, 1.0 / self.sample_rate).astype(np.float32)
        self._hop_seconds = self.HOP_SIZE / self.sample_rate
        self._highpass_curve = self._build_highpass(self.settings.highpass_hz)
        # Medidores para la interfaz (se leen desde otro hilo, solo lectura).
        self.input_level_db = -120.0
        self.output_level_db = -120.0
        self.gate_open = True
        # Hay voz si la salida supera el umbral de la puerta; lo usa el ducking.
        self.voice_detected = False
        self._voice_hold = 0.0
        self.reset()

    def reset(self) -> None:
        bins = self.FRAME_SIZE // 2 + 1
        self._in_buffer = np.zeros(0, dtype=np.float32)
        self._prev_tail = np.zeros(self.HOP_SIZE, dtype=np.float32)
        self._analysis = np.zeros(self.FRAME_SIZE, dtype=np.float32)
        self._overlap = np.zeros(self.HOP_SIZE, dtype=np.float32)
        # Un hop de silencio inicial evita quedarse sin muestras con bloques chicos;
        # latencia total = HOP_SIZE (prebuffer) + HOP_SIZE (solape) = FRAME_SIZE.
        self._out_buffer = np.zeros(self.HOP_SIZE, dtype=np.float32)
        self._noise_psd = np.full(bins, 1e-9, dtype=np.float32)
        self._smoothed_psd = np.full(bins, 1e-9, dtype=np.float32)
        self._spectral_gain = np.ones(bins, dtype=np.float32)
        self._frames_seen = 0
        self._gate_gain = 1.0
        self._gate_hold = 0.0

    def update_settings(self, settings: MicFilterSettings) -> None:
        if settings.highpass_hz != self.settings.highpass_hz:
            self._highpass_curve = self._build_highpass(settings.highpass_hz)
        self.settings = settings

    def _build_highpass(self, cutoff_hz: float) -> np.ndarray:
        freqs = np.maximum(self._freqs, 1.0)
        return (1.0 / np.sqrt(1.0 + (cutoff_hz / freqs) ** 4)).astype(np.float32)

    @staticmethod
    def _to_db(value: float) -> float:
        return float(20.0 * np.log10(max(value, 1e-6)))

    def process(self, block: np.ndarray) -> np.ndarray:
        block = np.asarray(block, dtype=np.float32).reshape(-1)
        frames = block.shape[0]
        self.input_level_db = self._to_db(float(np.sqrt(np.mean(block * block))) if frames else 0.0)

        settings = self.settings
        if not settings.any_enabled:
            self.gate_open = True
            self.output_level_db = self.input_level_db
            self._update_voice(frames)
            return block

        self._in_buffer = np.concatenate((self._in_buffer, block))
        produced: list[np.ndarray] = []
        while self._in_buffer.shape[0] >= self.HOP_SIZE:
            hop = self._in_buffer[: self.HOP_SIZE]
            self._in_buffer = self._in_buffer[self.HOP_SIZE :]
            produced.append(self._process_hop(hop, settings))

        if produced:
            self._out_buffer = np.concatenate([self._out_buffer, *produced])

        if self._out_buffer.shape[0] >= frames:
            output = self._out_buffer[:frames]
            self._out_buffer = self._out_buffer[frames:]
        else:
            output = np.concatenate((self._out_buffer, np.zeros(frames - self._out_buffer.shape[0], dtype=np.float32)))
            self._out_buffer = np.zeros(0, dtype=np.float32)

        self.output_level_db = self._to_db(float(np.sqrt(np.mean(output * output))) if frames else 0.0)
        self._update_voice(frames)
        return output

    def _update_voice(self, frames: int) -> None:
        if self.output_level_db >= self.settings.gate_threshold_db and (self.gate_open or not self.settings.noise_gate):
            self.voice_detected = True
            self._voice_hold = 0.5
        elif self.voice_detected:
            # Se sostiene medio segundo para no subir la musica entre palabras.
            self._voice_hold -= frames / self.sample_rate
            if self._voice_hold <= 0.0:
                self.voice_detected = False

    def _process_hop(self, hop: np.ndarray, settings: MicFilterSettings) -> np.ndarray:
        self._analysis[: self.HOP_SIZE] = self._prev_tail
        self._analysis[self.HOP_SIZE :] = hop
        self._prev_tail = hop.copy()

        spectrum = np.fft.rfft(self._analysis * self._window)
        power = (spectrum.real * spectrum.real + spectrum.imag * spectrum.imag).astype(np.float32)
        gain = np.ones_like(power)

        if settings.highpass:
            gain *= self._highpass_curve

        if settings.noise_suppression:
            gain *= self._suppression_gain(power * gain * gain, settings.suppression_strength)

        # Parseval con espectro de un lado y ventana sqrt-Hann (sum w^2 = N/2).
        frame_rms = 2.0 * float(np.sqrt(np.sum(power * gain * gain))) / self.FRAME_SIZE
        gate_target = self._gate_target(frame_rms, settings) if settings.noise_gate else 1.0
        if not settings.noise_gate:
            self.gate_open = True

        frame = np.fft.irfft(spectrum * gain, n=self.FRAME_SIZE).astype(np.float32) * self._window
        frame *= self._gate_ramp(gate_target)

        output = self._overlap + frame[: self.HOP_SIZE]
        self._overlap = frame[self.HOP_SIZE :].copy()
        return output

    def _suppression_gain(self, power: np.ndarray, strength: float) -> np.ndarray:
        self._frames_seen += 1
        # PSD suavizada en el tiempo (~30 ms) para estimar el piso de ruido.
        self._smoothed_psd = 0.7 * self._smoothed_psd + 0.3 * power
        if self._frames_seen <= 4:
            self._noise_psd = np.maximum(self._smoothed_psd, 1e-9)
        else:
            # Seguimiento de minimos: baja rapido, sube lento (~3 dB por segundo),
            # asi la voz no se "aprende" como ruido pero el piso se adapta.
            rise = np.float32(10.0 ** (3.0 * self._hop_seconds / 10.0))
            self._noise_psd = np.where(
                self._smoothed_psd < self._noise_psd,
                0.9 * self._noise_psd + 0.1 * self._smoothed_psd,
                self._noise_psd * rise,
            ).astype(np.float32)
            self._noise_psd = np.maximum(self._noise_psd, 1e-12)

        over_subtraction = 1.5 + 3.5 * strength
        floor = np.float32(10.0 ** (-(6.0 + 24.0 * strength) / 20.0))
        target = 1.0 - over_subtraction * self._noise_psd / np.maximum(power, 1e-12)
        target = np.clip(target, floor, 1.0).astype(np.float32)

        # Suavizado en frecuencia para reducir el "ruido musical".
        target = np.convolve(target, np.array([0.25, 0.5, 0.25], dtype=np.float32), mode="same")

        # Ataque rapido (la voz entra al instante) y liberacion mas lenta.
        rising = target > self._spectral_gain
        self._spectral_gain = np.where(
            rising,
            0.3 * self._spectral_gain + 0.7 * target,
            0.75 * self._spectral_gain + 0.25 * target,
        ).astype(np.float32)
        return self._spectral_gain

    def _gate_target(self, frame_rms: float, settings: MicFilterSettings) -> float:
        level_db = self._to_db(frame_rms)
        open_threshold = settings.gate_threshold_db
        close_threshold = open_threshold - 6.0
        if level_db >= open_threshold:
            self.gate_open = True
            self._gate_hold = 0.25
        elif self.gate_open and level_db < close_threshold:
            # Mantiene la puerta abierta un instante para no cortar finales de palabra.
            self._gate_hold -= self._hop_seconds
            if self._gate_hold <= 0.0:
                self.gate_open = False
        return 1.0 if self.gate_open else 0.0

    def _gate_ramp(self, target: float) -> np.ndarray:
        attack_step = self._hop_seconds / 0.005
        release_step = self._hop_seconds / 0.12
        start = self._gate_gain
        if target > start:
            end = min(target, start + attack_step)
        else:
            end = max(target, start - release_step)
        self._gate_gain = end
        if start == end:
            return np.float32(end)
        return np.linspace(start, end, self.FRAME_SIZE, dtype=np.float32)
