"""WARDOGS 弹道引擎 — 规则层，与 LLM 解耦。"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

METERS_PER_UNIT = 100

WEAPON_META: dict[str, dict[str, Any]] = {
    "mortar": {
        "name": "L81 迫击炮",
        "min_range_m": 132,
        "max_range_m": 684,
        "dead_zone_m": 50,
    },
    "spg": {
        "name": "SPH-2 攀枝花（155mm 自行火炮）",
        "min_range_m": 780,
        "max_range_m": 2629,
        "dead_zone_m": 220,
        # 射表 low 首行 1181m，但社区/实测平地低弹道稳定可用约从 1283m 起
        "low_arc_min_m": 1181,
        "low_arc_practical_min_m": 1283,
        "auto_prefer_high_until_m": 1750,
        "flat_arc_min_m": 490,
    },
}


@dataclass
class Point:
    x: float
    y: float


@dataclass
class MilSolution:
    mil: float | None = None
    min_mil: float | None = None
    max_mil: float | None = None

    def formatted(self) -> str | None:
        if self.min_mil is None:
            return None
        lo = round(self.min_mil)
        hi = round(self.max_mil or self.min_mil)
        if lo != hi:
            return f"{lo}–{hi}"
        return str(round(self.mil or lo))


@dataclass
class FiringSolution:
    weapon_id: str
    weapon_name: str
    origin: Point
    target: Point
    distance_m: float
    distance_km: float
    azimuth_deg: float
    azimuth_mil: float
    dx_m: float
    dy_m: float
    elevation_single: MilSolution | None = None
    elevation_low: MilSolution | None = None
    elevation_high: MilSolution | None = None
    preferred_arc: str | None = None
    effective_arc: str | None = None
    gun_alt: float | None = None
    target_alt: float | None = None
    delta_z: float | None = None
    warnings: list[str] = field(default_factory=list)
    parse_warnings: list[str] = field(default_factory=list)
    terrain_note: str | None = None
    correction_hint: str | None = None
    aiming_note: str | None = None
    arc_mode_hint: str | None = None
    sight_rng_m: int | None = None
    mil_dial: int | None = None
    dial_pairs: list[dict[str, Any]] = field(default_factory=list)
    sight_rng_flat_m: int | None = None
    mil_dial_flat: int | None = None
    correction_layers: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "weapon_id": self.weapon_id,
            "weapon_name": self.weapon_name,
            "origin": {"x": self.origin.x, "y": self.origin.y},
            "target": {"x": self.target.x, "y": self.target.y},
            "distance_m": round(self.distance_m),
            "distance_km": round(self.distance_km, 3),
            "azimuth_deg": round(self.azimuth_deg, 1),
            "azimuth_mil": round(self.azimuth_mil),
            "dx_m": round(self.dx_m),
            "dy_m": round(self.dy_m),
            "elevation": {
                "single": self.elevation_single.formatted() if self.elevation_single else None,
                "low": self.elevation_low.formatted() if self.elevation_low else None,
                "high": self.elevation_high.formatted() if self.elevation_high else None,
            },
            "preferred_arc": self.preferred_arc,
            "effective_arc": self.effective_arc,
            "recommended_mil": self.recommended_mil(),
            "sight_rng_m": self.sight_rng_m,
            "mil_dial": self.mil_dial,
            "dial_pairs": self.dial_pairs,
            "gun_alt": self.gun_alt,
            "target_alt": self.target_alt,
            "delta_z": round(self.delta_z, 1) if self.delta_z is not None else None,
            "warnings": self.warnings,
            "parse_warnings": self.parse_warnings,
            "terrain_note": self.terrain_note,
            "correction_hint": self.correction_hint,
            "aiming_note": self.aiming_note,
            "arc_mode_hint": self.arc_mode_hint,
            "voice_callout": self.voice_callout(),
            "sight_rng_flat_m": self.sight_rng_flat_m,
            "mil_dial_flat": self.mil_dial_flat,
            "correction_layers": self.correction_layers,
        }

    def recommended_mil(self) -> str | None:
        if self.weapon_id == "mortar":
            return self.elevation_single.formatted() if self.elevation_single else None
        arc = self.effective_arc or self.preferred_arc
        if arc == "high":
            return self.elevation_high.formatted() if self.elevation_high else None
        if arc == "low":
            return self.elevation_low.formatted() if self.elevation_low else None
        if self.elevation_low and self.elevation_high:
            return self.elevation_low.formatted()
        return (self.elevation_low or self.elevation_high or MilSolution()).formatted()

    def voice_callout(self) -> str:
        parts = [
            f"方位 {self.azimuth_deg:.0f} 度",
        ]
        if self.sight_rng_m is not None:
            parts.append(f"RNG {self.sight_rng_m} 米")
        if self.mil_dial is not None:
            parts.append(f"MIL {self.mil_dial}")
        elif self.recommended_mil():
            parts.append(f"MIL {self.recommended_mil()}")
        arc = self.effective_arc
        if arc == "high":
            parts.append("高弹道模式")
        elif arc == "low":
            parts.append("低弹道模式")
        return "，".join(parts)


class BallisticsEngine:
    def __init__(self, weapons_path: Path | None = None) -> None:
        path = weapons_path or Path(__file__).resolve().parent.parent / "data" / "weapons.json"
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        self.weapons: dict[str, dict] = {w["id"]: w for w in data["weapons"]}

    @staticmethod
    def parse_coordinates(text: str) -> Point | None:
        from .coords import extract_coord_pairs

        pairs = extract_coord_pairs(text or "")
        if not pairs:
            return None
        return pairs[0][0]

    @staticmethod
    def _group_table(table: list[list[float]]) -> list[tuple[float, list[float]]]:
        grouped: dict[float, list[float]] = {}
        for dist, mil in sorted(table, key=lambda r: (r[0], r[1])):
            grouped.setdefault(dist, []).append(mil)
        return sorted(grouped.items())

    def _table_dial_pair(
        self, table: list[list[float]], map_distance_m: float
    ) -> dict[str, Any] | None:
        """射表 [HUD左RNG, 右MIL] 对 map 距离插值；高/低支共用同一地图距离。"""
        if not table or not math.isfinite(map_distance_m):
            return None
        groups = self._group_table(table)
        eps = 1e-6
        for dist, mils in groups:
            if abs(dist - map_distance_m) <= eps:
                mil = sum(mils) / len(mils)
                return {
                    "map_range_m": int(round(map_distance_m)),
                    "sight_rng_m": int(round(dist)),
                    "mil": int(round(mil)),
                }
        for i in range(len(groups) - 1):
            d1, m1s = groups[i]
            d2, m2s = groups[i + 1]
            lo, hi = min(d1, d2), max(d1, d2)
            if lo + eps < map_distance_m < hi - eps or abs(map_distance_m - lo) <= eps or abs(map_distance_m - hi) <= eps:
                if abs(d2 - d1) < eps:
                    continue
                factor = (map_distance_m - d1) / (d2 - d1)
                sight_rng = d1 + factor * (d2 - d1)
                right_avg = sum(m2s) / len(m2s)
                left_mil = min(m1s, key=lambda v: abs(v - right_avg))
                right_mil = min(m2s, key=lambda v: abs(v - left_mil))
                mil = left_mil + factor * (right_mil - left_mil)
                return {
                    "map_range_m": int(round(map_distance_m)),
                    "sight_rng_m": int(round(sight_rng)),
                    "mil": int(round(mil)),
                }
        return None

    @staticmethod
    def _mil_solution_to_dial(solution: MilSolution | None, distance_m: float) -> dict[str, Any] | None:
        if not solution:
            return None
        mil = solution.mil if solution.mil is not None else solution.min_mil
        if mil is None:
            return None
        mil_int = int(round(mil))
        rng_m = int(round(distance_m))
        return {
            "map_range_m": rng_m,
            "sight_rng_m": rng_m,
            "mil": mil_int,
        }

    def _interpolate(self, table: list[list[float]], distance_m: float) -> MilSolution | None:
        if not table or not math.isfinite(distance_m):
            return None
        groups = self._group_table(table)
        eps = 1e-6
        for dist, mils in groups:
            if abs(dist - distance_m) <= eps:
                lo, hi = min(mils), max(mils)
                return MilSolution(mil=mils[0] if len(mils) == 1 else None, min_mil=lo, max_mil=hi)

        for i in range(len(groups) - 1):
            d1, m1s = groups[i]
            d2, m2s = groups[i + 1]
            if d1 < distance_m < d2:
                right_avg = sum(m2s) / len(m2s)
                left_mil = min(m1s, key=lambda v: abs(v - right_avg))
                right_mil = min(m2s, key=lambda v: abs(v - left_mil))
                factor = (distance_m - d1) / (d2 - d1)
                mil = left_mil + factor * (right_mil - left_mil)
                return MilSolution(mil=mil, min_mil=mil, max_mil=mil)
        return None

    @staticmethod
    def _table_min_distance(table: list[list[float]]) -> float | None:
        if not table:
            return None
        return min(row[0] for row in table if len(row) >= 2)

    @staticmethod
    def _apply_sph_low_hud_calibration(distance_m: float, table_mil: float) -> tuple[float, float]:
        """
        社区 flat 低弹道射表在 ~1520–1832 m 段普遍低于游戏 HUD 配对读数。
        校准锚点：实测 RNG≈1655–1660 m → 右 MIL≈160；射表同距约 119，1832 m 处射表≈160。
        返回 (calibrated_mil, bump_applied)。
        """
        lo_rise = 1520.0
        peak_d = 1660.0
        hi_rise = 1832.0
        peak_bump = 42.0

        if distance_m < lo_rise or distance_m > hi_rise:
            return table_mil, 0.0

        if distance_m <= peak_d:
            bump = peak_bump * (distance_m - lo_rise) / (peak_d - lo_rise)
        else:
            bump = peak_bump * (hi_rise - distance_m) / (hi_rise - peak_d)

        bump = max(0.0, min(peak_bump, bump))
        return table_mil + bump, bump

    @staticmethod
    def _calibrate_sph_low_solution(distance_m: float, low: MilSolution | None) -> MilSolution | None:
        if not low or low.min_mil is None:
            return low
        base = low.mil if low.mil is not None else low.min_mil
        calibrated, bump = BallisticsEngine._apply_sph_low_hud_calibration(distance_m, base)
        if bump < 0.5:
            return low
        return MilSolution(mil=calibrated, min_mil=calibrated, max_mil=calibrated)

    @staticmethod
    def _resolve_sph_effective_arc(
        preferred_arc: str | None,
        low: MilSolution | None,
        high: MilSolution | None,
        distance_m: float,
        meta: dict[str, Any],
        gun_alt: float | None = None,
        target_alt: float | None = None,
    ) -> tuple[str | None, list[str]]:
        """SPH 口令/主读数必须与真实可用弹道一致，避免「建议低弹道 + 高 MIL」。"""
        low_ok = low is not None
        high_ok = high is not None
        practical_min = float(meta.get("low_arc_practical_min_m", 1283))
        extra: list[str] = []

        want = preferred_arc if preferred_arc in ("low", "high") else None

        if want == "low":
            if low_ok and distance_m + 0.5 >= practical_min:
                extra.append(
                    "已选低支：抬炮幅度较小，左RNG随右MIL升高（未过最大射程点）。"
                )
                return "low", extra
            if high_ok:
                extra.append(
                    f"低支在此距离（约 {round(distance_m)}m）不可用，已改高支读数。"
                )
                return "high", extra
            return ("low" if low_ok else None), extra

        if want == "high":
            if high_ok:
                return "high", extra
            if low_ok:
                extra.append("高支不可用，已改为低支读数。")
                return "low", extra
            return None, extra

        terrain_known = gun_alt is not None and target_alt is not None

        # 自动：近距只能高支；地形未知优先高支过障；填了 ASL 再建议低支省弹
        if high_ok and (not low_ok or distance_m + 0.5 < practical_min):
            return "high", extra
        if (
            low_ok
            and high_ok
            and distance_m + 0.5 >= practical_min
            and not terrain_known
        ):
            extra.append(
                "自动：未填炮/目标 ASL，默认建议高支（低支易撞山）。"
                "确认平地且无遮挡可设 config \"arc\": \"low\"；填 ASL 后 auto 可推低支。"
            )
            return "high", extra
        if low_ok and distance_m + 0.5 >= practical_min:
            extra.append(
                "自动：首发建议低支（抬炮至左RNG仍升高段，右MIL较小）。"
                "需高支读数请在 config.json 设 \"arc\": \"high\"。"
            )
            return "low", extra
        if high_ok:
            return "high", extra
        return None, extra

    def compute(
        self,
        origin: Point,
        target: Point,
        weapon_id: str = "mortar",
        preferred_arc: str | None = None,
        gun_alt: float | None = None,
        target_alt: float | None = None,
    ) -> FiringSolution:
        weapon = self.weapons.get(weapon_id, self.weapons["mortar"])
        meta = WEAPON_META.get(weapon_id, WEAPON_META["mortar"])

        dx = target.x - origin.x
        dy = target.y - origin.y
        distance_m = math.hypot(dx, dy) * METERS_PER_UNIT
        azimuth_deg = math.degrees(math.atan2(dx, dy))
        if azimuth_deg < 0:
            azimuth_deg += 360

        ballistics = weapon.get("ballistics", {})
        single = self._interpolate(ballistics.get("single", []), distance_m)
        low_table = ballistics.get("low", [])
        high_table = ballistics.get("high", [])
        low = self._interpolate(low_table, distance_m)
        high = self._interpolate(high_table, distance_m)

        low_hud_bump = 0.0
        if weapon_id == "spg":
            practical_min = float(meta.get("low_arc_practical_min_m", 1283))
            if distance_m + 0.5 < practical_min:
                low = None
            elif low is not None:
                base_mil = low.mil if low.mil is not None else low.min_mil
                if base_mil is not None:
                    _, low_hud_bump = self._apply_sph_low_hud_calibration(distance_m, base_mil)
                    low = self._calibrate_sph_low_solution(distance_m, low)

        warnings: list[str] = []
        if distance_m < meta["dead_zone_m"]:
            warnings.append(f"目标在 {meta['dead_zone_m']}m 死区内，请移动火炮。")
        if distance_m < meta["min_range_m"]:
            warnings.append(f"距离 {round(distance_m)}m 低于最小射程 {meta['min_range_m']}m。")
        elif distance_m > meta["max_range_m"]:
            warnings.append(f"距离 {round(distance_m)}m 超出最大射程 {meta['max_range_m']}m。")
        if weapon_id == "spg" and low is None and high is not None:
            practical_min = int(meta.get("low_arc_practical_min_m", 1283))
            if distance_m + 0.5 < practical_min:
                warnings.append(
                    f"约 {practical_min}m 以内请用高弹道（右 MIL 约 610–1390）；"
                    f"低弹道（右 MIL 约 20–600）需更远距离。"
                )
        delta_z = None
        terrain_note = None
        if gun_alt is not None and target_alt is not None:
            delta_z = target_alt - gun_alt
            if abs(delta_z) >= 0.5:
                if delta_z > 0:
                    terrain_note = f"目标比炮位高 {delta_z:.1f}m，平地射表可能偏短。"
                else:
                    terrain_note = f"目标比炮位低 {abs(delta_z):.1f}m，平地射表可能偏长。"

        correction_hint = (
            "偏短=落点在目标靠近你这一侧（打近了）→ 迫击炮/SPH高弹道：略降 MIL 或略增 RNG；"
            "偏长=落点越过目标（打远了）→ 略升 MIL 或略减 RNG。"
            "SPH 低弹道：偏短→升 MIL，偏长→降 MIL。"
        )

        effective_arc: str | None = None
        if weapon_id == "spg":
            effective_arc, arc_warnings = self._resolve_sph_effective_arc(
                preferred_arc, low, high, distance_m, meta, gun_alt, target_alt
            )
            warnings.extend(arc_warnings)
            if effective_arc == "low" and low_hud_bump >= 0.5:
                warnings.append(
                    f"低弹道 MIL 已按游戏 HUD 配对校准（+{round(low_hud_bump)}，"
                    f"社区 flat 表在 {round(distance_m)}m 附近常偏低）。"
                )

        dial_pairs: list[dict[str, Any]] = []
        sight_rng_m: int | None = None
        mil_dial: int | None = None

        if single:
            pair = self._mil_solution_to_dial(single, distance_m)
            if pair:
                dial_pairs.append({"arc": "single", **pair})
                sight_rng_m = pair["sight_rng_m"]
                mil_dial = pair["mil"]
        low_table = ballistics.get("low", [])
        high_table = ballistics.get("high", [])
        if weapon_id == "spg" and low_table:
            # 低支：左 RNG = 地图距离；MIL 用已校准的 elevation_low（非 raw 射表插值）
            if low:
                pair = self._mil_solution_to_dial(low, distance_m)
            else:
                pair = self._table_dial_pair(low_table, distance_m)
            if pair:
                dial_pairs.append({"arc": "low", **pair})
        elif low:
            pair = self._mil_solution_to_dial(low, distance_m)
            if pair:
                dial_pairs.append({"arc": "low", **pair})
        if weapon_id == "spg" and high_table:
            pair = self._table_dial_pair(high_table, distance_m)
            if pair:
                dial_pairs.append({"arc": "high", **pair})
        elif high:
            pair = self._mil_solution_to_dial(high, distance_m)
            if pair:
                dial_pairs.append({"arc": "high", **pair})

        if weapon_id == "spg" and effective_arc:
            for p in dial_pairs:
                if p.get("arc") == effective_arc:
                    sight_rng_m = p["sight_rng_m"]
                    mil_dial = p["mil"]
                    break
            if mil_dial is not None and len(dial_pairs) >= 2:
                warnings.insert(
                    0,
                    "同距离有两套读数：低支=抬炮未过RNG顶点；高支=继续抬高后左RNG回落。"
                    "请与炮镜上实际抬炮幅度一致，勿混用两支的 RNG/MIL。",
                )
        elif dial_pairs:
            p = dial_pairs[0]
            sight_rng_m = p["sight_rng_m"]
            mil_dial = p["mil"]

        aiming_note = None
        if weapon_id == "mortar":
            aiming_note = (
                "迫击炮：左刻度 RNG 与右刻度 MIL 是配套读数，数值不会相同。"
                f"平地上请左刻度 RNG≈{int(round(distance_m))}m，右刻度 MIL≈{mil_dial or '—'}；"
                "方向优先用地图中键标记 + 准星对准白色标记。"
            )
        arc_mode_hint = None
        if weapon_id == "spg":
            branch = "低支" if effective_arc == "low" else "高支" if effective_arc == "high" else "—"
            aiming_note = (
                "SPH-2：游戏内无弹道开关；抬高炮管时左RNG先升后降，右MIL一直升。"
                f"过最大射程点后为高支。本次主用{branch}：左RNG {sight_rng_m or '—'} + 右MIL {mil_dial or '—'}；"
                "若有两套读数，选与当前抬炮幅度一致的一支。射击前停平车体。"
            )

        return FiringSolution(
            weapon_id=weapon_id,
            weapon_name=meta["name"],
            origin=origin,
            target=target,
            distance_m=distance_m,
            distance_km=distance_m / 1000,
            azimuth_deg=azimuth_deg,
            azimuth_mil=azimuth_deg * 6400 / 360,
            dx_m=dx * METERS_PER_UNIT,
            dy_m=dy * METERS_PER_UNIT,
            elevation_single=single,
            elevation_low=low,
            elevation_high=high,
            preferred_arc=preferred_arc,
            effective_arc=effective_arc,
            gun_alt=gun_alt,
            target_alt=target_alt,
            delta_z=delta_z,
            warnings=warnings,
            terrain_note=terrain_note,
            correction_hint=correction_hint,
            aiming_note=aiming_note,
            arc_mode_hint=arc_mode_hint,
            sight_rng_m=sight_rng_m,
            mil_dial=mil_dial,
            dial_pairs=dial_pairs,
        )
