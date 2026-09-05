
| 2026-09-05 | Phase 1 | persist 节点接 P2 的 `memory.jobs.ExtractionQueue`：投递即返回，抽取与写库在后台线程（不变式 4 长期记忆异步写入、FR-704 不在热路径）；API 用异步队列并在 lifespan 回收，eval / 单测用 InlineExtractionQueue 换确定性 | `e9a8d79` | test 816/816、lint 通过；新增用例断言 persist 不等抽取（慢抽取器 1s，本轮 < 0.5s） |
