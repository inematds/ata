import pytest

from ata.pipeline.cleanup import clean_text, collapse_repeats, load_glossary


@pytest.mark.parametrize("raw,want", [
    ("isso é bom", "isso é bom"),
    ("é, então vamos", "então vamos"),
    ("hum, vamos lá né?", "vamos lá?"),
    ("é é é certo", "certo"),
    ("é...", ""),
    ("tipo assim a gente vai", "a gente vai"),
    ("ahn o prazo é quarta", "o prazo é quarta"),
])
def test_pt_fillers(raw, want):
    assert clean_text(raw, "pt-BR") == want


def test_en_fillers_multiword():
    assert clean_text("um, I think uh you know it works", "en") == "I think it works"


def test_es_ambiguous_kept_as_word():
    assert clean_text("este proyecto es bueno", "es") == "este proyecto es bueno"
    assert clean_text("este, o sea, mmm vamos", "es") == "vamos"


def test_extra_fillers():
    assert clean_text("basicamente vamos lançar", "pt-BR", extra_fillers=["basicamente"]) == "vamos lançar"


def test_collapse_repeats():
    assert collapse_repeats("a a a reunião") == "a reunião"
    assert collapse_repeats("eu acho eu acho que sim") == "eu acho que sim"
    assert collapse_repeats("muito bem, muito bem") == "muito bem,"
    assert collapse_repeats("sim não sim") == "sim não sim"


def test_glossary_file(tmp_path):
    p = tmp_path / "g.txt"
    p.write_text("# comentário\ncrunch log => CrunchLog\npara keet => Parakeet\nlinha ruim\n", encoding="utf-8")
    g = load_glossary(p)
    assert g == [("crunch log", "CrunchLog"), ("para keet", "Parakeet")]
    assert clean_text("o Crunch Log usa para keet", "pt-BR", g) == "o CrunchLog usa Parakeet"
    assert load_glossary(tmp_path / "nada.txt") == []


def test_glossary_whole_words_only():
    assert clean_text("ata e atalho", "pt-BR", [("ata", "Ata")]) == "Ata e atalho"
