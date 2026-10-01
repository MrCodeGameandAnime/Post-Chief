# Post Chief

### Objective

Build a self-hosted social-media planning, publishing, analytics, and agent-control platform for 404 Builds.

The system replaces Metricool as the publishing layer while preserving and expanding the existing GitHub-based operating model.

The core operating loop is:

```text
GPT maintainer agent
        ↓
private GitHub social workspace
        ↓
reads brand / plan / history / analytics
        ↓
decides what work is required
        ↓
creates or updates posts and assets
        ↓
calls 404 Social API
        ↓
404 Social schedules / publishes
        ↓
native social APIs
        ↓
metrics / results / failures
        ↓
404 Social API
        ↓
GPT maintainer agent
        ↓
writes durable state back to GitHub
```

GitHub is the durable planning and knowledge layer.

404 Social is the operational execution layer.

GPT is the maintainer/operator.

The dashboard is the human control room.

---

# 1. Existing GitHub Workflow

The existing private repository is:

```text
MrCodeGameandAnime/404-builds-social
```

Existing structure:

```text
assets/
    art/
    brand/
    vid/

docs/
    ANALYTICS.md
    BRAND.md
    CONTENT_LEDGER.md
    CONTENT_PLAN.md
    PLAN.md
    README.md

posts/
    2026-09/
        001-404-builds-experiment-reel.md

templates/
    build-update.md
    devlog.md
    product-launch.md
    tech-quote.md
    tech-quote.svg
```

Preserve this conceptual model.

Do not migrate planning state into the application database and abandon GitHub.

The repository remains authoritative for:

```text
brand rules
content strategy
content history
planned campaigns
post drafts
post records
templates
human instructions
agent observations
approved media
summarized analytics
```

---

# 2. Separation of Responsibilities

## GitHub

GitHub stores durable human-readable state.

Examples:

```text
BRAND.md
CONTENT_PLAN.md
CONTENT_LEDGER.md
ANALYTICS.md
posts/
templates/
assets/
```

GitHub may also provide read-only context from other private project repositories.

Example:

```text
404-builds-social       READ + WRITE

Dungeon-Drifters        READ
Windows-App-Cleaner     READ
HeadsUp                 READ
other product repos     READ
```

The social agent may inspect project repos to discover:

```text
releases
changelogs
screenshots
README changes
meaningful commits
product milestones
new assets
release tags
completed features
```

Do not treat every commit as social content.

The agent decides whether activity is meaningful based on the social plan.

---

## Application Database

Use PostgreSQL for operational state.

Store:

```text
users
organizations
social connections
GitHub installations
OAuth credentials
refresh tokens
campaigns
publications
schedule state
job state
provider IDs
webhook state
retry state
analytics snapshots
rate-limit state
agent execution records
approval state
```

Never store OAuth secrets or social credentials in GitHub.

---

## Queue / Scheduler

Use Redis plus a worker system or another durable queue.

Responsibilities:

```text
scheduled jobs
publishing
retries
analytics collection
webhook processing
media processing
token refresh
background synchronization
```

Jobs must be idempotent.

Repeated execution must not accidentally create duplicate social posts.

---

# 3. Provider Architecture

Do not scatter platform-specific behavior throughout the codebase.

Create a provider abstraction.

Conceptually:

```python
class SocialProvider:
    async def connect(...)
    async def disconnect(...)
    async def refresh_auth(...)

    async def validate_media(...)
    async def publish(...)
    async def delete(...)

    async def get_post(...)
    async def get_post_metrics(...)
    async def get_account_metrics(...)

    async def get_comments(...)
    async def reply(...)
```

Each provider advertises capabilities.

Example:

```json
{
  "provider": "instagram",
  "capabilities": {
    "text": false,
    "image": true,
    "video": true,
    "carousel": true,
    "analytics": true,
    "comments": true,
    "replies": true,
    "messages": false
  }
}
```

The application must not assume every social network supports the same operations.

---

# 4. Initial Social Providers

Do not attempt all integrations simultaneously.

## Phase 1

Build:

```text
Facebook Pages
Instagram Professional
Threads
Bluesky
LinkedIn
```

These are sufficient to replace the current 404 Builds publishing workflow.

## Phase 2

Add:

```text
X
Pinterest
TikTok Personal
TikTok Business
YouTube
```

## Phase 3

Add:

```text
Google Business Profile
Twitch
```

## Phase 4

Advertising:

```text
Meta Ads
Google Ads
TikTok Ads
```

## Phase 5

External reporting:

```text
Looker Studio
```

Do not allow later providers to block the Phase 1 product.

---

# 5. GitHub Integration

Use a GitHub App.

Do not build the production integration around personal access tokens.

Support:

```text
Install GitHub App
Choose organization/account
Select specific repositories
Select one social workspace repository
Grant optional read access to project repositories
```

Default permissions:

### Social workspace

```text
Contents: read/write
Metadata: read
```

### Project/source repositories

```text
Contents: read
Metadata: read
Releases: read
```

Add more permissions only when required.

The application should allow:

```text
Social Workspace:
MrCodeGameandAnime/404-builds-social

Source Repositories:
☑ Dungeon Drifters
☑ Windows App Cleaner
☑ HeadsUp
☐ Room Fever
```

The application should expose GitHub information to the GPT maintainer through its own normalized API where practical.

---

# 6. Core Data Model

Use the distinction:

```text
Campaign
    ↓
Publications
```

A Campaign is one conceptual piece of content.

Example:

```text
WAC certification announcement
```

Publications are platform-specific executions:

```text
Campaign 42
├── Facebook publication
├── Instagram publication
├── Threads publication
├── Bluesky publication
└── LinkedIn publication
```

Suggested core entities:

```text
Organization
User
Workspace
GitHubConnection
SocialAccount
Campaign
CampaignAsset
Publication
Schedule
AnalyticsSnapshot
Comment
AgentRun
Approval
WebhookEvent
```

---

# 7. Campaign Model

A campaign contains a master version plus optional platform overrides.

Example:

```json
{
  "title": "WAC certification",
  "body": "master copy",
  "assets": ["asset-123"],
  "scheduled_at": "2026-10-05T08:00:00-04:00",

  "destinations": [
    "facebook",
    "instagram",
    "threads",
    "bluesky",
    "linkedin"
  ],

  "overrides": {
    "linkedin": {
      "body": "longer LinkedIn copy"
    },

    "threads": {
      "body": "short conversational version"
    }
  }
}
```

One campaign should appear as one item in the planner even when publishing to multiple networks.

---

# 8. Publishing API

Expose a clean API for the GPT maintainer.

Initial endpoints should conceptually include:

```text
GET    /connections
GET    /capabilities

GET    /campaigns
POST   /campaigns
GET    /campaigns/{id}
PATCH  /campaigns/{id}

POST   /campaigns/{id}/schedule
POST   /campaigns/{id}/publish
POST   /campaigns/{id}/cancel

GET    /publications/{id}
POST   /publications/{id}/retry

GET    /analytics
GET    /analytics/campaigns/{id}

GET    /comments
POST   /comments/{id}/reply

GET    /github/workspace
GET    /github/repositories
GET    /github/repositories/{repo}/releases
GET    /github/repositories/{repo}/activity
```

Do not expose raw provider complexity to the agent unless necessary.

The agent should say:

```text
publish campaign 42
```

not:

```text
create Instagram media container
poll media container
publish container
retrieve media ID
then perform Meta-specific follow-up request
```

That belongs inside the provider.

---

# 9. Scheduler

404 Social owns scheduling.

Do not depend on the social network having native scheduled-post support.

Example:

```text
Campaign scheduled:
2026-10-07 08:00 America/New_York
```

At execution:

```text
scheduler
    ↓
create publication jobs
    ↓
Facebook provider
Instagram provider
Threads provider
Bluesky provider
LinkedIn provider
```

Each publication receives independent state:

```text
pending
processing
published
failed
retrying
cancelled
```

A campaign may therefore show:

```text
Facebook      Published
Instagram     Published
Threads       Published
Bluesky       Published
LinkedIn      Failed
```

The system must support retrying LinkedIn without republishing the other four.

---

# 10. Agent Workflow

The GPT maintainer should operate using a defined cycle.

Example daily cycle:

```text
1. Open configured GitHub social workspace.

2. Read:
   docs/BRAND.md
   docs/CONTENT_PLAN.md
   docs/CONTENT_LEDGER.md
   docs/ANALYTICS.md
   relevant recent posts/
   relevant templates/

3. Query 404 Social:
   connected networks
   scheduled campaigns
   recent publications
   failures
   current metrics

4. Inspect authorized project repositories when useful.

5. Determine whether action is required.

6. If content is required:
   select topic
   verify factual status
   create post record
   prepare platform-specific copy
   select/create media

7. Write/update GitHub records.

8. Submit campaign through 404 Social API.

9. Schedule or publish according to plan and autonomy rules.

10. After publication:
    retrieve provider IDs
    update ledger

11. Later:
    retrieve analytics
    update ANALYTICS.md

12. Adjust CONTENT_PLAN.md only when evidence or human direction warrants it.
```

The agent should not create filler merely because a scheduled run occurred.

---

# 11. Agent Autonomy

Implement configurable permissions.

Example:

```text
Read GitHub                 AUTO
Update planning docs        AUTO
Create post drafts          AUTO
Generate media              AUTO
Schedule organic posts      AUTO
Publish organic posts       AUTO

Reply to public comments    APPROVAL
Send private messages       APPROVAL
Change overall strategy     APPROVAL
Delete published content    APPROVAL

Spend advertising money     DISABLED
Modify ad campaigns         DISABLED
```

These should be configurable per organization.

---

# 12. Dashboard

The dashboard is not the primary intelligence.

It is a human control surface over the system.

Initial navigation:

```text
Overview
Planner
Content
Agent
Analytics
Inbox
Assets
Connections
GitHub
Settings
```

### Overview

Show:

```text
Today's activity
Upcoming posts
Failures
Recent performance
Agent actions
Items needing approval
```

### Planner

Calendar views:

```text
month
week
list
```

Campaigns appear once even if cross-posted.

Platform icons show destinations.

### Content

Show:

```text
drafts
scheduled
published
failed
templates
campaign history
```

### Agent

Show:

```text
current objective
current GitHub workspace
recent reads
recent decisions
recent actions
observations
pending approvals
autonomy configuration
```

Avoid displaying hidden chain-of-thought.

Show concise decision summaries and action logs instead.

### Analytics

Normalize metrics where reasonable:

```text
views
reach
impressions
likes
comments
shares
clicks
watch time
followers
engagement
```

Preserve provider-specific metrics separately.

Do not pretend unlike metrics are identical.

### Connections

Display:

```text
Facebook
Instagram
Threads
Bluesky
LinkedIn
GitHub
```

with:

```text
Connected
Reconnect required
Permission issue
Token expiration
Unavailable
```

---

# 13. Media System

Support:

```text
images
videos
SVG
generated media
GitHub assets
manual upload
```

Maintain an internal asset record.

Example:

```text
Asset
ID
source
GitHub path
MIME type
dimensions
duration
checksum
campaign associations
```

Avoid downloading the same private GitHub asset repeatedly when unnecessary.

Cache safely when appropriate.

---

# 14. Analytics Feedback Loop

Analytics are not merely dashboard decoration.

They feed the agent.

Example:

```text
Campaign 42

Instagram
Reach: 1,440
Likes: 84

Threads
Views: 4,310
Replies: 22

LinkedIn
Impressions: 890
Clicks: 31
```

The agent may summarize:

```text
Short product demos have outperformed quote cards on Instagram
during the last four comparable posts.

Threads responds better to compact build commentary.

LinkedIn has produced more outbound clicks from release posts.
```

Only make conclusions supported by sufficient data.

Do not make strategic changes from one anomalous post.

Write useful summarized observations back into GitHub.

---

# 15. GitHub Post Record

Continue using durable Markdown records.

Example:

```markdown
---
id: 014
campaign_id: 42
status: published
created: 2026-10-04
scheduled: 2026-10-05T08:00:00-04:00
published: 2026-10-05T08:00:11-04:00

platforms:
  - facebook
  - instagram
  - threads
  - bluesky
  - linkedin
---

# WAC Certification

## Core idea

Windows App Cleaner has completed Microsoft certification.

## Facebook

...

## Instagram

...

## Threads

...

## Bluesky

...

## LinkedIn

...

## Assets

- assets/art/wac-certification.png

## Publication IDs

- Facebook: ...
- Instagram: ...
- Threads: ...
- Bluesky: ...
- LinkedIn: ...

## Results

Pending.
```

The database remains authoritative for immediate operational state.

GitHub receives the durable human-readable record.

---

# 16. Failure Handling

This is mandatory.

Every provider call must return normalized error information.

Example:

```json
{
  "status": "failed",
  "provider": "linkedin",
  "reason": "AUTH_EXPIRED",
  "retryable": false,
  "action_required": "RECONNECT"
}
```

Possible classes:

```text
AUTH_EXPIRED
AUTH_REVOKED
RATE_LIMITED
MEDIA_INVALID
CONTENT_REJECTED
NETWORK_ERROR
PROVIDER_ERROR
PERMISSION_MISSING
ACCOUNT_RESTRICTED
UNKNOWN
```

The dashboard and API should tell the agent what happened without requiring it to decode raw provider responses.

---

# 17. Security

Requirements:

```text
Encrypt OAuth tokens at rest.
Never commit secrets to GitHub.
Never expose provider tokens to the frontend.
Use short-lived GitHub installation tokens.
Verify provider webhooks.
Verify GitHub webhooks.
Use CSRF/state protection for OAuth.
Use least-privilege permissions.
Keep audit logs for agent actions.
```

Do not allow arbitrary GitHub repositories to be read merely because the user authenticated with GitHub.

Only use repositories granted to the GitHub App.

---

# 18. MVP Definition

The MVP is complete when this exact workflow works end-to-end:

```text
1. User signs into 404 Social.

2. User connects:
   GitHub
   Facebook
   Instagram
   Threads
   Bluesky
   LinkedIn

3. User selects:
   MrCodeGameandAnime/404-builds-social
   as the social workspace.

4. GPT maintainer can read the workspace.

5. GPT maintainer creates a campaign.

6. Campaign appears in the planner.

7. Campaign is scheduled.

8. At the scheduled time, 404 Social publishes it.

9. Dashboard reports success/failure independently per platform.

10. 404 Social retrieves basic metrics.

11. GPT maintainer retrieves results through the API.

12. GPT maintainer updates:
    CONTENT_LEDGER.md
    ANALYTICS.md
    relevant post record.
```

At that point Metricool is no longer required for the core 404 Builds workflow.

---

# 19. Do Not Overbuild Before MVP

Do **not** block MVP on:

```text
TikTok
YouTube
X
Pinterest
Twitch
Google Business Profile
advertising
Looker Studio
DM inbox
advanced comment moderation
multi-agent architecture
complex AI orchestration
automatic ad spending
enterprise roles
billing
public SaaS onboarding
```

First prove:

```text
GitHub
   ↓
GPT
   ↓
404 Social API
   ↓
native social platforms
   ↓
analytics
   ↓
GPT
   ↓
GitHub
```

Everything else builds on that.

---

# 20. Recommended Implementation Order

```text
Gate 1
Repository bootstrap
Architecture docs
Configuration
Database schema
Provider interfaces

Gate 2
GitHub App integration
Private repo selection
Workspace read/write

Gate 3
Campaign CRUD
Publication model
Media model
Planner API

Gate 4
Bluesky provider
Use as first end-to-end publishing proof

Gate 5
Meta authentication
Facebook provider
Instagram provider
Threads provider

Gate 6
LinkedIn provider

Gate 7
Scheduler
Durable queues
Retries
Idempotency
Failure normalization

Gate 8
Dashboard
Connections
Planner
Campaign editor
Publication status

Gate 9
Analytics ingestion
Normalized reporting
Historical snapshots

Gate 10
GPT-facing API
Agent action audit log
Autonomy controls

Gate 11
Full GitHub feedback loop
Post records
Ledger updates
Analytics summaries

Gate 12
End-to-end 404 Builds dogfood test
Remove Metricool dependency from normal operation
```

Each gate must have tests and an explicit completion report before proceeding.

---

# 21. Definition of Done

The system is successful when the maintainer can receive an instruction like:

```text
Maintain the 404 Builds social presence.

Use the configured GitHub workspace as persistent context.
Follow BRAND.md and CONTENT_PLAN.md.
Review recent content and analytics.
Inspect authorized project repositories when useful.
Prepare content only when warranted.
Use existing templates and approved assets.
Schedule and publish through 404 Social.
Handle platform failures safely.
Record publication results.
Update GitHub with durable content and analytics history.
Request approval when an action exceeds configured autonomy.
```
