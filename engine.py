"""认证引擎（无界面）：状态检测、自动登录、注销、配置。

与 WebView2 版共用同一套 portal / store / tray 模块，只是去掉了 HTTP 服务层，
界面直接在主线程轮询 engine.state，因此不需要起本地 http 服务。
"""

from __future__ import annotations

import threading
import time
from datetime import datetime

import store
from portal import Portal, PortalError, probe_internet


class Engine:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._op_lock = threading.Lock()
        self.stop_event = threading.Event()
        self.cfg = store.load()
        self.logs: list[dict] = []
        self._seq = 0
        self._fail_count = 0
        self._next_attempt = 0.0
        self._next_check = 0.0
        self._portal: Portal | None = None
        self._warned_no_pw = False
        self.state = {
            "phase": "idle",          # idle / checking / logging_in / online / offline / error
            "internet": False,
            "portal_reachable": False,
            "authenticated": False,
            "ip": "",
            "acct": "",
            "message": "尚未检测",
            "last_check": 0.0,
            "last_login": 0.0,
            "online_since": 0.0,
            "login_count": 0,
            "busy": False,
        }
        store.ensure_dirs()
        self.log("程序已启动，配置目录：" + store.DATA_DIR, "info")

    # ------------------------------------------------------------- 日志
    def log(self, msg: str, level: str = "info") -> None:
        with self._lock:
            self._seq += 1
            self.logs.append({
                "seq": self._seq,
                "t": datetime.now().strftime("%H:%M:%S"),
                "level": level,
                "msg": msg,
            })
            if len(self.logs) > 400:
                del self.logs[:100]
        try:
            with open(store.LOG_PATH, "a", encoding="utf-8") as fh:
                fh.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} [{level}] {msg}\n")
        except OSError:
            pass

    # ------------------------------------------------------------- 门户
    def portal(self, refresh: bool = False) -> Portal:
        base = self.cfg.get("base_url") or store.DEFAULTS["base_url"]
        if (refresh or self._portal is None
                or self._portal.base != base.rstrip("/")
                or self._portal.timeout != float(self.cfg.get("timeout") or 6)):
            self._portal = Portal(base, timeout=float(self.cfg.get("timeout") or 6),
                                  pool=self.cfg.get("pool") or "")
        return self._portal

    def _patch(self, **kw) -> None:
        with self._lock:
            was = self.state["authenticated"]
            self.state.update(kw)
            if kw.get("authenticated") is True and not was:
                self.state["online_since"] = time.time()
            if kw.get("authenticated") is False:
                self.state["online_since"] = 0.0

    def snapshot(self) -> dict:
        with self._lock:
            st = dict(self.state)
            st["online_seconds"] = (int(time.time() - self.state["online_since"])
                                    if self.state.get("online_since") else 0)
            if self.state.get("busy"):
                st["next_in"] = 0
            elif self._next_check:
                st["next_in"] = max(0, int(round(self._next_check - time.time())))
            else:
                st["next_in"] = -1
            return st

    # ------------------------------------------------------------- 检测
    def check(self, quiet: bool = False) -> None:
        with self._op_lock:
            prev_auth = self.state["authenticated"]
            prev_check = self.state["last_check"]
            self._patch(phase="checking", busy=True)
            cfg = self.cfg
            net = probe_internet(cfg["probe_urls"], timeout=min(6.0, float(cfg["timeout"]) + 1))
            reachable = authenticated = False
            ip = acct = ""
            msg = ""
            try:
                status = self.portal().ip_status()
                reachable = True
                authenticated = bool(status.get("logined"))
                ip = status.get("ip") or ""
                acct = status.get("acct") or ""
            except PortalError as exc:
                msg = str(exc)

            self._next_check = time.time() + int(self.cfg.get("interval") or 20)
            self._patch(
                internet=bool(net["online"]), portal_reachable=reachable,
                authenticated=authenticated, ip=ip, acct=acct,
                last_check=time.time(), busy=False,
                phase="online" if authenticated else ("offline" if reachable else "error"),
                message=msg if not reachable else ("已认证，可正常上网" if authenticated else "需要认证"),
            )
            if not quiet:
                if authenticated:
                    self.log(f"已在线：IP {ip or '未知'}"
                             f"{'，账号 ' + acct if acct else ''}"
                             f"{'，外网可达' if net['online'] else '，外网暂不可达'}", "ok")
                elif reachable:
                    self.log(f"未认证：门户可达（IP {ip or '未知'}），准备登录", "warn")
                else:
                    self.log(f"门户不可达：{msg or '网络未连接'}", "error")
            elif prev_check:
                if prev_auth and not authenticated:
                    self.log("检测到认证已失效，准备自动重新登录", "warn")
                elif not prev_auth and authenticated:
                    self.log(f"网络已恢复在线（IP {ip or '未知'}）", "ok")

    # ------------------------------------------------------------- 登录
    def login(self, user: str | None = None, password: str | None = None) -> dict:
        with self._op_lock:
            cfg = self.cfg
            user = (user if user is not None else cfg.get("user", "")).strip()
            if password is None:
                password = store.unprotect(cfg.get(store._ENC_FIELD, ""))
            if not user:
                return {"ok": False, "msg": "请先填写学号/工号"}
            if not password:
                return {"ok": False, "msg": "请先填写密码"}

            self._patch(phase="logging_in", busy=True, message="正在认证…")
            self.log(f"开始认证：{user}", "info")
            client = self.portal(refresh=True)
            try:
                if not client.query:
                    client.resolve_query()
                result = client.login(user, password, poll_seconds=12)
            except PortalError as exc:
                result = {"ok": False, "msg": f"门户请求失败：{exc}"}
            except Exception as exc:                    # noqa: BLE001
                result = {"ok": False, "msg": f"认证异常：{exc}"}

            if result.get("ok"):
                self._fail_count = 0
                self._next_attempt = 0.0
                self._patch(phase="online", authenticated=True, busy=False,
                            last_login=time.time(), internet=True, portal_reachable=True,
                            message=result.get("msg", "认证成功"),
                            login_count=self.state["login_count"] + 1)
                self.log(result.get("msg") or "认证成功", "ok")
                try:
                    st = client.ip_status()
                    self._patch(ip=st.get("ip") or self.state["ip"], acct=st.get("acct") or user)
                except PortalError:
                    self._patch(acct=user)
            else:
                self._fail_count += 1
                backoff = min(300, 15 * (2 ** max(0, self._fail_count - 1)))
                self._next_attempt = time.time() + backoff
                if self.state.get("authenticated"):
                    self._patch(phase="online", busy=False, message=f"{result.get('msg')}（当前认证仍有效）")
                    self.log(f"认证失败：{result.get('msg')}；当前会话仍在线", "warn")
                else:
                    self._patch(phase="offline", busy=False, message=result.get("msg", "认证失败"))
                    self.log(f"认证失败：{result.get('msg')}（{backoff}s 后重试）", "error")
            return result

    # ------------------------------------------------------------- 注销
    def logout(self) -> dict:
        with self._op_lock:
            try:
                res = self.portal().logoff()
            except PortalError as exc:
                res = {"ok": False, "msg": f"注销失败：{exc}"}
            self._patch(authenticated=False, phase="offline", online_since=0.0,
                        message=res.get("msg", ""))
            self.log(res.get("msg", ""), "ok" if res.get("ok") else "error")
            return res

    # ------------------------------------------------------------- 配置
    def update_config(self, patch: dict) -> None:
        with self._lock:
            cfg = self.cfg
            if "password" in patch:
                pw = patch.pop("password") or ""
                if pw:
                    cfg[store._ENC_FIELD] = store.protect(pw)
                elif patch.get("clear_password"):
                    cfg[store._ENC_FIELD] = ""
            patch.pop("clear_password", None)
            for key in ("user", "base_url", "pool"):
                if key in patch and patch[key] is not None:
                    cfg[key] = str(patch[key]).strip()
            for key in ("remember", "auto_login", "auto_close"):
                if key in patch:
                    cfg[key] = bool(patch[key])
            for key, lo, hi in (("interval", 5, 3600), ("timeout", 2, 30), ("retry", 1, 20)):
                if key in patch:
                    try:
                        cfg[key] = max(lo, min(hi, int(patch[key])))
                    except (TypeError, ValueError):
                        pass
            if "theme" in patch:
                mode = str(patch["theme"]).lower()
                cfg["theme"] = mode if mode in ("light", "dark", "system") else "system"
            if "win_round" in patch:
                cfg["win_round"] = "dwm" if str(patch["win_round"]).lower() == "dwm" else "big"
            if "probe_urls" in patch:
                urls = patch["probe_urls"]
                if isinstance(urls, str):
                    urls = urls.replace("\n", ",").split(",")
                cfg["probe_urls"] = [u.strip() for u in urls if u and u.strip()][:5]
                if not cfg["probe_urls"]:
                    cfg["probe_urls"] = list(store.DEFAULTS["probe_urls"])
            if "autostart" in patch:
                want = bool(patch["autostart"])
                if want != store.get_autostart():
                    if store.set_autostart(want):
                        cfg["autostart"] = want
                        self.log("已" + ("开启" if want else "关闭") + "开机自启", "info")
                    else:
                        self.log("写入开机自启失败", "error")
                else:
                    cfg["autostart"] = want
            if not cfg.get("user") and cfg.get(store._ENC_FIELD):
                cfg[store._ENC_FIELD] = ""
            if not cfg.get("remember", True):
                cfg[store._ENC_FIELD] = ""
            store.save(cfg)
            self._portal = None
        self.log("设置已保存", "info")

    # ------------------------------------------------------------- 守护循环
    def run_forever(self) -> None:
        self.log("后台守护已启动", "info")
        while not self.stop_event.is_set():
            cfg = self.cfg
            interval = int(cfg.get("interval") or 20)
            try:
                if cfg.get("auto_login"):
                    self.check(quiet=True)
                    need = (not self.state["authenticated"]) and self.state["portal_reachable"]
                    if need and time.time() >= self._next_attempt:
                        if not store.unprotect(cfg.get(store._ENC_FIELD, "")):
                            self._next_attempt = time.time() + 300
                            if not self._warned_no_pw:
                                self._warned_no_pw = True
                                self.log("未设置密码，自动认证暂停（请在设置中填写密码）", "warn")
                        else:
                            self._warned_no_pw = False
                            self.login()
            except Exception as exc:                    # noqa: BLE001
                self.log(f"守护线程异常：{exc}", "error")
                time.sleep(5)
            self._next_check = time.time() + interval
            slept = 0.0
            while slept < interval and not self.stop_event.is_set():
                time.sleep(0.25)
                slept += 0.25
        self.log("后台守护已停止", "info")

    def shutdown(self) -> None:
        self.stop_event.set()
