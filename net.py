"""net.py - the watcher. Every outbound connection the server opens is logged here.

Two layers:
  1. request(): the ONE function the server, voice and hearing use for HTTP. It logs the
     host and the reason before the connection opens.
  2. install_socket_watch(): a safety net under everything else (third-party libraries,
     model downloads). Any socket connect to a non-loopback address, or to an HTTPS proxy,
     is logged with reason "unwatched socket".

GET /drill/connections on the server returns the log for the current turn.
"""
import ipaddress
import json
import os
import socket
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

_lock = threading.Lock()
_turn = {"id": 0, "started": time.time(), "connections": []}
_local = threading.local()
LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0"}

# urllib only: direct opener for loopback so a local call never touches a proxy.
_direct = urllib.request.build_opener(urllib.request.ProxyHandler({}))
_default = urllib.request.build_opener()


def is_local(host):
    host = (host or "").strip("[]").lower()
    if host in LOCAL_HOSTS:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def log(host, reason, kind="http"):
    with _lock:
        _turn["connections"].append({
            "host": host, "reason": reason, "kind": kind,
            "local": is_local(host), "t": round(time.time() - _turn["started"], 3),
        })


def new_turn():
    with _lock:
        _turn["id"] += 1
        _turn["started"] = time.time()
        _turn["connections"] = []
        return _turn["id"]


def snapshot():
    with _lock:
        return {"turn": _turn["id"], "connections": list(_turn["connections"])}


def request(url, reason, data=None, headers=None, method=None, timeout=60):
    """Open url, log it, return (status, headers, body_bytes). Raises urllib errors."""
    host = urllib.parse.urlsplit(url).hostname or "?"
    log(host, reason)
    if isinstance(data, (dict, list)):
        data = json.dumps(data).encode()
        headers = {"Content-Type": "application/json", **(headers or {})}
    req = urllib.request.Request(url, data=data, headers=headers or {}, method=method)
    opener = _direct if is_local(host) else _default
    _local.watched = True
    try:
        with opener.open(req, timeout=timeout) as r:
            return r.status, dict(r.headers), r.read()
    finally:
        _local.watched = False


def request_json(url, reason, payload=None, headers=None, timeout=60, method=None):
    status, hdrs, body = request(url, reason, data=payload, headers=headers,
                                 timeout=timeout, method=method)
    return json.loads(body.decode() or "null")


def _proxy_addrs():
    out = set()
    for var in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "ALL_PROXY"):
        v = os.environ.get(var)
        if v:
            p = urllib.parse.urlsplit(v if "://" in v else "http://" + v)
            if p.hostname and p.port:
                out.add((p.hostname, p.port))
    return out


def install_socket_watch():
    """Log every socket connect that leaves loopback or goes through a proxy."""
    if getattr(socket.socket, "_jarvis_watched", False):
        return
    proxies = _proxy_addrs()
    names = {}
    real_getaddrinfo = socket.getaddrinfo

    def getaddrinfo(host, *a, **kw):
        res = real_getaddrinfo(host, *a, **kw)
        if isinstance(host, str):
            for r in res:
                names[r[4][0]] = host
        return res

    real_connect, real_connect_ex = socket.socket.connect, socket.socket.connect_ex

    def note(sock, address):
        if getattr(_local, "watched", False):
            return  # already logged by request() with its reason
        if sock.family not in (socket.AF_INET, socket.AF_INET6) or not isinstance(address, tuple):
            return
        ip, port = address[0], address[1]
        host = names.get(ip, ip)
        if (host, port) in proxies or (ip, port) in proxies:
            log(f"proxy {host}:{port} (tunnel to an unknown host)", "unwatched socket", "socket")
        elif not is_local(ip):
            log(host, "unwatched socket", "socket")

    def connect(self, address):
        note(self, address)
        return real_connect(self, address)

    def connect_ex(self, address):
        note(self, address)
        return real_connect_ex(self, address)

    socket.getaddrinfo = getaddrinfo
    socket.socket.connect = connect
    socket.socket.connect_ex = connect_ex
    socket.socket._jarvis_watched = True


__all__ = ["request", "request_json", "log", "new_turn", "snapshot", "is_local",
           "install_socket_watch", "urllib"]
HTTPError = urllib.error.HTTPError
URLError = urllib.error.URLError
