-- 首次建库时执行（仅空数据卷时触发）。
-- 表结构由 Alembic 管理，这里只做扩展、schema 与 Langfuse 独立库。
CREATE EXTENSION IF NOT EXISTS vector;
CREATE SCHEMA IF NOT EXISTS biz;
CREATE SCHEMA IF NOT EXISTS agent;
-- Langfuse 使用同一实例内的独立数据库，与业务/Agent 数据隔离
CREATE DATABASE langfuse;
