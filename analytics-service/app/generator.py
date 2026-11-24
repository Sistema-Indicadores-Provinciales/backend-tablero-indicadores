"""Authenticated sources, Google Sheets and saved chart workspaces."""
import json
import hashlib
import os
import re
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from time import monotonic
from typing import Annotated, Literal
from uuid import UUID, uuid4
from urllib.parse import urlparse, quote

import httpx
import jwt
from dotenv import load_dotenv
from fastapi import APIRouter, Depends, File, Header, HTTPException, UploadFile
from pydantic import BaseModel, Field, ConfigDict, SecretStr, ValidationError, field_validator, model_validator
from pymongo import MongoClient
from pymongo.errors import PyMongoError
from starlette.concurrency import run_in_threadpool

from app.data_engine import (DataError, read_rows, bounded, table, chart, filter_frame, restrict_frame,
                             filter_selection, apply_view_filters, contextual_filter_options, MAX_ROWS, MAX_COLS)
from app import google_oauth
from app.cors_origins import cors_origins
from app.workspace_access import current_user, owned_workspace, ensure_menu, publication_options, publish, view_workspace
from app.section_access import (create_section, section_options, require_admin, dashboard_for,
                                validate_destination, target_section)
from app.google_public import parse_link, read_public_rows, SHEET_NAME
from app.formosa_boundary import load_formosa_boundary
from app.section_snapshots import SectionSnapshot, snapshots

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

@router.get("/maps/formosa-boundary")
def formosa_boundary(_owner=Depends(user)):
    return load_formosa_boundary(db())

def source_for(source_id, owner):
    doc = db().analytics_sources.find_one({"_id": source_id, "$or": [{"owner": owner}, {"kind": "system"}]})
    if not doc:
        raise HTTPException(404, "La fuente no existe o no tenés acceso.")
    return doc

def local_path(doc):
    base = SYSTEM if doc.get("kind") == "system" else UPLOADS
    try:
        relative = doc.get("path")
        if isinstance(relative, str) and relative:
            path = (base / relative).resolve()
            if path.is_relative_to(base) and path.is_file():
                with path.open("rb"):
                    return path
    except (OSError, ValueError, RuntimeError):
        pass
    raise HTTPException(404, "El archivo no está disponible en este servidor. Volvé a subirlo o elegí otra fuente de datos.")

@router.get("/sources")
def sources(owner=Depends(user)):
    for path in SYSTEM.rglob("*"):
        if path.is_file() and path.suffix.lower() in {".xlsx", ".xls", ".xlsm", ".csv", ".tsv"}:
            relative = path.relative_to(SYSTEM).as_posix()
            db().analytics_sources.update_one({"_id": "system-" + hashlib.sha256(relative.encode()).hexdigest()[:24]}, {"$setOnInsert": {"name": path.name, "kind": "system", "path": relative}}, upsert=True)
    available = []
    for doc in db().analytics_sources.find({"$or": [{"owner": owner}, {"kind": "system"}]}):
        if doc.get("kind") != "google":
            try:
                local_path(doc)
            except HTTPException as exc:
                if exc.status_code != 404:
                    raise
                # Keep metadata and saved dashboards intact if a volume is temporarily absent.
                continue
        available.append(public(doc))
        if len(available) == 500:
            break
    return available

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

def google_get(source, token, range_name=None, *, row_range=None):
    key = os.getenv("GOOGLE_SHEETS_API_KEY", "")
    if not token and not key:
        raise HTTPException(409, "Para leer esta hoja, conectá tu cuenta desde el panel Google Sheets. Si la conexión no está habilitada, consultá la configuración con un administrador.")
    sid = source["spreadsheet_id"]
    url = f"https://sheets.googleapis.com/v4/spreadsheets/{sid}"
    params = {"key": key} if not token else {}
    if range_name is None:
        params["fields"] = "properties(title),sheets(properties(title,gridProperties(rowCount,columnCount)))"
    else:
        a1 = "'" + range_name.replace("'", "''") + "'"
        if row_range is not None:
            start, end, width = row_range
            if not (1 <= start <= end <= 1_000_000 and 1 <= width <= MAX_COLS):
                raise DataError('Rango de lectura no válido.')
            from openpyxl.utils import get_column_letter
            a1 += f'!A{start}:{get_column_letter(width)}{end}'
        url += "/values/" + quote(a1, safe="")
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

class TextPart(BaseModel):
    model_config = ConfigDict(extra='forbid')
    column: str = Field(default='', max_length=500)
    literal: str = Field(default='', max_length=500)
    start: int = Field(default=1, ge=1, le=10000)
    length: int | None = Field(default=None, ge=0, le=10000)
    case: str = Field(default='none', pattern=r'^(none|upper|lower)$')


class AggregateJoin(BaseModel):
    """One bounded, pre-aggregated join within the already authorized workbook."""
    model_config = ConfigDict(extra='forbid')
    sheet: str = Field(min_length=1, max_length=200)
    header_row: int = Field(default=1, ge=0, le=100000)
    decimal: str = Field(default=',', pattern=r'^[,.]$')
    types: dict[str, str] = Field(default_factory=dict)
    keys: list[Annotated[str, Field(min_length=1, max_length=500)]] = Field(min_length=1, max_length=10)
    right_dimensions: list[Annotated[str, Field(min_length=1, max_length=500)]] = Field(default_factory=list, max_length=10)
    left_sums: list[Annotated[str, Field(min_length=1, max_length=500)]] = Field(min_length=1, max_length=20)
    right_sums: dict[Annotated[str, Field(min_length=1, max_length=500)], Annotated[str, Field(min_length=1, max_length=500)]] = Field(min_length=1, max_length=20)


class GoogleSummary(BaseModel):
    model_config = ConfigDict(extra='forbid')
    dimensions: list[Annotated[str, Field(min_length=1, max_length=500)]] = Field(min_length=1, max_length=20)
    counts: dict[Annotated[str, Field(min_length=1, max_length=500)], Annotated[str, Field(min_length=1, max_length=500)]] = Field(default_factory=dict, max_length=20)
    max_values: dict[Annotated[str, Field(min_length=1, max_length=500)], Annotated[str, Field(min_length=1, max_length=500)]] = Field(default_factory=dict, max_length=10)
    row_count: str = Field(default='Registros', min_length=1, max_length=500)


class ReadConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sheet: str = Field(min_length=1, max_length=200)
    header_row: int = Field(default=1, ge=0, le=100000)
    decimal: str = Field(default=",", pattern=r"^[,.]$")
    types: dict[str, str] = Field(default_factory=dict)
    computed_columns: dict[Annotated[str, Field(min_length=1, max_length=500)], list[TextPart]] = Field(default_factory=dict, max_length=30)
    aggregate_join: AggregateJoin | None = None
    google_summary: GoogleSummary | None = None

    @model_validator(mode='after')
    def exclusive_reads(self):
        if self.aggregate_join and self.google_summary:
            raise ValueError('La combinación de pestañas y el resumen por bloques son lecturas diferentes.')
        return self

    @field_validator('computed_columns')
    @classmethod
    def bounded_expressions(cls, definitions):
        if any(not parts or len(parts) > 10 for parts in definitions.values()):
            raise ValueError('Cada fórmula necesita entre 1 y 10 partes de texto.')
        return definitions


class SortConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    field: str = Field(min_length=1, max_length=500)
    aggregation: str = Field(default="alphabetical", pattern=r"^(alphabetical|sum|max|min|count|distinct)$")
    direction: str = Field(default="asc", pattern=r"^(asc|desc)$")
    tie_order: list[Annotated[str, Field(max_length=2000)]] = Field(default_factory=list, max_length=500)
    tie_breaker: Literal['alphabetical', 'source_heap'] = 'alphabetical'


class FilterRule(BaseModel):
    model_config = ConfigDict(extra="forbid")
    column: str = Field(min_length=1, max_length=500)
    operator: str = Field(pattern=r"^(equal|not_equal|contains|not_contains|empty|not_empty|in|greater_equal)$")
    value: str = Field(default="", max_length=2000)
    values: list[Annotated[str, Field(max_length=2000)]] = Field(default_factory=list, max_length=100)


class IndicatorCondition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    column: str = Field(min_length=1, max_length=500)
    minimum: float = Field(allow_inf_nan=False)


class MetricCondition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    column: str = Field(min_length=1, max_length=500)
    excluded_value: str = Field(min_length=1, max_length=2000)


class IndicatorLookup(BaseModel):
    model_config = ConfigDict(extra="forbid")
    column: str = Field(min_length=1, max_length=500)
    values: dict[Annotated[str, Field(max_length=2000)], Annotated[float, Field(allow_inf_nan=False)]] = Field(max_length=100)


class IndicatorRatio(BaseModel):
    model_config = ConfigDict(extra="forbid")
    denominator: str = Field(min_length=1, max_length=500)
    scale: float = Field(default=100, allow_inf_nan=False)


class MetricRatio(IndicatorRatio):
    subtract_denominator: bool = False
    series_order: Literal['asc', 'desc'] | None = None


class MapLocation(BaseModel):
    model_config = ConfigDict(extra='forbid')
    lat: float = Field(ge=-90, le=90, allow_inf_nan=False)
    lng: float = Field(ge=-180, le=180, allow_inf_nan=False)
    name: str = Field(max_length=500)
    reference: str = Field(max_length=1000)
    note: str = Field(default='', max_length=1000)


class MapLookup(BaseModel):
    model_config = ConfigDict(extra='forbid')
    column: str = Field(min_length=1, max_length=500)
    locations: dict[Annotated[str, Field(min_length=1, max_length=500)], MapLocation] = Field(max_length=2000)
    category_col: str = Field(default='', max_length=500)
    sized: bool = True


class ChartConfig(ReadConfig):
    chart_type: str = "bar"
    aggregation: str = "sum"
    x_col: str = ""
    y_col: str = ""
    y_cols: list[Annotated[str, Field(min_length=1, max_length=500)]] = Field(default_factory=list, max_length=19)
    pie_label_mode: str = Field(default="percent", pattern=r"^(percent|value)$")
    group_col: str = ""
    date_bucket: str = "none"
    filters: dict[str, list[str]] = Field(default_factory=dict)
    filter_rules: list[FilterRule] = Field(default_factory=list, max_length=50)
    count_col: str = Field(default="", max_length=500)
    indicator_condition: IndicatorCondition | None = None
    indicator_rules: list[FilterRule] = Field(default_factory=list, max_length=50)
    indicator_sum_rules: list[FilterRule] = Field(default_factory=list, max_length=50)
    indicator_addends: list[Annotated[str, Field(min_length=1, max_length=500)]] = Field(default_factory=list, max_length=19)
    indicator_text_column: str = Field(default='', max_length=500)
    indicator_single_value_columns: list[str] = Field(default_factory=list, max_length=20)
    value_format: Literal['number', 'percent', 'percent_points'] = 'number'
    indicator_divisor: float = Field(default=1, gt=0, allow_inf_nan=False)
    indicator_rounding: str = Field(default='none', pattern=r'^(none|floor|ceil)$')
    indicator_lookup: IndicatorLookup | None = None
    indicator_ratio: IndicatorRatio | None = None
    indicator_decimals: int | None = Field(default=None, ge=0, le=6)
    indicator_suffix: str = Field(default='', max_length=20)
    indicator_empty: Literal['missing', 'zero'] = 'missing'
    metric_condition: MetricCondition | None = None
    metric_ratio: MetricRatio | None = None
    table_mode: str = Field(default='rows', pattern=r'^(rows|summary)$')
    table_dimensions: list[Annotated[str, Field(min_length=1, max_length=500)]] = Field(default_factory=list, max_length=20)
    table_sorts: list[SortConfig] = Field(default_factory=list, max_length=5)
    preserve_empty_categories: bool = False
    category_limit: int | None = Field(default=None, ge=2, le=500)
    include_other: bool = False
    series_limit: int | None = Field(default=None, ge=1, le=50)
    category_sort: SortConfig | None = None
    series_sort: SortConfig | None = None
    latitude_col: str = Field(default="", max_length=500)
    longitude_col: str = Field(default="", max_length=500)
    label_col: str = Field(default="", max_length=500)
    size_col: str = Field(default="", max_length=500)
    map_mode: str = Field(default="markers", pattern=r"^(markers|circles)$")
    map_metric_cols: list[Annotated[str, Field(min_length=1, max_length=500)]] = Field(default_factory=list, max_length=20)
    map_lookup: MapLookup | None = None

def load_table(source_id, owner, config, google_token):
    doc = source_for(source_id, owner)
    if doc["kind"] == "google" and doc.get("access_mode") != "public":
        google_token = google_token or google_oauth.access_token(db(), owner)
    return table_from_source(doc, config, google_token)

def table_from_source(doc, config, google_token):
    if config.google_summary:
        if doc.get('kind') != 'google' or doc.get('access_mode') == 'public':
            raise DataError('Para leer por bloques, conectá una cuenta con acceso al archivo de Google Sheets.')
        from .google_summary import summarize_google
        from .calculated_columns import calculated_columns
        frame, metadata = summarize_google(doc, config, google_token, google_get)
        return calculated_columns(frame, metadata, config.model_dump()['computed_columns'])
    if config.aggregate_join:
        if doc.get('access_mode') == 'public':
            raise DataError('Para combinar pestañas, conectá una cuenta con acceso al archivo completo.')
        from .aggregate_join import aggregate_join
        left_config = ReadConfig.model_validate(config.model_dump(include={'sheet', 'header_row', 'decimal', 'types'}))
        left, left_meta = table_from_source(doc, left_config, google_token)
        right_config = ReadConfig.model_validate(config.aggregate_join.model_dump(include={'sheet', 'header_row', 'decimal', 'types'}))
        right, right_meta = table_from_source(doc, right_config, google_token)
        frame, metadata = aggregate_join(left, left_meta, right, right_meta, config.aggregate_join.model_dump())
        from .calculated_columns import calculated_columns
        return calculated_columns(frame, metadata, config.model_dump()['computed_columns'])
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
    from .calculated_columns import calculated_columns
    frame, metadata = calculated_columns(frame, metadata, config.model_dump()['computed_columns'])
    metadata["raw_preview"] = raw[:20]
    if doc.get("access_mode") == "public":
        metadata["warnings"].append("Se consulta la pestaña del enlace. Google puede demorar unos minutos en reflejar cambios publicados.")
    if doc["kind"] != "google":
        metadata["warnings"].append("Las fórmulas se leen por su último resultado guardado. Si faltan valores, recalculá y guardá el Excel antes de subirlo.")
    return frame, metadata


def read_config_key(config: ReadConfig) -> str:
    """Identify a parsed table without considering chart-only settings."""
    read = config.model_dump(include={"sheet", "header_row", "decimal", "types", "computed_columns", "aggregate_join", "google_summary"})
    return json.dumps(read, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def workspace_fingerprint(workspace: dict) -> str:
    """Reject snapshots after a saved workspace changes configuration."""
    payload = {
        "source_id": workspace.get("source_id"),
        "widgets": [{"id": item.get("id"), "source_id": item.get("source_id"), "config": item.get("config")} for item in workspace.get("widgets", [])],
        "filter_columns": workspace.get("filter_columns"),
        "filter_sorts": workspace.get("filter_sorts"),
        "filter_option_rules": workspace.get("filter_option_rules"),
        "column_aliases": workspace.get("column_aliases"),
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


def workspace_view_payload(workspace: dict, source: dict, owner: str) -> dict:
    sources = [source, *[source_for(identifier, workspace["owner"]) for identifier in {
        widget.get("source_id") for widget in workspace.get("widgets", [])
        if widget.get("source_id") and widget["source_id"] != workspace["source_id"]
    }]]
    return {
        **public(workspace),
        "can_edit": workspace["owner"] == owner,
        "source_kind": source["kind"],
        "source_access_mode": source.get("access_mode"),
        "requires_google": any(item["kind"] == "google" and item.get("access_mode") != "public" for item in sources),
    }


def validate_view_filters(filters: dict) -> None:
    """Keep viewer filter requests bounded before calculating a section."""
    try:
        for key, selection in filters.items():
            _, values = filter_selection(selection)
            if len(key) > 500 or len(values) > 100 or any(len(value) > 2000 for value in values):
                raise HTTPException(422, "La selección de filtros es demasiado extensa.")
    except DataError as exc:
        raise HTTPException(422, str(exc)) from exc


def widget_read_key(widget: dict) -> str:
    read_key = read_config_key(ChartConfig.model_validate(widget["config"]))
    return json.dumps([widget["source_id"], read_key]) if widget.get("source_id") else read_key


def load_section_snapshot(workspace: dict, source: dict, viewer: str, google_token: str) -> SectionSnapshot:
    """Read each distinct sheet configuration once and retain no credential."""
    sources = {workspace["source_id"]: source}
    tables: dict[str, tuple[object, dict]] = {}
    for widget in workspace.get("widgets", []):
        config = ChartConfig.model_validate(widget["config"])
        key = widget_read_key(widget)
        if key in tables:
            continue
        identifier = widget.get("source_id") or workspace["source_id"]
        if identifier not in sources:
            sources[identifier] = source_for(identifier, workspace["owner"])
        widget_source = sources[identifier]
        if widget_source["kind"] == "google" and widget_source.get("access_mode") != "public":
            google_token = google_token or google_oauth.access_token(db(), viewer)
        frame, metadata = table_from_source(widget_source, config, google_token)
        metadata.pop("raw_preview", None)
        tables[key] = (frame, metadata)
    return SectionSnapshot(uuid4().hex, workspace["_id"], viewer, workspace_fingerprint(workspace), tables, monotonic())


def section_allowed_columns(workspace: dict, snapshot: SectionSnapshot) -> set[str]:
    configured = workspace.get("filter_columns")
    if configured is not None:
        return set(configured)
    allowed: set[str] = set()
    for saved in workspace.get("widgets", []):
        config = ChartConfig.model_validate(saved["config"])
        frame, _ = snapshot.tables[widget_read_key(saved)]
        if config.chart_type == "table":
            allowed.update(frame.columns)
        allowed.update([
            config.x_col, config.y_col, *config.y_cols, config.group_col,
            config.latitude_col, config.longitude_col, config.label_col, config.size_col,
            *config.map_metric_cols, *config.filters,
            *([config.map_lookup.column, config.map_lookup.category_col] if config.map_lookup else []),
        ])
    allowed.discard("")
    return allowed


def render_snapshot_widget(widget: dict, snapshot: SectionSnapshot, filters: dict, allowed: set[str], filter_sorts: dict, filter_option_rules: list | None = None, options_cache: dict | None = None) -> dict:
    """Calculate one widget from an already loaded table."""
    config = ChartConfig.model_validate(widget["config"])
    frame, metadata = snapshot.tables[widget_read_key(widget)]
    frame = restrict_frame(frame, config.model_dump())
    applicable = {column: selection for column, selection in filters.items() if column in frame.columns}
    # Control restrictions limit available choices, not a chart's measurements.
    # This cache lives only for this render/viewer/filter selection. Only charts
    # with identical sources, parsing and saved restrictions share their facets.
    option_key = (widget_read_key(widget), json.dumps(config.model_dump(include={'filters', 'filter_rules'}), sort_keys=True))
    options = options_cache.get(option_key) if options_cache is not None else None
    if options is None:
        option_frame = restrict_frame(frame, {"filter_rules": filter_option_rules or []})
        options = contextual_filter_options(option_frame, allowed, applicable, filter_sorts, 5000 if config.google_summary else 100)
        for column, option in options.items():
            option["type"] = metadata["column_meta"][column]["type"]
        if options_cache is not None:
            options_cache[option_key] = options
    filtered_frame = apply_view_filters(frame, applicable)
    result = chart(filtered_frame, {**config.model_dump(), "filters": {}, "filter_rules": []})
    if metadata.get('row_count_column'):
        result['filtered_rows'] = int(filtered_frame[metadata['row_count_column']].sum())
    result["filter_options"] = options
    result["ignored_filters"] = [column for column, selection in filters.items()
                                 if filter_selection(selection)[1] and column not in frame.columns]
    result["warnings"] = metadata["warnings"] + result.get("warnings", [])
    return result


def merge_section_filter_options(rendered: dict[str, dict], filters: dict) -> dict:
    """Publish one coherent set of contextual choices for the shared filter bar."""
    merged, ranks = {}, {}
    for item in rendered.values():
        data = item.get("data")
        if not data:
            continue
        ignored_rank = len(data.get("ignored_filters", []))
        for column, option in data.get("filter_options", {}).items():
            rank = (ignored_rank, 0 if option.get("sort_applied") else 1)
            if column in ranks and rank > ranks[column]:
                continue
            if ranks.get(column) != rank:
                merged.pop(column, None)
            ranks[column] = rank
            previous = merged.get(column, {})
            values = list(dict.fromkeys([*previous.get("values", []), *option.get("values", [])]))
            _, selected = filter_selection(filters.get(column, []))
            available = option.get("available_selected") or [value for value in selected if value in option.get("values", [])]
            merged[column] = {
                "values": values[:max(previous.get('value_limit', 100), option.get('value_limit', 100))],
                "value_limit": max(previous.get('value_limit', 100), option.get('value_limit', 100)),
                "total": max(len(values), previous.get("total", 0), option.get("total", 0)),
                "unfiltered_total": max(previous.get("unfiltered_total", 0), option.get("unfiltered_total", 0)),
                "type": option.get("type"),
                "sort_applied": option.get("sort_applied", previous.get("sort_applied", False)),
                "available_selected": list(dict.fromkeys([*previous.get("available_selected", []), *available])),
            }
    return merged


def render_section_snapshot(workspace: dict, snapshot: SectionSnapshot, filters: dict) -> tuple[dict, dict]:
    """Render all widgets while isolating a chart-specific data error."""
    allowed = section_allowed_columns(workspace, snapshot)
    if any(column not in allowed for column in filters):
        raise HTTPException(422, "Solo podés filtrar por los campos habilitados en esta sección.")
    rendered: dict[str, dict] = {}
    options_cache = {}
    for widget in workspace.get("widgets", []):
        try:
            rendered[widget["id"]] = {"data": render_snapshot_widget(widget, snapshot, filters, allowed,
                workspace.get("filter_sorts", {}), workspace.get("filter_option_rules", []), options_cache)}
        except (DataError, ValidationError, ValueError) as exc:
            rendered[widget["id"]] = {"error": str(exc) or "No se pudo generar este gráfico."}
    return rendered, merge_section_filter_options(rendered, filters)

@router.post("/sources/{source_id}/preview")
def preview(source_id: str, config: ReadConfig, owner=Depends(user), x_google_access_token: str = Header(default="")):
    return load_table(source_id, owner, config, x_google_access_token)[1]

@router.post("/sources/{source_id}/chart")
def generate(source_id: str, config: ChartConfig, owner=Depends(user), x_google_access_token: str = Header(default="")):
    frame, metadata = load_table(source_id, owner, config, x_google_access_token)
    result = chart(frame, config.model_dump())
    if metadata.get('row_count_column'):
        result['filtered_rows'] = int(restrict_frame(frame, config.model_dump())[metadata['row_count_column']].sum())
    result["warnings"] = metadata["warnings"] + result.get("warnings", [])
    return result

class Widget(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(max_length=100)
    title: str = Field(default="Gráfico", max_length=200)
    width: int = Field(default=12, ge=6, le=12)
    library: str = Field(default="Plotly", pattern=r"^(Plotly|ECharts)$")
    source_id: str | None = Field(default=None, min_length=1, max_length=500)
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
    filter_columns: list[Annotated[str, Field(min_length=1, max_length=500)]] | None = Field(default=None, max_length=20)
    column_aliases: dict[str, str] = Field(default_factory=dict, max_length=MAX_COLS)
    filter_sorts: dict[str, SortConfig] = Field(default_factory=dict, max_length=20)
    filter_option_rules: list[FilterRule] = Field(default_factory=list, max_length=50)
    destination: Destination | None = None
    default_filters: dict[Annotated[str, Field(min_length=1, max_length=500)], list[Annotated[str, Field(max_length=2000)]]] = Field(default_factory=dict, max_length=20)

    @field_validator('default_filters')
    @classmethod
    def validate_defaults(cls, values):
        if any(len(selected) > 100 for selected in values.values()):
            raise ValueError('Cada filtro inicial admite hasta 100 valores.')
        return values

    @field_validator("column_aliases")
    @classmethod
    def validate_aliases(cls, aliases: dict[str, str]) -> dict[str, str]:
        normalized, labels = {}, set()
        for column, alias in aliases.items():
            column, alias = column.strip(), alias.strip()
            if not column or len(column) > 500 or not alias or len(alias) > 200:
                raise ValueError("Los nombres visibles de las columnas no son válidos.")
            key = alias.casefold()
            if key in labels:
                raise ValueError("Los nombres visibles de las columnas no pueden repetirse.")
            labels.add(key)
            normalized[column] = alias
        return normalized

    @field_validator("filter_sorts")
    @classmethod
    def validate_filter_sort_columns(cls, sorts: dict[str, SortConfig]) -> dict[str, SortConfig]:
        if any(not column.strip() or len(column) > 500 for column in sorts):
            raise ValueError("El campo configurado para ordenar un filtro no es válido.")
        return sorts

@router.get("/workspaces")
def workspaces(owner=Depends(user)):
    return [public(d) for d in db().analytics_workspaces.find({"owner": owner, "deletedAt": None}).limit(200)]

@router.post("/workspaces")
def save_workspace(config: Workspace, owner=Depends(user)):
    source_for(config.source_id, owner)
    for identifier in {widget.source_id for widget in config.widgets if widget.source_id}:
        source_for(identifier, owner)
    if config.destination:
        validate_destination(db(), config.destination.model_dump(), current_user(db(), owner))
    doc = {"_id": uuid4().hex, "owner": owner, **config.model_dump(exclude_none=True)}
    db().analytics_workspaces.insert_one(doc)
    return public(doc)

@router.put("/workspaces/{workspace_id}")
def update_workspace(workspace_id: str, config: Workspace, owner=Depends(user)):
    source_for(config.source_id, owner)
    for identifier in {widget.source_id for widget in config.widgets if widget.source_id}:
        source_for(identifier, owner)
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
    snapshots.discard_workspace(workspace_id)
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
    source = source_for(workspace["source_id"], workspace["owner"])
    return workspace_view_payload(workspace, source, owner)


@router.get("/workspaces/{workspace_id}/widgets/{widget_id}/chart")
def saved_chart(workspace_id: str, widget_id: str, owner=Depends(user), x_google_access_token: str = Header(default="")):
    return render_saved_chart(workspace_id, widget_id, owner, x_google_access_token, {})


class ViewFilters(BaseModel):
    model_config = ConfigDict(extra="forbid")
    filters: dict[str, list[str] | dict[str, list[str]]] = Field(default_factory=dict, max_length=MAX_COLS)


class SectionRenderRequest(ViewFilters):
    snapshot_id: str | None = Field(default=None, min_length=1, max_length=100)
    refresh: bool = False


@router.post("/workspaces/{workspace_id}/widgets/{widget_id}/chart")
def filtered_saved_chart(workspace_id: str, widget_id: str, body: ViewFilters, owner=Depends(user),
                         x_google_access_token: str = Header(default="")):
    validate_view_filters(body.filters)
    return render_saved_chart(workspace_id, widget_id, owner, x_google_access_token, body.filters)


@router.post("/workspaces/{workspace_id}/render")
def render_workspace_section(workspace_id: str, body: SectionRenderRequest, owner=Depends(user),
                             x_google_access_token: str = Header(default="")):
    """Render the section without rereading its spreadsheet on filter changes."""
    validate_view_filters(body.filters)
    workspace = view_workspace(db(), workspace_id, owner)
    source = source_for(workspace["source_id"], workspace["owner"])
    fingerprint = workspace_fingerprint(workspace)
    snapshot = None
    if not body.refresh and body.snapshot_id:
        snapshot = snapshots.get(body.snapshot_id, workspace_id, owner, fingerprint)
        if snapshot is None:
            raise HTTPException(409, "Los datos temporales de esta vista ya no están disponibles. Presioná Actualizar datos.")
    if snapshot is None:
        # Keep the previous data if a manual refresh cannot read the source.
        snapshot = load_section_snapshot(workspace, source, owner, x_google_access_token)
        snapshots.put(snapshot)
        if body.refresh:
            snapshots.discard(body.snapshot_id)
    widgets, filter_options = render_section_snapshot(workspace, snapshot, body.filters)
    return {
        "snapshot_id": snapshot.snapshot_id,
        "workspace": workspace_view_payload(workspace, source, owner),
        "widgets": widgets,
        "filter_options": filter_options,
    }


def render_saved_chart(workspace_id, widget_id, owner, google_token, filters):
    workspace = view_workspace(db(), workspace_id, owner)
    widget = next((w for w in workspace["widgets"] if w["id"] == widget_id), None)
    if widget is None:
        raise HTTPException(404, "Este gráfico ya no forma parte del tablero. Actualizá la página.")
    # Only additional filters are accepted. A viewer cannot change the chart, sheet or saved restrictions.
    source = source_for(widget.get("source_id") or workspace["source_id"], workspace["owner"])
    config = ChartConfig.model_validate(widget["config"])
    if source["kind"] == "google" and source.get("access_mode") != "public":
        google_token = google_token or google_oauth.access_token(db(), owner)
    frame, metadata = table_from_source(source, config, google_token)
    frame = restrict_frame(frame, config.model_dump())
    configured_columns = workspace.get("filter_columns")
    if configured_columns is not None:
        allowed = set(configured_columns)
    else:
        # Older sections share their existing chart fields until the author chooses the controls.
        allowed = set(frame.columns) if config.chart_type == "table" else set()
        for saved in workspace["widgets"]:
            cfg = saved["config"]
            allowed.update([
                cfg.get("x_col", ""), cfg.get("y_col", ""), *cfg.get("y_cols", []), cfg.get("group_col", ""),
                cfg.get("latitude_col", ""), cfg.get("longitude_col", ""), cfg.get("label_col", ""), cfg.get("size_col", ""),
                *cfg.get("map_metric_cols", []), *cfg.get("filters", {}),
                *([cfg['map_lookup']['column'], cfg['map_lookup'].get('category_col', '')] if cfg.get('map_lookup') else []),
            ])
    if any(column not in allowed for column in filters):
        raise HTTPException(422, "Solo podés filtrar por los campos habilitados en esta sección.")
    applicable = {column: selection for column, selection in filters.items() if column in frame.columns}
    option_frame = restrict_frame(frame, {"filter_rules": workspace.get("filter_option_rules", [])})
    options = contextual_filter_options(option_frame, allowed, applicable, workspace.get("filter_sorts", {}), 5000 if config.google_summary else 100)
    for column, option in options.items():
        option['type'] = metadata['column_meta'][column]['type']
    filtered_frame = apply_view_filters(frame, applicable)
    result = chart(filtered_frame, {**config.model_dump(), "filters": {}, "filter_rules": []})
    if metadata.get('row_count_column'):
        result['filtered_rows'] = int(filtered_frame[metadata['row_count_column']].sum())
    result["filter_options"] = options
    result["ignored_filters"] = [column for column, selection in filters.items()
                                 if filter_selection(selection)[1] and column not in frame.columns]
    result["warnings"] = metadata["warnings"] + result.get("warnings", [])
    return result
