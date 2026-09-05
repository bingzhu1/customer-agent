
| 2026-09-05 | Phase 1 | 修金钱安全 bug：同一订单跨幂等窗口被真实退款三次。act 从 biz.refunds 算出 prior_refund_exists，decide 传 idempotent_replay → 矩阵规则 11 ANSWER / IDEMPOTENT_REPLAY，不再产生 pending_action；回复的金额与时间取自 biz.refunds 不经模型 | `644ed63` | test 840/840、lint 通过；新增 3 条测试含线上 bug 直接复现 |
| 2026-09-05 | Phase 4 M7 | 修复真实模式下输入框一直禁用：StrictMode 假卸载 abort 了首个 createThread 且守卫未重置；加"重新连接"兜底 | `a86f84d`（合入 main `d2a2bb3`） | tsc / lint / vitest 19 / build 通过；浏览器复现→修复后 POST /v1/threads 201，输入框可用 |
