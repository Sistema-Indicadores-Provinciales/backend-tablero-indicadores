"""Server-side Google grants, scoped to the signed-in application user."""
import base64
import hashlib
import json
import os
import threading
import time
from uuid import uuid4

import httpx
from cryptography.fernet import Fernet, InvalidToken
from fastapi import HTTPException

from app.workspace_access import current_user

SCOPE = "https://www.googleapis.com/auth/spreadsheets.readonly"
_locks = [threading.Lock() for _ in range(64)]


def cipher():
    key = os.getenv("GOOGLE_TOKEN_ENCRYPTION_KEY", "").strip()
    if key:
        try:
            return Fernet(key.encode())
        except ValueError:
            raise HTTPException(503, "La clave de cifrado de Google no es válida.")
    # Domain-separated key derivation from the existing, high-entropy server secret.
    secret = os.getenv("JWT_SECRET", "")
    if len(secret) < 32:
        raise HTTPException(503, "Configurá una clave de cifrado de Google o un JWT_SECRET de al menos 32 caracteres.")
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(b"indicators/google/v1\0" + secret.encode()).digest()))


def seal(value, context):
    return cipher().encrypt(json.dumps({"context": context, "value": value}).encode()).decode()


def unseal(value, context):
    try:
        data = json.loads(cipher().decrypt(value.encode()))
        if data["context"] != context:
            raise ValueError()
        return data["value"]
    except (InvalidToken, ValueError, KeyError, TypeError):
        raise HTTPException(409, "La conexión de Google necesita renovarse. Volvé a conectar tu cuenta.")


def credentials(database):
    settings = database.analytics_settings.find_one({"_id": "google-sheets"}) or {}
    client_id = settings.get("client_id") or os.getenv("GOOGLE_CLIENT_ID", "").strip()
    secret = ""
    if settings.get("client_secret"):
        secret = unseal(settings["client_secret"], "client:" + client_id)
    elif client_id and client_id == os.getenv("GOOGLE_CLIENT_ID", "").strip():
        secret = os.getenv("GOOGLE_CLIENT_SECRET", "").strip()
    return client_id, secret


def status(database, owner):
    current_user(database, owner)
    client_id, secret = credentials(database)
    grant = database.analytics_google_connections.find_one({"_id": owner, "client_id": client_id})
    return {"client_id": client_id, "public_access": True, "persistent_available": bool(secret),
            "connected": bool(grant and secret)}


def token_request(fields):
    try:
        response = httpx.post("https://oauth2.googleapis.com/token", data=fields, timeout=20)
        data = response.json()
    except (httpx.HTTPError, ValueError):
        raise HTTPException(502, "Google no respondió. Reintentá la conexión en unos minutos.")
    if response.status_code == 429:
        raise HTTPException(429, "Google alcanzó su límite de consultas. Reintentá en unos minutos.")
    if response.status_code >= 500:
        raise HTTPException(502, "Google no está disponible temporalmente. Reintentá más tarde.")
    if data.get("error") == "invalid_grant":
        raise HTTPException(409, "Google requiere una nueva autorización. Volvé a conectar tu cuenta.")
    if response.status_code != 200 or not data.get("access_token"):
        raise HTTPException(503, "No se pudo autorizar Google. Un administrador debe revisar el ID y el secreto del cliente.")
    if data.get("scope") and SCOPE not in data["scope"].split():
        raise HTTPException(409, "Autorizá la lectura de Google Sheets para conectar tu cuenta.")
    return data


def connect(database, owner, code, origin):
    current_user(database, owner)
    client_id, secret = credentials(database)
    if not client_id or not secret:
        raise HTTPException(409, "Un administrador debe habilitar la conexión persistente en Administración → Conexiones.")
    data = token_request({"client_id": client_id, "client_secret": secret, "code": code,
                          "grant_type": "authorization_code", "redirect_uri": origin})
    # Never reuse a previous account's refresh token when switching Google accounts.
    if not data.get("refresh_token"):
        raise HTTPException(409, "Google no entregó permiso para mantener la conexión. Quitá el acceso de esta aplicación en tu cuenta Google y volvé a conectar.")
    value = {"access_token": data["access_token"], "refresh_token": data["refresh_token"],
             "expires_at": time.time() + int(data.get("expires_in", 3600))}
    database.analytics_google_connections.replace_one({"_id": owner}, {
        "_id": owner, "client_id": client_id, "version": uuid4().hex,
        "credentials": seal(value, "user:" + owner),
    }, upsert=True)
    return status(database, owner)


def access_token(database, owner):
    current_user(database, owner)
    with _locks[hash(owner) % len(_locks)]:
        client_id, secret = credentials(database)
        grant = database.analytics_google_connections.find_one({"_id": owner, "client_id": client_id})
        if not grant:
            return ""
        match = {"_id": owner, "version": grant["version"]}
        try:
            value = unseal(grant["credentials"], "user:" + owner)
            if value["expires_at"] > time.time() + 60:
                return value["access_token"]
            if not secret:
                raise HTTPException(503, "Un administrador debe revisar la configuración de Google.")
            data = token_request({"client_id": client_id, "client_secret": secret,
                                  "refresh_token": value["refresh_token"], "grant_type": "refresh_token"})
        except HTTPException as exc:
            if exc.status_code == 409:
                database.analytics_google_connections.delete_one(match)
            raise
        value.update(access_token=data["access_token"], expires_at=time.time() + int(data.get("expires_in", 3600)))
        if data.get("refresh_token"):
            value["refresh_token"] = data["refresh_token"]
        # A concurrent disconnect/account change must not resurrect an old grant.
        updated = database.analytics_google_connections.update_one(match, {"$set": {"credentials": seal(value, "user:" + owner)}})
        if not updated.matched_count:
            raise HTTPException(409, "La conexión de Google cambió. Actualizá la página.")
        return value["access_token"]


def disconnect(database, owner):
    current_user(database, owner)
    grant = database.analytics_google_connections.find_one_and_delete({"_id": owner})
    revoked = True
    if grant:
        try:
            value = unseal(grant["credentials"], "user:" + owner)
            response = httpx.post("https://oauth2.googleapis.com/revoke", data={"token": value["refresh_token"]}, timeout=15)
            revoked = response.status_code in {200, 400}
        except (httpx.HTTPError, HTTPException):
            revoked = False
    return {"connected": False, "revoked": revoked}
