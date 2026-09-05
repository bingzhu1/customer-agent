"""Phase 2 的 RAG 子系统：YAML → chunk → embedding → pgvector 检索。

分层边界（CLAUDE.md §7）：本包只提供检索能力，不含任何业务规则，也不判定授权。
`search_policy` 工具由 Phase 1 拥有，它调用 `PolicyRetriever.search`，本包不反向依赖 tools / graph。
"""
