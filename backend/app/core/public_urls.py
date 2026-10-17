"""HTTP transport for untrusted media URLs.

Service endpoints configured by the operator (LLM, sync, proxies) continue to
use core.network. Media URLs must resolve only to public addresses. Connections
use those numeric addresses, including proxy CONNECT targets, while retaining
the original HTTP Host and TLS certificate name. Redirects re-enter this check.
"""

from __future__ import annotations

import io
import ipaddress
import socket
import threading
import time
import urllib.error
import urllib.request
from email.message import Message
from urllib.parse import urlsplit

import httpcore
import httpx

_DNS_CACHE: dict[str, tuple[float, tuple[str, ...]]] = {}
_DNS_CACHE_LOCK = threading.Lock()


class UnsafeURLError(ValueError):
    """A media URL cannot be fetched from the public network."""


def _resolve_fake_ip(host: str) -> tuple[str, ...]:
    """Resolve real addresses when a local VPN returns its synthetic DNS pool.

    Never permit that private pool as a destination. Use a fixed numeric HTTPS
    resolver so its own DNS cannot recurse into the synthetic pool. Ordinary
    private DNS answers and literal benchmark IPs do not use this fallback.
    """
    from app.core.network import httpx_client_kwargs

    with _DNS_CACHE_LOCK:
        cached = _DNS_CACHE.get(host)
        if cached and cached[0] > time.monotonic():
            return cached[1]
    addresses = []
    ttl = 60
    try:
        with httpx.Client(**httpx_client_kwargs("https://1.1.1.1"), timeout=8) as client:
            for record_type in ("A", "AAAA"):
                response = client.get(
                    "https://1.1.1.1/dns-query",
                    params={"name": host, "type": record_type},
                    headers={"Accept": "application/dns-json"},
                )
                response.raise_for_status()
                data = response.json()
                if data.get("Status") != 0:
                    raise UnsafeURLError("URL host could not be resolved by public DNS")
                addresses.extend(
                    answer["data"]
                    for answer in data.get("Answer", [])
                    if answer.get("type") in {1, 28}
                )
                ttl = min([ttl, *(int(answer.get("TTL", 0)) for answer in data.get("Answer", []))])
    except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
        raise UnsafeURLError("URL host could not be resolved by public DNS") from exc
    resolved = tuple(dict.fromkeys(addresses))
    if resolved and all(_public_address(address) for address in resolved):
        with _DNS_CACHE_LOCK:
            if len(_DNS_CACHE) >= 256:
                _DNS_CACHE.pop(next(iter(_DNS_CACHE)))
            _DNS_CACHE[host] = (time.monotonic() + max(ttl, 0), resolved)
    return resolved


def _public_address(value: str) -> bool:
    address = ipaddress.ip_address(value)
    if (
        not address.is_global
        or address.is_multicast
        or address.is_reserved
        or address.is_unspecified
    ):
        return False
    if isinstance(address, ipaddress.IPv6Address):
        if address.ipv4_mapped:
            return _public_address(str(address.ipv4_mapped))
        # Translation/tunnel addresses can encode a non-public IPv4 target.
        if address.sixtofour or address.teredo:
            return False
        if address in ipaddress.ip_network("64:ff9b::/96") or address in ipaddress.ip_network(
            "64:ff9b:1::/48"
        ):
            return False
    return True


def resolve_public_url(url: str) -> tuple[str, int, tuple[str, ...]]:
    """Validate syntax and every DNS result; return addresses safe to connect to."""
    try:
        if any(ord(char) <= 32 or ord(char) == 127 for char in url) or "\\" in url:
            raise UnsafeURLError("URL contains invalid characters")
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"}:
            raise UnsafeURLError("Only http/https media URLs are allowed")
        if parsed.username is not None or parsed.password is not None:
            raise UnsafeURLError("Credentials in media URLs are not allowed")
        host = (parsed.hostname or "").rstrip(".").lower()
        port = parsed.port if parsed.port is not None else (443 if parsed.scheme == "https" else 80)
        if not host or "%" in host or port < 1:
            raise UnsafeURLError("URL has an invalid host or port")
        if host == "localhost" or host.endswith(".localhost") or host == "metadata.google.internal":
            raise UnsafeURLError("URL must point to a public address")
        host = host.encode("idna").decode("ascii")
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            addresses = tuple(
                dict.fromkeys(
                    result[4][0]
                    for result in socket.getaddrinfo(
                        host,
                        port,
                        type=socket.SOCK_STREAM,
                    )
                )
            )
            if addresses and all(
                ipaddress.ip_address(address) in ipaddress.ip_network("198.18.0.0/15")
                for address in addresses
            ):
                addresses = _resolve_fake_ip(host)
        else:
            addresses = (str(address),)
        if not addresses or not all(_public_address(address) for address in addresses):
            raise UnsafeURLError("URL must resolve only to public addresses")
        return host, port, addresses
    except UnsafeURLError:
        raise
    except (OSError, ValueError, UnicodeError) as exc:
        raise UnsafeURLError("URL host could not be resolved to a public address") from exc


def validate_public_http_url(url: str) -> None:
    resolve_public_url(url)


class _ProxyTLSStream(httpcore.NetworkStream):
    """HTTP CONNECT uses a pinned IP but must authenticate the original host."""

    def __init__(self, stream, hostname: str, proxy_https: bool, tls_layers: int = 0):
        self.stream = stream
        self.hostname = hostname
        self.proxy_https = proxy_https
        self.tls_layers = tls_layers

    def read(self, max_bytes, timeout=None):
        return self.stream.read(max_bytes, timeout)

    def write(self, buffer, timeout=None):
        return self.stream.write(buffer, timeout)

    def close(self):
        self.stream.close()

    def get_extra_info(self, info):
        return self.stream.get_extra_info(info)

    def start_tls(self, ssl_context, server_hostname=None, timeout=None):
        # With an HTTPS proxy the first TLS layer authenticates the proxy.
        # httpcore's CONNECT path ignores the sni_hostname request extension.
        if not self.proxy_https or self.tls_layers:
            server_hostname = self.hostname
        stream = self.stream.start_tls(ssl_context, server_hostname, timeout)
        return _ProxyTLSStream(stream, self.hostname, self.proxy_https, self.tls_layers + 1)


class _ProxyTLSBackend(httpcore.SyncBackend):
    def __init__(self, hostname: str, proxy_https: bool):
        self.hostname = hostname
        self.proxy_https = proxy_https

    def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        stream = super().connect_tcp(host, port, timeout, local_address, socket_options)
        return _ProxyTLSStream(stream, self.hostname, self.proxy_https)


class PublicHTTPTransport(httpx.BaseTransport):
    """Pin public DNS results without changing Host, SNI, cookies or redirects."""

    def __init__(self, *, proxy: str | None = None, verify=True):
        self._proxy = proxy or None
        self._verify = verify
        self._transports: dict[tuple[str, str, int], httpx.HTTPTransport] = {}
        self._lock = threading.Lock()

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        host, port, addresses = resolve_public_url(str(request.url))
        origin = (request.url.scheme, host, port)
        with self._lock:
            # Pools are separate by logical origin: hosts sharing an IP must
            # never reuse a TLS connection verified for a different hostname.
            transport = self._transports.get(origin)
            if transport is None:
                transport = httpx.HTTPTransport(proxy=self._proxy, verify=self._verify)
                if self._proxy and urlsplit(self._proxy).scheme in {"http", "https"}:
                    # httpx does not expose HTTPProxy's network_backend option.
                    # Set it before the pool creates any connections.
                    transport._pool._network_backend = _ProxyTLSBackend(
                        host,
                        urlsplit(self._proxy).scheme == "https",
                    )
                self._transports[origin] = transport
        headers = request.headers.copy()
        headers["Host"] = request.url.netloc.decode("ascii")
        extensions = {**request.extensions, "sni_hostname": host}
        if self._proxy and urlsplit(self._proxy).scheme in {"http", "https"}:
            extensions.pop("sni_hostname", None)
        for index, address in enumerate(addresses):
            pinned = httpx.Request(
                request.method,
                request.url.copy_with(host=address),
                headers=headers,
                stream=request.stream,
                extensions=extensions,
            )
            try:
                return transport.handle_request(pinned)
            except (httpx.ConnectError, httpx.ConnectTimeout):
                if index == len(addresses) - 1:
                    raise
        raise AssertionError("No resolved address")

    def close(self) -> None:
        with self._lock:
            for transport in self._transports.values():
                transport.close()
            self._transports.clear()


def public_httpx_client_kwargs(target_url: str | None = None) -> dict:
    from app.core.network import runtime_proxy_url

    return {"transport": PublicHTTPTransport(proxy=runtime_proxy_url()), "trust_env": False}


class _ResponseStream(io.RawIOBase):
    def __init__(self, response: httpx.Response, client: httpx.Client):
        super().__init__()
        self.response = response
        self.client = client
        self.chunks = response.iter_bytes()
        self.pending = b""

    def readable(self):
        return True

    def readinto(self, buffer):
        while not self.pending:
            try:
                self.pending = next(self.chunks)
            except StopIteration:
                return 0
        length = min(len(buffer), len(self.pending))
        buffer[:length] = self.pending[:length]
        self.pending = self.pending[length:]
        return length

    def close(self):
        try:
            self.response.close()
        finally:
            self.client.close()
            super().close()


def public_urlopen(
    request: urllib.request.Request | str,
    *,
    timeout: float | None = None,
    follow_redirects: bool = True,
):
    """Streaming urllib-compatible response backed by the public-only transport."""
    req = (
        request if isinstance(request, urllib.request.Request) else urllib.request.Request(request)
    )
    validate_public_http_url(req.full_url)
    client = httpx.Client(
        **public_httpx_client_kwargs(),
        timeout=timeout,
        follow_redirects=follow_redirects,
    )
    try:
        response = client.send(
            client.build_request(
                req.get_method(),
                req.full_url,
                headers=dict(req.header_items()),
                content=req.data,
            ),
            stream=True,
        )
        headers = Message()
        for name, value in response.headers.multi_items():
            headers.add_header(name, value)
        fp = io.BufferedReader(_ResponseStream(response, client))
        result = urllib.response.addinfourl(fp, headers, str(response.url), response.status_code)
        if response.status_code >= 400 or (
            300 <= response.status_code < 400 and not follow_redirects
        ):
            raise urllib.error.HTTPError(
                str(response.url),
                response.status_code,
                response.reason_phrase,
                headers,
                result,
            )
        return result
    except urllib.error.HTTPError:
        raise
    except Exception as exc:
        client.close()
        if isinstance(exc, httpx.HTTPError):
            raise urllib.error.URLError(str(exc)) from exc
        raise
