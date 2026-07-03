"""Shared outbound HTTP proxy helpers."""

from __future__ import annotations

import os
import sys
import ipaddress
import urllib.parse
import urllib.request
from typing import Any


_DISABLED_PROXY_VALUES = {"direct", "none", "off", "false", "0"}


def normalize_proxy_url(raw: str) -> str:
    proxy = str(raw or "").strip()
    if not proxy:
        return ""
    if "://" not in proxy:
        proxy = f"http://{proxy}"
    return proxy


def _windows_user_proxy() -> str:
    if sys.platform != "win32":
        return ""
    try:
        import winreg

        key_path = r"Software\Microsoft\Windows\CurrentVersion\Internet Settings"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
            enabled, _ = winreg.QueryValueEx(key, "ProxyEnable")
            if not enabled:
                return ""
            proxy_server, _ = winreg.QueryValueEx(key, "ProxyServer")
    except OSError:
        return ""

    raw = str(proxy_server or "").strip()
    if not raw:
        return ""
    if ";" not in raw and "=" not in raw:
        return normalize_proxy_url(raw)

    entries: dict[str, str] = {}
    for part in raw.split(";"):
        if "=" not in part:
            continue
        scheme, value = part.split("=", 1)
        entries[scheme.strip().lower()] = value.strip()

    for scheme in ("https", "http"):
        if entries.get(scheme):
            return normalize_proxy_url(entries[scheme])
    if entries.get("socks"):
        socks_proxy = entries["socks"]
        if "://" not in socks_proxy:
            socks_proxy = f"socks5://{socks_proxy}"
        return socks_proxy
    return ""


def runtime_proxy_url(setting_name: str = "network_proxy") -> str | None:
    """Resolve the app-level outbound proxy.

    Returns:
        str: explicit or detected proxy URL.
        None: let the HTTP client follow its default environment behavior.
        "": force direct connection.
    """
    try:
        from app.core.settings import get_runtime_settings

        raw = str(getattr(get_runtime_settings(), setting_name, "") or "").strip()
    except Exception:
        raw = ""

    if raw:
        if raw.lower() in _DISABLED_PROXY_VALUES:
            return ""
        return normalize_proxy_url(raw)

    for key in ("HTTPS_PROXY", "https_proxy", "ALL_PROXY", "all_proxy", "HTTP_PROXY", "http_proxy"):
        value = os.environ.get(key, "").strip()
        if value:
            return normalize_proxy_url(value)

    detected = _windows_user_proxy()
    return detected or None


def _request_url(request: urllib.request.Request | str) -> str:
    if isinstance(request, urllib.request.Request):
        return request.full_url
    return str(request or "")


def _is_private_target(url: str) -> bool:
    try:
        parsed = urllib.parse.urlparse(url)
    except Exception:
        return False
    hostname = (parsed.hostname or "").strip().lower()
    if not hostname:
        return False
    if hostname in {"localhost"} or hostname.endswith(".local"):
        return True
    try:
        ip = ipaddress.ip_address(hostname)
        return bool(ip.is_private or ip.is_loopback or ip.is_link_local)
    except ValueError:
        return False


def urllib_urlopen(request: urllib.request.Request | str, *, timeout: float | None = None):
    if _is_private_target(_request_url(request)):
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        return opener.open(request, timeout=timeout)
    proxy = runtime_proxy_url()
    if proxy is None:
        return urllib.request.urlopen(request, timeout=timeout)
    proxies = {} if proxy == "" else {"http": proxy, "https": proxy}
    opener = urllib.request.build_opener(urllib.request.ProxyHandler(proxies))
    return opener.open(request, timeout=timeout)


def httpx_client_kwargs(target_url: str | None = None) -> dict[str, Any]:
    if target_url and _is_private_target(target_url):
        return {"trust_env": False, "proxy": None}
    proxy = runtime_proxy_url()
    if proxy is None:
        return {"trust_env": True}
    if proxy == "":
        return {"trust_env": False, "proxy": None}
    return {"trust_env": False, "proxy": proxy}
