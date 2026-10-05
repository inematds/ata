"""Cliente do sidecar NeMo-Speech.cpp (``nemo-speech serve``) em ``engine.host:engine.port``.

HTTP só com stdlib (urllib); WebSocket do modo ao vivo com ``websockets`` (extra ``live``, importado só no
``open``). Tempo devolvido é o do ARQUIVO.

Suposições sobre a API (a pesquisa verificou só os parâmetros do WebSocket — ``word_timestamps``,
``speaker_diarization``, ``language``, ``endpointing_ms``, ``speech_contexts`` — e que ``transcribe --diarize
--json`` põe ``speaker`` 1-based por palavra; o resto segue o formato "OpenAI-compatível" e as fixtures de
``tests/engines`` são o contrato escrito):

* ``POST /v1/audio/transcriptions`` multipart: ``file``, ``model``, ``language`` (``pt``/``en``/``es``; omitido
  em ``auto``), ``response_format=verbose_json``, ``timestamp_granularities[]=word`` (também mandamos
  ``word_timestamps=true``), ``diarize=true|false`` (+ ``speaker_diarization`` e ``max_speakers`` quando > 0).
* Resposta: ``words`` no topo e/ou ``segments[].words``; palavra = ``word``|``text``; tempo =
  ``start``/``end`` | ``start_time``/``end_time`` | ``start_ms``/``end_ms`` | ``offset``/``duration``
  (unidade dos campos sem sufixo decidida uma vez por resposta: ms se o maior tempo passa da duração do
  áudio, ou de 10 000 sem duração conhecida); confiança = ``confidence``|``probability``|``score``.
* Falante: ``speaker``|``speaker_id``|``spk`` em segmentos ou palavras; inteiros (ou dígitos) com mínimo 1 são
  1-based -> ``S{n-1}``; ``SPEAKER_00``/``speaker_0`` -> ``S0``; outros rótulos -> ``S0``, ``S1``... na ordem.
* Saúde: ``GET /health`` e, se não houver, ``GET /v1/models``.
* WebSocket ``/v1/audio/transcriptions/realtime``: 1º quadro texto JSON com a configuração da sessão, depois
  quadros binários PCM16 LE mono; fim = quadro texto ``{"type": "end"}``. Eventos recebidos são JSON com
  ``type`` em (partial|interim|transcript.partial|...delta) ou (final|transcript.final|...completed) ou
  ``is_final``; normalizados para ``{"type": "partial"|"final", "text", "start", "end", "words"?, "speaker"?}``.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from .. import audio
from ..config import Config
from ..types import Span, Word
from .base import EngineError, EngineMissing

TRANSCRIBE_PATH = "/v1/audio/transcriptions"
REALTIME_PATH = "/v1/audio/transcriptions/realtime"
HEALTH_PATHS = ("/health", "/v1/models")
NOT_RUNNING = "motor nemo não está rodando: ata engine start"
LIVE_INSTALL = "instale com: uv tool install 'ata[live]'"

_LANG = {"pt-BR": "pt", "pt": "pt", "en": "en", "es": "es"}


def base_url(config: Config) -> str:
    host = str(config.get("engine.host") or "127.0.0.1")
    port = int(config.get("engine.port") or 47520)
    return f"http://{host}:{port}"


def api_language(language: str | None) -> str | None:
    if not language or language == "auto":
        return None
    return _LANG.get(language, language.split("-")[0].lower())


# ------------------------------------------------------------------------------------------------ HTTP

def _is_refused(exc: BaseException) -> bool:
    reason = getattr(exc, "reason", exc)
    return isinstance(reason, (ConnectionRefusedError, ConnectionResetError)) or (
        isinstance(reason, OSError) and getattr(reason, "errno", None) in (111, 61, 10061))


def _request(url: str, *, data: bytes | None = None, headers: dict[str, str] | None = None,
             timeout: float = 600.0, method: str | None = None) -> dict[str, Any]:
    req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as exc:
        raise EngineError(f"motor nemo respondeu HTTP {exc.code} em {urllib.parse.urlsplit(url).path}") from None
    except (urllib.error.URLError, OSError) as exc:
        if _is_refused(exc):
            raise EngineError(NOT_RUNNING) from None
        raise EngineError(f"motor nemo inacessível ({type(exc).__name__}): ata engine status") from None
    try:
        out = json.loads(raw.decode("utf-8") or "{}")
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise EngineError("motor nemo devolveu resposta que não é JSON") from None
    if not isinstance(out, dict):
        raise EngineError("motor nemo devolveu JSON inesperado (esperado objeto)")
    return out


def encode_multipart(fields: list[tuple[str, str]], files: list[tuple[str, str, bytes, str]]
                     ) -> tuple[bytes, str]:
    """(campos texto, arquivos (campo, nome, bytes, content-type)) -> (corpo, content-type)."""
    boundary = "ata-" + uuid.uuid4().hex
    parts: list[bytes] = []
    for name, value in fields:
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'
                     .encode())
    for name, filename, content, ctype in files:
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'
                     f"Content-Type: {ctype}\r\n\r\n".encode() + content + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def health(config: Config, timeout: float = 3.0) -> dict[str, Any]:
    """{"ok": bool, "path": str|None, "info": dict}. Nunca levanta."""
    url = base_url(config)
    for path in HEALTH_PATHS:
        try:
            info = _request(url + path, timeout=timeout)
            return {"ok": True, "path": path, "info": info}
        except EngineError as exc:
            if str(exc) == NOT_RUNNING:
                return {"ok": False, "path": None, "info": {}, "error": NOT_RUNNING}
            if "não é JSON" in str(exc):       # 2xx com corpo texto ("ok") também é saudável
                return {"ok": True, "path": path, "info": {}}
            continue
    return {"ok": False, "path": None, "info": {}, "error": "sem /health nem /v1/models"}


def transcribe_request(config: Config, wav: Path, *, model: str, language: str | None, diarize: bool,
                       max_speakers: int = 0, timeout: float | None = None) -> dict[str, Any]:
    fields = [("model", model), ("response_format", "verbose_json"), ("timestamp_granularities[]", "word"),
              ("word_timestamps", "true"), ("diarize", "true" if diarize else "false")]
    lang = api_language(language)
    if lang:
        fields.append(("language", lang))
    if diarize:
        fields.append(("speaker_diarization", "true"))
        if max_speakers > 0:
            fields.append(("max_speakers", str(int(max_speakers))))
    body, ctype = encode_multipart(fields, [("file", Path(wav).name, Path(wav).read_bytes(), "audio/wav")])
    t = float(timeout if timeout is not None else config.get("engine.timeout_seconds") or 1800)
    return _request(base_url(config) + TRANSCRIBE_PATH, data=body, headers={"Content-Type": ctype},
                    timeout=t, method="POST")


# ------------------------------------------------------------------------------------------- parsing

def _num(d: dict[str, Any], *keys: str) -> float | None:
    for k in keys:
        v = d.get(k)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return float(v)
        if isinstance(v, str):
            try:
                return float(v)
            except ValueError:
                continue
    return None


def _raw_times(d: dict[str, Any]) -> tuple[float, float, bool] | None:
    """(início, fim, já_em_ms_explícito) sem escalar."""
    ms_start, ms_end = _num(d, "start_ms", "startMs"), _num(d, "end_ms", "endMs")
    if ms_start is not None and ms_end is not None:
        return ms_start, ms_end, True
    start = _num(d, "start", "start_time", "startTime", "begin", "offset")
    end = _num(d, "end", "end_time", "endTime")
    if end is None and start is not None:
        dur = _num(d, "duration")
        end = start + dur if dur is not None else None
    if start is None or end is None:
        return None
    return start, end, False


def unit_scale(dicts: list[dict[str, Any]], duration: float | None = None) -> float:
    """Unidade dos campos sem unidade, decidida UMA vez por resposta: 0.001 (ms) se o maior tempo passa da
    duração do áudio (+50 %) ou, sem duração conhecida, de 10 000; senão 1 (segundos)."""
    ends = [t[1] for t in (_raw_times(d) for d in dicts) if t is not None and not t[2]]
    if not ends:
        return 1.0
    top = max(ends)
    if duration is not None and duration > 0:
        return 0.001 if top > duration * 100 and top / 1000.0 <= duration * 1.5 + 1.0 else 1.0
    return 0.001 if top > 1e4 else 1.0


def _times(d: dict[str, Any], scale: float = 1.0) -> tuple[float, float] | None:
    raw = _raw_times(d)
    if raw is None:
        return None
    start, end, is_ms = raw
    k = 0.001 if is_ms else scale
    start, end = start * k, end * k
    return start, max(start, end)


def _text(d: dict[str, Any]) -> str:
    for k in ("word", "text", "token", "punctuated_word"):
        v = d.get(k)
        if isinstance(v, str):
            return v.strip()
    return ""


def _raw_speaker(d: dict[str, Any]) -> Any:
    for k in ("speaker", "speaker_id", "spk", "speaker_label"):
        if k in d and d[k] is not None:
            return d[k]
    return None


class SpeakerMap:
    """Normaliza rótulos de falante de uma resposta inteira para ``S0``, ``S1``..."""

    def __init__(self, raw_labels: list[Any]) -> None:
        nums: list[int] = []
        numeric = bool(raw_labels)
        for r in raw_labels:
            if isinstance(r, bool):
                numeric = False
            elif isinstance(r, int):
                nums.append(r)
            elif isinstance(r, str) and r.strip().isdigit():
                nums.append(int(r))
            else:
                numeric = False
        self.one_based = numeric and bool(nums) and min(nums) >= 1
        self._order: dict[str, str] = {}

    def __call__(self, raw: Any) -> str:
        if isinstance(raw, int) and not isinstance(raw, bool) or (isinstance(raw, str) and raw.strip().isdigit()):
            n = int(raw)
            return f"S{max(0, n - 1) if self.one_based else n}"
        s = str(raw).strip()
        m = re.fullmatch(r"(?i)(?:speaker|spk|s)[ _-]?(\d+)", s)
        if m:
            return f"S{int(m.group(1))}"
        if s not in self._order:
            self._order[s] = f"S{len(self._order)}"
        return self._order[s]


def _segments(resp: dict[str, Any]) -> list[dict[str, Any]]:
    for k in ("segments", "utterances", "results"):
        v = resp.get(k)
        if isinstance(v, list):
            return [s for s in v if isinstance(s, dict)]
    return []


def _word_dicts(resp: dict[str, Any]) -> list[tuple[dict[str, Any], Any]]:
    """[(palavra, falante do segmento pai ou None)] na ordem."""
    top = resp.get("words")
    if isinstance(top, list) and top:
        return [(w, None) for w in top if isinstance(w, dict)]
    out: list[tuple[dict[str, Any], Any]] = []
    for seg in _segments(resp):
        ws = seg.get("words")
        if isinstance(ws, list):
            out.extend((w, _raw_speaker(seg)) for w in ws if isinstance(w, dict))
    return out


def parse_words(resp: dict[str, Any], duration: float | None = None) -> list[Word]:
    pairs = _word_dicts(resp)
    scale = unit_scale([d for d, _ in pairs], duration)
    words: list[Word] = []
    for d, _ in pairs:
        text, times = _text(d), _times(d, scale)
        if not text or times is None:
            continue
        conf = _num(d, "confidence", "probability", "score")
        words.append(Word(text, times[0], times[1], conf))
    words.sort(key=lambda w: (w.start, w.end))
    return words


def parse_spans(resp: dict[str, Any], merge_gap: float = 0.5, duration: float | None = None) -> list[Span]:
    """Spans de falante: segmentos com falante; senão, palavras com falante agrupadas por continuidade."""
    segs = [s for s in _segments(resp) if _raw_speaker(s) is not None and _raw_times(s) is not None]
    if segs:
        scale = unit_scale(segs, duration)
        smap = SpeakerMap([_raw_speaker(s) for s in segs])
        spans = [Span(*_times(s, scale), smap(_raw_speaker(s))) for s in segs]  # type: ignore[misc]
        return sorted(spans, key=lambda s: (s.start, s.end))
    pairs = [(d, _raw_speaker(d) if _raw_speaker(d) is not None else parent) for d, parent in _word_dicts(resp)]
    pairs = [(d, r) for d, r in pairs if r is not None and _raw_times(d) is not None]
    if not pairs:
        return []
    scale = unit_scale([d for d, _ in pairs], duration)
    smap = SpeakerMap([r for _, r in pairs])
    timed = sorted(((_times(d, scale), r) for d, r in pairs), key=lambda p: p[0])  # type: ignore[arg-type,return-value]
    spans: list[Span] = []
    for (a, b), r in timed:  # type: ignore[misc]
        spk = smap(r)
        if spans and spans[-1].speaker == spk and a - spans[-1].end <= merge_gap:
            spans[-1] = Span(spans[-1].start, max(b, spans[-1].end), spk)
        else:
            spans.append(Span(a, b, spk))
    return spans


# --------------------------------------------------------------------------------------- motores

def _duration(wav: Path) -> float | None:
    try:
        return audio.duration(wav)
    except (OSError, EOFError, ValueError):
        return None


class NemoAsr:
    name = "nemo"

    def __init__(self, config: Config, model: str) -> None:
        self.config = config
        self.model = model or "parakeet-tdt-0.6b-v3"

    def transcribe(self, wav: Path, language: str) -> list[Word]:
        resp = transcribe_request(self.config, Path(wav), model=self.model, language=language, diarize=False)
        return parse_words(resp, duration=_duration(wav))


class NemoDiarizer:
    name = "nemo"

    def __init__(self, config: Config, model: str) -> None:
        self.config = config
        self.model = model or "nemotron-3-diarization"

    def diarize(self, wav: Path, max_speakers: int = 0) -> list[Span]:
        resp = transcribe_request(self.config, Path(wav), model=self.model, language=None, diarize=True,
                                  max_speakers=max_speakers)
        return parse_spans(resp, duration=_duration(wav))


def normalize_event(msg: dict[str, Any]) -> dict[str, Any] | None:
    """Evento do servidor -> {"type": "partial"|"final", ...}; None para eventos de controle."""
    typ = str(msg.get("type") or msg.get("event") or "").lower()
    if typ in ("error",) or msg.get("error"):
        return {"type": "error", "message": "motor nemo reportou erro na sessão ao vivo"}
    is_final = msg.get("is_final")
    if isinstance(is_final, bool):
        kind = "final" if is_final else "partial"
    elif typ in ("final", "transcript.final", "transcript", "result") or typ.endswith(".completed") \
            or typ.endswith(".done") or typ.endswith("final"):
        kind = "final"
    elif typ in ("partial", "interim") or typ.endswith(".partial") or typ.endswith(".delta") \
            or typ.endswith("partial") or typ.endswith("interim"):
        kind = "partial"
    else:
        return None
    text = msg.get("text")
    if not isinstance(text, str):
        text = msg.get("transcript") if isinstance(msg.get("transcript"), str) else msg.get("delta", "")
    ev: dict[str, Any] = {"type": kind, "text": str(text or "").strip()}
    words = parse_words(msg) if isinstance(msg.get("words"), list) else []
    ws = [w for w in msg.get("words", []) if isinstance(w, dict)] if isinstance(msg.get("words"), list) else []
    times = _times(msg, unit_scale([msg, *ws]))
    if times is None and words:
        times = (words[0].start, words[-1].end)
    ev["start"], ev["end"] = times if times else (None, None)
    if words:
        ev["words"] = [w.to_json() for w in words]
    spk = _raw_speaker(msg)
    if spk is not None:
        ev["speaker"] = SpeakerMap([spk])(spk)
    return ev


class NemoStreamSession:
    def __init__(self, ws: Any, poll: float = 0.05) -> None:
        self._ws = ws
        self._poll = poll
        self._buffer: list[dict[str, Any]] = []
        self._closed = False
        self._ended = False

    def send(self, pcm16: bytes) -> None:
        if self._closed:
            raise EngineError("sessão ao vivo já fechada")
        try:
            self._ws.send(bytes(pcm16))
        except Exception as exc:  # noqa: BLE001 - qualquer falha do transporte vira EngineError
            raise EngineError(f"sessão ao vivo caiu ({type(exc).__name__})") from None

    def _recv_all(self, timeout: float) -> bool:
        """Lê o que houver até ``timeout``; True se o servidor fechou."""
        from websockets.exceptions import ConnectionClosed
        while True:
            try:
                raw = self._ws.recv(timeout=timeout)
            except TimeoutError:
                return False
            except ConnectionClosed:
                return True
            if isinstance(raw, bytes):
                continue
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if isinstance(msg, dict):
                ev = normalize_event(msg)
                if ev is not None:
                    self._buffer.append(ev)

    def events(self) -> Iterator[dict[str, Any]]:
        if not self._closed:
            self._recv_all(self._poll)
        while self._buffer:
            yield self._buffer.pop(0)

    def finish(self, timeout: float = 5.0) -> list[dict[str, Any]]:
        """Manda o fim do áudio e espera os finais (até o servidor fechar ou ``timeout``)."""
        if self._closed or self._ended:
            return []
        self._ended = True
        try:
            self._ws.send(json.dumps({"type": "end"}))
            self._recv_all(timeout)
        except Exception:  # noqa: BLE001
            pass
        out, self._buffer = self._buffer, []
        return out

    def close(self) -> None:
        if self._closed:
            return
        pending = self.finish()
        self._buffer = pending + self._buffer
        self._closed = True
        try:
            self._ws.close()
        except Exception:  # noqa: BLE001
            pass


class NemoStreaming:
    name = "nemo"

    def __init__(self, config: Config, model: str) -> None:
        self.config = config
        self.model = model or "nemotron-3.5-asr-streaming-0.6b"

    def url(self) -> str:
        return base_url(self.config).replace("http://", "ws://", 1) + REALTIME_PATH

    def session_config(self, language: str, sample_rate: int) -> dict[str, Any]:
        cfg: dict[str, Any] = {"type": "session.start", "model": self.model, "sample_rate": int(sample_rate),
                               "encoding": "pcm_s16le", "channels": 1, "word_timestamps": True,
                               "speaker_diarization": False,
                               "chunk_ms": int(self.config.get("live.chunk_ms") or 1120),
                               "endpointing_ms": 600}
        lang = api_language(language)
        if lang:
            cfg["language"] = lang
        return cfg

    def open(self, language: str, sample_rate: int = 16000) -> NemoStreamSession:
        try:
            from websockets.sync.client import connect
        except ImportError:
            raise EngineMissing(f"modo ao vivo precisa do pacote websockets: {LIVE_INSTALL}") from None
        try:
            import inspect
            extra = {"legacy": True} if "legacy" in inspect.signature(connect).parameters else {}
            ws = connect(self.url(), open_timeout=5, max_size=None, **extra)
        except (ConnectionRefusedError, OSError) as exc:
            if _is_refused(exc) or isinstance(exc, ConnectionRefusedError):
                raise EngineError(NOT_RUNNING) from None
            raise EngineError(f"motor nemo inacessível ({type(exc).__name__}): ata engine status") from None
        except Exception as exc:  # noqa: BLE001 - handshake recusado etc.
            raise EngineError(f"motor nemo recusou a sessão ao vivo ({type(exc).__name__})") from None
        ws.send(json.dumps(self.session_config(language, sample_rate)))
        return NemoStreamSession(ws)


def make_asr(config: Config, model: str) -> NemoAsr:
    return NemoAsr(config, model)


def make_diarizer(config: Config, model: str) -> NemoDiarizer:
    return NemoDiarizer(config, model)


def make_streaming(config: Config, model: str) -> NemoStreaming:
    return NemoStreaming(config, model)
