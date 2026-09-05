
| 2026-09-05 | Phase 1 | 修金钱安全 bug：同一订单跨幂等窗口被真实退款三次。act 从 biz.refunds 算出 prior_refund_exists，decide 传 idempotent_replay → 矩阵规则 11 ANSWER / IDEMPOTENT_REPLAY，不再产生 pending_action；回复的金额与时间取自 biz.refunds 不经模型 | `644ed63` | test 840/840、lint 通过；新增 3 条测试含线上 bug 直接复现 |
| 2026-09-05 | Phase 4 M7 | 修复真实模式下输入框一直禁用：StrictMode 假卸载 abort 了首个 createThread 且守卫未重置；加"重新连接"兜底 | `a86f84d`（合入 main `d2a2bb3`） | tsc / lint / vitest 19 / build 通过；浏览器复现→修复后 POST /v1/threads 201，输入框可用 |
| 2026-09-05 | Phase 5 | 修长期记忆"嘴上否认"与语言偏好失效：RESPOND_SYSTEM 去掉写死中文、告知模型有记忆可自述；search 固定注入 language_preference（不再被 top_k 挤掉） | `288c2e1` | test_memory_* 42/42、graph/llm 36/36、lint 通过；API 实测：问记忆→复述 4 条；"用英文回答"→英文；催单问句 3 次均英文；"请用中文"压过记忆 |
| 2026-09-05 | Phase 5 | 修软删记忆 key 永远学不回来：upsert 命中已删行且证据时间晚于 deleted_at 时复活 | `0b6c4dc` | test_memory_* 65/65、lint 通过；API 复现：清空→"以后用英文回答"→12 秒后同会话催单与新会话政策问答均为英文 |
| 2026-09-06 | Phase 5 | 模板分支跟随回复语言：22 组英文骨架 + domain/language 确定性判定（本轮要求 → 记忆 → 中文），respond 按 state.reply_language 选模板与 prompt 语言 | `d6d5ee1` | 全量 915/915、lint、mypy 通过；API 实测英文偏好下缺单号 / 已退 / 超额转人工 / 他人订单均英文，"请用中文"压回中文 |
