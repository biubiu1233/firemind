#!/usr/bin/env python3
"""FireMind 小窗助手 — F1~F4 识图，简洁诸元/修正。"""
from __future__ import annotations

import base64
import io
import json
import os
import re
import sys
import threading
import time
import tkinter as tk
from tkinter import simpledialog, ttk
from pathlib import Path

import keyboard
import mss
import requests
from PIL import Image

DEFAULT_HOTKEYS = {
    "gun": "f1",
    "target": "f2",
    "impact": "f3",
    "impact2": "f4",
    "toggle_ui": "f12",
}
DEFAULT_API_URL = os.environ.get("FIREMIND_DEFAULT_API_URL", "").strip()


def app_dir() -> Path:
    return Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).parent


def load_config() -> dict:
    path = app_dir() / "config.json"
    data = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    if os.environ.get("FIREMIND_API_URL"):
        data["api_url"] = os.environ["FIREMIND_API_URL"]
    elif not data.get("api_url") and DEFAULT_API_URL:
        data["api_url"] = DEFAULT_API_URL
    data.setdefault("weapon", "spg")
    data.setdefault("arc", "auto")
    data.setdefault("monitor", 1)
    data.setdefault("crop_left_px", 460)
    data.setdefault("correction_mode", "single")
    data.setdefault("dual_offset_deg", 90)
    data.setdefault("ocr_source", "map")
    data["hotkeys"] = {**DEFAULT_HOTKEYS, **(data.get("hotkeys") or {})}
    return data


def save_config(data: dict) -> None:
    (app_dir() / "config.json").write_text(
        json.dumps({k: v for k, v in data.items() if not str(k).startswith("_")}, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


CFG = load_config()
API_URL = (CFG.get("api_url") or "").rstrip("/")
state: dict = {"session_id": None, "gun": None, "target": None, "impact1": None}
overlay: tk.Tk | None = None
ui_visible = True

main_var: tk.StringVar | None = None
sub_var: tk.StringVar | None = None
corr_mode_var: tk.StringVar | None = None
gun_alt_var: tk.StringVar | None = None
target_alt_var: tk.StringVar | None = None
tip_label: tk.Label | None = None


def ui_call(fn) -> None:
    if overlay:
        overlay.after(0, fn)


def set_lines(main: str, sub: str = "", toast: bool = True) -> None:
    def go() -> None:
        if main_var:
            main_var.set(main)
        if sub_var:
            sub_var.set(sub)
        if toast and not ui_visible:
            show_toast(main, sub)

    ui_call(go)


def show_toast(title: str, body: str = "") -> None:
    if not overlay:
        return

    tw = tk.Toplevel(overlay)
    tw.overrideredirect(True)
    tw.attributes("-topmost", True)
    tw.configure(bg="#2a3140", highlightbackground="#4a5568", highlightthickness=1)
    pad = tk.Frame(tw, bg="#2a3140", padx=12, pady=10)
    pad.pack()
    tk.Label(
        pad, text=title[:80], bg="#2a3140", fg="#ffffff",
        font=("Microsoft YaHei UI", 10, "bold"), wraplength=320, justify="left",
    ).pack(anchor="w")
    if body:
        tk.Label(
            pad, text=body[:160], bg="#2a3140", fg="#b8c0cc",
            font=("Microsoft YaHei UI", 9), wraplength=320, justify="left",
        ).pack(anchor="w", pady=(4, 0))
    tw.update_idletasks()
    sw = tw.winfo_screenwidth()
    sh = tw.winfo_screenheight()
    w, h = tw.winfo_width(), tw.winfo_height()
    tw.geometry(f"+{max(8, sw - w - 24)}+{max(8, sh - h - 80)}")
    tw.after(2800, tw.destroy)


def run_bg(fn) -> None:
    threading.Thread(target=lambda: _safe(fn), daemon=True).start()


def _safe(fn) -> None:
    try:
        fn()
    except Exception as e:
        set_lines("错误", str(e)[:120])


def read_mode() -> None:
    if corr_mode_var:
        CFG["correction_mode"] = corr_mode_var.get()
        save_config(CFG)
    refresh_tip_label()


def grab_b64() -> str:
    idx = max(1, int(CFG.get("monitor", 1)))
    with mss.mss() as sct:
        if idx >= len(sct.monitors):
            idx = 1
        shot = sct.grab(sct.monitors[idx])
        img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
    w, h = img.size
    src = (CFG.get("ocr_source") or "map").lower()
    if src in ("chat", "chat_coords"):
        lf = float(CFG.get("chat_left_frac", 0))
        wf = float(CFG.get("chat_width_frac", 0.5))
        tf = float(CFG.get("chat_top_frac", 0.48))
        hf = float(CFG.get("chat_height_frac", 0.52))
        img = img.crop(
            (
                max(0, int(w * lf)),
                max(0, int(h * tf)),
                min(w, int(w * (lf + wf))),
                min(h, int(h * (tf + hf))),
            )
        )
        w, h = img.size
    else:
        cl = int(CFG.get("crop_left_px") or 0)
        if cl > 0 and w > cl + 320:
            img = img.crop((cl, 0, w, h))
            w, h = img.size
    max_w = int(CFG.get("ocr_max_width") or 1280)
    if w > max_w:
        img = img.resize((max_w, max(1, int(h * max_w / w))), Image.Resampling.LANCZOS)
    if CFG.get("save_ocr_debug"):
        debug_path = app_dir() / "last_ocr_shot.jpg"
        try:
            img.save(debug_path, format="JPEG", quality=85)
        except OSError:
            pass
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=82)
    return base64.standard_b64encode(buf.getvalue()).decode("ascii")


def _grab_hide() -> str:
    hidden = False
    if overlay and ui_visible:
        ev = threading.Event()

        def hide() -> None:
            overlay.withdraw()
            overlay.update_idletasks()
            ev.set()

        overlay.after(0, hide)
        ev.wait(3)
        hidden = True
        time.sleep(0.1)
    try:
        return grab_b64()
    finally:
        if hidden and overlay:

            def show() -> None:
                overlay.deiconify()
                overlay.lift()

            overlay.after(0, show)


def _last_xy_from_text(text: str) -> dict | None:
    if not text:
        return None
    matches = list(
        re.finditer(
            r"x\s*([0-9]+(?:\.[0-9]+)?)\s*,?\s*y\s*([0-9]+(?:\.[0-9]+)?)",
            text,
            re.I,
        )
    )
    if not matches:
        return None
    m = matches[-1]
    x, y = float(m.group(1)), float(m.group(2))
    if not (0 <= x <= 164 and 0 <= y <= 164):
        return None
    return {"x": x, "y": y}


def pick_point(d: dict, role: str) -> tuple[dict | None, str]:
    err = str(d.get("error") or "")
    p = d.get(role)
    if isinstance(p, dict) and p.get("x") is not None:
        return p, ""
    for p in d.get("points") or []:
        if isinstance(p, dict) and p.get("x") is not None:
            return p, ""
    parsed = _last_xy_from_text(d.get("ocr_text") or "")
    if parsed:
        return parsed, ""
    if API_URL and len(d.get("ocr_text") or "") > 3:
        try:
            r = requests.post(
                f"{API_URL}/api/parse-coords",
                json={"text": d["ocr_text"]},
                timeout=15,
            )
            if r.ok:
                pick = r.json().get("pick")
                if pick and pick.get("x") is not None:
                    return pick, ""
        except requests.RequestException:
            pass
    if err and not d.get("ocr_text"):
        return None, err[:100]
    return None, "未识别坐标：F12藏窗，十字对准地图点再按"


def _ocr_capture_mode() -> str:
    src = (CFG.get("ocr_source") or "map").lower()
    return "chat_coords" if src in ("chat", "chat_coords") else "crosshair_map"


def api_ocr(role: str) -> dict:
    try:
        r = requests.post(
            f"{API_URL}/api/vision/ocr",
            json={
                "image_base64": f"data:image/jpeg;base64,{_grab_hide()}",
                "role": role,
                "capture_mode": _ocr_capture_mode(),
            },
            timeout=90,
        )
        r.raise_for_status()
        return r.json()
    except requests.HTTPError as e:
        return {"error": f"HTTP {e.response.status_code}"}
    except requests.RequestException as e:
        return {"error": str(e)[:80]}


def api_calc() -> dict:
    payload = {
        "session_id": state["session_id"],
        "gun_x": state["gun"]["x"],
        "gun_y": state["gun"]["y"],
        "target_x": state["target"]["x"],
        "target_y": state["target"]["y"],
        "weapon_id": CFG.get("weapon", "spg"),
        "layer_asl": False,
        "layer_hull": False,
        "baseline_pitch": False,
    }
    arc = CFG.get("arc") or "auto"
    if arc in ("low", "high"):
        payload["preferred_arc"] = arc
    if gun_alt_var and gun_alt_var.get().strip():
        payload["gun_alt"] = float(gun_alt_var.get())
    if target_alt_var and target_alt_var.get().strip():
        payload["target_alt"] = float(target_alt_var.get())
    r = requests.post(f"{API_URL}/api/calculate", json=payload, timeout=60)
    r.raise_for_status()
    return r.json()


def _tip_text() -> str:
    dual = corr_mode_var and corr_mode_var.get() == "dual"
    if dual:
        return (
            "【实验·两发】F1炮→F2目→按诸元打目标→F3登记落点→"
            "炮口转约90°、RNG/MIL不变打检查点→F4登记；偏差可能较大。"
            " F12藏窗后仍会弹提示。"
        )
    return (
        "F1炮位 · F2目标 · F3单发落点 · F12藏窗 · 十字对准地图白字再按键。"
        " 未填ASL时SPH默认建议高支过障。"
    )


def refresh_tip_label() -> None:
    if tip_label:
        tip_label.config(text=_tip_text())


def sol_one_liner(sol: dict) -> str:
    az = sol.get("azimuth_deg")
    dist = sol.get("distance_m")
    pairs = sol.get("dial_pairs") or []
    spg = [p for p in pairs if p.get("arc") in ("low", "high") and p.get("sight_rng_m") is not None]
    parts = []
    if dist is not None:
        parts.append(f"距{dist}m")
    if az is not None:
        parts.append(f"方位{az}°")
    if len(spg) >= 2:
        chunks = []
        for p in spg:
            tag = "低支" if p["arc"] == "low" else "高支"
            chunks.append(f"{tag} RNG{p['sight_rng_m']}/MIL{p['mil']}")
        parts.append(" | ".join(chunks))
    else:
        rng, mil = sol.get("sight_rng_m"), sol.get("mil_dial")
        if rng is not None and mil is not None:
            parts.append(f"RNG {rng} · MIL {mil}")
        elif mil is not None:
            parts.append(f"MIL {mil}")
    eff = sol.get("effective_arc")
    if eff in ("low", "high") and len(spg) >= 2:
        hint = "首发建议高支" if eff == "high" else f"首发用{eff}"
        parts.append(hint)
    return " · ".join(parts) if parts else "诸元已更新"


def corr_one_liner(c: dict) -> str:
    az = c.get("new_azimuth_deg")
    pairs = c.get("correction_pairs") or []
    spg = [p for p in pairs if p.get("arc") in ("low", "high") and p.get("new_rng_m") is not None]
    if len(spg) >= 2:
        chunks = []
        for p in spg:
            tag = "低支" if p["arc"] == "low" else "高支"
            chunks.append(f"{tag} RNG→{p['new_rng_m']}/MIL→{p['new_mil']}")
        head = "修正："
        if az is not None:
            head += f"方位→{round(az, 1)}° · "
        return head + " | ".join(chunks)
    parts = []
    if az is not None:
        parts.append(f"方位→{round(az, 1)}°")
    if c.get("new_rng_m") is not None:
        parts.append(f"RNG→{c['new_rng_m']}")
    if c.get("new_mil") is not None:
        parts.append(f"MIL→{round(c['new_mil'])}")
    if not parts:
        return "已登记，偏差过小或无修正"
    return "修正：" + " · ".join(parts)


def fetch_check_sub() -> str:
    if not state.get("session_id"):
        return ""
    try:
        r = requests.post(
            f"{API_URL}/api/dual-shot/check-point",
            json={"session_id": state["session_id"], "offset_deg": CFG.get("dual_offset_deg", 90)},
            timeout=25,
        )
        if r.status_code == 404:
            return "（服务器未更新两发 API）"
        r.raise_for_status()
        cp = r.json().get("check_point") or {}
        return f"第二发瞄准检查点 x{cp.get('x')} y{cp.get('y')}"
    except requests.RequestException:
        return ""


def on_gun() -> None:
    set_lines("识别炮位…", "")
    d = api_ocr("gun")
    p, err = pick_point(d, "gun")
    if not p:
        set_lines("未识别炮位", err)
        return
    state["gun"] = p
    state["impact1"] = None
    set_lines("炮位 OK", f"x={p['x']}, y={p['y']}")


def on_target() -> None:
    if not state.get("gun"):
        set_lines("请先 F1 炮位", "")
        return
    set_lines("识别目标…", "")
    d = api_ocr("target")
    p, err = pick_point(d, "target")
    if not p:
        set_lines("未识别目标", err)
        return
    state["target"] = p
    set_lines("计算中…", "")
    data = api_calc()
    state["session_id"] = data.get("session_id")
    sol = data.get("solution") or {}
    sub = sol_one_liner(sol)
    if corr_mode_var and corr_mode_var.get() == "dual":
        ck = fetch_check_sub()
        if ck:
            sub = sub + " · " + ck if sub else ck
    set_lines("目标 OK · 按诸元试射", sub)


def _arc_payload() -> dict:
    a = CFG.get("arc") or "auto"
    return {"arc": a} if a in ("low", "high") else {}


def on_impact_single(p: dict) -> None:
    r = requests.post(
        f"{API_URL}/api/correct-impact",
        json={"session_id": state["session_id"], "impact_x": p["x"], "impact_y": p["y"], **_arc_payload()},
        timeout=60,
    )
    r.raise_for_status()
    c = r.json().get("correction") or r.json()
    ma = c.get("miss_analysis") or {}
    sub = ma.get("line_summary") or f"落点 x={p['x']}, y={p['y']}"
    set_lines(corr_one_liner(c), sub)


def _dual_merge(i1: dict, i2: dict) -> None:
    r = requests.post(
        f"{API_URL}/api/correct-dual-impact",
        json={
            "session_id": state["session_id"],
            "impact1_x": i1["x"],
            "impact1_y": i1["y"],
            "impact2_x": i2["x"],
            "impact2_y": i2["y"],
            "offset_deg": CFG.get("dual_offset_deg", 90),
            **_arc_payload(),
        },
        timeout=60,
    )
    if r.status_code == 404:
        set_lines("两发 API 未部署", "请更新 Render")
        return
    r.raise_for_status()
    c = r.json().get("correction") or {}
    set_lines(corr_one_liner(c), f"两发已合并")


def on_impact() -> None:
    if not state.get("session_id"):
        set_lines("请先 F1→F2", "")
        return
    read_mode()
    set_lines("识别落点…", "")
    d = api_ocr("impact")
    p, err = pick_point(d, "impact")
    if not p:
        set_lines("未识别落点", err)
        return
    if corr_mode_var and corr_mode_var.get() == "dual":
        if state.get("impact1") is None:
            state["impact1"] = p
            set_lines("第一发已登记", fetch_check_sub() or "请打检查点再 F4")
            return
        _dual_merge(state["impact1"], p)
        state["impact1"] = None
        return
    on_impact_single(p)


def on_impact2() -> None:
    read_mode()
    if corr_mode_var and corr_mode_var.get() != "dual":
        set_lines("请选「两发修正」", "")
        return
    if not state.get("impact1"):
        set_lines("请先 F3 登记第一发", "")
        return
    set_lines("识别第二发…", "")
    d = api_ocr("impact")
    p, err = pick_point(d, "impact")
    if not p:
        set_lines("未识别", err)
        return
    _dual_merge(state["impact1"], p)
    state["impact1"] = None


def toggle_ui() -> None:
    global ui_visible
    if not overlay:
        return

    def go() -> None:
        global ui_visible
        if ui_visible:
            overlay.withdraw()
            ui_visible = False
        else:
            overlay.deiconify()
            ui_visible = True

    ui_call(go)


def register_hotkeys() -> None:
    hk = CFG["hotkeys"]
    keyboard.add_hotkey(hk["gun"], lambda: run_bg(on_gun))
    keyboard.add_hotkey(hk["target"], lambda: run_bg(on_target))
    keyboard.add_hotkey(hk["impact"], lambda: run_bg(on_impact))
    keyboard.add_hotkey(hk.get("impact2", "f4"), lambda: run_bg(on_impact2))
    keyboard.add_hotkey(hk.get("toggle_ui", "f12"), toggle_ui)


def ensure_api_url() -> None:
    global API_URL, CFG
    if API_URL:
        return
    tmp = tk.Tk()
    tmp.withdraw()
    url = simpledialog.askstring("FireMind", "粘贴公网地址", parent=tmp)
    tmp.destroy()
    if not url:
        sys.exit(1)
    API_URL = url.strip().rstrip("/")
    CFG["api_url"] = API_URL
    save_config(CFG)


def build_ui(root: tk.Tk) -> None:
    global main_var, sub_var, corr_mode_var, gun_alt_var, target_alt_var, tip_label

    root.title("FireMind")
    root.attributes("-topmost", True)
    root.geometry("440x210+24+24")
    root.configure(bg="#1a1d24")

    frm = tk.Frame(root, bg="#1a1d24", padx=10, pady=8)
    frm.pack(fill="both", expand=True)

    row1 = tk.Frame(frm, bg="#1a1d24")
    row1.pack(fill="x")
    corr_mode_var = tk.StringVar(value=CFG.get("correction_mode", "single"))
    tk.Radiobutton(
        row1, text="单发 F3", variable=corr_mode_var, value="single", command=read_mode,
        bg="#1a1d24", fg="#e8eaed", selectcolor="#333", activebackground="#1a1d24",
    ).pack(side="left")
    tk.Radiobutton(
        row1, text="两发 F3+F4", variable=corr_mode_var, value="dual", command=read_mode,
        bg="#1a1d24", fg="#e8eaed", selectcolor="#333", activebackground="#1a1d24",
    ).pack(side="left", padx=(8, 0))
    tk.Label(row1, text="F12隐藏", bg="#1a1d24", fg="#888", font=("Segoe UI", 8)).pack(side="right")

    row2 = tk.Frame(frm, bg="#1a1d24")
    row2.pack(fill="x", pady=(6, 0))
    tk.Label(row2, text="炮ASL", bg="#1a1d24", fg="#aaa", font=("Segoe UI", 8)).pack(side="left")
    gun_alt_var = tk.StringVar()
    tk.Entry(row2, textvariable=gun_alt_var, width=6, bg="#12151a", fg="#eee", relief="flat").pack(side="left", padx=4)
    tk.Label(row2, text="目ASL", bg="#1a1d24", fg="#aaa", font=("Segoe UI", 8)).pack(side="left")
    target_alt_var = tk.StringVar()
    tk.Entry(row2, textvariable=target_alt_var, width=6, bg="#12151a", fg="#eee", relief="flat").pack(side="left", padx=4)
    tk.Label(row2, text="(可选)", bg="#1a1d24", fg="#666", font=("Segoe UI", 8)).pack(side="left")

    main_var = tk.StringVar(value="F1炮 F2目 F3/F4落点")
    sub_var = tk.StringVar(value="就绪")
    tip_label = tk.Label(
        frm, text=_tip_text(), bg="#1a1d24", fg="#9aa3ad",
        font=("Microsoft YaHei UI", 9), wraplength=420, justify="left",
    )
    tip_label.pack(anchor="w", pady=(6, 0))
    tk.Label(frm, textvariable=main_var, bg="#1a1d24", fg="#fff", font=("Consolas", 10, "bold"), wraplength=420, justify="left").pack(
        anchor="w", pady=(6, 2)
    )
    tk.Label(frm, textvariable=sub_var, bg="#1a1d24", fg="#9aa0a6", font=("Segoe UI", 9), wraplength=420, justify="left").pack(anchor="w")


def main() -> None:
    global overlay
    ensure_api_url()
    overlay = tk.Tk()
    build_ui(overlay)
    register_hotkeys()
    overlay.mainloop()


if __name__ == "__main__":
    main()
