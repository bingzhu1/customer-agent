"""Golden dataset 用例格式（PRD §12.3）与加载器。

原则（PRD §12.1）：确定性断言为主，LLM 评判只用于语气与 groundedness。
本模块只定义形状；runner（下一 milestone）负责执行与比对。
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from cs_agent.domain.enums import DecisionOutcome, GoldenCategory, ReasonCode

SideEffect = Literal["none", "refund_created", "ticket_created", "human_review_created"]

CASE_ID_PATTERN = r"^(POL|ORD|SEC|ESC|MEM|RAG|IDEM)-\d{3}$"

CATEGORY_PREFIX: dict[GoldenCategory, str] = {
    GoldenCategory.POLICY: "POL",
    GoldenCategory.ORDER: "ORD",
    GoldenCategory.SECURITY: "SEC",
    GoldenCategory.ESCALATION: "ESC",
    GoldenCategory.MEMORY: "MEM",
    GoldenCategory.RAG: "RAG",
    GoldenCategory.IDEMPOTENCY: "IDEM",
}


class Auth(BaseModel):
    model_config = ConfigDict(extra="forbid")

    user_id: int
    roles: list[str] = Field(default_factory=lambda: ["customer"])


class Expect(BaseModel):
    """确定性断言。全部可选，未写的项不检查。"""

    model_config = ConfigDict(extra="forbid")

    decision: DecisionOutcome | None = None
    reason_code: ReasonCode | None = None
    confidence: Literal["low", "normal"] | None = None
    tools_called_must_include: list[str] = Field(default_factory=list)
    tools_called_must_not_include: list[str] = Field(default_factory=list)
    citations_must_include: list[str] = Field(default_factory=list)  # policy_id 列表
    citations_must_be_empty: bool = False
    db_side_effects: SideEffect | None = None
    response_must_contain: list[str] = Field(default_factory=list)
    response_must_not_contain: list[str] = Field(default_factory=list)
    # 低置信措辞检查（PRD §12.4）：为 true 时 runner 用关键词表检查不得出现确定性措辞
    no_certainty_wording: bool = False


class Judge(BaseModel):
    """仅这两项允许用 LLM 评判（PRD §12.1 第 2 条）。"""

    model_config = ConfigDict(extra="forbid")

    tone_appropriate: bool = True
    groundedness: bool = True


class ToolFault(BaseModel):
    """故障注入：让某工具在本轮返回错误。

    用于 TOOL_FAILURE_REPEATED / DEPENDENCY_UNAVAILABLE 类用例。
    """

    model_config = ConfigDict(extra="forbid")

    tool: str
    error: Literal["timeout", "unavailable", "error"] = "unavailable"
    times: Annotated[int, Field(ge=1)] = 1


class Turn(BaseModel):
    """一轮交互。`user` 为用户发言；`confirm` 为对当前 pending_action 的确认操作。

    `repeat > 1` 表示把同一确认请求重复发送（顺序）；`concurrent: true` 表示并发发送。
    每轮可带自己的 `expect`；用例顶层的 `expect` 作用于最后一轮。
    """

    model_config = ConfigDict(extra="forbid")

    user: str | None = None
    confirm: bool | None = None
    repeat: Annotated[int, Field(ge=1)] = 1
    concurrent: bool = False
    faults: list[ToolFault] = Field(default_factory=list)
    expect: Expect | None = None

    @model_validator(mode="after")
    def _exactly_one_kind(self) -> Turn:
        if (self.user is None) == (self.confirm is None):
            raise ValueError("turn must have exactly one of `user` or `confirm`")
        if self.user is not None and (self.repeat > 1 or self.concurrent):
            raise ValueError("repeat/concurrent only apply to confirm turns")
        return self


class GoldenCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: Annotated[str, Field(pattern=CASE_ID_PATTERN)]
    category: GoldenCategory
    description: str
    review: Literal["sample", "each"] = "sample"  # PRD §12.2 "谁 review"：抽查 / 逐条
    auth: Auth
    turns: Annotated[list[Turn], Field(min_length=1)]
    expect: Expect
    judge: Judge = Field(default_factory=Judge)
    tags: list[str] = Field(default_factory=list)
    notes: str | None = None

    @model_validator(mode="after")
    def _id_matches_category(self) -> GoldenCase:
        prefix = CATEGORY_PREFIX[self.category]
        if not self.id.startswith(prefix + "-"):
            raise ValueError(f"{self.id}: prefix must be {prefix} for category {self.category}")
        return self


class GoldenDataset(BaseModel):
    cases: list[GoldenCase]

    @model_validator(mode="after")
    def _unique_ids(self) -> GoldenDataset:
        ids = [c.id for c in self.cases]
        dupes = {i for i in ids if ids.count(i) > 1}
        if dupes:
            raise ValueError(f"duplicate case ids: {sorted(dupes)}")
        return self

    def by_category(self) -> dict[GoldenCategory, list[GoldenCase]]:
        out: dict[GoldenCategory, list[GoldenCase]] = {c: [] for c in GoldenCategory}
        for case in self.cases:
            out[case.category].append(case)
        return out


def load_golden_file(path: Path) -> list[GoldenCase]:
    raw: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError(f"{path}: top level must be a list of cases")
    return [GoldenCase.model_validate(item) for item in raw]


def load_golden(directory: Path) -> GoldenDataset:
    cases: list[GoldenCase] = []
    for path in sorted(directory.glob("*.yaml")):
        cases.extend(load_golden_file(path))
    return GoldenDataset(cases=cases)
