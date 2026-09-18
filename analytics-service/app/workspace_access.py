"""Bridge saved charts to the existing Dashboard / Section / User.access model."""
import hashlib
import re
import unicodedata

from bson import ObjectId
from fastapi import HTTPException
from app.section_access import (target_section, validate_destination, describe_destination,
                                available_destinations, validated_recipients, apply_recipients)


def current_user(database, user_id):
    if not ObjectId.is_valid(user_id):
        raise HTTPException(401, "La sesión no corresponde a un usuario válido.")
    account = database.users.find_one({"_id": ObjectId(user_id)}, {"access": 1, "profileType": 1, "username": 1})
    if not account:
        raise HTTPException(401, "El usuario ya no existe. Volvé a ingresar.")
    return account


def owned_workspace(database, workspace_id, owner):
    workspace = database.analytics_workspaces.find_one({"_id": workspace_id, "owner": owner, "deletedAt": None})
    if not workspace:
        raise HTTPException(404, "El tablero no existe o no podés editarlo.")
    return workspace


def grant_section(database, user_id, dashboard_id, section_id):
    # Each write is atomic; unrelated dashboards and sections are never replaced.
    database.users.update_one(
        {"_id": user_id, "access.dashboard": {"$ne": dashboard_id}},
        {"$push": {"access": {"dashboard": dashboard_id, "sections": [section_id]}}},
    )
    database.users.update_one(
        {"_id": user_id, "access": {"$elemMatch": {"dashboard": dashboard_id}}},
        {"$addToSet": {"access.$.sections": section_id}},
    )


def ensure_menu(database, workspace, account):
    """Idempotent, resumable creation, also compatible with standalone MongoDB."""
    if workspace.get("deletedAt"):
        raise HTTPException(404, "El tablero fue eliminado.")
    wid = workspace["_id"]
    if workspace.get("destination"):
        if workspace.get("menu_initialized"):
            dashboard, section = target_section(database, workspace["destination"])
            if section.get("workspaceId") != wid:
                raise HTTPException(409, "Los gráficos ya no están vinculados a esta sección.")
            return dashboard, section
        dashboard, section = validate_destination(database, workspace["destination"], account, wid)
        claimed = database.sections.update_one({"_id": section["_id"], "deletedAt": None,
            "$or": [{"workspaceId": None}, {"workspaceId": wid}]}, {"$set": {"workspaceId": wid}})
        if not claimed.matched_count:
            raise HTTPException(409, "Otro usuario agregó gráficos a esta sección. Actualizá la página.")
        database.analytics_workspaces.update_one({"_id": wid}, {"$set": {"menu_initialized": True}})
        return dashboard, database.sections.find_one({"_id": section["_id"]})
    did = ObjectId(hashlib.sha256(("dashboard:" + wid).encode()).hexdigest()[:24])
    sid = ObjectId(hashlib.sha256(("section:" + wid).encode()).hexdigest()[:24])
    if workspace.get("menu_initialized"):
        dashboard = database.dashboards.find_one({"_id": did})
        section = database.sections.find_one({"_id": sid})
        if not dashboard or not section or dashboard.get("deletedAt") or section.get("deletedAt"):
            raise HTTPException(409, "El tablero o su sección fueron eliminados. Guardá una copia nueva para volver a agregarlo al menú.")
        return dashboard, section
    slug = unicodedata.normalize("NFKD", workspace["name"]).encode("ascii", "ignore").decode().lower()
    slug = re.sub(r"[^a-z0-9]+", "-", slug).strip("-")[:70] or "tablero"
    database.sections.update_one({"_id": sid}, {"$setOnInsert": {
        "keyname": "graficos-" + str(sid), "name": "Gráficos", "show": True,
        "linked": True, "workspaceId": wid,
    }}, upsert=True)
    database.dashboards.update_one({"_id": did}, {"$setOnInsert": {
        "keyname": slug + "-" + str(did), "name": workspace["name"], "show": True,
        "icon": "material-symbols:bar-chart-rounded", "sections": [sid], "generatedWorkspaceId": wid,
    }}, upsert=True)
    if not workspace.get("menu_initialized"):
        grant_section(database, account["_id"], did, sid)
        database.analytics_workspaces.update_one({"_id": wid, "owner": str(account["_id"])}, {"$set": {"menu_initialized": True}})
    return database.dashboards.find_one({"_id": did}), database.sections.find_one({"_id": sid})


def publication_options(database, account, workspace=None):
    can_share = account.get("profileType") == "ADMIN"
    result = {"canShare": can_share, "currentUserId": str(account["_id"]), "users": [], "recipientIds": [], "published": False,
              "destinations": available_destinations(database, account)}
    if can_share:
        result["users"] = [{"_id": str(u["_id"]), "username": u.get("username", "Usuario")}
                           for u in database.users.find({}, {"username": 1}).sort("username", 1)]
    if workspace is None:
        return result
    if workspace.get("destination"):
        dashboard, section = target_section(database, workspace["destination"])
        result["destination"] = describe_destination(dashboard, section)
    else:
        dashboard = database.dashboards.find_one({"generatedWorkspaceId": workspace["_id"]})
        section = database.sections.find_one({"workspaceId": workspace["_id"]})
    if not dashboard or not section or dashboard.get("deletedAt") or section.get("deletedAt"):
        return result
    attached = section["_id"] in dashboard.get("sections", [])
    linked = section.get("workspaceId") == workspace["_id"]
    result.update({"published": linked, "name": dashboard.get("name"), "icon": dashboard.get("icon"),
                   "path": "/" + dashboard["keyname"] + "/" + section["keyname"],
                   "show": dashboard.get("show", False) and section.get("show", False) and attached and linked})
    result["canView"] = result["show"] and bool(database.users.find_one({"_id": account["_id"], "access": {"$elemMatch": {
        "dashboard": dashboard["_id"], "sections": section["_id"],
    }}}))
    if can_share:
        result["recipientIds"] = [str(u["_id"]) for u in database.users.find({"access": {"$elemMatch": {
            "dashboard": dashboard["_id"], "sections": section["_id"],
        }}}, {"_id": 1})]
    return result


def publish(database, workspace, account, icon, recipient_ids=None):
    recipients = validated_recipients(database, account, recipient_ids)
    dashboard, section = ensure_menu(database, workspace, account)
    if not workspace.get("destination"):
        database.dashboards.update_one({"_id": dashboard["_id"]}, {"$set": {"name": workspace["name"], "icon": icon}})
    if recipients is not None:
        apply_recipients(database, dashboard["_id"], section["_id"], recipients)
    return publication_options(database, account, workspace)


def view_workspace(database, workspace_id, user_id):
    account = current_user(database, user_id)
    workspace = database.analytics_workspaces.find_one({"_id": workspace_id, "deletedAt": None})
    if workspace:
        sections = list(database.sections.find({"workspaceId": workspace_id, "show": True, "deletedAt": None}))
        for section in sections:
            for access in account.get("access", []):
                if section["_id"] in access.get("sections", []) and database.dashboards.find_one({
                    "_id": access["dashboard"], "show": True, "deletedAt": None, "sections": section["_id"],
                }):
                    return workspace
    raise HTTPException(404, "El tablero no está disponible o no tenés acceso. Consultá con un administrador.")
