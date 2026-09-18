# Nextcloud + ChatGPT over MCP

**English** | [Русский](README.ru.md)

A self-hosted MCP integration that gives ChatGPT controlled access to
Nextcloud, including OAuth, local OCR, PDF/DOCX/XLSX processing, file creation,
and verified delivery. Initial implementation and acceptance: September 18,
2026.

Verified stack: Ubuntu 24.04, Python 3.12, FastMCP 4.0.5, Nextcloud 33.0.8,
Tesseract 5.3.4, LibreOffice 24.2.7.2, and bubblewrap 0.9.0.

This is the English edition of the complete deployment guide. It preserves the
real failure history, security boundaries, and acceptance evidence. The server
is designed for one Nextcloud owner; multi-user use requires a different
authorization and isolation model.

## 1. Capabilities

- Browse approved folders and search by filename.
- Read PDF, DOCX, XLSX, text formats, images, and multipage TIFF.
- Run local Russian/English OCR and render pages for visual verification.
- Create DOCX/XLSX, convert Office files to PDF, and extract PDF content.
- Save only into approved roots, then read back and verify SHA-256.
- Copy photos into a new structure without deleting originals.

No shell, delete, bulk move, arbitrary URL fetch, macro execution, or
unconditional overwrite tool is exposed.

## 2. Architecture

```text
ChatGPT
  │  Streamable HTTP MCP + OAuth 2.1/PKCE
  ▼
public HTTPS MCP endpoint
  ├── FastMCP OAuthProxy → Nextcloud OAuth2
  ├── Nextcloud WebDAV / OCS API
  └── isolated local document worker
        ├── PyMuPDF + Tesseract rus/eng
        ├── python-docx + openpyxl
        ├── LibreOffice headless
        └── bubblewrap without network or OAuth secrets
```

ChatGPT→MCP and MCP→Nextcloud are separate OAuth relationships. FastMCP handles
downstream DCR and PKCE. Nextcloud uses a static confidential client. Never
enter the upstream Nextcloud `client_secret` in ChatGPT.

## 3. Why a standalone MCP service

The inspected Nextcloud installation had `app_api` and `oauth2`, but no
Assistant, Context Agent, or registered Deploy Daemon. Rather than add that
deployment layer, this project uses official OAuth2, OCS, and WebDAV APIs. This
keeps path policy, document isolation, and rollback explicit.

## 4. Security model

Nextcloud OAuth2 has no file scopes: a token has the selected account's full
permissions. Use a dedicated account with narrowly shared folders when possible.
The service also verifies the exact owner through OCS, fixes the Nextcloud
origin, normalizes relative paths, separates read/write roots, encrypts OAuth
state, and provides no destructive tools.

Documents are untrusted input. The worker receives only the current file, no
token, and no network. It sees read-only code/libraries and one job directory,
has resource limits, rejects unsafe archives and active formulas, and does not
execute macros. This reduces risk but is not a formal proof that every native
parser is safe.

## 5. Prerequisites

- Nextcloud on public HTTPS and admin access to create an OAuth client.
- Ubuntu 24.04 or compatible Linux, Python 3.12, and a separate MCP hostname.
- ChatGPT Developer mode in the web interface.
- Current Nextcloud backups and an independent rollback route.

OpenAI currently documents Developer mode for Pro, Plus, Business, Enterprise,
and Education on the web. Check current availability before deployment.

## 6. Dependencies

```bash
sudo apt update
sudo apt install -y python3 python3-venv python3-pip \
  tesseract-ocr tesseract-ocr-rus tesseract-ocr-eng \
  libreoffice bubblewrap fonts-dejavu-core fontconfig
```

Times New Roman requires a legally obtained local font package and is not
distributed here. After installation, run `fc-cache -f` and verify with
`fc-match 'Times New Roman'`.

## 7. Install the service files

```bash
git clone https://github.com/karamnovr-code/nextcloud-chatgpt-mcp.git
cd nextcloud-chatgpt-mcp

sudo useradd --system --home /var/lib/nextcloud-mcp \
  --shell /usr/sbin/nologin nextcloud-mcp
sudo install -d -o root -g root -m 0755 /opt/nextcloud-mcp
sudo install -d -o root -g nextcloud-mcp -m 0750 /etc/nextcloud-mcp
sudo install -d -o nextcloud-mcp -g nextcloud-mcp -m 0700 \
  /var/lib/nextcloud-mcp /var/lib/nextcloud-mcp/oauth \
  /var/lib/nextcloud-mcp/jobs
```

Copy the reviewed checkout to `/opt/nextcloud-mcp`, then:

```bash
python3 -m venv /opt/nextcloud-mcp/.venv
/opt/nextcloud-mcp/.venv/bin/pip install --upgrade pip
/opt/nextcloud-mcp/.venv/bin/pip install -r /opt/nextcloud-mcp/requirements.lock.txt
sudo chown -R root:root /opt/nextcloud-mcp
sudo chmod -R go-w /opt/nextcloud-mcp
```

## 8. Create the Nextcloud OAuth client

The upstream redirect goes to MCP, not directly to ChatGPT:

```text
https://mcp.example.com/auth/callback
```

Create it in **Administration settings → Security → OAuth 2.0 clients**, or:

```bash
sudo -u www-data php occ oauth2:add-client \
  'ChatGPT Nextcloud' \
  'https://mcp.example.com/auth/callback' --output=json
```

Store the returned credentials in a root-only file or secret manager. Keep the
client ID for targeted rollback. Check `oauth2:delete-client --help` before
removal because syntax is version-dependent.

## 9. Configuration

Create `/etc/nextcloud-mcp/config.json` with mode `0640` and owner
`root:nextcloud-mcp`:

```json
{
  "public_url": "https://mcp.example.com",
  "nextcloud_url": "https://cloud.example.com",
  "owner": "nextcloud-user-id",
  "client_id": "NEXTCLOUD_OAUTH_CLIENT_ID",
  "client_secret": "NEXTCLOUD_OAUTH_CLIENT_SECRET",
  "jwt_signing_key": "GENERATE_A_LONG_RANDOM_VALUE",
  "state_dir": "/var/lib/nextcloud-mcp/oauth",
  "jobs_dir": "/var/lib/nextcloud-mcp/jobs",
  "port": 9385,
  "max_input_bytes": 268435456,
  "read_roots": ["ChatGPT Nextcloud", "Documents", "Photos"],
  "write_roots": ["ChatGPT Nextcloud"],
  "deny_roots": ["Documents/System"],
  "read_only_segments": ["Source materials"],
  "oauth_redirect_bridge": true
}
```

Use a persistent high-entropy `jwt_signing_key`; changing it invalidates stored
registrations and OAuth state. Downstream ChatGPT uses PKCE S256. The tested
Nextcloud confidential client does not, so PKCE and the MCP `resource` parameter
are not forwarded upstream. The allowlisted callback is
`https://chatgpt.com/connector_platform_oauth_redirect`.

## 10. HTTPS proxy

```caddyfile
mcp.example.com {
    request_body {
        max_size 32MiB
    }
    header {
        X-Content-Type-Options nosniff
        Referrer-Policy no-referrer
    }
    reverse_proxy 127.0.0.1:9385 {
        flush_interval -1
    }
}
```

Validate Caddy before reload. A remote TLS edge may use a dedicated reverse SSH
forward bound only to loopback, with shell, PTY, agent, and X11 disabled. Never
expose the backend directly.

## 11. systemd

Use [deployment/nextcloud-mcp.service.example](deployment/nextcloud-mcp.service.example).
One unusual setting is deliberate: `ProtectKernelTunables=false`. With `true`,
systemd masked `/proc`, preventing the unprivileged inner bubblewrap from
mounting its own `/proc`. The dedicated UID, empty capability set,
`NoNewPrivileges`, read-only system, and per-document sandbox remain enabled.

```bash
sudo install -m 0644 deployment/nextcloud-mcp.service.example \
  /etc/systemd/system/nextcloud-mcp.service
sudo systemctl daemon-reload
sudo systemctl enable --now nextcloud-mcp.service
```

## 12. Document sandbox

Each job runs through fixed `bwrap --unshare-all` arguments with read-only
libraries, virtualenv, and code; one writable `/work`; private `/tmp`, `/proc`,
and `/dev`; and no network. User input never enters a shell command.

LibreOffice must not create a second nested bubblewrap. The implementation
reuses the outer sandbox only when launcher markers, code permissions,
cwd/HOME, and the current request file all match.

## 13. OCR and formats

- **PDF:** native text first, then Tesseract `rus+eng`. Every page carries a
  source marker; completion requires `next_start = null`.
- **DOCX:** paragraphs, tables, nested tables, headers/footers, and content
  controls. Units are blocks because OOXML has no reliable physical pages.
- **XLSX:** sheets, values, formulas, and bounds derived from real cell records,
  not a possibly stale worksheet dimension.
- **Images:** JPEG, PNG, HEIC, and multipage TIFF, with bounded downscaling.

OCR can confuse characters: `OCR` became Cyrillic `ОСВ` in one acceptance test.
Handwriting, seals, tables, and material figures require visual review. PDF
conversion preserves extracted content, not exact layout. DOC/XLS and macros
are unsupported. LibreOffice validation is not Microsoft Office validation.

## 14. Word and Excel quality rules

The official Word profile applies A4, baseline margins, Times New Roman 14,
alignment, and indentation. A real approved `template_path` takes precedence
and preserves its styles/page setup. Excel formulas must be explicit objects,
for example `{ "formula": "=SUM(A1:A3)" }`.

The bundled `server.py` and `document_rules.md` reflect the original Russian
official-document workflow. International deployments should translate or
replace those runtime instructions for their language, templates, page format,
and local document standards before production use.

After generation: wait for `completed`, call `save_job`, require
`verified: true` and SHA-256, reopen the saved file, and inspect rendered pages
when layout matters.

## 15. Connect in ChatGPT

1. Open **Settings → Security and login → Developer mode** in ChatGPT web.
2. Create a developer-mode app and enter `https://mcp.example.com/mcp`.
3. Select OAuth/DCR. Do not enter the upstream Nextcloud secret.
4. Sign in to Nextcloud and press **Allow access** once.
5. After return, press **Refresh** to load current tools and instructions.
6. Start a new chat and select the app.

Review write-tool JSON arguments before confirming.

## 16. Mobile OAuth: stuck consent, then `state mismatch`

On iPhone, the first consent click appeared to do nothing; the second returned
`state mismatch`. The first POST had succeeded and consumed the state:

```text
Nextcloud POST → MCP /auth/callback → 302 ChatGPT callback
```

Some browsers enforce the form page's CSP `form-action` across subsequent
redirects. `OAuthRedirectBridge` converts only a GET `/auth/callback` 302 with
one exact allowlisted ChatGPT destination into a no-store HTML transition. It
preserves `Set-Cookie`, adds strict CSP/referrer policy, and never changes code,
state, cookies, or PKCE. Re-evaluate this workaround after dependency updates.

Never log OAuth query strings, code, state, cookies, authorization headers,
tokens, or secrets. A server-side 302 does not prove the browser followed it.

## 17. Worker failure on a 14 KB PDF

All jobs failed before parsing because `ProtectKernelTunables=true` caused:

```text
bwrap: Can't mount proc on /newroot/proc: Operation not permitted
```

Using an empty `/proc` broke LibreOffice and was rejected. The correct fix was
`ProtectKernelTunables=false` while retaining the other restrictions. Acceptance
smoke must reproduce every installed `[Service]` property. Failed job IDs need
new submissions after repair.

## 18. Failure history

| Failure | Cause | Fix |
|---|---|---|
| Guessed callback | Stale example | Exact current callback and allowlist |
| Health 200 treated as readiness | Process only | Test metadata, 401, OAuth, discovery, real tool |
| Connected account, no tools | Stale snapshot | Press Refresh |
| Second-click state mismatch | State already consumed | Start a new attempt; keep CSRF checks |
| Nested bubblewrap failed | Existing user namespace | Reuse only a verified outer sandbox |
| Smoke omitted unit properties | Manual drift | Read every actual service property |
| PDF substituted font | Font invisible in sandbox | Read-only font mount and embedded-font check |
| DOCX lost nested content | Incomplete traversal | Recursive extraction and regression tests |
| XLSX hid cells | Stale dimension | Inspect actual cell records |
| Dangerous template formula survived | Only new values scanned | Scan all formulas/names/external functions |
| Stream exceeded memory | Trusted header | Count received bytes; return no partial result |
| Credential reached traceback | Unsafe CLI parsing | Revoke it; parse structured output silently |
| Pytest collected upstream tests | Unbounded discovery | Restrict `testpaths` |
| OAuth events lost in WebDAV noise | Broad logging | Short window and OAuth route allowlist |
| Browser-open used as MCP test | MCP is not a page | Use an MCP client; unauthenticated 401 is normal |
| `OCR` read as `ОСВ` | OCR uncertainty | Verify material fields with page previews |

## 19. Acceptance matrix

```bash
curl -fsS https://mcp.example.com/healthz
curl -i https://mcp.example.com/mcp
curl -fsS https://mcp.example.com/.well-known/oauth-authorization-server
curl -fsS https://mcp.example.com/.well-known/oauth-protected-resource/mcp
```

Expect health 200, unauthenticated MCP 401, metadata 200, literal issuer match,
and S256 support. Reject unknown redirects, missing PKCE, raw upstream bearer,
another user's token, traversal/encoding/URLs, source writes, and overwrite.
Test native and scanned PDFs, visual previews, complete pagination, nested DOCX,
formula/stale-dimension XLSX, conversions, large photos, and multipage TIFF.

The real user route is: connect → Refresh → read a known scan completely →
verify figures visually → create/save Word and Excel → reopen both → verify links.

## 20. Acceptance prompt

```text
Use only the ChatGPT Nextcloud app. Open "ChatGPT Nextcloud/Connection test".
Read the entire PDF. For a scan, use OCR and verify amounts against the page
image. Create a Word report and an Excel workbook with line items and total.
Save both under new date-prefixed names, reopen them, and return their links.
Do not overwrite existing files.
```

Use synthetic data until the complete route passes.

## 21. Updating

Preserve active source hashes, private configuration, OAuth state, unit/proxy
configuration, and free-space evidence. Test in a separate checkout. Install
only reviewed files, restart only MCP, verify metadata/401/worker, Refresh
ChatGPT after tool changes, and repeat the user route. Do not casually delete
OAuth state or rotate `jwt_signing_key`.

## 22. Targeted rollback

Stop only the MCP service/tunnel; remove only its proxy block; revoke only its
OAuth client and tunnel key; preserve user results; verify ordinary Nextcloud.
Do not restore an entire old proxy configuration or database over newer changes.

## 23. Never publish

Never publish client/signing secrets, tokens, cookies, OAuth code/state,
callback query strings, private SSH keys/topology, personal names/paths,
database dumps, private logs, live configuration, OAuth state, or real jobs.
Public repositories should contain placeholders only.

## 24. Primary references

- [OpenAI: ChatGPT Developer mode](https://developers.openai.com/api/docs/guides/developer-mode)
- [OpenAI: OAuth authentication for MCP apps](https://developers.openai.com/plugins/build/auth)
- [Nextcloud: OAuth2](https://docs.nextcloud.com/server/stable/admin_manual/configuration_server/oauth2.html)
- [Nextcloud: WebDAV APIs](https://docs.nextcloud.com/server/stable/developer_manual/client_apis/WebDAV/basic.html)
- [Nextcloud: WebDAV SEARCH](https://docs.nextcloud.com/server/stable/developer_manual/client_apis/WebDAV/search.html)
- [FastMCP: OAuth Proxy](https://github.com/PrefectHQ/fastmcp/blob/main/docs/servers/auth/oauth-proxy.mdx)
- [MDN: CSP `form-action`](https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/Content-Security-Policy/form-action)

## 25. Original deployment evidence

Verified: Nextcloud 33.0.8 healthy; FastMCP metadata and unauthenticated 401;
DCR, S256, exact callback, and successful owner OAuth; 14 tools after Refresh;
Russian scan and `10,000 + 5,000 = 15,000` checked against the image; Word/Excel
saved and reopened; 12 previously failing jobs passed after the systemd fix;
official Word profile passed structural, PDF render, and real Times New Roman
checks; final suite passed 82 tests.

Not claimed: formal safety of every parser, perfect OCR, exact reconstruction of
complex PDF layouts, universal Microsoft Office compatibility, universal need
for the redirect bridge, or a multi-day refresh-token lifecycle test.

The acceptance target is the full user route:
**find → read completely → verify visually → create → save → reopen**.
