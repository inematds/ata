"""WAV 16 kHz mono PCM16: leitura, escrita, níveis e alinhamento entre faixas."""

from __future__ import annotations

import math
import struct
import wave
from pathlib import Path

import numpy as np

SAMPLE_RATE = 16000


def read_wav(path: Path | str) -> np.ndarray:
    """Lê WAV PCM16 mono 16 kHz como int16. Outros formatos -> ValueError (use resample_file)."""
    with wave.open(str(path), "rb") as w:
        if w.getsampwidth() != 2 or w.getnchannels() != 1 or w.getframerate() != SAMPLE_RATE:
            raise ValueError(f"{Path(path).name}: esperado 16 kHz mono PCM16, veio "
                             f"{w.getframerate()} Hz, {w.getnchannels()} canais, {8 * w.getsampwidth()} bits")
        raw = w.readframes(w.getnframes())
    return np.frombuffer(raw, dtype="<i2").astype(np.int16)


def write_wav(path: Path | str, samples: np.ndarray, rate: int = SAMPLE_RATE) -> Path:
    p = Path(path)
    data = to_int16(samples)
    with wave.open(str(p), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(data.astype("<i2").tobytes())
    return p


def to_int16(samples: np.ndarray) -> np.ndarray:
    a = np.asarray(samples)
    if a.dtype == np.int16:
        return a
    if np.issubdtype(a.dtype, np.floating):
        return np.clip(np.round(a * 32767.0), -32768, 32767).astype(np.int16)
    return np.clip(a, -32768, 32767).astype(np.int16)


def to_float(samples: np.ndarray) -> np.ndarray:
    return np.asarray(samples, dtype=np.float32) / 32768.0


def duration(path: Path | str) -> float:
    with wave.open(str(path), "rb") as w:
        return w.getnframes() / float(w.getframerate())


def rms_db(samples: np.ndarray) -> float:
    if len(samples) == 0:
        return -math.inf
    x = to_float(samples)
    rms = float(np.sqrt(np.mean(np.square(x, dtype=np.float64))))
    return -math.inf if rms <= 0 else 20.0 * math.log10(rms)


def window_db(samples: np.ndarray, start: float, end: float, rate: int = SAMPLE_RATE) -> float:
    a, b = max(0, int(start * rate)), max(0, int(end * rate))
    return rms_db(samples[a:b])


def is_silent(samples: np.ndarray, threshold_db: float = -60.0) -> bool:
    return rms_db(samples) < threshold_db


def repair_header(path: Path | str) -> bool:
    """Corrige tamanhos RIFF/data de um WAV cujo gravador morreu antes de fechar. True se mudou."""
    p = Path(path)
    size = p.stat().st_size
    if size < 44:
        return False
    with open(p, "r+b") as f:
        head = f.read(44)
        if head[:4] != b"RIFF" or head[8:12] != b"WAVE" or head[36:40] != b"data":
            return False
        riff, data = struct.unpack("<I", head[4:8])[0], struct.unpack("<I", head[40:44])[0]
        want_riff, want_data = size - 8, size - 44
        if (riff, data) == (want_riff, want_data):
            return False
        f.seek(4)
        f.write(struct.pack("<I", want_riff))
        f.seek(40)
        f.write(struct.pack("<I", want_data))
    return True


def first_sound(samples: np.ndarray, threshold_db: float = -55.0, block: int = 160) -> float | None:
    """Segundo do primeiro bloco de 10 ms acima do limiar; None se tudo silêncio."""
    x = to_float(samples)
    for i in range(0, len(x) - block + 1, block):
        seg = x[i:i + block]
        rms = float(np.sqrt(np.mean(seg * seg)))
        if rms > 0 and 20 * math.log10(rms) > threshold_db:
            return i / SAMPLE_RATE
    return None


def estimate_lag(far: np.ndarray, mic: np.ndarray, max_lag_s: float = 2.0, rate: int = SAMPLE_RATE,
                 analysis_s: float = 60.0) -> tuple[float, float]:
    """Atraso do mic em relação ao far (s) por correlação cruzada via FFT, e a força do pico (0..1).

    Positivo = o som do far aparece no mic ``lag`` segundos depois. Usado quando o início das faixas
    não foi medido no áudio (2× pw-record): o vazamento da caixa de som no mic dá o alinhamento.
    Força < 0.2 = sem vazamento útil (fone de ouvido), não confiar.
    """
    n = int(min(len(far), len(mic), analysis_s * rate))
    if n < rate:
        return 0.0, 0.0
    a = to_float(far[:n]).astype(np.float64)
    b = to_float(mic[:n]).astype(np.float64)
    a -= a.mean()
    b -= b.mean()
    size = 1 << (2 * n - 1).bit_length()
    corr = np.fft.irfft(np.fft.rfft(b, size) * np.conj(np.fft.rfft(a, size)), size)
    max_lag = int(max_lag_s * rate)
    lags = np.concatenate([corr[:max_lag + 1], corr[-max_lag:]])
    idx = np.concatenate([np.arange(0, max_lag + 1), np.arange(-max_lag, 0)])
    k = int(np.argmax(np.abs(lags)))
    denom = math.sqrt(float(np.dot(a, a)) * float(np.dot(b, b))) or 1.0
    return idx[k] / rate, float(abs(lags[k]) / denom)


def resample_linear(samples: np.ndarray, src_rate: int, dst_rate: int = SAMPLE_RATE) -> np.ndarray:
    """Reamostragem linear simples (suficiente para TTS/import; o motor onnx usa soxr quando houver)."""
    if src_rate == dst_rate or len(samples) == 0:
        return np.asarray(samples)
    x = to_float(to_int16(samples)) if np.asarray(samples).dtype == np.int16 else np.asarray(samples, np.float32)
    n_out = int(round(len(x) * dst_rate / src_rate))
    t_out = np.linspace(0, len(x) - 1, n_out)
    return to_int16(np.interp(t_out, np.arange(len(x)), x))


def read_any_wav(path: Path | str) -> np.ndarray:
    """Lê WAV PCM de qualquer taxa/canais/largura e devolve 16 kHz mono int16."""
    with wave.open(str(path), "rb") as w:
        rate, ch, width = w.getframerate(), w.getnchannels(), w.getsampwidth()
        raw = w.readframes(w.getnframes())
    if width == 2:
        a = np.frombuffer(raw, "<i2").astype(np.float32) / 32768.0
    elif width == 1:
        a = (np.frombuffer(raw, np.uint8).astype(np.float32) - 128.0) / 128.0
    elif width == 4:
        a = np.frombuffer(raw, "<i4").astype(np.float32) / 2147483648.0
    else:
        raise ValueError(f"largura de amostra não suportada: {width}")
    if ch > 1:
        a = a.reshape(-1, ch).mean(axis=1)
    return resample_linear(to_int16(a), rate, SAMPLE_RATE)
