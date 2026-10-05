import json

from ata.knowledge import commands as KC
from ata.knowledge import prep as P
from knowledge.helpers import make_meeting, run_cmd


def test_search_command(config, capsys):
    make_meeting(config)
    assert run_cmd(KC, ["search", "tablets", "--mode", "lexical", "--limit", "3"], config) == 0
    out = capsys.readouterr().out.strip().splitlines()
    assert 1 <= len(out) <= 3 and out[0].startswith("[lançamento beta, 00:")
    assert run_cmd(KC, ["search", "xilofone", "--mode", "lexical"], config) == 3
    assert run_cmd(KC, ["search", "tablets", "--json", "--speaker", "Pessoa 3"], config) == 0
    rows = json.loads(capsys.readouterr().out)
    assert rows and {"meeting", "title", "date", "start", "speaker", "text", "score", "note_path"} <= set(rows[0])
    assert run_cmd(KC, ["search", "tablets", "--since", "ontem"], config) == 2


def test_ask_command(config, capsys):
    b = make_meeting(config)
    assert run_cmd(KC, ["ask", "o que decidimos?", "--meeting", "latest", "--json"], config) == 0
    res = json.loads(capsys.readouterr().out)
    assert res["answer"] is None and res["evidence"] and (b / "ask.md").is_file()
    assert run_cmd(KC, ["ask", "xilofone"], config) == 3


def test_prep_command(config, capsys):
    make_meeting(config)
    assert run_cmd(KC, ["prep", "lançamento", "--dry-run"], config) == 0
    assert "## Roteiro" in capsys.readouterr().out and not P.prep_path(config, "lançamento").exists()
    assert run_cmd(KC, ["prep", "lançamento", "--calendar", "Ana, Bruno"], config) == 0
    assert P.prep_path(config, "lançamento").is_file()
    assert run_cmd(KC, ["prep", "x", "--days", "0"], config) == 2


def test_actions_command(config, capsys):
    make_meeting(config)
    assert run_cmd(KC, ["actions", "--json"], config) == 0
    item = json.loads(capsys.readouterr().out)[0]
    assert run_cmd(KC, ["actions", "--done", item["id"]], config) == 0
    assert run_cmd(KC, ["actions"], config) == 3
    assert run_cmd(KC, ["actions", "--all"], config) == 0
    assert "[x]" in capsys.readouterr().out
    assert run_cmd(KC, ["actions", "--done", "ffffffff"], config) == 3
    assert run_cmd(KC, ["actions", "--decisions", "--owner", "Pessoa 2"], config) == 0
    assert "Decidimos lançar" in capsys.readouterr().out


def test_reindex_command(config, capsys):
    make_meeting(config)
    assert run_cmd(KC, ["reindex"], config) == 0
    assert "índice: 1 reuniões" in capsys.readouterr().out
