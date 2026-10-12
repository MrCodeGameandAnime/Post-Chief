# Post Chief MCP

The adapter lives in `postchief_mcp/` and runs inside the existing API process.
Its remote endpoint is `https://postchief.404builds.com/mcp`. The agent's computer
does not need to be running; recurring AI runs are configured separately.

## Connect your cloud agent

1. Back up PostgreSQL, deploy the updated image, and apply additive migration
   `c84b3d91a602`. Retain the previous image for rollback.
2. In ChatGPT's custom MCP settings, enter the server URL above and choose OAuth.
   Use predefined client ID **`post-chief-chatgpt`**, with no client secret
   (public client, PKCE, token endpoint authentication method `none`).
3. Copy the **exact OAuth callback URI** from the connection's management screen
   into the private VM configuration. Do not use wildcard callbacks or guess a
   generic URL. This server does not advertise DCR, CIMD, or issuer-response
   identification; the client may supply a connection-specific callback.
4. Sign into Post Chief in the linking window, review permissions, and allow the
   connection. Credentials and tokens do not belong in chats or tool arguments.
5. Install/select the resulting plugin for the agent. Ask it to call
   `get_identity`, `get_autonomy`, `list_connections`, and `list_campaigns`.
   A settings entry or API health response alone does not verify agent access.

If this client cannot use a predefined public PKCE client, keep authentication
enabled and revise client registration based on its connection error.

## VM configuration

Add these to the existing `.env`, preserving its signing/encryption keys and
multiline GitHub private key. Compose correctly parses that file.

```dotenv
MCP_ENABLED=true
MCP_CLIENT_ID=post-chief-chatgpt
MCP_REDIRECT_URIS='["EXACT_HTTPS_CALLBACK_FROM_CHATGPT"]'
PUBLIC_URL=https://postchief.404builds.com
FRONTEND_URL=https://postchief.404builds.com
SECURE_COOKIES=true
```

Replace the callback placeholder before starting. `MCP_ENABLED` defaults to
false. With MCP enabled and an empty callback list, discovery works but account
linking fails closed. Restart application containers to apply configuration.
The existing Cloudflare route covers MCP, metadata, and consent paths; no new
hostname, VM port, or Cloudflare Access login is required.

### Connect Codex alongside ChatGPT

Configure the same predefined public client in Codex:

```powershell
codex mcp add post_chief --url https://postchief.404builds.com/mcp --oauth-client-id post-chief-chatgpt
```

Copy the callback printed by Codex. Keep the existing ChatGPT callback and add
the Codex URI to `MCP_REDIRECT_URIS`. For a local Codex callback, register
`http://127.0.0.1/callback/<exact-callback-id>` without a port. The server permits
the ephemeral port Codex chooses while matching the literal host, path, and query
exactly (RFC 8252). No other HTTP host is permitted. A registered explicit port
remains exact. The authorization code is bound to the full callback including
the port used for that request.

After deploying this callback support and restarting the API, run
`codex mcp login post_chief` and approve the separate Codex connection. Existing
ChatGPT grants and sessions remain intact. Refresh the MCP tools in Codex after
login; verify `get_identity` before using mutations.

## Tools

| Group | Tools |
|---|---|
| Identity | get_identity, get_autonomy, list_connections |
| Content | list_campaigns, get_campaign, get_publication, create_campaign, update_campaign, cancel_campaign |
| Delivery | schedule_campaign, publish_campaign, retry_publication |
| Media | list_assets, import_github_asset |
| GitHub | get_workspace, list_repositories, read_github_content, write_github_content |
| Reporting | get_analytics, refresh_analytics, list_reporting_accounts, get_account_report |
| Audit | list_approvals, list_audit_events, report_agent_run |

Schemas reuse API validation. Edits require the current campaign revision;
schedule timestamps require a timezone offset. GitHub media imports require
`media:write` and `github:read` and preserve authorized repository checks.
Binary media is not embedded in tool JSON. Responses are bounded to 1 MiB;
large reads require smaller pages or a specific record.

`status=ok` means the API operation succeeded, not that a scheduled post has
finished publishing. Read destination outcomes with `get_publication`.
`approval_required` carries the original action/payload and approval ID. The
owner approves in the dashboard; the agent resubmits identical inputs plus
`approval_id`. Expired, changed, or consumed approvals are rejected.

Do not automatically retry mutations after `outcome_unknown` or too-large
responses: an operation may have completed. Read current state first. Provider
quotas, throttling, reconciliation, and Post Chief autonomy rules still apply.

## Authorization

Consent creates an identity named `MCP: post-chief-chatgpt` in Agent settings.
Revoking it immediately blocks its OAuth access and refresh tokens. Scope
reductions affect existing tokens; expansions cannot exceed original consent.
Link again to grant additional permissions. Owner-only controls are not tools.

Codes last five minutes and are single-use; access tokens last fifteen minutes.
Refresh tokens rotate and expire with their family after thirty days. Reusing a
spent refresh token revokes that family. Expiry/revocation requires renewed
authorization. Hashes and grants persist in PostgreSQL; raw tokens are not
stored. Expired records are pruned in bounded batches during startup and
authorization activity. Authorization requests have bounded size/rate limits.

## Verification and deployment

Focused tests are in `../tests/test_mcp_*.py`. PostgreSQL concurrency tests use
a disposable database configured through `POST_CHIEF_TEST_DATABASE_URL`.
Providers are mocked; these tests must not post to real accounts.

Build/export on Windows and transfer through the existing SSH/SCP workflow.
Back up before migration, then load the image and recreate application
containers. Confirm actual ChatGPT linking/tool calls, restart persistence,
and VM idle/peak memory. Keep Windows worker/scheduler stopped. Rollback
restores the old image without dropping tables or restored content/media.
