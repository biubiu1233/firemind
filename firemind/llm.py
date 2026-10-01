"""可选 LLM 层 — OpenAI 兼容 API，无 Key 时自动降级为规则引擎。"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any

from .parser import mission_from_llm, parse_llm_json

SYSTEM_PROMPT = """你是 FireMind，WARDOGS 游戏的智能炮兵副官。
从用户消息中提取结构化射击任务，只输出 JSON，不要其他文字。

JSON 格式：
{
  "gun": {"x": 98.43, "y": 110.38} 或 null,
  "target": {"x": 94.53, "y": 109.03} 或 null,
  "weapon_id": "mortar" 或 "spg",
  "preferred_arc": "low" | "high" | null,
  "gun_alt": 数字或null,
  "target_alt": 数字或null,
  "intent": "fire_mission" | "correction" | "question" | "clarify",
  "reply": "给用户的简短中文回复（一句话）"
}

规则：
- 迫击炮/L81/81mm → weapon_id=mortar
- 攀枝花/SPH-2/155mm自行火炮 → weapon_id=spg
- 坐标格式 x98.43 y110.38 或 98.43, 110.38
- 用户描述试射偏差（偏短/偏长/偏左/偏右）→ intent=correction
- 缺少坐标时 intent=clarify，reply 说明缺什么
"""


def llm_available() -> bool:
    return bool(os.environ.get("OPENAI_API_KEY") or os.environ.get("FIREMIND_API_KEY"))


def _api_config() -> tuple[str, str, str]:
    key = os.environ.get("FIREMIND_API_KEY") or os.environ.get("OPENAI_API_KEY", "")
    base = os.environ.get("FIREMIND_API_BASE") or os.environ.get("OPENAI_API_BASE", "https://api.openai.com/v1")
    model = os.environ.get("FIREMIND_MODEL", "gpt-4o-mini")
    return key, base.rstrip("/"), model


def chat_completion(messages: list[dict[str, str]]) -> str | None:
    key, base, model = _api_config()
    if not key:
        return None

    payload = json.dumps({
        "model": model,
        "messages": messages,
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
    }).encode("utf-8")

    req = urllib.request.Request(
        f"{base}/chat/completions",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {key}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            return body["choices"][0]["message"]["content"]
    except (urllib.error.URLError, KeyError, json.JSONDecodeError, TimeoutError):
        return None


def chat_completion_plain(messages: list[dict[str, str]], temperature: float = 0.4) -> str | None:
    key, base, model = _api_config()
    if not key:
        return None

    payload = json.dumps({
        "model": model,
        "messages": messages,
        "temperature": temperature,
    }).encode("utf-8")

    req = urllib.request.Request(
        f"{base}/chat/completions",
        data=payload,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {key}",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            return body["choices"][0]["message"]["content"].strip()
    except (urllib.error.URLError, KeyError, json.JSONDecodeError, TimeoutError):
        return None


def _briefing_payload(solution: dict[str, Any]) -> dict[str, Any]:
    """LLM 只看本次有效弹道，避免写出「建议低弹道 + 120 MIL」等混用文案。"""
    payload = dict(solution)
    if payload.get("weapon_id") != "spg":
        return payload
    eff = payload.get("effective_arc")
    if eff not in ("low", "high"):
        return payload
    pairs = payload.get("dial_pairs") or []
    payload["dial_pairs"] = [p for p in pairs if p.get("arc") == eff]
    elev = payload.get("elevation") or {}
    payload["elevation"] = {eff: elev.get(eff)}
    payload["_briefing_rule"] = (
        f"只写{('高' if eff == 'high' else '低')}弹道；"
        f"队频口令必须原文复制 voice_callout；"
        "禁止出现另一弹道或「建议低弹道」字样（当 effective_arc 为 high 时）。"
    )
    return payload


def generate_briefing(solution: dict[str, Any], fallback: str) -> tuple[str, str]:
    """返回 (brief_text, engine)。"""
    if not llm_available():
        return fallback, "rules"

    system = (
        "你是 FireMind，WARDOGS 炮兵副官。根据射击诸元 JSON 写简短中文步骤。"
        "只能使用 JSON 已有数字，不要编造。"
        "必须遵守 _briefing_rule（若有）。"
        "SPH-2：仅 effective_arc 对应弹道；voice_callout 原文作为队频口令。"
        "effective_arc=high 时禁止写低弹道或 MIL≤600。"
        "控制在 120 字以内。"
    )
    user = json.dumps(_briefing_payload(solution), ensure_ascii=False)
    text = chat_completion_plain(
        [{"role": "system", "content": system}, {"role": "user", "content": user}],
    )
    if text:
        return text, "llm"
    return fallback, "rules"


def parse_with_llm(user_text: str, history: list[dict[str, str]] | None = None) -> dict[str, Any] | None:
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    for h in history or []:
        if h.get("role") in ("user", "assistant"):
            messages.append({"role": h["role"], "content": h.get("content", "")})
    messages.append({"role": "user", "content": user_text})

    raw = chat_completion(messages)
    if not raw:
        return None
    data = parse_llm_json(raw)
    if not data:
        return None
    data["_mission"] = mission_from_llm(data)
    return data
