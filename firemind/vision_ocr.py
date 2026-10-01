"""截图识坐标 — 自动找地图十字准星旁的 x/y。"""

from __future__ import annotations

import base64
import json
import os
import re
import urllib.error
import urllib.request
from typing import Any

from .coords import extract_coord_pairs

PROMPT_CROSSHAIR = """WARDOGS 战术地图截图。地图视图里有十字准星（+ 或瞄准十字），十字附近常有白色标签：x93.53 与 y36.54（可能在十字左右或上下）。
你的任务：找到十字准星所指向的那一组 x、y 游戏坐标（范围约 0~163.84，可带小数）。
不要读聊天、列表、其它标记点的坐标；只要十字准星这一点。
只输出 JSON，不要其它文字：
{"points":[{"x":93.53,"y":36.54,"label":"unknown"}]}
label 可选 gun|target|impact|unknown。"""


def ocr_available() -> bool:
    return bool(
        os.environ.get("FIREMIND_OCR_API_KEY")
        or os.environ.get("FIREMIND_API_KEY")
        or os.environ.get("OPENAI_API_KEY")
    )


def _cfg() -> tuple[str, str, str]:
    key = (
        os.environ.get("FIREMIND_OCR_API_KEY")
        or os.environ.get("FIREMIND_API_KEY")
        or os.environ.get("OPENAI_API_KEY", "")
    )
    base = (
        os.environ.get("FIREMIND_OCR_API_BASE")
        or os.environ.get("FIREMIND_API_BASE")
        or "https://api.deepseek.com/v1"
    ).rstrip("/")
    model = os.environ.get("FIREMIND_OCR_MODEL", "deepseek-v4-flash-vision-exp")
    return key, base, model


def _parse_json_content(text: str) -> dict | None:
    text = text.strip()
    text = re.sub(r"^```\w*\s*|\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{[\s\S]*\}", text)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                return None
    return None


def ocr_map_image(
    image_bytes: bytes,
    mime: str = "image/png",
    role: str | None = None,
    capture_mode: str | None = None,
) -> dict[str, Any]:
    if not ocr_available():
        return {"error": "OCR key not configured"}
    key, base, model = _cfg()
    b64 = base64.standard_b64encode(image_bytes).decode()
    hint = {"gun": "这是炮位步骤，label=gun。", "target": "这是目标步骤，label=target。", "impact": "这是落点步骤，label=impact。"}.get(
        role or "", ""
    )
    prompt = PROMPT_CROSSHAIR + (" " + hint if hint else "")
    payload = json.dumps(
        {
            "model": model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
                    ],
                }
            ],
            "temperature": 0.05,
            "max_tokens": 800,
        }
    ).encode()
    req = urllib.request.Request(
        f"{base}/chat/completions",
        data=payload,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            text = json.loads(resp.read())["choices"][0]["message"]["content"]
    except (urllib.error.URLError, KeyError, json.JSONDecodeError, TimeoutError):
        return {"error": "vision API failed"}
    parsed = _parse_json_content(text)
    points: list[dict[str, Any]] = []
    if parsed and isinstance(parsed.get("points"), list):
        for raw in parsed["points"]:
            if not isinstance(raw, dict):
                continue
            try:
                x, y = float(raw["x"]), float(raw["y"])
            except (KeyError, TypeError, ValueError):
                continue
            if not (0 <= x <= 164 and 0 <= y <= 164):
                continue
            points.append({"x": x, "y": y, "label": raw.get("label") or "unknown"})
    out: dict[str, Any] = {"points": points}
    pick = points[0] if points else None
    if not pick:
        pairs = extract_coord_pairs(text)
        if pairs:
            pick = {"x": pairs[0][0].x, "y": pairs[0][0].y, "label": "unknown"}
            out["points"] = [pick]
    if pick and role in ("gun", "target", "impact"):
        out[role] = pick
    return out
