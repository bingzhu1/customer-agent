"""初始迁移：biz 七张业务表 + agent.eval_runs / eval_results

Revision ID: 0001
Revises:
Create Date: 2026-09-05

表结构对应 PRD §7.2 / §7.3。schema biz / agent 与 vector 扩展由
docker/postgres/init/01-init.sql 建好，这里用 CREATE SCHEMA IF NOT EXISTS 兜底。
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

BIZ = "biz"
AGENT = "agent"


def upgrade() -> None:
    op.execute(f"CREATE SCHEMA IF NOT EXISTS {BIZ}")
    op.execute(f"CREATE SCHEMA IF NOT EXISTS {AGENT}")

    # ---- biz.users：客户主体 ----
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("tier", sa.String(32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("email", name="uq_users_email"),
        schema=BIZ,
    )

    # ---- biz.orders：订单主体（note 为买家留言，间接注入用例用） ----
    op.create_table(
        "orders",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("total_amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("currency", sa.String(8), nullable=False),
        sa.Column("placed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], [f"{BIZ}.users.id"], name="fk_orders_user_id_users"),
        schema=BIZ,
    )
    op.create_index("ix_biz_orders_user_id", "orders", ["user_id"], schema=BIZ)

    # ---- biz.order_items：订单明细，category / item_condition 为策略维度 ----
    op.create_table(
        "order_items",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("order_id", sa.Integer(), nullable=False),
        sa.Column("sku", sa.String(64), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("category", sa.String(32), nullable=False),
        sa.Column("qty", sa.Integer(), nullable=False),
        sa.Column("unit_price", sa.Numeric(12, 2), nullable=False),
        sa.Column("item_condition", sa.String(32), nullable=False),
        sa.ForeignKeyConstraint(
            ["order_id"], [f"{BIZ}.orders.id"], name="fk_order_items_order_id_orders"
        ),
        schema=BIZ,
    )
    op.create_index("ix_biz_order_items_order_id", "order_items", ["order_id"], schema=BIZ)

    # ---- biz.shipments：物流 ----
    op.create_table(
        "shipments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("order_id", sa.Integer(), nullable=False),
        sa.Column("carrier", sa.String(64), nullable=False),
        sa.Column("tracking_no", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("shipped_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("estimated_delivery", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_event_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_event_desc", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["order_id"], [f"{BIZ}.orders.id"], name="fk_shipments_order_id_orders"
        ),
        schema=BIZ,
    )
    op.create_index("ix_biz_shipments_order_id", "shipments", ["order_id"], schema=BIZ)

    # ---- biz.payments：支付记录 ----
    op.create_table(
        "payments",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("order_id", sa.Integer(), nullable=False),
        sa.Column("method", sa.String(32), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["order_id"], [f"{BIZ}.orders.id"], name="fk_payments_order_id_orders"
        ),
        schema=BIZ,
    )
    op.create_index("ix_biz_payments_order_id", "payments", ["order_id"], schema=BIZ)

    # ---- biz.tickets：工单，body 为用户原文（不可信内容） ----
    op.create_table(
        "tickets",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("order_id", sa.Integer(), nullable=True),
        sa.Column("type", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("priority", sa.String(16), nullable=False),
        sa.Column("subject", sa.String(200), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], [f"{BIZ}.users.id"], name="fk_tickets_user_id_users"),
        sa.ForeignKeyConstraint(
            ["order_id"], [f"{BIZ}.orders.id"], name="fk_tickets_order_id_orders"
        ),
        schema=BIZ,
    )
    op.create_index("ix_biz_tickets_user_id", "tickets", ["user_id"], schema=BIZ)

    # ---- biz.refunds：退款结果，simulated 默认 true ----
    op.create_table(
        "refunds",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("order_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("reason_code", sa.String(64), nullable=True),
        sa.Column("policy_id", sa.String(64), nullable=True),
        sa.Column("policy_version", sa.Integer(), nullable=True),
        sa.Column("simulated", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("executed_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["order_id"], [f"{BIZ}.orders.id"], name="fk_refunds_order_id_orders"
        ),
        sa.ForeignKeyConstraint(["user_id"], [f"{BIZ}.users.id"], name="fk_refunds_user_id_users"),
        schema=BIZ,
    )
    op.create_index("ix_biz_refunds_order_id", "refunds", ["order_id"], schema=BIZ)

    # ---- agent.eval_runs：评估批次 ----
    op.create_table(
        "eval_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("version_tag", sa.String(64), nullable=False),
        sa.Column("git_sha", sa.String(64), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("config", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        schema=AGENT,
    )

    # ---- agent.eval_results：单条评估结果 ----
    op.create_table(
        "eval_results",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("case_id", sa.String(128), nullable=False),
        sa.Column("passed", sa.Boolean(), nullable=False),
        sa.Column("decision", sa.String(32), nullable=True),
        sa.Column("reason_code", sa.String(64), nullable=True),
        sa.Column("metrics", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("raw", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.ForeignKeyConstraint(
            ["run_id"], [f"{AGENT}.eval_runs.id"], name="fk_eval_results_run_id_eval_runs"
        ),
        schema=AGENT,
    )
    op.create_index("ix_agent_eval_results_run_id", "eval_results", ["run_id"], schema=AGENT)


def downgrade() -> None:
    # 按外键依赖逆序删除；不删 schema（由 docker init 拥有）
    op.drop_index("ix_agent_eval_results_run_id", table_name="eval_results", schema=AGENT)
    op.drop_table("eval_results", schema=AGENT)
    op.drop_table("eval_runs", schema=AGENT)

    op.drop_index("ix_biz_refunds_order_id", table_name="refunds", schema=BIZ)
    op.drop_table("refunds", schema=BIZ)
    op.drop_index("ix_biz_tickets_user_id", table_name="tickets", schema=BIZ)
    op.drop_table("tickets", schema=BIZ)
    op.drop_index("ix_biz_payments_order_id", table_name="payments", schema=BIZ)
    op.drop_table("payments", schema=BIZ)
    op.drop_index("ix_biz_shipments_order_id", table_name="shipments", schema=BIZ)
    op.drop_table("shipments", schema=BIZ)
    op.drop_index("ix_biz_order_items_order_id", table_name="order_items", schema=BIZ)
    op.drop_table("order_items", schema=BIZ)
    op.drop_index("ix_biz_orders_user_id", table_name="orders", schema=BIZ)
    op.drop_table("orders", schema=BIZ)
    op.drop_table("users", schema=BIZ)
