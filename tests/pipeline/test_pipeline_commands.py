import argparse
import csv
import io
import json
import shutil
import subprocess

import numpy as np
import pytest

from ata import audio, bundle
from ata.pipeline import commands, run
from ata.pipeline.export import cues
from ata.types import Turn, Word


def cli(config, *argv):
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers()
    commands.add_parser(sub)
    args = ap.parse_args(list(argv))
    return args.func(args, config)


def crunch_bundle(dst, meeting):
    shutil.copytree(meeting, dst)
    m = bundle.read_meta(meeting)
    d = {"name": m.name, "created_at": m.created_at, "bundle_version": 2,
         "tracks": {k: {"file": f"{k}.wav", "start_epoch": t.start_epoch + (0.25 if k == "mic" else 0.0),
                        "sample_rate": 16000, "samples": t.samples, "silent": False}
                    for k, t in m.tracks.items()}}
    (dst / "meta.json").write_text(json.dumps(d))
    return dst


def test_dry_run_crunchlog(meeting, config, tmp_path, capsys):
    b = crunch_bundle(tmp_path / "crunch", meeting)
    assert cli(config, "process", "--dry-run", str(b)) == 0
    out = capsys.readouterr().out.splitlines()
    assert out[0] == 'bundle crunchlog/2 lido: 2 tracks, offsets={"far": 0.0, "mic": 0.25}'
    assert out[1] == "danos: nenhum"
    assert not (b / "turns.json").exists() and not (b / "note.md").exists()


def test_dry_run_ata_lists_damage(meeting, config, capsys):
    (meeting / "mic.wav").unlink()
    assert cli(config, "process", "--dry-run", meeting.name) == 0
    out = capsys.readouterr().out
    assert out.startswith("bundle ata/1 lido: 2 tracks, offsets=") and "danos: mic_missing" in out
    assert not (meeting / "words.json").exists()


def test_process_command_and_latest(meeting, config, capsys):
    assert cli(config, "process", "latest", "--no-summary", "--speakers", "3") == 0
    assert capsys.readouterr().out.strip() == f"note: {config.notes / (meeting.name + '.md')}"


def test_process_unknown_target(config, capsys):
    assert cli(config, "process", "nao-existe") == 2
    assert "gravação não encontrada" in capsys.readouterr().err


@pytest.mark.parametrize("fmt", ["srt", "vtt", "txt", "json", "md", "csv"])
def test_export_formats(meeting, config, fmt, tmp_path):
    run.process_bundle(meeting, config)
    out = tmp_path / f"x.{fmt}"
    assert cli(config, "export", str(meeting), "--format", fmt, "--out", str(out)) == 0
    text = out.read_text()
    if fmt == "srt":
        assert text.startswith("1\n00:00:01,") and "Eu: Bom dia" in text
    elif fmt == "vtt":
        assert text.startswith("WEBVTT") and "00:00:01." in text
    elif fmt == "txt":
        assert text.splitlines()[0].startswith("[00:01] Eu: Bom dia")
    elif fmt == "json":
        rows = json.loads(text)["turns"]
        assert rows[0]["speaker"] == "Eu" and rows[0]["label"] == "Eu"
    elif fmt == "md":
        assert text.startswith("---") and "## Transcrição" in text
    else:
        rows = list(csv.reader(io.StringIO(text)))
        assert rows[0] == ["start", "end", "speaker", "label", "track", "text"] and len(rows) > 3


def test_export_default_path_and_unprocessed(meeting, config):
    assert cli(config, "export", str(meeting), "--format", "txt") == 3
    run.process_bundle(meeting, config, summarize=False)
    assert cli(config, "export", str(meeting), "--format", "txt") == 0
    assert (meeting / f"{meeting.name}.txt").is_file()


def test_cues_respect_limits():
    words = [Word(f"palavra{i}", i * 0.5, i * 0.5 + 0.4) for i in range(40)]
    t = Turn(0.0, 20.0, "Pessoa 2", " ".join(w.text for w in words), "far", words)
    cs = cues([t], {"Pessoa 2": "Ana"})
    assert len(cs) > 2 and all(b - a <= 7.0 + 1e-6 for a, b, _ in cs)
    assert all(c.startswith("Ana: ") for _, _, c in cs)
    joined = " ".join(c.removeprefix("Ana: ") for _, _, c in cs)
    assert joined == t.text


def test_speakers_list_and_set(meeting, config, capsys):
    run.process_bundle(meeting, config)
    assert cli(config, "speakers", str(meeting)) == 0
    out = capsys.readouterr().out
    assert "Pessoa 2 (sem nome)" in out and "Eu (sem nome)" in out
    assert cli(config, "speakers", str(meeting), "Pessoa 2=Ana", "Pessoa 3=Bia") == 0
    note = (config.notes / f"{meeting.name}.md").read_text()
    assert "Ana: Tenho os números" in note and '  - "Bia"' in note
    capsys.readouterr()
    assert cli(config, "speakers", str(meeting)) == 0
    assert "Pessoa 2 = Ana" in capsys.readouterr().out
    assert cli(config, "speakers", str(meeting), "Pessoa 2=") == 0
    assert bundle.read_speaker_names(meeting) == {"Pessoa 3": "Bia"}


def test_speakers_rejects_unknown_label_and_bad_syntax(meeting, config, capsys):
    run.process_bundle(meeting, config, summarize=False)
    assert cli(config, "speakers", str(meeting), "Pessoa 9=X") == 2
    assert "rótulo inexistente" in capsys.readouterr().err
    assert cli(config, "speakers", str(meeting), "semigual") == 2


def test_speakers_via_note_path(meeting, config):
    run.process_bundle(meeting, config, summarize=False)
    note = config.notes / f"{meeting.name}.md"
    assert cli(config, "speakers", str(note), "Pessoa 2=Ana") == 0
    assert bundle.read_speaker_names(meeting) == {"Pessoa 2": "Ana"}


def test_rerender_command(meeting, config, capsys):
    assert cli(config, "rerender", str(meeting)) == 3
    run.process_bundle(meeting, config)
    (meeting / "my-notes.md").write_text("trazer o contrato", encoding="utf-8")
    assert cli(config, "rerender", str(meeting)) == 0
    assert "## Minhas anotações" in (meeting / "note.md").read_text()


def _tone_wav(path, rate=44100, channels=2, seconds=2.0):
    import wave
    t = np.arange(int(seconds * rate)) / rate
    sig = (0.3 * np.sin(2 * np.pi * 220 * t) * 32767).astype("<i2")
    sig[: int(0.5 * rate)] = 0
    data = np.repeat(sig[:, None], channels, axis=1).tobytes()
    with wave.open(str(path), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(data)
    return path


def test_import_wav_makes_far_only_bundle(config, tmp_path, capsys):
    src = _tone_wav(tmp_path / "entrevista.wav")
    assert cli(config, "import", str(src), "--title", "Entrevista", "--no-summary") == 0
    out = capsys.readouterr().out
    bdir = bundle.list_bundles(config.recordings)[0]
    meta = bundle.read_meta(bdir)
    assert set(meta.tracks) == {"far"} and meta.title == "Entrevista"
    assert len(audio.read_wav(bdir / "far.wav")) == 32000
    note = (bdir / "note.md").read_text()
    assert "Problemas da gravação" not in note and "Pessoa 2:" in note and "note: " in out


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="sem ffmpeg")
def test_import_via_ffmpeg(config, tmp_path):
    wav = _tone_wav(tmp_path / "a.wav")
    ogg = tmp_path / "a.ogg"
    subprocess.run(["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-i", str(wav), str(ogg)], check=True)
    assert cli(config, "import", str(ogg), "--no-process") == 0
    bdir = bundle.list_bundles(config.recordings)[0]
    assert abs(audio.duration(bdir / "far.wav") - 2.0) < 0.1


def test_import_missing_ffmpeg_is_dependency_error(config, tmp_path, monkeypatch):
    from ata.engines.base import EngineMissing
    from ata.pipeline import importer
    (tmp_path / "a.mp3").write_bytes(b"\x00" * 100)
    monkeypatch.setattr(importer.shutil, "which", lambda name: None)
    with pytest.raises(EngineMissing):
        importer.import_file(tmp_path / "a.mp3", config)


def test_import_missing_file(config, capsys):
    assert cli(config, "import", "/nao/existe.wav") == 2


def test_bad_lang_and_speakers_args(config):
    with pytest.raises(SystemExit):
        cli(config, "process", "latest", "--lang", "fr")
    with pytest.raises(SystemExit):
        cli(config, "process", "latest", "--speakers", "0")
