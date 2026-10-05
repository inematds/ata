import logging
import sqlite3
from datetime import date, datetime, timedelta

import pytest

from ata import bundle
from ata.engines import registry
from ata.knowledge import index as I
from ata.types import Turn
from knowledge.helpers import LINES_EN, make_meeting


def _days_ago(n):
    return datetime.now().astimezone().replace(hour=10, minute=0) - timedelta(days=n)


def test_chunk_turns_windows():
    turns = [Turn(i * 10.0, i * 10.0 + 8, "Eu" if i % 2 else "Pessoa 2", f"frase {i}", "far") for i in range(30)]
    chunks = I.chunk_turns(turns)
    assert len(chunks) >= 3
    for c in chunks[:-1]:
        assert 60 <= c.end - c.start <= 90
    assert sum(len(c.lines) for c in chunks) == 30
    assert set(chunks[0].speakers) == {"Eu", "Pessoa 2"}
    assert "Pessoa 2: frase 0" in chunks[0].text


def test_chunk_single_long_turn_and_empty():
    chunks = I.chunk_turns([Turn(0, 200, "Eu", "muito longo", "mic"), Turn(201, 202, "Eu", "  ", "mic")])
    assert len(chunks) == 1 and chunks[0].end == 200
    assert I.chunk_turns([]) == []


def test_index_bundle_tables(config):
    b = make_meeting(config)
    n = I.index_bundle(b, config)
    con = sqlite3.connect(I.db_path(config))
    kinds = {r[0] for r in con.execute("SELECT kind FROM chunks")}
    assert n > 0 and {"transcript", "decision", "action", "question", "tldr"} <= kinds
    assert con.execute("SELECT COUNT(*) FROM vectors").fetchone()[0] == n
    row = con.execute("SELECT title, date, language, note_path FROM meetings").fetchone()
    assert row[0] == "lançamento beta" and row[2] == "pt-BR" and row[3].endswith(f"notas/{b.name}.md")
    assert row[1] == b.name[:10]


def test_index_bundle_idempotent(config):
    b = make_meeting(config)
    n1 = I.index_bundle(b, config)
    n2 = I.index_bundle(b, config)
    con = sqlite3.connect(I.db_path(config))
    assert n1 == n2 == con.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    assert con.execute("SELECT COUNT(*) FROM chunks_fts").fetchone()[0] == n1


def test_lexical_without_accents(config):
    b = make_meeting(config)
    I.index_bundle(b, config)
    hits = I.search(config, "lancamento", mode="lexical")
    assert hits and all(h.meeting == b.name for h in hits)
    hits = I.search(config, "tablets", mode="lexical", filters={"kind": "transcript"})
    assert hits[0].speaker == "Pessoa 3" and "tablets" in hits[0].text


def test_hit_format(config):
    b = make_meeting(config)
    I.index_bundle(b, config)
    h = I.search(config, "duzentas pessoas", mode="lexical", filters={"kind": "transcript"})[0]
    line = h.format()
    assert line.startswith("[lançamento beta, 00:") and "Pessoa 2: Decidimos lançar" in line
    assert h.to_json()["line"] == line and h.to_json()["ts"] == h.ts


def test_semantic_search(config):
    b = make_meeting(config)
    I.index_bundle(b, config)
    hits = I.search(config, "erro tablets quarta", mode="semantic")
    assert hits and "tablets" in hits[0].text


def test_hybrid_rrf():
    scores = I.rrf([[1, 2, 3], [3, 1]])
    assert scores[1] == pytest.approx(1 / 61 + 1 / 62)
    assert scores[3] == pytest.approx(1 / 63 + 1 / 61)
    assert max(scores, key=scores.get) == 1


def test_query_with_fts_syntax_chars(config):
    I.index_bundle(make_meeting(config), config)
    for q in ['o que decidimos?', 'pré-lançamento "beta"', "NOT (AND)", "a:b*", "???", ""]:
        I.search(config, q)        # não levanta


def test_fts_query_quotes_tokens():
    assert I.fts_query('pré-lançamento "x"') == '"pre"* OR "lancamento"* OR "x"' or \
        I.fts_query('pré-lançamento "x"') == '"pre" OR "lancamento"* OR "x"'
    assert I.fts_query("?!") is None


def test_filter_speaker_and_language(config):
    pt = make_meeting(config)
    en = make_meeting(config, title="launch sync", language="en", lines=LINES_EN)
    I.reindex(config)
    hits = I.search(config, "tablets tablet", filters={"speaker": "Pessoa 3", "kind": "transcript"})
    assert hits and all(h.meeting == pt.name and h.speaker == "Pessoa 3" for h in hits)
    hits = I.search(config, "tablet launch", filters={"language": "en"})
    assert hits and all(h.meeting == en.name for h in hits)


def test_filter_dates_and_meeting(config):
    old = make_meeting(config, title="antiga", started=_days_ago(40))
    new = make_meeting(config, title="recente", started=_days_ago(1))
    I.reindex(config)
    since = (date.today() - timedelta(days=7)).isoformat()
    assert {h.meeting for h in I.search(config, "tablets", filters={"since": since})} == {new.name}
    assert {h.meeting for h in I.search(config, "tablets", filters={"until": since})} == {old.name}
    assert {h.meeting for h in I.search(config, "tablets", filters={"since": "7d"})} == {new.name}
    assert {h.meeting for h in I.search(config, "tablets", filters={"meeting": old.name})} == {old.name}
    assert {h.meeting for h in I.search(config, "tablets", filters={"meeting": "antiga"})} == {old.name}


def test_summary_items_searchable(config):
    I.index_bundle(make_meeting(config), config)
    hits = I.search(config, "duzentas", filters={"kind": "decision"})
    assert hits and hits[0].kind == "decision" and hits[0].start == 16.0


def test_speaker_names_applied(config):
    b = make_meeting(config, names={"Pessoa 3": "Bruno"})
    I.index_bundle(b, config)
    hits = I.search(config, "tablets", filters={"speaker": "Bruno", "kind": "transcript"})
    assert hits and hits[0].speaker == "Bruno"
    assert I.meetings(config)[0]["participants"] == sorted(["Bruno", "Eu", "Pessoa 2"])


def test_reindex_rebuilds(config):
    a = make_meeting(config, title="um")
    make_meeting(config, title="dois")
    (config.recordings / "lixo").mkdir(parents=True)
    res = I.reindex(config)
    assert res["meetings"] == 2 and res["chunks"] > 0 and res["failed"] == 0
    import shutil
    shutil.rmtree(a)
    assert I.reindex(config)["meetings"] == 1
    assert {m["title"] for m in I.meetings(config)} == {"dois"}


def test_unprocessed_bundle_skipped(config):
    from ata.testing import DEFAULT_LINES_PT, synth_meeting
    synth_meeting(config.recordings, DEFAULT_LINES_PT, title="crua")
    assert I.reindex(config)["meetings"] == 0


def test_embedder_failure_falls_back_to_lexical(config, monkeypatch, caplog):
    def boom(c):
        raise RuntimeError("sem ollama")
    monkeypatch.setattr(registry, "text_embedder_for", boom)
    b = make_meeting(config)
    with caplog.at_level(logging.WARNING):
        I.index_bundle(b, config)
        hits = I.search(config, "tablets")
    assert hits and "embeddings indisponíveis: RuntimeError" in caplog.text
    assert "tablets" not in caplog.text
    assert I.search(config, "tablets", mode="semantic") == []


def test_vectors_other_dim_ignored(config):
    b = make_meeting(config)
    I.index_bundle(b, config)
    con = sqlite3.connect(I.db_path(config))
    con.execute("UPDATE vectors SET dim = 1024 WHERE chunk_id = (SELECT MIN(chunk_id) FROM vectors)")
    con.commit()
    con.close()
    assert I.search(config, "tablets", mode="semantic")       # não quebra no np.dot


def test_search_auto_indexes(config):
    make_meeting(config)
    assert I.search(config, "tablets")          # índice vazio -> reconstrói sozinho


def test_parse_date():
    assert I.parse_date("2026-01-02") == "2026-01-02"
    assert I.parse_date("7d") == (date.today() - timedelta(days=7)).isoformat()
    assert I.parse_date("2w") == (date.today() - timedelta(days=14)).isoformat()
    assert I.parse_date(None) is None
    with pytest.raises(ValueError):
        I.parse_date("ontem")


def test_invalid_mode(config):
    with pytest.raises(ValueError):
        I.search(config, "x", mode="fuzzy")


def test_resolve_meeting_id(config):
    b = make_meeting(config)
    assert I.resolve_meeting_id(config, str(b)) == b.name
    assert I.resolve_meeting_id(config, "latest") == b.name
    assert I.resolve_meeting_id(config, "qualquer") == "qualquer"
    assert bundle.resolve(b.name, config.recordings) == b
