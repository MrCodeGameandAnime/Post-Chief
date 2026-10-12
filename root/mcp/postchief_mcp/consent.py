"""Owner-controlled account linking, separate from agent tool access."""
import html
import secrets
from typing import Literal
from urllib.parse import urlencode, urlsplit, urlunsplit, parse_qsl
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field, ConfigDict
from sqlalchemy import select, update
from postchief.auth import require_owner
from .models import MCPPending
from .store import digest, now, utc, OAuthError
from .callbacks import callback_allowed

SCOPE_LABELS = {'campaigns:read': 'Read campaigns and connected accounts',
    'campaigns:write': 'Create, edit, and cancel campaigns',
    'campaigns:schedule': 'Schedule campaigns', 'campaigns:publish': 'Publish and retry delivery',
    'media:read': 'Read media details', 'media:write': 'Import media',
    'github:read': 'Read authorized GitHub repositories', 'github:write': 'Write workspace files',
    'analytics:read': 'Read saved analytics', 'analytics:collect': 'Request fresh analytics',
    'audit:read': 'Read audit history and approvals', 'agent:report': 'Save work summaries'}


class Consent(BaseModel):
    model_config = ConfigDict(extra='forbid')
    request: str = Field(min_length=32, max_length=100)
    decision: Literal['allow', 'deny']
    scopes: list[str] = Field(default_factory=list, max_length=12)


def redirect(parameters, **result):
    target = urlsplit(parameters['redirect_uri'])
    query = [*parse_qsl(target.query, keep_blank_values=True), *result.items()]
    if parameters.get('state') is not None: query.append(('state', parameters['state']))
    return urlunsplit((target.scheme, target.netloc, target.path, urlencode(query), ''))


def pending(db, request_id):
    row = db.scalar(select(MCPPending).where(MCPPending.token_hash == digest(request_id)))
    if not row or row.consumed or utc(row.expires_at) <= now():
        raise HTTPException(400, 'This connection request expired or was already used; connect again')
    return row


def build_consent_routes(app):
    router = APIRouter(prefix='/api/mcp/oauth')

    @router.get('/consent', response_class=HTMLResponse)
    def page(request: str):
        if len(request) > 100: raise HTTPException(400, 'Invalid connection request')
        with app.state.sessions() as db:
            params = pending(db, request).parameters
        nonce = secrets.token_urlsafe(24)
        choices = ''.join('<label><input type="checkbox" name="scope" checked value="' +
            html.escape(scope, quote=True) + '"> ' + html.escape(SCOPE_LABELS[scope]) + '</label>' for scope in params['scopes'])
        body = PAGE.replace('%%NONCE%%', nonce).replace('%%SCOPES%%', choices)
        return HTMLResponse(body, headers={'Cache-Control': 'no-store',
            'Content-Security-Policy': "default-src 'none'; script-src 'nonce-" + nonce +
            "'; style-src 'nonce-" + nonce + "'; connect-src 'self'; form-action 'self'; "
            "frame-ancestors 'none'; base-uri 'none'"})

    @router.post('/consent')
    def consent(data: Consent, actor=Depends(require_owner)):
        provider = app.state.mcp_oauth
        with app.state.sessions() as db:
            row = pending(db, data.request)
            params = row.parameters
            if not callback_allowed(params['redirect_uri'], app.state.settings.mcp_redirect_uris):
                raise HTTPException(400, 'Connection callback is no longer permitted')
            if len(set(data.scopes)) != len(data.scopes) or set(data.scopes) - set(params['scopes']):
                raise HTTPException(400, 'Select only the requested permissions')
            if data.decision == 'allow' and not data.scopes:
                raise HTTPException(400, 'Select at least one permission')
            consumed = db.execute(update(MCPPending).where(MCPPending.id == row.id,
                MCPPending.consumed.is_(False), MCPPending.expires_at > now()).values(consumed=True)
                .execution_options(synchronize_session=False))
            if consumed.rowcount != 1: raise HTTPException(400, 'Connection request already used')
            if data.decision == 'deny':
                destination = redirect(params, error='access_denied')
            else:
                try:
                    code = provider.store.authorize_in_session(db, actor.id, params['client_id'],
                        params['redirect_uri'], params['resource'], params['challenge'], data.scopes)
                except OAuthError:
                    raise HTTPException(400, 'Invalid authorization request') from None
                destination = redirect(params, code=code)
            db.commit()
        return {'redirect_url': destination}

    app.include_router(router)


PAGE = '''<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Connect your agent · Post Chief</title>
<style nonce="%%NONCE%%">
body{font:16px system-ui;background:#f6f7ef;color:#183d31;margin:0;padding:32px}
main{max-width:520px;margin:32px auto;padding:32px;background:white;border:1px solid #d8dfd1;border-radius:20px}
label{display:block;margin:12px 0}input[type=email],input[type=password]{box-sizing:border-box;width:100%;padding:12px;border:1px solid #a9b9a4;border-radius:8px}
button{padding:12px 18px;margin:12px 8px 0 0;border:1px solid #a9b9a4;border-radius:8px;background:#c5e969;color:#183d31;font-weight:600;cursor:pointer}
#message{white-space:pre-wrap;color:#843b26}.hidden{display:none}fieldset{border:0;padding:0}
</style></head><body><main><h1>Connect your agent</h1>
<p>Give your agent permission to use Post Chief. You can revoke this connection in Agent settings.</p>
<form id="login" class="hidden"><h2>Sign in</h2><label>Email<input name="email" type="email" autocomplete="username" required></label>
<label>Password<input name="password" type="password" autocomplete="current-password" required></label><button>Sign in</button></form>
<form id="consent" class="hidden"><h2>Permissions</h2><fieldset>%%SCOPES%%</fieldset>
<p>Your existing publishing and approval settings still apply.</p><button name="decision" value="allow">Allow connection</button><button name="decision" value="deny">Cancel</button></form>
<p id="message" role="status"></p></main><script nonce="%%NONCE%%">
const login=document.getElementById('login'), consent=document.getElementById('consent'), message=document.getElementById('message');
let csrf='';
async function session(){const r=await fetch('/api/auth/me');if(r.ok){const me=await r.json();if(me.kind!=='owner')throw Error('Owner sign-in required');csrf=me.csrf;login.classList.add('hidden');consent.classList.remove('hidden')}else{login.classList.remove('hidden');consent.classList.add('hidden')}}
login.addEventListener('submit',async e=>{e.preventDefault();message.textContent='';const form=new FormData(login);try{const r=await fetch('/api/auth/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({email:form.get('email'),password:form.get('password')})});login.elements.password.value='';if(!r.ok)throw Error('Sign-in failed. Check your credentials or try again later.');await session()}catch(err){message.textContent=err.message}});
consent.addEventListener('submit',async e=>{e.preventDefault();const buttons=consent.querySelectorAll('button');buttons.forEach(b=>b.disabled=true);try{const data={request:new URLSearchParams(location.search).get('request'),decision:e.submitter.value,scopes:[...consent.querySelectorAll('input:checked')].map(i=>i.value)};const r=await fetch('/api/mcp/oauth/consent',{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},body:JSON.stringify(data)});const result=await r.json();if(!r.ok)throw Error(typeof result.detail==='string'?result.detail:'Unable to connect; try again');location.assign(result.redirect_url)}catch(err){message.textContent=err.message;buttons.forEach(b=>b.disabled=false)}});
session().catch(err=>message.textContent=err.message);
</script></body></html>'''
