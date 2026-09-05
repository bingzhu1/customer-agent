"""被测 agent 注册表：CLI 的 `--agent` 名字 → 实例。

V0…V6 的实现文件由各 Phase 加入；这里用惰性导入，缺失的实现不会影响其他 agent 运行。
约定：模块内暴露一个 `AgentUnderTest` 子类（若多个则需提供 `AGENT` 变量指向要用的类）。
"""

from __future__ import annotations

import importlib
import inspect
from collections.abc import Callable

from cs_agent.eval.dummy import AlwaysHumanAgent
from cs_agent.eval.protocol import AgentUnderTest

_LAZY_MODULES: dict[str, str] = {
    "v0": "cs_agent.agents.v0_naive",
    "v1": "cs_agent.agents.v1_tools",
    "v3": "cs_agent.agents.v3_policy",
}


def _load_from_module(module_path: str) -> AgentUnderTest:
    module = importlib.import_module(module_path)
    explicit = getattr(module, "AGENT", None)
    if explicit is not None:
        instance = explicit() if inspect.isclass(explicit) else explicit
        if not isinstance(instance, AgentUnderTest):
            raise TypeError(f"{module_path}.AGENT is not an AgentUnderTest")
        return instance
    candidates = [
        obj
        for _, obj in inspect.getmembers(module, inspect.isclass)
        if issubclass(obj, AgentUnderTest)
        and obj is not AgentUnderTest
        and obj.__module__ == module.__name__
    ]
    if len(candidates) != 1:
        raise LookupError(
            f"{module_path}: expected exactly one AgentUnderTest subclass, found "
            f"{[c.__name__ for c in candidates]}; set module-level AGENT to disambiguate"
        )
    return candidates[0]()


_FACTORIES: dict[str, Callable[[], AgentUnderTest]] = {
    "dummy": AlwaysHumanAgent,
}


def available_agents() -> list[str]:
    return sorted(set(_FACTORIES) | set(_LAZY_MODULES))


def build_agent(name: str) -> AgentUnderTest:
    if name in _FACTORIES:
        return _FACTORIES[name]()
    if name in _LAZY_MODULES:
        return _load_from_module(_LAZY_MODULES[name])
    raise KeyError(f"unknown agent {name!r}; available: {available_agents()}")
