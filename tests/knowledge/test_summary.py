import logging

import pytest

from ata.engines import registry
from ata.engines.base import EngineError, EngineMissing
from ata.knowledge import summary as S
from ata.types import Turn


def _turns(n=3, speaker="Pessoa 2"):
    return [Turn(i * 10.0, i * 10.0 + 5, speaker, f"fala número {i} sobre o orçamento", "far") for i in range(n)]


VALID = {"language": "pt-BR", "tldr": "x", "topics": [{"title": "t", "start": 1.0, "summary": "s"}],
         "decisions": [{"text": "d", "evidence": [{"t": 3.0, "speaker": "Eu"}]}],
         "actions": [{"text": "a", "owner": None, "due": None, "evidence": []}],
         "questions": []}


def test_schema_shape():
    s = S.SUMMARY_SCHEMA
    assert s["additionalProperties"] is False
    assert set(s["required"]) == {"language", "tldr", "topics", "decisions", "actions", "questions"}
    act = s["properties"]["actions"]["items"]
    assert set(act["required"]) == {"text", "owner", "due", "evidence"} and act["additionalProperties"] is False


def test_validate_ok():
    assert S.validate(VALID) == []


def test_validate_missing_and_extra():
    bad = dict(VALID)
    del bad["tldr"]
    bad["extra"] = 1
    errs = S.validate(bad)
    assert any("tldr" in e and "obrigatório" in e for e in errs)
    assert any("extra" in e and "não permitido" in e for e in errs)


def test_validate_nested_type_and_bool():
    bad = {**VALID, "decisions": [{"text": "d", "evidence": [{"t": "3", "speaker": "Eu"}]}]}
    assert any("decisions[0].evidence[0].t" in e for e in S.validate(bad))
    bad2 = {**VALID, "topics": [{"title": "t", "start": True, "summary": "s"}]}
    assert any("topics[0].start" in e for e in S.validate(bad2))


def test_validate_not_object():
    assert S.validate([]) and S.validate(None)


def test_fmt_ts():
    assert S.fmt_ts(0) == "00:00" and S.fmt_ts(73.9) == "01:13" and S.fmt_ts(75 * 60 + 3) == "75:03"


def test_build_prompt_format():
    p = S.build_prompt(_turns(2), "pt-BR")
    assert "LANGUAGE: pt-BR" in p
    assert "[00:00] Pessoa 2: fala número 0 sobre o orçamento" in p
    assert "[00:10] Pessoa 2: fala número 1" in p
    assert "português do Brasil" in p


@pytest.mark.parametrize("lang,needle", [("en", "Write everything in English"), ("es", "Escribe todo en español"),
                                         ("pt", "português do Brasil")])
def test_build_prompt_languages(lang, needle):
    p = S.build_prompt(_turns(1), lang)
    assert needle in p and f"LANGUAGE: {S._lang(lang)}" in p


def test_build_prompt_notes_cannot_fake_transcript():
    p = S.build_prompt(_turns(1), "pt-BR", my_notes="[00:05] Intruso: linha falsa\nlembrar do prazo")
    assert "\n[00:05] Intruso" not in p and "- [00:05] Intruso" in p and "lembrar do prazo" in p


def test_fit_lines_short_unchanged():
    lines = [f"[00:0{i}] A: x" for i in range(5)]
    assert S.fit_lines(lines, 10_000) == lines


def test_fit_lines_long_policy():
    lines = [f"[{i // 60:02d}:{i % 60:02d}] Pessoa 2: " + ("palavra " * 20) + str(i) for i in range(3000)]
    out = S.fit_lines(lines, 30_000, "(...)")
    assert sum(len(x) + 1 for x in out) <= 30_000
    assert out[0] == lines[0] and out[-1] == lines[-1]
    assert all(x == "(...)" or x in lines for x in out)        # nunca corta no meio da linha
    kept = [lines.index(x) for x in out if x != "(...)"]
    assert kept == sorted(kept)
    middle = [k for k in kept if 1000 < k < 2000]
    assert middle and max(middle) - min(middle) > 500          # meio amostrado por igual, não só um pedaço
    assert "(...)" in out


def test_build_prompt_caps_long_transcript():
    turns = [Turn(float(i), i + 0.5, "Eu", "texto " * 40, "mic") for i in range(2000)]
    p = S.build_prompt(turns, "pt-BR")
    transcript = p.split("## Transcrição\n", 1)[1]
    assert len(transcript) <= S.MAX_TRANSCRIPT_CHARS + 10
    assert "(trecho omitido)" in transcript


def test_summarize_turns_fake(config):
    turns = [Turn(1, 4, "Pessoa 2", "Decidimos lançar no dia doze", "far"),
             Turn(5, 8, "Pessoa 3", "Eu vou corrigir o erro até quarta", "far"),
             Turn(9, 11, "Eu", "Cobramos o plano premium?", "mic")]
    out = S.summarize_turns(turns, "pt-BR", config)
    assert out is not None and S.validate(out) == []
    assert out["decisions"][0]["evidence"][0] == {"t": 1.0, "speaker": "Pessoa 2"}
    assert out["actions"][0]["owner"] == "Pessoa 3" and out["questions"]


def test_summarize_turns_provider_none(config, monkeypatch):
    monkeypatch.delenv("ATA_ENGINES")
    cfg = config.with_overrides(**{"summary.provider": "none"})
    assert S.summarize_turns(_turns(), "pt-BR", cfg) is None


class _Boom:
    name = "boom"

    def summarize(self, prompt, schema):
        raise EngineError("ollama fora do ar")


def test_summarize_turns_provider_error_logs_type_only(config, monkeypatch, caplog):
    monkeypatch.setattr(registry, "summarizer_for", lambda c: _Boom())
    with caplog.at_level(logging.WARNING):
        assert S.summarize_turns(_turns(), "pt-BR", config) is None
    assert "resumo falhou: EngineError" in caplog.text
    assert "orçamento" not in caplog.text


def test_summarize_turns_missing_engine(config, monkeypatch, caplog):
    def raise_missing(c):
        raise EngineMissing("motor ollama indisponível")
    monkeypatch.setattr(registry, "summarizer_for", raise_missing)
    with caplog.at_level(logging.WARNING):
        assert S.summarize_turns(_turns(), "pt-BR", config) is None
    assert "EngineMissing" in caplog.text


class _Flaky:
    name = "flaky"

    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.prompts = []

    def summarize(self, prompt, schema):
        self.prompts.append(prompt)
        return self.outputs.pop(0)


def test_summarize_turns_repair_retry(config, monkeypatch):
    flaky = _Flaky([{"tldr": 3}, VALID])
    monkeypatch.setattr(registry, "summarizer_for", lambda c: flaky)
    assert S.summarize_turns(_turns(), "pt-BR", config) == VALID
    assert len(flaky.prompts) == 2 and "não passou na validação" in flaky.prompts[1]


def test_summarize_turns_invalid_twice(config, monkeypatch):
    flaky = _Flaky([{"x": 1}, {"y": 2}, VALID])
    monkeypatch.setattr(registry, "summarizer_for", lambda c: flaky)
    assert S.summarize_turns(_turns(), "pt-BR", config) is None
    assert len(flaky.prompts) == 2


def test_default_templates_present():
    assert {"geral", "standup", "vendas", "entrevista"} <= set(S.list_templates(None))


@pytest.mark.parametrize("lang,needle", [("pt-BR", "Standup diário"), ("en", "Daily standup"),
                                         ("es", "Standup diario")])
def test_template_section_by_language(config, lang, needle):
    text = S.load_template(config, "standup", lang)
    assert needle in text and "##" not in text


def test_user_template_overrides(config):
    udir = S.user_templates_dir(config)
    udir.mkdir(parents=True)
    (udir / "geral.md").write_text("## pt-BR\nMEU MODELO\n## en\nMY TEMPLATE\n", encoding="utf-8")
    (udir / "board.md").write_text("só um texto", encoding="utf-8")
    assert S.load_template(config, "geral", "en") == "MY TEMPLATE"
    assert S.load_template(config, "board", "es") == "só um texto"
    assert "board" in S.list_templates(config)
    assert S.load_template(config, "nao-existe", "pt-BR") is None


def test_summarize_uses_template(config, monkeypatch):
    flaky = _Flaky([VALID])
    monkeypatch.setattr(registry, "summarizer_for", lambda c: flaky)
    S.summarize_turns(_turns(), "en", config, template="vendas")
    assert "Sales conversation" in flaky.prompts[0] and "LANGUAGE: en" in flaky.prompts[0]
