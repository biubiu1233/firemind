"""游戏坐标解析 — 避免 X/Y 跨组配对、炮位/目标顺序颠倒。"""

from __future__ import annotations

import re

from .ballistics import Point

_NUM = r"[+-]?\d+(?:[.,]\d+)?"


def _num(s: str) -> float:
    return float(s.replace(",", "."))


def extract_coord_pairs(text: str) -> list[tuple[Point, int, str]]:
    """
    返回 [(Point, char_index, snippet), ...] 按在文本中出现顺序。
    只接受成对的 (x,y)，禁止「第一个 x + 第一个 y」跨组配对。
    """
    raw = (text or "").strip()
    if not raw:
        return []

    out: list[tuple[Point, int, str]] = []
    seen: set[tuple[float, float]] = set()

    patterns = [
        re.compile(rf"x\s*[:=]?\s*({_NUM})\s*[,，\s]+\s*y\s*[:=]?\s*({_NUM})", re.I),
        re.compile(
            rf"(?:炮位|目标|迫击炮|攀枝花|我方|gun|target|origin)"
            rf"[:：\s]*({_NUM})\s*[,，]\s*({_NUM})",
            re.I,
        ),
    ]

    for pat in patterns:
        for m in pat.finditer(raw):
            x, y = _num(m.group(1)), _num(m.group(2))
            key = (round(x, 4), round(y, 4))
            if key in seen:
                continue
            seen.add(key)
            out.append((Point(x, y), m.start(), m.group(0)[:40]))

    out.sort(key=lambda t: t[1])
    return out


def _role_before(text: str, index: int) -> str | None:
    """只看当前坐标前、上一分隔符之后的片段，避免前面的「目标」污染后面的「炮位」。"""
    start = max(
        text.rfind("，", 0, index),
        text.rfind(",", 0, index),
        text.rfind(";", 0, index),
        text.rfind("；", 0, index),
        text.rfind("\n", 0, index),
        0,
    )
    window = text[start:index]
    if re.search(r"目标|靶|target|打击点|enemy", window, re.I):
        return "target"
    if re.search(r"炮位|我方|gun|origin|迫击炮位置|攀枝花位置", window, re.I):
        return "gun"
    if re.search(r"迫击炮|攀枝花|sph", window, re.I) and not re.search(r"目标", window, re.I):
        return "gun"
    return None


def resolve_gun_target(text: str) -> tuple[Point | None, Point | None, list[str]]:
    """从一段文本解析炮位与目标，返回 (gun, target, warnings)。"""
    warnings: list[str] = []
    pairs = extract_coord_pairs(text)
    if not pairs:
        return None, None, warnings

    gun: Point | None = None
    target: Point | None = None
    unlabeled: list[Point] = []

    for pt, idx, _ in pairs:
        role = _role_before(text, idx)
        if role == "gun":
            gun = pt
        elif role == "target":
            target = pt
        else:
            unlabeled.append(pt)

    if gun is None and target is None and len(unlabeled) >= 2:
        gun, target = unlabeled[0], unlabeled[1]
        warnings.append(
            "未标注炮位/目标，已按「先炮位、后目标」理解；若顺序反了请写明「炮位」「目标」。"
        )
    elif gun is None and target is None and len(unlabeled) == 1:
        return None, unlabeled[0], warnings
    else:
        if gun is None and len(unlabeled) == 1:
            gun = unlabeled[0]
        elif gun is None and len(unlabeled) >= 1 and target is not None:
            gun = unlabeled[0]
            if len(unlabeled) > 1:
                warnings.append("检测到多组坐标，请确认炮位/目标是否对应正确。")
        if target is None and len(unlabeled) >= 1:
            if gun is not None and unlabeled[0] != gun:
                target = unlabeled[0]
            elif len(unlabeled) >= 2:
                target = unlabeled[1] if gun == unlabeled[0] else unlabeled[0]

    return gun, target, warnings
