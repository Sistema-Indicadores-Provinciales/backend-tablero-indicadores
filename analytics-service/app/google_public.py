"""Read an explicitly accessible Sheets tab without credentials or stored snapshots."""
import csv
import io
import re
from urllib.parse import parse_qs, urlencode, urljoin, urlparse

import httpx
from fastapi import HTTPException
from app.data_engine import DataError, bounded

MAX_BYTES = 25 * 1024 * 1024
SHEET_NAME = "Hoja vinculada"
ID = r"[A-Za-z0-9_-]{20,200}"


def parse_link(value):
    value = value.strip()
    if re.fullmatch(ID, value):
        return {"spreadsheet_id": value}
    parsed = urlparse(value)
    if parsed.scheme != "https" or parsed.netloc != "docs.google.com":
        raise DataError("Pegá un enlace de Google Sheets de docs.google.com.")
    published = re.fullmatch(r"/spreadsheets/d/e/(" + ID + r")/(?:pubhtml|pub)/?", parsed.path)
    normal = re.fullmatch(r"/spreadsheets/(?:u/\d+/)?d/(" + ID + r")(?:/(?:edit|view|preview|export|pubhtml|pub))?/?", parsed.path)
    if not published and not normal:
        raise DataError("El enlace no corresponde a una hoja de Google Sheets.")
    query = parse_qs(parsed.query)
    fragment = parse_qs(parsed.fragment)
    gids = fragment.get("gid", query.get("gid", []))
    if gids and (len(gids) != 1 or not re.fullmatch(r"\d{1,20}", gids[0])):
        raise DataError("El enlace contiene una pestaña inválida. Copiá nuevamente su dirección desde Google Sheets.")
    result = {"published_id" if published else "spreadsheet_id": (published or normal).group(1)}
    if gids:
        result["gid"] = gids[0]
    return result


def export_url(source):
    key = "published_id" if source.get("published_id") else "spreadsheet_id"
    value = source.get(key, "")
    if not re.fullmatch(ID, value):
        raise DataError("El identificador de la hoja no es válido.")
    if key == "published_id":
        url, params = f"https://docs.google.com/spreadsheets/d/e/{value}/pub", {"output": "csv"}
    else:
        url, params = f"https://docs.google.com/spreadsheets/d/{value}/export", {"format": "csv"}
    if source.get("gid") is not None:
        if not re.fullmatch(r"\d{1,20}", str(source["gid"])):
            raise DataError("La pestaña de Google Sheets no es válida.")
        params["gid"] = str(source["gid"])
    return url + "?" + urlencode(params)


def allowed_download(url):
    parsed = urlparse(url)
    host = parsed.hostname or ""
    # Sheets exports redirect to a Google-owned download host. Never follow arbitrary URLs.
    return (parsed.scheme == "https" and not parsed.username and not parsed.password
            and parsed.netloc == host and (host == "docs.google.com" or
            re.fullmatch(r"doc-[a-z0-9-]+\.googleusercontent\.com", host) is not None))


def unavailable(source):
    if source.get("published_id"):
        return HTTPException(409, "Esta pestaña no está publicada o dejó de estar disponible. Revisá el enlace de publicación en Google Sheets.")
    return HTTPException(409, "No se pudo leer esta hoja sin una cuenta. Si es privada, conectá Google con una cuenta que tenga acceso; también verificá el enlace y la pestaña.")


def read_public_rows(source):
    url = export_url(source)
    try:
        # Do not forward Google tokens, user cookies or application authentication headers.
        with httpx.Client(timeout=30, follow_redirects=False, trust_env=False) as client:
            for _ in range(5):
                if not allowed_download(url):
                    raise unavailable(source)
                with client.stream("GET", url, headers={"Accept": "text/csv"}) as response:
                    if response.status_code in {301, 302, 303, 307, 308}:
                        location = response.headers.get("location")
                        if not location:
                            raise unavailable(source)
                        url = urljoin(url, location)
                        continue
                    if response.status_code in {401, 403, 404, 410}:
                        raise unavailable(source)
                    if response.status_code == 429:
                        raise HTTPException(429, "Google alcanzó su límite de consultas. Reintentá en unos minutos.")
                    if response.status_code != 200:
                        raise HTTPException(502, "Google Sheets no respondió correctamente. Reintentá.")
                    content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
                    if content_type not in {"text/csv", "application/csv", "application/octet-stream", "text/plain"}:
                        raise unavailable(source)
                    chunks, size = [], 0
                    for chunk in response.iter_bytes():
                        size += len(chunk)
                        if size > MAX_BYTES:
                            raise DataError("La hoja de Google supera 25 MB. Reducí el tamaño de la pestaña.")
                        chunks.append(chunk)
                    try:
                        text = b"".join(chunks).decode("utf-8-sig")
                    except UnicodeDecodeError as exc:
                        raise DataError("Google no devolvió una hoja en un formato válido.") from exc
                    if re.match(r"\s*<(?:!doctype|html|head|body)\b", text, re.I):
                        raise unavailable(source)
                    try:
                        rows = bounded(csv.reader(io.StringIO(text, newline=""), strict=True))
                    except csv.Error as exc:
                        raise DataError("No se pudieron interpretar los datos publicados de Google Sheets.") from exc
                    if not rows:
                        raise DataError("La pestaña de Google Sheets está vacía.")
                    return rows
        raise HTTPException(502, "Google redirigió la descarga demasiadas veces. Volvé a copiar el enlace de la hoja.")
    except httpx.HTTPError as exc:
        raise HTTPException(502, "No se pudo conectar con Google Sheets. Reintentá.") from exc
