# GitHub App setup

Create a private GitHub App for Post Chief. Set its homepage to the dashboard and its webhook URL to `PUBLIC_URL/api/webhooks/github`. Select repository **Contents: read/write** and **Metadata: read**. Subscribe to installation and installation-repository events. Generate a private key and a strong webhook secret. Store the App ID, app slug, PEM key and webhook secret only in `root/.env` or the deployment secret manager.

Post Chief reduces installation token permissions for each operation. Repository listing and reads use read-only tokens; content access is further restricted to the specific repository ID. Writes request a token with write access only to the selected social workspace. Source repository tokens remain read-only, even when the installed App has broader permissions.

Install the App only on the intended repositories. The dashboard/owner API can retrieve the installation link from `GET /api/github/installation-link`, list granted repositories through `GET /api/github/installations/{id}/repositories`, then select a workspace and optional sources using `POST /api/github/workspace`. Account ID authentication alone never grants repository access.

Owner setup: migrations must be applied before starting the API. On first startup, `BOOTSTRAP_EMAIL` and a password of at least 16 characters create the single self-hosted owner. Existing users are never overwritten. The generated local development `.env` contains the initial owner credentials; change them before external deployment. HTTPS and `SECURE_COOKIES=true` are required outside localhost.

The content API only reads explicitly selected repositories and only writes to the social workspace. Writes require the current file SHA; a conflict returns 409 instead of overwriting another editor. File reads are UTF-8 through the GitHub Contents API. Binary asset ingestion is a separate operation.

Webhooks require HMAC SHA-256 verification and a delivery ID. Duplicate deliveries are acknowledged once; deleted or suspended installations are disconnected. GitHub installation tokens are created on demand and are never persisted or exposed to clients.

Direct app execution uses the GitHub App JWT and installation token flow documented in [GitHub's installation authentication guide](https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/authenticating-as-a-github-app-installation).

Login attempts are throttled using durable audit events. When deploying behind a proxy, configure trusted forwarded addresses explicitly; do not trust client-supplied forwarding headers.
