import json
from pathlib import Path

import pytest

from ata import bundle
from ata.bundle import BundleError
from ata.compat.crunchlog import meta_from_crunchlog

DEMO = Path("/home/nmaldaner/CrunchLog/recordings/2026-10-05-1642-demo")


def crunch_meta(**over):
    d = {
        "name": "2026-10-05-1642-demo", "slug": "demo", "host": "h", "platform": "linux",
        "recorder": {"name": "crunchlog-demo", "version": "2.0.0a1"},
        "created_at": "2026-10-05T16:42:24-03:00", "stopped_at": "2026-10-05T16:45:02-03:00", "speakers": 3,
        "tracks": {k: {"file": f"{k}.wav", "device": "d", "start_epoch": 1791229344.5, "sample_rate": 16000,
                       "samples": 32000, "dropped_frames": 0, "silent": False} for k in ("far", "mic")},
        "damaged": False, "damage_reasons": [], "bundle_version": 2,
    }
    d.update(over)
    return d


def test_basic_mapping():
    m = meta_from_crunchlog(crunch_meta())
    assert m.source_schema == "crunchlog/2" and m.language == "en" and m.speakers_hint == 3
    assert set(m.tracks) == {"far", "mic"} and m.tracks["far"].start_measured
    assert bundle.damage_report(m) == []


def test_dropped_frames_become_damage():
    d = crunch_meta()
    d["tracks"]["mic"]["dropped_frames"] = 12
    assert "dropped_frames" in meta_from_crunchlog(d).damage_reasons


@pytest.mark.parametrize("path,value,where", [
    (("tracks", "far", "sample_rate"), "16000", "meta.tracks.far.sample_rate"),
    (("tracks", "mic", "start_epoch"), "x", "meta.tracks.mic.start_epoch"),
    (("tracks", "far", "silent"), 0, "meta.tracks.far.silent"),
    (("name",), 5, "meta.name"),
    (("speakers",), True, "meta.speakers"),
])
def test_strict_types(path, value, where):
    d = crunch_meta()
    node = d
    for k in path[:-1]:
        node = node[k]
    node[path[-1]] = value
    with pytest.raises(BundleError, match=where.replace(".", r"\.")):
        meta_from_crunchlog(d)


def test_wrong_version_rejected():
    with pytest.raises(BundleError, match="bundle_version"):
        meta_from_crunchlog(crunch_meta(bundle_version=3))


def test_read_meta_dispatches_to_compat(tmp_path):
    (tmp_path / "meta.json").write_text(json.dumps(crunch_meta()))
    assert bundle.read_meta(tmp_path).source_schema == "crunchlog/2"


@pytest.mark.skipif(not (DEMO / "meta.json").is_file(), reason="demo do CrunchLog ausente")
def test_real_demo_meta_reads():
    m = meta_from_crunchlog(json.loads((DEMO / "meta.json").read_text()))
    assert m.name == "2026-10-05-1642-demo" and len(m.tracks) == 2
