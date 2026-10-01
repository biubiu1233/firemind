"""FireMind 编排层 — 规则引擎 + LLM + 弹道 + 修正。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .ballistics import BallisticsEngine, FiringSolution, Point
from .correction import suggest_correction, suggest_correction_from_impact
from .llm import generate_briefing, llm_available, parse_with_llm
from .parser import ParsedMission, build_clarification, parse_mission_rules


@dataclass
class SessionState:
    mission: ParsedMission = field(default_factory=ParsedMission)
    last_solution: FiringSolution | None = None
    history: list[dict[str, str]] = field(default_factory=list)


@dataclass
class ChatResponse:
    reply: str
    solution: dict[str, Any] | None = None
    correction: dict[str, Any] | None = None
    needs_input: list[str] = field(default_factory=list)
    engine: str = "rules"
    intent: str = "fire_mission"

    def to_dict(self) -> dict[str, Any]:
        return {
            "reply": self.reply,
            "solution": self.solution,
            "correction": self.correction,
            "needs_input": self.needs_input,
            "engine": self.engine,
            "intent": self.intent,
            "llm_enabled": llm_available(),
        }


class FireMindOrchestrator:
    def __init__(self) -> None:
        self.engine = BallisticsEngine()
        self.sessions: dict[str, SessionState] = {}

    def _get_session(self, session_id: str) -> SessionState:
        if session_id not in self.sessions:
            self.sessions[session_id] = SessionState()
        return self.sessions[session_id]

    def handle_message(self, session_id: str, message: str) -> ChatResponse:
        session = self._get_session(session_id)
        text = message.strip()
        if not text:
            return ChatResponse(reply="请输入射击任务或试射偏差描述。", engine="rules")

        engine_used = "rules"
        llm_data = None
        if llm_available():
            llm_data = parse_with_llm(text, session.history[-6:])
            if llm_data:
                engine_used = "llm"

        intent = "fire_mission"
        llm_reply = None
        if llm_data:
            intent = llm_data.get("intent", "fire_mission")
            llm_reply = llm_data.get("reply")
            mission = session.mission.merge(llm_data["_mission"])
        else:
            mission = session.mission.merge(parse_mission_rules(text, session.mission))

        session.mission = mission

        # 试射修正
        if intent == "correction" or any(k in text for k in ("偏短", "偏长", "偏左", "偏右", "近了", "远了")):
            if session.last_solution is None:
                return ChatResponse(
                    reply="还没有射击诸元。请先告诉我炮位和目标坐标。",
                    engine=engine_used,
                    intent="correction",
                )
            corr = suggest_correction(session.last_solution, text, self.engine, mission.preferred_arc)
            reply = llm_reply or corr.explanation
            session.history.append({"role": "user", "content": text})
            session.history.append({"role": "assistant", "content": reply})
            return ChatResponse(
                reply=reply,
                correction=corr.to_dict(),
                engine=engine_used,
                intent="correction",
            )

        if mission.missing:
            clarify = llm_reply or build_clarification(mission) or "请补充炮位和目标坐标。"
            return ChatResponse(
                reply=clarify,
                needs_input=mission.missing,
                engine=engine_used,
                intent="clarify",
            )

        assert mission.gun and mission.target
        solution = self.engine.compute(
            mission.gun,
            mission.target,
            weapon_id=mission.weapon_id or "mortar",
            preferred_arc=mission.preferred_arc,
            gun_alt=mission.gun_alt,
            target_alt=mission.target_alt,
        )
        solution.parse_warnings = list(mission.parse_warnings)
        session.last_solution = solution
        sol_dict = solution.to_dict()

        reply = llm_reply or self._format_solution_reply(solution)
        session.history.append({"role": "user", "content": text})
        session.history.append({"role": "assistant", "content": reply})

        return ChatResponse(
            reply=reply,
            solution=sol_dict,
            engine=engine_used,
            intent="fire_mission",
        )

    @staticmethod
    def _format_solution_reply(s: FiringSolution) -> str:
        lines = [
            f"收到。{s.weapon_name}射击方案：",
            f"• 炮位 x{s.origin.x:.2f} y{s.origin.y:.2f} → 目标 x{s.target.x:.2f} y{s.target.y:.2f}",
            f"• 方位角 {s.azimuth_deg:.1f}°（地图罗盘：0°北 90°东 180°南 270°西）",
            f"• 地图距离 / 左刻度 RNG：{int(round(s.distance_m))} m",
        ]
        if s.weapon_id == "spg" and s.effective_arc:
            arc_label = "高弹道" if s.effective_arc == "high" else "低弹道"
            lines.append(f"• 游戏内请先切到【{arc_label}】再拧刻度（两套 MIL 不可混用）")
        for w in s.parse_warnings:
            lines.append(f"⚠ {w}")
        eff = s.effective_arc
        if s.mil_dial is not None and s.sight_rng_m is not None:
            if s.weapon_id == "spg" and eff:
                arc_label = "高弹道" if eff == "high" else "低弹道"
                lines.append(
                    f"• 本次仅【{arc_label}】：左 RNG {s.sight_rng_m} m + 右 MIL {s.mil_dial}"
                )
            else:
                lines.append(
                    f"• 本次读数：左 RNG {s.sight_rng_m} m + 右 MIL {s.mil_dial}"
                )
        if s.weapon_id == "spg" and eff == "high":
            lines.append("• 勿使用低弹道 ~100 多 MIL；1660m 级距离首发请用高弹道 MIL 1100+。")
        if s.aiming_note:
            lines.append(f"💡 {s.aiming_note}")
        if s.warnings:
            lines.append("⚠ " + s.warnings[0])
        lines.append(f"\n队频口令：{s.voice_callout()}")
        lines.append(
            "\n首发试射后：在地图上对落点标记，把落点坐标粘贴到「试射修正」区，"
            "会自动算偏东/西/北/南多少米并给出 RNG/MIL 修正建议。"
        )
        return "\n".join(lines)

    def run_impact_correction(
        self,
        session_id: str,
        impact_x: float,
        impact_y: float,
        arc: str | None = None,
    ) -> ChatResponse:
        session = self._get_session(session_id)
        if session.last_solution is None:
            return ChatResponse(
                reply="请先计算一次射击诸元，再填写落点坐标。",
                engine="rules",
                intent="correction",
            )
        use_arc = arc if arc in ("low", "high") else session.mission.preferred_arc
        corr = suggest_correction_from_impact(
            session.last_solution,
            Point(impact_x, impact_y),
            self.engine,
            use_arc,
        )
        return ChatResponse(
            reply=corr.explanation,
            correction=corr.to_dict(),
            engine="rules",
            intent="correction",
        )

    def run_form(
        self,
        session_id: str,
        gun_x: float,
        gun_y: float,
        target_x: float,
        target_y: float,
        weapon_id: str = "mortar",
        preferred_arc: str | None = None,
        gun_alt: float | None = None,
        target_alt: float | None = None,
        layer_asl: bool = True,
        layer_hull: bool = False,
        baseline_pitch: bool = False,
        hull_heading_deg: float | None = None,
    ) -> ChatResponse:
        session = self._get_session(session_id)
        arc = preferred_arc if preferred_arc in ("low", "high") else None

        solution = self.engine.compute(
            Point(gun_x, gun_y),
            Point(target_x, target_y),
            weapon_id=weapon_id,
            preferred_arc=arc,
            gun_alt=gun_alt,
            target_alt=target_alt,
        )
        from .solution_layers import LayerOptions, apply_layers

        if weapon_id == "spg" or (layer_asl and gun_alt is not None and target_alt is not None):
            solution, layer_notes = apply_layers(
                solution,
                LayerOptions(
                    use_asl=layer_asl and gun_alt is not None and target_alt is not None,
                    use_hull=layer_hull and weapon_id == "spg",
                    baseline_pitch=baseline_pitch and weapon_id == "spg",
                    hull_heading_deg=hull_heading_deg,
                ),
            )
            solution.warnings.extend(layer_notes)
        session.last_solution = solution
        session.mission = ParsedMission(
            gun=Point(gun_x, gun_y),
            target=Point(target_x, target_y),
            weapon_id=weapon_id,
            preferred_arc=arc,
            gun_alt=gun_alt,
            target_alt=target_alt,
        )

        fallback = self._format_solution_reply(solution)
        brief, engine = generate_briefing(solution.to_dict(), fallback)

        return ChatResponse(
            reply=brief,
            solution=solution.to_dict(),
            engine=engine,
            intent="fire_mission",
        )

    def calculate_direct(
        self,
        gun_x: float,
        gun_y: float,
        target_x: float,
        target_y: float,
        weapon_id: str = "mortar",
        preferred_arc: str | None = None,
        gun_alt: float | None = None,
        target_alt: float | None = None,
    ) -> dict[str, Any]:
        return self.run_form(
            "api",
            gun_x,
            gun_y,
            target_x,
            target_y,
            weapon_id,
            preferred_arc,
            gun_alt,
            target_alt,
        ).solution or {}
