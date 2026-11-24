"""校园网助手 · Tk 版（macOS Vibrancy 风格）

纯 tkinter 实现，无 WebView、无本地 HTTP 服务：界面直接读 Engine 状态，
常驻内存约 40 MB，空闲 CPU ≈ 0。

配色严格使用三级灰：#1c1c1e / #2c2c2e / #3a3a3c，
无渐变、无发光、无大面积阴影、无描边，层级全靠色差；圆角最大 12px。
"""

from __future__ import annotations

import ctypes
import os
import sys
import threading
import time
import tkinter as tk
from tkinter import font as tkfont

import store
import tray as tray_mod
from engine import Engine

# ---------------------------------------------------------------- 调色板
DEEP = "#1c1c1e"        # 侧栏
MID = "#2c2c2e"         # 中栏 / 卡片
SURFACE = "#3a3a3c"     # 内容区 / 按钮
ACCENT = "#0a84ff"      # 唯一强调色（macOS 蓝）
DANGER = "#ff453a"

WHITE = "#ffffff"


def blend(bg: str, alpha: float) -> str:
    """把白色按 alpha 叠到 bg 上，模拟 white/xx 的透明度。"""
    bg = bg.lstrip("#")
    r, g, b = (int(bg[i:i + 2], 16) for i in (0, 2, 4))
    nr = round(r + (255 - r) * alpha)
    ng = round(g + (255 - g) * alpha)
    nb = round(b + (255 - b) * alpha)
    return f"#{nr:02x}{ng:02x}{nb:02x}"


def hover(bg: str) -> str:
    return blend(bg, 0.05)


def active(bg: str) -> str:
    return blend(bg, 0.09)


# 文本三档：95% / 70% / 40% 白
T_PRIMARY = blend(MID, 0.95)
T_SECOND = blend(MID, 0.70)
T_MUTED = blend(MID, 0.40)
T_PRIMARY_ON_DEEP = blend(DEEP, 0.95)
T_SECOND_ON_DEEP = blend(DEEP, 0.70)
T_MUTED_ON_DEEP = blend(DEEP, 0.40)
T_PRIMARY_ON_SURFACE = blend(SURFACE, 0.95)
T_SECOND_ON_SURFACE = blend(SURFACE, 0.70)
T_MUTED_ON_SURFACE = blend(SURFACE, 0.40)

F_TITLE = ("SimSun", 20)            # 宋体＝中文衬线，对应 serif 标题
F_HEAD = ("SimSun", 15)
F_BODY = ("Microsoft YaHei UI", 10)
F_SMALL = ("Microsoft YaHei UI", 9)
F_MONO = ("Consolas", 9)
F_SERIF_LATIN = ("Georgia", 11)

RADIUS = 12


# ---------------------------------------------------------------- 组件
class RoundRect(tk.Canvas):
    """圆角矩形按钮：无描边，靠底色差异区分状态。"""

    def __init__(self, master, text, command=None, *, bg=MID, fg=None,
                 fill=SURFACE, width=120, height=34, radius=8,
                 font=None, accent=False, danger=False):
        super().__init__(master, width=width, height=height, bg=bg,
                         highlightthickness=0, bd=0, takefocus=0)
        self._bg = bg
        self._fill = fill
        self._hover = hover(fill)
        self._press = active(fill)
        self._accent = accent
        self._danger = danger
        self._radius = radius
        self._text = text
        self._command = command
        self._font = font or F_BODY
        self._fg = fg or (WHITE if accent else T_PRIMARY_ON_SURFACE)
        self._draw(self._fill)
        self.bind("<Enter>", lambda e: self._draw(self._hover))
        self.bind("<Leave>", lambda e: self._draw(self._fill))
        self.bind("<ButtonPress-1>", lambda e: self._draw(self._press))
        self.bind("<ButtonRelease-1>", self._release)

    def _rr(self, x1, y1, x2, y2, r):
        pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
               x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
               x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
        return self.create_polygon(pts, smooth=True, splinesteps=24, fill=self._cur)

    def _draw(self, color):
        self._cur = color
        self.delete("all")
        w = int(self["width"])
        h = int(self["height"])
        self._rr(0, 0, w - 1, h - 1, self._radius)
        self.create_text(w // 2, h // 2 + 1, text=self._text, fill=self._fg, font=self._font)

    def _release(self, event):
        inside = 0 <= event.x <= int(self["width"]) and 0 <= event.y <= int(self["height"])
        self._draw(self._hover if inside else self._fill)
        if inside and self._command:
            self._command()

    def set_text(self, text: str) -> None:
        self._text = text
        self._draw(self._fill)

    def set_enabled(self, enabled: bool) -> None:
        self._command = self._command if enabled else None
        self._fg = (WHITE if self._accent else T_PRIMARY_ON_SURFACE) if enabled else T_MUTED_ON_SURFACE


class Toggle(tk.Canvas):
    """开关：圆角矩形轨道 + 方形圆角滑块（不用 rounded-full）。"""

    def __init__(self, master, value: bool, command=None, *, bg=MID,
                 width=44, height=24):
        super().__init__(master, width=width, height=height, bg=bg,
                         highlightthickness=0, bd=0, takefocus=0)
        self._on = value
        self._command = command
        self._bg = bg
        self._cw, self._ch = width, height
        self.bind("<Button-1>", self._toggle)
        self.bind("<Enter>", lambda e: self._paint(True))
        self.bind("<Leave>", lambda e: self._paint(False))
        self._paint()

    def _paint(self, hovering=False):
        self.delete("all")
        track = ACCENT if self._on else (hover(SURFACE) if hovering else SURFACE)
        self._rr(0, 2, self._cw - 1, self._ch - 3, 10, track)
        knob_w = 18
        x = self._cw - knob_w - 4 if self._on else 4
        self._rr(x, 4, x + knob_w, self._ch - 5, 8, WHITE)

    def _rr(self, x1, y1, x2, y2, r, color):
        pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
               x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
               x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
        self.create_polygon(pts, smooth=True, splinesteps=24, fill=color)

    def _toggle(self, _event=None):
        self._on = not self._on
        self._paint()
        if self._command:
            self._command(self._on)

    @property
    def value(self) -> bool:
        return self._on


class Slider(tk.Canvas):
    """横向滑杆：轨道 + 已选段 + 圆角滑块。"""

    def __init__(self, master, value, minimum=5, maximum=180, step=5,
                 command=None, bg=MID, width=260):
        super().__init__(master, width=width, height=22, bg=bg,
                         highlightthickness=0, bd=0, takefocus=0)
        self._min, self._max, self._step = minimum, maximum, step
        self._value = max(minimum, min(maximum, int(value)))
        self._command = command
        self._cw = width
        self.bind("<Button-1>", self._drag)
        self.bind("<B1-Motion>", self._drag)
        self._paint()

    def _paint(self):
        self.delete("all")
        y = 11
        self.create_rectangle(0, y - 2, self._cw, y + 2, fill=SURFACE, outline="")
        ratio = (self._value - self._min) / max(1, self._max - self._min)
        x = int(ratio * (self._cw - 12)) + 6
        self.create_rectangle(0, y - 2, x, y + 2, fill=ACCENT, outline="")
        self.create_rectangle(x - 5, y - 6, x + 5, y + 6, fill=WHITE, outline="")

    def _drag(self, event):
        ratio = max(0.0, min(1.0, (event.x - 6) / max(1, self._cw - 12)))
        raw = self._min + ratio * (self._max - self._min)
        step = self._step
        self._value = int(round(raw / step) * step)
        self._value = max(self._min, min(self._max, self._value))
        self._paint()
        if self._command:
            self._command(self._value)

    @property
    def value(self) -> int:
        return self._value


class NavItem(tk.Frame):
    """侧栏导航项：悬停/选中只变底色。"""

    def __init__(self, master, text, command, *, active=False):
        super().__init__(master, bg=DEEP, height=32)
        self.pack_propagate(False)
        self._command = command
        self._active = active
        self.label = tk.Label(self, text=text, anchor="w", bg=DEEP,
                              fg=T_PRIMARY_ON_DEEP if active else T_SECOND_ON_DEEP,
                              font=F_BODY, padx=12, pady=6)
        self.label.pack(fill="both", expand=True)
        for w in (self, self.label):
            w.bind("<Button-1>", lambda e: self._command())
            w.bind("<Enter>", self._enter)
            w.bind("<Leave>", self._leave)
        self._paint()

    def _paint(self):
        bg = blend(DEEP, 0.10) if self._active else DEEP
        self.configure(bg=bg)
        self.label.configure(bg=bg, fg=T_PRIMARY_ON_DEEP if self._active else T_SECOND_ON_DEEP)

    def _enter(self, _e):
        if not self._active:
            self.configure(bg=blend(DEEP, 0.05))
            self.label.configure(bg=blend(DEEP, 0.05), fg=T_PRIMARY_ON_DEEP)

    def _leave(self, _e):
        self._paint()

    def set_active(self, active: bool):
        self._active = active
        self._paint()


# ---------------------------------------------------------------- 主界面
class App(tk.Tk):
    def __init__(self, engine: Engine, start_hidden: bool = False):
        super().__init__()
        self.engine = engine
        self.withdraw()
        self.overrideredirect(True)
        self.configure(bg=DEEP)
        self.geometry("1080x700+%d+%d" % self._center(1080, 700))
        self._drag = None
        self._views: dict[str, tk.Frame] = {}
        self._navs: dict[str, NavItem] = {}
        self._current = "overview"
        self._sig = None
        self._last_seq = 0
        self._tray = None

        self._build()
        self.after(120, self._finish_show, start_hidden)
        self._start_tray()
        threading.Thread(target=engine.run_forever, daemon=True).start()
        self._tick()

    # ------------------------------------------------------------ 窗口
    def _center(self, w, h):
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        return max(0, (sw - w) // 2), max(0, (sh - h) // 2 - 20)

    def _apply_rounding(self):
        try:
            self.update_idletasks()
            hwnd = ctypes.windll.user32.GetParent(self.winfo_id())
            w = max(200, self.winfo_width())
            h = max(200, self.winfo_height())
            rgn = ctypes.windll.gdi32.CreateRoundRectRgn(0, 0, w + 1, h + 1,
                                                         RADIUS * 2, RADIUS * 2)
            ctypes.windll.user32.SetWindowRgn(hwnd, rgn, True)
        except Exception:                               # noqa: BLE001
            pass

    def _finish_show(self, start_hidden: bool):
        self.title("校园网助手")
        if not start_hidden:
            self.deiconify()
            self.lift()
            self.update_idletasks()
            self._apply_rounding()
        else:
            self._tray_notify("校园网助手已启动", "在右下角托盘里，右键可操作")

    def show_window(self):
        self.deiconify()
        self.lift()
        try:
            self.focus_force()
        except Exception:                               # noqa: BLE001
            pass

    def _start_tray(self):
        def state_fn():
            st = self.engine.state
            key = "online" if st.get("authenticated") else {
                "offline": "offline", "error": "error"}.get(st.get("phase"), "idle")
            tip = f"校园网助手 · {'已认证' if key == 'online' else '待认证'} · {st.get('ip') or '—'}"
            return key, tip, bool(self.engine.cfg.get("auto_login"))

        def open_win():
            self.after(0, self.show_window)

        def do_check():
            threading.Thread(target=self.engine.check, daemon=True).start()

        def do_login():
            threading.Thread(target=self.engine.login, daemon=True).start()

        def toggle_auto():
            self.after(0, lambda: self._toggle_auto_from_tray())

        def quit_app():
            self.after(0, self.quit_app)

        try:
            self._tray = tray_mod.Tray(
                os.path.join(store.APP_DIR, "assets"), state_fn,
                open_win, do_check, do_login, toggle_auto, quit_app,
                log=self.engine.log)
            self._tray.start()
        except Exception as exc:                        # noqa: BLE001
            self.engine.log(f"托盘初始化失败：{exc}", "warn")

    def _tray_notify(self, title, text):
        if self._tray is not None:
            try:
                self._tray.notify(title, text)
            except Exception:                           # noqa: BLE001
                pass

    def _toggle_auto_from_tray(self):
        self.engine.update_config({"auto_login": not self.engine.cfg.get("auto_login")})
        if self._current == "settings":
            self._sync_settings()
        self._refresh()

    def quit_app(self):
        self.engine.shutdown()
        if self._tray is not None:
            try:
                self._tray.stop()
            except Exception:                           # noqa: BLE001
                pass
        self.destroy()

    # ------------------------------------------------------------ 布局
    def _build(self):
        self._build_titlebar()

        body = tk.Frame(self, bg=DEEP)
        body.pack(fill="both", expand=True)

        # 侧栏
        side = tk.Frame(body, bg=DEEP, width=196)
        side.pack(side="left", fill="y")
        side.pack_propagate(False)
        tk.Label(side, text="校园网助手", bg=DEEP, fg=T_PRIMARY_ON_DEEP,
                 font=F_HEAD, anchor="w", padx=16).pack(fill="x", pady=(16, 2))
        tk.Label(side, text="阳光学院 · 172.19.1.1", bg=DEEP, fg=T_MUTED_ON_DEEP,
                 font=F_SMALL, anchor="w", padx=16).pack(fill="x", pady=(0, 18))

        for key, label in (("overview", "总览"), ("settings", "设置"),
                           ("logs", "运行日志"), ("about", "关于")):
            item = NavItem(side, label, lambda k=key: self._switch(k),
                           active=(key == self._current))
            item.pack(fill="x", padx=8, pady=1)
            self._navs[key] = item

        self.side_status = tk.Label(side, text="● 检测中", bg=DEEP, fg=T_MUTED_ON_DEEP,
                                    font=F_SMALL, anchor="w", padx=16)
        self.side_status.pack(side="bottom", fill="x", pady=(0, 14))

        # 中栏：状态
        mid = tk.Frame(body, bg=MID, width=300)
        mid.pack(side="left", fill="y")
        mid.pack_propagate(False)
        self._build_status(mid)

        # 内容区
        content = tk.Frame(body, bg=SURFACE)
        content.pack(side="left", fill="both", expand=True)
        self._content = content
        self._build_views(content)
        self._switch("overview")

    def _build_titlebar(self):
        bar = tk.Frame(self, bg=DEEP, height=36)
        bar.pack(fill="x")
        bar.pack_propagate(False)
        title = tk.Label(bar, text="校园网助手", bg=DEEP, fg=T_MUTED_ON_DEEP, font=F_SMALL)
        title.pack(side="left", padx=14)

        for text, cmd, color in (("×", self.quit_app, DANGER), ("—", self._minimize, None)):
            lbl = tk.Label(bar, text=text, bg=DEEP, fg=T_SECOND_ON_DEEP, font=("Segoe UI", 11),
                           width=3, cursor="hand2")
            lbl.pack(side="right", padx=(0, 6), pady=4)
            lbl.bind("<Button-1>", lambda e, c=cmd: c())
            lbl.bind("<Enter>", lambda e, w=lbl, c=color: w.configure(
                bg=blend(DEEP, .12), fg=(c or T_PRIMARY_ON_DEEP)))
            lbl.bind("<Leave>", lambda e, w=lbl: w.configure(bg=DEEP, fg=T_SECOND_ON_DEEP))

        for w in (bar, title):
            w.bind("<ButtonPress-1>", self._drag_start)
            w.bind("<B1-Motion>", self._drag_move)

    def _minimize(self):
        try:
            self.overrideredirect(False)
            self.iconify()
        except Exception:                               # noqa: BLE001
            pass

    def _drag_start(self, e):
        self._drag = (e.x_root - self.winfo_x(), e.y_root - self.winfo_y())

    def _drag_move(self, e):
        if not self._drag:
            return
        self.geometry(f"+{e.x_root - self._drag[0]}+{e.y_root - self._drag[1]}")

    def _build_status(self, mid):
        tk.Label(mid, text="当前状态", bg=MID, fg=T_MUTED, font=F_SMALL,
                 anchor="w", padx=20).pack(fill="x", pady=(18, 4))
        self.st_title = tk.Label(mid, text="检测中", bg=MID, fg=T_PRIMARY,
                                 font=F_TITLE, anchor="w", padx=20)
        self.st_title.pack(fill="x")
        self.st_msg = tk.Label(mid, text="正在读取认证门户", bg=MID, fg=T_SECOND,
                               font=F_BODY, anchor="w", justify="left",
                               wraplength=250, padx=20, pady=6)
        self.st_msg.pack(fill="x")

        meta = tk.Frame(mid, bg=MID)
        meta.pack(fill="x", padx=20, pady=(10, 0))
        self.st_ip = self._meta_row(meta, "终端 IP")
        self.st_up = self._meta_row(meta, "本次在线")
        self.st_last = self._meta_row(meta, "上次登录")
        self.st_count = self._meta_row(meta, "登录次数")

        btns = tk.Frame(mid, bg=MID)
        btns.pack(fill="x", padx=20, pady=(18, 0))
        self.btn_login = RoundRect(btns, "立即登录", self._do_login, bg=MID,
                                   fill=ACCENT, accent=True, width=120, height=36)
        self.btn_login.pack(fill="x")
        row = tk.Frame(btns, bg=MID)
        row.pack(fill="x", pady=(8, 0))
        self.btn_check = RoundRect(row, "重新检测", self._do_check, bg=MID,
                                   fill=SURFACE, width=120, height=34)
        self.btn_check.pack(side="left", fill="x", expand=True)
        self.btn_logout = RoundRect(row, "注销下线", self._do_logout, bg=MID,
                                    fill=SURFACE, width=110, height=34, danger=True)
        self.btn_logout.pack(side="left", fill="x", expand=True, padx=(8, 0))

        foot = tk.Label(mid, text="", bg=MID, fg=T_MUTED, font=F_SMALL, anchor="w", padx=20)
        foot.pack(side="bottom", fill="x", pady=(0, 14))
        self.st_foot = foot

    def _meta_row(self, parent, label):
        row = tk.Frame(parent, bg=MID)
        row.pack(fill="x", pady=2)
        tk.Label(row, text=label, bg=MID, fg=T_MUTED, font=F_SMALL,
                 width=8, anchor="w").pack(side="left")
        val = tk.Label(row, text="—", bg=MID, fg=T_PRIMARY, font=F_BODY, anchor="w")
        val.pack(side="left", fill="x", expand=True)
        return val

    # ------------------------------------------------------------ 视图
    def _build_views(self, parent):
        self._views["overview"] = self._view_overview(parent)
        self._views["settings"] = self._view_settings(parent)
        self._views["logs"] = self._view_logs(parent)
        self._views["about"] = self._view_about(parent)

    def _page_head(self, parent, title, sub=""):
        tk.Label(parent, text=title, bg=SURFACE, fg=T_PRIMARY_ON_SURFACE,
                 font=F_TITLE, anchor="w").pack(fill="x", padx=28, pady=(22, 2))
        if sub:
            tk.Label(parent, text=sub, bg=SURFACE, fg=T_MUTED_ON_SURFACE,
                     font=F_SMALL, anchor="w").pack(fill="x", padx=28)

    def _card(self, parent, **kw):
        return tk.Frame(parent, bg=MID, **kw)

    def _view_overview(self, parent):
        frame = tk.Frame(parent, bg=SURFACE)
        self._page_head(frame, "概览", "认证状态、连接详情与最近活动")

        cards = tk.Frame(frame, bg=SURFACE)
        cards.pack(fill="x", padx=28, pady=(16, 0))
        self._ov_auth = self._stat_card(cards, "认证状态")
        self._ov_uptime = self._stat_card(cards, "本次在线")
        self._ov_conn = self._stat_card(cards, "外网连通")

        tk.Label(frame, text="最近活动", bg=SURFACE, fg=T_SECOND_ON_SURFACE,
                 font=F_HEAD, anchor="w").pack(fill="x", padx=28, pady=(22, 8))
        self._ov_log = tk.Text(frame, bg=MID, fg=T_SECOND, font=F_MONO, bd=0,
                               highlightthickness=0, height=10, wrap="none",
                               padx=12, pady=10, insertwidth=0)
        self._ov_log.pack(fill="both", expand=True, padx=28, pady=(0, 22))
        self._ov_log.configure(state="disabled")
        return frame

    def _stat_card(self, parent, label):
        card = tk.Frame(parent, bg=MID)
        card.pack(side="left", fill="x", expand=True, padx=(0, 10))
        tk.Label(card, text=label, bg=MID, fg=T_MUTED, font=F_SMALL,
                 anchor="w", padx=14).pack(fill="x", pady=(12, 2))
        val = tk.Label(card, text="—", bg=MID, fg=T_PRIMARY, font=F_HEAD,
                       anchor="w", padx=14)
        val.pack(fill="x", pady=(0, 12))
        return val

    def _view_logs(self, parent):
        frame = tk.Frame(parent, bg=SURFACE)
        head = tk.Frame(frame, bg=SURFACE)
        head.pack(fill="x", padx=28, pady=(22, 8))
        tk.Label(head, text="运行日志", bg=SURFACE, fg=T_PRIMARY_ON_SURFACE,
                 font=F_TITLE, anchor="w").pack(side="left")
        RoundRect(head, "清空", self._clear_logs, bg=SURFACE, fill=MID,
                  width=72, height=30, font=F_SMALL).pack(side="right")

        self._log_text = tk.Text(frame, bg=MID, fg=T_SECOND, font=F_MONO, bd=0,
                                 highlightthickness=0, wrap="word",
                                 padx=12, pady=10, insertwidth=0)
        self._log_text.pack(fill="both", expand=True, padx=28, pady=(0, 22))
        self._log_text.configure(state="disabled")
        self._log_text.tag_configure("ok", foreground="#5fd38d")
        self._log_text.tag_configure("warn", foreground="#e7c46b")
        self._log_text.tag_configure("error", foreground="#ff8a80")
        self._log_text.tag_configure("time", foreground=T_MUTED)
        return frame

    def _view_settings(self, parent):
        outer = tk.Frame(parent, bg=SURFACE)
        self._page_head(outer, "设置", "修改后点底部保存生效")

        body = tk.Frame(outer, bg=SURFACE)
        body.pack(fill="both", expand=True, padx=28, pady=(14, 0))

        self.v_user = self._field(body, "学号 / 工号")
        self.v_pass = self._field(body, "密码", secret=True,
                                  hint="已保存（留空不修改）")
        self.v_remember = self._make_switch(body, "记住密码", "使用 Windows DPAPI 加密保存在本机")

        tk.Label(body, text="自动认证", bg=SURFACE, fg=T_MUTED_ON_SURFACE,
                 font=F_SMALL, anchor="w").pack(fill="x", pady=(16, 4))
        self.v_auto = self._make_switch(body, "断网自动登录", "检测到未认证时自动提交账号密码")

        row = tk.Frame(body, bg=SURFACE)
        row.pack(fill="x", pady=(10, 0))
        tk.Label(row, text="检测周期", bg=SURFACE, fg=T_SECOND_ON_SURFACE,
                 font=F_BODY, width=8, anchor="w").pack(side="left")
        self.v_interval = Slider(row, self.engine.cfg.get("interval", 20), 5, 180, 5,
                                 command=self._on_interval, bg=SURFACE, width=220)
        self.v_interval.pack(side="left")
        self.v_interval_label = tk.Label(row, text=f"{self.engine.cfg.get('interval', 20)} 秒",
                                         bg=SURFACE, fg=T_PRIMARY_ON_SURFACE,
                                         font=F_BODY, width=8, anchor="e")
        self.v_interval_label.pack(side="right")

        row2 = tk.Frame(body, bg=SURFACE)
        row2.pack(fill="x", pady=(10, 0))
        self.v_timeout = self._field_inline(row2, "请求超时（秒）", self.engine.cfg.get("timeout", 6))
        self.v_retry = self._field_inline(row2, "失败重试", self.engine.cfg.get("retry", 3))

        self.v_base = self._field(body, "认证门户地址", value=self.engine.cfg.get("base_url", ""))

        tk.Label(body, text="系统", bg=SURFACE, fg=T_MUTED_ON_SURFACE,
                 font=F_SMALL, anchor="w").pack(fill="x", pady=(16, 4))
        self.v_startup = self._make_switch(body, "开机自动运行", "登录后静默启动，只驻留托盘")
        self.v_autoclose = self._make_switch(body, "关闭时退出", "关掉窗口就结束进程（否则收进托盘）")

        bar = tk.Frame(outer, bg=SURFACE)
        bar.pack(fill="x", padx=28, pady=14)
        RoundRect(bar, "保存设置", self._save_settings, bg=SURFACE, fill=ACCENT,
                  accent=True, width=140, height=36).pack(side="right")

        self._pending_interval = self.engine.cfg.get("interval", 20)
        return outer

    def _field(self, parent, label, *, value="", secret=False, hint=""):
        tk.Label(parent, text=label, bg=SURFACE, fg=T_SECOND_ON_SURFACE,
                 font=F_SMALL, anchor="w").pack(fill="x", pady=(8, 3))
        entry = tk.Entry(parent, bg=MID, fg=T_PRIMARY, font=F_BODY, bd=0,
                         highlightthickness=0, insertbackground=T_PRIMARY,
                         relief="flat")
        entry.pack(fill="x", ipady=7, padx=0)
        entry.insert(0, value)
        if secret:
            entry.configure(show="•")
            if hint:
                entry.configure(fg=T_SECOND)
        return entry

    def _field_inline(self, parent, label, value):
        box = tk.Frame(parent, bg=SURFACE)
        box.pack(side="left", fill="x", expand=True, padx=(0, 10))
        tk.Label(box, text=label, bg=SURFACE, fg=T_SECOND_ON_SURFACE,
                 font=F_SMALL, anchor="w").pack(fill="x", pady=(0, 3))
        entry = tk.Entry(box, bg=MID, fg=T_PRIMARY, font=F_BODY, bd=0,
                         highlightthickness=0, insertbackground=T_PRIMARY)
        entry.pack(fill="x", ipady=6)
        entry.insert(0, str(value))
        return entry

    def _make_switch(self, parent, label, sub):
        row = tk.Frame(parent, bg=SURFACE)
        row.pack(fill="x", pady=3)
        txt = tk.Frame(row, bg=SURFACE)
        txt.pack(side="left", fill="x", expand=True)
        tk.Label(txt, text=label, bg=SURFACE, fg=T_PRIMARY_ON_SURFACE,
                 font=F_BODY, anchor="w").pack(fill="x")
        tk.Label(txt, text=sub, bg=SURFACE, fg=T_MUTED_ON_SURFACE,
                 font=F_SMALL, anchor="w").pack(fill="x")
        toggle = Toggle(row, False, bg=SURFACE)
        toggle.pack(side="right", padx=(10, 2))
        return toggle

    def _view_about(self, parent):
        frame = tk.Frame(parent, bg=SURFACE)
        self._page_head(frame, "关于", "Tk 原生界面版 · GPL-3.0")
        lines = [
            ("版本", "v2.0.0（Tk 版）"),
            ("界面", "tkinter 原生控件，无 WebView、无本地 HTTP 服务"),
            ("许可证", "GNU General Public License v3.0"),
            ("仓库", "github.com/Heznc/campus-net-assistant-tk"),
            ("数据目录", store.DATA_DIR),
        ]
        for key, val in lines:
            row = tk.Frame(frame, bg=SURFACE)
            row.pack(fill="x", padx=28, pady=6)
            tk.Label(row, text=key, bg=SURFACE, fg=T_MUTED_ON_SURFACE,
                     font=F_SMALL, width=8, anchor="w").pack(side="left")
            tk.Label(row, text=val, bg=SURFACE, fg=T_SECOND_ON_SURFACE,
                     font=F_BODY, anchor="w", justify="left").pack(side="left")
        return frame

    # ------------------------------------------------------------ 行为
    def _switch(self, key):
        self._current = key
        for name, frame in self._views.items():
            if name == key:
                frame.pack(fill="both", expand=True)
            else:
                frame.pack_forget()
        for name, nav in self._navs.items():
            nav.set_active(name == key)
        if key == "settings":
            self._sync_settings()
        if key == "logs":
            self._render_logs(full=True)

    def _do_check(self):
        threading.Thread(target=self.engine.check, daemon=True).start()

    def _do_login(self):
        user = self.v_user.get().strip() if self._current == "settings" else None
        threading.Thread(target=self.engine.login, kwargs={"user": user or None},
                         daemon=True).start()

    def _do_logout(self):
        if not self._confirm("确定注销当前上网认证？"):
            return
        threading.Thread(target=self.engine.logout, daemon=True).start()

    def _confirm(self, text):
        from tkinter import messagebox
        return messagebox.askyesno("校园网助手", text, parent=self)

    def _clear_logs(self):
        self._log_text.configure(state="normal")
        self._log_text.delete("1.0", "end")
        self._log_text.configure(state="disabled")

    def _on_interval(self, value):
        self._pending_interval = value
        self.v_interval_label.configure(text=f"{value} 秒")

    def _save_settings(self):
        patch = {
            "user": self.v_user.get().strip(),
            "auto_login": self.v_auto.value,
            "remember": self.v_remember.value,
            "autostart": self.v_startup.value,
            "auto_close": self.v_autoclose.value,
            "interval": self._pending_interval,
            "base_url": self.v_base.get().strip(),
        }
        try:
            patch["timeout"] = int(self.v_timeout.get())
            patch["retry"] = int(self.v_retry.get())
        except ValueError:
            pass
        pw = self.v_pass.get()
        if pw:
            patch["password"] = pw
        self.engine.update_config(patch)
        self.v_pass.delete(0, "end")
        self._refresh()

    def _sync_settings(self):
        cfg = self.engine.cfg
        self.v_user.delete(0, "end")
        self.v_user.insert(0, cfg.get("user", ""))
        self.v_base.delete(0, "end")
        self.v_base.insert(0, cfg.get("base_url", ""))
        self.v_timeout.delete(0, "end")
        self.v_timeout.insert(0, str(cfg.get("timeout", 6)))
        self.v_retry.delete(0, "end")
        self.v_retry.insert(0, str(cfg.get("retry", 3)))
        for widget, value in ((self.v_remember, cfg.get("remember", True)),
                              (self.v_auto, cfg.get("auto_login", True)),
                              (self.v_startup, store.get_autostart()),
                              (self.v_autoclose, cfg.get("auto_close", False))):
            widget._on = bool(value)
            widget._paint()

    # ------------------------------------------------------------ 刷新
    def _tick(self):
        try:
            st = self.engine.snapshot()
            sig = (st["phase"], st["authenticated"], st["ip"], st["message"],
                   st["login_count"], st["last_login"], len(self.engine.logs))
            if sig != self._sig:
                self._sig = sig
                self._render(st)
            else:
                self._render_dynamic(st)
        except Exception:                               # noqa: BLE001
            pass
        self.after(1000, self._tick)

    def _render_dynamic(self, st):
        up = self._fmt_dur(st["online_seconds"]) if st["authenticated"] else "—"
        self.st_up.configure(text=up)
        if self._current == "overview":
            self._ov_uptime.configure(text=up)

    def _render(self, st):
        phase_titles = {
            "online": "网络已连接", "offline": "需要上网认证",
            "logging_in": "正在认证", "checking": "正在检测",
            "error": "认证门户不可达", "idle": "准备就绪",
        }
        self.st_title.configure(text=phase_titles.get(st["phase"], "准备就绪"))
        self.st_msg.configure(text=st["message"] or "")
        self.st_ip.configure(text=st["ip"] or "—")
        self.st_last.configure(text=self._fmt_time(st["last_login"]))
        self.st_count.configure(text=str(st["login_count"]))
        self._render_dynamic(st)

        state_text = "已认证" if st["authenticated"] else (
            "待认证" if st["portal_reachable"] else "未连接")
        self.side_status.configure(
            text=f"● {state_text}",
            fg="#5fd38d" if st["authenticated"] else
               ("#e7c46b" if st["portal_reachable"] else T_MUTED_ON_DEEP))

        self._ov_auth.configure(text="已认证" if st["authenticated"] else "未认证")
        self._ov_conn.configure(text="可达" if st["internet"] else ("内网" if st["portal_reachable"] else "断开"))

        self._render_logs()

    def _render_logs(self, full: bool = False):
        view = self._ov_log if not full else self._log_text
        view.configure(state="normal")
        if full:
            view.delete("1.0", "end")
            entries = self.engine.logs[-200:]
        else:
            entries = [e for e in self.engine.logs if e["seq"] > self._last_seq][-8:]
            if entries:
                self._last_seq = entries[-1]["seq"]
        for e in entries:
            tag = e["level"] if e["level"] in ("ok", "warn", "error") else ""
            view.insert("end", f"{e['t']}  ", "time")
            view.insert("end", e["msg"] + "\n", tag)
        view.see("end")
        view.configure(state="disabled")

    @staticmethod
    def _fmt_dur(sec):
        sec = max(0, int(sec or 0))
        if sec < 60:
            return f"{sec} 秒"
        m, h = sec // 60, sec // 3600
        if h:
            return f"{h} 小时 {m % 60} 分"
        return f"{m} 分 {sec % 60} 秒"

    @staticmethod
    def _fmt_time(ts):
        if not ts:
            return "—"
        return time.strftime("%m-%d %H:%M", time.localtime(ts))


def acquire_single_instance() -> bool:
    """命名互斥体，保证只跑一个实例。"""
    try:
        handle = ctypes.windll.kernel32.CreateMutexW(None, False, "CampusNetAssistantTK")
        if ctypes.windll.kernel32.GetLastError() == 183:      # ERROR_ALREADY_EXISTS
            return False
        globals()["_MUTEX"] = handle
        return True
    except Exception:                                   # noqa: BLE001
        return True


def main() -> int:
    hidden = "--hidden" in sys.argv
    if not acquire_single_instance():
        return 0
    engine = Engine()
    app = App(engine, start_hidden=hidden)
    try:
        app.mainloop()
    finally:
        engine.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(main())
