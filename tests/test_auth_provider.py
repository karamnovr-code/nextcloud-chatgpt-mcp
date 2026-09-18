import asyncio
import os
import time

import httpx
import pytest

from auth_provider import NextcloudOwnerVerifier, build_auth
from fastmcp.server.auth.oauth_proxy.models import JTIMapping, UpstreamTokenSet


def test_only_canonical_owner_receives_upstream_token():
    async def run():
        seen = []
        def handle(request):
            seen.append(request)
            return httpx.Response(200, json={"ocs": {"meta": {"statuscode": 100}, "data": {"id": "test-owner", "email": "private"}}})
        verifier = NextcloudOwnerVerifier("https://cloud.example", "test-owner", transport=httpx.MockTransport(handle))
        result = await verifier.verify_token("upstream-fixture")
        assert result.token == "upstream-fixture"
        assert result.subject == "test-owner"
        assert result.claims == {"sub": "test-owner"}
        assert seen[0].url.path == "/ocs/v2.php/cloud/user"
        assert seen[0].headers["authorization"] == "Bearer upstream-fixture"
    asyncio.run(run())


@pytest.mark.parametrize("status,payload", [
    (200, {"ocs": {"data": {"id": "someone-else"}}}),
    (200, {"ocs": {"data": {"id": "Roman"}}}),
    (200, {"ocs": {"data": {"displayname": "test-owner"}}}),
    (200, []), (200, {"ocs": []}), (401, {}), (302, {}),
    (200, {"ocs": {"meta": {"statuscode": 997}, "data": {"id": "test-owner"}}}),
])
def test_identity_failure_denied(status, payload):
    async def run():
        verifier = NextcloudOwnerVerifier("https://cloud.example", "test-owner", transport=httpx.MockTransport(lambda _: httpx.Response(status, json=payload)))
        assert await verifier.verify_token("fixture") is None
    asyncio.run(run())


def test_transport_failure_denied_without_token_logging(caplog):
    async def run():
        def fail(request):
            raise httpx.ConnectError("error-with-sensitive-value", request=request)
        verifier = NextcloudOwnerVerifier("https://cloud.example", "test-owner", transport=httpx.MockTransport(fail))
        assert await verifier.verify_token("sensitive-token") is None
        assert "sensitive" not in caplog.text
    asyncio.run(run())


def config(tmp_path):
    return dict(public_url="https://mcp.example", nextcloud_url="https://cloud.example", client_id="fixture-client", client_secret="fixture-secret", state_dir=str(tmp_path / "state"), jwt_signing_key="a-strong-fixture-key-" + "x" * 48, owner="test-owner")


def test_proxy_strict_redirects_and_encrypted_persistent_storage(tmp_path):
    async def run():
        cfg = config(tmp_path)
        first = build_auth(cfg)
        assert first._forward_pkce is False
        assert first._forward_resource is False
        assert first._validate_client_redirect_uri("https://chatgpt.com/connector_platform_oauth_redirect")
        for bad in ["https://evil.example/cb", "https://chatgpt.com.evil.example/connector_platform_oauth_redirect", "http://chatgpt.com/connector_platform_oauth_redirect", "https://chatgpt.com/other", "https://chat.openai.com/aip/any", "https://chatgpt.com/connector_platform/oauth/callback", "https://chatgpt.com/connector/oauth/any", "https://chatgpt.com/connector_platform_oauth_redirect/extra"]:
            assert not first._validate_client_redirect_uri(bad)
        await first._client_storage.put("fixture", {"access_token": "never-plaintext"}, collection="test")
        second = build_auth(cfg)
        assert await second._client_storage.get("fixture", collection="test") == {"access_token": "never-plaintext"}
        for path in (tmp_path / "state").rglob("*"):
            if path.is_file():
                assert b"never-plaintext" not in path.read_bytes()
        assert os.stat(tmp_path / "state").st_mode & 0o777 == 0o700
    asyncio.run(run())


def test_http_metadata_advertises_rfc9207_for_stable_chatgpt_callback(tmp_path):
    from starlette.applications import Starlette
    async def run():
        proxy = build_auth(config(tmp_path))
        app = Starlette(routes=proxy.get_routes("/mcp"))
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://mcp.example") as client:
            response = await client.get("/.well-known/oauth-authorization-server")
        assert response.status_code == 200
        metadata = response.json()
        assert metadata["authorization_response_iss_parameter_supported"] is True
        assert metadata["issuer"].rstrip("/") == "https://mcp.example"
        assert metadata["code_challenge_methods_supported"] == ["S256"]
    asyncio.run(run())


@pytest.mark.parametrize("key,value", [("owner", ""), ("jwt_signing_key", "short"), ("public_url", "http://mcp.example"), ("nextcloud_url", "https://name:password@cloud.example"), ("nextcloud_url", "https://cloud.example/?secret=1")])
def test_bad_configuration_fails_closed(tmp_path, key, value):
    cfg = config(tmp_path); cfg[key] = value
    with pytest.raises(ValueError):
        build_auth(cfg)


@pytest.mark.parametrize("owner", ["test-owner", "not-test-owner"])
def test_proxy_swaps_only_signed_reference_to_owner_upstream_token(tmp_path, owner):
    async def run():
        cfg = config(tmp_path)
        proxy = build_auth(cfg)
        proxy.set_mcp_path("/mcp")
        seen_tokens = []
        def respond(request):
            seen_tokens.append(request.headers["authorization"])
            return httpx.Response(200, json={"ocs": {"data": {"id": owner}}})
        proxy._token_validator.transport = httpx.MockTransport(respond)
        now = time.time()
        await proxy._upstream_token_store.put("upstream-id", UpstreamTokenSet(
            upstream_token_id="upstream-id", access_token="native-nextcloud-fixture",
            refresh_token=None, refresh_token_expires_at=None, expires_at=now + 3600,
            token_type="Bearer", scope="", client_id="chatgpt-fixture", created_at=now,
        ))
        await proxy._jti_mapping_store.put("reference-id", JTIMapping(
            jti="reference-id", upstream_token_id="upstream-id", created_at=now,
        ))
        downstream = proxy.jwt_issuer.issue_access_token(
            client_id="chatgpt-fixture", scopes=[], jti="reference-id", expires_in=3600,
        )
        assert await proxy.load_access_token("native-nextcloud-fixture") is None
        assert seen_tokens == []
        result = await proxy.load_access_token(downstream)
        if owner == "test-owner":
            assert result.token == "native-nextcloud-fixture"
            assert result.token != downstream
        else:
            assert result is None
        assert seen_tokens == ["Bearer native-nextcloud-fixture"]
    asyncio.run(run())
