import pytest
from fastapi import HTTPException

from ai_capabilities import capabilities, image_supported, request_options


def provider(vendor="deepseek", model="deepseek-flash", **extra):
    return {"id": vendor, "model": model, **extra}


def levels(p):
    return [item["value"] for item in capabilities(p)["options"]]


@pytest.mark.parametrize("model", ["deepseek-flash", "deepseek-v4-pro"])
def test_deepseek_actual_levels_and_default_budget(model):
    p = provider(model=model)
    assert levels(p) == ["default", "none", "low", "high", "max"]
    default = request_options(p)
    assert default == {"max_tokens": 32768}
    for effort in ("low", "high", "max"):
        fields = request_options(p, effort)
        assert fields == {"thinking": {"type": "enabled"}, "reasoning_effort": effort, "max_tokens": 32768}
        assert "extra_body" not in fields
    assert request_options(p, "none") == {"thinking": {"type": "disabled"}, "reasoning_effort": "none"}


@pytest.mark.parametrize("effort", ["medium", "xhigh", "ultra", "automatic", "LOW", None, {}, 3])
def test_invalid_or_mapped_alias_effort_is_rejected(effort):
    with pytest.raises(HTTPException) as error:
        request_options(provider(), effort)
    assert error.value.status_code == 400


@pytest.mark.parametrize("vendor,model", [("custom", "deepseek-flash"), ("deepseek", "deepseek-pro"), ("openai", "gpt-5.2-pro"), ("openai", "gpt-6-unknown"), ("openai", "gpt-5.5-2028-01-01"), ("anthropic", "claude-future-9"), ("qwen", "qwen-plus-2025-01-25"), ("qwen", "qwen-plus-2028-01-01"), ("doubao", "ep-20260101-abcd"), ("moonshot", "kimi-k3.1-pro")])
def test_unverified_models_remain_default(vendor, model):
    p = provider(vendor, model)
    assert levels(p) == ["default"]
    assert request_options(p) == {}
    with pytest.raises(HTTPException):
        request_options(p, "high")


@pytest.mark.parametrize("model,expected", [
    ("gpt-6-astra", ["low", "medium", "high", "xhigh", "max"]),
    ("gpt-6.1-sol", ["low", "medium", "high", "xhigh", "max"]),
    ("gpt-6.1-terra", ["none", "low", "medium", "high", "xhigh", "max"]),
    ("gpt-5.5", ["none", "low", "medium", "high", "xhigh"]),
    ("gpt-5.2-2025-12-11", ["none", "low", "medium", "high", "xhigh"]),
    ("gpt-5.1", ["none", "low", "medium", "high"]),
])
def test_openai_model_specific_levels_and_token_field(model, expected):
    p = provider("openai", model)
    assert levels(p) == ["default", *expected]
    fields = request_options(p, "high")
    assert fields == {"reasoning_effort": "high", "max_completion_tokens": 32768}
    assert "max_tokens" not in fields
    assert "reasoning_effort" not in request_options(p)


def test_claude_uses_official_output_config_and_adaptive_mode():
    p = provider("anthropic", "claude-sonnet-4-6", protocol="anthropic")
    assert "xhigh" not in levels(p)
    assert request_options(p, "high") == {"thinking": {"type": "adaptive"}, "output_config": {"effort": "high"}, "max_tokens": 32768}
    assert request_options(p, "none") == {"thinking": {"type": "disabled"}}
    assert request_options(p) == {}
    newer = provider("anthropic", "claude-opus-5-5", protocol="anthropic")
    assert "none" not in levels(newer)
    assert "xhigh" in levels(newer)
    assert request_options(newer) == {"max_tokens": 32768}


@pytest.mark.parametrize("model", ["qwen-plus", "qwen-plus-2025-04-28", "qwen-flash-2025-07-28", "qwen3.5-plus"])
def test_qwen_toggle_is_not_fake_effort_levels(model):
    p = provider("qwen", model)
    assert levels(p) == ["default", "none", "enabled"]
    assert request_options(p, "enabled") == {"enable_thinking": True}
    assert request_options(p, "none") == {"enable_thinking": False}
    assert request_options(p) == {}


def test_gemini_does_not_turn_off_pro_or_later_thinking():
    assert "none" not in levels(provider("gemini", "gemini-2.5-pro"))
    assert "none" in levels(provider("gemini", "gemini-2.5-flash"))
    assert "none" not in levels(provider("gemini", "gemini-3-flash-preview"))
    assert "xhigh" not in levels(provider("gemini", "gemini-3-flash-preview"))
    fields = request_options(provider("gemini", "gemini-2.5-pro"), "high")
    assert fields == {"reasoning_effort": "high", "max_tokens": 32768}


def test_glm_effective_grades_follow_version():
    assert levels(provider("zhipu", "glm-5.2")) == ["default", "none", "high", "max"]
    assert levels(provider("zhipu", "glm-5.3")) == ["default", "low", "high", "max"]
    assert request_options(provider("zhipu", "glm-5.2"), "none") == {"thinking": {"type": "disabled"}, "reasoning_effort": "none"}
    assert request_options(provider("zhipu", "glm-4.6"), "enabled") == {"thinking": {"type": "enabled"}, "max_tokens": 32768}


def test_image_capability_never_grants_consent_or_infers_unknowns():
    assert image_supported(provider()) is True
    assert image_supported(provider(model="deepseek-v4-pro")) is False
    assert image_supported(provider("custom", "deepseek-v4-pro")) is None
    assert image_supported(provider("qwen", "qwen-plus")) is None


def test_large_requested_output_budget_is_never_lowered():
    assert request_options(provider(), "low", 64000)["max_tokens"] == 64000
    assert request_options(provider("openai", "gpt-5.5"), "none", 64000)["max_completion_tokens"] == 64000
