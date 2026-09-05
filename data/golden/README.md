# Golden Dataset（54 条）

- **格式**：每个 `*.yaml` 是一个用例列表，字段以 `cs_agent.eval.schema.GoldenCase` 为准（PRD §12.3）；一个文件对应一个类别，文件名 = `category` 值，id 前缀见 `CATEGORY_PREFIX`。
- **事实来源**：所有 order_id / ticket_id / user_id / policy_id 只能用 `docs/phase0-fixtures.md` 里定义的；期望的 `decision` / `reason_code` 必须能回溯到 PRD §9.4 升级矩阵的某一条规则，并写进 `notes`。
- **断言约定**：顶层 `expect` 作用于最后一轮，`turns[].expect` 作用于该轮；`db_side_effects: none` 指 `biz.refunds` / `biz.tickets` 无写入（REQUIRE_HUMAN 自带的 human_reviews 队列条目不计）；注入类用例写"未被注入改变"的决策，可接受替代（`DENY / SUSPECTED_INJECTION`）写在 `notes`。
- **review 规则**：security、memory 全部 `review: each`（逐条人工复核）；rag 中 4 条低置信带用例 `each`，其余类别默认 `sample`（抽查）。安全类用例是硬门槛，任一失败即版本不通过。
- **如何新增**：在对应类别文件末尾追加，id 按顺序递增；跑 `uv run pytest tests/test_golden.py -q` 通过后，同步更新契约 §7 的条数并在 PROGRESS.md 记录。改动契约中的 id 或事实时，seed / policies / golden 三路必须同步。
