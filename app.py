#!/usr/bin/env python3
"""FireMind Web API."""

from __future__ import annotations

import uuid
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory

from firemind.llm import llm_available
from firemind.ocr_rate_limit import allow_ocr
from firemind.orchestrator import FireMindOrchestrator
from firemind.vision_ocr import ocr_available, ocr_map_image

ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"

app = Flask(__name__, static_folder=str(STATIC), static_url_path="")
orchestrator = FireMindOrchestrator()


@app.get("/")
def index():
    return send_from_directory(STATIC, "index.html")


@app.get("/api/health")
def health():
    return jsonify({
        "status": "ok",
        "product": "FireMind",
        "llm_enabled": llm_available(),
        "ocr_enabled": ocr_available(),
    })


@app.post("/api/chat")
def chat():
    data = request.get_json(silent=True) or {}
    message = (data.get("message") or "").strip()
    session_id = data.get("session_id") or str(uuid.uuid4())
    if not message:
        return jsonify({"error": "message required"}), 400
    resp = orchestrator.handle_message(session_id, message)
    out = resp.to_dict()
    out["session_id"] = session_id
    return jsonify(out)


@app.post("/api/calculate")
def calculate():
    data = request.get_json(silent=True) or {}
    session_id = data.get("session_id") or str(uuid.uuid4())
    try:
        arc = data.get("preferred_arc")
        if arc in ("", "auto", None):
            arc = None
        resp = orchestrator.run_form(
            session_id,
            gun_x=float(data["gun_x"]),
            gun_y=float(data["gun_y"]),
            target_x=float(data["target_x"]),
            target_y=float(data["target_y"]),
            weapon_id=data.get("weapon_id", "mortar"),
            preferred_arc=arc,
            gun_alt=_optional_float(data.get("gun_alt")),
            target_alt=_optional_float(data.get("target_alt")),
            layer_asl=bool(data.get("layer_asl", True)),
            layer_hull=bool(data.get("layer_hull", False)),
            baseline_pitch=bool(data.get("baseline_pitch", False)),
            hull_heading_deg=_optional_float(data.get("hull_heading_deg")),
        )
    except (KeyError, TypeError, ValueError) as e:
        return jsonify({"error": str(e)}), 400
    out = resp.to_dict()
    out["session_id"] = session_id
    return jsonify(out)


def _optional_float(value) -> float | None:
    if value is None or value == "":
        return None
    return float(value)


@app.post("/api/correct")
def correct():
    data = request.get_json(silent=True) or {}
    session_id = data.get("session_id")
    miss_text = (data.get("miss") or data.get("message") or "").strip()
    if not session_id or not miss_text:
        return jsonify({"error": "session_id and miss required"}), 400
    session = orchestrator._get_session(session_id)
    if not session.last_solution:
        return jsonify({"error": "no prior solution in session"}), 400
    from firemind.correction import suggest_correction

    corr = suggest_correction(
        session.last_solution,
        miss_text,
        orchestrator.engine,
        data.get("arc"),
    )
    return jsonify(corr.to_dict())


@app.post("/api/vision/ocr")
def vision_ocr():
    data = request.get_json(silent=True) or {}
    client = request.headers.get("X-Forwarded-For", request.remote_addr or "anon")
    ok, remaining = allow_ocr(client.split(",")[0].strip())
    if not ok:
        return jsonify({"error": "OCR rate limit", "remaining": 0}), 429
    image_bytes, mime, err = _decode_image_payload(data)
    if err:
        return jsonify({"error": err}), 400
    role = (data.get("role") or "").strip() or None
    result = ocr_map_image(image_bytes, mime, role, data.get("capture_mode"))
    result["ocr_remaining_hour"] = remaining
    return jsonify(result)


def _decode_image_payload(data: dict) -> tuple[bytes | None, str, str | None]:
    import base64

    raw = data.get("image_base64") or data.get("image")
    if not raw:
        return None, "image/png", "image_base64 required"
    if isinstance(raw, str) and raw.startswith("data:"):
        head, _, b64 = raw.partition(",")
        mime = head.split(";")[0].replace("data:", "") or "image/png"
        raw = b64
    else:
        mime = data.get("mime") or "image/png"
    try:
        return base64.b64decode(raw), mime, None
    except ValueError:
        return None, mime, "invalid base64 in image_base64"


@app.post("/api/dual-shot/check-point")
def dual_shot_check_point():
    data = request.get_json(silent=True) or {}
    session_id = data.get("session_id")
    if not session_id:
        return jsonify({"error": "session_id required"}), 400
    session = orchestrator._get_session(session_id)
    if session.last_solution is None:
        return jsonify({"error": "请先 F1/F2 或网页计算诸元"}), 400
    from firemind.dual_shot import compute_check_point

    offset = float(data.get("offset_deg", 90))
    sol = session.last_solution
    cp = compute_check_point(sol.origin, sol.target, offset)
    return jsonify({"check_point": cp, "session_id": session_id})


@app.post("/api/correct-dual-impact")
def correct_dual_impact():
    data = request.get_json(silent=True) or {}
    session_id = data.get("session_id")
    if not session_id:
        return jsonify({"error": "session_id required"}), 400
    try:
        i1x, i1y = float(data["impact1_x"]), float(data["impact1_y"])
        i2x, i2y = float(data["impact2_x"]), float(data["impact2_y"])
    except (KeyError, TypeError, ValueError):
        return jsonify({"error": "impact1_x/y and impact2_x/y required"}), 400
    arc = data.get("arc")
    if arc in ("", "auto", None):
        arc = None
    offset = float(data.get("offset_deg", 90))
    from firemind.ballistics import Point
    from firemind.dual_shot import merge_dual_impact_correction

    session = orchestrator._get_session(session_id)
    if session.last_solution is None:
        return jsonify({"error": "no prior solution"}), 400
    use_arc = arc if arc in ("low", "high") else session.mission.preferred_arc
    corr, cp_info = merge_dual_impact_correction(
        session.last_solution,
        Point(i1x, i1y),
        Point(i2x, i2y),
        orchestrator.engine,
        use_arc,
        offset,
    )
    return jsonify({
        "correction": corr.to_dict(),
        "check_point": cp_info,
        "session_id": session_id,
    })


@app.post("/api/correct-impact")
def correct_impact():
    data = request.get_json(silent=True) or {}
    session_id = data.get("session_id")
    if not session_id:
        return jsonify({"error": "session_id required"}), 400
    try:
        impact_x = float(data["impact_x"])
        impact_y = float(data["impact_y"])
    except (KeyError, TypeError, ValueError):
        return jsonify({"error": "impact_x and impact_y required"}), 400
    arc = data.get("arc")
    if arc in ("", "auto", None):
        arc = None
    resp = orchestrator.run_impact_correction(session_id, impact_x, impact_y, arc)
    out = resp.to_dict()
    out["session_id"] = session_id
    return jsonify(out)


if __name__ == "__main__":
    import os

    port = int(os.environ.get("PORT", 8787))
    app.run(host="0.0.0.0", port=port, debug=False)
