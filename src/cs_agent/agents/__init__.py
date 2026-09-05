"""被测 agent 的实现集合（V0…V6），每个版本一个模块。

各版本都实现 `cs_agent.eval.protocol.AgentUnderTest`，由 `cs_agent.eval.registry` 惰性加载，
这样 eval runner 不依赖任何具体实现，同一套 golden 用例才能贯穿 V0→V6（PRD §12.1 第 4 条）。
"""
