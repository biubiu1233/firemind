"""两发试射登记 — 检查点 + 合并修正。"""

from __future__ import annotations

import math
from typing import Any

from .ballistics import BallisticsEngine, FiringSolution, Point
from .correction import (
    CorrectionResult,
    MissAnalysis,
    _aim_point_from_impact,
    _compute_solution_to_point,
    _current_mil,
    _lateral_azimuth_consistent,
    compute_miss_from_coords,
    suggest_correction_from_miss,
)


def _azimuth_deg(gun: Point, target: Point) -> float:
    dx, dy = target.x - gun.x, target.y - gun.y
    az = math.degrees(math.atan2(dx, dy))
    return az + 360 if az < 0 else az


def _point_at_az(gun: Point, az_deg: float, dist_world: float) -> Point:
    az = math.radians(az_deg)
    return Point(gun.x + math.sin(az) * dist_world, gun.y + math.cos(az) * dist_world)


def compute_check_point(gun: Point, target: Point, offset_deg: float = 90.0) -> dict[str, Any]:
    offset_deg = max(30.0, min(150.0, float(offset_deg)))
    dist_w = math.hypot(target.x - gun.x, target.y - gun.y)
    az_target = _azimuth_deg(gun, target)
    az_check = (az_target + offset_deg) % 360.0
    cp = _point_at_az(gun, az_check, dist_w)
    return {
        "x": round(cp.x, 2),
        "y": round(cp.y, 2),
        "offset_deg": offset_deg,
        "instruction": (
            f"①按诸元打目标，F3 登记落点。"
            f"②方位转 +{offset_deg:.0f}°，RNG/MIL 不变，十字对准检查点 x{cp.x:.2f} y{cp.y:.2f} 试射，F4 登记。"
        ),
    }


def merge_dual_impact_correction(
    solution: FiringSolution,
    impact1: Point,
    impact2: Point,
    engine: BallisticsEngine,
    arc: str | None = None,
    offset_deg: float = 90.0,
):
    cp_info = compute_check_point(solution.origin, solution.target, offset_deg)
    check = Point(cp_info["x"], cp_info["y"])
    a1 = compute_miss_from_coords(solution.origin, solution.target, impact1)
    a2 = compute_miss_from_coords(solution.origin, check, impact2)
    miss = MissAnalysis(
        dx_m=(a1.dx_m + a2.dx_m) / 2,
        dy_m=(a1.dy_m + a2.dy_m) / 2,
        lateral_m=(a1.lateral_m + a2.lateral_m) / 2,
        range_m=(a1.range_m + a2.range_m) / 2,
        map_summary=f"①{a1.map_summary} ②{a2.map_summary}",
        line_summary=f"合并 {a1.line_summary}；{a2.line_summary}",
    )
    use_arc = (
        arc
        or solution.effective_arc
        or solution.preferred_arc
        or ("high" if solution.elevation_low is None else "low")
    )
    base = suggest_correction_from_miss(
        solution, miss.lateral_m, miss.range_m, engine, arc, miss_analysis=miss
    )
    aim1 = _aim_point_from_impact(solution.target, impact1)
    aim2 = _aim_point_from_impact(check, impact2)
    aim = Point((aim1.x + aim2.x) / 2, (aim1.y + aim2.y) / 2)
    new_sol = _compute_solution_to_point(engine, solution, aim, use_arc)
    old_mil = _current_mil(solution, use_arc) or solution.mil_dial
    new_az = base.new_azimuth_deg if abs(miss.lateral_m) >= 1 else solution.azimuth_deg
    new_rng = base.new_rng_m
    new_mil = base.new_mil

    if new_sol and new_sol.sight_rng_m is not None and new_sol.mil_dial is not None:
        if _lateral_azimuth_consistent(
            miss.lateral_m, solution.azimuth_deg, new_sol.azimuth_deg
        ):
            new_az = new_sol.azimuth_deg
        new_rng = new_sol.sight_rng_m
        new_mil = float(new_sol.mil_dial)

    steps = [
        f"检查点 x{check.x} y{check.y}",
        miss.map_summary,
        miss.line_summary,
        f"合并瞄准 x{aim.x:.2f} y{aim.y:.2f}",
    ]
    mil_delta = (new_mil - old_mil) if old_mil is not None and new_mil is not None else None
    corr = CorrectionResult(
        lateral_m=miss.lateral_m,
        range_m=miss.range_m,
        new_azimuth_deg=new_az if abs(miss.lateral_m) >= 1 else None,
        mil_delta=mil_delta,
        new_mil=new_mil,
        new_rng_m=new_rng,
        arc=use_arc,
        explanation="两发合并（射表重算）",
        steps=steps,
        miss_analysis=miss,
    )
    return corr, cp_info
