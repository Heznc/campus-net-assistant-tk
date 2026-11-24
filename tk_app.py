"""校园网助手 · Tk 版（macOS Vibrancy 风格）

纯 tkinter 实现，无 WebView、无本地 HTTP 服务。

设计约束：
- 只用三级灰：#1c1c1e / #2c2c2e / #3a3a3c
- 无渐变、无发光、无阴影、无描边（层级全靠色差）
- 圆角：窗口 12px，卡片/输入框/按钮 8–10px
- 字体统一用系统黑体（微软雅黑），只有字重差异，不引入任何需授权的字体
"""

from __future__ import annotations

import ctypes
import math
import os
import sys
import threading
import time
import tkinter as tk

import store
import tray as tray_mod
from engine import Engine

# ---------------------------------------------------------------- 调色板
BASE = "#141416"       # 窗口底板（衬托圆角卡片）
DEEP = "#1c1c1e"
MID = "#2c2c2e"
SURFACE = "#3a3a3c"
ACCENT = "#aeb9c9"        # 钢灰蓝：灰中带一点冷色
ACCENT_FG = "#15171b"     # 浅灰底上的深色文字
ACCENT_TRACK = "#93a0b3"  # 开关/滑杆/光环用的深一档灰蓝
DANGER = "#ff453a"
WHITE = "#ffffff"

DARK = True               # 当前是否深色主题
INK = "#ffffff"           # 文字/叠加色：深色主题叠白，浅色主题叠黑

RADIUS_WIN = 44
RADIUS_CARD = 28
RADIUS_CTRL = 22


def blend(bg: str, alpha: float) -> str:
    """把 bg 朝 INK 混合：深色主题提亮，浅色主题压暗。"""
    bg = bg.lstrip("#")
    ink_ = INK.lstrip("#")
    ir, ig, ib = (int(ink_[i:i + 2], 16) for i in (0, 2, 4))
    r, g, b = (int(bg[i:i + 2], 16) for i in (0, 2, 4))
    return "#%02x%02x%02x" % (
        round(r + (ir - r) * alpha),
        round(g + (ig - g) * alpha),
        round(b + (ib - b) * alpha),
    )


def set_palette(dark: bool) -> None:
    """切换深浅色：重新计算三级灰、文字色、强调色。"""
    global DARK, INK, BASE, DEEP, MID, SURFACE
    global ACCENT, ACCENT_FG, ACCENT_TRACK
    global T1, T2, T3, T1_D, T2_D, T3_D, T1_S, T2_S, T3_S
    DARK = dark
    INK = "#ffffff" if dark else "#000000"
    if dark:
        BASE, DEEP, MID, SURFACE = "#141416", "#1c1c1e", "#2c2c2e", "#3a3a3c"
        ACCENT, ACCENT_FG, ACCENT_TRACK = "#aeb9c9", "#15171b", "#93a0b3"
    else:
        BASE, DEEP, MID, SURFACE = "#e3e4e8", "#ebecef", "#f4f5f7", "#ffffff"
        ACCENT, ACCENT_FG, ACCENT_TRACK = "#8b9cb6", "#0f1115", "#7789a5"
    T1, T2, T3 = blend(MID, .95), blend(MID, .70), blend(MID, .40)
    T1_D, T2_D, T3_D = blend(DEEP, .95), blend(DEEP, .70), blend(DEEP, .40)
    T1_S, T2_S, T3_S = blend(SURFACE, .95), blend(SURFACE, .70), blend(SURFACE, .40)


# 文本三档（按各自底色混合）
T1, T2, T3 = blend(MID, .95), blend(MID, .70), blend(MID, .40)
T1_D, T2_D, T3_D = blend(DEEP, .95), blend(DEEP, .70), blend(DEEP, .40)
T1_S, T2_S, T3_S = blend(SURFACE, .95), blend(SURFACE, .70), blend(SURFACE, .40)

# 统一系统黑体，仅用字重区分层级（无版权风险）
FONT = "Microsoft YaHei UI"
F_BODY = (FONT, 10)
F_BODY_B = (FONT, 10, "bold")
F_SMALL = (FONT, 9)
F_SMALL_B = (FONT, 9, "bold")
F_TITLE = (FONT, 20, "bold")
F_HEAD = (FONT, 13, "bold")
F_BTN = (FONT, 10, "bold")


try:
    from PIL import Image, ImageDraw, ImageFilter, ImageTk
    HAS_PIL = True
except Exception:                                       # noqa: BLE001
    HAS_PIL = False


_IMG_CACHE: dict = {}


def darken(bg: str, amount: float) -> str:
    """把颜色按比例压暗（浅色底做悬停/按下态）。"""
    bg = bg.lstrip("#")
    r, g, b = (int(bg[i:i + 2], 16) for i in (0, 2, 4))
    return "#%02x%02x%02x" % (round(r * (1 - amount)),
                             round(g * (1 - amount)),
                             round(b * (1 - amount)))


def _round_photo(w: int, h: int, r: int, fill: str, ss: int = 4):
    """生成抗锯齿圆角图（4 倍超采样后缩回），带缓存。"""
    key = (w, h, r, fill)
    if key in _IMG_CACHE:
        return _IMG_CACHE[key]
    img = Image.new("RGBA", (w * ss, h * ss), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, w * ss - 1, h * ss - 1], radius=r * ss, fill=fill)
    img = img.resize((w, h), Image.LANCZOS)
    photo = ImageTk.PhotoImage(img)
    _IMG_CACHE[key] = photo
    return photo


SQUIRCLE_N = 4.0        # 指数越大越方；4 接近 iOS 的超椭圆


def squircle_points(x0: float, y0: float, w: float, h: float, r: float,
                    n: float = SQUIRCLE_N, steps: int = 16):
    """超椭圆（squircle）圆角矩形的轮廓点。"""
    r = max(0.0, min(r, w / 2, h / 2))
    if r <= 0:
        return [(x0, y0), (x0 + w, y0), (x0 + w, y0 + h), (x0, y0 + h)]
    e = 2.0 / n
    pts = []

    def arc(cx, cy, t0, t1):
        for i in range(steps + 1):
            t = t0 + (t1 - t0) * i / steps
            c, sn = math.cos(t), math.sin(t)
            u = (abs(c) ** e) * (1 if c >= 0 else -1)
            v = (abs(sn) ** e) * (1 if sn >= 0 else -1)
            pts.append((cx + u * r, cy + v * r))

    arc(x0 + w - r, y0 + r, -math.pi / 2, 0)            # 右上
    arc(x0 + w - r, y0 + h - r, 0, math.pi / 2)         # 右下
    arc(x0 + r, y0 + h - r, math.pi / 2, math.pi)       # 左下
    arc(x0 + r, y0 + r, math.pi, 1.5 * math.pi)         # 左上
    return pts


def _squircle_photo(w: int, h: int, r: int, fill: str, bg: str | None = None,
                    ss: int = 4, n: float | None = None):
    """圆角矩形图：直接压到底色上输出不透明图，避免透明像素在浅色主题下边黑。"""
    key = ("sq", w, h, r, fill, bg, n)
    if key in _IMG_CACHE:
        return _IMG_CACHE[key]
    img = Image.new("RGB", (w * ss, h * ss), bg or fill)
    d = ImageDraw.Draw(img)
    poly = squircle_points(0, 0, w * ss - 1, h * ss - 1, r * ss,
                           n=n if n is not None else SQUIRCLE_N)
    d.polygon(poly, fill=fill)
    img = img.resize((w, h), Image.LANCZOS)
    photo = ImageTk.PhotoImage(img)
    _IMG_CACHE[key] = photo
    return photo


def _circle_photo(d: int, fill: str, bg: str, ss: int = 4):
    key = ("circle", d, fill, bg)
    if key in _IMG_CACHE:
        return _IMG_CACHE[key]
    img = Image.new("RGB", (d * ss, d * ss), bg)
    ImageDraw.Draw(img).ellipse((0, 0, d * ss - 1, d * ss - 1), fill=fill)
    img = img.resize((d, d), Image.LANCZOS)
    photo = ImageTk.PhotoImage(img)
    _IMG_CACHE[key] = photo
    return photo


def _corner_mask(size: int, r: int, outer: str, inner: str, corner: str):
    """角落补丁：圆弧外侧填 outer、内侧填 inner，输出不透明图（抗锯齿）。"""
    key = ("mask", size, r, outer, inner, corner)
    if key in _IMG_CACHE:
        return _IMG_CACHE[key]
    if not HAS_PIL:
        return None
    ss = 4
    big = size * ss
    img = Image.new("RGB", (big, big), outer)
    px = img.load()
    rgb_in = tuple(int(inner.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4))
    for y in range(big):
        for x in range(big):
            if corner == "tr":
                u, v = big - 1 - x, y
            elif corner == "bl":
                u, v = x, big - 1 - y
            elif corner == "br":
                u, v = big - 1 - x, big - 1 - y
            else:
                u, v = x, y
            u /= ss
            v /= ss
            if u >= r or v >= r:
                px[x, y] = rgb_in
                continue
            if ((r - u) / r) ** SQUIRCLE_N + ((r - v) / r) ** SQUIRCLE_N <= 1.0:
                px[x, y] = rgb_in
    img = img.resize((size, size), Image.LANCZOS)
    photo = ImageTk.PhotoImage(img)
    _IMG_CACHE[key] = photo
    return photo


def draw_round(canvas: tk.Canvas, x: int, y: int, w: int, h: int, r: int,
               fill: str, tag: str = "shape", bg: str | None = None,
               capsule: bool = False) -> None:
    """在 canvas 上画一块抗锯齿的**超椭圆**圆角。没有 PIL 时退化为多边形。"""
    r = max(0, min(r, min(w, h) // 2))
    if HAS_PIL and w > 1 and h > 1:
        if bg is None:
            try:
                bg = canvas.cget("bg")
            except Exception:                       # noqa: BLE001
                bg = None
        canvas.create_image(x, y, image=_squircle_photo(
            w, h, r, fill, bg, n=2.0 if capsule else None), anchor="nw", tags=tag)
    else:
        pts = squircle_points(x, y, w - 1, h - 1, r, steps=8)
        canvas.create_polygon([c for p in pts for c in p], smooth=False,
                              fill=fill, tags=tag)


def rr_points(x1, y1, x2, y2, r):
    return [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
            x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
            x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]


# ---------------------------------------------------------------- 基础控件
class RoundCanvas(tk.Canvas):
    """圆角容器：抗锯齿圆角底图。"""

    def __init__(self, master, *, bg, fill, radius=RADIUS_CARD, width=None, height=None):
        kw = {}
        if width is not None:
            kw["width"] = width
        if height is not None:
            kw["height"] = height
        super().__init__(master, bg=bg, highlightthickness=0, bd=0, takefocus=0, **kw)
        self._fill = fill
        self._radius = radius
        self.bind("<Configure>", self._redraw)

    def _redraw(self, _event=None):
        w, h = self.winfo_width(), self.winfo_height()
        if w <= 1 or h <= 1:
            return
        self.delete("bgshape")
        draw_round(self, 0, 0, w, h, self._radius, self._fill, tag="bgshape")
        self.tag_lower("bgshape")

    def set_fill(self, color: str):
        self._fill = color
        self._redraw()


class ScrollFrame(tk.Frame):
    """可滚动容器：内容超高时滚轮/拖动可滚，滚动条隐藏。"""

    def __init__(self, master, *, bg=SURFACE):
        super().__init__(master, bg=bg)
        self.canvas = tk.Canvas(self, bg=bg, highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True)
        self.inner = tk.Frame(self.canvas, bg=bg)
        self._win = self.canvas.create_window(0, 0, window=self.inner, anchor="nw")
        self.canvas.bind("<Configure>", self._sync)
        self.inner.bind("<Configure>", self._region)

    def _sync(self, event):
        self.canvas.itemconfigure(self._win, width=event.width)
        self.after_idle(self._region)

    def _region(self, _e=None):
        bbox = self.canvas.bbox("all")
        if bbox:
            self.canvas.configure(scrollregion=(0, 0, self.canvas.winfo_width(),
                                                max(bbox[3], self.canvas.winfo_height())))

    def scroll_by(self, delta: int):
        first, last = self.canvas.yview()
        if first <= 0.0 and last >= 1.0:
            return False
        self.canvas.yview_scroll(delta, "units")
        return True

    def contains(self, widget) -> bool:
        w = widget
        while w is not None:
            if w is self or w is self.canvas or w is self.inner:
                return True
            w = getattr(w, "master", None)
        return False


def _system_dark() -> bool:
    """读 Windows 的「应用模式」；读不到就按深色处理。"""
    try:
        import winreg
        with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as key:
            light, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
        return not bool(light)
    except Exception:                                   # noqa: BLE001
        return True


def resolve_theme(mode: str) -> bool:
    if mode == "light":
        return False
    if mode == "dark":
        return True
    return _system_dark()


class Card(tk.Frame):
    """圆角卡片：Canvas 画 AA 圆角底，内层 Frame 铺满，四角再用遮罩补成圆角。"""

    def __init__(self, master, *, bg=None, fill=None, radius=RADIUS_CARD, padding=6):
        bg = bg or SURFACE
        fill = fill or MID
        super().__init__(master, bg=bg)
        self._bg = bg
        self._fill = fill
        self._radius = radius
        self._canvas = RoundCanvas(self, bg=bg, fill=fill, radius=radius)
        self._canvas.pack(fill="both", expand=True)
        self.body = tk.Frame(self._canvas, bg=fill)
        self._pad = padding
        self._win = self._canvas.create_window(padding, padding, window=self.body, anchor="nw")
        self._masks = []
        self._canvas.bind("<Configure>", self._on_resize, add="+")

    def _on_resize(self, event):
        w, h = event.width, event.height
        self._canvas.itemconfigure(self._win, width=max(10, w - self._pad * 2),
                                   height=max(10, h - self._pad * 2))
        r = min(self._radius, max(2, min(w, h) // 2))
        for item in self._masks:
            self._canvas.delete(item)
        self._masks = []
        for corner, pos in (("tl", (0, 0)), ("tr", (w - r, 0)),
                            ("bl", (0, h - r)), ("br", (w - r, h - r))):
            photo = _corner_mask(r, r, self._bg, self._fill, corner)
            if photo is not None:
                self._masks.append(self._canvas.create_image(pos[0], pos[1], image=photo,
                                                             anchor="nw"))


class Button(tk.Canvas):
    """圆角按钮：AA 圆角 + 文字严格居中。"""

    def __init__(self, master, text, command=None, *, bg=None, fill=None,
                 fg=None, width=120, height=34, radius=RADIUS_CTRL,
                 font=F_BTN, accent=False):
        bg = bg or MID
        fill = fill or SURFACE
        super().__init__(master, width=width, height=height, bg=bg,
                         highlightthickness=0, bd=0, takefocus=0)
        self._bg, self._fill = bg, fill
        self._fg = fg or (ACCENT_FG if accent else T1_S)
        self._radius = radius
        self._text = text
        self._font = font
        self._command = command
        self._enabled = True
        self._paint(self._fill)
        self.bind("<Enter>", lambda e: self._enabled and self._paint(self._state_color(.06)))
        self.bind("<Leave>", lambda e: self._paint(self._fill))
        self.bind("<ButtonPress-1>", lambda e: self._enabled and self._paint(self._state_color(.12)))
        self.bind("<ButtonRelease-1>", self._release)
        self.bind("<Configure>", lambda e: self._paint(self._fill))

    def _size(self):
        """按实际尺寸绘制（pack fill 后 canvas 宽高会变）。"""
        w = self.winfo_width()
        h = self.winfo_height()
        if w <= 1 or h <= 1:
            w, h = int(self["width"]), int(self["height"])
        return w, h

    def _paint(self, color):
        self.delete("all")
        w, h = self._size()
        draw_round(self, 0, 0, w, h, self._radius, color, capsule=True)
        self.create_text(w / 2, h / 2, text=self._text, fill=self._fg,
                         font=self._font, anchor="center")

    def _state_color(self, amount: float):
        """浅灰按钮变暗，深色按钮提亮。"""
        if self._fill == ACCENT:
            return darken(self._fill, amount)
        return blend(self._fill, amount)

    def _release(self, event):
        w, h = self._size()
        inside = 0 <= event.x <= w and 0 <= event.y <= h
        self._paint(self._state_color(.06) if inside else self._fill)
        if inside and self._enabled and self._command:
            self._command()

    def set_text(self, text):
        self._text = text
        self._paint(self._fill)

    def set_enabled(self, enabled):
        self._enabled = enabled
        self._fg = (ACCENT_FG if self._fill == ACCENT else T1_S) if enabled else T3_S
        self._paint(self._fill)


class RoundedEntry(tk.Canvas):
    """AA 圆角输入框：Canvas 圆角底 + 同色 Entry 内嵌 + 占位提示（幽灵文字）。"""

    def __init__(self, master, *, bg=None, fill=None, radius=RADIUS_CTRL,
                 height=36, show=None, fg=None, justify="left"):
        bg = bg or SURFACE
        fill = fill or MID
        super().__init__(master, bg=bg, height=height, highlightthickness=0, bd=0, takefocus=0)
        self._fill = fill
        self._radius = radius
        self._secret = bool(show)
        self.entry = tk.Entry(self, bg=fill, fg=fg or T1, font=F_BODY, bd=0,
                              relief="flat", highlightthickness=0, justify=justify,
                              insertbackground=T1, show=show or "")
        self._win = self.create_window(12, height // 2, window=self.entry, anchor="w")
        # 占位提示：单独的 Label 盖在 Entry 上（后创建的窗口项在上层）
        self._ph = tk.Label(self, text="", bg=fill, fg=T3, font=F_BODY, anchor="w")
        self._ph_win = self.create_window(12, height // 2, window=self._ph, anchor="w",
                                          state="hidden")
        self._ph_text = ""
        self._ph_color = T3
        self._ph.bind("<Button-1>", lambda e: self.entry.focus_set())
        for ev in ("<FocusIn>", "<FocusOut>", "<KeyRelease>"):
            self.entry.bind(ev, lambda e: self._sync_ph(), add="+")
        self.bind("<Configure>", self._resize)

    def _resize(self, event):
        w, h = event.width, event.height
        self.delete("bgshape")
        draw_round(self, 0, 0, w, h, self._radius, self._fill, tag="bgshape")
        self.tag_lower("bgshape")
        self.itemconfigure(self._win, width=max(10, w - 24), height=h - 10)
        self.itemconfigure(self._ph_win, width=max(10, w - 26), height=h - 10)
        self.itemconfigure(self._win, state="normal")

    # 占位提示
    def set_placeholder(self, text: str, color: str | None = None):
        self._ph_text = text or ""
        if color:
            self._ph_color = color
        self._ph.configure(text=self._ph_text, fg=self._ph_color)
        self._sync_ph()

    def _sync_ph(self):
        show = bool(self._ph_text) and not self.entry.get()
        self.itemconfigure(self._ph_win, state="normal" if show else "hidden")
        if show:
            self.tag_raise(self._ph_win)

    # 代理常用方法
    def get(self):
        return self.entry.get()

    def set(self, text):
        self.entry.delete(0, "end")
        self.entry.insert(0, text)
        self._sync_ph()

    def clear(self):
        self.entry.delete(0, "end")
        self._sync_ph()

    def focus(self):
        self.entry.focus_set()
        self._sync_ph()


class RoundedText(tk.Canvas):
    """AA 圆角日志区：Canvas 圆角底 + 内嵌 Text。"""

    def __init__(self, master, *, bg=None, fill=None, radius=RADIUS_CARD, height=200):
        bg = bg or SURFACE
        fill = fill or MID
        super().__init__(master, bg=bg, height=height, highlightthickness=0, bd=0)
        self._fill = fill
        self._radius = radius
        self.text = tk.Text(self, bg=fill, fg=T2, font=F_SMALL, bd=0,
                            highlightthickness=0, wrap="word", padx=12, pady=10,
                            insertwidth=0, selectbackground=blend(fill, .18))
        self._win = self.create_window(10, 8, window=self.text, anchor="nw")
        self.bind("<Configure>", self._resize)

    def _resize(self, event):
        w, h = event.width, event.height
        self.delete("bgshape")
        draw_round(self, 0, 0, w, h, self._radius, self._fill, tag="bgshape")
        self.tag_lower("bgshape")
        self.itemconfigure(self._win, width=max(10, w - 20), height=max(10, h - 16))


class Toggle(tk.Canvas):
    """开关：胶囊形轨道 + 圆形滑块（AA）。"""

    def __init__(self, master, value: bool, command=None, *, bg=None, width=46, height=26):
        bg = bg or MID
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
        track = ACCENT_TRACK if self._on else blend(SURFACE, .20 if hovering else .14)
        draw_round(self, 0, 0, self._cw, self._ch, self._ch // 2, track, capsule=True)
        knob = self._ch - 8
        x = self._cw - knob - 4 if self._on else 4
        y = 4
        if HAS_PIL:
            self.create_image(x, y, image=_circle_photo(knob, WHITE, track), anchor="nw")
        else:
            self.create_oval(x, y, x + knob, y + knob, fill=WHITE, outline="")

    def _toggle(self, _e=None):
        self._on = not self._on
        self._paint()
        if self._command:
            self._command(self._on)

    @property
    def value(self):
        return self._on


class Slider(tk.Canvas):
    """横向滑杆：AA 轨道 + 圆形滑块。"""

    def __init__(self, master, value, minimum=5, maximum=3600, step=5,
                 command=None, bg=None, width=200):
        bg = bg or MID
        super().__init__(master, width=width, height=24, bg=bg,
                         highlightthickness=0, bd=0, takefocus=0)
        self._min, self._max, self._step = minimum, maximum, step
        self._value = max(minimum, min(maximum, int(value)))
        self._command = command
        self._cw = width
        self.bind("<Button-1>", self._drag)
        self.bind("<B1-Motion>", self._drag)
        self._paint()

    # 对数刻度：5–3600 秒时，180 秒大约在一半位置，便于调常用区间
    @property
    def _use_log(self) -> bool:
        return self._min > 0 and self._max / self._min > 50

    def _ratio_of(self, value: int) -> float:
        if self._use_log:
            return math.log(value / self._min) / math.log(self._max / self._min)
        return (value - self._min) / max(1, self._max - self._min)

    def _value_of(self, ratio: float) -> int:
        ratio = max(0.0, min(1.0, ratio))
        if self._use_log:
            raw = self._min * (self._max / self._min) ** ratio
        else:
            raw = self._min + ratio * (self._max - self._min)
        return max(self._min, min(self._max, int(round(raw / self._step) * self._step)))

    def _paint(self):
        self.delete("all")
        y = 12
        draw_round(self, 6, y - 3, self._cw - 12, 6, 3, blend(SURFACE, .12), capsule=True)
        x = int(self._ratio_of(self._value) * (self._cw - 24)) + 12
        if x > 8:
            draw_round(self, 6, y - 3, x - 6, 6, 3, ACCENT_TRACK, capsule=True)
        r = 7
        under = ACCENT_TRACK if x > 6 else blend(SURFACE, .12)
        if HAS_PIL:
            self.create_image(x - r, y - r, image=_circle_photo(r * 2, WHITE, under),
                              anchor="nw")
        else:
            self.create_oval(x - r, y - r, x + r, y + r, fill=WHITE, outline="")

    def _drag(self, event):
        ratio = (event.x - 12) / max(1, self._cw - 24)
        self._value = self._value_of(ratio)
        self._paint()
        if self._command:
            self._command(self._value)

    def set_value(self, value, notify=False):
        self._value = max(self._min, min(self._max, int(value)))
        self._paint()
        if notify and self._command:
            self._command(self._value)

    @property
    def value(self):
        return self._value


class NavItem(tk.Canvas):
    """圆角导航项：AA 圆角高亮，文字垂直居中。"""

    def __init__(self, master, text, command, *, bg=None, width=178, height=34):
        bg = bg or DEEP
        super().__init__(master, bg=bg, width=width, height=height,
                         highlightthickness=0, bd=0, takefocus=0)
        self._bg = bg
        self._text = text
        self._command = command
        self._active = False
        self._cw, self._ch = width, height
        self.bind("<Button-1>", lambda e: command())
        self.bind("<Enter>", lambda e: self._paint(hover=True))
        self.bind("<Leave>", lambda e: self._paint())
        self._paint()

    def _paint(self, hover=False):
        self.delete("all")
        if self._active:
            fill = blend(DEEP, .10)
        elif hover:
            fill = blend(DEEP, .05)
        else:
            fill = None
        if fill:
            draw_round(self, 0, 0, self._cw, self._ch, 20, fill)
        self.create_text(16, self._ch / 2, text=self._text, anchor="w",
                         font=F_BODY_B if self._active else F_BODY,
                         fill=T1_D if self._active else T2_D)

    def set_active(self, active):
        self._active = active
        self._paint()


class Spinner(tk.Canvas):
    """保存中的转圈指示器。"""

    def __init__(self, master, *, bg=None, size=18, color=None):
        bg = bg or SURFACE
        color = color or ACCENT_TRACK
        super().__init__(master, width=size, height=size, bg=bg,
                         highlightthickness=0, bd=0, takefocus=0)
        self._size = size
        self._color = color
        self._angle = 0
        self._job = None
        self._arc = self.create_arc(3, 3, size - 3, size - 3, start=0, extent=90,
                                    style="arc", outline=color, width=2)

    def start(self):
        self.stop()
        self._spin()

    def _spin(self):
        self._angle = (self._angle + 24) % 360
        self.itemconfigure(self._arc, start=self._angle)
        self._job = self.after(40, self._spin)

    def stop(self):
        if self._job:
            self.after_cancel(self._job)
            self._job = None


class StatusRing(tk.Canvas):
    """状态光环：外圈柔光 + 主环 + 中心点，颜色随网络状态变化。"""

    COLORS = {
        "online": "#30d158",    # 已认证
        "offline": "#ffd60a",   # 待认证
        "error": "#ff453a",     # 门户不可达
        "checking": "#93a0b3",  # 检测中 / 认证中
        "idle": "#8e8e93",      # 空闲
    }

    def __init__(self, master, *, bg=None, size=44):
        bg = bg or MID
        super().__init__(master, width=size, height=size, bg=bg,
                         highlightthickness=0, bd=0, takefocus=0)
        self._size = size
        self._bg = bg
        self._key = None
        self.set_state("idle")

    def set_state(self, key: str):
        key = key if key in self.COLORS else "idle"
        if key == self._key:
            return
        self._key = key
        self._render(self.COLORS[key])

    def _render(self, color: str):
        """光晕只模糊 alpha 通道，颜色保持纯净（否则透明像素会把光晕拖成灰色）。"""
        self.delete("all")
        size = self._size
        if not HAS_PIL:
            pad = 6
            self.create_oval(pad, pad, size - pad, size - pad, outline=color, width=3)
            self.create_oval(size / 2 - 4, size / 2 - 4, size / 2 + 4, size / 2 + 4, fill=color)
            return

        ss = 4
        big = size * ss
        pad = 6 * ss
        box = (pad, pad, big - pad - 1, big - pad - 1)

        # 光晕：先画 alpha 蒙版，再按状态色着色
        mask = Image.new("L", (big, big), 0)
        ImageDraw.Draw(mask).ellipse(box, outline=255, width=5 * ss)
        mask = mask.filter(ImageFilter.GaussianBlur(4 * ss))

        base = Image.new("RGB", (big, big), self._bg)
        glow = Image.new("RGB", (big, big), color)
        base.paste(glow, (0, 0), mask)

        # 主环 + 中心点
        ring = Image.new("L", (big, big), 0)
        rd = ImageDraw.Draw(ring)
        rd.ellipse(box, outline=255, width=3 * ss)
        base.paste(Image.new("RGB", (big, big), color), (0, 0), ring)

        cx = cy = big / 2
        r = 4.5 * ss
        dot = Image.new("L", (big, big), 0)
        ImageDraw.Draw(dot).ellipse((cx - r, cy - r, cx + r, cy + r), fill=255)
        base.paste(Image.new("RGB", (big, big), color), (0, 0), dot)

        img = base.resize((size, size), Image.LANCZOS)
        photo = ImageTk.PhotoImage(img)
        self._photo = photo
        self.create_image(0, 0, image=photo, anchor="nw")


class IconButton(tk.Canvas):
    """标题栏小按钮：AA 圆角，字符居中。"""

    def __init__(self, master, glyph, command, *, bg=None, width=32, height=24, danger=False):
        bg = bg or DEEP
        super().__init__(master, width=width, height=height, bg=bg,
                         highlightthickness=0, bd=0, takefocus=0)
        self._bg, self._glyph, self._command = bg, glyph, command
        self._danger = danger
        self._cw, self._ch = width, height
        self.bind("<Button-1>", lambda e: command())
        self.bind("<Enter>", lambda e: self._paint(True))
        self.bind("<Leave>", lambda e: self._paint(False))
        self._paint()

    def _paint(self, hover=False):
        self.delete("all")
        if hover:
            draw_round(self, 0, 0, self._cw, self._ch, 16,
                       DANGER if self._danger else blend(DEEP, .12), capsule=True)
        self.create_text(self._cw / 2, self._ch / 2, text=self._glyph,
                         fill=WHITE if hover else T2_D, font=(FONT, 10, "bold"))


# ---------------------------------------------------------------- 主窗口
class AskDialog(tk.Toplevel):
    """无边框三选一对话框，返回 save / discard / cancel。"""

    def __init__(self, master, title: str, message: str,
                 save_text="保存", discard_text="不保存", cancel_text="取消"):
        super().__init__(master)
        self.result = "cancel"
        self.withdraw()
        self.overrideredirect(True)
        self.configure(bg=MID)
        self.attributes("-topmost", True)
        self._master = master

        wrap = tk.Frame(self, bg=MID, padx=24, pady=20)
        wrap.pack(fill="both", expand=True)
        tk.Label(wrap, text=title, bg=MID, fg=T1, font=(FONT, 13, "bold"),
                 anchor="w").pack(fill="x")
        tk.Label(wrap, text=message, bg=MID, fg=T2, font=F_SMALL, anchor="w",
                 justify="left", wraplength=320).pack(fill="x", pady=(6, 16))

        row = tk.Frame(wrap, bg=MID)
        row.pack(fill="x")
        Button(row, cancel_text, lambda: self._done("cancel"), bg=MID, fill=SURFACE,
               width=76, height=32, font=F_SMALL).pack(side="right", padx=(8, 0))
        Button(row, discard_text, lambda: self._done("discard"), bg=MID, fill=SURFACE,
               width=84, height=32, font=F_SMALL).pack(side="right", padx=(8, 0))
        Button(row, save_text, lambda: self._done("save"), bg=MID, fill=ACCENT,
               accent=True, width=76, height=32, font=F_SMALL).pack(side="right")

        self.update_idletasks()
        w, h = max(360, self.winfo_reqwidth()), self.winfo_reqheight()
        mx = master.winfo_rootx() + (master.winfo_width() - w) // 2
        my = master.winfo_rooty() + (master.winfo_height() - h) // 2
        self.geometry("%dx%d+%d+%d" % (w, h, mx, my))
        self.deiconify()
        self.lift()
        self.grab_set()
        self._round()

    def _round(self):
        try:
            hwnd = ctypes.windll.user32.GetParent(self.winfo_id())
            w, h = self.winfo_width(), self.winfo_height()
            arr = (ctypes.c_long * 4)(0, 0, w, h)
            rgn = ctypes.windll.gdi32.CreatePolygonRgn(
                (ctypes.c_long * (len(squircle_points(0, 0, w, h, 18, steps=24)) * 2))(),
                0, 2)
            pts = squircle_points(0, 0, w, h, 18, steps=24)
            flat = [int(v) for pt in pts for v in pt]
            self._pts = (ctypes.c_long * len(flat))(*flat)
            rgn = ctypes.windll.gdi32.CreatePolygonRgn(self._pts, len(pts), 2)
            ctypes.windll.user32.SetWindowRgn(hwnd, rgn, True)
        except Exception:                               # noqa: BLE001
            pass

    def _done(self, value):
        self.result = value
        try:
            self.grab_release()
        except Exception:                               # noqa: BLE001
            pass
        self.destroy()

    def ask(self) -> str:
        try:
            self.wait_window(self)
        except Exception:                               # noqa: BLE001
            pass
        return self.result


class App(tk.Tk):
    def __init__(self, engine: Engine, start_hidden: bool = False):
        super().__init__()
        self.engine = engine
        self.withdraw()
        self.overrideredirect(True)
        self.configure(bg=DEEP)
        _size = os.environ.get("CNA_TK_SIZE", "1080x700")
        try:
            _w, _h = (int(v) for v in _size.lower().split("x"))
        except Exception:                               # noqa: BLE001
            _w, _h = 1080, 700
        self._init_size = (_w, _h)
        self.geometry("%dx%d+%d+%d" % (_w, _h, *self._center(_w, _h)))
        self._drag = None
        self._views: dict[str, tk.Frame] = {}
        self._navs: dict[str, NavItem] = {}
        self._current = "overview"
        self._sig = None
        self._last_seq = 0
        self._tray = None
        self.theme_mode = str(engine.cfg.get("theme", "system"))
        set_palette(resolve_theme(self.theme_mode))
        self._pending_interval = int(engine.cfg.get("interval", 20))
        self._first_run = not self._has_credentials()
        self._grips: dict[str, tk.Frame] = {}
        self._grip = None
        self._round_job = None
        self._saving = False
        self._min_size = (900, 560)
        self.minsize(*self._min_size)

        self._build()
        self._install_grips()
        self.bind("<Configure>", lambda e: e.widget is self and self._round_later())
        self.after(80, lambda: self._finish_show(start_hidden))
        self._start_tray()
        self.bind_all("<MouseWheel>", self._on_wheel)
        threading.Thread(target=engine.run_forever, daemon=True).start()
        self._tick()

    # ------------------------------------------------------------ 基础
    def _has_credentials(self) -> bool:
        cfg = self.engine.cfg
        return bool(cfg.get("user")) and bool(store.unprotect(cfg.get(store._ENC_FIELD, "")))

    def _center(self, w, h):
        return max(0, (self.winfo_screenwidth() - w) // 2), max(0, (self.winfo_screenheight() - h) // 2 - 20)

    def _apply_rounding(self):
        """窗口外轮廓：优先用 DWM 圆角（合成器抗锯齿），失败再退回区域裁剪。"""
        try:
            self.update_idletasks()
            hwnd = ctypes.windll.user32.GetParent(self.winfo_id())
            mode = os.environ.get("CNA_TK_ROUND") or self.engine.cfg.get("win_round", "big")
            if mode == "dwm":
                pref = ctypes.c_int(2)                  # DWMWCP_ROUND（小圆角但抗锯齿）
                ctypes.windll.dwmapi.DwmSetWindowAttribute(
                    hwnd, 33, ctypes.byref(pref), ctypes.sizeof(pref))
                ctypes.windll.user32.SetWindowRgn(hwnd, None, True)
                return
            # 关掉 DWM 圆角，避免和区域裁剪叠加
            pref = ctypes.c_int(1)                      # DWMWCP_DONOTROUND
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, 33, ctypes.byref(pref), ctypes.sizeof(pref))
        except Exception:                               # noqa: BLE001
            pass
        try:
            hwnd = ctypes.windll.user32.GetParent(self.winfo_id())
            w, h = max(200, self.winfo_width()), max(200, self.winfo_height())
            pts = squircle_points(0, 0, w, h, RADIUS_WIN, steps=80)
            flat = [int(v) for pt in pts for v in pt]
            arr = (ctypes.c_long * len(flat))(*flat)
            rgn = ctypes.windll.gdi32.CreatePolygonRgn(arr, len(pts), 2)
            ctypes.windll.user32.SetWindowRgn(hwnd, rgn, True)
        except Exception:                               # noqa: BLE001
            pass

    def _round_later(self):
        if self._round_job:
            self.after_cancel(self._round_job)
        self._round_job = self.after(120, self._apply_rounding)

    def _rebuild_ui(self):
        """换主题时整棵控件树重建（颜色都在创建时确定）。"""
        cur = self._current
        for child in list(self.winfo_children()):
            child.destroy()
        self._views.clear()
        self._navs.clear()
        self._scrolls = []
        self._build()
        self._install_grips()
        self._switch(cur)
        self._apply_rounding()

    def _set_theme(self, mode: str):
        self.theme_mode = mode
        self.engine.update_config({"theme": mode})
        set_palette(resolve_theme(mode))
        self._rebuild_ui()

    # ------------------------------------------------------------ 拖动改尺寸
    def _install_grips(self):
        cursors = {"n": "size_ns", "s": "size_ns", "e": "size_we", "w": "size_we",
                   "ne": "size_ne_sw", "sw": "size_ne_sw",
                   "nw": "size_nw_se", "se": "size_nw_se"}
        for name, cur in cursors.items():
            f = tk.Frame(self, bg=DEEP, cursor=cur)
            f.bind("<ButtonPress-1>", lambda e, n=name: self._grip_press(e, n))
            f.bind("<B1-Motion>", self._grip_motion)
            f.bind("<ButtonRelease-1>", lambda e: self._round_later())
            self._grips[name] = f
        self._place_grips()

    def _place_grips(self):
        g, c = 5, 10
        G = self._grips
        G["n"].place(x=0, y=0, relwidth=1, height=g)
        G["s"].place(x=0, rely=1, y=-g, relwidth=1, height=g)
        G["w"].place(x=0, y=0, width=g, relheight=1)
        G["e"].place(relx=1, x=-g, y=0, width=g, relheight=1)
        G["nw"].place(x=0, y=0, width=c, height=c)
        G["ne"].place(relx=1, x=-c, y=0, width=c, height=c)
        G["sw"].place(x=0, rely=1, y=-c, width=c, height=c)
        G["se"].place(relx=1, x=-c, rely=1, y=-c, width=c, height=c)
        for f in G.values():
            f.lift()

    def _grip_press(self, event, direction):
        self._grip = {
            "dir": direction, "sx": event.x_root, "sy": event.y_root,
            "x": self.winfo_x(), "y": self.winfo_y(),
            "w": self.winfo_width(), "h": self.winfo_height(),
        }

    def _grip_motion(self, event):
        g = self._grip
        if not g:
            return
        dx = event.x_root - g["sx"]
        dy = event.y_root - g["sy"]
        x, y, w, h = g["x"], g["y"], g["w"], g["h"]
        d = g["dir"]
        minw, minh = self._min_size
        if "e" in d:
            w = max(minw, w + dx)
        if "s" in d:
            h = max(minh, h + dy)
        if "w" in d:
            nw = max(minw, w - dx)
            x, w = x + (w - nw), nw
        if "n" in d:
            nh = max(minh, h - dy)
            y, h = y + (h - nh), nh
        self.geometry(f"{w}x{h}+{x}+{y}")

    def _finish_show(self, start_hidden: bool):
        self.title("校园网助手")
        if start_hidden:
            self._notify("校园网助手已启动", "在右下角托盘里，右键可操作")
            return
        self.deiconify()
        self.lift()
        w, h = self._init_size
        x, y = self._center(w, h)
        self.geometry("%dx%d+%d+%d" % (w, h, x, y))
        self.update_idletasks()
        self._apply_rounding()
        if self._first_run:
            self._switch("welcome")
            self.w_user.focus()
        forced = os.environ.get("CNA_TK_VIEW")
        if forced in ("overview", "settings", "logs", "about"):
            self._switch(forced)

    # ------------------------------------------------------------ 窗口行为
    def hide_to_tray(self):
        """点 × 只收进托盘，不退出。"""
        self.withdraw()
        self._notify("已收进托盘", "右键托盘图标可退出")

    def show_window(self):
        self.deiconify()
        self.lift()
        try:
            self.focus_force()
        except Exception:                               # noqa: BLE001
            pass
        self.update_idletasks()
        self._apply_rounding()

    def quit_app(self):
        self.engine.shutdown()
        if self._tray is not None:
            try:
                self._tray.stop()
            except Exception:                           # noqa: BLE001
                pass
        self.destroy()

    # ------------------------------------------------------------ 托盘
    def _start_tray(self):
        def state_fn():
            st = self.engine.state
            key = "online" if st.get("authenticated") else {
                "offline": "offline", "error": "error"}.get(st.get("phase"), "idle")
            return key, f"校园网助手 · {'已认证' if key == 'online' else '待认证'} · {st.get('ip') or '—'}", \
                bool(self.engine.cfg.get("auto_login"))

        def open_win():
            self.after(0, self.show_window)

        def do_check():
            threading.Thread(target=self.engine.check, daemon=True).start()

        def do_login():
            threading.Thread(target=self.engine.login, daemon=True).start()

        def toggle_auto():
            self.after(0, lambda: (self.engine.update_config(
                {"auto_login": not self.engine.cfg.get("auto_login")}), self._refresh()))

        def quit_app():
            self.after(0, self.quit_app)

        try:
            self._tray = tray_mod.Tray(os.path.join(store.APP_DIR, "assets"), state_fn,
                                       open_win, do_check, do_login, toggle_auto, quit_app,
                                       log=self.engine.log)
            self._tray.start()
        except Exception as exc:                        # noqa: BLE001
            self.engine.log(f"托盘初始化失败：{exc}", "warn")

    def _notify(self, title, text):
        if self._tray is not None:
            try:
                self._tray.notify(title, text)
            except Exception:                           # noqa: BLE001
                pass

    # ------------------------------------------------------------ 布局
    def _build(self):
        """层叠结构：窗口底 → 中栏卡片 → 内容卡片，一层压一层。"""
        self._build_titlebar()
        body = tk.Frame(self, bg=DEEP)
        body.pack(fill="both", expand=True)

        # 第一层：侧栏（与窗口底色同色，作为最底层）
        side = tk.Frame(body, bg=DEEP, width=200)
        side.pack(side="left", fill="y")
        side.pack_propagate(False)
        tk.Label(side, text="校园网助手", bg=DEEP, fg=T1_D, font=F_HEAD,
                 anchor="w", padx=18).pack(fill="x", pady=(14, 0))
        tk.Label(side, text="阳光学院 · 172.19.1.1", bg=DEEP, fg=T3_D, font=F_SMALL,
                 anchor="w", padx=18).pack(fill="x", pady=(2, 14))

        for key, label in (("overview", "总览"), ("settings", "设置"),
                           ("logs", "运行日志"), ("about", "关于")):
            item = NavItem(side, label, lambda k=key: self._switch_manual(k))
            item.pack(padx=10, pady=1)
            self._navs[key] = item

        self.side_status = tk.Label(side, text="● 检测中", bg=DEEP, fg=T3_D,
                                    font=F_SMALL, anchor="w", padx=18)
        self.side_status.pack(side="bottom", fill="x", pady=(0, 12))

        # 第二层：中栏卡片（浮在侧栏之上，四周留 8px）
        mid_card = Card(body, bg=DEEP, fill=MID, radius=RADIUS_CARD, padding=6)
        mid_card.pack(side="left", fill="both", expand=True, padx=(0, 6), pady=6)
        mid = mid_card.body

        status = tk.Frame(mid, bg=MID, width=272)
        status.pack(side="left", fill="y")
        status.pack_propagate(False)
        self._build_status(status)

        # 第三层：内容卡片（嵌在中栏卡片里，右上下一圈 8px 露出中栏色）
        content_card = Card(mid, bg=MID, fill=SURFACE, radius=RADIUS_CARD - 2, padding=6)
        content_card.pack(side="left", fill="both", expand=True, padx=(0, 6), pady=6)
        content = content_card.body
        self._content = content
        self._build_views(content)
        self._switch_manual(self._current)

    def _build_titlebar(self):
        bar = tk.Frame(self, bg=DEEP, height=34)
        bar.pack(fill="x")
        bar.pack_propagate(False)
        title = tk.Label(bar, text="校园网助手", bg=DEEP, fg=T3_D, font=F_SMALL)
        title.pack(side="left", padx=(24, 0))

        for text, cmd, danger in (("×", self.hide_to_tray, True), ("—", self._minimize, False)):
            IconButton(bar, text, cmd, bg=DEEP, danger=danger).pack(
                side="right", padx=(0, 10), pady=6)

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
        if self._drag:
            self.geometry(f"+{e.x_root - self._drag[0]}+{e.y_root - self._drag[1]}")

    # ------------------------------------------------------------ 中栏
    def _build_status(self, mid):
        tk.Label(mid, text="当前状态", bg=MID, fg=T3, font=F_SMALL,
                 anchor="w", padx=16).pack(fill="x", pady=(16, 2))
        head = tk.Frame(mid, bg=MID)
        head.pack(fill="x", padx=16)
        self.st_ring = StatusRing(head, bg=MID, size=46)
        self.st_ring.pack(side="left", pady=(2, 0))
        self.st_title = tk.Label(head, text="检测中", bg=MID, fg=T1, font=F_TITLE, anchor="w")
        self.st_title.pack(side="left", padx=(10, 0))
        self.st_msg = tk.Label(mid, text="正在读取认证门户", bg=MID, fg=T2, font=F_BODY,
                               anchor="w", justify="left", wraplength=252, padx=16, pady=4)
        self.st_msg.pack(fill="x")

        meta = tk.Frame(mid, bg=MID)
        meta.pack(fill="x", padx=16, pady=(8, 0))
        self.st_ip = self._meta_row(meta, "终端 IP")
        self.st_up = self._meta_row(meta, "本次在线")
        self.st_last = self._meta_row(meta, "上次登录")
        self.st_count = self._meta_row(meta, "登录次数")

        btns = tk.Frame(mid, bg=MID)
        btns.pack(fill="x", padx=16, pady=(16, 0))
        self.btn_login = Button(btns, "立即登录", self._do_login, bg=MID,
                                fill=ACCENT, width=260, height=38, accent=True)
        self.btn_login.pack(fill="x")
        row = tk.Frame(btns, bg=MID)
        row.pack(fill="x", pady=(8, 0))
        self.btn_check = Button(row, "重新检测", self._do_check, bg=MID, fill=SURFACE,
                                width=124, height=34)
        self.btn_check.pack(side="left", fill="x", expand=True)
        self.btn_logout = Button(row, "注销下线", self._do_logout, bg=MID, fill=SURFACE,
                                 width=124, height=34)
        self.btn_logout.pack(side="left", fill="x", expand=True, padx=(8, 0))

    def _meta_row(self, parent, label):
        row = tk.Frame(parent, bg=MID)
        row.pack(fill="x", pady=2)
        tk.Label(row, text=label, bg=MID, fg=T3, font=F_SMALL,
                 width=8, anchor="w").pack(side="left")
        val = tk.Label(row, text="—", bg=MID, fg=T1, font=F_BODY, anchor="w")
        val.pack(side="left", fill="x", expand=True)
        return val

    # ------------------------------------------------------------ 视图
    def _build_views(self, parent):
        self._views["welcome"] = self._view_welcome(parent)
        self._views["overview"] = self._view_overview(parent)
        self._views["settings"] = self._view_settings(parent)
        self._views["logs"] = self._view_logs(parent)
        self._views["about"] = self._view_about(parent)

    def _page_head(self, parent, title, sub=""):
        tk.Label(parent, text=title, bg=SURFACE, fg=T1_S, font=(FONT, 18, "bold"),
                 anchor="w").pack(fill="x", padx=16, pady=(10, 2))
        if sub:
            tk.Label(parent, text=sub, bg=SURFACE, fg=T3_S, font=F_SMALL,
                     anchor="w").pack(fill="x", padx=18)

    # 首次使用引导
    def _view_welcome(self, parent):
        frame = tk.Frame(parent, bg=SURFACE)
        box = tk.Frame(frame, bg=SURFACE)
        box.place(relx=0.5, rely=0.46, anchor="center")

        tk.Label(box, text="欢迎使用校园网助手", bg=SURFACE, fg=T1_S,
                 font=(FONT, 22, "bold")).pack(anchor="w")
        tk.Label(box, text="填写一次学号与密码，之后断网会自动帮你认证。",
                 bg=SURFACE, fg=T2_S, font=F_BODY).pack(anchor="w", pady=(6, 18))

        tk.Label(box, text="学号 / 工号", bg=SURFACE, fg=T2_S, font=F_SMALL).pack(anchor="w")
        self.w_user = RoundedEntry(box, bg=SURFACE, fill=MID, height=38)
        self.w_user.set_placeholder("请输入学号 / 工号")
        self.w_user.configure(width=360)
        self.w_user.pack(fill="x", pady=(4, 12))

        tk.Label(box, text="密码", bg=SURFACE, fg=T2_S, font=F_SMALL).pack(anchor="w")
        self.w_pass = RoundedEntry(box, bg=SURFACE, fill=MID, height=38, show="•")
        self.w_pass.set_placeholder("请输入密码")
        self.w_pass.configure(width=360)
        self.w_pass.pack(fill="x", pady=(4, 6))

        self.w_msg = tk.Label(box, text="", bg=SURFACE, fg=DANGER, font=F_SMALL, anchor="w")
        self.w_msg.pack(fill="x")

        Button(box, "保存并登录", self._welcome_submit, bg=SURFACE, fill=ACCENT,
               accent=True, width=360, height=40, font=(FONT, 11, "bold")).pack(pady=(12, 0))
        tk.Label(box, text="密码用 Windows DPAPI 加密保存在本机", bg=SURFACE,
                 fg=T3_S, font=F_SMALL).pack(pady=(10, 0))
        return frame

    def _welcome_submit(self):
        user = self.w_user.get().strip()
        pw = self.w_pass.get()
        if not user:
            self.w_msg.configure(text="请输入学号 / 工号")
            self.w_user.focus()
            return
        if not pw:
            self.w_msg.configure(text="请输入密码")
            self.w_pass.focus()
            return
        self._first_run = False
        self.engine.update_config({"user": user, "password": pw})
        self._sync_settings()
        self._switch("overview")
        threading.Thread(target=self.engine.login, daemon=True).start()

    # 总览
    def _view_overview(self, parent):
        frame = tk.Frame(parent, bg=SURFACE)
        self._page_head(frame, "概览", "认证状态、连接详情与最近活动")

        row = tk.Frame(frame, bg=SURFACE)
        row.pack(fill="x", padx=18, pady=(16, 0))
        self._ov_auth = self._stat_card(row, "认证状态")
        self._ov_uptime = self._stat_card(row, "本次在线")
        self._ov_conn = self._stat_card(row, "外网连通")

        head = tk.Frame(frame, bg=SURFACE)
        head.pack(fill="x", padx=18, pady=(20, 8))
        tk.Label(head, text="最近活动", bg=SURFACE, fg=T2_S, font=F_HEAD).pack(side="left")

        holder = tk.Frame(frame, bg=SURFACE)
        holder.pack(fill="both", expand=True, padx=18, pady=(0, 22))
        self._ov_log = RoundedText(holder, bg=SURFACE, fill=MID, height=180)
        self._ov_log.pack(fill="both", expand=True)
        self._ov_log.text.configure(state="disabled")
        self._ov_log.text.tag_configure("ok", foreground="#5fd38d")
        self._ov_log.text.tag_configure("warn", foreground="#e7c46b")
        self._ov_log.text.tag_configure("error", foreground="#ff8a80")
        self._ov_log.text.tag_configure("time", foreground=T3)
        return frame

    def _stat_card(self, parent, label):
        card = Card(parent, bg=SURFACE, fill=MID, radius=RADIUS_CARD, padding=14)
        card.pack(side="left", fill="x", expand=True, padx=(0, 10))
        tk.Label(card.body, text=label, bg=MID, fg=T3, font=F_SMALL, anchor="w").pack(fill="x")
        val = tk.Label(card.body, text="—", bg=MID, fg=T1, font=(FONT, 14, "bold"), anchor="w")
        val.pack(fill="x", pady=(4, 0))
        card.configure(height=78)
        card.pack_propagate(False)
        return val

    # 日志
    def _view_logs(self, parent):
        frame = tk.Frame(parent, bg=SURFACE)
        head = tk.Frame(frame, bg=SURFACE)
        head.pack(fill="x", padx=18, pady=(22, 10))
        tk.Label(head, text="运行日志", bg=SURFACE, fg=T1_S, font=F_TITLE).pack(side="left")
        Button(head, "清空", self._clear_logs, bg=SURFACE, fill=MID,
               width=72, height=30, font=F_SMALL).pack(side="right")

        holder = tk.Frame(frame, bg=SURFACE)
        holder.pack(fill="both", expand=True, padx=18, pady=(0, 22))
        self._log_view = RoundedText(holder, bg=SURFACE, fill=MID)
        self._log_view.pack(fill="both", expand=True)
        self._log_view.text.configure(state="disabled")
        self._log_view.text.tag_configure("ok", foreground="#5fd38d")
        self._log_view.text.tag_configure("warn", foreground="#e7c46b")
        self._log_view.text.tag_configure("error", foreground="#ff8a80")
        self._log_view.text.tag_configure("time", foreground=T3)
        return frame

    # 设置
    def _view_settings(self, parent):
        view = tk.Frame(parent, bg=SURFACE)
        scroll = ScrollFrame(view, bg=SURFACE)
        scroll.pack(fill="both", expand=True, pady=(0, 50))
        if not hasattr(self, "_scrolls"):
            self._scrolls = []
        self._scrolls.append(scroll)
        outer = scroll.inner
        self._page_head(outer, "设置", "修改后点底部保存生效")
        outer.winfo_children()[0].pack_configure(pady=(8, 0))

        body = tk.Frame(outer, bg=SURFACE)
        body.pack(fill="both", expand=True, padx=18, pady=(2, 0))

        tk.Label(body, text="账号", bg=SURFACE, fg=T3_S, font=F_SMALL_B,
                 anchor="w").pack(fill="x", pady=(0, 6))
        self.v_user = self._field(body, "学号 / 工号")
        self.v_pass = self._field(body, "密码", secret=True)
        self.v_remember = self._make_switch(body, "记住密码", "用 DPAPI 加密保存在本机", compact=True)

        tk.Label(body, text="自动认证", bg=SURFACE, fg=T3_S, font=F_SMALL_B,
                 anchor="w").pack(fill="x", pady=(4, 2))
        self.v_auto = self._make_switch(body, "断网自动登录", "")

        srow = tk.Frame(body, bg=SURFACE)
        srow.pack(fill="x", pady=(6, 0))
        tk.Label(srow, text="检测周期", bg=SURFACE, fg=T2_S, font=F_BODY,
                 width=8, anchor="w").pack(side="left")
        self.v_interval = Slider(srow, self._pending_interval, 5, 3600, 5,
                                 command=self._on_interval, bg=SURFACE, width=170)
        self.v_interval.pack(side="left", padx=(0, 8))
        self.v_interval_entry = RoundedEntry(srow, bg=SURFACE, fill=MID,
                                             height=28, justify="center")
        self.v_interval_entry.configure(width=64)
        self.v_interval_entry.set(str(self._pending_interval))
        self.v_interval_entry.pack(side="left")
        self.v_interval_entry.entry.bind("<Return>", self._apply_interval_entry)
        self.v_interval_entry.entry.bind("<FocusOut>", self._apply_interval_entry)
        tk.Label(srow, text="秒", bg=SURFACE, fg=T2_S, font=F_SMALL).pack(side="left", padx=(6, 0))

        rrow = tk.Frame(body, bg=SURFACE)
        rrow.pack(fill="x", pady=(6, 0))
        self.v_timeout = self._field_inline(rrow, "请求超时（秒）", self.engine.cfg.get("timeout", 6))
        self.v_retry = self._field_inline(rrow, "失败重试", self.engine.cfg.get("retry", 3))

        self.v_base = self._field(body, "认证门户地址")

        trow = tk.Frame(body, bg=SURFACE)
        trow.pack(fill="x", pady=(6, 0))
        tk.Label(trow, text="主题", bg=SURFACE, fg=T2_S, font=F_BODY, width=8,
                 anchor="w").pack(side="left")
        self._theme_btns = {}
        for mode, label in (("system", "跟随系统"), ("light", "明亮"), ("dark", "黑夜")):
            btn = Button(trow, label, lambda m=mode: self._set_theme(m), bg=SURFACE,
                         fill=ACCENT_TRACK if self.theme_mode == mode else MID,
                         width=74, height=26, font=F_SMALL,
                         fg=ACCENT_FG if self.theme_mode == mode else None,
                         accent=(self.theme_mode == mode))
            btn.pack(side="left", padx=(0, 6))
            self._theme_btns[mode] = btn

        tk.Label(body, text="系统", bg=SURFACE, fg=T3_S, font=F_SMALL_B,
                 anchor="w").pack(fill="x", pady=(6, 2))
        pair = tk.Frame(body, bg=SURFACE)
        pair.pack(fill="x")
        self.v_startup = self._make_switch(pair, "开机自启", "", side="left")
        self.v_autoclose = self._make_switch(pair, "关闭退出", "", side="left")
        self.v_round = self._make_switch(pair, "平滑外框", "", side="left")

        # 悬浮保存按钮：固定右下角，滚动内容不会跑到它下面
        self.save_btn = Button(view, "保存设置", self._save_settings, bg=SURFACE,
                               fill=ACCENT, accent=True, width=132, height=38)
        self.save_btn.place(relx=1.0, rely=1.0, x=-18, y=-8, anchor="se")
        self.save_spinner = Spinner(view, bg=SURFACE, size=18)

        return view

    def _field(self, parent, label, *, secret=False):
        lbl = tk.Label(parent, text=label, bg=SURFACE, fg=T2_S, font=F_SMALL, anchor="w")
        lbl.pack(fill="x", pady=(1, 1))
        entry = RoundedEntry(parent, bg=SURFACE, fill=MID, height=28,
                             show="•" if secret else None)
        entry.pack(fill="x")
        entry._lbl = lbl
        return entry

    def _field_inline(self, parent, label, value):
        box = tk.Frame(parent, bg=SURFACE)
        box.pack(side="left", fill="x", expand=True, padx=(0, 10))
        tk.Label(box, text=label, bg=SURFACE, fg=T2_S, font=F_SMALL,
                 anchor="w").pack(fill="x", pady=(0, 4))
        entry = RoundedEntry(box, bg=SURFACE, fill=MID, height=32)
        entry.pack(fill="x")
        entry.set(str(value))
        return entry

    def _make_switch(self, parent, label, sub="", *, compact=False, side=None):
        row = tk.Frame(parent, bg=SURFACE)
        if side:
            row.pack(side=side, fill="x", expand=True, pady=3, padx=(0, 8))
        else:
            row.pack(fill="x", pady=0)
        txt = tk.Frame(row, bg=SURFACE)
        txt.pack(side="left", fill="x", expand=True)
        tk.Label(txt, text=label, bg=SURFACE, fg=T1_S, font=F_BODY, anchor="w").pack(fill="x")
        if sub:
            tk.Label(txt, text=sub, bg=SURFACE, fg=T3_S, font=F_SMALL, anchor="w").pack(fill="x")
        toggle = Toggle(row, False, bg=SURFACE)
        toggle.pack(side="right", padx=(10, 2))
        return toggle

    # 关于
    def _view_about(self, parent):
        frame = tk.Frame(parent, bg=SURFACE)
        self._page_head(frame, "关于", "Tk 原生界面版 · GPL-3.0")
        lines = [
            ("版本", "v2.1.0（Tk 版）"),
            ("界面", "tkinter 原生控件，无 WebView、无本地 HTTP 服务"),
            ("字体", "系统黑体（微软雅黑），仅用字重区分层级"),
            ("许可证", "GNU General Public License v3.0"),
            ("仓库", "github.com/Heznc/campus-net-assistant-tk"),
            ("数据目录", store.DATA_DIR),
        ]
        for key, val in lines:
            row = tk.Frame(frame, bg=SURFACE)
            row.pack(fill="x", padx=18, pady=6)
            tk.Label(row, text=key, bg=SURFACE, fg=T3_S, font=F_SMALL,
                     width=8, anchor="w").pack(side="left")
            tk.Label(row, text=val, bg=SURFACE, fg=T2_S, font=F_BODY,
                     anchor="w", justify="left").pack(side="left")
        return frame

    def _mark_baseline(self):
        self._baseline = self._snapshot()

    # ------------------------------------------------------------ 未保存检查
    def _snapshot(self):
        try:
            return (
                self.v_user.get().strip(),
                self.v_pass.get(),
                self.v_remember.value,
                self.v_auto.value,
                int(self._pending_interval),
                self.v_timeout.get().strip(),
                self.v_retry.get().strip(),
                self.v_base.get().strip(),
                self.v_startup.value,
                self.v_autoclose.value,
                self.v_round.value,
            )
        except Exception:                               # noqa: BLE001
            return None

    def _is_dirty(self) -> bool:
        if not hasattr(self, "v_user"):
            return False
        snap = self._snapshot()
        return snap is not None and snap != self._baseline

    def _confirm_leave_settings(self) -> bool:
        """切走设置页且存在未保存改动时询问。"""
        if not self._is_dirty():
            return True
        dlg = AskDialog(self, "有未保存的更改",
                        "设置已经修改但还没保存，要现在保存吗？")
        choice = dlg.ask()
        if choice == "cancel":
            return False
        if choice == "save":
            self._save_settings()
        else:
            self._sync_settings()
        return True

    # ------------------------------------------------------------ 行为
    def _switch_manual(self, key):
        self._switch(key)

    def _switch(self, key):
        if key != "settings" and getattr(self, "_current", None) == "settings":
            if not self._confirm_leave_settings():
                return
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
        if self._first_run:
            self._switch("welcome")
            self.w_user.focus()
        forced = os.environ.get("CNA_TK_VIEW")
        if forced in ("overview", "settings", "logs", "about"):
            self._switch(forced)
            return
        threading.Thread(target=self.engine.login, daemon=True).start()

    def _do_logout(self):
        from tkinter import messagebox
        if not messagebox.askyesno("校园网助手", "确定注销当前上网认证？", parent=self):
            return
        threading.Thread(target=self.engine.logout, daemon=True).start()

    def _clear_logs(self):
        t = self._log_view.text
        t.configure(state="normal")
        t.delete("1.0", "end")
        t.configure(state="disabled")

    def _on_interval(self, value):
        self._pending_interval = int(value)
        self.v_interval_entry.set(str(self._pending_interval))

    def _apply_interval_entry(self, _event=None):
        """手动输入秒数：回车或失焦时校验并同步滑杆。"""
        raw = self.v_interval_entry.get().strip()
        try:
            value = int(float(raw))
        except (TypeError, ValueError):
            value = self._pending_interval
        value = max(5, min(3600, value))
        self._pending_interval = value
        self.v_interval.set_value(value)
        self.v_interval_entry.set(str(value))
        return "break"

    def _save_settings(self):
        if self._saving:
            return
        patch = {
            "user": self.v_user.get().strip(),
            "auto_login": self.v_auto.value,
            "remember": self.v_remember.value,
            "autostart": self.v_startup.value,
            "auto_close": self.v_autoclose.value,
            "win_round": "dwm" if self.v_round.value else "big",
            "interval": self._pending_interval,
            "base_url": self.v_base.get().strip(),
        }
        try:
            patch["timeout"] = int(self.v_timeout.get())
            patch["retry"] = int(self.v_retry.get())
        except (TypeError, ValueError):
            pass
        pw = self.v_pass.get()
        if pw:
            patch["password"] = pw

        self._saving = True
        self.save_btn.set_enabled(False)
        self.save_spinner.place(relx=1.0, rely=1.0, x=-162, y=-35, anchor="se")
        self.save_spinner.start()

        self._save_patch, self._save_pw = patch, pw
        self._save_flag = False

        def work():
            try:
                self.engine.update_config(patch)
            except Exception as exc:                    # noqa: BLE001
                self.engine.log(f"保存设置失败：{exc}", "error")
            self._save_flag = True                      # 只置标志，UI 由主线程收尾

        threading.Thread(target=work, daemon=True).start()
        self.after(80, self._poll_save)

    def _poll_save(self):
        """主线程轮询：保存完成后收尾（跨线程不直接调 Tk）。"""
        if not self._save_flag:
            self.after(80, self._poll_save)
            return
        self._save_done(self._save_patch, self._save_pw)

    def _save_done(self, patch, pw):
        self._saving = False
        try:
            self.save_spinner.stop()
            self.save_spinner.place_forget()
            self.save_btn.set_enabled(True)
        except Exception:                               # noqa: BLE001
            pass
        self._apply_rounding()
        self.v_pass.clear()
        if patch.get("user") and (pw or self._has_credentials()):
            self._first_run = False
        self._sync_settings()
        self._refresh()

    def _sync_settings(self):
        cfg = self.engine.cfg
        has_pw = bool(store.unprotect(cfg.get(store._ENC_FIELD, "")))
        if has_pw:
            self.v_pass.set_placeholder("•" * 10, blend(SURFACE, .38))
            self.v_pass._lbl.configure(text="密码   ·   已保存，留空则不修改")
        else:
            self.v_pass.set_placeholder("请输入密码", blend(SURFACE, .45))
            self.v_pass._lbl.configure(text="密码   ·   尚未保存，填写后才能自动认证")
        self.v_user.set(cfg.get("user", ""))
        self.v_user.set_placeholder("请输入学号 / 工号")
        self.after_idle(self._mark_baseline)
        self.v_base.set(cfg.get("base_url", ""))
        self.v_timeout.set(str(cfg.get("timeout", 6)))
        self.v_retry.set(str(cfg.get("retry", 3)))
        self._pending_interval = int(cfg.get("interval", 20))
        self.v_interval.set_value(self._pending_interval)
        self.v_interval_entry.set(str(self._pending_interval))
        self.v_round._on = str(cfg.get("win_round", "big")) == "dwm"
        self.v_round._paint()
        for widget, value in ((self.v_remember, cfg.get("remember", True)),
                              (self.v_auto, cfg.get("auto_login", True)),
                              (self.v_startup, store.get_autostart()),
                              (self.v_autoclose, cfg.get("auto_close", False))):
            widget._on = bool(value)
            widget._paint()

    # ------------------------------------------------------------ 刷新
    def _on_wheel(self, event):
        for sf in getattr(self, "_scrolls", []):
            try:
                if sf.contains(event.widget) and sf.scroll_by(int(-event.delta / 120)):
                    return "break"
            except Exception:                           # noqa: BLE001
                pass
        return None

    def _tick(self):
        try:
            if self.theme_mode == "system" and resolve_theme("system") != DARK:
                set_palette(not DARK)
                self._rebuild_ui()
                return self.after(1000, self._tick)
            self._refresh()
        except Exception:                               # noqa: BLE001
            pass
        self.after(1000, self._tick)

    def _refresh(self):
        st = self.engine.snapshot()
        sig = (st["phase"], st["authenticated"], st["ip"], st["message"],
               st["login_count"], st["last_login"], len(self.engine.logs))
        if sig == self._sig:
            up = self._fmt_dur(st["online_seconds"]) if st["authenticated"] else "—"
            self.st_up.configure(text=up)
            if self._current == "overview":
                self._ov_uptime.configure(text=up)
            return
        self._sig = sig
        self._render(st)

    def _render(self, st):
        titles = {"online": "网络已连接", "offline": "需要上网认证",
                  "logging_in": "正在认证", "checking": "正在检测",
                  "error": "认证门户不可达", "idle": "准备就绪"}
        self.st_title.configure(text=titles.get(st["phase"], "准备就绪"))
        if st.get("authenticated"):
            ring_key = "online"
        elif st["phase"] in ("checking", "logging_in"):
            ring_key = "checking"
        elif st["portal_reachable"]:
            ring_key = "offline"
        elif st["phase"] == "error":
            ring_key = "error"
        else:
            ring_key = "idle"
        self.st_ring.set_state(ring_key)
        self.st_msg.configure(text=st["message"] or "")
        self.st_ip.configure(text=st["ip"] or "—")
        self.st_last.configure(text=self._fmt_time(st["last_login"]))
        self.st_count.configure(text=str(st["login_count"]))
        up = self._fmt_dur(st["online_seconds"]) if st["authenticated"] else "—"
        self.st_up.configure(text=up)
        if self._current == "overview":
            self._ov_uptime.configure(text=up)
            self._ov_auth.configure(text="已认证" if st["authenticated"] else "未认证")
            self._ov_conn.configure(text="可达" if st["internet"]
                                    else ("内网" if st["portal_reachable"] else "断开"))

        state_text = "已认证" if st["authenticated"] else (
            "待认证" if st["portal_reachable"] else "未连接")
        self.side_status.configure(
            text=f"● {state_text}",
            fg="#5fd38d" if st["authenticated"] else
               ("#e7c46b" if st["portal_reachable"] else T3_D))
        self._render_logs()

    def _render_logs(self, full=False):
        widget = self._ov_log.text if not full else self._log_view.text
        widget.configure(state="normal")
        if full:
            widget.delete("1.0", "end")
            entries = self.engine.logs[-200:]
        else:
            entries = [e for e in self.engine.logs if e["seq"] > self._last_seq][-8:]
            if entries:
                self._last_seq = entries[-1]["seq"]
        for e in entries:
            tag = e["level"] if e["level"] in ("ok", "warn", "error") else ""
            widget.insert("end", f"{e['t']}  ", "time")
            widget.insert("end", e["msg"] + "\n", tag)
        widget.see("end")
        widget.configure(state="disabled")

    @staticmethod
    def _fmt_dur(sec):
        sec = max(0, int(sec or 0))
        if sec < 60:
            return f"{sec} 秒"
        m, h = sec // 60, sec // 3600
        return f"{h} 小时 {m % 60} 分" if h else f"{m} 分 {sec % 60} 秒"

    @staticmethod
    def _fmt_time(ts):
        return time.strftime("%m-%d %H:%M", time.localtime(ts)) if ts else "—"


def acquire_single_instance() -> bool:
    try:
        handle = ctypes.windll.kernel32.CreateMutexW(None, False, "CampusNetAssistantTK")
        if ctypes.windll.kernel32.GetLastError() == 183:
            return False
        globals()["_MUTEX"] = handle
        return True
    except Exception:                                   # noqa: BLE001
        return True


def _hide_console() -> None:
    """用 python.exe 启动时把自带的黑色控制台窗口藏起来。"""
    try:
        hwnd = ctypes.windll.kernel32.GetConsoleWindow()
        if hwnd:
            ctypes.windll.user32.ShowWindow(hwnd, 0)
    except Exception:                                   # noqa: BLE001
        pass


def main() -> int:
    _hide_console()
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
