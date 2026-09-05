# policies/ —— 策略 YAML

- 本目录的 `*.yaml` 是策略的**唯一事实来源**：RAG 语料由它生成（PRD §11），策略引擎由它求值（PRD §9.2）。不要在别处再维护一份策略文案。
- 每个文件是一个规则列表（顶层 list），每条规则的字段以 `src/cs_agent/policy/schema.py` 的 `PolicyRule` 为准；id / 版本 / 要点以 `docs/phase0-fixtures.md` §5 为准，改动需三路同步。
- **策略变更递增 `version` 并更新 `effective_date`，不允许原地修改**：策略引擎的判定与回答引用都会回带 `policy_version`，原地改会让历史判定不可复现。
- 加规则：在对应 domain 的文件末尾追加一条；`decisional` 规则（allow_refund / deny_refund）必须填 `applies_to` 与 pass/fail reason code，`informational` 规则不填 conditions 与金额；`anchor` 全局唯一，格式 `domain#slug`。
- `human_text` 面向客户，须用自然语言把条件（天数、状态、金额上限、审批）完整写出；每条至少 2 个 `faq`，答案只依据本规则。
- 政策未覆盖的主题（价格保护、发票开具、账户注销、海外直邮关税）**不得**写入本目录，golden 用它们测 `RETRIEVAL_NO_RESULT`。
- 校验：`uv run pytest tests/test_policies.py -q`。
