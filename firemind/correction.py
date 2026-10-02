"""试射修正 Copilot — 解析落点偏差并建议调整。"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from typing import Any

from .ballistics import METERS_PER_UNIT, BallisticsEngine, FiringSolution, Point


@dataclass
class MissAnalysis:
    """落点相对目标的偏差（地图坐标 + 炮线分解）。"""

    dx_m: float
    dy_m: float
    lateral_m: float
    range_m: float
    map_summary: str
    line_summary: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "dx_m": round(self.dx_m),
            "dy_m": round(self.dy_m),
            "lateral_m": round(self.lateral_m, 1),
            "range_m": round(self.range_m, 1),
            "map_summary": self.map_summary,
            "line_summary": self.line_summary,
        }


@dataclass
class CorrectionResult:
    lateral_m: float
    range_m: float
    new_azimuth_deg: float | None
    mil_delta: float | None
    new_mil: float | None
    new_rng_m: int | None
    arc: str
    explanation: str
    steps: list[str]
    miss_analysis: MissAnalysis | None = None
    altitude_hint: dict[str, Any] | None = None
    correction_pairs: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        out = {
            "lateral_m": round(self.lateral_m, 1),
            "range_m": round(self.range_m, 1),
            "new_azimuth_deg": round(self.new_azimuth_deg, 1) if self.new_azimuth_deg is not None else None,
            "mil_delta": round(self.mil_delta, 1) if self.mil_delta is not None else None,
            "new_mil": round(self.new_mil, 1) if self.new_mil is not None else None,
            "new_rng_m": self.new_rng_m,
            "arc": self.arc,
            "explanation": self.explanation,
            "steps": self.steps,
            "correction_pairs": self.correction_pairs,
        }
        if self.miss_analysis:
            out["miss_analysis"] = self.miss_analysis.to_dict()
        if self.altitude_hint:
            out["altitude_hint"] = self.altitude_hint
        return out


def estimate_target_altitude_hint(
    gun_alt: float,
    range_m: float,
    lateral_m: float,
    distance_m: float,
    weapon_id: str,
    arc: str,
) -> dict[str, Any] | None:
    """
    由炮位海拔 + 沿炮线落点偏差 **粗估** 目标点相对炮位的高度差。

    仅在一发试射、且偏差主要来自高度（侧向很小）时才有参考意义；
    不能替代落点对 RNG/MIL 的修正。
    """
    if not math.isfinite(gun_alt) or abs(range_m) < 5:
        return None
    if abs(lateral_m) > max(25.0, abs(range_m) * 0.45):
        return None

    # 偏近 (range_m>0) → 常见原因之一是目标比「平地假设」更高
    if weapon_id == "mortar":
        scale = max(distance_m, 132.0) / 900.0
    elif arc == "low":
        scale = max(distance_m, 780.0) / 1400.0
    else:
        scale = max(distance_m, 780.0) / 1100.0

    delta_z = range_m * scale
    if abs(delta_z) < 2:
        return None

    target_hint = gun_alt + delta_z
    direction = "高" if delta_z > 0 else "低"
    return {
        "gun_alt_m": round(gun_alt, 1),
        "delta_z_hint_m": round(delta_z, 1),
        "target_alt_hint_m": round(target_hint, 1),
        "confidence": "low",
        "note": (
            f"在「首发按平地算、侧向偏差不大」的假设下，目标可能比炮位{direction}约 "
            f"{abs(round(delta_z))} m（目标 ASL 粗估约 {round(target_hint)} m）。"
            "多发因素会混淆，请以 RNG/MIL 修正为准。"
        ),
    }


def _cardinal_label(delta_m: float, pos: str, neg: str) -> str:
    if abs(delta_m) < 1:
        return ""
    direction = pos if delta_m > 0 else neg
    return f"{direction}{abs(round(delta_m))}m"


def compute_miss_from_coords(
    gun: Point,
    target: Point,
    impact: Point,
    meters_per_unit: float = METERS_PER_UNIT,
) -> MissAnalysis:
    """
    地图：X 东、Y 北（与社区地图一致）。
    炮线：沿炮位→目标方向，range_m>0 偏短（打近），lateral_m>0 落点在炮线右侧。
    """
    dx_m = (impact.x - target.x) * meters_per_unit
    dy_m = (impact.y - target.y) * meters_per_unit

    gx = (target.x - gun.x) * meters_per_unit
    gy = (target.y - gun.y) * meters_per_unit
    length = math.hypot(gx, gy) or 1.0
    along = (gx / length, gy / length)
    right = (gy / length, -gx / length)

    ex, ey = dx_m, dy_m
    lateral_m = ex * right[0] + ey * right[1]
    range_m = -(ex * along[0] + ey * along[1])

    parts_map = [
        _cardinal_label(dx_m, "东", "西"),
        _cardinal_label(dy_m, "北", "南"),
    ]
    map_parts = [p for p in parts_map if p]
    if map_parts:
        map_summary = "落点相对目标：" + "、".join(map_parts)
    else:
        map_summary = "落点与目标几乎重合"

    line_parts = []
    if abs(lateral_m) >= 1:
        line_parts.append(f"沿炮线偏{'右' if lateral_m > 0 else '左'}{abs(round(lateral_m))}m")
    if abs(range_m) >= 1:
        line_parts.append(f"偏{'近' if range_m > 0 else '远'}{abs(round(range_m))}m")
    line_summary = "；".join(line_parts) if line_parts else "沿炮线无显著偏差"

    return MissAnalysis(
        dx_m=dx_m,
        dy_m=dy_m,
        lateral_m=lateral_m,
        range_m=range_m,
        map_summary=map_summary,
        line_summary=line_summary,
    )


LATERAL = re.compile(r"(偏左|偏右|左边|右边|左侧|右侧|左|右)\s*(\d+(?:\.\d+)?)?\s*(?:米|m)?", re.I)
RANGE_ERR = re.compile(r"(偏短|偏长|近了|远了|short|long)\s*(\d+(?:\.\d+)?)?\s*(?:米|m)?", re.I)
DIR_MAP = {
    "偏左": -1, "左边": -1, "左侧": -1, "左": -1,
    "偏右": 1, "右边": 1, "右侧": 1, "右": 1,
}


def parse_miss(text: str) -> tuple[float, float]:
    """返回 (lateral_m, range_m)：正 lateral=偏右，正 range=偏短（打近了）。"""
    lateral = 0.0
    range_err = 0.0

    for m in LATERAL.finditer(text):
        direction = m.group(1)
        dist = float(m.group(2)) if m.group(2) else 20.0
        sign = DIR_MAP.get(direction, 0)
        lateral += sign * dist

    for m in RANGE_ERR.finditer(text):
        kind = m.group(1).lower()
        dist = float(m.group(2)) if m.group(2) else 30.0
        if kind in ("偏短", "short", "近了"):
            range_err += dist
        else:
            range_err -= dist

    if lateral == 0 and "左" in text and "右" not in text:
        lateral = -20
    if lateral == 0 and "右" in text:
        lateral = 20
    if range_err == 0:
        if "短" in text or "近" in text:
            range_err = 30
        elif "长" in text or "远" in text:
            range_err = -30

    return lateral, range_err


def _current_mil(solution: FiringSolution, arc: str) -> float | None:
    if solution.mil_dial is not None:
        return float(solution.mil_dial)
    if solution.weapon_id == "mortar" and solution.elevation_single:
        return solution.elevation_single.mil or solution.elevation_single.min_mil
    if arc == "high" and solution.elevation_high:
        return solution.elevation_high.mil or solution.elevation_high.min_mil
    if arc == "low" and solution.elevation_low:
        return solution.elevation_low.mil or solution.elevation_low.min_mil
    sol = solution.elevation_low or solution.elevation_high or solution.elevation_single
    if sol is None:
        return None
    return sol.mil or sol.min_mil


def suggest_correction_from_miss(
    solution: FiringSolution,
    lateral_m: float,
    range_m: float,
    engine: BallisticsEngine,
    arc: str | None = None,
    miss_analysis: MissAnalysis | None = None,
) -> CorrectionResult:
    use_arc = (
        arc
        or solution.effective_arc
        or solution.preferred_arc
        or ("high" if solution.elevation_low is None else "low")
    )
    if solution.elevation_low and not solution.elevation_high:
        use_arc = "high"
    elif solution.elevation_high and not solution.elevation_low:
        use_arc = "low" if use_arc == "low" else "high"

    steps: list[str] = []
    if miss_analysis:
        steps.append(miss_analysis.map_summary)
        steps.append(miss_analysis.line_summary)

    new_az = solution.azimuth_deg
    new_rng: int | None = None
    base_rng = solution.sight_rng_m or round(solution.distance_m)

    if abs(lateral_m) >= 1:
        # 侧向偏差 → 修方位角（近似：atan2 小角度修正）
        bearing_rad = math.radians(solution.azimuth_deg)
        # 偏右需要向左修方位（减）；简化用距离比例估角度
        angle_fix = math.degrees(math.atan2(-lateral_m, max(solution.distance_m, 50)))
        new_az = (solution.azimuth_deg + angle_fix) % 360
        direction = "右" if lateral_m > 0 else "左"
        steps.append(f"落点偏{direction} {abs(lateral_m):.0f}m → 方位角 {solution.azimuth_deg:.1f}° 调整为 {new_az:.1f}°")

    current_mil = _current_mil(solution, use_arc)
    mil_delta = 0.0
    new_mil = current_mil

    if abs(range_m) >= 80:
        steps.append(
            "偏差很大（≥80m）：先确认游戏内【高/低弹道】与 FireMind「本次使用」一致；"
            "高弹道勿用 100 多 MIL，低弹道勿用 1100+ MIL。"
        )

    if abs(range_m) >= 1 and current_mil is not None:
        # 偏短 → 需要更远
        if solution.weapon_id == "mortar" or use_arc == "high":
            # 更远 → 降低 MIL
            mil_delta = -0.12 * range_m
        else:
            # SPH 低弹道：更远 → 提高 MIL
            mil_delta = 0.12 * range_m
        new_mil = current_mil + mil_delta
        new_rng = int(round(base_rng + range_m))
        if range_m > 0:
            steps.append(
                f"修正建议：打近了 → 左 RNG 约 {base_rng}→{new_rng} m；"
                f"右 MIL 约 {current_mil:.0f}→{new_mil:.0f}（Δ{mil_delta:+.0f}）"
            )
        else:
            steps.append(
                f"修正建议：打远了 → 左 RNG 约 {base_rng}→{new_rng} m；"
                f"右 MIL 约 {current_mil:.0f}→{new_mil:.0f}（Δ{mil_delta:+.0f}）"
            )

    alt_hint: dict[str, Any] | None = None
    if (
        solution.gun_alt is not None
        and solution.target_alt is None
        and abs(range_m) >= 5
    ):
        alt_hint = estimate_target_altitude_hint(
            solution.gun_alt,
            range_m,
            lateral_m,
            solution.distance_m,
            solution.weapon_id,
            use_arc,
        )
        if alt_hint:
            steps.append(f"高度粗估（仅供参考）：{alt_hint['note']}")

    if len(steps) <= (2 if miss_analysis else 0):
        steps.append("请粘贴落点坐标，或描述偏差")

    explanation = "；".join(steps)
    return CorrectionResult(
        lateral_m=lateral_m,
        range_m=range_m,
        new_azimuth_deg=new_az if abs(lateral_m) >= 1 else None,
        mil_delta=mil_delta if abs(range_m) >= 1 else None,
        new_mil=new_mil,
        new_rng_m=new_rng if abs(range_m) >= 1 else None,
        arc=use_arc,
        explanation=explanation,
        steps=steps,
        miss_analysis=miss_analysis,
        altitude_hint=alt_hint,
    )


def suggest_correction(
    solution: FiringSolution,
    miss_text: str,
    engine: BallisticsEngine,
    arc: str | None = None,
) -> CorrectionResult:
    lateral_m, range_m = parse_miss(miss_text)
    return suggest_correction_from_miss(
        solution, lateral_m, range_m, engine, arc, miss_analysis=None
    )


def _aim_point_from_impact(target: Point, impact: Point) -> Point:
    """与 wardogs 社区计算器一致：瞄准点 += (目标 − 落点)。"""
    return Point(
        target.x + (target.x - impact.x),
        target.y + (target.y - impact.y),
    )


def _point_at_az_range_m(origin: Point, azimuth_deg: float, range_m: float) -> Point:
    dist_world = range_m / METERS_PER_UNIT
    az = math.radians(azimuth_deg)
    return Point(
        origin.x + math.sin(az) * dist_world,
        origin.y + math.cos(az) * dist_world,
    )


def _azimuth_delta_deg(from_deg: float, to_deg: float) -> float:
    return ((to_deg - from_deg + 180) % 360) - 180


def _lateral_azimuth_consistent(lateral_m: float, old_az: float, new_az: float) -> bool:
    """落点偏左 → 方位应增大（小角），偏右 → 方位应减小；不一致时勿采用射表方位。"""
    if abs(lateral_m) < 1:
        return True
    delta = _azimuth_delta_deg(old_az, new_az)
    return delta * (-lateral_m) >= 0 or abs(delta) < 0.05


def _dial_pair_for_arc(solution: FiringSolution, arc: str) -> tuple[int | None, int | None]:
    for p in solution.dial_pairs or []:
        if p.get("arc") == arc and p.get("sight_rng_m") is not None:
            return int(p["sight_rng_m"]), int(p["mil"])
    if solution.effective_arc == arc:
        rng = solution.sight_rng_m
        mil = solution.mil_dial
        if rng is not None and mil is not None:
            return int(rng), int(mil)
    return None, None


def _recompute_rng_mil_for_arc(
    solution: FiringSolution,
    analysis: MissAnalysis,
    engine: BallisticsEngine,
    use_arc: str,
    base_az: float | None,
    impact: Point | None,
) -> tuple[float | None, int | None, float | None]:
    """按同一落点偏差，在指定弹道支上重算 RNG/MIL（SPH 双支展示用）。"""
    new_az = base_az if base_az is not None else solution.azimuth_deg
    rng0, mil0 = _dial_pair_for_arc(solution, use_arc)
    if rng0 is None:
        rng0 = solution.sight_rng_m or int(round(solution.distance_m))
    if mil0 is None:
        cm = _current_mil(solution, use_arc)
        mil0 = int(round(cm)) if cm is not None else None

    lat_only = abs(analysis.lateral_m) >= 1 and abs(analysis.range_m) < 10
    range_only = abs(analysis.range_m) >= 1 and abs(analysis.lateral_m) < 10
    combined = abs(analysis.lateral_m) >= 1 and abs(analysis.range_m) >= 1

    if lat_only:
        return new_az, rng0, float(mil0) if mil0 is not None else None

    if range_only:
        aim_pt = _point_at_az_range_m(
            solution.origin,
            new_az,
            solution.distance_m + analysis.range_m,
        )
        rs = _compute_solution_to_point(engine, solution, aim_pt, use_arc)
        if rs and rs.sight_rng_m is not None and rs.mil_dial is not None:
            return rs.azimuth_deg, rs.sight_rng_m, float(rs.mil_dial)
        return new_az, rng0, float(mil0) if mil0 is not None else None

    if combined and impact is not None:
        corrected = _aim_point_from_impact(solution.target, impact)
        rs = _compute_solution_to_point(engine, solution, corrected, use_arc)
        if rs and rs.sight_rng_m is not None and rs.mil_dial is not None:
            cand_az = rs.azimuth_deg
            if _lateral_azimuth_consistent(
                analysis.lateral_m, solution.azimuth_deg, cand_az
            ):
                new_az = cand_az
            return new_az, rs.sight_rng_m, float(rs.mil_dial)

    return new_az, rng0, float(mil0) if mil0 is not None else None


def _sph_correction_pairs(
    solution: FiringSolution,
    analysis: MissAnalysis,
    engine: BallisticsEngine,
    primary_arc: str,
    primary_az: float | None,
    impact: Point | None,
) -> list[dict[str, Any]]:
    if solution.weapon_id != "spg":
        return []
    pairs: list[dict[str, Any]] = []
    for arc in ("low", "high"):
        if arc == "low" and solution.elevation_low is None:
            continue
        if arc == "high" and solution.elevation_high is None:
            continue
        az, rng, mil = _recompute_rng_mil_for_arc(
            solution, analysis, engine, arc, primary_az, impact
        )
        if rng is None or mil is None:
            continue
        pairs.append({
            "arc": arc,
            "new_azimuth_deg": round(az, 1) if az is not None else None,
            "new_rng_m": int(rng),
            "new_mil": int(round(mil)),
            "recommended": arc == primary_arc,
        })
    return pairs


def _compute_solution_to_point(
    engine: BallisticsEngine,
    solution: FiringSolution,
    aim: Point,
    use_arc: str,
) -> FiringSolution | None:
    try:
        return engine.compute(
            solution.origin,
            aim,
            weapon_id=solution.weapon_id,
            preferred_arc=use_arc,
            gun_alt=solution.gun_alt,
            target_alt=solution.target_alt,
        )
    except (ValueError, TypeError):
        return None


def suggest_correction_from_impact(
    solution: FiringSolution,
    impact: Point,
    engine: BallisticsEngine,
    arc: str | None = None,
) -> CorrectionResult:
    analysis = compute_miss_from_coords(
        solution.origin,
        solution.target,
        impact,
    )
    max_ok = max(350.0, solution.distance_m * 0.35)
    if abs(analysis.lateral_m) > max_ok or abs(analysis.range_m) > max_ok:
        bad = CorrectionResult(
            lateral_m=analysis.lateral_m,
            range_m=analysis.range_m,
            new_azimuth_deg=None,
            mil_delta=None,
            new_mil=None,
            new_rng_m=None,
            arc=solution.effective_arc or "low",
            explanation=(
                f"落点偏差异常（侧{abs(round(analysis.lateral_m))}m/距{abs(round(analysis.range_m))}m），"
                "多半是 OCR 读错坐标。请用聊天框📍坐标或重新 F3；勿照此修正。"
            ),
            steps=[analysis.map_summary, analysis.line_summary, "已拒绝离谱修正"],
            miss_analysis=analysis,
            correction_pairs=[],
        )
        return bad
    use_arc = (
        arc
        or solution.effective_arc
        or solution.preferred_arc
        or ("high" if solution.elevation_low is None else "low")
    )
    base = suggest_correction_from_miss(
        solution,
        analysis.lateral_m,
        analysis.range_m,
        engine,
        arc,
        miss_analysis=analysis,
    )
    old_mil = _current_mil(solution, use_arc) or solution.mil_dial
    new_az = base.new_azimuth_deg if abs(analysis.lateral_m) >= 1 else solution.azimuth_deg
    new_rng = base.new_rng_m
    new_mil = base.new_mil
    steps = list(base.steps)
    lat_only = abs(analysis.lateral_m) >= 1 and abs(analysis.range_m) < 10
    range_only = abs(analysis.range_m) >= 1 and abs(analysis.lateral_m) < 10
    combined = abs(analysis.lateral_m) >= 1 and abs(analysis.range_m) >= 1

    if lat_only:
        # 纯侧向：只转方位，左/右 RNG·MIL 保持首发成对读数（社区试射习惯）
        new_rng = solution.sight_rng_m
        new_mil = float(solution.mil_dial) if solution.mil_dial is not None else new_mil
        steps.append(
            f"侧向修正：方位 {solution.azimuth_deg:.1f}° → {new_az:.1f}°；"
            f"RNG/MIL 仍用首发 {new_rng}/{round(new_mil) if new_mil else '—'}"
        )
    elif range_only:
        aim_pt = _point_at_az_range_m(
            solution.origin,
            new_az or solution.azimuth_deg,
            solution.distance_m + analysis.range_m,
        )
        rs = _compute_solution_to_point(engine, solution, aim_pt, use_arc)
        if rs and rs.sight_rng_m is not None and rs.mil_dial is not None:
            new_az = rs.azimuth_deg
            new_rng = rs.sight_rng_m
            new_mil = float(rs.mil_dial)
            steps.append(
                f"距离修正（射表重算）：RNG {solution.sight_rng_m}→{new_rng}，"
                f"MIL {round(old_mil or 0)}→{round(new_mil)}"
            )
    elif combined:
        corrected = _aim_point_from_impact(solution.target, impact)
        rs = _compute_solution_to_point(engine, solution, corrected, use_arc)
        if rs and rs.sight_rng_m is not None and rs.mil_dial is not None:
            cand_az = rs.azimuth_deg
            if _lateral_azimuth_consistent(
                analysis.lateral_m, solution.azimuth_deg, cand_az
            ):
                new_az = cand_az
            elif new_az is not None:
                steps.append(
                    "合并修正：方位采用沿炮线小角修正（与地图落点读数一致）"
                )
            new_rng = rs.sight_rng_m
            new_mil = float(rs.mil_dial)
            steps.append(
                f"合并瞄准 x{corrected.x:.2f} y{corrected.y:.2f}（射表重算 RNG/MIL）"
            )

    mil_delta = None
    if old_mil is not None and new_mil is not None and abs(analysis.range_m) >= 1:
        mil_delta = new_mil - old_mil

    explanation = "；".join(steps)
    corr_pairs = _sph_correction_pairs(
        solution, analysis, engine, use_arc, new_az, impact
    )
    return CorrectionResult(
        lateral_m=analysis.lateral_m,
        range_m=analysis.range_m,
        new_azimuth_deg=new_az if abs(analysis.lateral_m) >= 1 else None,
        mil_delta=mil_delta,
        new_mil=new_mil if abs(analysis.range_m) >= 1 else None,
        new_rng_m=new_rng if abs(analysis.range_m) >= 1 else None,
        arc=use_arc,
        explanation=explanation,
        steps=steps,
        miss_analysis=analysis,
        altitude_hint=base.altitude_hint,
        correction_pairs=corr_pairs,
    )


def recompute_after_correction(
    solution: FiringSolution,
    correction: CorrectionResult,
    engine: BallisticsEngine,
) -> FiringSolution | None:
    """根据修正后的方位/虚拟目标点重新计算（可选二次验证）。"""
    if correction.new_azimuth_deg is None and correction.new_mil is None:
        return None

    dist_world = solution.distance_m / 100
    az_rad = math.radians(correction.new_azimuth_deg or solution.azimuth_deg)
    ox, oy = solution.origin.x, solution.origin.y
    tx = ox + math.sin(az_rad) * dist_world
    ty = oy + math.cos(az_rad) * dist_world

    if abs(correction.range_m) >= 1:
        range_world = correction.range_m / 100
        tx += math.sin(az_rad) * range_world
        ty += math.cos(az_rad) * range_world

    return engine.compute(
        Point(ox, oy),
        Point(tx, ty),
        weapon_id=solution.weapon_id,
        preferred_arc=correction.arc,
        gun_alt=solution.gun_alt,
        target_alt=solution.target_alt,
    )
