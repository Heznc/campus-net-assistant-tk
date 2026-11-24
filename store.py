"""配置与凭据存储。

口令用 Windows DPAPI（当前用户作用域）加密后写入磁盘，同机其他账户无法解开；
在非 Windows 环境（或 DPAPI 不可用）退化为本机混淆存储，仅用于自测。
"""

from __future__ import annotations

import base64
import json
import os
import sys
import threading

FROZEN = bool(getattr(sys, "frozen", False))

if FROZEN:
    APP_DIR = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    # 便携优先：exe 同目录下的 data\ 可写就用它（设置跟着程序走）；
    # 装在 Program Files 这类只读位置时退回 %APPDATA%\CampusNetAssistant
    _exe_dir = os.path.dirname(sys.executable)
    _portable = os.path.join(_exe_dir, "data")
    try:
        os.makedirs(_portable, exist_ok=True)
        _probe = os.path.join(_portable, ".writable")
        with open(_probe, "w", encoding="utf-8") as _fh:
            _fh.write("1")
        os.remove(_probe)
        DATA_DIR = _portable
    except OSError:
        DATA_DIR = os.path.join(os.environ.get("APPDATA") or os.path.expanduser("~"),
                                "CampusNetAssistant")
else:
    APP_DIR = os.path.dirname(os.path.abspath(__file__))
    DATA_DIR = os.path.join(APP_DIR, "data")
CONFIG_PATH = os.path.join(DATA_DIR, "config.json")
LOG_PATH = os.path.join(DATA_DIR, "assistant.log")

DEFAULTS = {
    "user": "",
    "remember": True,
    "base_url": "http://172.19.1.1",
    "auto_login": True,
    "interval": 20,                 # 秒：断网检测周期
    "timeout": 6,                   # 秒：单次请求超时
    "retry": 3,                     # 连续重试次数
    "probe_urls": [
        "http://connect.rom.miui.com/generate_204",
        "http://www.baidu.com",
    ],
    "pool": "",                     # 运营商线路（门户未启用时可留空）
    "autostart": False,             # 开机自动运行
    "auto_close": False,            # 关闭窗口时退出程序
    "win_round": "big",             # 外框圆角：big=大圆角 / dwm=系统抗锯齿
    "theme": "system",              # 主题：light / dark / system
}

_lock = threading.RLock()
_ENC_PREFIX = "dpapi:"
_FALLBACK_PREFIX = "obf:"
_ENC_FIELD = "_password"

# ------------------------------------------------------------------ DPAPI

if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes

    class _BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    _crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    _crypt32.CryptProtectData.argtypes = [
        ctypes.POINTER(_BLOB), wintypes.LPCWSTR, ctypes.POINTER(_BLOB),
        ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_BLOB)]
    _crypt32.CryptUnprotectData.argtypes = [
        ctypes.POINTER(_BLOB), ctypes.POINTER(wintypes.LPWSTR), ctypes.POINTER(_BLOB),
        ctypes.c_void_p, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(_BLOB)]
    _kernel32.LocalFree.argtypes = [ctypes.c_void_p]

    _UI_FORBIDDEN = 0x1

    def _dpapi(data: bytes, protect: bool) -> bytes:
        src_buf = ctypes.create_string_buffer(data, len(data))
        src = _BLOB(len(data), ctypes.cast(src_buf, ctypes.POINTER(ctypes.c_char)))
        out = _BLOB()
        fn = _crypt32.CryptProtectData if protect else _crypt32.CryptUnprotectData
        ok = fn(ctypes.byref(src), None, None, None, None, _UI_FORBIDDEN, ctypes.byref(out))
        if not ok:
            raise OSError(ctypes.get_last_error(), "DPAPI 调用失败")
        try:
            return ctypes.string_at(out.pbData, out.cbData)
        finally:
            _kernel32.LocalFree(out.pbData)
else:                                   # pragma: no cover - 仅用于非 Windows 自测
    def _dpapi(data: bytes, protect: bool) -> bytes:
        raise OSError("DPAPI 不可用")


def _fallback_encrypt(data: bytes) -> bytes:
    key = ("campus-net-assistant:" + os.environ.get("COMPUTERNAME", "local")).encode()
    return bytes(b ^ key[i % len(key)] for i, b in enumerate(data))


def protect(secret: str) -> str:
    raw = secret.encode("utf-8")
    try:
        return _ENC_PREFIX + base64.b64encode(_dpapi(raw, True)).decode()
    except OSError:
        return _FALLBACK_PREFIX + base64.b64encode(_fallback_encrypt(raw)).decode()


def unprotect(blob: str) -> str:
    if not blob:
        return ""
    try:
        if blob.startswith(_ENC_PREFIX):
            return _dpapi(base64.b64decode(blob[len(_ENC_PREFIX):]), False).decode("utf-8")
        if blob.startswith(_FALLBACK_PREFIX):
            return _fallback_encrypt(
                base64.b64decode(blob[len(_FALLBACK_PREFIX):])).decode("utf-8")
    except (OSError, ValueError):
        return ""
    return ""


# ------------------------------------------------------------------ 配置文件

def ensure_dirs() -> None:
    os.makedirs(DATA_DIR, exist_ok=True)


def load() -> dict:
    with _lock:
        cfg = dict(DEFAULTS)
        try:
            with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
                stored = json.load(fh)
            if isinstance(stored, dict):
                cfg.update({k: v for k, v in stored.items() if k in DEFAULTS or k == _ENC_FIELD})
        except (OSError, ValueError):
            pass
        if not isinstance(cfg.get("probe_urls"), list):
            cfg["probe_urls"] = list(DEFAULTS["probe_urls"])
        cfg["interval"] = max(5, min(3600, int(cfg.get("interval") or 20)))
        cfg["timeout"] = max(2, min(30, int(cfg.get("timeout") or 6)))
        cfg["retry"] = max(1, min(20, int(cfg.get("retry") or 3)))
        return cfg


def save(cfg: dict) -> None:
    with _lock:
        ensure_dirs()
        payload = {k: cfg[k] for k in DEFAULTS if k in cfg}
        if cfg.get(_ENC_FIELD):
            payload[_ENC_FIELD] = cfg[_ENC_FIELD]
        tmp = CONFIG_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, CONFIG_PATH)


def public(cfg: dict) -> dict:
    """给界面用的配置：不带口令密文，只说明有没有存过。"""
    out = {k: cfg.get(k, DEFAULTS[k]) for k in DEFAULTS}
    out["has_password"] = bool(unprotect(cfg.get(_ENC_FIELD, "")))
    return out


# ------------------------------------------------------------------ 开机自启

_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_RUN_NAME = "CampusNetAssistant"


def _pythonw() -> str:
    exe = sys.executable
    if exe and exe.lower().endswith("python.exe"):
        cand = exe[:-len("python.exe")] + "pythonw.exe"
        if os.path.exists(cand):
            return cand
    return exe


def autostart_command() -> str:
    exe, args = _launch_target()
    return f'"{exe}" {args}'


def _launch_target() -> tuple[str, str]:
    """返回 (启动程序, 参数)。打包后就是 exe 自己。"""
    if FROZEN:
        return sys.executable, "--hidden"
    launcher = os.environ.get("CNA_LAUNCHER")
    if launcher and os.path.isfile(launcher):
        return launcher, "--hidden"
    exe = os.path.join(APP_DIR, "校园网助手.exe")
    if os.path.isfile(exe):
        return exe, "--hidden"
    return _pythonw(), f'"{os.path.join(APP_DIR, "tk_app.py")}" --hidden'


def _startup_dir() -> str:
    appdata = os.environ.get("APPDATA", "")
    return os.path.join(appdata, "Microsoft", "Windows", "Start Menu", "Programs", "Startup")


def startup_shortcut_path() -> str:
    return os.path.join(_startup_dir(), "校园网助手.lnk")


def _create_shortcut(path: str) -> bool:
    """生成启动目录快捷方式（任务管理器里显示为「校园网助手」）。"""
    import subprocess

    exe, args = _launch_target()
    icon = sys.executable if FROZEN else os.path.join(APP_DIR, "assets", "app.ico")
    workdir = os.path.dirname(sys.executable) if FROZEN else APP_DIR
    script = (
        "$ws = New-Object -ComObject WScript.Shell; "
        "$sc = $ws.CreateShortcut('{lnk}'); "
        "$sc.TargetPath = '{exe}'; "
        "$sc.Arguments = '{args}'; "
        "$sc.WorkingDirectory = '{wd}'; "
        "$sc.IconLocation = '{icon}'; "
        "$sc.WindowStyle = 7; "
        "$sc.Description = '阳光学院校园网自动登录'; "
        "$sc.Save()"
    ).format(
        lnk=path.replace("'", "''"),
        exe=exe.replace("'", "''"),
        args=args.replace("'", "''"),
        wd=workdir.replace("'", "''"),
        icon=icon.replace("'", "''"),
    )
    flags = 0x08000000 if sys.platform == "win32" else 0
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, timeout=25, creationflags=flags)
        return proc.returncode == 0 and os.path.exists(path)
    except Exception:                                   # noqa: BLE001
        return False


def _remove_run_value() -> None:
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, _RUN_NAME)
    except OSError:
        pass


def get_autostart() -> bool:
    if sys.platform != "win32":
        return False
    if os.path.exists(startup_shortcut_path()):
        return True
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _RUN_KEY) as key:
            value, _ = winreg.QueryValueEx(key, _RUN_NAME)
        return bool(value)
    except OSError:
        return False


def set_autostart(enabled: bool) -> bool:
    """开机自启：写启动目录快捷方式（任务管理器可见、带图标），并清掉旧的 Run 注册表项。"""
    if sys.platform != "win32":
        return False
    lnk = startup_shortcut_path()
    try:
        if enabled:
            os.makedirs(_startup_dir(), exist_ok=True)
            ok = _create_shortcut(lnk)
            if ok:
                _remove_run_value()                      # 迁移：避免两处重复启动
            return ok
        if os.path.exists(lnk):
            os.remove(lnk)
        _remove_run_value()
        return True
    except OSError:
        return False
