# Maintainer API and owner controls

The owner creates a named scoped key in Agent, or `POST /api/agent/keys`. The returned `pc_agent_…` token is shown once and only its SHA-256 hash is stored. Send it in `Authorization: Bearer …`. Never put it in URLs, GitHub content or run summaries. Revoke with `DELETE /api/agent/keys/{id}`. Agent keys cannot create keys, manage connections, change autonomy or decide approvals.

Read endpoints reuse the campaign/planner, connection, capability, media, GitHub and analytics APIs with explicit scopes. `GET /api/settings/autonomy` reports policy. `GET /api/audit`, `/api/agent/runs` and `/api/approvals` expose permitted concise records. Agents only see their own approvals. Authentication attempts with valid keys record method/path without credentials or request bodies. Mutating route audits report durable results. `POST /api/agent/runs` records a bounded summary/status; do not submit hidden chain-of-thought or secrets.

Autonomy modes are AUTO, APPROVAL and DISABLED. Draft/media creation and analytics refresh are AUTO initially. Scheduling, public publishing and workspace writes require approval initially. The owner can change each permission in Agent or `PUT /api/settings/autonomy`. Scope grants never override a disabled policy. Policy changes control future agent actions; already authorized schedules remain explicit records and can be cancelled separately.

Approval-required operations use `POST /api/agent/actions` with `action`, `target_id` where applicable, and `data`. Supported actions: campaign.create/update/schedule/publish/cancel, publication.retry, analytics.refresh and github.write. Native API writes obey autonomy too; they cannot bypass required approval. Media uploads can use the native endpoint only under AUTO; an approval-required media upload must be performed by the owner.

Example publishing request:

```json
{"action":"campaign.publish","target_id":"campaign-uuid","data":{}}
```

A 428 response returns `detail.approval_id` and the exact canonical payload/context. An owner reviews it, then calls `POST /api/approvals/{id}/approve` or `/reject`. The agent resubmits the identical action plus `approval_id`. Approvals expire after fifteen minutes, bind actor/organization/action/payload/current campaign revision (and publication outcome for retries), and are single-use. Consumption and the native database mutation share a transaction. Failure rolls back consumption. Changed copy or stale outcomes need a new approval. Approved GitHub writes retain optimistic SHA checks.

The API does not run an LLM or autonomous maintainer itself. An external GPT/client uses this contract, authorized GitHub context and its concise run reports. Public comments, DMs, advertising and published-content deletion remain unavailable in this MVP.
