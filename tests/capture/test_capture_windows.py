import sys
import threading
from types import SimpleNamespace

import numpy as np
import pytest

from ata import audio
from ata.capture.base import UnsupportedPlatform
from ata.capture.windows import (
    ChunkResampler,
    ClockMap,
    StartEstimator,
    WasapiBackend,
    WasapiTrack,
    WavWriter,
    pad_samples_needed,
    to_mono_float,
)


def test_clock_map():
    perf = iter([10.0, 10.002])
    c = ClockMap.capture(time_fn=lambda: 1000.0, perf_fn=lambda: next(perf))
    assert c.perf0 == pytest.approx(10.001)
    assert c.to_epoch(12.001) == pytest.approx(1002.0)


def test_pad_samples_needed():
    # dentro da tolerância: nada
    assert pad_samples_needed(16000, 100.0, 101.2) == 0
    # 2 s sem callback: completa até now - tolerância
    assert pad_samples_needed(16000, 100.0, 103.0) == int(2.75 * 16000) - 16000
    # adiantado (callback acabou de chegar): nunca negativo
    assert pad_samples_needed(48000, 100.0, 101.0) == 0


def test_start_estimator_median_ignores_jitter():
    e = StartEstimator(limit=5)
    assert e.estimate() is None
    for cb, written in [(100.02, 0.02), (100.04, 0.04), (100.30, 0.06), (100.08, 0.08), (100.10, 0.10),
                        (999.0, 0.0)]:
        e.add(cb, written)
    assert e.estimate() == pytest.approx(100.0)
    assert len(e.values) == 5


@pytest.mark.parametrize("src,chunk", [(48000, 480), (48000, 1024), (44100, 441), (44100, 1000), (16000, 320)])
def test_chunk_resampler_no_drift(src, chunk):
    r = ChunkResampler(src)
    total = 0
    secs = 3
    x = np.sin(2 * np.pi * 300 * np.arange(src * secs) / src).astype(np.float32)
    for i in range(0, len(x), chunk):
        total += len(r.push(x[i:i + chunk]))
    assert abs(total - 16000 * secs) <= 1


def test_chunk_resampler_continuity():
    r = ChunkResampler(48000)
    x = np.sin(2 * np.pi * 200 * np.arange(48000) / 48000).astype(np.float32)
    y = np.concatenate([r.push(x[i:i + 777]) for i in range(0, len(x), 777)])
    ref = np.sin(2 * np.pi * 200 * np.arange(len(y)) * 3 / 48000)
    assert np.max(np.abs(y - ref)) < 0.01


def test_to_mono_float():
    stereo = np.array([1000, 3000, -2000, -4000], "<i2").tobytes()
    assert to_mono_float(stereo, 2) == pytest.approx(np.array([2000, -3000]) / 32768.0)
    f = np.array([0.5, -0.5, 0.25, 0.75], "<f4").tobytes()
    assert to_mono_float(f, 2, "float32") == pytest.approx([0.0, 0.5])
    with pytest.raises(ValueError):
        to_mono_float(b"", 1, "int24")


def test_wav_writer_incremental(tmp_path):
    w = WavWriter(tmp_path / "x.wav")
    w.write(np.full(1000, 0.1, np.float32))
    assert audio.duration(tmp_path / "x.wav") == pytest.approx(1000 / 16000)   # válido no meio da gravação
    w.pad(500)
    w.close()
    x = audio.read_wav(tmp_path / "x.wav")
    assert len(x) == 1500 and w.samples == 1500 and np.all(x[1000:] == 0)


def make_track(tmp_path, name="far", anchor=1000.0):
    clock = ClockMap(epoch0=1000.0, perf0=0.0)
    return WasapiTrack(name, tmp_path / f"{name}.wav", "dev", 48000, 2, clock, anchor_epoch=anchor)


def test_track_keepalive_pads_when_loopback_silent(tmp_path):
    t = make_track(tmp_path)
    chunk = np.zeros(960 * 2, "<i2").tobytes()       # 20 ms estéreo 48 kHz
    for i in range(1, 51):                           # 1 s de callbacks
        t.on_audio(chunk, i * 0.02)
    assert t.writer.samples == pytest.approx(16000, abs=2)
    assert t.start_epoch == pytest.approx(1000.0, abs=0.001)
    assert t.keepalive(1001.1) == 0                  # atraso normal
    n = t.keepalive(1004.0)                          # 3 s sem callback: loopback calado
    assert n > 0 and t.writer.samples == int((4.0 - 0.25) * 16000)
    t.close()


def test_track_thread_safety(tmp_path):
    t = make_track(tmp_path)
    chunk = np.ones(960 * 2, "<i2").tobytes()
    th = threading.Thread(target=lambda: [t.on_audio(chunk, i * 0.02) for i in range(1, 200)])
    th.start()
    for k in range(50):
        t.keepalive(1000.0 + k * 0.01)
    th.join()
    t.close()
    assert len(audio.read_wav(tmp_path / "far.wav")) == t.writer.samples


def test_backend_requires_windows(tmp_path):
    if sys.platform == "win32":
        pytest.skip("só fora do Windows")
    with pytest.raises(UnsupportedPlatform):
        WasapiBackend().start(tmp_path)
    assert WasapiBackend().devices()["available"] is False


class FakeStream:
    def __init__(self, cb, rate, ch):
        self.cb, self.rate, self.ch, self.active = cb, rate, ch, False

    def start_stream(self):
        self.active = True

    def is_active(self):
        return self.active

    def stop_stream(self):
        self.active = False

    def close(self):
        self.active = False


class FakePA:
    def __init__(self, streams):
        self.streams = streams

    def get_default_wasapi_loopback(self):
        return {"index": 7, "name": "Alto-falantes [Loopback]", "defaultSampleRate": 48000,
                "maxInputChannels": 2, "isLoopbackDevice": True}

    def get_default_input_device_info(self):
        return {"index": 2, "name": "Microfone", "defaultSampleRate": 48000, "maxInputChannels": 1}

    def get_device_count(self):
        return 0

    def open(self, *, format, channels, rate, input, input_device_index, frames_per_buffer, stream_callback):
        s = FakeStream(stream_callback, rate, channels)
        self.streams.append(s)
        return s

    def terminate(self):
        pass


def test_backend_with_fake_pyaudio(tmp_path):
    streams = []
    mod = SimpleNamespace(PyAudio=lambda: FakePA(streams), paInt16=8, paContinue=0)
    now = {"t": 5000.0}
    b = WasapiBackend(pyaudio_module=mod, time_fn=lambda: now["t"], perf_fn=lambda: now["t"] - 4000.0)
    h = b.start(tmp_path)
    far, mic = streams
    assert far.ch == 2 and mic.ch == 1 and h.poll() is None
    for i in range(50):                                   # 1 s de áudio nos dois
        now["t"] += 0.02
        far.cb(np.full(960 * 2, 3000, "<i2").tobytes(), 960, {}, 0)
        mic.cb(np.full(960, 3000, "<i2").tobytes(), 960, {}, 0)
    now["t"] += 2.0                                       # mic travou 2 s
    tracks = h.stop()
    assert tracks["far"].start_measured and tracks["mic"].start_measured
    assert tracks["far"].start_epoch == pytest.approx(5000.0, abs=0.01)
    assert tracks["mic"].samples >= 16000 + int(1.7 * 16000)
    assert "dropped_frames" in h.damage_reasons and h.warnings
