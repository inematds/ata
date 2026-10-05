"""ata engine: detecção de backend, plano de instalação, download com sha256 (file://), start/stop/status
com runtime injetado. Nenhum processo real nem rede."""

from __future__ import annotations

import hashlib
import json
import signal
import tarfile

import pytest

from ata import engine_cmd as ec
from ata.data import load_manifest
from ata.engines.base import EngineError, EngineMissing


class FakeProc:
    def __init__(self, pid=4242, code=None):
        self.pid = pid
        self.code = code

    def poll(self):
        return self.code

    def wait(self):
        return 0


class FakeRt(ec.Runtime):
    pass


@pytest.fixture
def rt(monkeypatch):
    alive: set[int] = set()
    calls: dict = {"launched": [], "killed": []}
    healthy = {"ok": False}

    def kill(pid, sig):
        calls["killed"].append((pid, sig))
        if sig == 0:
            if pid not in alive:
                raise ProcessLookupError
            return
        alive.discard(pid)

    def launcher(cmd, log, fg):
        calls["launched"].append(cmd)
        alive.add(4242)
        healthy["ok"] = True
        return FakeProc()

    r = ec.Runtime(which=lambda n: {"systemd-run": "/usr/bin/systemd-run"}.get(n), run=lambda *a, **k: (1, ""),
                   launcher=launcher, kill=kill, platform="linux", machine="aarch64",
                   health=lambda c: dict(healthy), sleep=lambda s: None)
    monkeypatch.setattr(ec, "RUNTIME", r)
    r.alive, r.calls, r.healthy = alive, calls, healthy  # type: ignore[attr-defined]
    return r


@pytest.mark.parametrize("plat,mach,gpu,want", [
    ("linux", "aarch64", "NVIDIA GB10, 580.95.05", "cuda13"),
    ("linux", "x86_64", "NVIDIA GeForce RTX 4090, 590.10", "cuda13"),
    ("linux", "x86_64", "NVIDIA GeForce RTX 3060, 550.54.14", "cuda12"),
    ("linux", "x86_64", None, "cpu"),
    ("darwin", "arm64", None, "metal"),
    ("darwin", "x86_64", None, "cpu"),
    ("win32", "AMD64", None, "cpu"),
])
def test_detect_backend(plat, mach, gpu, want):
    assert ec.detect_backend(plat, mach, gpu) == want


def test_manifest_has_required_entries():
    m = load_manifest()
    names = {a["name"] for a in m["archive"]}
    assert {"linux-aarch64-cuda13", "linux-x86_64-cpu", "macos-aarch64-metal", "windows-x86_64-cpu"} <= names
    models = {x["name"] for x in m["model"]}
    assert {"parakeet-tdt-0.6b-v3", "nemotron-3.5-asr-streaming-0.6b", "nemotron-3-diarization",
            "silero-vad"} <= models
    for x in m["archive"] + m["model"]:
        assert {"name", "url", "sha256", "size_mb", "license"} <= set(x)
        if not x["sha256"]:
            assert x["verify"] == "on-first-download"
    for x in m["model"]:
        assert isinstance(x["languages"], list) and x["role"]
    assert "anthropic" not in json.dumps(m).lower() and "openai.com" not in json.dumps(m).lower()


def test_archive_url_template():
    m = load_manifest()
    a, b = ec.select_archive(m, "linux", "aarch64", "cuda13")
    assert b == "cuda13"
    assert ec.archive_url(m, a) == ("https://github.com/NVIDIA/NeMo-Speech.cpp/releases/latest/download/"
                                    "nemo-speech-linux-aarch64-cuda13.tar.gz")
    w, _ = ec.select_archive(m, "win32", "AMD64", "cpu")
    assert ec.archive_url(m, w).endswith("windows-x86_64-cpu.zip")
    v, eff = ec.select_archive(m, "linux", "x86_64", "vulkan")      # sem build vulkan -> cpu
    assert eff == "cpu" and v["name"] == "linux-x86_64-cpu"


def test_install_dry_run(run, config, rt, capsys):
    rt.run = lambda *a, **k: (0, "NVIDIA GB10, 580.95.05\n")
    rt.which = lambda n: "/usr/bin/" + n
    assert run("ata.engine_cmd", ["engine", "install", "--dry-run"], config) == 0
    out = capsys.readouterr().out
    assert "backend: cuda13" in out and "linux-aarch64-cuda13" in out
    assert "parakeet-tdt-0.6b-v3" in out and "[sha256 ausente]" in out and "nada baixado" in out
    assert not (config.path("paths.models") / "downloads").exists()


def test_install_refuses_unverified(run, config, rt, capsys):
    assert run("ata.engine_cmd", ["engine", "install", "--backend", "cuda13"], config) == 1
    assert "--allow-unverified" in capsys.readouterr().err


def test_install_onnx_dry_run(run, config, rt, capsys):
    assert run("ata.engine_cmd", ["engine", "install", "--onnx", "--dry-run"], config) == 0
    out = capsys.readouterr().out
    assert "pyannote-segmentation-3.0" in out and "wespeaker" in out and "backend" not in out


def _local_manifest(tmp_path, monkeypatch, sha_ok=True):
    """Manifesto local com file:// e sha256 conhecido (archive tar.gz com binário fake + modelo)."""
    src = tmp_path / "src"
    (src / "bin").mkdir(parents=True)
    exe = src / "bin" / "nemo-speech"
    exe.write_text("#!/bin/sh\n")
    exe.chmod(0o755)
    tgz = tmp_path / "eng.tar.gz"
    with tarfile.open(tgz, "w:gz") as t:
        t.add(src / "bin", arcname="bin")
    model = tmp_path / "parakeet.gguf"
    model.write_bytes(b"pesos-falsos" * 100)
    sha_a = hashlib.sha256(tgz.read_bytes()).hexdigest()
    sha_m = hashlib.sha256(model.read_bytes()).hexdigest() if sha_ok else "0" * 64
    toml = f'''
[release]
binary = "nemo-speech"
url_template = "file:///nao-usado/{{archive}}"
serve_args = ["serve", "--port", "{{port}}"]
[[archive]]
name = "linux-aarch64-cuda13"
os = "linux"
arch = "aarch64"
backend = "cuda13"
url = "{tgz.as_uri()}"
sha256 = "{sha_a}"
size_mb = 1
license = "Apache-2.0"
[[model]]
name = "parakeet-tdt-0.6b-v3"
role = "asr"
file = "parakeet.gguf"
url = "{model.as_uri()}"
sha256 = "{sha_m}"
size_mb = 1
license = "CC-BY-4.0"
languages = ["pt-BR"]
'''
    mp = tmp_path / "models.toml"
    mp.write_text(toml, encoding="utf-8")
    monkeypatch.setenv("ATA_MODELS_MANIFEST", str(mp))
    return mp


def test_install_from_file_url_with_sha(run, config, rt, tmp_path, monkeypatch):
    _local_manifest(tmp_path, monkeypatch)
    rt.urlopen = __import__("urllib.request").request.urlopen
    assert run("ata.engine_cmd", ["engine", "install", "--backend", "cuda13"], config) == 0
    mdir = config.path("paths.models")
    assert (mdir / "parakeet.gguf").read_bytes().startswith(b"pesos-falsos")
    assert not list(mdir.rglob("*.part"))
    found = ec.find_binary(config, load_manifest(), rt)
    assert found and found.endswith("nemo-speech-linux-aarch64-cuda13/bin/nemo-speech".replace(
        "nemo-speech-linux", "nemo-speech/linux"))
    assert json.loads((mdir / "nemo-speech" / "installed.json").read_text())["backend"] == "cuda13"


def test_install_sha_mismatch_leaves_nothing(run, config, rt, tmp_path, monkeypatch):
    _local_manifest(tmp_path, monkeypatch, sha_ok=False)
    rt.urlopen = __import__("urllib.request").request.urlopen
    with pytest.raises(EngineError) as exc:
        run("ata.engine_cmd", ["engine", "install", "--backend", "cuda13"], config)
    assert "sha256 não confere" in str(exc.value)
    mdir = config.path("paths.models")
    assert not (mdir / "parakeet.gguf").exists() and not list(mdir.rglob("*.part"))


def test_download_overwrites_stale_part(tmp_path):
    src = tmp_path / "a.bin"
    src.write_bytes(b"conteudo")
    dest = tmp_path / "out" / "a.bin"
    dest.parent.mkdir()
    (dest.parent / "a.bin.part").write_bytes(b"lixo-velho")      # file:// ignora Range -> reescreve
    sha = hashlib.sha256(b"conteudo").hexdigest()
    rt = ec.Runtime(urlopen=__import__("urllib.request").request.urlopen)
    assert ec.download(src.as_uri(), dest, sha, rt=rt) == sha
    assert dest.read_bytes() == b"conteudo"
    with pytest.raises(EngineError):
        ec.download(src.as_uri(), tmp_path / "b.bin", "", rt=rt)


def test_start_status_stop(run, config, rt, tmp_path, monkeypatch, capsys):
    exe = tmp_path / "nemo-speech"
    exe.write_text("#!/bin/sh\n")
    exe.chmod(0o755)
    cfg = config.with_overrides(engine__binary=str(exe), engine__memory_max="8G")
    assert run("ata.engine_cmd", ["engine", "start"], cfg) == 0
    cmd = rt.calls["launched"][0]
    assert cmd[:4] == ["systemd-run", "--user", "--scope", "--quiet"]
    assert "MemoryMax=8G" in cmd and cmd[cmd.index("--") + 1] == str(exe)
    assert "serve" in cmd and str(cfg.get("engine.port")) in cmd
    state = json.loads((cfg.cache / "engine.json").read_text())
    assert state["pid"] == 4242 and state["unit"].startswith("ata-engine-")
    assert run("ata.engine_cmd", ["engine", "start"], cfg) == 3           # já rodando
    capsys.readouterr()
    assert run("ata.engine_cmd", ["engine", "status", "--json"], cfg) == 0
    st = json.loads(capsys.readouterr().out)
    assert st["running"] and st["healthy"] and st["pid"] == 4242
    assert run("ata.engine_cmd", ["engine", "stop"], cfg) == 0
    assert (4242, signal.SIGTERM) in rt.calls["killed"]
    assert all(pid == 4242 for pid, _ in rt.calls["killed"])               # só o PID dele
    assert not (cfg.cache / "engine.json").exists()
    assert run("ata.engine_cmd", ["engine", "stop"], cfg) == 3


def test_start_without_systemd_and_missing_binary(run, config, rt, tmp_path, monkeypatch):
    rt.which = lambda n: None
    with pytest.raises(EngineMissing) as exc:
        run("ata.engine_cmd", ["engine", "start"], config)
    assert "ata engine install" in str(exc.value)
    exe = tmp_path / "nemo-speech"
    exe.write_text("")
    cmd, unit = ec.serve_command(config.with_overrides(engine__binary=str(exe)), str(exe),
                                 load_manifest(), rt)
    assert unit is None and cmd[0] == str(exe)
    rt.platform = "darwin"
    rt.which = lambda n: "/x/" + n
    assert ec.serve_command(config, str(exe), load_manifest(), rt)[1] is None


def test_start_process_dies(run, config, rt, tmp_path):
    exe = tmp_path / "nemo-speech"
    exe.write_text("")
    rt.launcher = lambda cmd, log, fg: FakeProc(code=1)
    with pytest.raises(EngineError) as exc:
        run("ata.engine_cmd", ["engine", "start"], config.with_overrides(engine__binary=str(exe)))
    assert "saiu ao iniciar" in str(exc.value)
    assert not (config.cache / "engine.json").exists()


def test_stale_state(run, config, rt, capsys):
    ec.write_state(config, {"pid": 999999, "port": 1})
    st = ec.engine_status(config, rt)
    assert not st["running"] and st["stale_state"]
    assert run("ata.engine_cmd", ["engine", "stop"], config) == 3
    assert not (config.cache / "engine.json").exists()
