"""自然语言 / 聊天坐标解析 — 规则引擎 + 可选 LLM 增强。"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from .ballistics import Point
from .coords import resolve_gun_target


@dataclass
class ParsedMission:
    gun: Point | None = None
    target: Point | None = None
    weapon_id: str | None = None
    preferred_arc: str | None = None
    gun_alt: float | None = None
    target_alt: float | None = None
    missing: list[str] = field(default_factory=list)
    parse_warnings: list[str] = field(default_factory=list)
    confidence: str = "high"
    source: str = "rules"

    def merge(self, other: ParsedMission) -> ParsedMission:
        return ParsedMission(
            gun=other.gun or self.gun,
            target=other.target or self.target,
            weapon_id=other.weapon_id or self.weapon_id,
            preferred_arc=other.preferred_arc or self.preferred_arc,
            gun_alt=other.gun_alt if other.gun_alt is not None else self.gun_alt,
            target_alt=other.target_alt if other.target_alt is not None else self.target_alt,
            missing=other.missing or self.missing,
            parse_warnings=list(dict.fromkeys([*self.parse_warnings, *other.parse_warnings])),
            confidence=other.confidence,
            source=other.source,
        )


MORTAR_KW = re.compile(r"迫击炮|l81|mortar|81mm|摩迫", re.I)
SPG_KW = re.compile(r"攀枝花|sph|自行火炮|155|spg|pzh", re.I)
HIGH_ARC = re.compile(r"高弹道|高弧|high\s*arc|越过|山脊|建筑后面", re.I)
LOW_ARC = re.compile(r"低弹道|低弧|low\s*arc|平射|快打", re.I)
ALT_GUN = re.compile(r"炮位.*?海拔\s*([+-]?\d+(?:\.\d+)?)|gun.*?alt.*?([+-]?\d+(?:\.\d+)?)", re.I)
ALT_TGT = re.compile(r"目标.*?海拔\s*([+-]?\d+(?:\.\d+)?)|target.*?alt.*?([+-]?\d+(?:\.\d+)?)", re.I)


def parse_mission_rules(text: str, context: ParsedMission | None = None) -> ParsedMission:
    ctx = context or ParsedMission()
    gun, target, parse_warnings = resolve_gun_target(text)

    gun = gun or ctx.gun
    target = target or ctx.target
    parse_warnings = list(dict.fromkeys([*ctx.parse_warnings, *parse_warnings]))

    weapon_id = ctx.weapon_id
    if MORTAR_KW.search(text):
        weapon_id = "mortar"
    elif SPG_KW.search(text):
        weapon_id = "spg"

    preferred_arc = ctx.preferred_arc
    if HIGH_ARC.search(text):
        preferred_arc = "high"
    elif LOW_ARC.search(text):
        preferred_arc = "low"

    gun_alt = ctx.gun_alt
    target_alt = ctx.target_alt
    m = ALT_GUN.search(text)
    if m:
        gun_alt = float(next(g for g in m.groups() if g))
    m = ALT_TGT.search(text)
    if m:
        target_alt = float(next(g for g in m.groups() if g))

    missing = []
    if gun is None:
        missing.append("gun")
    if target is None:
        missing.append("target")

    return ParsedMission(
        gun=gun,
        target=target,
        weapon_id=weapon_id or "mortar",
        preferred_arc=preferred_arc,
        gun_alt=gun_alt,
        target_alt=target_alt,
        missing=missing,
        parse_warnings=parse_warnings,
        confidence="high" if not missing else "partial",
        source="rules",
    )


def build_clarification(mission: ParsedMission) -> str:
    if not mission.missing:
        return ""
    parts = []
    if "gun" in mission.missing:
        parts.append("炮位坐标（右键地图 → 标记坐标，格式 x98.43 y110.38）")
    if "target" in mission.missing:
        parts.append("目标坐标")
    return "还需要：" + "、".join(parts)


def parse_llm_json(raw: str) -> dict[str, Any] | None:
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        m = re.search(r"\{[\s\S]*\}", raw)
        if m:
            try:
                return json.loads(m.group(0))
            except json.JSONDecodeError:
                return None
    return None


def mission_from_llm(data: dict[str, Any]) -> ParsedMission:
    def pt(obj: Any) -> Point | None:
        if not isinstance(obj, dict):
            return None
        x, y = obj.get("x"), obj.get("y")
        if x is None or y is None:
            return None
        return Point(float(x), float(y))

    weapon = data.get("weapon_id") or data.get("weapon")
    if weapon in ("mortar", "l81", "迫击炮"):
        weapon = "mortar"
    elif weapon in ("spg", "sph", "sph-2", "攀枝花"):
        weapon = "spg"

    arc = data.get("preferred_arc") or data.get("arc")
    if arc in ("高", "high", "高弹道"):
        arc = "high"
    elif arc in ("低", "low", "低弹道"):
        arc = "low"

    gun = pt(data.get("gun") or data.get("origin"))
    target = pt(data.get("target"))
    missing = [k for k, v in [("gun", gun), ("target", target)] if v is None]

    return ParsedMission(
        gun=gun,
        target=target,
        weapon_id=weapon or "mortar",
        preferred_arc=arc,
        gun_alt=data.get("gun_alt"),
        target_alt=data.get("target_alt"),
        missing=missing,
        confidence="high" if not missing else "partial",
        source="llm",
    )
