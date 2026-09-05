from cs_agent.settings import Settings


def test_defaults_match_prd_model_ids() -> None:
    s = Settings(_env_file=None)  # type: ignore[call-arg]
    assert s.llm_model_primary == "claude-sonnet-5"
    assert s.llm_model_fallback == "claude-haiku-4-5"


def test_placeholder_key_is_not_configured() -> None:
    s = Settings(_env_file=None, anthropic_api_key="sk-ant-...")  # type: ignore[call-arg]
    assert not s.llm_configured


def test_secrets_not_in_repr() -> None:
    s = Settings(_env_file=None, anthropic_api_key="sk-ant-secret-value-1234567890")  # type: ignore[call-arg]
    assert "secret-value" not in repr(s)
