"""YAML → RAG chunk 生成器（FR-301、PRD §11 ①）。

策略 YAML 是唯一事实来源，chunk **由它生成**而不是人工同步：
- 每条规则一张 **rule card**：`human_text` 原文 + 适用范围 / 条件 / 处理结果的自然语言渲染；
- 规则下的每条 `faq` 各自一个 chunk（本项目的 FAQ 都短，不需要 600 token 二次切分）。

**确定性是硬要求**：同一份 YAML 生成两次必须逐字节相同，否则 ingest 无法判断
"内容是否真的变了"，也就没法做幂等覆盖。因此这里不用集合遍历、不用 `hash()`、
不带时间戳，字典一律按键排序输出。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Literal

from cs_agent.domain.enums import PolicyEffect
from cs_agent.policy.schema import Condition, PolicyRule, PolicySet

ChunkKind = Literal["rule_card", "faq"]

#: 条件字段的中文名。策略引擎的事实字段（PRD §9.3）在这里只做"渲染成人话"，
#: 不参与任何判定；未收录的字段回落到原始英文名，不报错。
_FIELD_LABELS: dict[str, str] = {
    "days_since_delivery": "签收后天数",
    "days_since_purchase": "下单后天数",
    "item_condition": "商品状态",
    "item_category": "商品类别",
    "order_amount": "订单金额",
    "order_delivered": "订单是否已签收",
    "user_tier": "会员等级",
    "ticket_type": "工单类型",
}

_VALUE_LABELS: dict[str, str] = {
    "unused": "未使用",
    "unopened": "未拆封",
    "used": "已使用",
    "damaged": "已损坏",
    "standard": "标准商品",
    "food": "食品",
    "custom": "定制商品",
    "gold": "金卡会员",
    "true": "是",
    "false": "否",
}

#: `applies_to` 三个字段各自的值标签表不同（standard 在类别里是"标准商品"，在等级里是"普通会员"）
_TIER_LABELS: dict[str, str] = {"standard": "普通会员", "gold": "金卡会员"}

_OPERATOR_WORDS: list[tuple[str, str]] = [
    ("eq", "等于"),
    ("ne", "不等于"),
    ("lt", "小于"),
    ("lte", "不超过"),
    ("gt", "大于"),
    ("gte", "不少于"),
    ("in_", "属于"),
    ("not_in", "不属于"),
]

_EFFECT_TEXT: dict[PolicyEffect, str] = {
    PolicyEffect.ALLOW_REFUND: "满足上述条件时可以退款，不满足则不符合退款条件。",
    PolicyEffect.DENY_REFUND: "属于本规则适用范围的，不支持退款。",
    PolicyEffect.REQUIRE_HUMAN: "属于本规则适用范围的，不能自动办理，需转人工专员处理。",
    PolicyEffect.INFORMATIONAL: "本条为说明性政策，只用于答复咨询，不参与退款资格判定。",
}


@dataclass(frozen=True, slots=True)
class PolicyChunkData:
    """一个待入库的 chunk。字段与 `agent.policy_chunks` 一一对应。"""

    policy_id: str
    policy_version: int
    chunk_index: int
    kind: ChunkKind
    anchor: str
    content: str
    metadata: dict[str, Any]

    def metadata_json(self) -> str:
        """metadata 的稳定 JSON，直接写进 `policy_chunks.metadata`（jsonb）。"""
        return json.dumps(self.metadata, ensure_ascii=False, sort_keys=True)

    def to_json(self) -> str:
        """稳定序列化，供确定性断言与调试比对用（键排序、不转义中文）。"""
        return json.dumps(
            {
                "policy_id": self.policy_id,
                "policy_version": self.policy_version,
                "chunk_index": self.chunk_index,
                "kind": self.kind,
                "anchor": self.anchor,
                "content": self.content,
                "metadata": self.metadata,
            },
            ensure_ascii=False,
            sort_keys=True,
        )


def _fmt_scalar(field: str, value: Any) -> str:
    if isinstance(value, bool):
        return _VALUE_LABELS["true" if value else "false"]
    if field == "user_tier":
        return _TIER_LABELS.get(str(value), str(value))
    return _VALUE_LABELS.get(str(value), str(value))


def _fmt_value(field: str, value: Any) -> str:
    if isinstance(value, list):
        return "、".join(_fmt_scalar(field, v) for v in value)
    return _fmt_scalar(field, value)


def render_condition(field: str, condition: Condition) -> str:
    """把单个条件渲染成一句人话，如 `签收后天数 不超过 30`。

    一个字段上可以同时挂多个操作符（YAML 里少见但 schema 允许），按固定顺序拼接。
    """
    label = _FIELD_LABELS.get(field, field)
    parts: list[str] = []
    for attr, word in _OPERATOR_WORDS:
        value = getattr(condition, attr)
        if value is None:
            continue
        rendered = _fmt_value(field, value)
        # 数字/英文前留一个空格（中文排版惯例），中文值前不留
        sep = " " if rendered[:1].isascii() and rendered[:1].isalnum() else ""
        parts.append(f"{word}{sep}{rendered}")
    return label + "且".join(parts)


def _render_applies_to(rule: PolicyRule) -> str:
    a = rule.applies_to
    parts: list[str] = []
    if a.item_category is not None:
        parts.append(f"商品类别为{_fmt_value('item_category', a.item_category)}")
    if a.user_tier is not None:
        parts.append(f"会员等级为{_fmt_value('user_tier', a.user_tier)}")
    if a.ticket_type is not None:
        parts.append(f"工单类型为{_fmt_value('ticket_type', a.ticket_type)}")
    return "；".join(parts) if parts else "全部用户与全部商品类别"


def _render_outcome(rule: PolicyRule) -> str:
    parts = [_EFFECT_TEXT[rule.effect]]
    if rule.max_auto_amount is not None:
        parts.append(f"单笔退款不超过 {rule.max_auto_amount} 元的可由系统自动办理。")
    if rule.requires_approval_above is not None:
        parts.append(f"超过 {rule.requires_approval_above} 元的需转人工审批后处理。")
    return "".join(parts)


def render_rule_card(rule: PolicyRule) -> str:
    """rule card 正文：结构化条件的人话渲染 + `human_text` 原文。

    原文放最后且完整保留——检索命中后要能原样引用，不允许被摘要掉。
    """
    lines = [
        f"【策略】{rule.id} v{rule.version}｜{rule.title}",
        f"【适用范围】{_render_applies_to(rule)}",
    ]
    if rule.conditions:
        rendered = "；".join(
            render_condition(field, rule.conditions[field]) for field in sorted(rule.conditions)
        )
        lines.append(f"【生效条件】{rendered}")
    lines.append(f"【处理结果】{_render_outcome(rule)}")
    lines.append(f"【生效日期】{rule.effective_date.isoformat()} 起")
    lines.append("【政策原文】")
    lines.append(rule.human_text.strip())
    return "\n".join(lines)


def render_faq(rule: PolicyRule, index: int) -> str:
    entry = rule.faq[index]
    return f"【常见问题】{rule.title}\n问：{entry.q}\n答：{entry.a}"


def _metadata(rule: PolicyRule, kind: ChunkKind) -> dict[str, Any]:
    """PRD §11 ① 要求的 chunk metadata。键固定、值全为 JSON 标量，保证可稳定序列化。"""
    return {
        "policy_id": rule.id,
        "policy_version": rule.version,
        "domain": rule.domain.value,
        "anchor": rule.anchor,
        "effective_date": rule.effective_date.isoformat(),
        "kind": kind,
        "title": rule.title,
    }


def chunk_rule(rule: PolicyRule) -> list[PolicyChunkData]:
    """一条规则 → `1 张 rule card + len(faq) 个 FAQ chunk`，chunk_index 从 0 连续编号。"""
    chunks = [
        PolicyChunkData(
            policy_id=rule.id,
            policy_version=rule.version,
            chunk_index=0,
            kind="rule_card",
            anchor=rule.anchor,
            content=render_rule_card(rule),
            metadata=_metadata(rule, "rule_card"),
        )
    ]
    for i in range(len(rule.faq)):
        chunks.append(
            PolicyChunkData(
                policy_id=rule.id,
                policy_version=rule.version,
                chunk_index=i + 1,
                kind="faq",
                anchor=rule.anchor,
                content=render_faq(rule, i),
                metadata=_metadata(rule, "faq"),
            )
        )
    return chunks


def chunk_policies(policies: PolicySet) -> list[PolicyChunkData]:
    """整个策略集 → chunk 列表，按 policy_id 排序后展开，与 YAML 文件顺序无关。"""
    chunks: list[PolicyChunkData] = []
    for rule in sorted(policies.rules, key=lambda r: r.id):
        chunks.extend(chunk_rule(rule))
    return chunks
