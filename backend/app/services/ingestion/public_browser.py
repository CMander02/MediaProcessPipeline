"""Keep background browser extraction on the same public HTTP boundary."""

from __future__ import annotations

import json
from http.cookiejar import Cookie
from urllib.parse import urldefrag

import httpx

from app.core.public_urls import public_httpx_client_kwargs


def _browser_cookies(context):
    cookies = httpx.Cookies()
    for item in context.cookies():
        domain = item["domain"]
        rest = {"SameSite": item.get("sameSite", "Lax")}
        if item.get("httpOnly"):
            rest["HttpOnly"] = None
        cookies.jar.set_cookie(
            Cookie(
                version=0,
                name=item["name"],
                value=item["value"],
                port=None,
                port_specified=False,
                domain=domain,
                domain_specified=domain.startswith("."),
                domain_initial_dot=domain.startswith("."),
                path=item.get("path", "/"),
                path_specified=True,
                secure=item.get("secure", False),
                expires=item.get("expires") if item.get("expires", -1) > 0 else None,
                discard=False,
                comment=None,
                comment_url=None,
                rest=rest,
            )
        )
    return cookies


def _cookie_state(cookies):
    return {
        (cookie.name, cookie.domain, cookie.path): {
            "name": cookie.name,
            "value": cookie.value,
            "domain": cookie.domain,
            "path": cookie.path,
            "secure": cookie.secure,
            "httpOnly": cookie.has_nonstandard_attr("HttpOnly")
            or cookie.has_nonstandard_attr("httponly"),
            "expires": cookie.expires if cookie.expires is not None else -1,
            "sameSite": str(cookie.get_nonstandard_attr("SameSite", "Lax")).capitalize(),
        }
        for cookie in cookies.jar
    }


def _sync_redirect_cookies(context, before, cookies):
    # Intermediate redirect responses never reach Chromium. Apply their cookie
    # changes to the actual cookie domain, not the bridge document's origin.
    after = _cookie_state(cookies)
    for name, domain, path in before.keys() - after.keys():
        context.clear_cookies(name=name, domain=domain, path=path)
    changed = [value for key, value in after.items() if before.get(key) != value]
    if changed:
        context.add_cookies(changed)


def goto_public_page(page, url, **kwargs):
    """Wait through our document redirect bridge before callers inspect a page."""
    response = page.goto(url, **kwargs)
    page.wait_for_function(
        "document.readyState !== 'loading' && "
        "!document.documentElement.hasAttribute('data-mpp-redirect')",
        timeout=kwargs.get("timeout", 30000),
    ).dispose()
    return response


def protect_browser_context(context):
    """Fetch intercepted requests ourselves, so Chromium cannot re-resolve DNS.

    The context must be created with service_workers='block'. Background
    extraction does not need WebSockets; disallow that separate network path.
    Interactive account-login contexts are not passed to this helper.
    """
    # Playwright routes only the first request of a browser redirect chain.
    # Follow it in our transport and fulfill only the final response; returning
    # a 302 to Chromium would let the next hop escape interception.
    client = httpx.Client(**public_httpx_client_kwargs(), timeout=30, follow_redirects=True)
    pending_documents = {}

    def fetch(route):
        request = route.request
        try:
            navigation = request.is_navigation_request()
            cached = pending_documents.pop(request.frame, None) if navigation else None
            if cached and cached[0] == urldefrag(request.url)[0] and request.method == "GET":
                response = cached[1]
            else:
                client.cookies = _browser_cookies(context)
                before = _cookie_state(client.cookies)
                response = client.request(
                    request.method,
                    request.url,
                    headers=request.all_headers(),
                    content=request.post_data_buffer,
                )
                if response.history:
                    _sync_redirect_cookies(context, before, client.cookies)
            if 300 <= response.status_code < 400 and response.status_code != 304:
                route.abort("blockedbyclient")
                return
            final_url = str(response.url)
            if navigation and urldefrag(final_url)[0] != urldefrag(request.url)[0]:
                if response.request.method != "GET":
                    # A location.replace navigation cannot preserve a POST body.
                    route.abort("blockedbyclient")
                    return
                pending_documents[request.frame] = (urldefrag(final_url)[0], response)
                destination = json.dumps(final_url, ensure_ascii=True).replace("<", "\\u003c")
                route.fulfill(
                    status=200,
                    headers={
                        "Content-Type": "text/html; charset=utf-8",
                        "Cache-Control": "no-store",
                    },
                    body=(
                        '<!doctype html><html data-mpp-redirect><head><meta charset="utf-8">'
                        f"<script>location.replace({destination})</script></head></html>"
                    ),
                )
                return
            if not navigation and response.url.copy_with(path="/", query=None, fragment=None) != (
                httpx.URL(request.url).copy_with(path="/", query=None, fragment=None)
            ):
                # Serving cross-origin content under the original resource URL
                # would bypass the browser's origin and cookie semantics.
                route.abort("blockedbyclient")
                return
            # httpx has decoded compression; the browser must receive the
            # resulting size and encoding, not the origin's compressed headers.
            headers = {
                name: value
                for name, value in response.headers.items()
                if name
                not in {"content-encoding", "content-length", "transfer-encoding", "connection"}
            }
            if "set-cookie" in response.headers:
                headers["set-cookie"] = "\n".join(response.headers.get_list("set-cookie"))
            route.fulfill(status=response.status_code, headers=headers, body=response.content)
        except Exception:
            route.abort("blockedbyclient")

    context.route("**/*", fetch)
    context.route_web_socket("**/*", lambda websocket: websocket.close())
    context.on("close", lambda *_args: client.close())
    return context


def fetch_browser_resource(context, url, *, headers, timeout):
    """Fetch an image with browser cookies through the protected transport.

    Playwright's APIRequestContext is not intercepted by context.route().
    """
    with httpx.Client(
        **public_httpx_client_kwargs(),
        timeout=timeout / 1000,
        follow_redirects=True,
        cookies=_browser_cookies(context),
    ) as client:
        return client.get(url, headers=headers)
