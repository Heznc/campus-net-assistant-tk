"""Windows 任务栏通知区（托盘）图标 —— 纯 ctypes 实现，无第三方依赖。

提供：左键双击打开界面、右键菜单（打开界面 / 立即检测 / 立即登录 /
自动登录开关 / 退出）、随认证状态变色的图标与悬浮提示。
"""

from __future__ import annotations

import ctypes
import os
import threading
import time
from ctypes import wintypes as w

user32 = ctypes.WinDLL("user32", use_last_error=True)
shell32 = ctypes.WinDLL("shell32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

# ---------------------------------------------------------------- 常量
WM_DESTROY = 0x0002
WM_CLOSE = 0x0010
WM_COMMAND = 0x0111
WM_TIMER = 0x0113
WM_NULL = 0x0000
WM_LBUTTONUP = 0x0202
WM_LBUTTONDBLCLK = 0x0203
WM_RBUTTONUP = 0x0205
WM_TRAY = 0x0400 + 20          # WM_USER + 20

NIM_ADD, NIM_MODIFY, NIM_DELETE = 0, 1, 2
NIF_MESSAGE, NIF_ICON, NIF_TIP, NIF_INFO = 0x1, 0x2, 0x4, 0x10

MF_STRING, MF_SEPARATOR, MF_CHECKED, MF_GRAYED, MF_DEFAULT = 0x0, 0x800, 0x8, 0x1, 0x1000
TPM_RIGHTBUTTON, TPM_RETURNCMD, TPM_NONOTIFY = 0x2, 0x100, 0x80
TPM_LEFTALIGN, TPM_BOTTOMALIGN = 0x0, 0x20

IMAGE_ICON = 1
LR_LOADFROMFILE, LR_DEFAULTSIZE = 0x10, 0x40

IDI_APPLICATION = 32512

ID_OPEN, ID_CHECK, ID_LOGIN, ID_AUTO, ID_QUIT = 1, 2, 3, 4, 9

TASKBAR_CREATED = user32.RegisterWindowMessageW("TaskbarCreated")
TIMER_ID = 7
TIMER_MS = 2000

WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, w.HWND, w.UINT, w.WPARAM, w.LPARAM)


class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize", w.UINT), ("style", w.UINT), ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
        ("hInstance", w.HINSTANCE), ("hIcon", w.HICON), ("hCursor", w.HANDLE),
        ("hbrBackground", w.HBRUSH), ("lpszMenuName", w.LPCWSTR),
        ("lpszClassName", w.LPCWSTR), ("hIconSm", w.HICON),
    ]


class NOTIFYICONDATAW(ctypes.Structure):
    """对齐 64 位 Vista+ 布局（含 guidItem / hBalloonIcon），cbSize 必须精确。"""
    _fields_ = [
        ("cbSize", w.DWORD), ("hWnd", w.HWND), ("uID", w.UINT),
        ("uFlags", w.UINT), ("uCallbackMessage", w.UINT), ("hIcon", w.HICON),
        ("szTip", w.WCHAR * 128), ("dwState", w.DWORD), ("dwStateMask", w.DWORD),
        ("szInfo", w.WCHAR * 256), ("uVersion", w.UINT),
        ("szInfoTitle", w.WCHAR * 64), ("dwInfoFlags", w.DWORD),
        ("guidItem", ctypes.c_byte * 16), ("hBalloonIcon", w.HICON),
    ]


class POINT(ctypes.Structure):
    _fields_ = [("x", w.LONG), ("y", w.LONG)]


user32.CreatePopupMenu.restype = w.HMENU
user32.LoadImageW.restype = w.HANDLE
user32.LoadImageW.argtypes = [w.HINSTANCE, w.LPCWSTR, w.UINT,
                              ctypes.c_int, ctypes.c_int, w.UINT]
user32.CreateWindowExW.restype = w.HWND
user32.DefWindowProcW.restype = ctypes.c_ssize_t
user32.DefWindowProcW.argtypes = [w.HWND, w.UINT, w.WPARAM, w.LPARAM]
user32.TrackPopupMenu.restype = ctypes.c_int
user32.TrackPopupMenu.argtypes = [w.HMENU, w.UINT, ctypes.c_int, ctypes.c_int,
                                  ctypes.c_int, w.HWND, ctypes.c_void_p]
shell32.Shell_NotifyIconW.argtypes = [w.DWORD, ctypes.POINTER(NOTIFYICONDATAW)]


class Tray:
    """托盘图标。所有回调都在托盘线程里触发，回调本身应快速返回。"""

    def __init__(self, icon_dir: str, state_fn, on_open, on_check, on_login,
                 on_toggle_auto, on_quit, log=None, on_ready=None):
        self.icon_dir = icon_dir
        self.state_fn = state_fn          # () -> (icon_key, tooltip, auto_login_bool)
        self.on_open = on_open
        self.on_check = on_check
        self.on_login = on_login
        self.on_toggle_auto = on_toggle_auto
        self.on_quit = on_quit
        self.log = log or (lambda *a, **k: None)
        self._on_ready = on_ready
        self.hwnd = None
        self._icons: dict[str, int] = {}
        self._current_key = None
        self._thread: threading.Thread | None = None
        self._proc = WNDPROC(self._wndproc)
        self.ok = False

    # ------------------------------------------------------------- 图标
    def _icon_for(self, key: str) -> int:
        if key in self._icons:
            return self._icons[key]
        path = os.path.join(self.icon_dir, f"tray_{key}.ico")
        handle = 0
        if os.path.isfile(path):
            handle = user32.LoadImageW(None, path, IMAGE_ICON, 16, 16, LR_LOADFROMFILE) or 0
        if not handle:
            handle = user32.LoadImageW(None, ctypes.cast(IDI_APPLICATION, w.LPCWSTR),
                                       IMAGE_ICON, 16, 16, LR_DEFAULTSIZE) or 0
        self._icons[key] = handle
        return handle

    # ------------------------------------------------------------- 图标数据
    def _nid(self) -> NOTIFYICONDATAW:
        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        nid.hWnd = self.hwnd
        nid.uID = 1
        return nid

    def _make_window(self, style: int, size: int) -> int:
        """用指定样式重建托盘窗口，返回句柄。"""
        if self.hwnd:
            try:
                user32.DestroyWindow(self.hwnd)
            except Exception:                           # noqa: BLE001
                pass
            self.hwnd = 0
        hinst = kernel32.GetModuleHandleW(None)
        self.hwnd = user32.CreateWindowExW(
            0, "CampusNetAssistantTray", "校园网助手",
            style, 0, 0, size, size, None, None, hinst, None)
        return self.hwnd or 0

    def _try_add(self, key: str, tip: str) -> bool:
        nid = self._nid()
        nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        nid.uCallbackMessage = WM_TRAY
        nid.hIcon = self._icon_for(key)
        nid.szTip = tip[:120]
        ctypes.set_last_error(0)
        ok = bool(shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid)))
        return ok

    def _add(self) -> None:
        """注册托盘图标：窗口样式/大小换几种组合重试。"""
        key, tip, _ = self.state_fn()
        WS_OVERLAPPED = 0
        WS_POPUP = 0x80000000
        variants = [(WS_POPUP, 1), (WS_OVERLAPPED, 1), (WS_POPUP, 16)]
        last_err = 0
        for rnd in range(2):
            for style, size in variants:
                hwnd = self._make_window(style, size)
                if not hwnd:
                    last_err = ctypes.get_last_error()
                    self.log(f"托盘窗口创建失败 style=0x{style:x} err={last_err}", "warn")
                    continue
                if self._try_add(key, tip):
                    self.ok = True
                    if self._on_ready:
                        try:
                            self._on_ready()
                        except Exception:               # noqa: BLE001
                            pass
                    self._current_key = key
                    self.log(f"托盘图标已就绪（右键可操作，style=0x{style:x} {size}px）", "info")
                    self._promote()
                    self._balloon("校园网助手已启动", "右键图标可快速检测 / 登录 / 退出")
                    return
                last_err = ctypes.get_last_error()
                self.log(f"托盘注册失败 style=0x{style:x} {size}px hwnd={hwnd} err={last_err}",
                         "warn")
                time.sleep(0.4)
            time.sleep(0.6)
        icon = self._icon_for(key)
        self.log(f"托盘图标创建失败：Shell_NotifyIcon 返回 0，err={last_err}，hIcon={icon}", "warn")

    def _promote(self) -> None:
        """Windows 11 默认把新图标收进“^”里，这里尽量把它设为常驻显示。"""
        import sys as _sys
        try:
            import winreg
        except ImportError:
            return
        exe = os.path.basename(_sys.executable).lower()
        try:
            root = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                  r"Control Panel\NotifyIconSettings")
        except OSError:
            return
        changed = 0
        index = 0
        while True:
            try:
                name = winreg.EnumKey(root, index)
            except OSError:
                break
            index += 1
            try:
                with winreg.OpenKey(root, name) as key:
                    path, _ = winreg.QueryValueEx(key, "ExecutablePath")
            except OSError:
                continue
            if not path or os.path.basename(path).lower() != exe:
                continue
            try:
                with winreg.OpenKey(root, name, 0, winreg.KEY_SET_VALUE) as key:
                    try:
                        promoted, _ = winreg.QueryValueEx(key, "IsPromoted")
                    except OSError:
                        promoted = 0
                    if promoted != 1:
                        winreg.SetValueEx(key, "IsPromoted", 0, winreg.REG_DWORD, 1)
                        changed += 1
            except OSError:
                continue
        if changed:
            self.log("已将托盘图标设为常驻显示（资源管理器重启后生效）", "info")

    def _refresh(self) -> None:
        if not self.ok:
            return
        key, tip, _ = self.state_fn()
        nid = self._nid()
        nid.uFlags = NIF_ICON | NIF_TIP
        nid.hIcon = self._icon_for(key)
        nid.szTip = tip[:120]
        if shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(nid)):
            self._current_key = key

    def _balloon(self, title: str, text: str) -> None:
        if not self.ok:
            return
        nid = self._nid()
        nid.uFlags = NIF_INFO
        nid.szInfoTitle = title[:63]
        nid.szInfo = text[:255]
        nid.dwInfoFlags = 0x1          # NIIF_INFO
        nid.uVersion = 2000
        shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(nid))

    # ------------------------------------------------------------- 菜单
    def _show_menu(self) -> None:
        _, _, auto = self.state_fn()
        menu = user32.CreatePopupMenu()
        user32.AppendMenuW(menu, MF_STRING | MF_DEFAULT, ID_OPEN, "打开界面")
        user32.AppendMenuW(menu, MF_STRING, ID_CHECK, "立即检测网络")
        user32.AppendMenuW(menu, MF_STRING, ID_LOGIN, "立即登录")
        user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
        user32.AppendMenuW(menu, MF_STRING, ID_AUTO,
                           "断网自动登录：" + ("开" if auto else "关"))
        user32.AppendMenuW(menu, MF_SEPARATOR, 0, None)
        user32.AppendMenuW(menu, MF_STRING, ID_QUIT, "退出")

        pt = POINT()
        user32.GetCursorPos(ctypes.byref(pt))
        user32.SetForegroundWindow(self.hwnd)
        cmd = user32.TrackPopupMenu(
            menu, TPM_RIGHTBUTTON | TPM_RETURNCMD | TPM_NONOTIFY,
            pt.x, pt.y, 0, self.hwnd, None)
        user32.PostMessageW(self.hwnd, WM_NULL, 0, 0)
        user32.DestroyMenu(menu)

        if cmd == ID_OPEN:
            self.on_open()
        elif cmd == ID_CHECK:
            self.on_check()
        elif cmd == ID_LOGIN:
            self.on_login()
        elif cmd == ID_AUTO:
            self.on_toggle_auto()
            self._refresh()
        elif cmd == ID_QUIT:
            self.on_quit()

    # ------------------------------------------------------------- 窗口过程
    def _wndproc(self, hwnd, msg, wparam, lparam):
        try:
            if msg == WM_TRAY:
                event = lparam & 0xFFFF
                if event in (WM_LBUTTONDBLCLK, WM_LBUTTONUP):
                    self.on_open()
                elif event == WM_RBUTTONUP:
                    self._show_menu()
                return 0
            if msg == TASKBAR_CREATED:
                # 资源管理器重启，之前的图标全没了，重新注册
                self.ok = False
                self._add()
                return 0
            if msg == WM_TIMER and wparam == TIMER_ID:
                if not self.ok:
                    self._add()
                else:
                    self._refresh()
                return 0
            if msg == WM_COMMAND:
                return 0
            if msg == WM_CLOSE:
                user32.DestroyWindow(hwnd)
                return 0
            if msg == WM_DESTROY:
                user32.PostQuitMessage(0)
                return 0
        except Exception as exc:                       # noqa: BLE001
            self.log(f"托盘点异常：{exc}", "error")
        return user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    # ------------------------------------------------------------- 线程
    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="tray", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        hinst = kernel32.GetModuleHandleW(None)
        cls_name = "CampusNetAssistantTray"
        wc = WNDCLASSEXW()
        wc.cbSize = ctypes.sizeof(WNDCLASSEXW)
        wc.lpfnWndProc = self._proc
        wc.hInstance = hinst
        wc.lpszClassName = cls_name
        if not user32.RegisterClassExW(ctypes.byref(wc)):
            err = ctypes.get_last_error()
            if err != 1410:                            # 已注册
                self.log(f"托盘窗口类注册失败：{err}", "warn")
                return
        self.hwnd = user32.CreateWindowExW(
            0, cls_name, "校园网助手", 0, 0, 0, 0, 0, None, None, hinst, None)
        if not self.hwnd:
            self.log("托盘窗口创建失败", "warn")
            return
        self._add()
        user32.SetTimer(self.hwnd, TIMER_ID, TIMER_MS, None)

        msg = w.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

        if self.ok:
            nid = self._nid()
            shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(nid))
            self.ok = False

    def stop(self) -> None:
        if self.hwnd:
            user32.PostMessageW(self.hwnd, WM_CLOSE, 0, 0)

    def notify(self, title: str, text: str) -> None:
        self._balloon(title, text)
