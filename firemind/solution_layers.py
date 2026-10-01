"""平地射表之上的并行修正：ASL、SPH 车体朝向、基线俯仰。"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from .ballistics import FiringSolution


@dataclass
class LayerOptions:
    use_asl: bool = True
    use_hull: bool = False
    baseline_pitch: bool = False
    hull_heading_deg: float | None = None


def _hull_layer(sol: FiringSolution, hull_heading_deg: float) -> dict[str, Any] | None:
    if sol.weapon_id != "spg" or sol.sight_rng_m is None or sol.mil_dial is None:
        return None
    rel = (sol.azimuth_deg - hull_heading_deg + 180) % 360 - 180
    dist = max(sol.distance_m, 400)
    arc = sol.effective_arc or "high"
    rng_delta = int(round(-0.06 * abs(rel) * dist / 1000))
    if arc == "high":
        mil_delta = round(-0.22 * rel, 1)
    else:
        mil_delta = round(0.18 * rel, 1)
    return {
        "id": "hull",
        "label": "车体朝向",
        "rng_delta": rng_delta,
        "mil_delta": mil_delta,
        "note": f"车体朝向 {hull_heading_deg:.0f}°，炮线 {sol.azimuth_deg:.1f}°，夹角 {rel:.0f}°",
    }


def _asl_layer(sol: FiringSolution) -> dict[str, Any] | None:
    if sol.gun_alt is None or sol.target_alt is None:
        return None
    dz = sol.target_alt - sol.gun_alt
    if abs(dz) < 0.5:
        return None
    arc = sol.effective_arc or ("high" if sol.weapon_id == "spg" else "single")
    rng_delta = int(round(-0.04 * dz))
    if sol.weapon_id == "mortar":
        mil_delta = round(-0.5 * dz, 1)
    elif arc == "high":
        mil_delta = round(-0.65 * dz, 1)
    else:
        mil_delta = round(0.45 * dz, 1)
    return {
        "id": "asl",
        "label": "游戏 ASL 高差",
        "rng_delta": rng_delta,
        "mil_delta": mil_delta,
        "note": f"炮位 ASL {sol.gun_alt:.0f} m → 目标 {sol.target_alt:.0f} m（Δ{dz:+.0f} m）",
    }


def _pitch_layer(sol: FiringSolution) -> dict[str, Any] | None:
    if sol.weapon_id != "spg" or sol.mil_dial is None:
        return None
    arc = sol.effective_arc or "high"
    mil_delta = 2.0 if arc == "high" else -1.5
    return {
        "id": "pitch",
        "label": "基线俯仰 +1°",
        "rng_delta": 0,
        "mil_delta": mil_delta,
        "note": "社区基线：车体俯仰约 +1° 时的 MIL 微调（仅 SPH）",
    }


def apply_layers(sol: FiringSolution, opt: LayerOptions) -> tuple[FiringSolution, list[str]]:
    notes: list[str] = []
    if sol.sight_rng_flat_m is None:
        sol.sight_rng_flat_m = sol.sight_rng_m
    if sol.mil_dial_flat is None:
        sol.mil_dial_flat = sol.mil_dial

    layers: list[dict[str, Any]] = []
    if opt.use_asl:
        L = _asl_layer(sol)
        if L:
            layers.append(L)
    if opt.use_hull and opt.hull_heading_deg is not None:
        L = _hull_layer(sol, float(opt.hull_heading_deg))
        if L:
            layers.append(L)
    if opt.baseline_pitch:
        L = _pitch_layer(sol)
        if L:
            layers.append(L)

    rng = sol.sight_rng_flat_m or sol.sight_rng_m or 0
    mil = float(sol.mil_dial_flat or sol.mil_dial or 0)
    for L in layers:
        rng += int(L.get("rng_delta") or 0)
        mil += float(L.get("mil_delta") or 0)

    sol.sight_rng_m = int(rng)
    sol.mil_dial = int(round(mil))
    sol.correction_layers = layers
    if layers:
        notes.append("已叠加并行修正层（平地射表 + " + " + ".join(x["label"] for x in layers) + "）")
    return sol, notes
