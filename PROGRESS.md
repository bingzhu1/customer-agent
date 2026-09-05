
| 2026-09-05 | Phase 1 | 修金钱安全 bug：同一订单跨幂等窗口被真实退款三次。act 从 biz.refunds 算出 prior_refund_exists，decide 传 idempotent_replay → 矩阵规则 11 ANSWER / IDEMPOTENT_REPLAY，不再产生 pending_action；回复的金额与时间取自 biz.refunds 不经模型 | `644ed63` | test 840/840、lint 通过；新增 3 条测试含线上 bug 直接复现 |
