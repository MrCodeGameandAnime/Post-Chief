"""Managed writes preserve Post Chief's native approval and revision controls."""
from uuid import UUID
from typing import Any
from mcp.server.mcpserver import Context
from postchief.campaigns.schemas import CampaignCreate, CampaignPatch
from postchief.publishing.routes import Schedule
from postchief.github.routes import ContentWrite
from postchief.assets import GitHubAssetImport
from postchief.agents.routes import RunReport
from .tools_read import api, tool_options


async def action(bridge, ctx, name, target=None, data=None, approval_id=None):
    payload = {'action': name, 'data': data or {}}
    if target is not None: payload['target_id'] = str(target)
    if approval_id is not None: payload['approval_id'] = str(approval_id)
    return await api(bridge, ctx, 'POST', '/api/agent/actions', json=payload)


def register_write_tools(mcp, bridge):
    @mcp.tool(**tool_options(['campaigns:write'], read=False))
    async def create_campaign(campaign: CampaignCreate, ctx: Context,
            approval_id: UUID | None = None) -> dict[str, Any]:
        """Create an unscheduled campaign draft. Read connections/assets first; creating does not publish."""
        return await action(bridge, ctx, 'campaign.create',
            data=campaign.model_dump(mode='json', exclude_none=True), approval_id=approval_id)

    @mcp.tool(**tool_options(['campaigns:write'], read=False))
    async def update_campaign(campaign_id: UUID, changes: CampaignPatch, ctx: Context,
            approval_id: UUID | None = None) -> dict[str, Any]:
        """Edit a draft using its current revision. Attempted or published content cannot be edited."""
        return await action(bridge, ctx, 'campaign.update', campaign_id,
            changes.model_dump(mode='json', exclude_unset=True), approval_id)

    @mcp.tool(**tool_options(['campaigns:write'], read=False, destructive=True))
    async def cancel_campaign(campaign_id: UUID, ctx: Context,
            approval_id: UUID | None = None) -> dict[str, Any]:
        """Cancel pending delivery. This does not delete published posts; in-flight writes cannot be cancelled."""
        return await action(bridge, ctx, 'campaign.cancel', campaign_id, approval_id=approval_id)

    @mcp.tool(**tool_options(['campaigns:schedule'], read=False, external=True))
    async def schedule_campaign(campaign_id: UUID, schedule: Schedule, ctx: Context,
            approval_id: UUID | None = None) -> dict[str, Any]:
        """Schedule future delivery with a timezone offset. Resubmit identical inputs only after owner approval."""
        return await action(bridge, ctx, 'campaign.schedule', campaign_id,
            schedule.model_dump(mode='json'), approval_id)

    @mcp.tool(**tool_options(['campaigns:publish'], read=False, external=True))
    async def publish_campaign(campaign_id: UUID, ctx: Context,
            approval_id: UUID | None = None) -> dict[str, Any]:
        """Request immediate public delivery. Approval may be required; check destination results afterward."""
        return await action(bridge, ctx, 'campaign.publish', campaign_id, approval_id=approval_id)

    @mcp.tool(**tool_options(['campaigns:publish'], read=False, external=True))
    async def retry_publication(publication_id: UUID, ctx: Context,
            approval_id: UUID | None = None) -> dict[str, Any]:
        """Explicitly retry one failed destination. Never retry an uncertain outcome; read its state first."""
        return await action(bridge, ctx, 'publication.retry', publication_id, approval_id=approval_id)

    @mcp.tool(**tool_options(['media:write', 'github:read'], read=False, external=True))
    async def import_github_asset(asset: GitHubAssetImport, ctx: Context) -> dict[str, Any]:
        """Import media from an authorized GitHub repository, returning its asset ID for campaigns."""
        return await api(bridge, ctx, 'POST', '/api/assets/github', json=asset.model_dump())

    @mcp.tool(**tool_options(['github:write'], read=False, external=True))
    async def write_github_content(content: ContentWrite, ctx: Context,
            approval_id: UUID | None = None) -> dict[str, Any]:
        """Write a workspace file with its current SHA and owner approval when required. No source repo writes."""
        return await action(bridge, ctx, 'github.write',
            data=content.model_dump(), approval_id=approval_id)

    @mcp.tool(**tool_options(['analytics:collect'], read=False))
    async def refresh_analytics(publication_id: UUID, ctx: Context,
            approval_id: UUID | None = None) -> dict[str, Any]:
        """Request fresh metrics for a published destination; results arrive later through the worker."""
        return await action(bridge, ctx, 'analytics.refresh', publication_id, approval_id=approval_id)

    @mcp.tool(**tool_options(['agent:report'], read=False))
    async def report_agent_run(report: RunReport, ctx: Context) -> dict[str, Any]:
        """Save a concise work summary and status. Do not include credentials or private reasoning."""
        return await api(bridge, ctx, 'POST', '/api/agent/runs', json=report.model_dump())
