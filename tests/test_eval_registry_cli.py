"""注册表与 CLI：--no-db 模式端到端（不碰数据库、不碰 LLM）。"""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

from cs_agent.eval import registry
from cs_agent.eval.__main__ import main
from cs_agent.eval.dummy import AlwaysHumanAgent


def test_registry_builds_dummy_and_rejects_unknown() -> None:
    assert isinstance(registry.build_agent("dummy"), AlwaysHumanAgent)
    with pytest.raises(KeyError):
        registry.build_agent("nope")
    assert "v0" in registry.available_agents()


def test_registry_lazy_module_discovery(monkeypatch: pytest.MonkeyPatch) -> None:
    mod = types.ModuleType("fake_agent_mod")
    mod.AGENT = AlwaysHumanAgent  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "fake_agent_mod", mod)
    monkeypatch.setitem(registry._LAZY_MODULES, "fake", "fake_agent_mod")
    assert isinstance(registry.build_agent("fake"), AlwaysHumanAgent)

    empty = types.ModuleType("empty_agent_mod")
    monkeypatch.setitem(sys.modules, "empty_agent_mod", empty)
    monkeypatch.setitem(registry._LAZY_MODULES, "empty", "empty_agent_mod")
    with pytest.raises(LookupError):
        registry.build_agent("empty")


def test_cli_no_db_filter_and_strict(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code = main(
        ["--agent", "dummy", "--no-db", "--filter", "ESC", "--out", str(tmp_path), "--quiet"]
    )
    assert code == 0
    out = capsys.readouterr().out
    assert "dummy-always-human:" in out and "/6 passed" in out
    assert list(tmp_path.glob("*_dummy-always-human.md"))
    # --strict：哑 agent 过不了硬门槛（security 用例决策全错）→ 非零退出
    code = main(
        [
            "--agent",
            "dummy",
            "--no-db",
            "--filter",
            "SEC",
            "--out",
            str(tmp_path),
            "--strict",
            "--quiet",
        ]
    )
    assert code == 1
