from postchief.models import Organization

DEFAULTS={'campaigns:write':'AUTO','campaigns:schedule':'APPROVAL','campaigns:publish':'APPROVAL',
    'media:write':'AUTO','github:write':'APPROVAL','analytics:collect':'AUTO','agent:report':'AUTO'}
SCOPES={'campaigns:read','campaigns:write','campaigns:schedule','campaigns:publish','media:read','media:write',
    'github:read','github:write','analytics:read','analytics:collect','audit:read','agent:report'}


def mode_for(db,org_id,scope):
    org=db.get(Organization,org_id) if db else None
    mode=(org.autonomy.get(scope) if org else None) or DEFAULTS.get(scope,'DISABLED')
    return mode if mode in ('AUTO','APPROVAL','DISABLED') else 'DISABLED'
