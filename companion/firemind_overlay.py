#!/usr/bin/env python3
"""FireMind 桌面助手 — 网页同款选项 + F1/F2/F3/F4 识图 + 可隐藏悬浮窗。"""
from __future__ import annotations

import base64
import io
import json
import os
import sys
import threading
import time
import tkinter as tk
from tkinter import messagebox, simpledialog, ttk
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

BG = "#1a1d24"
FG = "#e8eaed"
ACCENT = "#5a9fd4"
MUTED = "#9aa0a6"


def app_dir() -> Path:
    return Path(sys.executable).parent if getattr(sys, "frozen", False) else Path(__file__).parent


def load_config() -> dict:
    path = app_dir() / "config.json"
    data = json.loads(path.read_text(encoding="utf-8")) if path.is_file() else {}
    if os.environ.get("FIREMIND_API_URL"):
        data["api_url"] = os.environ["FIREMIND_API_URL"]
    elif not data.get("api_url") and DEFAULT_API_URL:
        data["api_url"] = DEFAULT_API_URL
    data.setdefault("weapon", "mortar")
    data.setdefault("arc", "auto")
    data.setdefault("monitor", 1)
    data.setdefault("crop_left_px", 460)
    data.setdefault("ocr_max_px", 1600)
    data.setdefault("correction_mode", "single")
    data.setdefault("show_parallel_dials", True)
    data.setdefault("use_altitude_hint", True)
    hk = {**DEFAULT_HOTKEYS, **(data.get("hotkeys") or {})}
    data["hotkeys"] = hk
    return data


def save_config(data: dict) -> None:
    out = {k: v for k, v in data.items() if not k.startswith("_")}
    (app_dir() / "config.json").write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding="utf-8")


CFG = load_config()
API_URL = (CFG.get("api_url") or "").rstrip("/")
state: dict = {"session_id": None, "gun": None, "target": None, "impact1": None}
overlay: tk.Tk | None = None
ui_visible = True

# UI refs filled in build_ui
weapon_var: tk.StringVar | None = None
arc_var: tk.StringVar | None = None
corr_mode_var: tk.StringVar | None = None
parallel_var: tk.BooleanVar | None = None
alt_hint_var: tk.BooleanVar | None = None
gun_alt_var: tk.StringVar | None = None
target_alt_var: tk.StringVar | None = None
status_var: tk.StringVar | None = None
coords_var: tk.StringVar | None = None
solution_text: tk.Text | None = None
arc_frame: ttk.Frame | None = None
sph_extra: ttk.LabelFrame | None = None


def ui_call(fn) -> None:
    if overlay:
        overlay.after(0, fn)


def set_status(title: str, detail: str = "") -> None:
    def go() -> None:
        if status_var:
            status_var.set(title)
        if coords_var and detail:
            coords_var.set(detail)

    ui_call(go)


def run_bg(fn) -> None:
    def worker() -> None:
        try:
            fn()
        except Exception as e:
            set_status("错误", str(e))


def _safe(fn) -> None:
    try:
        fn()
    except Exception as e:
        set_status("错误", str(e))


def read_settings() -> None:
    if weapon_var:
        CFG["weapon"] = weapon_var.get()
    if arc_var:
        CFG["arc"] = arc_var.get()
    if corr_mode_var:
        CFG["correction_mode"] = corr_mode_var.get()
    if parallel_var:
        CFG["show_parallel_dials"] = parallel_var.get()
    if alt_hint_var:
        CFG["use_altitude_hint"] = alt_hint_var.get()
    save_config(CFG)


def sync_weapon_ui() -> None:
    spg = weapon_var.get() == "spg" if weapon_var else False
    if arc_frame:
        arc_frame.pack_forget()
        if spg:
            arc_frame.pack(fill="x", pady=(0, 6))
    if sph_extra:
        sph_extra.pack_forget()
        if spg:
            sph_extra.pack(fill="x", pady=(0, 6))
    read_settings()


def read_settings() -> None:
    CFG["weapon"] = weapon_var.get() if weapon_var else CFG.get("weapon", "mortar")
    CFG["arc"] = arc_var.get() if arc_var else "auto"
    CFG["correction_mode"] = corr_mode_var.get() if corr_mode_var else "single"
    CFG["show_parallel_dials"] = bool(parallel_var.get()) if parallel_var else True
    CFG["use_altitude_hint"] = bool(alt_hint_var.get()) if alt_hint_var else True
    save_config(CFG)


def sync_weapon_ui() -> None:
    spg = weapon_var.get() == "spg" if weapon_var else False
    if arc_frame:
        arc_frame.pack_forget()
        if spg:
            arc_frame.pack(fill="x", pady=(0, 6))
    if sph_extra:
        sph_extra.pack_forget()
        if spg:
            sph_extra.pack(fill="x", pady=(0, 6))
    read_settings()


def grab_b64() -> str:
    idx = max(1, int(CFG.get("monitor", 1)))
    with mss.mss() as sct:
        if idx >= len(sct.monitors):
            idx = 1
        shot = sct.grab(sct.monitors[idx])
        img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
    w, h = img.size
    crop_left = int(CFG.get("crop_left_px") or 0)
    if crop_left > 0 and w > crop_left + 320:
        img = img.crop((crop_left, 0, w, h))
    max_px = int(CFG.get("ocr_max_px") or 1600)
    if max(img.size) > max_px:
        img.thumbnail((max_px, max_px), Image.Resampling.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=90)
    b64 = base64.standard_b64encode(buf.getvalue()).decode("ascii")
    if CFG.get("debug_ocr_save"):
        try:
            (app_dir() / "debug_last.jpg").write_bytes(buf.getvalue())
        except OSError:
            pass
    return b64


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
        time.sleep(0.12)
    try:
        return grab_b64()
    finally:
        if hidden and overlay:

            def show() -> None:
                overlay.deiconify()
                overlay.lift()

            overlay.after(0, show)


def pick_point(d: dict, role: str) -> tuple[dict | None, str]:
    if d.get("error"):
        err = str(d["error"])
        if d.get("model_preview"):
            err += " | " + str(d["model_preview"])[:120]
        return None, err
    p = d.get(role)
    if isinstance(p, dict) and p.get("x") is not None:
        return p, ""
    for p in d.get("points") or []:
        if isinstance(p, dict) and p.get("x") is not None:
            return p, ""
    hint = "地图占满屏幕或先 F12 隐藏本窗再按 F1；双屏改 config monitor"
    if d.get("points") == []:
        return None, f"OCR 未读到坐标（{hint}）"
    return None, hint


def _grab_with_optional_hide() -> str:
    """截屏前短暂隐藏小窗，避免左侧黑 UI 干扰识图。"""
    hidden = False
    if overlay and ui_visible:
        evt = threading.Event()

        def _hide() -> None:
            overlay.withdraw()
            overlay.update_idletasks()
            evt.set()

        overlay.after(0, _hide)
        evt.wait(timeout=3)
        hidden = True
        time.sleep(0.12)
    try:
        return grab_b64()
    finally:
        if hidden and overlay:

            def _show() -> None:
                overlay.deiconify()
                overlay.lift()

            overlay.after(0, _show)


def api_ocr(role: str) -> dict:
    try:
        r = requests.post(
            f"{API_URL}/api/vision/ocr",
            json={
                "image_base64": f"data:image/jpeg;base64,{_grab_with_optional_hide()}",
                "role": role,
                "capture_mode": "crosshair_map",
            },
            timeout=90,
        )
        r.raise_for_status()
        return r.json()
    except requests.HTTPError as e:
        body = ""
        try:
            body = e.response.text[:200]
        except Exception:
            pass
        return {"error": f"HTTP {e.response.status_code}: {body or e}"}
    except requests.RequestException as e:
        return {"error": f"网络: {e}"}


def _optional_float(s: str) -> float | None:
    s = (s or "").strip()
    if not s:
        return None
    return float(s)


def api_calc() -> dict:
    read_settings()
    payload = {
        "session_id": state["session_id"],
        "gun_x": state["gun"]["x"],
        "gun_y": state["gun"]["y"],
        "target_x": state["target"]["x"],
        "target_y": state["target"]["y"],
        "weapon_id": CFG["weapon"],
    }
    arc = CFG.get("arc") or "auto"
    if arc in ("low", "high"):
        payload["preferred_arc"] = arc
    ga = _optional_float(gun_alt_var.get() if gun_alt_var else "")
    ta = _optional_float(target_alt_var.get() if target_alt_var else "")
    if ga is not None:
        payload["gun_alt"] = ga
    if ta is not None:
        payload["target_alt"] = ta
    r = requests.post(f"{API_URL}/api/calculate", json=payload, timeout=60)
    r.raise_for_status()
    return r.json()


def format_solution(data: dict) -> str:
    sol = data.get("solution") or {}
    if not sol:
        return data.get("reply") or "无诸元"
    lines = [
        f"{sol.get('weapon_name', '')} · 方位 {sol.get('azimuth_deg')}° · 距离 {sol.get('distance_m')} m",
        f"本次：RNG {sol.get('sight_rng_m')} · MIL {sol.get('mil_dial')}",
    ]
    if sol.get("effective_arc"):
        lines.append(f"弹道：{'高' if sol['effective_arc'] == 'high' else '低'}")
    if CFG.get("weapon") == "spg" and CFG.get("show_parallel_dials"):
        for p in sol.get("dial_pairs") or []:
            arc = p.get("arc")
            if arc in ("low", "high") and arc != sol.get("effective_arc"):
                lines.append(f"  另一弹道({arc})：RNG {p.get('sight_rng_m')} · MIL {p.get('mil')}")
    for w in (sol.get("warnings") or [])[:2]:
        lines.append(f"⚠ {w}")
    if data.get("reply"):
        lines.append("")
        lines.append(str(data["reply"])[:800])
    return "\n".join(lines)


def show_solution(text: str) -> None:
    def go() -> None:
        if solution_text:
            solution_text.delete("1.0", tk.END)
            solution_text.insert(tk.END, text)

    ui_call(go)


def format_correction(c: dict) -> str:
    lines = ["── 试射修正 ──"]
    ma = c.get("miss_analysis")
    if ma:
        lines.append(ma.get("map_summary", ""))
        lines.append(ma.get("line_summary", ""))
    parts = []
    if c.get("new_azimuth_deg") is not None:
        parts.append(f"方位 {c['new_azimuth_deg']}°")
    if c.get("new_rng_m") is not None:
        parts.append(f"RNG {c['new_rng_m']} m")
    if c.get("new_mil") is not None:
        parts.append(f"MIL {round(c['new_mil'])}")
    if parts:
        lines.append("建议：" + " · ".join(parts))
    if CFG.get("use_altitude_hint") and c.get("altitude_hint"):
        ah = c["altitude_hint"]
        lines.append(f"海拔粗估：目标约 {ah.get('target_alt_hint_m')} m（低置信度）")
    for s in c.get("steps") or []:
        lines.append(f"• {s}")
    return "\n".join(lines)


def append_solution_block(text: str) -> None:
    def go() -> None:
        if solution_text:
            solution_text.insert(tk.END, "\n\n" + text)

    ui_call(go)


def on_gun() -> None:
    set_status("识别炮位…", "截屏中…")
    d = api_ocr("gun")
    p, err = pick_point(d, "gun")
    if not p:
        set_status("未识别炮位", err)
        return
    state["gun"] = p
    state["impact1"] = None
    set_status("炮位 OK", f"x={p['x']}, y={p['y']}")


def on_target() -> None:
    if not state.get("gun"):
        set_status("请先 F1 炮位")
        return
    set_status("识别目标…", "截屏中…")
    d = api_ocr("target")
    p, err = pick_point(d, "target")
    if not p:
        set_status("未识别目标", err)
        return
    state["target"] = p
    set_status("计算诸元…")
    data = api_calc()
    state["session_id"] = data.get("session_id")
    show_solution(format_solution(data))
    set_status("目标 OK · 诸元已更新", f"目标 x={p['x']}, y={p['y']}")


def _arc_payload() -> dict:
    arc = CFG.get("arc") or "auto"
    return {"arc": arc} if arc in ("low", "high") else {}


def on_impact_single(p: dict) -> None:
    r = requests.post(
        f"{API_URL}/api/correct-impact",
        json={
            "session_id": state["session_id"],
            "impact_x": p["x"],
            "impact_y": p["y"],
            **_arc_payload(),
        },
        timeout=60,
    )
    if r.status_code == 404:
        set_status("两发 API 404", "请更新 Render 上 app.py + dual_shot.py")
        return
    r.raise_for_status()
    c = r.json().get("correction") or {}
    append_solution_block(format_correction(c))
    set_status(
        f"修正 RNG→{c.get('new_rng_m')} MIL→{round(c.get('new_mil') or 0)}",
        f"落点 x={p['x']}, y={p['y']}",
    )


def on_impact() -> None:
    if not state.get("session_id"):
        set_status("请先 F1 炮位、F2 目标")
        return
    mode = corr_mode_var.get() if corr_mode_var else "single"
    set_status("识别落点…")
    d = api_ocr("impact")
    p, err = pick_point(d, "impact")
    if not p:
        set_status("未识别落点", err)
        return
    read_settings()
    if mode == "dual":
        if state.get("impact1") is None:
            state["impact1"] = p
            set_status(
                "第一发已登记",
                f"请转≥30°到检查点，按 {CFG['hotkeys'].get('impact2', 'f4').upper()} 登记第二发",
            )
            return
        i1 = state["impact1"]
        r = requests.post(
            f"{API_URL}/api/correct-dual-impact",
            json={
                "session_id": state["session_id"],
                "impact1_x": i1["x"],
                "impact1_y": i1["y"],
                "impact2_x": p["x"],
                "impact2_y": p["y"],
                **_arc_payload(),
            },
            timeout=60,
        )
        r.raise_for_status()
        c = r.json().get("correction") or {}
        state["impact1"] = None
        append_solution_block(format_correction(c))
        set_status("两发修正完成", f"② x={p['x']}, y={p['y']}")
        return
    on_impact_single(p)


def on_impact2() -> None:
    if (corr_mode_var.get() if corr_mode_var else "single") != "dual":
        set_status("当前为单发模式", "请在界面选「两发登记」")
        return
    if not state.get("impact1"):
        set_status("请先 F3 登记第一发落点")
        return
    on_impact()


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
            overlay.lift()
            ui_visible = True

    ui_call(go)


def register_hotkeys() -> None:
    hk = CFG.get("hotkeys") or DEFAULT_HOTKEYS
    keyboard.add_hotkey(hk.get("gun", "f1"), lambda: run_bg(on_gun))
    keyboard.add_hotkey(hk.get("target", "f2"), lambda: run_bg(on_target))
    keyboard.add_hotkey(hk.get("impact", "f3"), lambda: run_bg(on_impact))
    keyboard.add_hotkey(hk.get("impact2", "f4"), lambda: run_bg(on_impact2))
    keyboard.add_hotkey(hk.get("toggle_ui", "f12"), lambda: toggle_ui())


def ensure_api_url() -> None:
    global API_URL, CFG
    if API_URL:
        return
    tmp = tk.Tk()
    tmp.withdraw()
    url = simpledialog.askstring("FireMind", "粘贴 FireMind 公网地址", parent=tmp)
    tmp.destroy()
    if not url:
        sys.exit(1)
    API_URL = url.strip().rstrip("/")
    CFG["api_url"] = API_URL
    save_config(CFG)


def build_ui(root: tk.Tk) -> None:
    global weapon_var, arc_var, corr_mode_var, parallel_var, alt_hint_var
    global gun_alt_var, target_alt_var, status_var, coords_var, solution_text
    global arc_frame, sph_extra

    root.configure(bg=BG)
    root.title("FireMind 助手")
    root.attributes("-topmost", True)
    root.geometry("440x680+32+32")

    style = ttk.Style()
    style.theme_use("clam")
    style.configure("TFrame", background=BG)
    style.configure("TLabel", background=BG, foreground=FG)
    style.configure("TLabelframe", background=BG, foreground=FG)
    style.configure("TLabelframe.Label", background=BG, foreground=ACCENT)
    style.configure("TRadiobutton", background=BG, foreground=FG)
    style.configure("TCheckbutton", background=BG, foreground=FG)

    top = ttk.Frame(root, padding=8)
    top.pack(fill="x")
    ttk.Label(top, text="FireMind", font=("Segoe UI", 14, "bold")).pack(side="left")
    ttk.Button(top, text="隐藏窗口 (F12)", command=toggle_ui).pack(side="right")

    hk = CFG.get("hotkeys") or DEFAULT_HOTKEYS
    ttk.Label(
        root,
        text=f"F1炮位 F2目标 F3落点 F4第二发(两发模式) · 隐藏不影响快捷键",
        font=("Segoe UI", 9),
        foreground=MUTED,
    ).pack(padx=10, anchor="w")

    body = ttk.Frame(root, padding=10)
    body.pack(fill="both", expand=True)

    wpn = ttk.LabelFrame(body, text="武器", padding=8)
    wpn.pack(fill="x", pady=(0, 6))
    weapon_var = tk.StringVar(value=CFG.get("weapon", "mortar"))
    ttk.Radiobutton(wpn, text="L81 迫击炮", variable=weapon_var, value="mortar", command=sync_weapon_ui).pack(
        anchor="w"
    )
    ttk.Radiobutton(wpn, text="SPH-2 攀枝花", variable=weapon_var, value="spg", command=sync_weapon_ui).pack(
        anchor="w"
    )

    arc_frame = ttk.LabelFrame(body, text="SPH 弹道", padding=8)
    arc_var = tk.StringVar(value=CFG.get("arc", "auto"))
    for val, label in (
        ("auto", "自动"),
        ("high", "高弹道"),
        ("low", "低弹道"),
    ):
        ttk.Radiobutton(arc_frame, text=label, variable=arc_var, value=val, command=read_settings).pack(anchor="w")

    sph_extra = ttk.LabelFrame(body, text="SPH 显示 / 修正", padding=8)
    parallel_var = tk.BooleanVar(value=CFG.get("show_parallel_dials", True))
    alt_hint_var = tk.BooleanVar(value=CFG.get("use_altitude_hint", True))
    ttk.Checkbutton(
        sph_extra,
        text="并行显示另一弹道 RNG/MIL（勿混用）",
        variable=parallel_var,
        command=read_settings,
    ).pack(anchor="w")
    ttk.Checkbutton(
        sph_extra,
        text="试射修正显示海拔粗估",
        variable=alt_hint_var,
        command=read_settings,
    ).pack(anchor="w")
    ttk.Label(
        sph_extra,
        text="迫击炮无车身修正；仅 SPH 需切游戏内高/低弹道。",
        foreground=MUTED,
        font=("Segoe UI", 8),
    ).pack(anchor="w", pady=(4, 0))

    alt_fr = ttk.LabelFrame(body, text="海拔（可选）", padding=8)
    alt_fr.pack(fill="x", pady=(0, 6))
    row = ttk.Frame(alt_fr)
    row.pack(fill="x")
    gun_alt_var = tk.StringVar()
    target_alt_var = tk.StringVar()
    ttk.Label(row, text="炮位 ASL").grid(row=0, column=0, sticky="w")
    ttk.Entry(row, textvariable=gun_alt_var, width=10).grid(row=0, column=1, padx=4)
    ttk.Label(row, text="目标 ASL").grid(row=0, column=2, sticky="w", padx=(8, 0))
    ttk.Entry(row, textvariable=target_alt_var, width=10).grid(row=0, column=3, padx=4)

    corr_fr = ttk.LabelFrame(body, text="试射修正模式", padding=8)
    corr_fr.pack(fill="x", pady=(0, 6))
    corr_mode_var = tk.StringVar(value=CFG.get("correction_mode", "single"))
    ttk.Radiobutton(
        corr_fr,
        text="单发：F3 识别落点并修正",
        variable=corr_mode_var,
        value="single",
        command=read_settings,
    ).pack(anchor="w")
    ttk.Radiobutton(
        corr_fr,
        text="两发：F3 第一发 → 转检查点 → F4/F3 第二发合并",
        variable=corr_mode_var,
        value="dual",
        command=read_settings,
    ).pack(anchor="w")

    status_var = tk.StringVar(value="就绪")
    coords_var = tk.StringVar(value=API_URL)
    ttk.Label(body, textvariable=status_var, font=("Consolas", 11, "bold")).pack(anchor="w", pady=(4, 0))
    ttk.Label(body, textvariable=coords_var, foreground=MUTED, wraplength=400).pack(anchor="w")

    sol_fr = ttk.LabelFrame(body, text="诸元 / 修正", padding=4)
    sol_fr.pack(fill="both", expand=True, pady=(8, 0))
    solution_text = tk.Text(
        sol_fr,
        height=12,
        wrap="word",
        bg="#12151a",
        fg=FG,
        font=("Consolas", 10),
        relief="flat",
    )
    solution_text.pack(fill="both", expand=True)
    solution_text.insert(tk.END, "F1 炮位 → F2 目标后此处显示诸元。")

    sync_weapon_ui()


def main() -> None:
    global overlay
    ensure_api_url()
    overlay = tk.Tk()
    build_ui(overlay)
    register_hotkeys()
    overlay.mainloop()


if __name__ == "__main__":
    main()
