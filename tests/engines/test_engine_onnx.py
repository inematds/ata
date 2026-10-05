"""Motor onnx: só o caminho de dependência ausente e os helpers puros (objetos fake). Nada de modelo."""

from __future__ import annotations

import sys
from types import SimpleNamespace

import numpy as np
import pytest

from ata.engines import onnx
from ata.engines.base import EngineMissing
from ata.types import Span


@pytest.mark.parametrize("factory,pkg", [(onnx.make_asr, "onnx_asr"), (onnx.make_diarizer, "sherpa_onnx"),
                                         (onnx.make_speaker_embedder, "sherpa_onnx")])
def test_missing_packages(config, monkeypatch, factory, pkg):
    monkeypatch.setitem(sys.modules, pkg, None)
    with pytest.raises(EngineMissing) as exc:
        factory(config, "")
    assert "uv tool install 'ata[onnx]'" in str(exc.value) and pkg in str(exc.value)


def test_missing_model_file_says_install(config, monkeypatch):
    monkeypatch.setitem(sys.modules, "sherpa_onnx", SimpleNamespace())
    with pytest.raises(EngineMissing) as exc:
        onnx.make_diarizer(config, "")
    assert "ata engine install --onnx" in str(exc.value)


def test_tokens_to_words():
    toks = [" bom", " d", "ia", " a", " to", "dos"]
    ts = [0.5, 0.9, 1.0, 1.6, 2.0, 2.2]
    words = onnx.tokens_to_words(toks, ts, offset=10.0)
    assert [w.text for w in words] == ["bom", "dia", "a", "todos"]
    assert words[0].start == 10.5 and words[0].end <= 10.9
    assert words[1].start == 10.9 and words[1].end <= 11.6
    assert words[-1].end == pytest.approx(12.28)
    sp = onnx.tokens_to_words(["▁olá", "▁mun", "do"], [0.0, 0.4, 0.5])
    assert [w.text for w in sp] == ["olá", "mundo"]


def test_result_and_segments_to_words():
    r = SimpleNamespace(text="oi gente", tokens=[" oi", " gen", "te"], timestamps=[0.1, 0.4, 0.5])
    assert [w.text for w in onnx.result_to_words(r)] == ["oi", "gente"]
    segs = [SimpleNamespace(start=30.0, end=31.0, text="b", tokens=[" b"], timestamps=[0.2]),
            SimpleNamespace(start=2.0, end=3.0, text="a", tokens=[" a"], timestamps=[0.1])]
    words = onnx.segments_to_words(segs)
    assert [(w.text, w.start) for w in words] == [("a", 2.1), ("b", 30.2)]
    plain = SimpleNamespace(start=1.0, end=2.0, text="um dois", tokens=None, timestamps=None)
    assert [(w.text, w.start, w.end) for w in onnx.result_to_words(plain)] == [("um", 1.0, 1.5),
                                                                                ("dois", 1.5, 2.0)]


def test_segments_to_spans():
    res = [SimpleNamespace(start=3.0, end=4.0, speaker=1), SimpleNamespace(start=0.0, end=2.0, speaker=0)]
    assert onnx.segments_to_spans(res) == [Span(0.0, 2.0, "S0"), Span(3.0, 4.0, "S1")]


def test_concat_label_audio_and_normalize():
    x = np.ones(16000 * 10, np.int16) * 1000
    spans = [Span(0, 2, "S0"), Span(3, 3.1, "S0"), Span(4, 9, "S1"), Span(5, 8, "S0")]
    chunk = onnx.concat_label_audio(x, spans, "S0", max_seconds=4.0)
    assert chunk.dtype == np.float32 and len(chunk) == 16000 * 4     # 3 s + 1 s do de 2 s; curto ignorado
    assert len(onnx.concat_label_audio(x, spans, "S9")) == 0
    assert onnx.l2_normalize([3, 4]) == [0.6, 0.8]


@pytest.mark.slow
def test_real_onnx_asr_roundtrip(config, tmp_path):  # pragma: no cover - precisa de modelo baixado
    pytest.importorskip("onnx_asr")
    from ata import audio
    wav = tmp_path / "s.wav"
    audio.write_wav(wav, np.zeros(16000, np.float32))
    assert isinstance(onnx.make_asr(config, "").transcribe(wav, "pt-BR"), list)
