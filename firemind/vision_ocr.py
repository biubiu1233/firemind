"""截图识坐标 — 读取地图白字 x/y 标签。"""

from __future__ import annotations

import base64
import json
import os
import re
import urllib.error
import urllib.request
from typing import Any

from .coords import extract_coord_pairs, extract_map_xy_labels

PROMPT_CROSSHAIR = """WARDOGS 战术地图局部截图（灰度地形 + 白色网格线）。

准星附近常有白色标签，格式为：字母 x 或 y 后面紧跟数字（可有小数），例如 y12.34 与 x56.78 分两行，或一行 x56.78 y12.34。y 常在 x 上方。
规则：x 后面的数字 → JSON 的 x；y 后面的数字 → JSON 的 y（与屏幕上谁先谁后无关）。数值范围约 0~163.84，以截图里实际读数为准，勿套用任何示例数字。

只读当前准星处这一组 x/y 白字；不要读聊天、弹药、其它标记。
看不清或没有白字时：{"points":[]}

只输出 JSON：
{"points":[{"x":<x后数字>,"y":<y后数字>,"label":"unknown"}]}
label 可选 gun|target|impact|unknown。"""

PROMPT_CHAT = """WARDOGS 截图：从聊天/输入框读取地图坐标（右键标记后常见）。
格式如 x95.82, y62.85 或 📍 x95.80, y62.86（0~163.84，可小数）。
只取聊天里最新完整一组 x,y，不要读地图十字、不要读其它 UI 数字。
只输出 JSON：{"points":[{"x":95.82,"y":62.85,"label":"unknown"}]}
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


def _point_dict(x: float, y: float, label: str = "unknown") -> dict[str, Any]:
    return {"x": x, "y": y, "label": label}


def _pick_from_map_text(text: str) -> dict[str, Any] | None:
    """地图模式：从模型原文里匹配 x<number> 与 y<number>。"""
    pt = extract_map_xy_labels(text)
    if pt:
        return _point_dict(pt.x, pt.y)
    pairs = extract_coord_pairs(text)
    if pairs:
        p = pairs[-1][0]
        return _point_dict(p.x, p.y)
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
    chat_mode = capture_mode in ("chat_coords", "chat")
    if chat_mode:
        prompt = PROMPT_CHAT + (" " + hint if hint else "")
    else:
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
            "temperature": 0,
            "max_tokens": 256,
        }
    ).encode()
    req = urllib.request.Request(
        f"{base}/chat/completions",
        data=payload,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")[:200]
        return {"error": f"vision API HTTP {e.code}: {detail}"}
    except TimeoutError:
        return {"error": "vision API 超时（截图过大或网络慢，请 F12 隐藏小窗后再试）"}
    except OSError as e:
        return {"error": f"vision API 连接中断: {e}"}
    except urllib.error.URLError as e:
        return {"error": f"vision API failed: {e}"}
    try:
        body = json.loads(raw)
    except json.JSONDecodeError as e:
        return {"error": f"vision API 返回非 JSON: {e}"}
    try:
        text = body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        err = body.get("error") if isinstance(body, dict) else None
        if isinstance(err, dict):
            msg = err.get("message") or err.get("code") or str(err)
        else:
            msg = str(body)[:200]
        return {"error": f"vision API bad response: {msg}"}

    parsed = _parse_json_content(text)
    json_points: list[dict[str, Any]] = []
    if parsed and isinstance(parsed.get("points"), list):
        for raw_pt in parsed["points"]:
            if not isinstance(raw_pt, dict):
                continue
            try:
                x, y = float(raw_pt["x"]), float(raw_pt["y"])
            except (KeyError, TypeError, ValueError):
                continue
            if not (0 <= x <= 164 and 0 <= y <= 164):
                continue
            json_points.append(
                {"x": x, "y": y, "label": raw_pt.get("label") or "unknown"}
            )

    pick: dict[str, Any] | None = None
    if chat_mode:
        if json_points:
            pick = json_points[-1]
        else:
            pairs = extract_coord_pairs(text)
            if pairs:
                pt = pairs[-1][0]
                pick = _point_dict(pt.x, pt.y)
    else:
        label_pick = _pick_from_map_text(text)
        if label_pick:
            pick = label_pick
        elif json_points:
            pick = json_points[0]

    out: dict[str, Any] = {
        "points": [pick] if pick else [],
        "ocr_text": text[:2000],
        "ocr_model": model,
    }
    if pick and role in ("gun", "target", "impact"):
        out[role] = pick
    if not pick and not chat_mode:
        out["parse_hint"] = "未找到 x/y 白字；请 F12 藏窗、放大地图、十字对准白字再按"
    return out
