"""Owner-only Nextcloud native OAuth2 bridge for the isolated ChatGPT MCP.

FastMCP owns downstream PKCE, consent, code exchange, token rotation and token
swapping. Only its stored upstream token reaches OCS/WebDAV. Nextcloud OAuth2
does not enforce file scopes: the document service must enforce path policy.
"""
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from fastmcp.server.auth import AccessToken, OAuthProxy, TokenVerifier
from key_value.aio.stores.filetree import (
    FileTreeStore,
    FileTreeV1CollectionSanitizationStrategy,
    FileTreeV1KeySanitizationStrategy,
)
from key_value.aio.wrappers.encryption import FernetEncryptionWrapper


# FastMCP 4 advertises RFC 9207 and includes `iss` in authorization responses.
# OpenAI's current stable callback applies to that mode (plugins/build/auth).
CHATGPT_REDIRECTS = ["https://chatgpt.com/connector_platform_oauth_redirect"]


def _https_base(value: str) -> str:
    parts = urlsplit(value)
    if (parts.scheme != "https" or not parts.hostname or parts.username
            or parts.password or parts.query or parts.fragment):
        raise ValueError("OAuth endpoints require HTTPS URLs without credentials or query strings")
    return value.rstrip("/")


class NextcloudOwnerVerifier(TokenVerifier):
    """Resolve the upstream bearer to the canonical Nextcloud user each request."""

    def __init__(self, nextcloud_url: str, owner: str, *, transport=None):
        super().__init__(required_scopes=[])
        self.nextcloud_url = _https_base(nextcloud_url)
        if not owner or owner != owner.strip():
            raise ValueError("A canonical Nextcloud owner is required")
        self.owner = owner
        self.transport = transport

    async def verify_token(self, token: str) -> AccessToken | None:
        if not token:
            return None
        try:
            async with httpx.AsyncClient(
                timeout=15.0, follow_redirects=False, transport=self.transport,
                trust_env=False,
            ) as client:
                response = await client.get(
                    f"{self.nextcloud_url}/ocs/v2.php/cloud/user",
                    params={"format": "json"},
                    headers={"Authorization": f"Bearer {token}", "OCS-APIRequest": "true", "Accept": "application/json"},
                )
            if response.status_code != 200:
                return None
            payload = response.json()
            ocs = payload.get("ocs") if isinstance(payload, dict) else None
            if not isinstance(ocs, dict):
                return None
            meta = ocs.get("meta", {})
            if not isinstance(meta, dict) or meta.get("statuscode", 100) not in (100, 200):
                return None
            data = ocs.get("data")
            if not isinstance(data, dict) or data.get("id") != self.owner:
                return None
        except (httpx.HTTPError, ValueError, TypeError):
            # Exception strings can contain request details; never log them.
            return None
        return AccessToken(token=token, client_id="nextcloud-owner", scopes=[],
                           subject=self.owner, claims={"sub": self.owner})


def build_auth(config: dict) -> OAuthProxy:
    public_url = _https_base(config["public_url"])
    nextcloud_url = _https_base(config["nextcloud_url"])
    key = config["jwt_signing_key"]
    if not isinstance(key, str) or len(key) < 32:
        raise ValueError("A persistent high-entropy signing key of at least 32 characters is required")
    if not config["client_id"] or not config["client_secret"]:
        raise ValueError("Static Nextcloud OAuth client credentials are required")
    verifier = NextcloudOwnerVerifier(nextcloud_url, config["owner"])
    directory = Path(config["state_dir"])
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    directory.chmod(0o700)
    store = FileTreeStore(
        data_directory=directory,
        key_sanitization_strategy=FileTreeV1KeySanitizationStrategy(directory),
        collection_sanitization_strategy=FileTreeV1CollectionSanitizationStrategy(directory),
    )
    encrypted_store = FernetEncryptionWrapper(
        key_value=store, source_material=key,
        salt="nextcloud-chatgpt-mcp-oauth-storage-v1", raise_on_decryption_error=True,
    )
    return OAuthProxy(
        upstream_authorization_endpoint=f"{nextcloud_url}/index.php/apps/oauth2/authorize",
        upstream_token_endpoint=f"{nextcloud_url}/index.php/apps/oauth2/api/v1/token",
        upstream_client_id=config["client_id"],
        upstream_client_secret=config["client_secret"],
        token_verifier=verifier, base_url=public_url, redirect_path="/auth/callback",
        allowed_client_redirect_uris=CHATGPT_REDIRECTS.copy(),
        valid_scopes=[], forward_pkce=False, forward_resource=False,
        token_endpoint_auth_method="client_secret_basic",
        client_storage=encrypted_store, jwt_signing_key=key,
        require_authorization_consent=True,
        token_expiry_threshold_seconds=30,
        enable_cimd=False,
    )
