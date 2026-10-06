"""Modo ao vivo: `ata live start [--meeting alvo] | tail | stop`.

Segue os ``far.wav``/``mic.wav`` que o gravador está escrevendo (pula o cabeçalho de 44 bytes, PCM16 16 kHz),
manda pedaços de ~1 s para uma sessão do ``StreamingAsr`` por faixa (``registry.streaming_for``) e grava cada
evento final como turno em ``live/turns.jsonl`` (mic = Eu, far = Pessoa, no idioma do bundle). Opcional:
resumo a cada N turnos em ``live/summaries.jsonl`` e marcação dos itens ``(qN)`` da cola em ``live/prep.json``.

O ao vivo é só prévia: a nota final vem do processamento dos arquivos. Nunca bloqueia a gravação e não
escreve texto de reunião em log (só em ``live/*.jsonl``, que é dado do bundle).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from . import bundle, i18n
from .config import Config
from .engines import registry
from .types import TRACKS

HEADER_BYTES = 44
BYTES_PER_SECOND = 16000 * 2
LIVE_DIR = "live"
STOP_NAME = "STOP"
PREP_THRESHOLD = 0.5


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _mmss(t: float) -> str:
    s = max(0, int(t))
    return f"{s // 60:02d}:{s % 60:02d}"


def _append_jsonl(path: Path, obj: dict[str, Any]) -> None:
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(obj, ensure_ascii=False) + "\n")


class TrackFollower:
    """Lê o PCM novo de um WAV que cresce (a partir do byte 44), em múltiplos de 2 bytes."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.pos = HEADER_BYTES

    def read(self, max_bytes: int | None = None, min_bytes: int = 2) -> bytes:
        try:
            size = self.path.stat().st_size
        except OSError:
            return b""
        avail = size - self.pos
        if max_bytes is not None:
            avail = min(avail, max_bytes)
        avail -= avail % 2
        if avail < min_bytes:
            return b""
        with open(self.path, "rb") as f:
            f.seek(self.pos)
            data = f.read(avail)
        self.pos += len(data)
        return data


def _tokens(text: str) -> set[str]:
    import unicodedata
    folded = unicodedata.normalize("NFKD", text.lower()).encode("ascii", "ignore").decode()
    return {w for w in re.findall(r"[a-z0-9]+", folded) if len(w) >= 3}


def overlap(item: str, answer: str) -> float:
    """Fração dos tokens de conteúdo da pergunta que aparecem na fala (usa a da parte D se houver)."""
    try:
        from .knowledge.prep import overlap as _overlap  # parte D
        return float(_overlap(item, answer))
    except ImportError:
        it = _tokens(item)
        return len(it & _tokens(answer)) / len(it) if it else 0.0


class LiveSession:
    def __init__(self, bdir: Path, config: Config, *, streaming: Any = None, language: str | None = None,
                 summary_every: int = 0, chunk_ms: int | None = None, summarizer: Any = None) -> None:
        self.bdir = Path(bdir)
        self.config = config
        self.dir = self.bdir / LIVE_DIR
        self.dir.mkdir(exist_ok=True)
        meta = None
        try:
            meta = bundle.read_meta(self.bdir)
        except bundle.BundleError:
            pass
        self.language = i18n.normalize(language or (meta.language if meta else None)
                                       or config.get("language.default"))
        if self.language == "auto":
            self.language = i18n.DEFAULT_LANGUAGE
        self.offsets = bundle.track_offsets(meta) if meta else {}
        self.streaming = streaming if streaming is not None else registry.streaming_for(config)
        ms = int(chunk_ms or config.get("live.chunk_ms") or 1000)
        self.chunk_bytes = max(2, (ms * BYTES_PER_SECOND // 1000) // 2 * 2)
        self.followers = {t: TrackFollower(self.bdir / f"{t}.wav") for t in TRACKS}
        self.sessions: dict[str, Any] = {}
        self.summary_every = max(0, int(summary_every or 0))
        self._summarizer = summarizer
        self.turns: list[dict[str, Any]] = []
        self._since_summary = 0
        self.turns_path = self.dir / "turns.jsonl"
        self.prep_path = self.dir / "prep.json"
        self.prep = self._load_prep()
        if self.turns_path.is_file():  # retomada: continua a contagem
            self.turns = [json.loads(x) for x in self.turns_path.read_text(encoding="utf-8").splitlines() if x]

    # ---- cola ---------------------------------------------------------------------------------------------
    def _load_prep(self) -> dict[str, Any] | None:
        if not self.prep_path.is_file():
            return None
        try:
            return json.loads(self.prep_path.read_text(encoding="utf-8"))
        except ValueError:
            return None

    def load_prep_query(self, query: str, days: int = 90) -> int:
        """Monta a cola pela parte D e grava os itens ``(qN)`` em live/prep.json. Devolve quantos itens."""
        from .knowledge import prep as prep_mod
        prep_mod.build_prep(self.config, query, days=days)
        items = prep_mod.prep_items(prep_mod.prep_path(self.config, query))
        self.set_prep(items, query=query)
        return len(items)

    def set_prep(self, items: list[dict[str, Any]], query: str | None = None) -> None:
        rows = [{"q": str(it.get("q") or it.get("id")), "text": str(it["text"]), "done": bool(it.get("done")),
                 "answered_at": None, "turn": None} for it in items]
        self.prep = {"query": query, "items": rows}
        bundle.write_json(self.prep_path, self.prep)

    def _tick(self, turn: dict[str, Any], index: int) -> list[str]:
        if not self.prep:
            return []
        ticked = []
        for it in self.prep.get("items", []):
            if it.get("done"):
                continue
            if overlap(it["text"], turn["text"]) >= PREP_THRESHOLD:
                it.update(done=True, answered_at=round(turn["start"], 2), turn=index)
                ticked.append(it["q"])
        if ticked:
            bundle.write_json(self.prep_path, self.prep)
        return ticked

    # ---- turnos -------------------------------------------------------------------------------------------
    def _speaker(self, track: str) -> str:
        return i18n.me_label(self.language) if track == "mic" else i18n.t(self.language, "other")

    def _session(self, track: str) -> Any:
        if track not in self.sessions:
            self.sessions[track] = self.streaming.open(self.language, 16000)
        return self.sessions[track]

    def _drain(self, track: str) -> int:
        sess = self.sessions.get(track)
        if sess is None:
            return 0
        n = 0
        for ev in sess.events():
            if ev.get("type") != "final" or not str(ev.get("text", "")).strip():
                continue
            off = float(self.offsets.get(track, 0.0))
            turn = {"start": round(float(ev.get("start", 0.0)) + off, 3),
                    "end": round(float(ev.get("end", ev.get("start", 0.0))) + off, 3),
                    "speaker": self._speaker(track), "track": track, "text": str(ev["text"]).strip()}
            turn["t"] = _mmss(turn["start"])
            idx = len(self.turns)
            ticked = self._tick(turn, idx)
            if ticked:
                turn["answers"] = ticked
            self.turns.append(turn)
            _append_jsonl(self.turns_path, turn)
            n += 1
            self._since_summary += 1
            if self.summary_every and self._since_summary >= self.summary_every:
                self._summarize()
        return n

    def step(self) -> int:
        """Uma passada: lê o PCM novo de cada faixa, manda ao motor, grava turnos finais. Devolve turnos novos."""
        new = 0
        for track, fol in self.followers.items():
            while True:
                data = fol.read(self.chunk_bytes, min_bytes=self.chunk_bytes)
                if not data:
                    break
                self._session(track).send(data)
                new += self._drain(track)
        return new

    def finish(self) -> int:
        """Manda o resto (pedaço parcial), fecha as sessões e grava os últimos finais."""
        new = 0
        for track, fol in self.followers.items():
            data = fol.read()
            if data:
                self._session(track).send(data)
            new += self._drain(track)
        for track, sess in list(self.sessions.items()):
            try:
                sess.close()
            except Exception:  # noqa: BLE001
                pass
            new += self._drain(track)
        self.sessions.clear()
        return new

    # ---- resumo por turno ---------------------------------------------------------------------------------
    def _summarize(self) -> None:
        self._since_summary = 0
        summ = self._summarizer
        if summ is None:
            try:
                summ = self._summarizer = registry.summarizer_for(self.config)
            except Exception:  # noqa: BLE001
                summ = None
        if summ is None:
            return
        recent = self.turns[-max(self.summary_every, 1) * 2:]
        lines = "\n".join(f"[{t['t']}] {t['speaker']}: {t['text']}" for t in recent)
        prompt = (f"LANGUAGE: {self.language}\nResuma os turnos mais recentes de uma reunião ao vivo em JSON "
                  f"(tldr curto, decisões, ações, perguntas).\n\n{lines}\n")
        try:
            from .knowledge.summary import SUMMARY_SCHEMA as schema  # parte D
        except ImportError:
            schema = {"type": "object"}
        try:
            out = summ.summarize(prompt, schema)
        except Exception as exc:  # noqa: BLE001 - nunca derruba o ao vivo
            self._status(error=f"resumo falhou ({type(exc).__name__})")
            return
        _append_jsonl(self.dir / "summaries.jsonl", {"at_turn": len(self.turns), "at": _now(), "summary": out})

    # ---- laço ---------------------------------------------------------------------------------------------
    def _status(self, **kw: Any) -> None:
        d = {"phase": "running", "pid": os.getpid(), "turns": len(self.turns), "language": self.language,
             "updated_at": _now()}
        d.update(kw)
        bundle.write_json(self.dir / "status.json", d)

    def stop_requested(self) -> bool:
        return (self.dir / STOP_NAME).exists() or (self.bdir / bundle.STOP_FILE).exists()

    def recording_finished(self) -> bool:
        try:
            return bundle.read_meta(self.bdir).stopped_at is not None
        except bundle.BundleError:
            return False

    def run(self, *, poll: float = 0.25, max_seconds: float | None = None, idle_timeout: float | None = None,
            log: Any = None) -> int:
        (self.dir / STOP_NAME).unlink(missing_ok=True)
        t0 = last_data = time.monotonic()
        self._status()
        try:
            while True:
                n = self.step()
                positions = sum(f.pos for f in self.followers.values())
                if n or positions != getattr(self, "_last_pos", None):
                    last_data = time.monotonic()
                    self._last_pos = positions
                    self._status()
                    if log and n:
                        for t in self.turns[-n:]:
                            log(t)
                now = time.monotonic()
                if self.stop_requested() or self.recording_finished():
                    break
                if max_seconds is not None and now - t0 >= max_seconds:
                    break
                if idle_timeout is not None and now - last_data >= idle_timeout:
                    break
                time.sleep(poll)
        finally:
            n = self.finish()
            if log and n:
                for t in self.turns[-n:]:
                    log(t)
            self._status(phase="stopped")
        return len(self.turns)


# ---- comandos ----------------------------------------------------------------------------------------------

def _resolve_target(config: Config, target: str | None, *, need_recording: bool) -> Path | None:
    if target:
        return bundle.resolve(target, config.recordings)
    try:
        from . import recorder  # parte A
        st = recorder.status(config)
        if st.get("phase") == "recording" and st.get("bundle"):
            return Path(st["bundle"])
    except ImportError:
        pass
    if need_recording:
        return None
    found = [b for b in bundle.list_bundles(config.recordings) if (b / LIVE_DIR / "turns.jsonl").is_file()]
    return found[0] if found else None


def format_turn(t: dict[str, Any]) -> str:
    extra = f"  ✓ {', '.join(t['answers'])}" if t.get("answers") else ""
    return f"[{t.get('t') or _mmss(t.get('start', 0))}] {t['speaker']}: {t['text']}{extra}"


def _cmd_start(args: argparse.Namespace, config: Config) -> int:
    try:
        bdir = _resolve_target(config, args.meeting, need_recording=True)
    except bundle.BundleError as exc:
        print(f"erro: {exc}", file=sys.stderr)
        return 2
    if bdir is None:
        print("nada gravando: comece com `ata start` ou passe --meeting", file=sys.stderr)
        return 3
    if args.detach:
        cmd = [sys.executable, "-m", "ata.cli"]
        if config.source:
            cmd += ["--config", str(config.source)]
        cmd += ["live", "start", "--meeting", str(bdir), "--summary-every", str(args.summary_every)]
        if args.prep:
            cmd += ["--prep", args.prep]
        (bdir / LIVE_DIR).mkdir(exist_ok=True)
        with open(bdir / LIVE_DIR / "producer.log", "ab") as logf:
            p = subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=logf,
                                 start_new_session=True)
        print(f"ao vivo em segundo plano (pid {p.pid}): {bdir.name}")
        return 0
    sess = LiveSession(bdir, config, summary_every=args.summary_every)
    if args.prep:
        try:
            n = sess.load_prep_query(args.prep)
            print(f"cola carregada: {n} itens")
        except ImportError:
            print("aviso: cola indisponível (ata.knowledge.prep)", file=sys.stderr)
    print(f"ao vivo: {bdir.name} (Ctrl+C ou `ata live stop` para parar)")
    try:
        total = sess.run(max_seconds=args.max_seconds, idle_timeout=args.idle_timeout,
                         log=(lambda t: print(format_turn(t), flush=True)) if args.print else None)
    except KeyboardInterrupt:
        total = len(sess.turns)
    print(f"ao vivo parado: {total} turnos em {bdir / LIVE_DIR / 'turns.jsonl'}")
    return 0


def tail(bdir: Path, *, once: bool = False, poll: float = 0.25, max_seconds: float | None = None,
         out: Any = None) -> int:
    out = out or sys.stdout
    path = bdir / LIVE_DIR / "turns.jsonl"
    status = bdir / LIVE_DIR / "status.json"
    seen, t0 = 0, time.monotonic()
    while True:
        lines = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
        for line in lines[seen:]:
            try:
                print(format_turn(json.loads(line)), file=out, flush=True)
            except (ValueError, KeyError):
                pass
        seen = len(lines)
        if once:
            return seen
        try:
            stopped = json.loads(status.read_text(encoding="utf-8")).get("phase") == "stopped"
        except (OSError, ValueError):
            stopped = False
        if stopped and seen == len(path.read_text(encoding="utf-8").splitlines() if path.is_file() else []):
            return seen
        if max_seconds is not None and time.monotonic() - t0 >= max_seconds:
            return seen
        time.sleep(poll)


def _cmd_tail(args: argparse.Namespace, config: Config) -> int:
    try:
        bdir = _resolve_target(config, args.meeting, need_recording=False)
    except bundle.BundleError as exc:
        print(f"erro: {exc}", file=sys.stderr)
        return 2
    if bdir is None:
        print("nenhuma sessão ao vivo encontrada", file=sys.stderr)
        return 3
    try:
        tail(bdir, once=args.once, max_seconds=args.max_seconds)
    except KeyboardInterrupt:
        pass
    return 0


def _cmd_stop(args: argparse.Namespace, config: Config) -> int:
    try:
        bdir = _resolve_target(config, args.meeting, need_recording=False)
    except bundle.BundleError as exc:
        print(f"erro: {exc}", file=sys.stderr)
        return 2
    if bdir is None or not (bdir / LIVE_DIR).is_dir():
        print("nenhuma sessão ao vivo encontrada", file=sys.stderr)
        return 3
    (bdir / LIVE_DIR / STOP_NAME).touch()
    print(f"pedido de parada enviado: {bdir.name}")
    return 0


def add_parser(sub: Any) -> None:
    p = sub.add_parser("live", help="transcrição ao vivo da gravação em andamento (prévia)",
                       description="Modo ao vivo: segue os WAVs da gravação e grava turnos em live/turns.jsonl.")
    lsub = p.add_subparsers(dest="live_cmd", metavar="ação")
    lsub.required = True
    s = lsub.add_parser("start", help="começa o ao vivo (padrão: a gravação atual)")
    s.add_argument("--meeting", help="alvo (pasta, nome ou 'latest')")
    s.add_argument("--summary-every", type=int, default=0, help="resumo a cada N turnos (0 = desligado)")
    s.add_argument("--prep", help="assunto da cola: marca os itens (qN) respondidos")
    s.add_argument("--max-seconds", type=float, default=None, help="para depois de S segundos")
    s.add_argument("--idle-timeout", type=float, default=None, help="para após S segundos sem áudio novo")
    s.add_argument("--print", action="store_true", help="imprime cada turno")
    s.add_argument("--detach", action="store_true", help="roda em segundo plano")
    s.set_defaults(func=_cmd_start)
    t = lsub.add_parser("tail", help="mostra os turnos ao vivo conforme chegam")
    t.add_argument("--meeting")
    t.add_argument("--once", action="store_true", help="imprime o que já existe e sai")
    t.add_argument("--max-seconds", type=float, default=None)
    t.set_defaults(func=_cmd_tail)
    st = lsub.add_parser("stop", help="para o ao vivo")
    st.add_argument("--meeting")
    st.set_defaults(func=_cmd_stop)
