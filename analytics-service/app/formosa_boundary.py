"""Obtiene y conserva el límite oficial de la provincia de Formosa."""
import json
import logging
import math
from datetime import datetime, timedelta, timezone
from urllib.parse import urljoin, urlparse

import httpx
from fastapi import HTTPException
from pymongo.errors import PyMongoError

IGN_URL = (
    "https://ide.ign.gob.ar/geoservicios/rest/services/ANIDA/org_politica/MapServer/169/query"
    "?where=FNA%3D%27Formosa%27"
    "&outFields=FNA%2CIN1"
    "&returnGeometry=true"
    "&outSR=4326"
    "&maxAllowableOffset=0.002"
    "&geometryPrecision=6"
    "&f=geojson"
)
GEOREF_URL = "https://apis.datos.gob.ar/georef/api/v2.0/provincias.geojson"
CACHE_ID = "formosa-boundary"
CACHE_TTL = timedelta(days=7)
MAX_BOUNDARY_BYTES = 2 * 1024 * 1024
REDIRECT_LIMIT = 5

logger = logging.getLogger(__name__)


class BoundarySourceError(ValueError):
    """Indica que una fuente externa no entregó un límite utilizable."""


def _allowed_source_url(url):
    try:
        parsed = urlparse(url)
        host = parsed.hostname or ""
        if parsed.username or parsed.password or parsed.port:
            return False
        if parsed.scheme == "https" and host == "ide.ign.gob.ar":
            return parsed.path == "/geoservicios/rest/services/ANIDA/org_politica/MapServer/169/query"
        if parsed.scheme == "https" and host == "apis.datos.gob.ar":
            return parsed.path == "/georef/api/v2.0/provincias.geojson"
        if parsed.scheme == "https" and host == "infra.datos.gob.ar":
            return parsed.path == "/files/latest/provincias.geojson"
        # GeoRef redirige actualmente su descarga oficial a este servidor HTTP.
        return parsed.scheme == "http" and host == "181.209.30.107" and parsed.path == "/files/latest/provincias.geojson"
    except ValueError:
        return False


def _download_json(url):
    try:
        with httpx.Client(timeout=20, follow_redirects=False, trust_env=False) as client:
            for _ in range(REDIRECT_LIMIT):
                if not _allowed_source_url(url):
                    raise BoundarySourceError("La fuente geográfica redirigió a un destino no permitido.")
                with client.stream("GET", url, headers={"Accept": "application/geo+json, application/json"}) as response:
                    if response.status_code in {301, 302, 303, 307, 308}:
                        location = response.headers.get("location")
                        if not location:
                            raise BoundarySourceError("La fuente geográfica devolvió una redirección incompleta.")
                        url = urljoin(url, location)
                        continue
                    if response.status_code != 200:
                        raise BoundarySourceError(f"La fuente geográfica respondió con estado {response.status_code}.")
                    chunks, size = [], 0
                    for chunk in response.iter_bytes():
                        size += len(chunk)
                        if size > MAX_BOUNDARY_BYTES:
                            raise BoundarySourceError("La respuesta geográfica supera el tamaño permitido.")
                        chunks.append(chunk)
                    try:
                        return json.loads(b"".join(chunks).decode("utf-8-sig"))
                    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                        raise BoundarySourceError("La fuente no devolvió un GeoJSON válido.") from exc
        raise BoundarySourceError("La fuente geográfica redirigió demasiadas veces.")
    except httpx.HTTPError as exc:
        raise BoundarySourceError("No se pudo conectar con la fuente geográfica.") from exc


def _position(value):
    if not isinstance(value, list) or len(value) < 2:
        return False
    longitude, latitude = value[:2]
    if isinstance(longitude, bool) or isinstance(latitude, bool):
        return False
    if not isinstance(longitude, (int, float)) or not isinstance(latitude, (int, float)):
        return False
    return math.isfinite(longitude) and math.isfinite(latitude) and -180 <= longitude <= 180 and -90 <= latitude <= 90


def _ring(value):
    return (isinstance(value, list) and len(value) >= 4 and all(_position(point) for point in value)
            and value[0][:2] == value[-1][:2])


def _polygon(value):
    return isinstance(value, list) and bool(value) and all(_ring(ring) for ring in value)


def _valid_geometry(geometry):
    if not isinstance(geometry, dict):
        return False
    coordinates = geometry.get("coordinates")
    if geometry.get("type") == "Polygon":
        return _polygon(coordinates)
    if geometry.get("type") == "MultiPolygon":
        return isinstance(coordinates, list) and bool(coordinates) and all(_polygon(polygon) for polygon in coordinates)
    return False


def _is_formosa(properties):
    if not isinstance(properties, dict):
        return False
    identifier = str(properties.get("id", properties.get("IN1", ""))).strip()
    names = (properties.get("nombre"), properties.get("FNA"), properties.get("nombre_completo"))
    return identifier == "34" or any(isinstance(name, str) and name.strip().casefold() in {"formosa", "provincia de formosa"} for name in names)


def _boundary(payload, source):
    if not isinstance(payload, dict) or payload.get("type") != "FeatureCollection":
        raise BoundarySourceError("La respuesta no es una colección GeoJSON.")
    features = []
    for feature in payload.get("features", []):
        if not isinstance(feature, dict) or feature.get("type") != "Feature":
            continue
        if not _is_formosa(feature.get("properties")) or not _valid_geometry(feature.get("geometry")):
            continue
        features.append({
            "type": "Feature",
            "properties": {"id": "34", "nombre": "Formosa", "fuente": source},
            "geometry": feature["geometry"],
        })
    if not features:
        raise BoundarySourceError("La fuente no devolvió el polígono de Formosa.")
    return {"type": "FeatureCollection", "features": features}


def _cached(database):
    try:
        document = database.analytics_geodata_cache.find_one({"_id": CACHE_ID})
    except PyMongoError as exc:
        logger.warning("No se pudo leer la caché geográfica: %s", exc)
        return None
    if not document:
        return None
    try:
        boundary = _boundary(document.get("geojson"), document.get("source", "cache"))
    except BoundarySourceError:
        return None
    updated_at = document.get("updated_at")
    if isinstance(updated_at, datetime) and updated_at.tzinfo is None:
        updated_at = updated_at.replace(tzinfo=timezone.utc)
    return boundary, updated_at


def _store(database, boundary, source, now):
    try:
        database.analytics_geodata_cache.replace_one(
            {"_id": CACHE_ID},
            {"_id": CACHE_ID, "geojson": boundary, "source": source, "updated_at": now},
            upsert=True,
        )
    except PyMongoError as exc:
        # El contorno sigue siendo utilizable aunque falle su persistencia.
        logger.warning("No se pudo guardar la caché geográfica: %s", exc)


def load_formosa_boundary(database, now=None):
    """Devuelve un polígono reciente o la última copia válida disponible."""
    now = now or datetime.now(timezone.utc)
    cached = _cached(database)
    if cached and isinstance(cached[1], datetime) and now - cached[1] < CACHE_TTL:
        return cached[0]

    sources = (("ign", IGN_URL), ("georef", GEOREF_URL))
    for source, url in sources:
        try:
            boundary = _boundary(_download_json(url), source)
            _store(database, boundary, source, now)
            return boundary
        except BoundarySourceError as exc:
            logger.warning("No se pudo actualizar el límite de Formosa desde %s: %s", source, exc)

    if cached:
        logger.warning("Se utiliza una copia geográfica vencida porque las fuentes oficiales no respondieron.")
        return cached[0]
    raise HTTPException(502, "No se pudo obtener el contorno oficial de Formosa. Reintentá en unos minutos.")
