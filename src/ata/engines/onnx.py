"""Motor CPU em processo: ASR com onnx-asr (Parakeet v3) e diarização/embeddings com sherpa-onnx.

Pacotes do extra ``onnx`` importados só quando o motor é escolhido; ausentes -> ``EngineMissing`` com a
linha de instalação. Modelos do sherpa-onnx (segmentação pyannote 3.0 + embedding WeSpeaker) vêm do
manifesto ``ata/data/models.toml`` e ficam em ``paths.models`` (``ata engine install --onnx``). O modelo do
onnx-asr é baixado do Hugging Face Hub pelo próprio onnx-asr na primeira execução.

Suposições sobre as APIs (README do onnx-asr 0.12 e exemplos do sherpa-onnx 1.13):
* ``onnx_asr.load_model(nome, quantization=...)`` -> modelo; ``.with_timestamps()`` -> ``recognize(wav)`` devolve
  objeto com ``text``, ``tokens``, ``timestamps`` (s, início de cada token); ``onnx_asr.load_vad("silero")`` e
  ``modelo.with_vad(vad)`` -> ``recognize`` devolve iterável de segmentos com ``start``, ``end`` e os mesmos campos.
* Tokens SentencePiece: início de palavra marcado por espaço ou "▁".
* ``sherpa_onnx.OfflineSpeakerDiarization(config).process(samples).sort_by_start_time()`` -> itens com
  ``start``, ``end``, ``speaker`` (int, 0-based).
"""

from __future__ import annotations

import importlib
import math
from pathlib import Path
from typing import Any, Iterable

import numpy as np

from .. import audio
from ..config import Config
from ..data import load_manifest, models_by_role
from ..types import Span, Word
from .base import EngineError, EngineMissing

INSTALL = "instale com: uv tool install 'ata[onnx]'"
DEFAULT_ASR_MODEL = "nemo-parakeet-tdt-0.6b-v3"
VAD_SECONDS = 25.0          # acima disso o onnx-asr precisa de VAD (Parakeet tem janela limitada)
TOKEN_END_PAD = 0.08


def _import(name: str) -> Any:
    try:
        return importlib.import_module(name)
    except ImportError:
        raise EngineMissing(f"motor onnx precisa do pacote {name}: {INSTALL}") from None


# ------------------------------------------------------------------------------------ helpers puros

def _is_word_start(tok: str) -> bool:
    return tok.startswith(" ") or tok.startswith("▁")


def tokens_to_words(tokens: Iterable[str], timestamps: Iterable[float], offset: float = 0.0,
                    end_pad: float = TOKEN_END_PAD) -> list[Word]:
    """Junta tokens SentencePiece em palavras. Início = 1º token; fim = início da próxima palavra (ou do
    último token + ``end_pad``), limitado pelo próximo começo."""
    toks = list(tokens)
    ts = [float(t) for t in timestamps]
    if len(toks) != len(ts):
        n = min(len(toks), len(ts))
        toks, ts = toks[:n], ts[:n]
    groups: list[tuple[str, float, float]] = []   # texto, início, último token
    for tok, t in zip(toks, ts):
        piece = tok.replace("▁", " ")
        if not groups or _is_word_start(tok) or groups[-1][0] == "":
            groups.append((piece.strip(), t, t))
        else:
            text, start, _ = groups[-1]
            groups[-1] = (text + piece.strip(), start, t)
    groups = [g for g in groups if g[0]]
    words: list[Word] = []
    for i, (text, start, last) in enumerate(groups):
        nxt = groups[i + 1][1] if i + 1 < len(groups) else None
        end = last + end_pad
        if nxt is not None:
            end = min(max(end, start + 0.01), nxt)
        words.append(Word(text, round(start + offset, 3), round(max(end, start) + offset, 3)))
    return words


def result_to_words(result: Any, offset: float = 0.0) -> list[Word]:
    """Resultado do onnx-asr (com timestamps) -> palavras. Sem tokens: uma palavra por espaço, tempo do
    segmento dividido igualmente."""
    tokens = getattr(result, "tokens", None)
    stamps = getattr(result, "timestamps", None)
    seg_start = float(getattr(result, "start", 0.0) or 0.0)
    if tokens and stamps:
        return tokens_to_words(tokens, stamps, offset + seg_start)
    text = str(getattr(result, "text", "") or "").split()
    end = float(getattr(result, "end", seg_start) or seg_start)
    if not text:
        return []
    step = max(0.01, (end - seg_start) / len(text))
    return [Word(w, round(offset + seg_start + i * step, 3), round(offset + seg_start + (i + 1) * step, 3))
            for i, w in enumerate(text)]


def segments_to_words(results: Iterable[Any]) -> list[Word]:
    """Saída do onnx-asr com VAD (segmentos com ``start``): timestamps dos tokens são relativos ao segmento."""
    out: list[Word] = []
    for seg in results:
        out.extend(result_to_words(seg))
    return sorted(out, key=lambda w: w.start)


def segments_to_spans(result: Iterable[Any]) -> list[Span]:
    spans = [Span(float(r.start), float(r.end), f"S{int(r.speaker)}") for r in result]
    return sorted(spans, key=lambda s: (s.start, s.end))


def concat_label_audio(samples: np.ndarray, spans: list[Span], label: str, rate: int = audio.SAMPLE_RATE,
                       max_seconds: float = 60.0, min_span: float = 0.3) -> np.ndarray:
    """Concatena (float32) os trechos de ``label`` até ``max_seconds``; ignora trechos curtos demais."""
    x = audio.to_float(samples) if np.asarray(samples).dtype == np.int16 else np.asarray(samples, np.float32)
    parts: list[np.ndarray] = []
    total = 0
    limit = int(max_seconds * rate)
    for s in sorted((s for s in spans if s.speaker == label), key=lambda s: -s.duration):
        if s.duration < min_span:
            continue
        a, b = int(s.start * rate), int(s.end * rate)
        seg = x[a:b][: max(0, limit - total)]
        if len(seg):
            parts.append(seg)
            total += len(seg)
        if total >= limit:
            break
    return np.concatenate(parts).astype(np.float32) if parts else np.zeros(0, np.float32)


def l2_normalize(v: Iterable[float]) -> list[float]:
    vals = [float(x) for x in v]
    n = math.sqrt(sum(x * x for x in vals)) or 1.0
    return [x / n for x in vals]


def model_file(config: Config, role: str, manifest: dict[str, Any] | None = None) -> Path:
    entries = models_by_role(manifest or load_manifest(), role)
    if not entries:
        raise EngineError(f"manifesto sem modelo para {role}")
    p = Path(str(config.get("paths.models"))).expanduser() / str(entries[0]["file"])
    if not p.is_file():
        raise EngineMissing(f"modelo {entries[0]['name']} ausente em {p.parent}: ata engine install --onnx")
    return p


# ---------------------------------------------------------------------------------------- motores

class OnnxAsr:
    name = "onnx"

    def __init__(self, config: Config, model: str) -> None:
        self.onnx_asr = _import("onnx_asr")
        self.model_name = model or DEFAULT_ASR_MODEL
        self.quantization = str(config.get("engine.onnx_quantization") or "int8")
        self._model: Any = None
        self._vad_model: Any = None

    def _load(self) -> tuple[Any, Any]:
        if self._model is None:
            base = self.onnx_asr.load_model(self.model_name, quantization=self.quantization or None)
            self._model = base.with_timestamps()
            try:
                vad = self.onnx_asr.load_vad("silero")
                self._vad_model = base.with_vad(vad).with_timestamps()
            except Exception:  # noqa: BLE001 - sem VAD: só áudio curto
                self._vad_model = None
        return self._model, self._vad_model

    def transcribe(self, wav: Path, language: str) -> list[Word]:
        model, vad_model = self._load()
        if audio.duration(wav) > VAD_SECONDS:
            if vad_model is None:
                raise EngineError("onnx-asr sem VAD: áudio longo não suportado nesta instalação")
            return segments_to_words(vad_model.recognize(str(wav)))
        return result_to_words(model.recognize(str(wav)))


class OnnxDiarizer:
    name = "onnx"

    def __init__(self, config: Config, model: str = "") -> None:
        self.sherpa = _import("sherpa_onnx")
        self.segmentation = model_file(config, "onnx-segmentation")
        self.embedding = model_file(config, "onnx-embedding")
        self.threshold = float(config.get("diarization.cluster_threshold") or 0.5)

    def _build(self, max_speakers: int) -> Any:
        so = self.sherpa
        cfg = so.OfflineSpeakerDiarizationConfig(
            segmentation=so.OfflineSpeakerSegmentationModelConfig(
                pyannote=so.OfflineSpeakerSegmentationPyannoteModelConfig(model=str(self.segmentation))),
            embedding=so.SpeakerEmbeddingExtractorConfig(model=str(self.embedding)),
            clustering=so.FastClusteringConfig(num_clusters=int(max_speakers) if max_speakers > 0 else -1,
                                               threshold=self.threshold),
            min_duration_on=0.3, min_duration_off=0.5)
        if not cfg.validate():
            raise EngineError("configuração do sherpa-onnx inválida (modelos corrompidos?)")
        return so.OfflineSpeakerDiarization(cfg)

    def diarize(self, wav: Path, max_speakers: int = 0) -> list[Span]:
        sd = self._build(max_speakers)
        samples = audio.to_float(audio.read_wav(wav))
        return segments_to_spans(sd.process(samples).sort_by_start_time())


class OnnxSpeakerEmbedder:
    name = "onnx"

    def __init__(self, config: Config, model: str = "") -> None:
        self.sherpa = _import("sherpa_onnx")
        self.embedding = model_file(config, "onnx-embedding")
        self._extractor: Any = None

    def _ex(self) -> Any:
        if self._extractor is None:
            cfg = self.sherpa.SpeakerEmbeddingExtractorConfig(model=str(self.embedding))
            self._extractor = self.sherpa.SpeakerEmbeddingExtractor(cfg)
        return self._extractor

    def embed(self, wav: Path, spans: list[Span]) -> dict[str, list[float]]:
        samples = audio.read_wav(wav)
        out: dict[str, list[float]] = {}
        for label in sorted({s.speaker for s in spans}):
            chunk = concat_label_audio(samples, spans, label)
            if len(chunk) < audio.SAMPLE_RATE // 2:
                continue
            ex = self._ex()
            stream = ex.create_stream()
            stream.accept_waveform(audio.SAMPLE_RATE, chunk)
            stream.input_finished()
            out[label] = l2_normalize(ex.compute(stream))
        return out


def make_asr(config: Config, model: str) -> OnnxAsr:
    return OnnxAsr(config, model)


def make_diarizer(config: Config, model: str) -> OnnxDiarizer:
    return OnnxDiarizer(config, model)


def make_speaker_embedder(config: Config, model: str) -> OnnxSpeakerEmbedder:
    return OnnxSpeakerEmbedder(config, model)
