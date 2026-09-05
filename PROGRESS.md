
| 2026-09-05 | Phase 1 | 第二轮 ④：三个只读工具 get_refunds / get_payments / get_profile（签名无身份字段，get_profile 无参数；越权与不存在同样返回空，避免存在性泄露）+ understand 增加 refund_status / payment_status / membership_question 三个意图；另加起服务预热（WARMUP_ON_STARTUP，失败只记日志），测试环境钉死为关 | `PENDING` | test 828/828、lint 通过 |
