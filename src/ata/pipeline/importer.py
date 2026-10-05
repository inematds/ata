"""Importa um arquivo de áudio/vídeo como bundle ``ata/1`` só com a faixa far (16 kHz mono PCM16).

WAV é lido direto (``audio.read_any_wav``); qualquer outro formato passa pelo ``ffmpeg`` (subprocesso local).
"""

from __future__ import annotations

import shutil
import socket
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

from .. import __version__, audio, bundle, i18n
from ..config import Config
from ..engines.base import EngineError, EngineMissing
from .run import IMPORT_RECORDER

FFMPEG_TIMEOUT_S = 3600


def _ffmpeg_to_wav(src: Path, dst: Path, ffmpeg: str | None = None) -> None:
    exe = ffmpeg or shutil.which("ffmpeg")
    if not exe:
        raise EngineMissing("ffmpeg não encontrado no PATH (instale: sudo apt install ffmpeg); "
                            "ou converta para WAV antes")
    cmd = [exe, "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-i", str(src),
           "-vn", "-ac", "1", "-ar", str(audio.SAMPLE_RATE), "-c:a", "pcm_s16le", str(dst)]
    try:
        r = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=FFMPEG_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        raise EngineError("ffmpeg demorou demais para converter o arquivo") from None
    if r.returncode != 0 or not dst.is_file():
        raise EngineError(f"ffmpeg não conseguiu converter o arquivo (código {r.returncode})")


def load_audio(src: Path, ffmpeg: str | None = None):  # noqa: ANN201 - np.ndarray
    """Áudio 16 kHz mono int16 de qualquer arquivo (WAV direto, resto via ffmpeg)."""
    if src.suffix.lower() == ".wav":
        try:
            return audio.read_any_wav(src)
        except Exception:  # noqa: BLE001 - WAV exótico (float, extensível): tenta o ffmpeg
            pass
    with tempfile.TemporaryDirectory(prefix="ata-import-") as tmp:
        out = Path(tmp) / "far.wav"
        _ffmpeg_to_wav(src, out, ffmpeg)
        return audio.read_wav(out)


def import_file(src: Path | str, config: Config, *, title: str | None = None, language: str | None = None,
                ffmpeg: str | None = None) -> Path:
    """Cria o bundle (só far) e devolve a pasta. Não processa."""
    src = Path(src).expanduser()
    if not src.is_file():
        raise FileNotFoundError(str(src))
    lang = i18n.normalize(language or config.get("language.default"))
    samples = load_audio(src, ffmpeg)
    if len(samples) == 0:
        raise EngineError("o arquivo não tem áudio")
    mtime = src.stat().st_mtime
    started = datetime.fromtimestamp(mtime).astimezone()
    title = title or src.stem
    bdir = bundle.new_bundle_dir(config.recordings, started, title)
    audio.write_wav(bdir / "far.wav", samples)
    seconds = len(samples) / audio.SAMPLE_RATE
    stopped = datetime.fromtimestamp(mtime + seconds).astimezone()
    meta = bundle.BundleMeta(
        name=bdir.name, created_at=started.isoformat(timespec="seconds"),
        stopped_at=stopped.isoformat(timespec="seconds"), slug=bundle.slugify(title), title=title,
        host=socket.gethostname(), platform=sys.platform,
        recorder={"name": IMPORT_RECORDER, "version": __version__, "source": src.suffix.lower().lstrip(".")},
        language_requested=lang,
        tracks={"far": bundle.Track(file="far.wav", device="import", start_epoch=mtime, start_measured=True,
                                    samples=len(samples), silent=audio.is_silent(samples))})
    bundle.write_meta(bdir, meta)
    return bdir
