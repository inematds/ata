from ata import bundle
from ata.note import fmt_ts, render_note
from ata.types import Turn

SUMMARY_FIXTURE = {
    "language": "pt-BR", "tldr": "Pessoa 2 trouxe os números.",
    "topics": [{"title": "Lançamento", "start": 5.0, "summary": "Teste fechado"}],
    "decisions": [{"text": "Lançar no dia doze", "evidence": [{"t": 5.0, "speaker": "Pessoa 2"}]}],
    "actions": [{"text": "Corrigir tablets", "owner": "Pessoa 3", "due": "quarta", "evidence": []}],
    "questions": [{"text": "Premium no beta?", "evidence": [{"t": 1.0, "speaker": "Eu"}]}],
}


def meta(**over):
    base = dict(name="2026-10-05-1642-demo", created_at="2026-10-05T16:42:00-03:00", title="Demo",
                tracks={"far": bundle.Track("far.wav", start_epoch=1.0, start_measured=True, samples=16000 * 90),
                        "mic": bundle.Track("mic.wav", start_epoch=1.0, start_measured=True, samples=16000 * 90)},
                engines={"asr": "fake"})
    base.update(over)
    return bundle.BundleMeta(**base)


TURNS = [Turn(1.0, 4.0, "Eu", "Bom dia", "mic"), Turn(5.0, 9.0, "Pessoa 2", "Decidimos lançar", "far"),
         Turn(3700.0, 3702.0, "Pessoa 3", "Até quarta", "far")]


def test_fmt_ts():
    assert fmt_ts(65) == "01:05" and fmt_ts(3725) == "1:02:05"


def test_frontmatter_and_sections_pt():
    text = render_note(meta(), TURNS, SUMMARY_FIXTURE, {"Pessoa 2": "Ana"}, "levar café", [], "pt-BR",
                       bundle_path="/x/y")
    head = text.split("---")[1]
    assert 'date: 2026-10-05' in head and 'start: "16:42"' in head and 'language: "pt-BR"' in head
    assert 'bundle: "/x/y"' in head and "tags: [reuniao]" in head and '  - "Ana"' in head
    assert "speakers: 3" in head and 'asr: "fake"' in head
    for h in ("# Reunião Demo", "## Resumo", "## Tópicos", "## Decisões", "## Ações", "## Perguntas em aberto",
              "## Participantes", "## Minhas anotações", "## Transcrição"):
        assert h in text
    assert "## Problemas da gravação" not in text
    assert "[00:05] Ana: Decidimos lançar" in text and "[1:01:40] Pessoa 3: Até quarta" in text
    assert "responsável: Pessoa 3" in text and "prazo: quarta" in text
    assert "Ana (Pessoa 2)" in text


def test_sections_en_es_and_tags():
    en = render_note(meta(title=None), TURNS, None, {}, None, [], "en")
    assert "# Meeting 2026-10-05 16:42" in en and "## Transcript" in en and "tags: [meeting]" in en
    assert "(no summary)" in en and "[00:01] Me" not in en  # turnos guardam o rótulo do bundle
    es = render_note(meta(), TURNS, None, {}, None, [], "es")
    assert "# Reunión Demo" in es and "## Transcripción" in es and "tags: [reunion]" in es


def test_damage_section():
    text = render_note(meta(), TURNS, None, {}, None, ["mic_missing", "start_unmeasured"], "pt-BR")
    assert "## Problemas da gravação" in text and "`mic_missing`" in text
    assert "Esta gravação tem problemas" in text


def test_note_resolves_back_to_bundle(meeting, config, tmp_path):
    note = tmp_path / "n.md"
    note.write_text(render_note(bundle.read_meta(meeting), TURNS, None, {}, None, [], "pt-BR",
                                bundle_path=meeting), encoding="utf-8")
    assert bundle.resolve(note, config.recordings) == meeting
