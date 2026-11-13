"""Verified, model-specific chat options; unknown IDs keep provider defaults.

This is an allowlist, not a guess based on any model containing 'reasoning'.
Sources and the verification date are in notes/reasoning-provider-docs.md.
"""
from __future__ import annotations

import re

from fastapi import HTTPException

_VERIFIED_THROUGH = "2026-11-13"
_LABELS = {
    "default": "官方默认",
    "none": "关闭思考（none）",
    "enabled": "开启思考",
    "minimal": "最少（minimal）",
    "low": "低（low）",
    "medium": "中（medium）",
    "high": "高（high）",
    "xhigh": "更高（xhigh）",
    "max": "最高（max）",
}
_DOCS = {
    "deepseek": "https://api-docs.deepseek.com/guides/thinking_mode/",
    "openai": "https://developers.openai.com/api/docs/guides/reasoning?api-mode=chat",
    "anthropic": "https://platform.claude.com/docs/en/build-with-claude/effort",
    "qwen": "https://help.aliyun.com/zh/model-studio/deep-thinking",
    "gemini": "https://ai.google.dev/gemini-api/docs/openai",
    "moonshot": "https://platform.kimi.ai/docs/guide/use-thinking-models",
    "zhipu": "https://docs.bigmodel.cn/cn/guide/capabilities/thinking",
}


def _identity(provider: dict) -> tuple[str, str]:
    return str(provider.get("id", "")).strip().lower(), str(provider.get("model", "")).strip().lower()


def _model(model: str, names: set[str]) -> bool:
    """Allow a named model's date snapshot, not arbitrary suffixes or successors."""
    return model in names or bool(re.fullmatch(r".+-\d{4}-\d{2}-\d{2}", model)
        and model[:-11] in names and model[-10:] <= _VERIFIED_THROUGH)


def _profile(provider: dict) -> tuple[str, tuple[str, ...], str, bool]:
    """style, available levels, explanation, default uses thinking."""
    vendor, model = _identity(provider)
    if vendor == "deepseek" and model in {"deepseek-flash", "deepseek-v4-pro"}:
        return "deepseek", ("none", "low", "high", "max"), "官方支持关闭、low、high、max；默认开启思考，强度为 high。更高强度可能增加等待和 Token 用量。", True

    if vendor == "openai":
        # Model cards establish these exact level sets. No pro/chat/codex alias
        # is inferred: some use Responses-only APIs or different effort levels.
        if _model(model, {"gpt-6-astra", "gpt-6.1-sol"}):
            return "openai", ("low", "medium", "high", "xhigh", "max"), "此模型不支持关闭思考；按官方等级发送 reasoning_effort。", True
        if _model(model, {"gpt-6-sol", "gpt-6-luna", "gpt-6.1-terra"}):
            return "openai", ("none", "low", "medium", "high", "xhigh", "max"), "按此模型的官方 reasoning_effort 等级发送；高强度可能增加等待和 Token 用量。", True
        if _model(model, {"gpt-5.2", "gpt-5.4", "gpt-5.5"}):
            return "openai", ("none", "low", "medium", "high", "xhigh"), "按此模型的官方等级发送 reasoning_effort；不添加该模型未声明的 max 等级。", model.startswith("gpt-5.5")
        if _model(model, {"gpt-5.1"}):
            return "openai", ("none", "low", "medium", "high"), "此模型官方支持 none、low、medium、high。", False

    if vendor == "anthropic" and provider.get("protocol", "anthropic") == "anthropic":
        # Claude uses YYYYMMDD snapshot IDs, unlike OpenAI's YYYY-MM-DD IDs.
        name = re.sub(r"-\d{8}$", "", model)
        if name in {"claude-opus-4-6", "claude-sonnet-4-6"}:
            return "claude", ("none", "low", "medium", "high", "max"), "使用官方 adaptive thinking 和 output_config.effort；此版本没有独立 xhigh 等级。", False
        if name in {"claude-opus-4-7", "claude-opus-4-8"}:
            return "claude", ("none", "low", "medium", "high", "xhigh", "max"), "使用官方 adaptive thinking 和 output_config.effort。", False
        if name in {"claude-opus-5-5", "claude-fable-5-2", "claude-mythos-5-2", "claude-fable-5-1", "claude-mythos-5-1", "claude-fable-5", "claude-mythos-5"}:
            return "claude", ("low", "medium", "high", "xhigh", "max"), "此模型始终开启自适应思考；使用官方 output_config.effort，不发送禁用思考参数。", True
        if name in {"claude-opus-5", "claude-sonnet-5", "claude-sonnet-5-5", "claude-haiku-5-5"}:
            # Mode-disable semantics differ across these models. Expose the
            # verified shared effort levels, without fabricating a common off.
            return "claude", ("low", "medium", "high", "xhigh", "max"), "按官方 output_config.effort 选择强度，并启用 adaptive thinking。", True

    qwen_hybrid = model in {"qwen-plus", "qwen-plus-latest", "qwen-flash", "qwen3.5-plus", "qwen3.6-plus", "qwen3.7-plus", "qwen3.7-max", "qwen3.8-max", "qwen3-vl-plus", "qwen3-vl-plus-latest"}
    qwen_hybrid |= bool(re.fullmatch(r"qwen-plus-\d{4}-\d{2}-\d{2}", model) and "2025-04-28" <= model[-10:] <= _VERIFIED_THROUGH)
    qwen_hybrid |= bool(re.fullmatch(r"qwen-flash-\d{4}-\d{2}-\d{2}", model) and "2025-07-28" <= model[-10:] <= _VERIFIED_THROUGH)
    if vendor == "qwen" and qwen_hybrid:
        return "qwen", ("none", "enabled"), "此模型官方提供 enable_thinking 开关；未将 Token 预算包装成不存在的 low/medium/high 等级。", model.startswith(("qwen3.5", "qwen3.6", "qwen3.7", "qwen3.8"))

    if vendor == "gemini":
        if model in {"gemini-2.5-flash", "gemini-2.5-flash-lite"}:
            return "gemini", ("none", "low", "medium", "high"), "通过官方 OpenAI 兼容接口发送 reasoning_effort；low/medium/high 对应不同思考预算。", True
        if model == "gemini-2.5-pro":
            return "gemini", ("low", "medium", "high"), "此模型不能关闭思考；兼容接口将强度映射为思考预算。", True
        if model in {"gemini-3-flash-preview", "gemini-3.1-flash-lite-preview", "gemini-3.1-flash-lite"}:
            return "gemini", ("minimal", "low", "medium", "high"), "按官方 OpenAI 兼容接口发送思考等级；此模型不能关闭思考。", True
        if model == "gemini-4-hydrogen":
            return "gemini", ("low", "medium", "high", "xhigh"), "按官方兼容接口发送思考等级；此模型不能关闭思考。", True

    if vendor == "moonshot" and model in {"kimi-k2.5", "kimi-k2.6"}:
        return "toggle", ("none", "enabled"), "此模型提供 thinking 开关；未声明可分别选择的 low/medium/high 强度。", True

    if vendor == "zhipu":
        if model in {"glm-5.3", "glm-5.3-flash", "glm-5.3-flashx"}:
            return "glm", ("low", "high", "max"), "GLM-5.3 系列支持 low/high/max；不提供关闭思考选项。", True
        if model == "glm-5.2":
            return "glm", ("none", "high", "max"), "官方有效强度为 high/max；low/medium 与 xhigh 是兼容映射，未列成独立等级。", True
        if model in {"glm-4.5", "glm-4.5-air", "glm-4.5v", "glm-4.6", "glm-4.6v", "glm-4.7", "glm-5", "glm-5.1", "glm-5-turbo", "glm-5v-turbo"}:
            return "toggle", ("none", "enabled"), "此模型使用官方 thinking.type 开关，未声明独立可调强度。", True

    note = "尚未核实此模型的可调思考参数，使用服务商默认设置。"
    if vendor == "doubao":
        note = "方舟接入点 ID 无法可靠判断其背后模型；当前使用官方默认设置。"
    elif vendor == "moonshot" and model.startswith("kimi-k3"):
        note = "当前适配使用官方默认；此模型多轮对话还要求保留完整思考上下文，暂未开放强度调节。"
    return "default", (), note, False


def capabilities(provider: dict) -> dict:
    _, levels, note, _ = _profile(provider)
    return {
        "options": [{"value": level, "label": _LABELS.get(level, "最少（minimal）")} for level in ("default", *levels)],
        "note": note,
        "docs_url": _DOCS.get(_identity(provider)[0], ""),
    }


def request_options(provider: dict, effort: str = "default", max_tokens: int = 2048) -> dict:
    """Return HTTP JSON body fields. No SDK-only extra_body wrapper is used."""
    style, levels, _, default_thinking = _profile(provider)
    if not isinstance(effort, str) or effort not in ("default", *levels):
        raise HTTPException(400, "当前模型不支持所选思考强度，请刷新页面后重新选择。")
    result: dict = {}
    if effort != "default":
        if style in {"deepseek", "glm"}:
            result.update(thinking={"type": "disabled" if effort == "none" else "enabled"}, reasoning_effort=effort)
        elif style == "claude":
            result["thinking"] = {"type": "disabled" if effort == "none" else "adaptive"}
            if effort != "none":
                result["output_config"] = {"effort": effort}
        elif style == "qwen":
            result["enable_thinking"] = effort != "none"
        elif style == "toggle":
            result["thinking"] = {"type": "disabled" if effort == "none" else "enabled"}
        else:
            result["reasoning_effort"] = effort
    thinking = default_thinking if effort == "default" else effort != "none"
    budget = max(max_tokens, 32768 if thinking else max_tokens)
    if style == "openai":
        # max_tokens is not accepted by several reasoning models. The caller
        # must remove it when merging this replacement field into a body.
        result["max_completion_tokens"] = budget
    elif thinking and style != "qwen":
        result["max_tokens"] = budget
    return result


def image_supported(provider: dict) -> bool | None:
    """Only known negative results block images; True never grants consent."""
    vendor, model = _identity(provider)
    if vendor == "deepseek":
        if model == "deepseek-flash":
            return True
        if model == "deepseek-v4-pro":
            return False
    return None
