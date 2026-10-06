import json
import os
import stat
from pathlib import Path

import pytest

from ata import audio, bundle, demo, demo_scripts
from ata.engines.base import EngineMissing

from .conftest import run_cmd


@pytest.mark.parametrize("lang", ["pt-BR", "en", "es"])
def test_scripts_shape(lang):
    s = demo_scripts.SCRIPTS[lang]
    lines = s["lines"]
    assert 22 <= len(lines) <= 30
    assert {ln.who for ln in lines} == {"Eu", "A", "B"}
    assert sum(ln.overlap for ln in lines) == 1
    ref = demo_scripts.reference(lang)
    assert len(ref["decisions"]) == 1 and len(ref["actions"]) == 2 and len(ref["questions"]) == 1
    assert all(a["owner"] and a["due"] for a in ref["actions"])
    assert len(ref["facts"]) >= 4 and any(ch.isdigit() for f in ref["facts"] for ch in f)
    assert ref["overlap_lines"] and ref["lines"] == len(lines)
    assert any(ln.text.rstrip().endswith("?") for ln in lines)


def test_scripts_are_distinct_per_language():
    titles = {s["title"] for s in demo_scripts.SCRIPTS.values()}
    products = {s["reference"]["product"] for s in demo_scripts.SCRIPTS.values()}
    assert len(titles) == 3 and len(products) == 3


@pytest.mark.parametrize("lang", ["pt-BR", "en", "es"])
def test_demo_no_tts_end_to_end(config, lang, capsys):
    assert run_cmd(demo, ["demo", "--lang", lang, "--no-tts"], config) == 0
    out = capsys.readouterr().out.strip().splitlines()
    assert out[-1].startswith("note: ")
    note = Path(out[-1][len("note: "):])
    assert note.is_file()
    bdir = bundle.list_bundles(config.recordings)[0]
    ref = json.loads((bdir / "demo-reference.json").read_text(encoding="utf-8"))
    assert ref["language"] == lang
    assert bundle.read_meta(bdir).language == lang
    turns = bundle.read_turns(bdir)
    assert len(turns) >= 15
    me = {"pt-BR": "Eu", "en": "Me", "es": "Yo"}[lang]
    assert any(t.speaker == me for t in turns)


def test_demo_no_process(config, capsys):
    assert run_cmd(demo, ["demo", "--no-tts", "--no-process"], config) == 0
    out = capsys.readouterr().out.strip().splitlines()
    assert out[-1].startswith("note: ")
    bdir = bundle.list_bundles(config.recordings)[0]
    assert not (bdir / "turns.json").exists() and (bdir / "demo-reference.json").is_file()


FAKE_PIPER = """#!{py}
import sys, wave, struct, math
args = sys.argv[1:]
out = args[args.index("--output_file") + 1]
model = args[args.index("--model") + 1]
text = sys.stdin.read()
n = int(22050 * max(0.5, len(text.split()) * 0.3))
f = 200 + (sum(map(ord, model)) % 200)
with wave.open(out, "wb") as w:
    w.setnchannels(1); w.setsampwidth(2); w.setframerate(22050)
    w.writeframes(b"".join(struct.pack("<h", int(8000 * math.sin(2 * math.pi * f * i / 22050))) for i in range(n)))
"""


@pytest.fixture
def fake_piper(tmp_path, monkeypatch, config):
    import sys
    bindir = tmp_path / "bin"
    bindir.mkdir()
    p = bindir / "piper"
    p.write_text(FAKE_PIPER.format(py=sys.executable))
    p.chmod(p.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    vd = demo.voices_dir(config)
    vd.mkdir(parents=True)
    for name in ("pt_BR-jeff-medium", "pt_BR-faber-medium", "pt_BR-cadu-medium", "en_US-amy-low"):
        (vd / f"{name}.onnx").write_bytes(b"x")
    return p


def test_find_voices_order_and_wanted(config, fake_piper):
    vs = demo.find_voices(config, "pt-BR")
    assert [v.stem.split("-")[1] for v in vs] == ["faber", "cadu", "jeff"]
    en = demo.find_voices(config, "en")
    assert len(en) == 3 and all(v.stem == "en_US-amy-low" for v in en)  # repete a única voz
    assert [v.stem for v in demo.find_voices(config, "pt-BR", ["jeff", "amy", "cadu"])][1] == "en_US-amy-low"
    with pytest.raises(EngineMissing):
        demo.find_voices(config, "es")
    with pytest.raises(EngineMissing):
        demo.find_voices(config, "pt-BR", ["nao-existe"])


def test_demo_tts_with_fake_piper(config, fake_piper, capsys):
    assert run_cmd(demo, ["demo", "--lang", "pt-BR"], config) == 0
    out = capsys.readouterr().out.strip().splitlines()
    assert out[0].startswith("vozes: ") and out[-1].startswith("note: ")
    bdir = bundle.list_bundles(config.recordings)[0]
    meta = bundle.read_meta(bdir)
    assert meta.recorder["tts"] == "piper" and "faber" in meta.recorder["voices"]
    assert all(t.start_measured for t in meta.tracks.values())
    assert bundle.damage_report(meta, bdir) == []
    far, mic = audio.read_wav(bdir / "far.wav"), audio.read_wav(bdir / "mic.wav")
    assert len(far) == len(mic) == meta.tracks["far"].samples
    # vazamento: o mic tem energia onde só o far fala, bem mais baixa
    assert audio.rms_db(mic) < audio.rms_db(far) + 6
    assert (bdir / "turns.json").is_file()


def test_demo_tts_without_piper(config, monkeypatch):
    monkeypatch.setenv("PATH", "/nonexistent")
    with pytest.raises(EngineMissing):
        run_cmd(demo, ["demo"], config)


def test_bleed_level(config, fake_piper):
    sc = demo_scripts.SCRIPTS["pt-BR"]
    vs = demo.find_voices(config, "pt-BR")
    lines = [ln for ln in sc["lines"] if ln.who != "Eu"][:3]
    bdir = demo.tts_meeting(config, lines, "pt-BR", "vazamento", vs, str(fake_piper))
    far, mic = audio.read_wav(bdir / "far.wav"), audio.read_wav(bdir / "mic.wav")
    assert abs((audio.rms_db(far) - audio.rms_db(mic)) - 18.0) < 0.5
