"""Disposable, controlled configuration tree; the CLI owns OS-keyring login."""

import os
import tempfile
from pathlib import Path

AGENT = """---
name: contract-text-only
description: Text-only protocol provider
tools: []
mainAgent: true
subagent: false
mcpServers: []
skills: []
plugins: []
commandExecutionPolicy: off
---
Respond with plain text only. Never invoke tools.
"""


class Profile:
    def __init__(self):
        self.temp = tempfile.TemporaryDirectory(prefix="antigravity-rpc-")
        self.root = Path(self.temp.name).resolve()
        self.workspace = self.root / "workspace"
        self.gemini = self.root / "gemini"
        self.gemini.mkdir()
        agent = self.workspace / ".agents" / "agents" / "contract-text-only.md"
        agent.parent.mkdir(parents=True)
        agent.write_text(AGENT, encoding="utf-8")
        # An explicit empty repository boundary prevents parent discovery.
        (self.workspace / ".git").mkdir()
        self.env = {
            key: value
            for key, value in os.environ.items()
            if key.upper()
            in {
                "SYSTEMROOT",
                "WINDIR",
                "PATH",
                "PATHEXT",
                "COMSPEC",
                "SYSTEMDRIVE",
                "NUMBER_OF_PROCESSORS",
                "LANG",
                "LC_ALL",
            }
        }
        for key in ("HOME", "USERPROFILE", "APPDATA", "LOCALAPPDATA", "TEMP", "TMP"):
            folder = self.root / key.lower()
            folder.mkdir()
            self.env[key] = str(folder)

    def command(self, executable):
        expected = {
            ".agents",
            ".agents/agents",
            ".agents/agents/contract-text-only.md",
            ".git",
        }
        actual = set()
        for item in self.workspace.rglob("*"):
            if item.is_symlink() or (
                hasattr(item, "is_junction") and item.is_junction()
            ):
                raise ValueError("Profile links are forbidden")
            actual.add(item.relative_to(self.workspace).as_posix())
        if actual != expected or list(self.gemini.iterdir()):
            raise ValueError("Unexpected profile contents")
        return [
            executable,
            "acp",
            "--agent",
            "contract-text-only",
            "--gemini-dir",
            str(self.gemini),
            "--app-data-dir",
            "contract-probe",
            "--include-default-customizations=false",
            "--disable-telemetry",
            "--log-file",
            os.devnull,
        ]

    def close(self):
        self.temp.cleanup()
