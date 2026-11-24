"""阳光学院（raas 门户）认证客户端：探测状态、登录、轮询、注销。

门户接口（POST，application/x-www-form-urlencoded）：
    api/ip.php      当前终端状态 {ret, data:{ip, logined, acct, proxymode, ...}}
    api/login.php   提交认证   {user, pass}            pass 为 encode_password() 结果
    api/stat.php    轮询认证结果 {ret: 2/3/4 认证中, 0 成功, 其他失败}
    api/ack_auth.php 认证回执
    api/logoff.php  注销

地址构造与门户 raas.js 一致：所有 api 请求都带上入口 URL 的查询串
（无查询串时直接请求，门户按源 IP 识别终端）。
"""

from __future__ import annotations

import http.cookiejar
import json
import socket
import time
import urllib.error
import urllib.parse
import urllib.request

from aes128 import PasswordTooLong, encode_password

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

# login.php / stat.php 返回码含义（取自门户 index.js）
RET_MESSAGES = {
    -1: "参数不正确",
    2: "域名不存在",
    3: "帐号或密码不正确",
    4: "帐号或密码不正确",
    5: "运营商配置不存在",
    6: "无效的运营商帐号",
    7: "保存配置失败",
    10000: "需要额外验证，请打开认证门户完成",
}

AUTH_PENDING = (2, 3, 4)          # stat.php: 仍在认证流程中
LOGIN_OK = (0, 3, 121, 122)       # login.php: 视为进入认证流程/已认证


class PortalError(Exception):
    """门户请求失败（网络不可达、返回非 JSON 等）。"""


class Portal:
    def __init__(self, base_url: str = "http://172.19.1.1", timeout: float = 6.0,
                 query: str = "", pool: str = ""):
        self.base = (base_url or "http://172.19.1.1").rstrip("/")
        self.timeout = timeout
        self.query = query.strip().lstrip("?")
        self.pool = pool.strip()
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar),
            urllib.request.ProxyHandler({}),      # 门户在校园网内，绕过系统代理
        )
        self.opener.addheaders = [
            ("User-Agent", UA),
            ("Accept", "application/json, text/javascript, */*; q=0.01"),
            ("X-Requested-With", "XMLHttpRequest"),
        ]

    # ---------------------------------------------------------------- 基础请求
    def _url(self, path: str) -> str:
        url = f"{self.base}/{path.lstrip('/')}"
        if self.query:
            url += ("&" if "?" in url else "?") + self.query
        return url

    def _post(self, path: str, data: dict | None = None) -> dict:
        body = urllib.parse.urlencode(data or {}).encode("utf-8")
        req = urllib.request.Request(self._url(path), data=body, method="POST")
        req.add_header("Content-Type", "application/x-www-form-urlencoded; charset=UTF-8")
        req.add_header("Referer", self.base + "/")
        try:
            with self.opener.open(req, timeout=self.timeout) as resp:
                raw = resp.read()
        except (urllib.error.URLError, socket.timeout, OSError) as exc:
            raise PortalError(f"{path} 请求失败：{getattr(exc, 'reason', exc)}") from exc
        text = raw.decode("utf-8", "replace").strip()
        try:
            return json.loads(text)
        except ValueError:
            snippet = " ".join(text.split())[:160]
            raise PortalError(f"{path} 返回了非 JSON 内容：{snippet}") from None

    # ---------------------------------------------------------------- 接口封装
    def resolve_query(self) -> str:
        """跟随门户入口跳转，取回终端相关的查询串（wlanuserip / nasip 等）。"""
        try:
            with self.opener.open(self.base + "/", timeout=self.timeout) as resp:
                final = resp.geturl()
        except (urllib.error.URLError, socket.timeout, OSError):
            return self.query
        parsed = urllib.parse.urlsplit(final)
        if parsed.query:
            self.query = parsed.query
        return self.query

    def ip_status(self) -> dict:
        """当前终端在门户侧的状态。"""
        res = self._post("api/ip.php", {})
        if res.get("ret") not in (0, None):
            raise PortalError(res.get("msg") or f"ip.php 返回 ret={res.get('ret')}")
        data = res.get("data") or {}
        if not isinstance(data, dict):
            data = {}
        return {
            "ip": data.get("ip", ""),
            "acct": data.get("acct", ""),
            "logined": int(data.get("logined") or 0) == 1,
            "proxymode": data.get("proxymode"),
            "raw": res,
        }

    def login(self, user: str, password: str, poll_seconds: int = 12) -> dict:
        """提交认证；返回 {ok, msg, ret, encoded}。"""
        user = (user or "").strip()
        if not user or not password:
            return {"ok": False, "msg": "请先填写学号/工号与密码", "ret": None}
        try:
            encoded = encode_password(password)
        except PasswordTooLong as exc:
            return {"ok": False, "msg": str(exc), "ret": None}

        payload = {"user": user, "pass": encoded}
        if self.pool:
            payload["pool"] = self.pool

        res = self._post("api/login.php", payload)
        ret = res.get("ret")
        msg = (res.get("msg") or "").strip()

        if ret == 10000:
            return {"ok": False, "msg": msg or RET_MESSAGES[10000], "ret": ret}
        if ret not in LOGIN_OK:
            return {"ok": False, "msg": msg or RET_MESSAGES.get(ret, f"认证失败（ret={ret}）"),
                    "ret": ret}

        # 认证成功回执（门户 JS 行为），失败不影响主流程
        try:
            self._post("api/ack_auth.php", payload)
        except PortalError:
            pass

        # 轮询 stat.php 直到认证完成
        deadline = time.time() + max(3, poll_seconds)
        last = ""
        while time.time() < deadline:
            time.sleep(1.0)
            try:
                st = self._post("api/stat.php", payload)
            except PortalError as exc:
                last = str(exc)
                continue
            sret = st.get("ret")
            last = (st.get("msg") or "").strip() or last
            if sret == 0:
                return {"ok": True, "msg": last or "认证成功！", "ret": sret}
            if sret not in AUTH_PENDING:
                return {"ok": False, "msg": last or f"认证失败（ret={sret}）", "ret": sret}
        return {"ok": True, "msg": last or "已提交认证，等待门户确认", "ret": None, "soft": True}

    def logoff(self) -> dict:
        res = self._post("api/logoff.php", {})
        ok = res.get("ret") in (0, None)
        return {"ok": ok, "msg": res.get("msg") or ("已注销下线" if ok else "注销失败"),
                "ret": res.get("ret")}


def probe_internet(urls: list[str], timeout: float = 4.0) -> dict:
    """访问外网探针地址，判断是否真正可以上网。"""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    opener.addheaders = [("User-Agent", UA)]
    detail = []
    for url in urls:
        if not url:
            continue
        started = time.time()
        try:
            with opener.open(url, timeout=timeout) as resp:
                code = resp.getcode()
                resp.read(2048)
            ok = code in (200, 204, 301, 302, 403)
            detail.append({"url": url, "code": code,
                           "ms": int((time.time() - started) * 1000), "ok": ok})
            if ok:
                return {"online": True, "detail": detail}
        except Exception as exc:                       # noqa: BLE001 - 探针失败即视为不通
            detail.append({"url": url, "code": None,
                           "ms": int((time.time() - started) * 1000),
                           "ok": False, "error": str(getattr(exc, "reason", exc))[:120]})
    return {"online": False, "detail": detail}
