import json
import sys
import types

import pytest

from ata import bundle
from ata.engines import registry
from ata.engines.base import EngineError
from ata.engines.fake import FakeAsr
from ata.pipeline import hooks, run
from ata.testing import DEFAULT_LINES_PT, Line, synth_meeting

# implementações reais dos hooks, capturadas antes do monkeypatch do conftest
REAL = {"summarize": hooks.summarize, "auto_label": hooks.auto_label, "index": hooks.index,
        "after_note": hooks.after_note}


def test_process_end_to_end(meeting, config, stub_hooks):
    note = run.process_bundle(meeting, config)
    assert note == config.notes / f"{meeting.name}.md" and note.is_file()
    assert (meeting / "note.md").read_text() == note.read_text()
    for f in ("words.json", "turns.json", "summary.json", "done.json", "pipeline.log"):
        assert (meeting / f).is_file(), f
    text = note.read_text()
    assert "[00:01] Eu: Bom dia a todos" in text
    assert "Pessoa 2: Tenho os números" in text and "Pessoa 3: A maior parte" in text
    assert "## Decisões" in text and "## Problemas da gravação" not in text
    done = json.loads((meeting / "done.json").read_text())
    assert done["ok"] and done["engines"]["asr"] == "fake" and done["gated"] > 0
    assert bundle.read_meta(meeting).engines["asr"] == "fake"
    assert stub_hooks["index"] == [meeting] and stub_hooks["after_note"] == [note]


def test_gate_removes_echo_from_mic(meeting, config):
    run.process_bundle(meeting, config, summarize=False)
    mic = {w.text for w in bundle.read_words(meeting)["mic"]}
    assert "Decidimos" not in mic and "tablets" not in mic and "Bom" in mic
    turns = bundle.read_turns(meeting)
    assert all(t.track == "mic" for t in turns if t.speaker == "Eu")


def test_turns_json_has_talk_time(meeting, config):
    run.process_bundle(meeting, config, summarize=False)
    d = json.loads((meeting / "turns.json").read_text())
    stats = d["speakers"]["stats"]
    assert set(stats) == {"Eu", "Pessoa 2", "Pessoa 3"} and stats["Eu"]["seconds"] > 0
    assert d["speakers"]["me_label"] == "Eu"


def test_no_summary_flag(meeting, config, stub_hooks):
    run.process_bundle(meeting, config, summarize=False)
    assert stub_hooks["summarize"] == [] and not (meeting / "summary.json").exists()
    assert "(sem resumo)" in (meeting / "note.md").read_text()


def test_pipeline_log_has_no_meeting_text(meeting, config):
    run.process_bundle(meeting, config)
    log = (meeting / "pipeline.log").read_text().lower()
    vocab = {w.lower() for line in DEFAULT_LINES_PT for w in line.text.split() if len(w) > 4}
    assert vocab and not [w for w in vocab if w in log]
    assert "asr" in log and "gate" in log


def test_offsets_applied_once(config):
    b = synth_meeting(config.recordings, [Line("Eu", "um dois tres"), Line("A", "quatro cinco")], bleed_db=None)
    m = bundle.read_meta(b)
    tracks = dict(m.tracks)
    tracks["mic"] = bundle.Track("mic.wav", start_epoch=tracks["far"].start_epoch + 2.0, start_measured=True,
                                 samples=tracks["mic"].samples, silent=False)
    bundle.write_meta(b, m.with_(tracks=tracks))
    raw = FakeAsr().transcribe(b / "mic.wav", "pt-BR")
    run.process_bundle(b, config, summarize=False)
    got = bundle.read_words(b)["mic"]
    assert [round(w.start - r.start, 3) for w, r in zip(got, raw)] == [2.0] * len(raw)


def test_asr_fallback_recorded(meeting, config, monkeypatch):
    class Broken:
        name = "nemo"

        def transcribe(self, wav, language):
            raise EngineError("sidecar fora do ar")

    monkeypatch.setattr(registry, "asr_for", lambda c, lang: Broken())
    run.process_bundle(meeting, config, summarize=False)
    eng = bundle.read_meta(meeting).engines
    assert eng["asr_fallback"] == "yes" and eng["asr"] == "fake"
    assert "asr_fallback" in (meeting / "pipeline.log").read_text()


def test_damaged_bundle_still_processes(meeting, config):
    (meeting / "mic.wav").unlink()
    note = run.process_bundle(meeting, config, summarize=False)
    text = note.read_text()
    assert "## Problemas da gravação" in text and "`mic_missing`" in text
    assert "Pessoa 2:" in text and json.loads((meeting / "done.json").read_text())["ok"]


def test_both_tracks_missing_writes_note(meeting, config):
    (meeting / "mic.wav").unlink()
    (meeting / "far.wav").unlink()
    text = run.process_bundle(meeting, config).read_text()
    assert "`far_missing`" in text and "`mic_missing`" in text


def test_speakers_hint_limits_far(meeting, config, monkeypatch):
    seen = {}
    real = registry.build

    def spy(role, ref, cfg):
        eng = real(role, ref, cfg)
        if role == "diarizer":
            orig = eng.diarize

            def diarize(wav, max_speakers=0):
                seen["max"] = max_speakers
                return orig(wav, max_speakers)
            eng.diarize = diarize
        return eng

    monkeypatch.setattr(registry, "build", spy)
    run.process_bundle(meeting, config, speakers=3, summarize=False)
    assert seen["max"] == 2


def test_language_auto_uses_detected_or_default(config):
    b = synth_meeting(config.recordings, [Line("Eu", "hello there"), Line("A", "we decided")], language="auto")
    run.process_bundle(b, config, summarize=False)
    m = bundle.read_meta(b)
    assert m.language_requested == "auto" and m.language_detected == "pt-BR"
    meta = m.with_(language_detected="en")
    bundle.write_meta(b, meta)
    text = run.process_bundle(b, config, summarize=False).read_text()
    assert "## Transcript" in text and "[00:01] Me:" in text


def test_detect_language_engine_hook(tmp_path):
    class WithLid:
        def detect_language(self, wav):
            return "es"

    assert run.detect_language(WithLid(), tmp_path / "x.wav") == "es"
    assert run.detect_language(object(), tmp_path / "x.wav") is None


def test_language_override_en(meeting, config):
    text = run.process_bundle(meeting, config, language="en", summarize=False).read_text()
    assert "# Meeting" in text and "Speaker 2:" in text and "tags: [meeting]" in text


def test_cleanup_and_glossary_applied(config, tmp_path):
    g = tmp_path / "glos.txt"
    g.write_text("crunch log => CrunchLog\n", encoding="utf-8")
    cfg = config.with_overrides(**{"cleanup.glossary": str(g)})
    b = synth_meeting(cfg.recordings, [Line("Eu", "hum, o o crunch log grava"), Line("A", "ahn certo")],
                      bleed_db=None)
    text = run.process_bundle(b, cfg, summarize=False).read_text()
    assert "Eu: o CrunchLog grava" in text and "Pessoa 2: certo" in text


def test_voices_hook_names_speakers(meeting, config, monkeypatch, stub_hooks):
    cfg = config.with_overrides(**{"voices.enabled": True})
    monkeypatch.setattr(hooks, "auto_label", lambda b, c, spans, labels: {"Pessoa 2": "Ana"})
    text = run.process_bundle(meeting, cfg, summarize=False).read_text()
    assert "Ana: Tenho os números" in text
    assert bundle.read_speaker_names(meeting) == {"Pessoa 2": "Ana"}


def test_rerender_applies_names(meeting, config):
    run.process_bundle(meeting, config)
    bundle.write_speaker_names(meeting, {"Pessoa 3": "Bia"})
    text = run.rerender(meeting, config).read_text()
    assert "Bia: A maior parte" in text and "responsável: Bia" in text


def test_compat_bundle_is_migrated_not_modified(tmp_path, config):
    src = synth_meeting(tmp_path / "crunch", DEFAULT_LINES_PT, title="demo")
    m = bundle.read_meta(src)
    crunch = {"name": m.name, "slug": "demo", "created_at": m.created_at, "speakers": 3, "bundle_version": 2,
              "tracks": {k: {"file": f"{k}.wav", "start_epoch": t.start_epoch, "sample_rate": 16000,
                             "samples": t.samples, "dropped_frames": 0, "silent": False}
                         for k, t in m.tracks.items()}}
    (src / "meta.json").write_text(json.dumps(crunch))
    before = sorted(p.name for p in src.iterdir())
    note = run.process_bundle(src, config, summarize=False)
    assert sorted(p.name for p in src.iterdir()) == before
    dest = config.recordings / m.name
    assert bundle.read_meta(dest).source_schema == "ata/1" and (dest / "note.md").is_file()
    assert note.read_text().count("tags: [meeting]") == 1   # CrunchLog = inglês


# ---- hooks reais (sem as partes D instaladas ou com módulo falso) ---------------------------------------------

def test_hook_missing_module_is_neutral(monkeypatch, tmp_path, config):
    monkeypatch.setattr(hooks, "SUMMARY", ("ata_nao_existe_xyz", "summarize_turns"))
    monkeypatch.setattr(hooks, "summarize", REAL["summarize"])
    assert hooks.summarize([], "pt-BR", config) is None
    monkeypatch.setattr(hooks, "VOICES", ("ata_nao_existe_voz", "auto_label"))
    assert REAL["auto_label"](tmp_path, config, {}, {}) == {}
    monkeypatch.setattr(hooks, "INDEX", ("ata_nao_existe_idx", "index_bundle"))
    monkeypatch.setattr(hooks, "CONNECT", ("ata_nao_existe_con", "after_note"))
    assert REAL["index"](tmp_path, config) is None and REAL["after_note"](tmp_path, tmp_path, config) is None


def test_hook_nested_import_error_propagates(monkeypatch, tmp_path, config):
    mod = tmp_path / "ata_hook_quebrado.py"
    mod.write_text("import pacote_que_nao_existe_abc\n")
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setattr(hooks, "INDEX", ("ata_hook_quebrado", "index_bundle"))
    with pytest.raises(ImportError):
        REAL["index"](tmp_path, config)


def test_hook_calls_module_function(monkeypatch, tmp_path, config):
    fake = types.ModuleType("ata_hook_ok")
    fake.summarize_turns = lambda turns, language, config, *, my_notes=None: {"tldr": language, "n": len(turns)}
    monkeypatch.setitem(sys.modules, "ata_hook_ok", fake)
    monkeypatch.setattr(hooks, "SUMMARY", ("ata_hook_ok", "summarize_turns"))
    assert REAL["summarize"]([], "es", config, None) == {"tldr": "es", "n": 0}


def test_failing_index_does_not_break_pipeline(meeting, config, monkeypatch):
    def boom(b, c):
        raise RuntimeError("falhou")
    monkeypatch.setattr(hooks, "index", boom)
    note = run.process_bundle(meeting, config, summarize=False)
    assert note.is_file() and "index ok=False" in (meeting / "pipeline.log").read_text()


def test_fallback_failure_message_has_no_meeting_text(meeting, config, monkeypatch):
    class Broken:
        name = "x"

        def transcribe(self, wav, language):
            raise EngineError("motor x falhou")

    monkeypatch.setattr(registry, "asr_for", lambda c, lang: Broken())
    monkeypatch.setattr(registry, "asr_fallback", lambda c: Broken())
    with pytest.raises(EngineError) as exc:
        run.process_bundle(meeting, config)
    assert "Bom dia" not in str(exc.value)


def test_unprocessed_rerender_errors(meeting, config):
    with pytest.raises(bundle.BundleError):
        run.rerender(meeting, config)


def test_words_fixture_untouched(meeting, config):
    """o pipeline não reescreve as fixtures nem o áudio do bundle ata/1 saudável."""
    before = (meeting / "far.wav").read_bytes()
    run.process_bundle(meeting, config, summarize=False)
    assert (meeting / "far.wav").read_bytes() == before
