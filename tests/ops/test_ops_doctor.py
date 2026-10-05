"""ata doctor e ata privacy com sondas injetadas (nada de nvidia-smi, Ollama ou CLIs reais)."""

from __future__ import annotations

import json

import pytest

from ata import bundle, doctor
from ata.engines.base import EngineError


def probes(**kw):
    tools = kw.pop("tools", {"pw-record", "wpctl", "ffmpeg", "nvidia-smi"})
    base = dict(which=lambda n: f"/usr/bin/{n}" if n in tools else None,
                run=lambda *a, **k: (0, "NVIDIA GB10, 580.95.05, 119 GiB\n"),
                platform="linux", machine="aarch64", python=(3, 12, 4),
                disk_free=lambda p: 100 * 1024 ** 3, importable=lambda n: False,
                engine_status=lambda c: {"running": True, "healthy": True, "host": "127.0.0.1", "port": 47520},
                ollama_models=lambda c: ["qwen3.6:35b-a3b", "qwen3-embedding:0.6b"])
    base.update(kw)
    return doctor.Probes(**base)


@pytest.fixture
def use(monkeypatch):
    def set_(p):
        monkeypatch.setattr(doctor, "PROBES", p)
        return p
    return set_


def test_doctor_all_good(run, config, use, capsys):
    use(probes())
    assert run("ata.doctor", ["doctor", "--skip-benchmark"], config) == 0
    out = capsys.readouterr().out.splitlines()
    assert any(line.startswith("[ok  ] python: 3.12.4") for line in out)
    assert any(line.startswith("[ok  ] captura: PipeWire") for line in out)
    assert any(line.startswith("[ok  ] motor nemo") for line in out)
    assert any(line.startswith("[warn] motor onnx") and "ata[onnx]" in line for line in out)
    assert any(line.startswith("[ok  ] gpu: NVIDIA GB10") for line in out)
    assert any(line.startswith("[skip] benchmark") for line in out)
    assert out[-1] == "gravação: sim · processamento: sim (fake) · resumo: sim (ollama)"
    assert (config.cache / "doctor.json").is_file()
    for line in out[:-1]:
        assert line[:6] in ("[ok  ]", "[warn]", "[fail]", "[skip]")


def test_doctor_missing_pipewire_fails(run, config, use, capsys):
    use(probes(tools={"ffmpeg"}))
    assert run("ata.doctor", ["doctor", "--skip-benchmark"], config) == 1
    out = capsys.readouterr().out
    assert "[fail] captura: PipeWire sem pw-record, wpctl" in out and "gravação: não" in out


def test_doctor_no_engine_without_fake(run, config, use, capsys, monkeypatch):
    monkeypatch.delenv("ATA_ENGINES")
    use(probes(engine_status=lambda c: {"running": False, "healthy": False}))
    assert run("ata.doctor", ["doctor", "--skip-benchmark"], config) == 1
    out = capsys.readouterr().out
    assert "[fail] processamento" in out and "processamento: não" in out
    assert "[warn] motor nemo: parado" in out


def test_doctor_ollama_down_and_model_missing(config, use):
    use(probes(ollama_models=lambda c: (_ for _ in ()).throw(EngineError("x"))))
    rep = doctor.run_checks(config, skip_benchmark=True)
    ol = [c for c in rep["checks"] if c["name"] == "ollama"][0]
    assert ol["status"] == "warn" and "ollama serve" in ol["detail"]
    use(probes(ollama_models=lambda c: ["llama3.1:8b"]))
    rep = doctor.run_checks(config, skip_benchmark=True)
    ol = [c for c in rep["checks"] if c["name"] == "ollama"][0]
    assert "ollama pull qwen3.6:35b-a3b" in ol["detail"]


def test_doctor_claude_provider_missing_binary_fails(config, use):
    use(probes())
    cfg = config.with_overrides(summary__provider="claude", privacy__level=1)
    rep = doctor.run_checks(cfg, skip_benchmark=True)
    cl = [c for c in rep["checks"] if c["name"] == "claude"][0]
    assert cl["status"] == "fail" and not rep["ok"]
    use(probes(tools={"pw-record", "wpctl", "claude"}))
    rep = doctor.run_checks(cfg, skip_benchmark=True)
    cl = [c for c in rep["checks"] if c["name"] == "claude"][0]
    assert cl["status"] == "ok" and "privacy.level = 1" in cl["detail"]


def test_doctor_json_and_disk_low(run, config, use, capsys):
    use(probes(disk_free=lambda p: 100 * 1024 ** 2))
    assert run("ata.doctor", ["doctor", "--json", "--skip-benchmark"], config) == 1
    rep = json.loads(capsys.readouterr().out)
    disk = [c for c in rep["checks"] if c["name"] == "disco"][0]
    assert disk["status"] == "fail" and rep["final"].startswith("gravação:")


def test_doctor_benchmark_runs_with_fake_ref(config, use, monkeypatch):
    monkeypatch.delenv("ATA_ENGINES")
    use(probes())
    cfg = config.with_overrides(**{"asr__pt-BR": "fake:x"})
    rep = doctor.run_checks(cfg)
    b = [c for c in rep["checks"] if c["name"] == "benchmark"][0]
    assert b["status"] == "ok" and "RTF" in b["detail"]
    assert rep["benchmark"]["audio_s"] == 20.0


def test_doctor_benchmark_skipped_when_fake(config, use):
    use(probes())
    rep = doctor.run_checks(config)
    assert [c for c in rep["checks"] if c["name"] == "benchmark"][0]["detail"] == "ATA_ENGINES=fake"


def test_doctor_windows_and_macos(config, use):
    use(probes(platform="win32", importable=lambda n: n == "pyaudiowpatch"))
    assert doctor.run_checks(config, skip_benchmark=True)["recording"] is True
    use(probes(platform="darwin", machine="arm64"))
    rep = doctor.run_checks(config, skip_benchmark=True)
    assert [c for c in rep["checks"] if c["name"] == "captura"][0]["status"] == "warn"


def test_privacy_default_config(run, config, capsys):
    assert run("ata.doctor", ["privacy"], config) == 0
    out = capsys.readouterr().out
    assert "0 — cofre" in out and "sai da máquina: nada" in out


def test_privacy_claude_config(run, config, capsys):
    cfg = config.with_overrides(summary__provider="claude", privacy__level=1)
    assert run("ata.doctor", ["privacy"], cfg) == 0
    out = capsys.readouterr().out
    assert "1 — assinatura" in out and "Anthropic via claude CLI" in out


def test_privacy_bundle(run, config, meeting, capsys):
    meta = bundle.read_meta(meeting)
    bundle.write_meta(meeting, meta.with_(engines={"asr.far": "nemo:parakeet-tdt-0.6b-v3",
                                                   "summary": "codex:gpt-6-luna"}))
    assert run("ata.doctor", ["privacy", str(meeting)], config) == 0
    out = capsys.readouterr().out
    assert "asr.far: nemo:parakeet-tdt-0.6b-v3" in out and "OpenAI via codex CLI" in out
    assert run("ata.doctor", ["privacy", "nao-existe"], config) == 1


def test_privacy_unprocessed_bundle(run, config, meeting, capsys):
    assert run("ata.doctor", ["privacy", "latest"], config) == 0
    out = capsys.readouterr().out
    assert "ainda não processada" in out and "saiu da máquina: nada" in out
