"""End a form redirect chain before returning to the registered ChatGPT client.

Some browsers enforce the originating page's ``form-action`` CSP across HTTP
redirects.  Nextcloud permits the registered MCP callback, but the callback's
next 302 points at ChatGPT.  Replacing only that final, tightly allowlisted 302
with a no-store HTML transition ends the form navigation before returning to
ChatGPT.  OAuth code, state, cookies, and PKCE values are not modified.
"""

from html import escape
from urllib.parse import urlsplit


class OAuthRedirectBridge:
    """Bridge only the successful MCP callback redirect to ChatGPT."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if (
            scope.get("type") != "http"
            or scope.get("path") != "/auth/callback"
            or scope.get("method") != "GET"
        ):
            return await self.app(scope, receive, send)

        bridged = False

        async def wrapped(message):
            nonlocal bridged
            if message["type"] == "http.response.start" and message["status"] == 302:
                headers = message.get("headers", [])
                locations = [value for key, value in headers if key.lower() == b"location"]
                if len(locations) == 1:
                    location = locations[0].decode("latin-1")
                    try:
                        url = urlsplit(location)
                        valid = (
                            url.scheme == "https"
                            and url.netloc == "chatgpt.com"
                            and url.path == "/connector_platform_oauth_redirect"
                            and not url.fragment
                            and not any(ord(char) < 32 for char in location)
                        )
                    except ValueError:
                        valid = False
                    if valid:
                        target = escape(location, quote=True)
                        body = (
                            '<!doctype html><html lang="en"><head><meta charset="utf-8">'
                            '<meta name="referrer" content="no-referrer">'
                            f'<meta http-equiv="refresh" content="0;url={target}">'
                            '<title>Return to ChatGPT</title></head><body>'
                            '<p>Returning to ChatGPT...</p>'
                            f'<a rel="noreferrer" href="{target}">Continue to ChatGPT</a>'
                            '</body></html>'
                        ).encode("utf-8")
                        excluded = {
                            b"location",
                            b"content-length",
                            b"content-type",
                            b"cache-control",
                            b"content-security-policy",
                            b"referrer-policy",
                        }
                        kept = [
                            (key, value)
                            for key, value in headers
                            if key.lower() not in excluded
                        ]
                        kept += [
                            (b"content-type", b"text/html; charset=utf-8"),
                            (b"content-length", str(len(body)).encode()),
                            (b"cache-control", b"no-store"),
                            (b"referrer-policy", b"no-referrer"),
                            (
                                b"content-security-policy",
                                b"default-src 'none'; base-uri 'none'; "
                                b"form-action 'none'; frame-ancestors 'none'",
                            ),
                        ]
                        bridged = True
                        await send(
                            {"type": "http.response.start", "status": 200, "headers": kept}
                        )
                        await send({"type": "http.response.body", "body": body})
                        return
            if bridged and message["type"] == "http.response.body":
                return
            await send(message)

        await self.app(scope, receive, wrapped)
