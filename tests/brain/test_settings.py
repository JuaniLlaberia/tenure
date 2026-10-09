from brain.deps import Settings

FLASH = "deepseek/deepseek-v4-flash-0731"

def test_defaults():
    settings = Settings.from_env({})
    assert settings.model_lead == FLASH
    assert settings.model_specialist == FLASH
    assert settings.model_reflect == FLASH
    assert settings.model_decide_fallback == FLASH
    assert settings.model_jev == "typesafe/jev-1.13"
    assert settings.jev_url == "https://openrouter.ai/api/alpha/decisions"
    assert settings.decide_threshold == 0.7
    assert settings.openrouter_api_key is None

def test_reads_env():
    settings = Settings.from_env(
        {
            "OPENROUTER_API_KEY": "sk-test",
            "MODEL_LEAD": "some/model",
            "DECIDE_THRESHOLD": "0.65",
        }
    )
    assert settings.openrouter_api_key.get_secret_value() == "sk-test"
    assert settings.model_lead == "some/model"
    assert settings.decide_threshold == 0.65

def test_empty_env_values_use_defaults():
    settings = Settings.from_env(
        {"MODEL_LEAD": "", "DECIDE_THRESHOLD": "", "OPENROUTER_API_KEY": ""}
    )
    assert settings.model_lead == FLASH
    assert settings.decide_threshold == 0.7
    assert settings.openrouter_api_key is None

def test_keys_hidden_in_repr():
    settings = Settings.from_env(
        {"OPENROUTER_API_KEY": "sk-secret-123", "DATABASE_URL": "postgres://u:pw-456@host/db"}
    )
    for text in [repr(settings), str(settings)]:
        assert "sk-secret-123" not in text
        assert "pw-456" not in text
