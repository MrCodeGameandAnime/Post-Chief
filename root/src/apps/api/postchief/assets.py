import hashlib
import io
import warnings
import xml.etree.ElementTree as ET
from pathlib import Path
from uuid import uuid4
from PIL import Image, UnidentifiedImageError
from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session
from postchief.auth import Actor, current_actor, require_scope
from postchief.db import get_db
from postchief.models import Asset, Campaign
from pydantic import BaseModel, Field
from postchief.github.routes import get_service, connection, selected_repo
from postchief.github.service import GitHubService

router = APIRouter(prefix="/assets", tags=["Media"])
MAX_BYTES = 80 * 1024 * 1024


def infer_media(data: bytes):
    if data[:5] == b"<?xml" or data.lstrip().startswith(b"<svg"):
        try:
            root = ET.fromstring(data)
            if root.tag.split("}")[-1] != "svg":
                raise ValueError()
            for element in root.iter():
                if element.tag.split("}")[-1] in ("script","foreignObject","image") or any(key.lower().startswith("on") or (key.split("}")[-1] == "href" and not value.startswith("#")) for key,value in element.attrib.items()):
                    raise ValueError()
        except (ET.ParseError, ValueError):
            raise HTTPException(422,"SVG contains unsupported or active content") from None
        return "image/svg+xml", ".svg", {}
    if len(data) > 12 and data[4:8] == b"ftyp":
        return "video/mp4", ".mp4", {}
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(data)) as image:
                kind = image.format
                width, height = image.size
                image.verify()
        formats = {"PNG":("image/png",".png"),"JPEG":("image/jpeg",".jpg"),"WEBP":("image/webp",".webp"),"GIF":("image/gif",".gif")}
        if kind not in formats:
            raise ValueError()
        mime, suffix = formats[kind]
        return mime, suffix, {"width":width,"height":height}
    except (UnidentifiedImageError, ValueError, OSError, SyntaxError, Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise HTTPException(422,"Upload a valid PNG, JPEG, WebP, GIF, SVG or MP4 file") from None


def asset_output(row: Asset):
    return {"id":row.id,"name":row.name,"mime_type":row.mime_type,"byte_size":row.byte_size,"checksum":row.checksum,"source":row.source,"details":row.details,"file_url":f"/api/assets/{row.id}/file"}


def get_asset(db: Session, actor: Actor, asset_id: str):
    row = db.scalar(select(Asset).where(Asset.org_id == actor.org_id, Asset.id == asset_id))
    if not row:
        raise HTTPException(404,"Asset not found")
    return row


def save_asset(request: Request, db: Session, actor: Actor, name: str, data: bytes, source="upload", extra=None):
    if not data or len(data) > MAX_BYTES:
        raise HTTPException(413,"Media must be between 1 byte and 80 MiB")
    mime, suffix, details = infer_media(data)
    checksum = hashlib.sha256(data).hexdigest()
    # Cache by organization and content hash so private assets never cross tenants.
    existing = db.scalar(select(Asset).where(Asset.org_id == actor.org_id, Asset.checksum == checksum))
    if existing:
        return existing
    directory = Path(request.app.state.settings.media_dir).resolve()
    directory.mkdir(parents=True,exist_ok=True)
    asset_id = str(uuid4())
    path = directory / (asset_id + suffix)
    path.write_bytes(data)
    row = Asset(id=asset_id,org_id=actor.org_id,name=Path(name).name[:300] or "asset",mime_type=mime,storage_path=path.name,checksum=checksum,byte_size=len(data),source=source,details={**details,**(extra or {})})
    db.add(row)
    try:
        db.commit()
    except Exception:
        path.unlink(missing_ok=True)
        raise
    return row


def asset_path(settings, row: Asset):
    directory = Path(settings.media_dir).resolve()
    path = (directory / row.storage_path).resolve()
    if not path.is_relative_to(directory) or not path.is_file():
        raise HTTPException(404,"Media file is unavailable")
    return path


@router.post("", status_code=201)
async def upload(request: Request, file: UploadFile = File(...), actor: Actor = Depends(current_actor), db: Session = Depends(get_db)):
    require_scope(actor,"media:write")
    data = bytearray()
    while chunk := await file.read(1024*1024):
        data.extend(chunk)
        if len(data) > MAX_BYTES:
            raise HTTPException(413,"Media exceeds 80 MiB")
    return asset_output(save_asset(request,db,actor,file.filename or "asset",bytes(data)))


@router.get("")
def list_assets(actor: Actor = Depends(current_actor), db: Session = Depends(get_db)):
    require_scope(actor,"media:read")
    return [asset_output(r) for r in db.scalars(select(Asset).where(Asset.org_id == actor.org_id).order_by(Asset.created_at.desc()).limit(200))]


class GitHubAssetImport(BaseModel):
    repo: str = Field(max_length=300)
    path: str = Field(min_length=1,max_length=1000)


@router.post("/github",status_code=201)
async def import_github_asset(data: GitHubAssetImport, request: Request, actor: Actor = Depends(current_actor), db: Session = Depends(get_db), service: GitHubService = Depends(get_service)):
    require_scope(actor,"media:write")
    require_scope(actor,"github:read")
    row = connection(db,actor)
    selected_repo(row,data.repo)
    cached = db.scalar(select(Asset).where(Asset.org_id == actor.org_id,Asset.source == "github",Asset.details["github_repo"].as_string() == data.repo,Asset.details["github_path"].as_string() == data.path).order_by(Asset.created_at.desc()).limit(1))
    result = await service.read_binary(row.installation_id,data.repo,data.path,etag=cached.details.get("etag") if cached else None)
    if result["data"] is None:
        if not cached:
            raise HTTPException(502,"GitHub returned an invalid cache response")
        return asset_output(cached)
    asset = save_asset(request,db,actor,Path(data.path).name,result["data"],source="github",extra={"github_repo":data.repo,"github_path":data.path,"etag":result.get("etag")})
    if asset.source == "github" and asset.details.get("github_repo") == data.repo and asset.details.get("github_path") == data.path:
        asset.details = {**asset.details,"etag":result.get("etag")}
        db.commit()
    return asset_output(asset)


@router.get("/{asset_id}/file")
def download(asset_id: str, request: Request, actor: Actor = Depends(current_actor), db: Session = Depends(get_db)):
    require_scope(actor,"media:read")
    row = get_asset(db,actor,asset_id)
    return FileResponse(asset_path(request.app.state.settings,row),media_type=row.mime_type,filename=row.name,headers={"X-Content-Type-Options":"nosniff"})


@router.delete("/{asset_id}")
def remove_asset(asset_id: str, actor: Actor = Depends(current_actor), db: Session = Depends(get_db)):
    require_scope(actor,"media:write")
    row = get_asset(db,actor,asset_id)
    campaigns = db.scalars(select(Campaign).where(Campaign.org_id == actor.org_id))
    if any(asset_id in c.asset_ids or any(asset_id in (v.get("asset_ids") or []) for v in c.overrides.values()) for c in campaigns):
        raise HTTPException(409,"Asset is used by a campaign")
    # Retain the physical file until retention cleanup, to avoid breaking in-flight provider downloads.
    db.delete(row)
    db.commit()
    return {"deleted":True}
