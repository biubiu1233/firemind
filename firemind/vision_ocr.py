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

PROMPT_CROSSHAIR = """WARDOGS 战术地图截图（卫星图 + 白色十字准星）。
十字准星旁有白色小字坐标，常见布局：y 在十字上方或下方（如 y31.76），x 在十字左侧或右侧（如 x84.71），必须读成一对。
只读十字准星中心对应的那一组 x、y（约 0~163.84，可小数）。不要读聊天、单位列表、其它标记。
只输出 JSON：
{"points":[{"x":84.71,"y":31.76,"label":"unknown"}]}"""


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
    if not pick:
        out["error"] = "no_coordinates_detected"
        out["model_preview"] = (text or "")[:280]
    return out
