import logging
import types

from ata import connect as C
from knowledge.helpers import make_meeting, run_cmd


def _brain(tmp_path):
    root = tmp_path / "cerebro"
    (root / "fontes").mkdir(parents=True)
    (root / "decisoes").mkdir()
    (root / "CLAUDE.md").write_text("# cérebro\n", encoding="utf-8")
    return root


def _fonte(root):
    files = sorted((root / "fontes").glob("*.md"))
    return files


def test_connect_cerebro_writes_fonte_and_registro(config, tmp_path, capsys):
    root = _brain(tmp_path)
    b = make_meeting(config, names={"Pessoa 2": "Ana Souza"})
    assert run_cmd(C, ["connect", "cerebro", "--dir", str(root)], config) == 0
    files = _fonte(root)
    assert len(files) == 1 and files[0].name == f"{b.name[:10]}-lancamento-beta.md"
    text = files[0].read_text(encoding="utf-8")
    for needle in ("tipo: fonte", "subtipo: reuniao", "idioma: pt-BR", 'participants: ["[[ana-souza]]"]',
                   f'fontes: ["{b.resolve()}"]', "atualizado: ", f'ata: "{b.name}"', "## Decisões", "## Ações",
                   "- [ ] Eu vou corrigir", "[Transcrição](file://"):
        assert needle in text, needle
    reg = (root / "decisoes" / "registro.md").read_text(encoding="utf-8")
    assert reg.startswith("# Registro de decisões")
    assert reg.count("**Decisão:**") == 2 and "**Responsável:** Ana Souza" in reg
    assert f"[[{files[0].stem}]]" in reg and f"<!-- ata:{b.name}:d1 -->" in reg
    assert "cerebro: conectado (1 reunião(ões)" in capsys.readouterr().out


def test_after_note_idempotent_and_updates_in_place(config, tmp_path):
    root = _brain(tmp_path)
    b = make_meeting(config)
    run_cmd(C, ["connect", "cerebro", "--dir", str(root)], config)
    note = config.notes / f"{b.name}.md"
    C.after_note(b, note, config)
    (b / "summary.json").write_text((b / "summary.json").read_text().replace("tablets", "celulares"))
    res = C.after_note(b, note, config)
    assert "cerebro" in res and not str(res["cerebro"]).startswith("erro")
    files = _fonte(root)
    assert len(files) == 1 and "celulares" in files[0].read_text(encoding="utf-8")
    reg = (root / "decisoes" / "registro.md").read_text(encoding="utf-8")
    assert reg.count("**Decisão:**") == 2           # sem duplicar


def test_user_edited_fonte_is_preserved(config, tmp_path):
    root = _brain(tmp_path)
    b = make_meeting(config)
    run_cmd(C, ["connect", "cerebro", "--dir", str(root)], config)
    f = _fonte(root)[0]
    f.write_text(f.read_text(encoding="utf-8") + "\nminha anotação\n", encoding="utf-8")
    (b / "summary.json").write_text((b / "summary.json").read_text().replace("tablets", "celulares"))
    C.after_note(b, None, config)
    files = _fonte(root)
    assert sorted(x.name for x in files) == sorted([f.name, f.stem + "-2.md"])
    assert "minha anotação" in f.read_text(encoding="utf-8")
    C.after_note(b, None, config)
    assert len(_fonte(root)) == 2                   # o -2 é do Ata: atualiza no lugar


def test_foreign_file_without_marker_gets_suffix(config, tmp_path):
    root = _brain(tmp_path)
    b = make_meeting(config)
    target = root / "fontes" / f"{b.name[:10]}-lancamento-beta.md"
    target.write_text("arquivo do usuário\n", encoding="utf-8")
    run_cmd(C, ["connect", "cerebro", "--dir", str(root)], config)
    assert target.read_text(encoding="utf-8") == "arquivo do usuário\n"
    assert (root / "fontes" / f"{target.stem}-2.md").is_file()


def test_safe_write_marker_without_record(tmp_path):
    p = tmp_path / "x.md"
    p.write_text('---\nata: "m1"\n---\nvelho\n', encoding="utf-8")
    path, action = C.safe_write(p, '---\nata: "m1"\n---\nnovo\n', "m1", {})
    assert path == p and action == "updated"
    path, action = C.safe_write(p, '---\nata: "m2"\n---\noutra\n', "m2", {})
    assert path.name == "x-2.md" and action == "alternate"


def test_with_marker():
    assert C.with_marker("---\ndate: x\n---\n# t\n", "m").startswith('---\ndate: x\nata: "m"\n---')
    assert C.with_marker("# sem frontmatter\n", "m").startswith('---\nata: "m"\n---\n\n# sem')
    twice = C.with_marker(C.with_marker("---\na: 1\n---\n", "m"), "m")
    assert twice.count("ata:") == 1


def test_obsidian_copy_updates_in_place(config, tmp_path):
    vault = tmp_path / "vault"
    vault.mkdir()
    b = make_meeting(config)
    assert run_cmd(C, ["connect", "obsidian", "--vault", str(vault)], config) == 0
    copy = vault / "Ata" / f"{b.name}.md"
    assert copy.is_file() and f'ata: "{b.name}"' in copy.read_text(encoding="utf-8")
    note = config.notes / f"{b.name}.md"
    note.write_text(note.read_text(encoding="utf-8").replace("Pessoa 2", "Ana"), encoding="utf-8")
    C.after_note(b, note, config)
    assert sorted(p.name for p in (vault / "Ata").iterdir()) == [copy.name]
    assert "Ana" in copy.read_text(encoding="utf-8")


def test_obsidian_missing_vault(config, tmp_path, capsys):
    assert run_cmd(C, ["connect", "obsidian", "--vault", str(tmp_path / "nada")], config) == 2


def test_agentic_os_prints_line_and_register(config, tmp_path, monkeypatch, capsys):
    vault = tmp_path / "Obsidian" / "Ata"
    b = make_meeting(config)
    calls = []
    monkeypatch.setattr(C, "_which", lambda name: "/usr/bin/claude")
    monkeypatch.setattr(C, "_run", lambda cmd, check=False: calls.append(cmd) or types.SimpleNamespace(returncode=0))
    assert run_cmd(C, ["connect", "agentic-os", "--vault", str(vault)], config) == 0
    out = capsys.readouterr().out
    assert C.MCP_LINE in out and calls == []
    assert (vault / f"{b.name}.md").is_file()
    assert run_cmd(C, ["connect", "agentic-os", "--vault", str(vault), "--register"], config) == 0
    assert calls == [C.MCP_ADD]
    assert C.load_connections(config)["agentic-os"]["registered"] is True
    assert run_cmd(C, ["connect", "agentic-os", "--undo", "--register"], config) == 0
    assert calls[-1] == C.MCP_REMOVE


def test_agentic_os_register_without_claude(config, tmp_path, monkeypatch):
    monkeypatch.setattr(C, "_which", lambda name: None)
    monkeypatch.setattr(C, "_run", lambda *a, **k: (_ for _ in ()).throw(AssertionError("não devia rodar")))
    assert run_cmd(C, ["connect", "agentic-os", "--vault", str(tmp_path / "v"), "--register"], config) == 4


def test_dry_run_writes_nothing(config, tmp_path, capsys):
    root = _brain(tmp_path)
    make_meeting(config)
    assert run_cmd(C, ["connect", "cerebro", "--dir", str(root), "--dry-run"], config) == 0
    assert _fonte(root) == [] and not (root / "decisoes" / "registro.md").exists()
    assert C.load_connections(config) == {}
    assert "(simulação) created:" in capsys.readouterr().out


def test_undo_removes_only_unedited(config, tmp_path, capsys):
    root = _brain(tmp_path)
    vault = tmp_path / "vault"
    vault.mkdir()
    make_meeting(config, title="um")
    make_meeting(config, title="dois")
    (root / "decisoes" / "registro.md").write_text("# Registro de decisões\n\n## 2026-01-01: minha\n\n"
                                                    "**Decisão:** do usuário\n", encoding="utf-8")
    run_cmd(C, ["connect", "cerebro", "--dir", str(root)], config)
    run_cmd(C, ["connect", "obsidian", "--vault", str(vault)], config)
    edited = _fonte(root)[0]
    edited.write_text("editado\n", encoding="utf-8")
    assert run_cmd(C, ["connect", "cerebro", "--undo"], config) == 0
    out = capsys.readouterr().out
    assert "mantido (editado por você)" in out and "removido:" in out
    assert _fonte(root) == [edited]
    reg = (root / "decisoes" / "registro.md").read_text(encoding="utf-8")
    assert "do usuário" in reg and "ata:" not in reg and "**Decisão:**" in reg
    assert "cerebro" not in C.load_connections(config) and "obsidian" in C.load_connections(config)


def test_status(config, tmp_path, capsys):
    assert run_cmd(C, ["connect"], config) == 3
    root = _brain(tmp_path)
    run_cmd(C, ["connect", "cerebro", "--dir", str(root)], config)
    capsys.readouterr()
    assert run_cmd(C, ["connect", "cerebro", "--status"], config) == 0
    assert f"cerebro: ativa -> {root}" in capsys.readouterr().out
    assert run_cmd(C, ["connect", "obsidian", "--status"], config) == 3


def test_connect_cerebro_requires_dir(config, tmp_path):
    assert run_cmd(C, ["connect", "cerebro"], config) == 2
    assert run_cmd(C, ["connect", "cerebro", "--dir", str(tmp_path / "nao")], config) == 2
    cfg = config.with_overrides(**{"cerebro.dir": str(_brain(tmp_path))})
    assert run_cmd(C, ["connect", "cerebro"], cfg) == 0


def test_after_note_never_raises(config, tmp_path, caplog):
    b = make_meeting(config)
    C.save_connections(config, {"cerebro": {"dir": str(tmp_path / "sumiu"), "enabled": True},
                                "obsidian": {"vault": str(tmp_path / "tb-sumiu"), "enabled": True}})
    with caplog.at_level(logging.WARNING):
        res = C.after_note(b, config.notes / f"{b.name}.md", config)
    assert res["cerebro"].startswith("erro: ") and res["obsidian"].startswith("erro: ")
    assert res["prep"] == 0
    assert "conexão cerebro falhou: FileNotFoundError" in caplog.text
    assert "tablets" not in caplog.text
    (config.cache / "connections.json").write_text("{lixo", encoding="utf-8")
    assert C.after_note(tmp_path / "nem-bundle", None, config)["prep"] == 0


def test_after_note_ticks_prep(config, tmp_path):
    from ata.knowledge import prep as P
    make_meeting(config, title="kickoff")
    P.build_prep(config, "kickoff")
    b = make_meeting(config, title="kickoff 2", summary={
        "language": "pt-BR", "tldr": "", "topics": [], "questions": [], "actions": [],
        "decisions": [{"text": "O plano premium será cobrado durante o beta", "evidence": []}]})
    assert C.after_note(b, None, config)["prep"] >= 1
