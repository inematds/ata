from datetime import date, datetime, timedelta

from ata import bundle
from ata.engines import registry
from ata.knowledge import actions as A
from ata.knowledge import ask as K
from ata.knowledge import prep as P
from knowledge.helpers import make_meeting


def _ago(n):
    return datetime.now().astimezone().replace(hour=9, minute=30) - timedelta(days=n)


# ---- ask -------------------------------------------------------------------------------------------------

def test_ask_fake_returns_evidence_without_answer(config):
    make_meeting(config)
    res = K.ask(config, "quando decidimos lançar?")
    assert res["answer"] is None and res["evidence"]
    assert all(c["ref"].startswith("[lançamento beta, ") for c in res["citations"])
    assert len(res["citations"]) == len(res["evidence"])
    assert any("Decidimos lançar" in e["line"] for e in res["evidence"])


def test_ask_meeting_appends_ask_md(config):
    b = make_meeting(config)
    other = make_meeting(config, title="outra")
    res = K.ask(config, "tablets", meeting=str(b))
    assert {e["meeting"] for e in res["evidence"]} == {b.name}
    K.ask(config, "duzentas pessoas", meeting=b.name)
    md = (b / "ask.md").read_text(encoding="utf-8")
    assert md.count("## ") == 2 and "tablets" in md and "(sem síntese" in md
    assert "[lançamento beta, " in md
    assert not (other / "ask.md").exists()


class _Answerer:
    name = "stub"

    def __init__(self, out):
        self.out = out
        self.prompt = None

    def summarize(self, prompt, schema):
        self.prompt = prompt
        assert schema is K.ANSWER_SCHEMA
        return self.out


def test_ask_with_real_provider(config, monkeypatch):
    make_meeting(config)
    stub = _Answerer({"answer": "Lançar no dia doze [lançamento beta, 00:16]",
                      "citations": [{"meeting": "lançamento beta", "t": 16.0, "speaker": "Pessoa 2"}]})
    monkeypatch.setattr(registry, "forced_fake", lambda: False)
    monkeypatch.setattr(registry, "summarizer_for", lambda c: stub)
    res = K.ask(config, "quando vamos lançar?")
    assert res["answer"].startswith("Lançar no dia doze")
    assert res["citations"][0]["ref"] == "[lançamento beta, 00:16]"
    assert "[título, mm:ss]" in stub.prompt and "LANGUAGE: pt-BR" in stub.prompt
    assert "Decidimos lançar" in stub.prompt


def test_ask_invalid_answer_falls_back(config, monkeypatch):
    make_meeting(config)
    monkeypatch.setattr(registry, "forced_fake", lambda: False)
    monkeypatch.setattr(registry, "summarizer_for", lambda c: _Answerer({"resposta": "x"}))
    res = K.ask(config, "tablets")
    assert res["answer"] is None and res["citations"]


def test_ask_no_evidence(config, monkeypatch):
    # o embedder fake (hash em 64 dimensões) tem colisões; sem vetores, só a busca lexical decide
    monkeypatch.setattr(registry, "text_embedder_for", lambda c: (_ for _ in ()).throw(RuntimeError()))
    make_meeting(config)
    res = K.ask(config, "xilofone zebra")
    assert res == {"question": "xilofone zebra", "answer": None, "citations": [], "evidence": []}


# ---- actions ---------------------------------------------------------------------------------------------

def test_actions_list_and_owner(config):
    make_meeting(config)
    items = A.list_items(config)
    assert len(items) == 1 and items[0]["owner"] == "Pessoa 3" and items[0]["status"] == "open"
    assert A.list_items(config, owner="pessoa 3") and not A.list_items(config, owner="Ana")
    line = A.format_item(items[0])
    assert line.startswith("- [ ] Eu vou corrigir") and "[lançamento beta, 00:" in line and items[0]["id"] in line


def test_actions_done_and_prefix(config):
    make_meeting(config)
    it = A.list_items(config)[0]
    assert A.set_done(config, it["id"][:4]) == it["id"]
    assert A.list_items(config) == []
    assert A.list_items(config, status="done")[0]["status"] == "done"
    assert A.state_path(config).is_file()
    assert A.set_done(config, it["id"], done=False) == it["id"]
    assert A.list_items(config)
    assert A.set_done(config, "zzzz") is None


def test_actions_ids_stable_across_reprocess(config):
    b = make_meeting(config)
    first = A.list_items(config)[0]["id"]
    s = bundle.read_summary(b)
    bundle.write_json(b / "summary.json", s)
    assert A.list_items(config)[0]["id"] == first


def test_decisions_and_since(config):
    make_meeting(config, title="velha", started=_ago(60))
    make_meeting(config, title="nova", started=_ago(2))
    decisions = A.list_items(config, kind="decision")
    assert len(decisions) == 4 and decisions[0]["title"] == "nova"
    assert {d["title"] for d in A.list_items(config, kind="decision", since="30d")} == {"nova"}
    assert A.list_items(config, kind="question")[0]["text"].endswith("?")


def test_owner_uses_speaker_names(config):
    make_meeting(config, names={"Pessoa 3": "Bruno"})
    assert A.list_items(config, owner="bruno")[0]["owner"] == "Bruno"


# ---- prep ------------------------------------------------------------------------------------------------

def test_build_prep_writes_markdown(config):
    make_meeting(config)
    md = P.build_prep(config, "lançamento beta", calendar="Convidados: Ana, Bruno")
    path = P.prep_path(config, "lançamento beta")
    assert path.is_file() and path.read_text(encoding="utf-8") == md
    assert path.name == f"{date.today().isoformat()}-lancamento-beta-preparacao.md"
    for head in ("# Preparação: lançamento beta", "## Ações em aberto", "## Decidido antes",
                 "## Perguntas em aberto", "## Roteiro", "## Notas usadas", "> Agenda: Convidados"):
        assert head in md
    items = [ln for ln in md.splitlines() if "(q" in ln and ln.startswith("- [ ] (q")]
    assert 3 <= len(items) <= 5
    assert items[0].startswith("- [ ] (q1) **Retomar** — Cobramos")
    assert "tipo: preparacao" in md and "ata: prep" in md


def test_build_prep_dry_run_and_no_match(config):
    make_meeting(config)
    md = P.build_prep(config, "xilofone", dry_run=True)
    assert not P.prep_path(config, "xilofone").exists()
    assert "Nenhuma reunião dos últimos 90 dias" in md
    items = [ln for ln in md.splitlines() if ln.startswith("- [ ] (q")]
    assert len(items) == 3 and "objetivo" in items[0]


def test_build_prep_respects_days(config):
    make_meeting(config, title="lançamento beta", started=_ago(120))
    md = P.build_prep(config, "lançamento", days=90, dry_run=True)
    assert "Nenhuma reunião" in md
    md = P.build_prep(config, "lançamento", days=200, dry_run=True)
    assert "Nenhuma reunião" not in md


def test_build_prep_language_from_config(config):
    make_meeting(config)
    cfg = config.with_overrides(**{"language.default": "en"})
    md = P.build_prep(cfg, "lançamento", dry_run=True)
    assert "# Prep: lançamento" in md and "## Open action items" in md and "**Follow up**" in md


def test_build_prep_skips_done_actions(config):
    make_meeting(config)
    A.set_done(config, A.list_items(config)[0]["id"])
    md = P.build_prep(config, "lançamento", dry_run=True)
    assert "**Status**" not in md


def test_overlap():
    assert P.overlap("**Status** — Eu vou corrigir o erro dos tablets", "corrigido: erro dos tablets") >= 0.5
    assert P.overlap("Qual o orçamento?", "lançar no dia doze") == 0.0
    assert P.overlap("de a o", "qualquer") == 0.0


def test_tick_prep_marks_answered(config):
    make_meeting(config, title="kickoff", started=_ago(3))
    P.build_prep(config, "kickoff")
    path = P.prep_path(config, "kickoff")
    new = make_meeting(config, title="kickoff 2", summary={
        "language": "pt-BR", "tldr": "", "topics": [],
        "decisions": [{"text": "Plano premium será cobrado durante o beta", "evidence": [{"t": 42.0, "speaker": "Eu"}]}],
        "actions": [{"text": "Bruno vai corrigir o erro dos tablets", "owner": "Bruno", "due": None,
                     "evidence": [{"t": 50.0, "speaker": "Pessoa 2"}]}],
        "questions": []})
    assert P.tick_prep(config, new) >= 2
    text = path.read_text(encoding="utf-8")
    assert "- [x] (q1) **Retomar** — Cobramos o plano premium durante o beta? ✓ [kickoff 2, 00:42]" in text
    assert "- [x] (q2) **Status** — Eu vou corrigir o erro dos tablets" in text
    assert P.tick_prep(config, new) == 0          # já marcados
    items = P.prep_items(path)
    assert items[0] == {"q": "q1", "text": "Cobramos o plano premium durante o beta? ✓ [kickoff 2, 00:42]",
                        "done": True}


def test_tick_prep_ignores_old_or_unrelated(config):
    make_meeting(config, title="kickoff")
    P.build_prep(config, "kickoff")
    old = P.prep_path(config, "kickoff")
    stale = old.with_name("2020-01-01-kickoff-preparacao.md")
    stale.write_text(old.read_text(encoding="utf-8"), encoding="utf-8")
    unrelated = make_meeting(config, title="x", summary={
        "language": "pt-BR", "tldr": "", "topics": [], "questions": [],
        "decisions": [{"text": "Trocar a cor do logotipo", "evidence": []}], "actions": []})
    assert P.tick_prep(config, unrelated) == 0
    assert "[x]" not in old.read_text(encoding="utf-8")
    nosum = make_meeting(config, title="y", summary=False)
    assert P.tick_prep(config, nosum) == 0
