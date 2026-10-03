# Post Chief Production Hosting Design

**Status:** Proposed for owner review  
**Date:** 2026-10-03  
**Repository:** `MrCodeGameandAnime/Post-Chief`

## Goal

Make Post Chief available when the owner's laptop is closed, expose it at `https://app.404builds.com`, and enable a connected GPT/Codex operator to use its existing agent API for the 404 Builds publishing workflow. Preserve the existing homepage during the first cutover and make the homepage's Post Chief link open the application directly.

The initial deployment is for 404 Builds' own channels. It is not a public multi-tenant SaaS launch.

## Existing application shape

The current app already has a deployable Compose stack:

- React/Vite dashboard bundled and served by the FastAPI API container.
- PostgreSQL for durable application state.
- Redis for immediate job delivery.
- Celery worker for publishing and analytics work.
- One Celery Beat scheduler.
- Media and database state persisted in Compose volumes.

The API publishes only on host loopback port 8000. PostgreSQL and Redis are private Compose services. Operations documentation requires exactly one scheduler and an HTTPS reverse proxy configured to trust only the actual proxy path. See [Compose](../../../root/compose.yaml), [operations](../../operations.md), and [stack](../../stack.md).

## Recommended deployment

Run the existing Compose application on one Oracle Cloud Infrastructure (OCI) VM and publish it through a named Cloudflare Tunnel.

1. Provision an Always Free eligible OCI Ampere A1 VM in the tenancy's home region, subject to current regional capacity. Oracle currently documents the A1 Always Free allowance as equivalent to 2 OCPUs and 12 GB memory. The A1 shape uses Arm, so the Compose image build and runtime must be verified on `linux/arm64` before the VM is accepted. No paid upgrade or paid resource is included in this design. If the eligible shape is unavailable or the app does not pass the Arm check, pause and review the fallback before provisioning anything billable.
2. Deploy the reviewed Post Chief source revision from `root/` with the existing Compose services. Persist PostgreSQL, Redis, media, and scheduler state. Do not expose PostgreSQL or Redis ports publicly.
3. Run `cloudflared` as a restart-on-boot service on the OCI VM, using a named, remotely managed Cloudflare Tunnel. Route public hostname `app.404builds.com` to `http://127.0.0.1:8000`. Keep the origin port bound to loopback. Cloudflare Tunnel provides an outbound-only connection from the origin and can route a public hostname to a private local service. This replaces the laptop-hosted Quick Tunnel.
4. Keep the root homepage at its current host during the first cutover. Configure `app.404builds.com` as a separate DNS hostname for the Post Chief app. Add a homepage navigation link labelled **Post Chief** that opens `https://app.404builds.com/`.
5. Use the same public origin for dashboard and API. No separate `api.404builds.com` hostname is required by the current Compose shape.

This keeps the current FastAPI/Celery/PostgreSQL/Redis design intact. A Cloudflare Pages/Workers-only deployment would require a separate backend and data-layer redesign, so it is an alternative rather than the first deployment.

### Hostname map

| Hostname | Service |
|---|---|
| `404builds.com` | Existing 404 Builds homepage during initial cutover |
| `app.404builds.com` | Post Chief dashboard and API on OCI, reached through Cloudflare Tunnel |

**DNS assumption:** Cloudflare is authoritative for the `404builds.com` zone. Verify the current nameservers before adding the app route. If DNS is hosted elsewhere, decide whether to move the zone or create the required tunnel CNAME at the authoritative provider. Do not alter the apex record as part of the app subdomain setup.

## Public URL and provider configuration

Set the production application values to:

- `PUBLIC_URL=https://app.404builds.com`
- `FRONTEND_URL=https://app.404builds.com`
- `SECURE_COOKIES=true`

Register the exact OAuth redirect URIs, provider app domains, and webhooks derived from that stable origin. For the core workflow, this includes the Meta and Threads OAuth callbacks and the GitHub webhook at `/api/webhooks/github`. Use the exact paths in [provider setup](../../providers.md) and [GitHub setup](../../github-setup.md). Remove the old temporary Quick Tunnel hostname from provider registrations after the new flow is validated.

Keep OAuth client secrets, the GitHub App private key, webhook secrets, `SIGNING_KEY`, `ENCRYPTION_KEY`, and owner bootstrap credentials in the VM's protected environment file or a secrets manager. Never place them in the dashboard bundle, GitHub content, or a public repository. Preserve the existing signing and encryption keys across upgrades and recovery.

## GPT/Codex operator access

Post Chief already defines a scoped agent API. Create a named agent key in Post Chief, keep it in the connected tool's server-side secret store, and send it only as a bearer credential. Do not place the key in browser JavaScript, URLs, GitHub files, or chat.

A stable website address alone does not provide a callable tool to ChatGPT/Codex. A supported MCP or equivalent tool connector must be registered and configured to call the existing Post Chief agent endpoints. The exact connector registration and authentication setup is a separate implementation gate. The app's [agent API and owner controls](../../agents.md) remain authoritative for scopes, audit records, and approval rules.

Keep the current approval policy for initial validation. In particular, schedule and publish actions require owner approval by default. Do not change them to AUTO until the owner explicitly chooses that operating mode after a successful end-to-end test.

## Cutover and validation

1. Confirm OCI Always Free A1 capacity, Cloudflare zone authority, and Arm64 Compose build compatibility. Stop before any paid resource selection.
2. Configure production secrets and owner bootstrap credentials without committing them. Apply the app's documented database migrations and launch API, worker, and exactly one scheduler.
3. Start the named Tunnel on VM boot and verify `https://app.404builds.com/api/health`, dashboard sign-in, database readiness, Celery worker ping, and scheduler liveness.
4. Reboot the VM and verify that Compose services and the Tunnel return automatically. Verify that database, media, credentials, and sessions persist.
5. Create and test the scoped operator credential and connected tool using read-only and draft operations first. Verify that approval gates still apply to schedule and publish.
6. Before switching new posts, create an encrypted off-host backup of PostgreSQL, media, source revision, and the signing/encryption keys. Follow the existing [backup and restore procedure](../../operations.md).
7. Move new 404 Builds campaigns to Post Chief after the owner reviews a concrete campaign and verifies its native outcome. Preserve Metricool history and existing schedules until separately reviewed; do not cancel or delete them as part of deployment.
8. Preserve the 08:00 America/New_York target for the daily publishing workflow. The external content run should create the campaign ahead of time; Post Chief's single scheduler dispatches its saved schedule. Keep duplicate checking enabled during the transition.

The repository records live acceptance for Facebook, Instagram, and Threads. Other providers and broader Metricool feature parity remain separate from this hosting cutover. See [dogfood acceptance](../../dogfood.md) and the [integration roadmap](../../integration-roadmap.md).

## Operating risks and boundaries

- A single VM is a single failure domain. The initial deployment prioritizes low operating cost and minimal code changes over high availability.
- Oracle documents Always Free compute availability by home region and notes that an out-of-host-capacity error can prevent VM creation. This design does not authorize paid fallback resources.
- The Oracle A1 shape is Arm. Production readiness requires a real `linux/arm64` build and runtime check, not an assumption based on local x86 development.
- A VM backup must be stored off the VM. Compose volumes alone are not disaster recovery. Preserve the original encryption key with encrypted backups.
- This design does not implement a Cloudflare Workers rewrite, multi-tenant onboarding, billing, automatic Metricool deletion, or expansion of provider integrations.

## Acceptance criteria

- `https://app.404builds.com` serves the authenticated Post Chief dashboard and API while the owner's laptop is closed.
- The 404 Builds homepage link opens Post Chief directly.
- Database and Redis remain private; API host binding remains loopback-only.
- The named Tunnel and exactly one scheduler resume after a VM restart.
- Public OAuth and webhook integrations use the stable app origin and exact registered paths.
- The connected GPT/Codex tool can use a scoped agent key without exposing it to the browser or repository.
- Draft/read tests succeed, while publishing remains approval-gated until the owner changes the policy.
- An encrypted off-host backup exists before the migration is treated as operational.
- New campaigns can be created and dispatched by Post Chief for Facebook, Instagram, and Threads without relying on Metricool.

## References

- [Oracle Always Free resources](https://docs.oracle.com/en-us/iaas/Content/FreeTier/freetier_topic-Always_Free_Resources.htm)
- [Cloudflare Tunnel published applications](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/routing-to-tunnel/)
- [Cloudflare Tunnel DNS records](https://developers.cloudflare.com/cloudflare-one/networks/connectors/cloudflare-tunnel/routing-to-tunnel/dns/)
- [Post Chief operations](../../operations.md)
- [Post Chief agent API](../../agents.md)
