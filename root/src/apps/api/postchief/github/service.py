import base64
import re
from datetime import timedelta
from pathlib import PurePosixPath
from urllib.parse import quote
import httpx
import jwt
from postchief.models import now


class GitHubError(Exception):
    def __init__(self, status: int, message: str):
        self.status, self.message = status, message
        super().__init__(message)


class GitHubService:
    def __init__(self, app_id: str, private_key: str, client: httpx.AsyncClient):
        self.app_id, self.private_key, self.http = app_id, private_key.replace("\\n", "\n"), client

    async def request(self, method: str, path: str, token: str, **kwargs):
        try:
            response = await self.http.request(method, "https://api.github.com" + path, headers={"Authorization":f"Bearer {token}", "Accept":"application/vnd.github+json", "X-GitHub-Api-Version":"2022-11-28"}, **kwargs)
        except httpx.HTTPError:
            raise GitHubError(502, "GitHub is unavailable") from None
        if response.is_error:
            status = response.status_code if response.status_code in (401,403,404,409,422,429) else 502
            raise GitHubError(status, "GitHub content changed; refresh its SHA" if status == 409 else "GitHub request failed")
        return response.json() if response.content else {}

    async def installation_token(self, installation: int, repository_ids: list[int] | None = None, write=False):
        if not self.app_id or not self.private_key:
            raise GitHubError(503, "Configure the GitHub App ID and private key")
        token = jwt.encode({"iat":now()-timedelta(seconds=60),"exp":now()+timedelta(minutes=9),"iss":self.app_id}, self.private_key, algorithm="RS256")
        payload = {"permissions":{"metadata":"read","contents":"write" if write else "read"}}
        if repository_ids is not None:
            payload["repository_ids"] = repository_ids
        data = await self.request("POST", f"/app/installations/{installation}/access_tokens", token, json=payload)
        return data["token"]

    async def repositories(self, installation: int, token: str | None = None):
        token = token or await self.installation_token(installation)
        repos, page = [], 1
        while True:
            data = await self.request("GET", f"/installation/repositories?per_page=100&page={page}", token)
            batch = data.get("repositories", [])
            repos.extend({"id":r["id"],"full_name":r["full_name"]} for r in batch)
            if len(batch) < 100:
                return repos
            page += 1

    async def authorized(self, installation: int, repo: str, write=False):
        if not re.fullmatch(r"[\w.-]+/[\w.-]+", repo):
            raise GitHubError(400, "Invalid repository name")
        token = await self.installation_token(installation)
        selected = next((r for r in await self.repositories(installation, token) if r["full_name"] == repo), None)
        if not selected:
            raise GitHubError(403, "Repository is not granted to this installation")
        return await self.installation_token(installation, [selected["id"]], write=write)

    @staticmethod
    def content_path(path: str):
        if not path or path.startswith("/") or "\\" in path or "\x00" in path or any(part in (".","..") for part in path.split("/")):
            raise GitHubError(400, "Invalid repository path")
        return quote(str(PurePosixPath(path)), safe="/")

    async def read_file(self, installation: int, repo: str, path: str):
        encoded_path = self.content_path(path)
        token = await self.authorized(installation, repo)
        data = await self.request("GET", f"/repos/{repo}/contents/{encoded_path}", token)
        if not isinstance(data, dict) or data.get("encoding") != "base64":
            raise GitHubError(422, "Select a text file up to GitHub's content API limit")
        try:
            content = base64.b64decode(data["content"]).decode("utf-8")
        except (UnicodeDecodeError, ValueError):
            raise GitHubError(422, "This file is not UTF-8 text") from None
        return {"path":path,"sha":data["sha"],"content":content}

    async def write_file(self, installation: int, repo: str, path: str, content: str, sha: str | None):
        encoded_path = self.content_path(path)
        token = await self.authorized(installation, repo, write=True)
        payload = {"message":f"Post Chief: update {path}","content":base64.b64encode(content.encode()).decode()}
        if sha:
            payload["sha"] = sha
        return await self.request("PUT", f"/repos/{repo}/contents/{encoded_path}", token, json=payload)

    async def activity(self, installation: int, repo: str, kind: str):
        token = await self.authorized(installation, repo)
        endpoint = "releases" if kind == "releases" else "commits"
        return await self.request("GET", f"/repos/{repo}/{endpoint}?per_page=30", token)

    async def feedback_snapshot(self,installation,repo,paths):
        token=await self.authorized(installation,repo)
        metadata=await self.request('GET',f'/repos/{repo}',token)
        branch=metadata['default_branch'];ref=quote('heads/'+branch,safe='/')
        head=(await self.request('GET',f'/repos/{repo}/git/ref/{ref}',token))['object']['sha']
        commit=await self.request('GET',f'/repos/{repo}/git/commits/{head}',token)
        tree=commit['tree']['sha'];trees={};files={}
        async def entries(sha):
            if sha not in trees:
                value=await self.request('GET',f'/repos/{repo}/git/trees/{sha}',token)
                if value.get('truncated'):raise GitHubError(422,'Workspace tree is truncated; use smaller directories')
                trees[sha]={entry['path']:entry for entry in value['tree']}
            return trees[sha]
        for path in paths:
            self.content_path(path);parts=path.split('/');current=tree;entry=None
            for index,part in enumerate(parts):
                entry=(await entries(current)).get(part)
                if not entry:break
                if index<len(parts)-1:
                    if entry['mode']!='040000' or entry['type']!='tree':raise GitHubError(422,'Feedback paths cannot follow symlinks or non-directory parents')
                    current=entry['sha']
            if not entry:files[path]={'content':'','mode':'100644','exists':False};continue
            if entry['type']!='blob' or entry['mode'] not in ('100644','100755'):
                raise GitHubError(422,'Feedback can update only ordinary text files, never symlinks')
            if entry.get('size',0)>1024*1024:raise GitHubError(413,'Feedback file exceeds 1 MiB')
            blob=await self.request('GET',f'/repos/{repo}/git/blobs/{entry["sha"]}',token)
            try:
                if blob.get('encoding')!='base64':raise ValueError()
                raw=base64.b64decode(''.join(blob['content'].split()),validate=True)
                if len(raw)>1024*1024:raise GitHubError(413,'Feedback file exceeds 1 MiB')
                content=raw.decode('utf-8')
            except (ValueError,UnicodeDecodeError):raise GitHubError(422,'Feedback requires UTF-8 text files') from None
            files[path]={'content':content,'mode':entry['mode'],'exists':True}
        return {'branch':branch,'head':head,'tree':tree,'files':files}

    async def commit_feedback(self,installation,repo,snapshot,files,message):
        token=await self.authorized(installation,repo,write=True)
        ref=quote('heads/'+snapshot['branch'],safe='/')
        latest=await self.request('GET',f'/repos/{repo}/git/ref/{ref}',token)
        if latest['object']['sha']!=snapshot['head']:raise GitHubError(409,'Workspace branch changed; review a fresh preview')
        tree=await self.request('POST',f'/repos/{repo}/git/trees',token,json={'base_tree':snapshot['tree'],
            'tree':[{'path':path,'mode':snapshot['files'][path]['mode'],'type':'blob','content':content} for path,content in files.items()]})
        commit=await self.request('POST',f'/repos/{repo}/git/commits',token,json={'message':message,'tree':tree['sha'],'parents':[snapshot['head']]})
        # Fast-forward-only makes a concurrent writer's branch update fail rather
        # than replacing it. All feedback files become visible in one commit.
        try:await self.request('PATCH',f'/repos/{repo}/git/refs/{ref}',token,json={'sha':commit['sha'],'force':False})
        except GitHubError as error:
            if error.status in (409,422):raise GitHubError(409,'Workspace branch changed or is protected; review a fresh preview') from None
            raise
        return {'sha':commit['sha'],'changed':True}

    async def read_binary(self, installation: int, repo: str, path: str, etag: str | None = None, max_bytes: int = 80*1024*1024):
        encoded = self.content_path(path)
        token = await self.authorized(installation,repo)
        headers = {"Authorization":f"Bearer {token}","Accept":"application/vnd.github.raw+json","X-GitHub-Api-Version":"2022-11-28"}
        if etag:
            headers["If-None-Match"] = etag
        try:
            async with self.http.stream("GET",f"https://api.github.com/repos/{repo}/contents/{encoded}",headers=headers) as response:
                if response.status_code == 304:
                    return {"data":None,"etag":etag}
                if response.is_error:
                    status = response.status_code if response.status_code in (401,403,404,429) else 502
                    raise GitHubError(status,"GitHub asset request failed")
                data = bytearray()
                async for chunk in response.aiter_bytes():
                    data.extend(chunk)
                    if len(data) > max_bytes:
                        raise GitHubError(413,"GitHub asset exceeds the media size limit")
                return {"data":bytes(data),"etag":response.headers.get("etag")}
        except httpx.HTTPError:
            raise GitHubError(502,"GitHub asset download is unavailable") from None
