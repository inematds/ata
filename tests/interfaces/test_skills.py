import json
import re
from pathlib import Path

import pytest

from ata import mcp_server

ROOT = Path(__file__).resolve().parents[2]
SKILLS = ("meeting-notes", "action-items", "meeting-prep", "ask-meetings", "meeting-to-wiki")


def frontmatter(text):
    m = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
    assert m, "sem frontmatter"
    out = {}
    for line in m.group(1).splitlines():
        k, _, v = line.partition(":")
        out[k.strip()] = v.strip()
    return out, text[m.end():]


@pytest.mark.parametrize("name", SKILLS)
def test_skill_frontmatter_and_steps(name):
    text = (ROOT / "skills" / name / "SKILL.md").read_text(encoding="utf-8")
    fm, body = frontmatter(text)
    assert fm["name"] == name
    assert 40 <= len(fm["description"]) <= 1024
    assert re.search(r"^1\. ", body, re.MULTILINE), "sem passos numerados"
    tools = set(re.findall(r"\bata_[a-z_]+", body))
    assert tools, "skill sem ferramenta ata_*"
    known = set(mcp_server.TOOL_NAMES) | {"ata_action_done"}
    assert tools <= known, tools - known


def test_plugin_and_mcp_json():
    plugin = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    assert plugin["name"] == "ata" and plugin["version"] == "0.1.0" and plugin["description"]
    mcp = json.loads((ROOT / ".mcp.json").read_text(encoding="utf-8"))
    assert mcp == {"mcpServers": {"ata": {"command": "ata", "args": ["mcp"]}}}
