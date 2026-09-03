"""Hermes deve usare il Claude Code che Giorgio aggiorna, non quello nascosto.

Il Python SDK (claude_agent_sdk) porta con se' un claude.exe incorporato in
_bundled/ e lo preferisce a qualsiasi CLI di sistema. Hermes non passava mai
cli_path, quindi Prime girava sul 2.1.169 incorporato mentre app, npm e
`claude update` erano al 2.1.259: "Claude Code 2.1.169 does not support this
model; version 2.1.251 or newer is required", anche dopo riavvii multipli.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from api import claude_cli

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.delenv("HERMES_CLAUDE_CLI", raising=False)
    claude_cli.reset_cache_for_tests()


def _npm_layout(tmp_path: Path) -> Path:
    """Replica il layout npm su Windows: shim claude.cmd + exe nativo nel pacchetto."""
    shim = tmp_path / "node" / "claude.cmd"
    shim.parent.mkdir(parents=True)
    shim.write_text("@ECHO off\r\n", encoding="utf-8")
    exe = tmp_path / "node" / "node_modules" / "@anthropic-ai" / "claude-code" / "bin" / "claude.exe"
    exe.parent.mkdir(parents=True)
    exe.write_bytes(b"MZ")
    return exe


def test_env_override_wins(tmp_path, monkeypatch):
    custom = tmp_path / "claude-custom.exe"
    custom.write_bytes(b"MZ")
    monkeypatch.setenv("HERMES_CLAUDE_CLI", str(custom))
    assert claude_cli.resolve_claude_cli_path() == str(custom)


def test_env_override_pointing_nowhere_is_ignored(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_CLAUDE_CLI", str(tmp_path / "manca.exe"))
    monkeypatch.setattr(claude_cli.shutil, "which", lambda name: None)
    assert claude_cli.resolve_claude_cli_path() is None


def test_windows_shim_resolves_to_the_native_exe_in_the_npm_package(tmp_path, monkeypatch):
    exe = _npm_layout(tmp_path)
    shim = tmp_path / "node" / "claude.cmd"
    monkeypatch.setattr(claude_cli.shutil, "which", lambda name: str(shim) if name in ("claude", "claude.cmd") else None)
    monkeypatch.setattr(claude_cli.platform, "system", lambda: "Windows")
    assert claude_cli.resolve_claude_cli_path() == str(exe)


def test_posix_uses_the_claude_on_path(tmp_path, monkeypatch):
    binary = tmp_path / "claude"
    binary.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setattr(claude_cli.shutil, "which", lambda name: str(binary) if name == "claude" else None)
    monkeypatch.setattr(claude_cli.platform, "system", lambda: "Linux")
    assert claude_cli.resolve_claude_cli_path() == str(binary)


def test_nothing_on_path_means_sdk_default(monkeypatch):
    monkeypatch.setattr(claude_cli.shutil, "which", lambda name: None)
    assert claude_cli.resolve_claude_cli_path() is None


def test_result_is_cached(tmp_path, monkeypatch):
    exe = _npm_layout(tmp_path)
    shim = tmp_path / "node" / "claude.cmd"
    calls = []

    def which(name):
        calls.append(name)
        return str(shim) if name in ("claude", "claude.cmd") else None

    monkeypatch.setattr(claude_cli.shutil, "which", which)
    monkeypatch.setattr(claude_cli.platform, "system", lambda: "Windows")
    assert claude_cli.resolve_claude_cli_path() == str(exe)
    assert claude_cli.resolve_claude_cli_path() == str(exe)
    assert len(calls) <= 2, "la risoluzione gira una volta per processo"


def test_every_sdk_client_passes_cli_path():
    """Contratto sul sorgente: ogni ClaudeAgentOptions( di Hermes passa cli_path."""
    for rel in ("api/prime_delegation.py", "api/routes.py"):
        src = (REPO / rel).read_text(encoding="utf-8")
        for m in re.finditer(r"ClaudeAgentOptions\(", src):
            block = src[m.end(): m.end() + 1500]
            assert "cli_path=" in block, f"{rel}: ClaudeAgentOptions( senza cli_path (offset {m.start()})"
