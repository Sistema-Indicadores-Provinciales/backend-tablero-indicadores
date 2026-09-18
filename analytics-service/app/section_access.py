"""Create sections and attach chart configurations to the existing catalog."""
import hashlib
import re
import unicodedata

from bson import ObjectId
from fastapi import HTTPException

# These sections have a dedicated application page, not an empty chart canvas.
STATIC_SECTION_KEYS = {"coparticipacion---cfi-y-totales"}


def require_admin(account):
    if account.get("profileType") != "ADMIN":
        raise HTTPException(403, "Solo un administrador puede agregar secciones o gráficos a una sección vacía.")


def object_id(value):
    if not ObjectId.is_valid(value):
        raise HTTPException(422, "El tablero o la sección no son válidos.")
    return ObjectId(value)


def dashboard_for(database, did):
    dashboard = database.dashboards.find_one({"_id": object_id(did), "deletedAt": None})
    if not dashboard:
        raise HTTPException(404, "El tablero ya no está disponible.")
    return dashboard


def target_section(database, destination):
    dashboard = dashboard_for(database, destination["dashboardId"])
    sid = object_id(destination["sectionId"])
    section = database.sections.find_one({"_id": sid, "deletedAt": None})
    if not section or sid not in dashboard.get("sections", []):
        raise HTTPException(404, "La sección ya no pertenece a este tablero o fue eliminada.")
    return dashboard, section


def validate_destination(database, destination, account, workspace_id=None):
    require_admin(account)
    dashboard, section = target_section(database, destination)
    if section["keyname"] in STATIC_SECTION_KEYS:
        raise HTTPException(409, "Esta sección tiene una página propia y no se puede reemplazar desde el generador.")
    if section.get("workspaceId") and section["workspaceId"] != workspace_id:
        raise HTTPException(409, "La sección ya tiene gráficos. Abrila y usá Editar gráficos para conservarlos y agregar otros.")
    return dashboard, section


def describe_destination(dashboard, section):
    return {"dashboardId": str(dashboard["_id"]), "sectionId": str(section["_id"]),
            "dashboardName": dashboard.get("name") or dashboard["keyname"],
            "sectionName": section.get("name") or section["keyname"],
            "path": "/" + dashboard["keyname"] + "/" + section["keyname"]}


def available_destinations(database, account):
    if account.get("profileType") != "ADMIN":
        return []
    result = []
    for dashboard in database.dashboards.find({"deletedAt": None, "show": True}).sort("name", 1):
        for section in database.sections.find({"_id": {"$in": dashboard.get("sections", [])}, "deletedAt": None, "show": True}):
            if not section.get("workspaceId") and section["keyname"] not in STATIC_SECTION_KEYS:
                result.append(describe_destination(dashboard, section))
    return result


def validated_recipients(database, account, recipient_ids):
    if recipient_ids is None:
        return None
    require_admin(account)
    if any(not ObjectId.is_valid(uid) for uid in recipient_ids):
        raise HTTPException(422, "Hay un usuario inválido en la selección.")
    recipients = {ObjectId(uid) for uid in recipient_ids}
    if database.users.count_documents({"_id": {"$in": list(recipients)}}) != len(recipients):
        raise HTTPException(422, "Uno de los usuarios ya no existe. Actualizá la selección.")
    return recipients


def apply_recipients(database, dashboard_id, section_id, recipients):
    from app.workspace_access import grant_section
    database.users.update_many(
        {"_id": {"$nin": list(recipients)}, "access": {"$elemMatch": {"dashboard": dashboard_id}}},
        {"$pull": {"access.$.sections": section_id}},
    )
    # Keep dashboard-only permissions and every unrelated section intact.
    for uid in recipients:
        grant_section(database, uid, dashboard_id, section_id)


def section_options(database, account, destination):
    from app.workspace_access import publication_options
    require_admin(account)
    dashboard, section = target_section(database, destination)
    result = publication_options(database, account)
    result.update(describe_destination(dashboard, section))
    result["recipientIds"] = [str(u["_id"]) for u in database.users.find({"access": {"$elemMatch": {
        "dashboard": dashboard["_id"], "sections": section["_id"],
    }}}, {"_id": 1})]
    result["workspaceId"] = section.get("workspaceId")
    result["canAddCharts"] = not section.get("workspaceId") and section["keyname"] not in STATIC_SECTION_KEYS
    return result


def create_section(database, account, dashboard_id, name, recipient_ids, request_id):
    require_admin(account)
    dashboard = dashboard_for(database, dashboard_id)
    recipients = validated_recipients(database, account, recipient_ids)
    name = name.strip()
    if not name:
        raise HTTPException(422, "Escribí un nombre para la sección.")
    sid = ObjectId(hashlib.sha256((str(account["_id"]) + ":section:" + str(request_id)).encode()).hexdigest()[:24])
    previous = database.sections.find_one({"_id": sid})
    if previous and (previous.get("deletedAt") or previous.get("createdFor") != str(dashboard["_id"])):
        raise HTTPException(409, "Esta creación ya no está disponible. Cerrá el formulario y volvé a intentarlo.")
    slug = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    slug = re.sub(r"[^a-z0-9]+", "-", slug).strip("-")[:70] or "seccion"
    database.sections.update_one({"_id": sid}, {"$setOnInsert": {
        "name": name, "keyname": slug + "-" + str(sid), "show": True, "linked": True,
        "createdFor": str(dashboard["_id"]),
    }}, upsert=True)
    attached = database.dashboards.update_one({"_id": dashboard["_id"], "deletedAt": None}, {"$addToSet": {"sections": sid}})
    if not attached.matched_count:
        raise HTTPException(409, "El tablero fue eliminado mientras se creaba la sección.")
    apply_recipients(database, dashboard["_id"], sid, recipients)
    section = database.sections.find_one({"_id": sid})
    return {**describe_destination(dashboard, section), "canView": account["_id"] in recipients and dashboard.get("show", False)}
