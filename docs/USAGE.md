# Post Chief usage guide

Post Chief is the self-hosted publishing and reporting workspace for 404 Builds.
The dashboard provides human control; external agents use the API or MCP tools;
GitHub holds durable content and reviewed feedback. PostgreSQL stores operational
state, Redis carries immediate jobs, and Celery workers execute provider requests.

This is the current consolidated guide. Documented capabilities do not establish 
that every provider has been approved or tested with a live account. 
See [verification and limitations](#verification-and-limitations).

## Contents

- [Getting started](#getting-started)
- [Deployment and operations](#deployment-and-operations)
- [Dashboard and campaigns](#dashboard-and-campaigns)
- [Scheduling and recovery](#scheduling-and-recovery)
- [GitHub workspace and feedback](#github-workspace-and-feedback)
- [Agents and MCP](#agents-and-mcp)
- [Provider setup and capabilities](#provider-setup-and-capabilities)
  - [Facebook, Instagram, and Threads](#facebook-instagram-and-threads)
  - [X](#x)
  - [Bluesky](#bluesky)
  - [LinkedIn](#linkedin)
  - [Pinterest](#pinterest)
  - [YouTube](#youtube)
  - [TikTok personal](#tiktok-personal)
  - [TikTok business](#tiktok-business)
  - [Google Business Profile](#google-business-profile)
  - [Website analytics and blog sources](#website-analytics-and-blog-sources)
  - [Twitch](#twitch)
- [Analytics and saved reports](#analytics-and-saved-reports)
- [Advertising reports](#advertising-reports)
- [Looker Studio](#looker-studio)
- [Development and CI](#development-and-ci)
- [Troubleshooting](#troubleshooting)
- [Verification and limitations](#verification-and-limitations)

## Getting started

The deployed dashboard is **https://postchief.404builds.com**; remote MCP is at
**https://postchief.404builds.com/mcp**. The Oracle deployment uses
`compose.yaml` followed by `compose.oracle.yaml`, with Cloudflare Tunnel pointing
to the API's loopback address. Commands below run from the application's `root/`
directory, or `~/post-chief` on the VM.

1. Sign in with the existing Post Chief owner account.
2. Open **Connections** and authorize the intended providers. Confirm the account
   identities and capabilities before choosing destinations.
3. Upload media in **Assets**, or import it from an authorized GitHub repository.
4. Create a campaign with master copy, assets, and destination-specific overrides.
5. Review it, then save a draft, schedule it, or publish it.
6. Inspect each destination's outcome. Collect analytics and review GitHub
   feedback when required.

The application does not host an LLM or schedule an AI agent's recurring runs.
Connecting MCP gives an external agent tools; recurring execution is configured
in that agent's host.

## Deployment and operations

### Configuration and credentials

Keep configuration in the deployment's private `.env`. Start from
[`root/.env.example`](../root/.env.example) for a new installation; preserve the
existing file and keys when upgrading.

| Setting | Purpose |
|---|---|
| `POSTGRES_PASSWORD`, `DATABASE_URL` | Database access; Compose supplies the application container URL. |
| `REDIS_URL` | Redis broker; Compose supplies the private container address. |
| `SIGNING_KEY` | Strong signing key, at least 32 characters; protects sessions and signed media URLs. |
| `ENCRYPTION_KEY` | Fernet key used for provider credentials and processing checkpoints. |
| `PUBLIC_URL`, `FRONTEND_URL` | Exact public HTTPS origin; both match when MCP is enabled. |
| `SECURE_COOKIES` | `true` in production; `false` only for local HTTP development. |
| `BOOTSTRAP_EMAIL`, `BOOTSTRAP_PASSWORD` | Create the initial owner once; password must have at least 16 characters. |

Bootstrap never overwrites an existing user or resets their password. Dashboard
password rotation is not implemented. Changing the bootstrap fields therefore
does not change an existing account's credentials.

Provider app credentials are listed in the provider sections below. GitHub PEM
keys can span multiple quoted lines: Compose parses this configuration, while a
raw `docker run --env-file` may reject it. Never print or commit the deployment
file, provider tokens, or credential-bearing URLs.

The production image serves the React dashboard at `/` and the API at `/api`.
Bind the API to `127.0.0.1:8000`; PostgreSQL and Redis remain on the private Docker
network. Use public HTTPS through the existing tunnel or a trusted reverse proxy.
Configure forwarded-header trust for the actual proxy addresses. Provider media
access must reach public HTTPS without an owner session.

### Start and inspect the stack

For a standard deployment that builds on its host:

```bash
docker compose up -d --build --wait --wait-timeout 120 api worker scheduler
docker compose ps
docker compose exec -T worker celery -A postchief_worker.tasks:app inspect ping --timeout 10
```

For the Oracle VM, use the locally built/transferred `post-chief:oracle` image:

```bash
cd ~/post-chief
dc() { sudo docker compose -f compose.yaml -f compose.oracle.yaml "$@"; }
dc up -d api worker scheduler
dc ps
curl --retry 12 --retry-delay 5 --retry-all-errors --max-time 10 \
  -fsS http://127.0.0.1:8000/api/health
dc logs --tail 100 api worker scheduler
sudo docker stats --no-stream
free -h
```

The migration service applies Alembic changes before normal API/worker startup.
Run exactly one scheduler and keep local Windows publishing services stopped
when the VM is the active deployment. Oracle uses one prefork worker process,
bounded logs, smaller PostgreSQL settings, and persistent swap. Monitor available
RAM and sustained swap activity; a single idle snapshot is not a load test.

Database-backed `/api/health`, worker ping, dashboard access, and actual MCP calls
check different parts of the stack. Startup may briefly reset API connections.
Normal worker operation uses Linux prefork process time limits plus bounded
provider network timeouts. Redis uses persistent AOF; database records remain the
durable source of pending work.

### Backups and restoration

Back up PostgreSQL, the media volume, the exact source revision, and deployment
secrets together. Keep an encrypted copy outside the Docker host. Ignored
`root/data/` and GitHub content records do not replace a recoverable off-host
operational backup.

1. Stop scheduler, worker, and API, allowing in-flight work to finish. Abruptly
   interrupting a public write can require reconciliation.
2. Create a custom-format `pg_dump` inside the database container, then copy it
   out. Avoid piping binary dump bytes through Windows PowerShell.
3. Archive the media volume while application services are stopped. Record the
   migration head, source revision, and backup time; preserve the encryption and
   signing keys with the backup.
4. Restart the application. Do not remove its Compose volumes.

Example database backup on Oracle:

```bash
dc stop -t 300 scheduler worker api
dc exec -T db pg_dump -U postchief -d postchief -Fc -f /tmp/post-chief.dump
dc cp db:/tmp/post-chief.dump ./post-chief.dump
sudo chmod 600 ./post-chief.dump
```

Docker's copy may produce a root-owned file, hence `sudo chmod`. This example
backs up the database only; also preserve media and secrets before calling the
backup complete.

Restore first into a separate disposable deployment using the original keys.
Keep its scheduler and worker stopped while checking record counts, migration
head, owner sign-in, media integrity/private downloads, credential decryption,
and checkpoints. Reconcile restored pending jobs against providers: a post may
have been published after the backup. Enable dispatch only after reviewing that
state. Never downgrade the live schema or remove production volumes as a test.

### Upgrades and key rotation

Record the running revision and take a recoverable backup. Review migrations and
CI evidence, stop application services, load/build the reviewed image, migrate,
then restart and check API, worker, dashboard, and MCP access. Retain the previous
image. A code rollback may require a compatible database; do not blindly
downgrade live data. MCP's additive tables can remain when reverting the image.

- Revoke compromised agent keys in **Agent**, then issue new scoped keys.
- Reconnect providers to replace encrypted grants while preserving their account
  identity and publication history.
- Rotate GitHub/private app credentials through the provider, update deployment
  configuration, and restart the application containers.
- Changing `SIGNING_KEY` invalidates sessions and existing signed media links;
  pause publishing during that change.
- Changing `ENCRYPTION_KEY` without re-encrypting every credential/checkpoint
  makes those records unreadable. There is no automated encryption-key rotation
  command; reconnecting alone cannot recover encrypted pending publication state.

## Dashboard and campaigns

The dashboard includes Overview, Planner, Content, Assets, Connections, GitHub,
Analytics, and Agent controls. A campaign appears once, with an independent
publication record for each connected destination.

Use the editor for master copy, provider copy/media overrides, saved drafts,
local-time scheduling, immediate delivery, cancellation, failed-destination
retries, and explicit reconciliation. Save edits before scheduling. Editor times
use the browser's timezone; API scheduling requires an offset-bearing ISO
timestamp and stores UTC. Publication timestamps describe each destination's
actual completion independently from the campaign's original schedule.

Publication attempts retain immutable content. The editor labels this
**Original master copy**; overrides still apply. Changes made directly on a
platform are not automatically synced into that history or GitHub feedback. New
copy after an attempt generally needs a new draft or the specific paused-destination
maintenance flow below.

The editor polls campaign delivery state every fifteen seconds. Unsaved fields
retain their values and opened revision. If another revision is saved, return to
Content and reopen the campaign before editing or scheduling. An unscheduled
draft is distinct from another state with no scheduled time.

Delivery details show status, errors, permalink when available, publication
time, and worker run count (`attempts`). Preparation, processing polls, and retries
can each add a run: multiple runs do not establish multiple public posts.

The shared typed API client uses an HttpOnly owner session and in-memory CSRF
token. Browser tokens/passwords are not persisted by the dashboard. OAuth runs
through server routes; provider app credentials/scopes must already be configured.

### Add a destination after publication

Select an available connected account in **Delivery** and save its draft.
Existing publication IDs, outcomes, timestamps, checkpoints, analytics, and
original master copy remain intact. Addition is blocked while a destination is
pending, retrying, or processing.

| Owner endpoint | Required input and behavior |
|---|---|
| `POST /api/campaigns/{id}/destinations` | Current campaign revision, account ID, and destination body. New publication starts paused (`cancelled`), without dispatch. |
| `PATCH /api/publications/{id}/draft` | Current revision, matching account ID, body, and asset IDs; only paused/unattempted destinations can be edited. Shared copy for another publication on the same provider cannot be changed. |
| `POST /api/publications/{id}/deliver` | Current revision; omit `scheduled_at` for immediate delivery or supply a future offset-bearing timestamp. Only that destination is dispatched. |

These maintenance operations require an owner session. Published-text changes
use the separate [Facebook edit flow](#facebook-instagram-and-threads), or the
native Instagram editing link; editing saved campaign history does not change a
native post.

## Scheduling and recovery

PostgreSQL stores schedules and retry deadlines. One Celery beat dispatcher scans
due publications every fifteen seconds; Redis carries immediate publication IDs,
not long-lived countdown jobs. Broker outages leave work due for a later sweep.

Workers atomically claim a publication with a ten-minute lease and commit before
provider I/O. Duplicate deliveries cannot claim an active lease. Encrypted
provider checkpoints become due database work; successful destinations are
terminal. Campaign status reflects independent outcomes: scheduled, publishing,
published, partial, or failed.

Known-safe transient errors back off from thirty seconds to one hour, with five
automatic retries. Preparation/media processing has a twenty-four-hour deadline.
Bluesky can reconcile its stable record key. Recovery at an uncertain public-write
intent for a provider without safe deduplication requires owner reconciliation;
never blindly retry a timeout that might have published.

| API operation | Meaning |
|---|---|
| `POST /api/campaigns/{id}/schedule` | Schedule using an offset-bearing ISO timestamp. |
| `POST /api/campaigns/{id}/publish` | Accept immediate dispatch; acceptance is not publication success. |
| `POST /api/campaigns/{id}/cancel` | Stop pending/retrying destinations; in-flight writes must reach an outcome first. Does not delete published posts. |
| `POST /api/publications/{id}/retry` | Explicitly retry only the failed destination after resolving its cause. |
| `POST /api/publications/{id}/reconcile` | Owner-only assertion after checking the native provider. |

For reconciliation, submit either
`{"resolution":"published","provider_id":"existing-provider-id"}` or
`{"resolution":"not_published"}` after confirming no public post exists. The
latter permits a separate explicit retry. Reconciliation is audited and is an
owner assertion, not an automatic provider verification. Some providers block a
not-published reset when an accepted native resource is already known.

Providers fetch private media through two-hour, asset/organization-bound signed
URLs. Treat those URLs as credentials and protect access logs. SVG can be stored
privately but must be rasterized before publishing; delivery rejects SVG.

## GitHub workspace and feedback

### Configure the GitHub App

Create a private GitHub App with the dashboard as homepage and
`PUBLIC_URL/api/webhooks/github` as webhook URL. Grant repository **Contents:
read/write** and **Metadata: read**. Keep webhooks active; installation and
installation-repository events are automatic. Generate a private PEM key and
strong webhook secret. Store `GITHUB_APP_ID`, `GITHUB_APP_SLUG`,
`GITHUB_PRIVATE_KEY`, and `GITHUB_WEBHOOK_SECRET` only in private configuration.

Install the App only on intended repositories. In GitHub settings within Post
Chief, select one writable social workspace and optional read-only source
repositories. API equivalents are:

- `GET /api/github/installation-link`
- `GET /api/github/installations/{id}/repositories`
- `POST /api/github/workspace`

Post Chief reduces installation-token permissions per operation. Repository
listing/reads use read-only access, and content tokens are restricted to the
specific repository ID. Writes request permission only for the selected social
workspace; source repositories remain read-only. Account identity alone never
grants repository access. UTF-8 content writes require the current file SHA;
conflicts return 409. Binary media ingestion is separate.

Webhooks verify HMAC SHA-256 and delivery IDs. Duplicate deliveries are
acknowledged once; deleted/suspended installations are disconnected. App JWTs
and short-lived installation tokens are created on demand and are never exposed
to clients or persisted as installation tokens. See [GitHub installation
authentication](https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/authenticating-as-a-github-app-installation).

### Review and sync feedback

`GET /api/feedback/campaigns/{id}/preview` prepares three workspace files:

| File | Default path |
|---|---|
| Post record | `posts/{campaign-id}.md` |
| Publication ledger | `docs/CONTENT_LEDGER.md` |
| Native analytics | `docs/ANALYTICS.md` |

Post records must be Markdown under `posts/`. Stable markers replace generated
campaign blocks without duplicating entries or altering surrounding owner text.
Broken/duplicate markers are conflicts. UTF-8 files up to 1 MiB are supported;
symlink paths/parents and truncated trees are rejected. Credentials, encrypted
processing state, and signed media URLs are excluded. Missing metrics remain
explicitly unavailable.

Review file tabs, diffs, change summaries, and optionally **Full file** in the
dashboard. The dialog supports keyboard navigation, Escape, and focus restoration;
failed writes keep the review open. Submit the preview's `revision`, `digest`,
and `base_commit` to `POST /api/feedback/campaigns/{id}/sync`. Changed campaign
data, metrics, workspace text, or branch head rejects a stale preview. All three
files are committed together using a fast-forward-only branch update. Unchanged
content returns the current commit without writing; a lost response is resolved
with a fresh preview. Preview diff/addition/deletion fields do not replace its
complete-content digest.

Instagram permalinks come from validated native analytics, with a stored-snapshot
fallback for older publications. Collecting metrics retains the permalink but
does not republish or automatically rewrite GitHub. Review a new preview to
update an older record.

Agents need campaign/analytics/GitHub read scopes plus `github:write`. Managed
`feedback.sync` takes the campaign target and the three preview fields; approval
binds the digest/revision. This REST action is not one of the current 25 MCP
tools. External clients may run the feedback loop after publication and analytics;
Post Chief does not generate filler content or run an LLM itself.

GitHub contracts: [trees](https://docs.github.com/en/rest/git/trees),
[commits](https://docs.github.com/en/rest/git/commits), and
[references](https://docs.github.com/en/rest/git/refs).

## Agents and MCP

### Scopes, policies, and approvals

Agent access is organization-scoped, revocable, and audited. Credentials cannot
create keys, manage provider connections, change autonomy, or decide approvals.
Owner access is required for those controls.

| Policy | Behavior |
|---|---|
| `AUTO` | A permitted action can run automatically. |
| `APPROVAL` | The owner must approve the exact action/payload. |
| `DISABLED` | The action cannot run; granted scopes do not override this. |

Defaults allow campaign drafts/edits, media writes, analytics collection, and run
reports automatically. Scheduling, publishing, and workspace writes require
approval. Read the current policies with `GET /api/settings/autonomy` or
`get_autonomy`; the owner may change them in **Agent** or
`PUT /api/settings/autonomy`. Changes affect future actions, not already
authorized schedules; those can be cancelled separately.

Managed REST mutations use `POST /api/agent/actions` with `action`, optional
`target_id`, and `data`. Actions include campaign create/update/schedule/publish/
cancel, publication retry, analytics refresh, GitHub writes, feedback sync, and
account report refresh (`report.refresh`). Native API writes also obey policy;
an approval-required media upload must be performed by the owner.

A 428 response returns the approval ID and canonical payload/context. Only the
owner approves or rejects it through the dashboard or
`POST /api/approvals/{id}/approve` / `/reject`. The agent then resubmits identical
inputs plus `approval_id`. Approvals expire after fifteen minutes, are single-use,
and bind the actor, organization, action, payload, current campaign revision, and
relevant publication outcome. Consumption and database mutation share a
transaction. Changed copy, stale state, or an expired approval requires a new
request; GitHub writes still check the SHA.

`GET /api/audit`, `/api/agent/runs`, and `/api/approvals` expose scoped records;
agents see only their own approvals. Agent request audits record method/path
without credentials or bodies. `POST /api/agent/runs` records a bounded concise
summary/status, never private reasoning, passwords, or tokens.

### Connect the cloud ChatGPT agent

The adapter lives under [`root/mcp/`](../root/mcp/README.md), runs inside the API
process, and exposes stateless Streamable HTTP at
`https://postchief.404builds.com/mcp`. No extra VM service, DNS record, port, or
Cloudflare Access login is needed.

1. Deploy the MCP-enabled image and apply additive migration `c84b3d91a602`.
2. In ChatGPT's main **Plugins** directory, select **+**, then **Create MCP App**
   or **Add custom MCP server**, according to the interface. The settings page
   for installed-plugin permissions is not the creation form.
3. Enter **Post Chief**, the `/mcp` server URL, and **OAuth** authentication.
4. In Advanced OAuth settings, choose **User-Defined OAuth Client**, client ID
   `post-chief-chatgpt`, no client secret, and token endpoint auth method `none`.
   Leave base scopes empty; select default scopes appropriate to the agent.
5. Copy the exact callback URI shown by that connection into the VM configuration
   below. Do not guess a generic URL or permit wildcard callbacks.
6. Restart the application containers, create/install the plugin, sign into Post
   Chief in the OAuth window, review scopes, and allow the connection.
7. Refresh the plugin if its tool list is empty. Select it in the intended agent
   chat and call `get_identity`, `get_autonomy`, `list_connections`, and
   `list_campaigns`. Discovery or API health alone does not prove agent execution.

```dotenv
MCP_ENABLED=true
MCP_CLIENT_ID=post-chief-chatgpt
MCP_REDIRECT_URIS='["EXACT_HTTPS_CALLBACK_FROM_CHATGPT"]'
PUBLIC_URL=https://postchief.404builds.com
FRONTEND_URL=https://postchief.404builds.com
SECURE_COOKIES=true
```

Replace the callback placeholder. MCP defaults to disabled. An empty callback
list permits discovery but rejects linking. The server uses a predefined public
PKCE client and does not advertise DCR, CIMD, or issuer-response identification.
If a client cannot use that registration mode, revise the registration contract
while retaining authentication.

### Connect Codex alongside ChatGPT

In Windows PowerShell:

```powershell
codex mcp add post_chief --url https://postchief.404builds.com/mcp --oauth-client-id post-chief-chatgpt
```

Copy the displayed callback. Keep the existing cloud callback and **add** the
Codex callback to `MCP_REDIRECT_URIS`. Register a local Codex URI as
`http://127.0.0.1/callback/<exact-callback-id>` without a port. RFC 8252 matching
permits Codex's ephemeral listener port while requiring the literal host, path,
and query to match. Other HTTP hosts are rejected; an explicitly registered port
remains exact. Codes bind the complete request callback including its actual port.

After updating configuration and restarting the containers:

```powershell
codex mcp login post_chief
```

Complete owner sign-in/consent and refresh Codex's tools. Codex and the cloud
agent receive separate OAuth sessions; adding a callback does not revoke the
existing cloud grant. Verify the same read-only calls before making changes.

### MCP tools and token behavior

| Group | Tools |
|---|---|
| Identity | `get_identity`, `get_autonomy`, `list_connections` |
| Campaigns | `list_campaigns`, `get_campaign`, `get_publication`, `create_campaign`, `update_campaign`, `cancel_campaign` |
| Delivery | `schedule_campaign`, `publish_campaign`, `retry_publication` |
| Media | `list_assets`, `import_github_asset` |
| GitHub | `get_workspace`, `list_repositories`, `read_github_content`, `write_github_content` |
| Reporting | `get_analytics`, `refresh_analytics`, `list_reporting_accounts`, `get_account_report` |
| Audit | `list_approvals`, `list_audit_events`, `report_agent_run` |

These 25 tools reuse API validation and permissions. Edits need the current
revision; schedules need timezone offsets. GitHub media import needs
`media:write` and `github:read`; repository contents are data, not instructions.
Binary media is not embedded in JSON. Responses are capped at 1 MiB; narrow
reads or paginate large results.

`status=ok` means an API operation succeeded, not that a post published. Read
the destination with `get_publication`. `approval_required` retains the original
payload and approval ID. After `outcome_unknown` or an oversized mutation
response, inspect state before retrying: a write may already have completed.

Consent creates an Agent identity named `MCP: post-chief-chatgpt`. Revocation
immediately blocks its access and refresh tokens; reduced scopes affect existing
tokens. Expanding access requires new consent. Authorization codes last five
minutes and are single-use, access tokens last fifteen minutes, and rotating
refresh-token families last thirty days. Reusing a spent refresh token revokes
its family. Expiry/revocation requires renewed authorization. PostgreSQL stores
hashes/grants, not raw tokens; expired records are pruned in bounded batches and
authorization requests have size/rate limits.

### Existing REST agent keys

The owner creates a scoped key in **Agent** or `POST /api/agent/keys`. The
`pc_agent_…` token is displayed once; only its SHA-256 hash is stored. Use
`Authorization: Bearer …`, never URL parameters or GitHub content. Revoke with
`DELETE /api/agent/keys/{id}`. These keys support the REST contract and are
distinct from MCP's OAuth linking.

## Provider setup and capabilities

All OAuth provider callbacks below are appended to `PUBLIC_URL`. Configure app
credentials, exact redirects, scopes, review/access levels, and eligible native
accounts before using **Connect**. State is owner/organization-bound and
single-use; after a failed callback, correct setup and start a fresh connection.
Grant storage is encrypted. Reconnecting preserves account/publication history
and does not republish content. Renewal safety and provider-specific exceptions
are described below.

| Provider | Main capability | Callback suffix |
|---|---|---|
| Facebook / Instagram (`meta`) | Native publishing and post metrics | `/api/connections/oauth/meta/callback` |
| Threads | Native publishing and post metrics | `/api/connections/oauth/threads/callback` |
| X | Paid API publishing/metrics, or manual handoff | `/api/connections/oauth/x/callback` |
| LinkedIn | Member posts; approved company Pages | `/api/connections/oauth/linkedin/callback` |
| Pinterest | Image Pins on a selected public board | `/api/connections/oauth/pinterest/callback` |
| YouTube | Video upload and native reporting | `/api/connections/oauth/youtube/callback` |
| TikTok personal | Owner-reviewed inbox upload, then native completion | `/api/connections/oauth/tiktok/callback` |
| TikTok business | Account/profile reports only | `/api/connections/oauth/tiktok_business/callback/` (trailing slash) |
| Google Business Profile (`gbp`) | Standard local posts and location reports | `/api/connections/oauth/gbp/callback` |
| Website (`web`) | GA4 reports only | `/api/connections/oauth/web/callback` |
| Twitch | Channel/live/follower/video reports only | `/api/connections/oauth/twitch/callback` |

Bluesky uses handle/App Password authentication. Blog sources use validated
public RSS/Atom URLs. Reporting-only connections cannot be campaign destinations.
App acceptance, media suitability, and public publication are distinct outcomes.

### Facebook, Instagram, and Threads

**Setup.** Set `META_CLIENT_ID`, `META_CLIENT_SECRET`, and `META_API_VERSION`
for a Facebook Login app. Authorize Pages; Post Chief discovers linked
professional Instagram accounts. Consumer Instagram accounts are unsupported.
Both Facebook/Instagram connection buttons use this combined Meta flow.

Requested Meta scopes are `business_management`, `pages_show_list`,
`pages_read_engagement`, `pages_read_user_content`, `pages_manage_posts`,
`instagram_basic`, `instagram_content_publish`, and `instagram_manage_insights`.
Enable Instagram insights in Permissions and features. Add the Page content
management use case (**Manage everything on your Page**) and enable Page
publishing/reading permissions. Reactions/comments summaries need
`pages_read_user_content`. An `Invalid Scopes` error requires correcting app
configuration and starting fresh consent; refreshing the old URL cannot add
permissions. Register the public hostname under App Domains and exact callbacks;
update both if a temporary tunnel hostname changes. Access outside app roles
requires the applicable permission/review approval.

Threads uses the **Access the Threads API** use case and its own
`THREADS_CLIENT_ID` / `THREADS_CLIENT_SECRET`; Facebook Login credentials are not
interchangeable. Separate registration is recommended, but the same registration
can work if it provides the Threads use case and Threads-specific credentials.
Request `threads_basic`, `threads_content_publish`, and
`threads_manage_insights`, using the Threads callback.

OAuth state expires after ten minutes and is consumed atomically before code
exchange. Threads diagnostics distinguish code exchange, long-lived exchange,
and profile lookup, using safe numeric codes. `AUTH_REVOKED` does not prove the
owner deliberately revoked access. Meta configuration errors without state never
trigger exchange or create a connection. Threads' long-lived exchange uses the
short token as an `access_token` query parameter: keep those URLs out of logs.
Unexpired long-lived Threads grants can refresh; revoked/expired grants require
owner reconnection.

**Media and recovery.** Facebook supports text, raster photos, and MP4 Reels;
Instagram supports JPEG, MP4 Reels, and carousels up to ten items; Threads supports
text, images, video, and carousels up to twenty items. Application media size is
capped at 80 MiB. Instagram, Threads, and hosted Facebook Reels require public
HTTPS media. Platform format, dimensions, duration, and permissions still apply.
Checkpoints and `publish_intent` are persisted; ambiguous public writes require
reconciliation. Facebook Reels wait for publishing completion; Instagram/Threads
reuse stored containers and poll processing rather than recreating them.

**Published text.** **Edit Facebook text** loads native feed text and produces a
specific before/after review. Reviews last ten minutes, bind owner/publication,
are single-use, and reject changed native text. Media remains intact and edit
history is recorded separately for feedback. An uncertain edit must be checked
against current native text before another edit. Facebook has no conditional
text-update API, so another native edit between final read/update can still
conflict. Instagram uses **Edit on Instagram**; native caption updates are not
exposed through this adapter. Other native edit formats/media replacement are
not implemented, and this flow never deletes/recreates posts.

References: Meta [Facebook](https://www.postman.com/meta/facebook/documentation/r56bjfd/facebook-api),
[Instagram](https://www.postman.com/meta/instagram/documentation/6yqw8pt/instagram-api),
and [Threads](https://www.postman.com/meta/threads/documentation/dht3nzz/threads-api)
collections; [Threads sample](https://github.com/fbsamples/threads_api/blob/main/src/index.js);
[Facebook Post SDK](https://github.com/facebook/facebook-python-business-sdk/blob/main/facebook_business/adobjects/post.py)
and [Instagram Media SDK](https://github.com/facebook/facebook-python-business-sdk/blob/main/facebook_business/adobjects/igmedia.py).

### X

**Manual handoff.** Open **X · manual handoff** under a saved campaign's Delivery
panel, copy its platform-specific caption, download selected JPEG/PNG/WebP assets,
post in X, and record the native URL/time. No X credentials or paid API reads are
needed. This is an owner-reported result, with no worker destination or invented
metrics. A campaign cannot combine direct X delivery and manual handoff.

`POST /api/campaigns/{id}/external/x` requires owner/CSRF authentication, current
revision, actual `body` / `asset_ids`, an HTTPS X/Twitter status URL,
timezone-aware `published_at`, and `confirmed_published: true`. URLs normalize to
`x.com` without tracking parameters; no external URL is fetched. Future times and
unsupported/cross-organization assets are rejected. One immutable manual result
is retained per campaign; identical submissions are idempotent. Deletion is
blocked while that record exists, and referenced media is retained. Results flow
into reviewed feedback as owner-reported, with unavailable native counters.
This path has no unattended schedule, native edit, automatic analytics, or
X-only campaign creation; use a normal campaign for connected core networks.

**Direct API.** Set confidential Web App OAuth 2.0 `X_CLIENT_ID` /
`X_CLIENT_SECRET`, not API key/secret or an app-only token. Enable user auth and
register the callback plus dashboard website. Scopes are `tweet.read tweet.write
users.read media.write offline.access`. Owner-bound state lasts ten minutes;
S256 PKCE uses a server-keyed verifier, and exchange uses HTTP Basic client auth.
Rotating grants are persisted under an encrypted durable account intent; concurrent
jobs wait and unconfirmed renewal requires reconnection rather than reuse.

Requests use paid credits. Post Chief does not buy credits or change billing;
review the provider's spending settings before live use. Metrics refresh is
explicit: success/non-transient failure stops further reads until requested again;
transient errors retain the normal retry deadline.

Text and up to four JPEG/PNG/WebP images, each at most 5 MB, are supported. The
weighted text bound is conservatively 280; long URLs/complex emoji can be
overcounted. Video, GIF, long posts, replies, quote posts, native edits, and
image alt-text metadata are outside this adapter. Upload IDs/expiries are saved
before public intent; expired media requires review. Ambiguous post creation
requires checking X before retry. Success retains numeric ID and public URL.

References: [PKCE](https://docs.x.com/fundamentals/authentication/oauth-2-0/user-access-token),
[post creation](https://docs.x.com/x-api/posts/create-post),
[media upload](https://docs.x.com/x-api/media/upload-media),
[post lookup](https://docs.x.com/x-api/posts/get-post-by-id), and
[pricing](https://docs.x.com/x-api/getting-started/pricing).

### Bluesky

Connect with a handle and App Password. Post Chief stores encrypted session and
refresh tokens, not the password. Allowed PDS hosts are `bsky.social` and
subdomains of `bsky.network`; other federated hosts need a future explicit policy.

Text allows 300 grapheme clusters and 3,000 UTF-8 bytes; link facets use UTF-8
byte offsets. Up to four raster images, each at most 2,000,000 bytes, are supported.
MP4 uploads use a PDS-scoped service token and persist video-processing jobs
instead of reuploading; the application video cap is 80 MiB. A stable TID record
key derived from publication ID allows reconciliation. Compare-and-swap creation
preserves existing records; `createdAt` uses actual publication time. Session
refresh saves encrypted grants through the publication worker.

Native engagement metrics retain their meanings; unsupported impressions and
missing counters stay unavailable. Comments/replies are not advertised as
publishing capabilities. Live account acceptance remains separate.

References: [post lexicon](https://github.com/bluesky-social/atproto/blob/main/lexicons/app/bsky/feed/post.json),
[image lexicon](https://github.com/bluesky-social/atproto/blob/main/lexicons/app/bsky/embed/images.json),
[video processing](https://github.com/bluesky-social/atproto/blob/main/lexicons/app/bsky/video/defs.json),
[record keys](https://atproto.com/specs/record-key), and
[putRecord](https://github.com/bluesky-social/atproto/blob/main/lexicons/com/atproto/repo/putRecord.json).

### LinkedIn

Set `LINKEDIN_CLIENT_ID`, `LINKEDIN_CLIENT_SECRET`, and `LINKEDIN_API_VERSION`
(configured default `202609`). Enable OpenID Connect and Share on LinkedIn.
Default `LINKEDIN_SCOPES` is `openid profile w_member_social`; email is unnecessary.
Identity comes from authenticated userinfo, not an unverified ID token. Tokens,
optional programmatic refresh grants, and resumable-upload credentials are encrypted.

Supports text, JPEG/PNG/GIF, organic multi-image posts up to twenty images, and
multipart MP4 video, with the 80 MiB application media cap. `x-restli-id` becomes
the publication URN. Persisted public intent protects recovery; uncertain writes
require reconciliation. `w_member_social` alone cannot GET versioned image status;
upload checkpoints provide processing time, and provider acceptance still applies.

Company Pages need approved organization products/scopes, including permitted
`w_organization_social`, organization-read/admin permissions, and
`r_organization_social` in `LINKEDIN_SCOPES`, followed by reconnection. Use
`/api/connections/linkedin/{member-account-id}/organizations` to list/POST an
eligible Page. The member's native publishing role is verified; arbitrary URNs
are rejected. Member metrics separately need `r_member_postAnalytics`; company
metrics need `r_organization_social`. Missing access is a permission error, not
zero metrics. Automatic renewal is possible only when a refresh token was issued.

References: [Posts](https://learn.microsoft.com/en-us/linkedin/marketing/community-management/shares/posts-api),
[Images](https://learn.microsoft.com/en-us/linkedin/marketing/community-management/shares/images-api),
[Videos](https://learn.microsoft.com/en-us/linkedin/marketing/community-management/shares/videos-api), and
[member analytics](https://learn.microsoft.com/en-us/linkedin/marketing/community-management/members/post-statistics).

### Pinterest

Set `PINTEREST_CLIENT_ID` / `PINTEREST_CLIENT_SECRET`. Consent requests
`boards:read,pins:read,pins:write,user_accounts:read`. Continuous rotating refresh
grants share durable renewal intent across publishing, analytics, and board
discovery; uncertain renewal requires reconnecting.

Choose an accessible public board in Connections. Discovery paginates. Switching
an already selected board is blocked with pending drafts/deliveries; an initially
missing board can be configured to recover setup. Reconnecting requires selecting
the board again. Each publication retains its board identity in encrypted intent.

One JPEG/PNG up to 10 MB is supported, with an 800-character description and
500-character asset alt text. Images use Base64. Text-only Pins, videos,
carousels, board creation, custom titles/links, and native edits are not supported.
Pin impressions/outbound clicks use a 30-day UTC window; Pin clicks/saves retain
native semantics, and missing counters remain unavailable.

References: [OpenAPI](https://github.com/pinterest/api-description) and
[authorization](https://developers.pinterest.com/docs/getting-started/set-up-authentication-and-authorization/).

### YouTube

Use confidential Google Web application `GOOGLE_CLIENT_ID` / `GOOGLE_CLIENT_SECRET`.
Enable YouTube Data API v3 and Analytics API; register the callback/final origin
and add intended test users while consent is in testing. Select the intended
channel/Brand Account. Discovery requires one existing channel from
`channels.list(mine=true)` and stores its stable ID instead of guessing among
channels. Offline grants are encrypted and renewed under a durable account lock.

Scopes are `https://www.googleapis.com/auth/youtube.upload`,
`https://www.googleapis.com/auth/youtube.readonly`, and
`https://www.googleapis.com/auth/yt-analytics.readonly`.

Use one MP4 up to 80 MiB. Provider overrides support a video title (campaign
title fallback, max 100 characters), visibility (default private), and explicit
made-for-kids declaration; unset audience blocks delivery. Descriptions allow
5,000 UTF-8 bytes; title/description reject angle brackets. Paused destinations
expose the same controls. Category is People & Blogs (22); custom categories,
tags, thumbnails, playlists, live streams, and native edits are outside this path.

Fixed-origin encrypted resumable sessions are saved before bytes are uploaded.
The worker checks accepted offsets, bounds chunks, verifies the video digest,
saves accepted video ID, and polls processing. Lost chunk/final responses resume
that session; expired sessions or ambiguous acceptance require reconciliation,
not a new upload. Known accepted IDs block a not-published reset. Unverified
Google projects may force private visibility: requested public/unlisted versus
actual private is a review outcome, not successful public delivery. Session URLs
and tokens are excluded from browser/audit/feedback data.

Data API views/likes/comments are lifetime counters. Analytics shares/watch time
use a separate 30-day Pacific reporting window and may lag. Watch time normalizes
to seconds while raw minutes/window remain available; a missing Analytics report
does not discard valid lifetime metrics.

References: [resumable uploads](https://developers.google.com/youtube/v3/guides/using_resumable_upload_protocol),
[insert restrictions](https://developers.google.com/youtube/v3/docs/videos/insert),
and [reporting dates](https://developers.google.com/youtube/analytics/dimensions#time-periods).

### TikTok personal

Set `TIKTOK_CLIENT_KEY` / `TIKTOK_CLIENT_SECRET`; enable Login Kit and the
Content Posting API upload product. Scopes: `user.info.basic`,
`user.info.profile`, `video.list`, and `video.upload`. Rotating refresh grants
are encrypted and serialized. This is separate from business/account and ads
authorization.

The implemented path is **Upload to TikTok inbox**. Review current creator/video
preview, explicitly consent, and initiate from an owner session. Agents may
prepare drafts/read results but cannot initiate inbox transfers. Supports one
MP4 up to 80 MiB and ten minutes; TikTok may impose additional codec, dimensions,
frame-rate, or account limits and native trimming.

`PULL_FROM_URL` uses two-hour signed media access. Verify the final HTTPS media
domain or `/api/media/` prefix in the TikTok app. Caption copy is shown for native
use; the upload API does not apply it. Finish caption, visibility, interactions,
disclosures, and posting in TikTok. Direct Post needs separate export controls/
client audit, including private/internal utility restrictions, and is not claimed.

The worker saves initialization intent and task ID before status polling. An
uncertain initialization requires reconciliation. `SEND_TO_USER_INBOX` becomes
`awaiting_owner`, without automatic repost/continuous polling. After native
completion, choose **Refresh TikTok completion** to check the same task.
Only `PUBLISH_COMPLETE` establishes a published outcome. Missing public IDs
retain task identity with visibility/counters unavailable. Display API video
queries supply native public views/likes/comments/shares; private/unavailable
video metrics stay absent. No public post is inferred from an inbox notification.

References: [Login Kit](https://developers.tiktok.com/doc/login-kit-web),
[token management](https://developers.tiktok.com/doc/oauth-user-access-token-management),
[inbox upload](https://developers.tiktok.com/doc/content-posting-api-reference-upload-video),
[media transfer](https://developers.tiktok.com/doc/content-posting-api-media-transfer-guide),
[status polling](https://developers.tiktok.com/doc/content-posting-api-reference-get-video-status),
[video queries](https://developers.tiktok.com/doc/tiktok-api-v2-video-query), and
[sharing requirements](https://developers.tiktok.com/doc/content-sharing-guidelines).

### TikTok business

This reporting-only connection uses the TikTok for Business Accounts API, not
personal Login Kit or advertiser authorization. Set `TIKTOK_BUSINESS_CLIENT_ID`,
`TIKTOK_BUSINESS_CLIENT_SECRET`, and `TIKTOK_BUSINESS_AUTHORIZATION_URL`. Register
the callback **with its trailing slash**, then copy the app-generated account
holder authorization URL for that redirect. Its HTTPS endpoint/client/callback
must match configuration. Post Chief adds single-use owner-bound state and does
not bypass consent.

Enable Accounts access with `user.info.basic`, `user.info.stats`, `user.insights`,
and `video.list`. The token inspector verifies granted permissions, app, and
account identity. Rotating grants are encrypted; interrupted/unconfirmed renewal
requires reconnection.

Reports separate lifetime follower/following/likes/public-video counts from
profile metrics over the last seven completed UTC dates. Enable native account
analytics; data may lag 24–48 hours and views can include organic/paid activity.
Recent posts are bounded to five pages of twenty, with captions up to 500
characters (shortening is explicit), IDs, counters, and returned media/ad flags.
Post counters are lifetime; some posts may be ads-only. Missing values, pagination,
and partial failures are explicit. This connection does not publish, moderate
comments, download media, or provide advertiser reports.

References: [Accounts authorization](https://business-api.tiktok.com/portal/docs/accounts-api-authorization/v1.3),
[token exchange](https://business-api.tiktok.com/portal/docs/obtain-a-short-term-access-token/v1.3),
[renewal](https://business-api.tiktok.com/portal/docs/renew-a-short-term-access-token/v1.3),
[profile insights](https://business-api.tiktok.com/portal/docs/get-profile-data-of-a-tiktok-account/v1.3), and
[post insights](https://business-api.tiktok.com/portal/docs/get-post-data-of-a-tiktok-account/v1.3).

### Google Business Profile

Provider `gbp` reuses the Google confidential Web client and requests
`https://www.googleapis.com/auth/business.manage`. Obtain Business Profile project
access; enable Account Management, Business Information, Performance, and Google
My Business v4 for local posts. Native ownership/management, verification, project
approval, and content eligibility are separate prerequisites.

Discovery uses fixed endpoints and required field masks, bounded to twenty
accounts and 100 unique locations. Excess discovery fails explicitly. Select the
intended displayed `accounts/{accountId}/locations/{locationId}`; reconnect to
discover newly granted locations. Renewal uses encrypted durable account intent.

Supports `STANDARD` English (`en-US`) local posts of 1–1,500 characters and at
most one JPEG/PNG, 10 KiB–5 MiB and at least 250 × 250 pixels, fetched via signed
HTTPS media. Events, offers, CTA configuration, native edits/deletion, and
business profile edits are outside this path. Creation intent and accepted full
resource name are saved before status reads: `PROCESSING` is pending, only `LIVE`
is published. Rejections/unknown outcomes retain accepted identity for review.
Retries do not recreate accepted resources. Reconciliation validates the connected
location and blocks a not-published reset once acceptance is known. Search URLs
must use fixed Google HTTPS hosts.

Post insights request Search views/CTA clicks over 30 UTC timestamp days, with
provider timezone/lag retained. Location reports keep seven native daily metrics
over 30 complete dates: desktop/mobile Search/Maps impressions, direction
requests, call clicks, and website clicks. Dates are Google location reporting
dates; do not invent a UTC location timezone. Surface impressions are unique per
day/surface and cannot be summed into unique reach. An omitted `DatedValue` value
is native zero; a missing date/metric stays unavailable.

References: [locations](https://developers.google.com/my-business/reference/businessinformation/rest/v1/accounts.locations/list),
[local posts](https://developers.google.com/my-business/reference/rest/v4/accounts.locations.localPosts),
[post insights](https://developers.google.com/my-business/reference/rest/v4/accounts.locations.localPosts/reportInsights),
[daily performance](https://developers.google.com/my-business/reference/performance/rest/v1/locations/fetchMultiDailyMetricsTimeSeries),
[daily value semantics](https://developers.google.com/my-business/reference/performance/rest/v1/TimeSeries), and
[photo requirements](https://support.google.com/business/answer/6103862).

### Website analytics and blog sources

**GA4 (`web`).** Reuse the Google Web client, enable Analytics Admin/Data APIs,
and request only `https://www.googleapis.com/auth/analytics.readonly`. Discovery
is bounded to 100 accessible properties over five account-summary pages. Each
property is reporting-only. Tracking must already be installed; Post Chief does
not install tags or change website/property content/settings.

Reports freeze a 30-complete-day window in the property's IANA timezone, keeping
separate totals, daily activity (30 rows), and session channels (25 rows). Metrics
are active users, sessions, and page/screen views. Daily/channel users cannot be
summed into unique window users. Missing rows, thresholds, truncation/data loss,
and native lag remain explicit. Properties containing app streams preserve
page/screen meaning rather than labeling all activity website-only.

**RSS/Atom (`blog`).** In Connections, enter a website to discover at most ten
advertised feeds, or connect a direct feed URL. Connecting validates the chosen
RSS 2.0/Atom feed. Saved snapshots contain at most 100 entries with bounded
plain-text titles/summaries, native date text, and entry IDs. Feed contents are
untrusted external context, never agent instructions or authorization. Article
pages, remote images, and attachments are not downloaded; this is not blog publishing.

Fetches require HTTPS/default port, no userinfo/fragments, public DNS results,
and an available IPv4 address. Connections pin the checked public IP while
preserving Host/TLS hostname; environment proxies are disabled. At most three
redirects are explicitly revalidated. Documents are capped at 1 MiB with a
30-second total deadline, uncompressed responses, no XML DTD/entities/UTF-16,
and UTF-8 HTML discovery. IPv6-only/private/authenticated feeds and arbitrary
downloads are unsupported. Suggested feeds do not connect themselves.

Owner/CSRF endpoints `POST /api/connections/blog/discover` and
`POST /api/connections/blog` accept `{url}`. Saved reads/refreshes use shared
account reports; a connection nonce prevents changed grants/source configuration
from saving stale collection results. Failures retain the last good snapshot.

References: [GA4 account summaries](https://developers.google.com/analytics/devguides/config/admin/v1/rest/v1beta/accountSummaries/list)
and [Data API reports](https://developers.google.com/analytics/devguides/reporting/data/v1/rest/v1beta/properties/runReport).

### Twitch

Set confidential `TWITCH_CLIENT_ID` / `TWITCH_CLIENT_SECRET`; request
`moderator:read:followers`. Exchange and renewal validate client, user identity,
and granted scope against Twitch's fixed validation endpoint. Tokens are
encrypted. No email, stream-key, edit, chat, subscription, or broadcast-write
permission is requested.

Reporting-only snapshots include profile/channel context, actual follower total,
current concurrent viewers when live, and at most twenty recent videos with
lifetime views. Offline does not imply zero historical audience. Optional
follower/video failures remain visible while valid channel/live context is kept.
Individual followers, email addresses, media, and remote thumbnails are not
stored/downloaded. Historical broadcaster audience, game/extension analytics,
and feed publishing are outside this connector.

Worker startup enqueues grant validation; the scheduler checks due grants each
minute and validates maintained grants every fifty minutes. Invalid auth/scope
disconnects only the matching grant, preserving concurrent reconnections. Temporary
failures retain the connection with an audit diagnostic. Every report collection
also validates its token. Keep worker and scheduler running for maintained grants.

References: [grants](https://dev.twitch.tv/docs/authentication/getting-tokens-oauth/),
[validation](https://dev.twitch.tv/docs/authentication/validate-tokens/),
[refresh](https://dev.twitch.tv/docs/authentication/refresh-tokens/), and
[Helix API](https://dev.twitch.tv/docs/api/reference).

## Analytics and saved reports

### Publication analytics

The scheduler scans published destinations each minute. Collection has its own
database lease/deadline. Successful snapshots are immutable and normally repeat
hourly. X is the exception: explicit refresh only, to control paid reads. Safe
transient failures retry after ten minutes; other providers' permission/auth
failures remain visible and retry after one day. Refresh requests record due work
in PostgreSQL even when Redis is unavailable.

- `GET /api/analytics`: paginated latest snapshots, account/campaign/provider
  context, errors, and next collection time.
- `GET /api/analytics/publications/{id}`: publication history.
- `POST /api/analytics/publications/{id}/refresh`: request collection.

Reads require `analytics:read`; collection requires `analytics:collect` and the
applicable autonomy policy. Dashboard Analytics shows snapshots, timestamps,
errors, refresh state, and provider-specific/history data.

Native metric meanings are retained. A native zero is zero; a missing value is
unavailable. Reactions differ from likes; reach, views, and impressions differ.
Bluesky replies map to comments and reposts to shares with their meaning attached.
Do not sum unlike provider metrics. Manual X reports remain owner-reported and
have no native metrics or paid refresh jobs.

### Account reports

Reporting-only connections use organization-scoped saved snapshots:

| Endpoint | Scope and behavior |
|---|---|
| `GET /api/reports/accounts` | `analytics:read`; discover reporting connections. |
| `GET /api/reports/accounts/{id}` | Latest saved report. |
| `GET /api/reports/accounts/{id}/history` | Saved history with `limit` / `offset`. |
| `GET /api/reports/accounts/{id}/export` | Authenticated JSON export. |
| `POST /api/reports/accounts/{id}/refresh` | `analytics:collect`; one-minute account cooldown and autonomy checks. |

Managed REST action `report.refresh` uses the connection ID as target and follows
approval policy. It is not currently exposed as a dedicated MCP refresh tool.
Snapshots, request intents, and safe errors are persisted as audit records. Failed
collection retains the last successful snapshot; results are rejected if the
connection changes during collection. Schema v1 includes `normalized`,
`semantics`, optional `window`, `notice`, `context`, `coverage`, and bounded
named `tables`. Export excludes credentials/signed URLs and preserves coverage
and collection meaning. Reports never create publication records or automatically
write GitHub. Reporting-only accounts are excluded from destinations by UI and API.

## Advertising reports

Meta Ads, Google Ads, and TikTok Ads are separate reporting-only connections.
Discover the intended account in Connections, then collect snapshots in Analytics.
These adapters never create ads, adjust budgets/campaigns, or start spending.

Reports cover the latest thirty completed **account-local** dates with explicit
currency/timezone. Native integer/decimal values remain strings for precision.
Missing counters, censored values, partial coverage, attribution differences, and
later native revisions are not converted into invented totals. No conversion
attribution, cross-currency calculations, or sums of unique audiences are inferred.

| Service | Configuration, consent, and collection |
|---|---|
| Google Ads | Reuse Google client plus `GOOGLE_ADS_DEVELOPER_TOKEN`; enable Ads API with intended access level. Callback `/api/connections/oauth/google_ads/callback`; scope `https://www.googleapis.com/auth/adwords`. Encrypted offline grants. Fixed `listAccessibleCustomers` / `googleAds:search` queries against v25; no arbitrary GAQL. Discovery bounds: 100 directly accessible customers, 100 enabled non-manager clients per manager, 100 unique connected clients. Verified manager login ID retained. Campaign totals cap at 1,000 rows with truncation. Native `cost_micros` remains integer account-currency micros; managers are auth contexts, not reportable clients. |
| Meta Ads | Reuse Meta client; callback `/api/connections/oauth/meta_ads/callback`, only `ads_read`, verified through granted permissions. Fixed Graph GET account identity/currency/timezone and synchronous insights. Discovery max 100 accounts; campaign insights max 1,000 rows. Fixed-origin pagination never follows token-bearing provider URLs. Uses a long-lived user grant, not the organic Page token; reconnect at recorded expiry/revocation. Clicks mean all-ad clicks, not link clicks; no conversion/actions attribution requested. |
| TikTok Ads | Separate `TIKTOK_ADS_APP_ID`, `TIKTOK_ADS_SECRET`, and app-generated `TIKTOK_ADS_AUTHORIZATION_URL`; exact callback `/api/connections/oauth/tiktok_ads/callback`. Request advertiser information/reporting only. Owner-bound state injected; this is distinct from personal/Accounts authorization. Long-term encrypted access token; deprecated advertiser refresh endpoint is not used. Authorized advertiser discovery max 100. Re-read identity/currency/IANA timezone; BASIC AUCTION campaign/day reports bounded to ten 100-row pages with continuation explicit. Reservation/specialized products are excluded. Censored `<5` stays text. |

For live acceptance, compare the intended account's known native report over the
same dates, currency, timezone, and permission level; inspect saved history/export
and failures, then verify disconnection/revocation. Implementing the adapter does
not authorize live advertising requests or spending.

References: [Google headers](https://developers.google.com/google-ads/api/rest/auth),
[Google search](https://developers.google.com/google-ads/api/rest/common/search),
[Meta SDK](https://github.com/facebook/facebook-python-business-sdk),
[TikTok advertiser authorization](https://business-api.tiktok.com/portal/docs?id=1738373141733378),
and [TikTok SDK](https://github.com/tiktok/tiktok-business-api-sdk). Current TikTok
portal contracts take precedence over older generated refresh-token prose.

## Looker Studio

The private Apps Script Community Connector is in
[`root/src/apps/looker-studio/`](../root/src/apps/looker-studio). The authenticated
API contract is `/api/reports/looker/v1/schema`, `/sources`, and `/data`, using owner
auth or a separate revocable agent key with only `analytics:read`. It reads saved
snapshots; it does not refresh providers or expose their credentials.

1. Use a stable HTTPS origin reachable from Google Apps Script.
2. Create an Apps Script project with the supplied `Code.js` and
   `appsscript.json`. Set the manifest logo to an actual accessible brand image.
3. Set Script Property `POST_CHIEF_BASE_URL` to the exact HTTPS origin without
   path/trailing slash. Users cannot redirect credentials via data-source settings;
   HTTP redirects are disabled.
4. Create a private Post Chief key with only `analytics:read`. Enter it through
   **KEY authentication**, not ordinary config/report URLs. It is stored in
   per-user properties, not shared Script Properties.
5. Deploy a private Community Connector; use Google's deployment ID in Looker,
   grant external-request scope, select one saved source/table, and choose dates.
6. Check isolation, values/timezones, freshness, filtering, and revoked-key
   behavior. Report/data-source sharing may expose cached data; review Looker's
   credential/sharing choices. Revoking a key blocks future reads, not previously
   extracted copies, which require separate removal.

Schema v1 is a long-form metric table: provider/account, snapshot/time, source
table/entity, reporting period/timezone/currency, native metric/raw value, chart
number, unit/meaning, status, coverage, and notice. Sources include advertising
tables, GA4 window totals, and normalized account metrics. Arbitrary blog text
and other non-ad provider tables are not converted into metrics; JSON exports
retain full reports.

Choose one source table per data source. Daily values and campaign window totals
are separate. Date filters include only entire reporting periods; no proration.
Lifetime/current counters use UTC collection dates, not historical engagement
changes. Sources use the latest successful snapshot, including the last good
report after an error, with its collection timestamp. Refresh native reports in
Post Chief explicitly.

Missing values yield no metric row. Censored values retain text and null chart
number. Values exceeding the safe fifteen-significant-digit/JavaScript integer
bound use `precision_limited` and no chart number. Google micros remain exact in
`metric_raw`; chart numbers convert to account-currency units using ordinary
IEEE754 precision. Aggregation defaults to NONE; filter metric/unit/currency/
entity/grain before choosing an intentional aggregation. Coverage/truncation
remain attached; no cross-currency/attribution inference.

The API caps data at 4,000 rows from one bounded snapshot. Source discovery pages
by 100 accounts; the connector supports up to 1,000 reporting accounts. Larger
organizations can use the authenticated API. Disconnecting a provider stops
collection while retaining saved snapshots. Connector deployment remains an
owner action; this is not a social connection or native Looker data-push API.

References: [authentication](https://developers.google.com/looker-studio/connector/auth),
[Community Connector API](https://developers.google.com/looker-studio/connector/reference),
and [connector deployment](https://developers.google.com/looker-studio/connector/build).

## Development and CI

### Layout and local startup

| Path | Contents |
|---|---|
| `root/` | Runtime, manifests, Compose files, migrations, and dependencies. |
| `root/src/apps/api/postchief/` | FastAPI, SQLAlchemy models, auth, and provider/API operations. |
| `root/src/apps/worker/` | Celery publication/analytics jobs. |
| `root/src/apps/web/` | React 19, TypeScript, Vite, TanStack Query, and shared API client. |
| `root/src/packages/` | Provider contracts, typed client, and shared types. |
| `root/mcp/postchief_mcp/` | Official SDK transport, OAuth, identity bridge, and tools. |
| `root/migrations/` | Alembic schema changes. |
| Sibling `.Post Chief/.codex/` | Planning, execution notes, and support artifacts outside the runtime repository. |

With Python 3.12+ and Node dependencies installed, run from `root/`:

```bash
python -m venv .venv
# Activate .venv using your shell's activation command.
python -m pip install -r requirements.lock
python -m pip install --no-deps -e .
npm ci
# Copy .env.example to .env and configure unique keys/database credentials.
alembic upgrade head
uvicorn postchief.main:create_app --factory --reload
```

Run `npm run dev` in another terminal. Vite proxies `/api`; the owner development
frontend defaults to port 5173. PostgreSQL is the production database; SQLite
supports isolated tests. Alembic owns migrations. API references:
[GitHub Apps](https://docs.github.com/en/rest/apps/apps) and
[SQLAlchemy mapping](https://docs.sqlalchemy.org/en/20/orm/declarative_tables.html).

### Checks and test isolation

When verification is requested, backend/frontend commands are:

```bash
python -m pytest -q
npm run typecheck
npm run build
npm test
```

HTTP endpoint tests use synchronous `fastapi.testclient.TestClient`; tests are
async when awaiting provider/worker internals. Pytest enforces strict config/
markers, function-scoped async loops, and warnings-as-errors. The pinned
Starlette 1.7.0 compatibility exception is only the exact
``Using `httpx` with `starlette.testclient` is deprecated; install `httpx2` instead.``
message, `StarletteDeprecationWarning`, originating through `fastapi.testclient`.
Other warnings and the same warning from application code remain errors; avoid
broad deprecation filters and revisit this exception with dependency updates.

Temporary state defaults to ignored `root/.pytest-tmp`; container checks can use
`/app/data/pytest`. If old Windows cache/temp ACLs conflict, use a fresh ignored
runtime directory and disable pytest's cache, without weakening ACLs:

```bash
python -m pytest -q -p no:cacheprovider --basetemp=data/pytest-fresh-run
```

Use a new directory name for each affected run. PostgreSQL fixtures use a
disposable `POST_CHIEF_TEST_DATABASE_URL`, create one random schema per fixture,
and remove only that schema. Never supply the live application's database as a
test database or print credential-bearing URLs. MCP tests are
`root/tests/test_mcp_*.py`; store concurrency requires PostgreSQL. Provider fixtures
and isolated image checks never publish to real accounts.

Celery integration uses TCP Redis with unique queues/key prefixes, not AF_UNIX
sockets or user-profile Docker socket cleanup. Any future socket resource must
live under test temp storage, reject links/reparse points, and remove only the
exact resource owned by the test. Frontend Vitest uses at most two workers with
the ordinary five-second per-test timeout.

### Recovery rehearsal

`root/tests/test_recovery.py` runs on a separate disposable PostgreSQL 17 server.
Set `POST_CHIEF_RECOVERY_DATABASE_URL` to its admin database and
`POST_CHIEF_RECOVERY_CONTAINER` to that same container; the user needs permission
to create databases and run local `pg_dump`/`pg_restore`. On Windows use
`127.0.0.1` to avoid slow localhost fallback. Without either opt-in variable,
the rehearsal skips; it does not default to production configuration.

```bash
python -m pytest -q tests/test_recovery.py
```

It creates two random databases, migrates/seeds synthetic data, dumps/restores
PostgreSQL and media, then checks counts, schema/head, owner auth, sessions,
signed media, private downloads, native zero metrics, and encrypted grants/
checkpoints. Missing media/wrong keys are rejected. No scheduler or provider runs;
only newly created databases are removed. Synthetic success does not prove that
production has an encrypted off-host backup or that restored dispatch is safe.

### CI workflow

**Application checks** runs on push, pull request, or manual dispatch with
read-only permissions, immutable Action pins, locked dependencies, bounded job
timeouts, and cancellation of obsolete concurrent runs.

- **API SQLite/PostgreSQL:** auth/OAuth, provider transport, API and scheduling
  coverage; dependency consistency; migration upgrade, drift, downgrade, upgrade.
  Downgrades use disposable CI data only; JUnit results are retained seven days.
- **Frontend:** clean npm installation, types, production bundle, dashboard
  interactions, typed-client CSRF/error checks; an empty test suite fails.
- **Container smoke:** unique ephemeral secrets, image build, isolated database/
  Redis/API/worker/scheduler startup, migrations, DB health, worker ping, and
  served dashboard; disposable CI containers/volumes are removed afterward.

Configure required branch checks in GitHub when appropriate. A workflow file
does not prove a successful remote run; report actual CI URL/status. Neither mocks
nor queue delivery establish live provider acceptance.

## Troubleshooting

| Symptom | Next step |
|---|---|
| API resets immediately after startup | Retry health, inspect `dc ps` and API logs. Readiness can lag container start. |
| `/mcp` returns 404 | Check the deployed image and `MCP_ENABLED`, then restart API. The endpoint is authenticated transport, not a dashboard page. |
| MCP requires authentication | Configure the predefined client, register its exact callback, and complete OAuth. A saved server entry alone is insufficient. |
| Codex redirect is unregistered | Keep the cloud URI and add the exact Codex loopback path without a port; deploy callback support before retrying login. |
| ChatGPT connects but has no tools | Refresh the plugin, select it in the intended chat, then verify a read-only call. |
| Agent receives approval_required / 428 | Owner reviews in the dashboard; resubmit identical inputs plus approval ID. Never self-approve. |
| Mutation timed out or returned outcome_unknown | Read current campaign/publication/repository state before another write. |
| Provider consent fails / Invalid Scopes | Check credentials, use case, granted permission/access level, origin, and callback; begin fresh consent. |
| Provider token expired or renewal is uncertain | Reconnect; do not blindly reuse possibly rotated grants. |
| Publication is RECONCILE | Inspect native provider, then use the owner reconciliation flow. |
| GitHub preview/write is stale | Refresh preview and review current branch/campaign revision; do not overwrite another editor. |
| Binary dump appears corrupt on Windows | Use database-container pg_dump plus Docker copy; avoid PowerShell binary pipelines. |
| chmod fails on a copied dump | Docker may own it as root; apply `sudo chmod 600` to the specific backup. |
| Worker/scheduler runs in both locations | Keep only the active deployment processing jobs, with exactly one scheduler. |

## Verification and limitations

Facebook, Instagram, and Threads publishing/analytics/reviewed GitHub are acceped. 
Additional provider connections and native acceptance remain individual owner actions; 
do not infer them from a connection card or local test. Historical planning does 
not expand current capabilities, and retiring another service is a separate decision 
from local implementation success.

The current release is a self-hosted owner workspace. Public SaaS signup,
customer onboarding, an administrator console for multiple businesses, billing,
and hosted multi-agent orchestration are not implemented. Public comments/DMs,
ad management/spending, and published-post deletion are outside the exposed
publishing workflow. Provider-specific restrictions above remain applicable.

Before accepting a new provider, verify configured secrets and exact redirects,
app approval/scopes, intended account identity, reviewed native content/media,
durable outcome/permalink, metric meaning, and revocation. A dispatch receipt,
upload acceptance, inbox handoff, or private-video fallback is not successful
public publication. Do not publish duplicate filler content to prove a check.
