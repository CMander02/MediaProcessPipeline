"""yt-dlp configured to send media requests through the public-only transport."""

from __future__ import annotations

import io

import httpx

from app.core.public_urls import PublicHTTPTransport, UnsafeURLError, _ResponseStream


def public_youtube_dl(options):
    # Keep yt-dlp lazy: API startup must not import all its extractors.
    import yt_dlp
    from yt_dlp.networking.common import Features, RequestHandler, Response
    from yt_dlp.networking.exceptions import HTTPError, RequestError
    from yt_dlp.utils.networking import select_proxy

    class PublicRH(RequestHandler):
        _SUPPORTED_URL_SCHEMES = ("http", "https")
        _SUPPORTED_PROXY_SCHEMES = ("http", "https", "socks5", "socks5h")
        _SUPPORTED_FEATURES = (Features.ALL_PROXY, Features.NO_PROXY)

        def _check_extensions(self, extensions):
            super()._check_extensions(extensions)
            for name in ("cookiejar", "timeout", "legacy_ssl", "keep_header_casing"):
                extensions.pop(name, None)

        def _send(self, request):
            client = httpx.Client(
                transport=PublicHTTPTransport(
                    proxy=select_proxy(request.url, self._get_proxies(request)),
                    verify=self._make_sslcontext(request.extensions.get("legacy_ssl")),
                ),
                trust_env=False,
                follow_redirects=True,
                timeout=self._calculate_timeout(request),
                cookies=self._get_cookiejar(request),
            )
            try:
                response = client.send(
                    client.build_request(
                        request.method,
                        request.url,
                        headers=self._get_headers(request),
                        content=request.data,
                    ),
                    stream=True,
                )
                result = Response(
                    io.BufferedReader(_ResponseStream(response, client)),
                    str(response.url),
                    dict(response.headers),
                    response.status_code,
                    reason=response.reason_phrase,
                )
                if response.status_code >= 400:
                    raise HTTPError(result)
                return result
            except HTTPError:
                raise
            except Exception as exc:
                client.close()
                raise RequestError(str(exc), cause=exc) from exc

    class PublicYoutubeDL(yt_dlp.YoutubeDL):
        def build_request_director(self, handlers, preferences=None):
            # Do not leave a less strict handler available for fallback.
            return super().build_request_director([PublicRH], preferences)

        def dl(self, name, info, subtitle=False, test=False):
            from yt_dlp.dependencies import Cryptodome
            from yt_dlp.downloader import get_suitable_downloader
            from yt_dlp.downloader.fragment import FragmentFD
            from yt_dlp.downloader.hls import HlsFD
            from yt_dlp.downloader.http import HttpFD

            downloader = get_suitable_downloader(info, self.params, to_stdout=(name == "-"))
            if downloader is None or not issubclass(downloader, (HttpFD, FragmentFD)):
                raise UnsafeURLError(
                    "This stream requires an external downloader without URL protection"
                )
            if issubclass(downloader, HlsFD):
                # HlsFD can silently delegate unsupported manifests to ffmpeg.
                # Supply the checked manifest so no second fetch can change it.
                manifest = info.get("hls_media_playlist_data")
                if not manifest:
                    with self.urlopen(info["url"]) as response:
                        manifest = response.read().decode("utf-8", "replace")
                        info = {**info, "url": response.url}
                if not HlsFD.can_download(manifest, info, False) or (
                    "#EXT-X-KEY:METHOD=AES-128" in manifest and not Cryptodome.AES
                ):
                    raise UnsafeURLError(
                        "This HLS stream cannot be downloaded safely by the native downloader"
                    )
                info = {**info, "hls_media_playlist_data": manifest}
            return super().dl(name, info, subtitle, test)

    return PublicYoutubeDL(
        {
            **options,
            "hls_prefer_native": True,
            "external_downloader": {"default": "native"},
            "postprocessor_args": {
                **options.get("postprocessor_args", {}),
                "ffmpeg_i": ["-protocol_whitelist", "file,pipe,crypto,data"],
            },
        }
    )
