"""引用后置校验的纯函数部分（FR-304 / FR-305）。

规则一句话：**回答里出现的每个 policy_id，都必须在本轮检索结果里**。
这里只回答"哪些引用是凭空冒出来的"，不做重生成、不做转人工——
重试一次、仍失败则 `REQUIRE_HUMAN`（FR-305）是决策层的编排职责，不属于本层。

纯函数：无 IO、无 LLM、无全局状态，可直接单测。
"""

from __future__ import annotations

from collections.abc import Iterable


def validate_citations(cited_ids: Iterable[str], retrieved_ids: Iterable[str]) -> list[str]:
    """返回"引用了但没检索到"的 policy_id 列表，去重并保持首次出现顺序。

    空列表 = 校验通过。比对前统一去空白，避免 LLM 输出里带空格导致误判；
    大小写**不做**归一化：policy_id 在 schema 里就是全大写，小写形式属于伪造。
    """
    retrieved = {rid.strip() for rid in retrieved_ids if rid.strip()}
    missing: list[str] = []
    for raw in cited_ids:
        cid = raw.strip()
        if not cid or cid in retrieved or cid in missing:
            continue
        missing.append(cid)
    return missing


def citations_are_valid(cited_ids: Iterable[str], retrieved_ids: Iterable[str]) -> bool:
    """`validate_citations` 的布尔糖，便于在断言里读得顺。"""
    return not validate_citations(cited_ids, retrieved_ids)
