import asyncio

from oauth_redirect_bridge import OAuthRedirectBridge


URL = (
    "https://chatgpt.com/connector_platform_oauth_redirect"
    "?code=TEST_CODE&state=TEST_STATE&iss=https%3A%2F%2Fmcp.example%2F"
)


async def run_bridge(path="/auth/callback", method="GET", url=URL, status=302):
    output = []

    async def send(message):
        output.append(message)

    async def app(scope, receive, sender):
        await sender(
            {
                "type": "http.response.start",
                "status": status,
                "headers": [
                    (b"location", url.encode()),
                    (b"set-cookie", b"binding=; Max-Age=0"),
                    (b"content-length", b"0"),
                ],
            }
        )
        await sender({"type": "http.response.body", "body": b""})

    await OAuthRedirectBridge(app)(
        {"type": "http", "path": path, "method": method}, None, send
    )
    return output


def test_successful_callback_becomes_safe_html_transition():
    output = asyncio.run(run_bridge())
    assert len(output) == 2
    assert output[0]["status"] == 200
    headers = dict(output[0]["headers"])
    assert headers[b"set-cookie"] == b"binding=; Max-Age=0"
    assert headers[b"cache-control"] == b"no-store"
    assert b"location" not in headers
    assert b"&amp;state=TEST_STATE" in output[1]["body"]
    assert b"TEST_CODE" in output[1]["body"]


def test_unrelated_routes_and_invalid_destinations_are_untouched():
    cases = (
        {"path": "/mcp"},
        {"method": "POST"},
        {"status": 400},
        {"url": "https://evil.example/x"},
        {"url": "https://chatgpt.com.evil.example/connector_platform_oauth_redirect"},
        {"url": "https://chatgpt.com/other"},
        {"url": "http://chatgpt.com/connector_platform_oauth_redirect"},
    )
    for arguments in cases:
        output = asyncio.run(run_bridge(**arguments))
        assert output[0]["status"] == arguments.get("status", 302)
        assert output[1]["body"] == b""
