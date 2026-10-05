"""ata bench com ATA_ENGINES=fake e conjuntos minúsculos gerados no teste."""

from __future__ import annotations

import json

import numpy as np
import pytest

from ata import audio, bench
from ata.engines.fake import write_fixture
from ata.types import Span, Word


def test_normalize_text_languages():
    assert bench.normalize_text("Olá, MUNDO!  Ação—já.", "pt-BR") == ["olá", "mundo", "ação", "já"]
    assert bench.normalize_text("¿Qué tal? Año", "es") == ["qué", "tal", "año"]
    assert bench.normalize_text("Don't  stop, café!", "en") == ["dont", "stop", "cafe"]


def test_edit_distance_and_wer():
    assert bench.edit_distance([], []) == 0
    assert bench.edit_distance(["a", "b", "c"], ["a", "x", "c", "d"]) == 2
    assert bench.edit_distance(["a"], []) == 1
    assert bench.wer("Bom dia, a todos.", "bom dia todos", "pt-BR") == (1, 4)


def test_der_perfect_with_relabel_and_errors():
    ref = [Span(0, 2, "ana"), Span(2, 4, "bia")]
    assert bench.der(ref, [Span(0, 2, "S1"), Span(2, 4, "S0")]) == (0.0, pytest.approx(4.0))
    err, tot = bench.der(ref, [Span(0, 4, "S0")])                  # tudo num falante: 2 s de confusão
    assert err == pytest.approx(2.0) and tot == pytest.approx(4.0)
    err, _ = bench.der(ref, [])                                     # falta tudo
    assert err == pytest.approx(4.0)
    err, _ = bench.der([Span(0, 1, "a")], [Span(0, 1, "x"), Span(1, 2, "y")])   # 1 s de sobra
    assert err == pytest.approx(1.0)


def test_best_mapping_greedy_branch():
    refs = [f"r{i}" for i in range(9)]
    hyps = [f"h{i}" for i in range(9)]
    overlap = {(f"h{i}", f"r{(i + 1) % 9}"): 10 for i in range(9)}
    m = bench.best_mapping(overlap, refs, hyps)
    assert m["h0"] == "r1" and m["h8"] == "r0"


def test_read_rttm(tmp_path):
    p = tmp_path / "a.rttm"
    p.write_text("SPEAKER a 1 0.50 1.25 <NA> <NA> ana <NA> <NA>\n;; comentário\n")
    assert bench.read_rttm(p) == [Span(0.5, 1.75, "ana")]


@pytest.fixture
def dataset(tmp_path):
    d = tmp_path / "set"
    d.mkdir()
    for i, (ref, hyp) in enumerate([("bom dia a todos", "bom dia todos"), ("vamos lançar", "vamos lançar")]):
        wav = d / f"c{i}.wav"
        audio.write_wav(wav, np.zeros(16000 * 2, np.float32))
        write_fixture(wav, words=[Word(t, k * 0.3, k * 0.3 + 0.2) for k, t in enumerate(hyp.split())],
                      spans=[Span(0, 1, "S0"), Span(1, 2, "S1")])
        (d / f"c{i}.txt").write_text(ref + "\n", encoding="utf-8")
        (d / f"c{i}.rttm").write_text(f"SPEAKER c{i} 1 0 1 <NA> <NA> ana <NA> <NA>\n"
                                      f"SPEAKER c{i} 1 1 1 <NA> <NA> bia <NA> <NA>\n")
    return d


def test_bench_asr_fake(run, config, dataset, tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    rc = run("ata.bench", ["bench", "asr", "--set", str(dataset), "--engines", "nemo:parakeet,onnx:x",
                           "--lang", "pt-BR"], config)
    assert rc == 0
    out = capsys.readouterr().out
    assert "| engine | WER | RTF | n |" in out and "| nemo:parakeet | 16.67% |" in out
    files = list((tmp_path / "bench" / "results").glob("*.json"))
    assert len(files) == 1
    doc = json.loads(files[0].read_text())
    res = doc["runs"][0]["results"]
    assert [r["engine"] for r in res] == ["nemo:parakeet", "onnx:x"]
    assert res[0]["edits"] == 1 and res[0]["words"] == 6 and res[0]["n"] == 2
    assert doc["runs"][0]["forced_fake"] is True
    assert "bom dia" not in files[0].read_text()          # só métricas, nada de texto


def test_bench_diar_fake_appends(run, config, dataset, tmp_path, capsys):
    out_dir = tmp_path / "res"
    for _ in range(2):
        assert run("ata.bench", ["bench", "diar", "--set", str(dataset), "--engines", "nemo",
                                 "--out", str(out_dir)], config) == 0
    assert "| nemo | 0.00% |" in capsys.readouterr().out
    doc = json.loads(next(out_dir.glob("*.json")).read_text())
    assert len(doc["runs"]) == 2 and doc["runs"][0]["kind"] == "diar"


def test_bench_summary_fake(run, config, tmp_path, capsys):
    d = tmp_path / "sum"
    d.mkdir()
    (d / "r1.txt").write_text("[00:01] Eu: Decidimos lançar no dia doze\n[00:05] Pessoa 2: Eu vou corrigir\n")
    assert run("ata.bench", ["bench", "summary", "--set", str(d), "--engines", "ollama:qwen",
                             "--out", str(tmp_path / "o")], config) == 0
    assert "| ollama:qwen | 100% |" in capsys.readouterr().out


def test_bench_errors(run, config, tmp_path, capsys):
    assert run("ata.bench", ["bench", "asr", "--set", str(tmp_path / "nao"), "--engines", "x"], config) == 2
    empty = tmp_path / "vazio"
    empty.mkdir()
    from ata.engines.base import EngineError
    with pytest.raises(EngineError):
        run("ata.bench", ["bench", "asr", "--set", str(empty), "--engines", "nemo", "--out",
                          str(tmp_path / "o")], config)
