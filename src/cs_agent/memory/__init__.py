"""Phase 5 记忆层（PRD §10，ADR-0009）。

五层记忆里，本包负责 ②2a（`CaseFacts`）、③（`case_state`）、④（`user_memory`）三层。
① 业务库与 ⑤ 知识库不在这里：前者归 Repository，后者归 `cs_agent.rag`。

**本包永远不参与授权与策略判断（红线 3 / 不变式 1）。**
结构上的保证：本包不 import `cs_agent.policy` 与 `cs_agent.decision`，
`PolicyFacts` / `DecisionInput` 里也没有任何记忆类字段——投毒攻击写不出来。
由 `tests/test_memory_poisoning.py` 逐条守住。
"""
