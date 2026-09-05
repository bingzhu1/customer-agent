
| 2026-09-05 | Phase 1 | 第二轮 ④：三个只读工具 get_refunds / get_payments / get_profile（签名无身份字段，get_profile 无参数；越权与不存在同样返回空，避免存在性泄露）+ understand 增加 refund_status / payment_status / membership_question 三个意图；另加起服务预热（WARMUP_ON_STARTUP，失败只记日志），测试环境钉死为关 | `ac6a89f` | test 828/828、lint 通过 |
| 2026-09-05 | 冲刺 | 余额恢复并广播；合入 Phase 1 ④ 三工具 + 预热、Phase 2 标定防呆、Phase 3 确认闭环 `POST /v1/actions/{id}/confirm`；8000 重启，预热 6.6 s | `5007469` | test 833/833 |
| 2026-09-05 | 冲刺 | 前端确认闭环合入；发现演示服务真实时钟与 seed 锚点（2026-09-01）漂移导致 82914 被正确 DENY，加 `DEMO_FIXED_NOW` 固定时钟；PRD §17 记幂等窗口边界效应；curl 复测 82914 → REQUIRE_CONFIRMATION | `9ebee8b` | test 833/833 |
