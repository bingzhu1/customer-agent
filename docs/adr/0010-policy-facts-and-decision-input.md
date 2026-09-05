# ADR-0010：PolicyFacts 与 DecisionInput 的字段边界与来源层

- 状态：已接受
- 日期：2026-09-05
- 相关：PRD §9.2–§9.5、FR-401/403/404、ADR-0005、ADR-0008、ADR-0009、CLAUDE.md 红线 1–3
- 代码：`src/cs_agent/policy/facts.py`、`src/cs_agent/policy/engine.py`、`src/cs_agent/decision/matrix.py`

## 背景

Phase 3 把"能不能退"拆成两个纯函数：

```
业务库 ──> PolicyFacts ──evaluate()──> PolicyVerdict ─┐
                                                      ├─> DecisionInput ──decide()──> Decision
其余确定性信号 ───────────────────────────────────────┘
```

这两个入参结构是整个安全模型的收口处。**只要一个字段的来源写错，红线就破了**：
`user_tier` 若来自记忆，ADR-0009 的投毒防线就没了；`ownership_ok` 若来自 LLM 的判断，
ADR-0008 的"越权不可表达"就没了。所以字段边界必须单独立一篇，而不是散在 docstring 里。

需要回答三个问题：哪些字段进、每个字段由**哪一层**填、为什么在这里切一刀。

## 决策

### 一、`PolicyFacts`：8 个字段，全部来自 `biz` schema

策略引擎的唯一输入。每个字段在每一轮**现查数据库**（FR-403），不复用上一轮结果，
不复用对话里提到的值。

| 字段 | 含义 | 来源 | 为什么不能来自别处 |
|---|---|---|---|
| `order_id` | 订单标识，仅用于审计与复现 | `understanding.order_id` 经 Repository scope 校验后 | 用户提的号码只是**查询键**；查得到才算数，查不到走矩阵规则 1 |
| `user_tier` | 会员等级 | `biz.users.tier` | 记忆里的"这是 VIP"不是等级（ADR-0009）；对话里的自述更不是 |
| `item_category` | 主商品类别 | `biz.order_items.category` | 类别决定食品 / 定制的硬拒绝，不能由模型归类 |
| `item_condition` | 商品状态 | `biz.order_items.item_condition` | 用户说"我没拆封"不构成事实；需要人工核实的走 require_human |
| `order_amount` | 订单金额 | `biz.orders.total_amount` | 对话里的金额可被诱导（"这单才 50 块"），限额判断必须用库里的值 |
| `order_delivered` | 是否已签收 | `biz.orders.delivered_at is not None` | 决定走 REFUND-UNDELIVERED-001 的人工拦截路径 |
| `days_since_delivery` | 签收天数 | `(now - delivered_at).days`，`now` 可注入 | 用户说"上周刚到"不算；未签收为 `None`，数值条件一律不通过 |
| `prior_refund_exists` | 是否已有成功退款 | `biz.refunds` 查询 | 幂等的事实依据只能是库，不能是"我记得退过" |

**刻意不进的字段**，每一个都是有意为之：

- **任何记忆字段**。`evaluate()` 的签名里根本没有记忆参数——让越权在类型层面不可表达
  （ADR-0009 强制手段第 3 条）。`tests/test_policy_engine.py::test_facts_has_no_memory_field`
  断言字段集恒等于 `CONDITION_FIELDS | {order_id}`，加字段就会红。
- **`user_id` / `tenant_id`**。身份不是策略事实，是 Repository 的 scope（红线 1、ADR-0008）。
  归属校验的结果以 `ownership_ok` 进决策层，不进策略层。
- **`ticket_type`**。schema 的 `applies_to` 支持它，但工单类型不是退款资格的输入；
  引擎显式忽略 `applies_to.ticket_type`。
- **检索结果 / 置信度**。策略判定不看 RAG 打分，低置信的处理在决策层规则 10.5。
- **`order_id` 不参与条件求值**。它在 `CONDITION_FIELDS` 之外——策略是规则，
  不能针对单个订单开后门。YAML 里写 `conditions: {order_id: ...}` 会直接抛 `PolicyConfigError`。

### 二、`DecisionInput`：17 个字段，来自确定性节点

矩阵不重新解释政策，只做优先级归并。字段按来源分四组：

**A. 安全闸门（规则 1–3）——来自 Repository 与确定性检测器**

| 字段 | 来源 | 说明 |
|---|---|---|
| `ownership_ok` | Repository 强制 scope 的查询结果 | 查不到就是 `False`，**不区分"不存在"与"不属于你"** |
| `injection_suspected` | `graph/untrusted.py` 的关键词检测 | 作用于用户消息与工具输出，不是让 LLM 自己判断"我是不是被注入了" |
| `role_sufficient` | `AuthContext.roles` | 用户自述身份不进授权判断（红线 1） |

**B. 升级信号（规则 4–7）——来自 LLM 的理解结果，但只作为"提议"**

| 字段 | 来源 | 说明 |
|---|---|---|
| `customer_requests_human` | `understanding.wants_human` | LLM 只报告"用户想转人工"，转不转由矩阵定 |
| `high_negative_sentiment` | `understanding.negative_sentiment` | 同上 |
| `repeated_tool_failure` | 图的重试计数器 | 计数是代码的，不是模型的印象 |
| `dependency_unavailable` | 依赖健康检查 / 异常捕获 | — |

这组字段**允许**来自 LLM，因为它们只能让结论**更保守**（都指向 REQUIRE_HUMAN）。
模型说谎的最坏后果是多转一次人工，不会造成越权或错误放款。这是本 ADR 的切分原则：
**能放宽结论的输入必须来自确定性来源；只能收紧结论的输入可以来自模型。**

**C. 策略与金额（规则 8–12）**

| 字段 | 来源 | 说明 |
|---|---|---|
| `verdict` | `evaluate()` 的返回值 | 矩阵不重算政策，只翻译 |
| `amount` | `biz.orders.total_amount` | 与 `PolicyFacts.order_amount` 同源，避免两处漂移 |
| `is_write_intent` / `is_eligibility_intent` | `understanding.intent` 映射到白名单 | 属于 B 组性质：判成写意图只会更严 |
| `idempotent_replay` | `biz.refunds` 唯一约束的查询结果 | 不是"应用层先查再写"，是库给的事实 |

**D. 检索置信（规则 10.5、13–14b）**

| 字段 | 来源 | 说明 |
|---|---|---|
| `retrieval_max_score` | 检索器返回的最高分 | `None` 表示本轮没做检索，相关规则整体不参与 |
| `tau_low` / `tau_high` | 配置（ADR-0007，Phase 2 标定） | 阈值是配置项，不是模型的判断 |
| `has_citable_chunk` | 引用后置校验的结果 | 规则 14 的第三个约束：低置信也要有据 |
| `missing_entity` | 实体抽取后的确定性检查 | — |

### 三、为什么切成两个结构、两个函数

1. **策略可以离线重放**。`PolicyFacts` 只有 8 个业务字段，一行 SQL 就能重建；
   出了争议可以拿当时的事实 + 当时的 YAML 版本复现判定，这是审计要的东西。
2. **两层的变更频率不同**。政策 YAML 由业务改（版本递增，不改旧版）；升级矩阵由工程改。
   合成一个函数会让"改了退款窗口"和"改了升级顺序"混在同一次评估回归里。
3. **`PolicyVerdict` 是两层之间唯一的接口**，且必带 `policy_id` / `policy_version`（FR-402）。
   回答里引用的版本与判定用的版本因此天然一致（ADR-0006、FR-306）。
4. **两个函数都是纯函数**，测试不需要数据库也不需要 LLM——Phase 3 的 210 个用例全部离线跑。

## 理由

替代做法是让一个 `decide(context)` 吃下所有东西。否决理由：
`context` 会变成筐，什么都往里塞，字段来源无从约束；一旦有人往里加 `user_memory`，
红线 3 只剩注释拦得住。**窄接口本身就是防线**——这与 ADR-0008 让工具签名不含 `user_id`
是同一个手法：不是禁止越权，是让越权写不出来。

## 备选方案

| 方案 | 否决理由 |
|---|---|
| 单一 `decide(context)` 大结构 | 字段来源不可约束，红线只能靠人盯 |
| `PolicyFacts` 里放 `user_id`，引擎自己查归属 | 引擎就不再是纯函数；归属属于 Repository 的职责 |
| 策略引擎直接返回 `DecisionOutcome` | 政策变更会牵动升级矩阵；也拿不到"ALLOW 但超限额"这种中间态 |
| 用 dict 传事实 | 丢掉类型检查，`mypy strict` 挡不住拼错的字段名 |

## 后果

**正面**：红线 1 与红线 3 有了结构性保证而非纪律性保证；判定可离线复现；两层可独立演进。

**负面**：

- 图里要多一个 `policy_gate` 节点专门把库里的行装配成 `PolicyFacts`，多一次查询往返；
- `PolicyFacts` 目前只服务退款域。保修 / 物流补偿等域若要走确定性判定，
  需要新的 facts 结构或扩展本结构（**不要**塞进 `dict[str, Any]` 了事）；
- `DecisionInput` 有 17 个字段，调用方容易漏填。这一点见下面的复审条件。

## 待办与复审条件

以下三条是本 ADR 记录在案的已知缺口，**不在冲刺内改，Phase 3 M5 收口时逐条处理**：

1. **`DecisionInput` 的默认值是宽松的**：`ownership_ok=True`、`role_sufficient=True`。
   漏填就等于"已校验通过"。当前图里 `role_sufficient` 尚未接线，规则 3 实际不会触发。
   复审时应评估改成 fail-closed（必填，或默认 `False`），代价是所有调用方都要显式传。
2. **`TOOL_BUDGET_EXCEEDED` 不在矩阵里**。§9.4 没有对应行，图里用事后 `_no_looser` 夹逼实现。
   要么补进矩阵成为正式一行，要么在 PRD 里写明它是矩阵外的钳位，二选一。
3. **D 组字段（检索置信）在 Phase 2 标定 τ 之前全部未接线**，规则 13/14/14b 目前无法触发。
   τ 标定后需要补一轮 eval 回归，确认低置信路径的行为符合 ADR-0007。

若未来出现"某些场景下记忆可以影响策略"的需求，**不是改本 ADR，而是把该信息落到
业务库的正式字段**（如 `users.tier`）再进 `PolicyFacts`——这是 ADR-0009 已经定过的取舍。
