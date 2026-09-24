"""Authenticated sources, Google Sheets and saved chart workspaces."""
import json
import hashlib
import os
import re
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from uuid import UUID, uuid4
from urllib.parse import urlparse, quote

import httpx
import jwt
from dotenv import load_dotenv
from fastapi import APIRouter, Depends, File, Header, HTTPException, UploadFile
from pydantic import BaseModel, Field, ConfigDict, SecretStr
from pymongo import MongoClient
from pymongo.errors import PyMongoError
from starlette.concurrency import run_in_threadpool

from app.data_engine import DataError, read_rows, bounded, table, chart, filter_frame, filter_value, MAX_ROWS, MAX_COLS
from app import google_oauth
from app.cors_origins import cors_origins
from app.workspace_access import current_user, owned_workspace, ensure_menu, publication_options, publish, view_workspace
from app.section_access import (create_section, section_options, require_admin, dashboard_for,
                                validate_destination, target_section)
from app.google_public import parse_link, read_public_rows, SHEET_NAME

BASE = Path(__file__).resolve().parents[1]
load_dotenv(BASE.parent / ".env", override=False)
UPLOADS = Path((os.getenv("ANALYTICS_STORAGE") or str(BASE / "storage"))).resolve()
SYSTEM = (BASE / "data").resolve()
MAX_BYTES = 25 * 1024 * 1024
router = APIRouter(prefix="/v2")

@lru_cache
def db():
    uri = os.getenv("MONGODB_URL")
    if not uri:
        raise HTTPException(503, "Falta configurar la conexión a la base de datos.")
    client = MongoClient(uri, serverSelectionTimeoutMS=5000)
    return client.get_default_database(default="Indicadores")

def user(authorization: str = Header(default="")):
    secret = os.getenv("JWT_SECRET")
    if not secret:
        raise HTTPException(503, "Falta configurar JWT_SECRET en el servidor.")
    try:
        scheme, token = authorization.split(" ", 1)
        if scheme != "Bearer":
            raise ValueError()
        payload = jwt.decode(token, secret, algorithms=["HS256"], options={"require": ["exp", "sub"]})
        if payload.get("tokenUse") != "access":
            raise ValueError()
        return str(payload["sub"])
    except (ValueError, jwt.InvalidTokenError):
        raise HTTPException(401, "La sesión venció. Volvé a ingresar.")

def public(doc):
    return {k: v for k, v in doc.items() if k not in {"path", "owner", "menu_initialized"}}

def source_for(source_id, owner):
    doc = db().analytics_sources.find_one({"_id": source_id, "$or": [{"owner": owner}, {"kind": "system"}]})
    if not doc:
        raise HTTPException(404, "La fuente no existe o no tenés acceso.")
    return doc

def local_path(doc):
    base = SYSTEM if doc["kind"] == "system" else UPLOADS
    path = (base / doc["path"]).resolve()
    if not path.is_relative_to(base) or not path.is_file():
        raise HTTPException(404, "El archivo no está disponible en este servidor. Revisá el volumen de datos o volvé a subirlo.")
    return path

@router.get("/sources")
def sources(owner=Depends(user)):
    for path in SYSTEM.rglob("*"):
        if path.is_file() and path.suffix.lower() in {".xlsx", ".xls", ".xlsm", ".csv", ".tsv"}:
            relative = path.relative_to(SYSTEM).as_posix()
            db().analytics_sources.update_one({"_id": "system-" + hashlib.sha256(relative.encode()).hexdigest()[:24]}, {"$setOnInsert": {"name": path.name, "kind": "system", "path": relative}}, upsert=True)
    return [public(d) for d in db().analytics_sources.find({"$or": [{"owner": owner}, {"kind": "system"}]}).limit(500)]

@router.post("/sources/upload")
async def upload(file: UploadFile = File(...), owner=Depends(user)):
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in {".xlsx", ".xls", ".xlsm", ".csv", ".tsv"}:
        raise HTTPException(400, "Subí un XLSX, XLSM, XLS, CSV o TSV.")
    UPLOADS.mkdir(parents=True, exist_ok=True)
    source_id = uuid4().hex
    path = UPLOADS / (source_id + suffix)
    size = 0
    try:
        with path.open("xb") as stream:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_BYTES:
                    raise HTTPException(413, "El archivo supera 25 MB.")
                stream.write(chunk)
        if not size:
            raise DataError("El archivo está vacío.")
        sheets = await run_in_threadpool(read_rows, path)
        doc = {"_id": source_id, "owner": owner, "name": (file.filename or "Archivo")[:200], "kind": "upload", "path": path.name, "sheets": sheets, "created": datetime.now(timezone.utc).isoformat()}
        await run_in_threadpool(db().analytics_sources.insert_one, doc)
        return public(doc)
    except Exception as exc:
        path.unlink(missing_ok=True)
        if isinstance(exc, (HTTPException, DataError, PyMongoError)):
            raise
        raise DataError("No se pudo abrir el archivo. Revisá el formato, la contraseña o si está dañado.") from exc
    finally:
        await file.close()

class GoogleSource(BaseModel):
    url: str = Field(min_length=1, max_length=500)
    name: str = Field(default="Google Sheets", max_length=200)

def spreadsheet_id(value):
    if re.fullmatch(r"[A-Za-z0-9_-]{20,200}", value):
        return value
    parsed = urlparse(value)
    match = re.match(r"/spreadsheets/d/([A-Za-z0-9_-]{20,200})(?:/|$)", parsed.path)
    if parsed.scheme != "https" or parsed.hostname != "docs.google.com" or not match:
        raise DataError("Pegá un enlace de Google Sheets con formato https://docs.google.com/spreadsheets/d/...")
    return match.group(1)

def google_get(source, token, range_name=None):
    key = os.getenv("GOOGLE_SHEETS_API_KEY", "")
    if not token and not key:
        raise HTTPException(409, "Para leer esta hoja, conectá tu cuenta desde el panel Google Sheets. Si la conexión no está habilitada, consultá la configuración con un administrador.")
    sid = source["spreadsheet_id"]
    url = f"https://sheets.googleapis.com/v4/spreadsheets/{sid}"
    params = {"key": key} if not token else {}
    if range_name is None:
        params["fields"] = "properties(title),sheets(properties(title,gridProperties(rowCount,columnCount)))"
    else:
        url += "/values/" + quote("'" + range_name.replace("'", "''") + "'", safe="")
        params.update({"valueRenderOption": "FORMATTED_VALUE", "majorDimension": "ROWS"})
    headers = {"Authorization": "Bearer " + token} if token else {}
    try:
        with httpx.stream("GET", url, params=params, headers=headers, timeout=30, follow_redirects=False) as response:
            if response.status_code in {401, 403, 404}:
                raise HTTPException(409, "Google no permitió leer la hoja. Conectá la cuenta que tiene acceso y verificá sus permisos.")
            if response.status_code == 429:
                raise HTTPException(429, "Google alcanzó su límite de consultas. Reintentá en unos minutos.")
            if response.status_code != 200:
                raise HTTPException(502, "Google Sheets no respondió correctamente.")
            chunks, size = [], 0
            for chunk in response.iter_bytes():
                size += len(chunk)
                if size > MAX_BYTES:
                    raise DataError("La respuesta de Google supera 25 MB. Reducí el tamaño de la hoja.")
                chunks.append(chunk)
            return json.loads(b"".join(chunks))
    except httpx.HTTPError:
        raise HTTPException(502, "No se pudo conectar con Google Sheets. Reintentá.")

@router.get("/google/status")
def google_status(owner=Depends(user)):
    return google_oauth.status(db(), owner)


class GoogleSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    client_id: str = Field(max_length=250, pattern=r"^[A-Za-z0-9_-]+\.apps\.googleusercontent\.com$")
    client_secret: SecretStr | None = None


@router.put("/google/settings")
def save_google_settings(config: GoogleSettings, owner=Depends(user)):
    account = current_user(db(), owner)
    if account.get("profileType") != "ADMIN":
        raise HTTPException(403, "Solo un administrador puede configurar la conexión con Google.")
    previous = db().analytics_settings.find_one({"_id": "google-sheets"}) or {}
    update = {
        "client_id": config.client_id, "updated_by": owner, "updated_at": datetime.now(timezone.utc),
    }
    secret = config.client_secret.get_secret_value().strip() if config.client_secret else ""
    if secret:
        if not 10 <= len(secret) <= 4000:
            raise HTTPException(422, "Revisá el secreto del cliente de Google.")
        update["client_secret"] = google_oauth.seal(secret, "client:" + config.client_id)
    elif previous.get("client_id") != config.client_id:
        update["client_secret"] = None
    db().analytics_settings.update_one({"_id": "google-sheets"}, {"$set": update}, upsert=True)
    return google_status(owner)


class GoogleCode(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: SecretStr
    client_id: str = Field(max_length=250)


@router.post("/google/connect")
def persist_google_connection(body: GoogleCode, owner=Depends(user), origin: str = Header(default=""),
                              x_requested_with: str = Header(default="")):
    # Popup authorization codes are bound to the page origin. Never accept an arbitrary redirect URI.
    if origin not in cors_origins() or x_requested_with != "XMLHttpRequest":
        raise HTTPException(403, "El origen de la conexión Google no está autorizado.")
    if body.client_id != google_oauth.credentials(db())[0]:
        raise HTTPException(409, "La configuración de Google cambió. Actualizá la página.")
    code = body.code.get_secret_value()
    if not code or len(code) > 4096:
        raise HTTPException(422, "El código de Google no es válido.")
    return google_oauth.connect(db(), owner, code, origin)


@router.delete("/google/connection")
def remove_google_connection(owner=Depends(user)):
    return google_oauth.disconnect(db(), owner)


@router.post("/sources/google")
def connect_google(body: GoogleSource, owner=Depends(user), x_google_access_token: str = Header(default="")):
    candidate = parse_link(body.url)
    try:
        read_public_rows(candidate)
        public_access = True
    except HTTPException as exc:
        if exc.status_code != 409 or candidate.get("published_id"):
            raise
        x_google_access_token = x_google_access_token or google_oauth.access_token(db(), owner)
        if not (x_google_access_token or os.getenv("GOOGLE_SHEETS_API_KEY", "").strip()):
            raise exc
        public_access = False
    if public_access:
        doc = {"_id": uuid4().hex, "owner": owner, "kind": "google", "access_mode": "public",
               "name": body.name, "public_sheet": candidate, "sheets": [SHEET_NAME]}
    else:
        info = google_get(candidate, x_google_access_token)
        doc = {"_id": uuid4().hex, "owner": owner, "kind": "google", "name": info.get("properties", {}).get("title", body.name), "spreadsheet_id": candidate["spreadsheet_id"], "sheets": [s["properties"]["title"] for s in info.get("sheets", [])]}
    # Source documents never contain Google credentials; grants belong to the requesting user.
    db().analytics_sources.insert_one(doc)
    return public(doc)

@router.get("/sources/{source_id}/sheets")
def sheets(source_id: str, owner=Depends(user), x_google_access_token: str = Header(default="")):
    doc = source_for(source_id, owner)
    if doc["kind"] == "google":
        if doc.get("access_mode") == "public":
            return [SHEET_NAME]
        info = google_get(doc, x_google_access_token or google_oauth.access_token(db(), owner))
        return [s["properties"]["title"] for s in info.get("sheets", [])]
    return read_rows(local_path(doc))

class ReadConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sheet: str = Field(min_length=1, max_length=200)
    header_row: int = Field(default=1, ge=0, le=100000)
    decimal: str = Field(default=",", pattern=r"^[,.]$")
    types: dict[str, str] = Field(default_factory=dict)

class ChartConfig(ReadConfig):
    chart_type: str = "bar"
    aggregation: str = "sum"
    x_col: str = ""
    y_col: str = ""
    group_col: str = ""
    date_bucket: str = "none"
    filters: dict[str, list[str]] = Field(default_factory=dict)

def load_table(source_id, owner, config, google_token):
    doc = source_for(source_id, owner)
    if doc["kind"] == "google" and doc.get("access_mode") != "public":
        google_token = google_token or google_oauth.access_token(db(), owner)
    return table_from_source(doc, config, google_token)

def table_from_source(doc, config, google_token):
    if doc["kind"] == "google":
        if doc.get("access_mode") == "public":
            if config.sheet != SHEET_NAME:
                raise DataError("Elegí la pestaña vinculada. Para usar otra, pegá su enlace en Google Sheets.")
            raw = read_public_rows(doc["public_sheet"])
        else:
            raw = bounded(google_get(doc, google_token, config.sheet).get("values", []))
    else:
        raw = read_rows(local_path(doc), config.sheet)
    frame, metadata = table(raw, config.header_row, config.decimal, config.types)
    metadata["raw_preview"] = raw[:20]
    if doc.get("access_mode") == "public":
        metadata["warnings"].append("Se consulta la pestaña del enlace. Google puede demorar unos minutos en reflejar cambios publicados.")
    if doc["kind"] != "google":
        metadata["warnings"].append("Las fórmulas se leen por su último resultado guardado. Si faltan valores, recalculá y guardá el Excel antes de subirlo.")
    return frame, metadata

@router.post("/sources/{source_id}/preview")
def preview(source_id: str, config: ReadConfig, owner=Depends(user), x_google_access_token: str = Header(default="")):
    return load_table(source_id, owner, config, x_google_access_token)[1]

@router.post("/sources/{source_id}/chart")
def generate(source_id: str, config: ChartConfig, owner=Depends(user), x_google_access_token: str = Header(default="")):
    frame, metadata = load_table(source_id, owner, config, x_google_access_token)
    result = chart(frame, config.model_dump())
    result["warnings"] = metadata["warnings"] + result.get("warnings", [])
    return result

class Widget(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(max_length=100)
    title: str = Field(default="Gráfico", max_length=200)
    width: int = Field(default=12, ge=6, le=12)
    library: str = Field(default="Plotly", pattern=r"^(Plotly|ECharts)$")
    config: ChartConfig

class Destination(BaseModel):
    model_config = ConfigDict(extra="forbid")
    dashboardId: str = Field(pattern=r"^[a-fA-F0-9]{24}$")
    sectionId: str = Field(pattern=r"^[a-fA-F0-9]{24}$")


class Workspace(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=200)
    source_id: str = Field(max_length=500)
    widgets: list[Widget] = Field(max_length=30)
    destination: Destination | None = None

@router.get("/workspaces")
def workspaces(owner=Depends(user)):
    return [public(d) for d in db().analytics_workspaces.find({"owner": owner, "deletedAt": None}).limit(200)]

@router.post("/workspaces")
def save_workspace(config: Workspace, owner=Depends(user)):
    source_for(config.source_id, owner)
    if config.destination:
        validate_destination(db(), config.destination.model_dump(), current_user(db(), owner))
    doc = {"_id": uuid4().hex, "owner": owner, **config.model_dump(exclude_none=True)}
    db().analytics_workspaces.insert_one(doc)
    return public(doc)

@router.put("/workspaces/{workspace_id}")
def update_workspace(workspace_id: str, config: Workspace, owner=Depends(user)):
    source_for(config.source_id, owner)
    existing = owned_workspace(db(), workspace_id, owner)
    destination = config.destination.model_dump() if config.destination else existing.get("destination")
    if destination != existing.get("destination"):
        raise HTTPException(409, "Para guardar en otra sección, creá una copia de los gráficos.")
    if destination:
        dashboard, section = target_section(db(), destination)
        if section.get("workspaceId") not in (None, workspace_id):
            raise HTTPException(409, "La sección ya tiene otra configuración. Actualizá la página.")
    body = config.model_dump(exclude_none=True)
    if destination:
        body["destination"] = destination
    result = db().analytics_workspaces.update_one({"_id": workspace_id, "owner": owner, "deletedAt": None}, {"$set": body})
    if not result.matched_count:
        raise HTTPException(404, "El tablero no existe o no tenés acceso.")
    return {"_id": workspace_id, **body}


class Publication(BaseModel):
    model_config = ConfigDict(extra="forbid")
    icon: str = Field(default="material-symbols:bar-chart-rounded", max_length=100, pattern=r"^[a-z0-9:_-]+$")
    recipientIds: list[str] | None = Field(default=None, max_length=10000)


@router.get("/publication-options")
def new_publication_options(owner=Depends(user)):
    return publication_options(db(), current_user(db(), owner))


class NewSection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=200)
    recipientIds: list[str] = Field(max_length=10000)
    requestId: UUID


@router.get("/dashboards/{dashboard_id}/section-options")
def new_section_options(dashboard_id: str, owner=Depends(user)):
    account = current_user(db(), owner)
    require_admin(account)
    dashboard = dashboard_for(db(), dashboard_id)
    return {**publication_options(db(), account), "dashboardName": dashboard.get("name") or dashboard["keyname"]}


@router.post("/dashboards/{dashboard_id}/sections")
def add_section(dashboard_id: str, config: NewSection, owner=Depends(user)):
    return create_section(db(), current_user(db(), owner), dashboard_id, config.name, config.recipientIds, config.requestId)


@router.get("/dashboards/{dashboard_id}/sections/{section_id}/options")
def selected_section_options(dashboard_id: str, section_id: str, owner=Depends(user)):
    return section_options(db(), current_user(db(), owner), {"dashboardId": dashboard_id, "sectionId": section_id})


@router.post("/workspaces/restore-menu")
def restore_menu(owner=Depends(user)):
    account = current_user(db(), owner)
    restored = 0
    skipped = 0
    for workspace in db().analytics_workspaces.find({"owner": owner, "menu_initialized": {"$ne": True}, "deletedAt": None}):
        try:
            ensure_menu(db(), workspace, account)
            restored += 1
        except HTTPException as exc:
            if exc.status_code not in {403, 404, 409}:
                raise
            skipped += 1
    return {"restored": restored, **({"skipped": skipped} if skipped else {})}


@router.get("/workspaces/{workspace_id}/publication")
def get_publication(workspace_id: str, owner=Depends(user)):
    workspace = owned_workspace(db(), workspace_id, owner)
    return publication_options(db(), current_user(db(), owner), workspace)


@router.put("/workspaces/{workspace_id}/publication")
def publish_workspace(workspace_id: str, config: Publication, owner=Depends(user)):
    workspace = owned_workspace(db(), workspace_id, owner)
    if not workspace.get("widgets"):
        raise HTTPException(422, "Agregá al menos un gráfico antes de guardar en el menú.")
    return publish(db(), workspace, current_user(db(), owner), config.icon, config.recipientIds)


@router.get("/workspaces/{workspace_id}")
def get_workspace(workspace_id: str, owner=Depends(user)):
    workspace = public(owned_workspace(db(), workspace_id, owner))
    if workspace.get("destination"):
        _, section = target_section(db(), workspace["destination"])
        workspace["name"] = section.get("name") or workspace["name"]
    else:
        dashboard = db().dashboards.find_one({"generatedWorkspaceId": workspace_id}, {"name": 1})
        if dashboard and dashboard.get("name"):
            workspace["name"] = dashboard["name"]
    return workspace


@router.get("/workspaces/{workspace_id}/view")
def workspace_view(workspace_id: str, owner=Depends(user)):
    workspace = view_workspace(db(), workspace_id, owner)
    doc = source_for(workspace["source_id"], workspace["owner"])
    return {**public(workspace), "can_edit": workspace["owner"] == owner, "source_kind": doc["kind"], "source_access_mode": doc.get("access_mode")}


@router.get("/workspaces/{workspace_id}/widgets/{widget_id}/chart")
def saved_chart(workspace_id: str, widget_id: str, owner=Depends(user), x_google_access_token: str = Header(default="")):
    return render_saved_chart(workspace_id, widget_id, owner, x_google_access_token, {})


class ViewFilters(BaseModel):
    model_config = ConfigDict(extra="forbid")
    filters: dict[str, list[str]] = Field(default_factory=dict, max_length=MAX_COLS)


@router.post("/workspaces/{workspace_id}/widgets/{widget_id}/chart")
def filtered_saved_chart(workspace_id: str, widget_id: str, body: ViewFilters, owner=Depends(user),
                         x_google_access_token: str = Header(default="")):
    if any(len(key) > 500 or len(values) > 100 or any(len(v) > 2000 for v in values) for key, values in body.filters.items()):
        raise HTTPException(422, "La selección de filtros es demasiado extensa.")
    return render_saved_chart(workspace_id, widget_id, owner, x_google_access_token, body.filters)


def render_saved_chart(workspace_id, widget_id, owner, google_token, filters):
    workspace = view_workspace(db(), workspace_id, owner)
    widget = next((w for w in workspace["widgets"] if w["id"] == widget_id), None)
    if widget is None:
        raise HTTPException(404, "Este gráfico ya no forma parte del tablero. Actualizá la página.")
    # Only additional filters are accepted. A viewer cannot change the chart, sheet or saved restrictions.
    source = source_for(workspace["source_id"], workspace["owner"])
    config = ChartConfig.model_validate(widget["config"])
    if source["kind"] == "google" and source.get("access_mode") != "public":
        google_token = google_token or google_oauth.access_token(db(), owner)
    frame, metadata = table_from_source(source, config, google_token)
    frame = filter_frame(frame, config.filters)
    allowed = set(frame.columns) if config.chart_type == "table" else set([config.x_col, config.y_col, config.group_col, *config.filters])
    if any(column not in allowed for column in filters):
        raise HTTPException(422, "Solo podés filtrar por los campos de este gráfico.")
    options = {}
    for column in frame.columns:
        if column not in allowed:
            continue
        values = list(dict.fromkeys(filter_value(v) for v in frame[column]))
        options[column] = {"values": values[:100], "total": len(values)}
    result = chart(frame, {**config.model_dump(), "filters": filters})
    result["filter_options"] = options
    result["warnings"] = metadata["warnings"] + result.get("warnings", [])
    return result
