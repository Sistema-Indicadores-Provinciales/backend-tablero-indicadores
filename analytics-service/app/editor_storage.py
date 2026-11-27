"""Apply explicit editor operations without normalizing stored configurations."""
from copy import deepcopy
import hashlib
import json
from typing import Any, Literal, get_args, get_origin, Annotated

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter


EDITABLE = {'name', 'source_id', 'widgets', 'filter_columns', 'column_aliases', 'filter_sorts',
            'filter_option_rules', 'default_filters', 'filter_settings'}
WIDGET_FIELDS = {'title', 'width', 'library', 'source_id', 'config'}


class Change(BaseModel):
    model_config = ConfigDict(extra='forbid')
    action: Literal['set', 'delete', 'insert', 'remove', 'reorder']
    widget: str | None = Field(default=None, max_length=100)
    path: list[str] = Field(default_factory=list, max_length=12)
    value: Any = None


class EditorChanges(BaseModel):
    model_config = ConfigDict(extra='forbid')
    revision: str = Field(pattern=r'^[a-f0-9]{64}$')
    changes: list[Change] = Field(max_length=2000)


def editor_document(workspace):
    return {key: deepcopy(value) for key, value in workspace.items() if key in EDITABLE}


def revision(workspace):
    def ordered(value):
        if isinstance(value, dict):
            return {key: list((name, ordered(parts)) for name, parts in item.items()) if key == 'computed_columns' and isinstance(item, dict)
                    else ordered(item) for key, item in value.items()}
        return [ordered(item) for item in value] if isinstance(value, list) else value
    return hashlib.sha256(json.dumps(ordered(editor_document(workspace)), ensure_ascii=False,
                                    sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def identical(first, second):
    if type(first) is not type(second):
        return False
    if isinstance(first, dict):
        return first.keys() == second.keys() and all((key != 'computed_columns' or not isinstance(value, dict)
            or not isinstance(second[key], dict) or list(value) == list(second[key])) and identical(value, second[key]) for key, value in first.items())
    if isinstance(first, list):
        return len(first) == len(second) and all(identical(a, b) for a, b in zip(first, second))
    return first == second


def projection(schema, value):
    """Validate known settings while leaving opaque persisted properties intact."""
    origin, args = get_origin(schema), get_args(schema)
    if origin is Annotated:
        return projection(args[0], value)
    if isinstance(schema, type) and issubclass(schema, BaseModel) and isinstance(value, dict):
        return {key: projection(schema.model_fields[key].annotation, item) for key, item in value.items() if key in schema.model_fields}
    if origin is list and isinstance(value, list):
        return [projection(args[0], item) for item in value]
    if origin is dict and isinstance(value, dict):
        return {key: projection(args[1], item) for key, item in value.items()}
    models = [argument for argument in args if isinstance(argument, type) and issubclass(argument, BaseModel)]
    if models and isinstance(value, dict):
        return projection(max(models, key=lambda model: len(value.keys() & model.model_fields.keys())), value)
    return value


def apply(workspace, changes, chart_model, widget_model, workspace_model):
    candidate = deepcopy(workspace)
    for change in changes:
        widgets = candidate.setdefault('widgets', [])
        widget = next((item for item in widgets if item['id'] == change.widget), None)
        if change.action == 'insert':
            if widget or change.path or not isinstance(change.value, dict) or change.value.get('id') != change.widget:
                raise HTTPException(422, 'El gráfico nuevo necesita un identificador único.')
            widget_model.model_validate(change.value)
            widgets.append(deepcopy(change.value))
            continue
        if change.action == 'remove':
            if not widget or change.path:
                raise HTTPException(422, 'El gráfico que querés quitar no existe.')
            widgets.remove(widget)
            continue
        if change.action == 'reorder':
            expected = [item['id'] for item in widgets]
            if change.widget or change.path or not isinstance(change.value, list) or len(change.value) != len(expected) or set(change.value) != set(expected):
                raise HTTPException(422, 'El orden debe incluir todos los gráficos una vez.')
            lookup = {item['id']: item for item in widgets}
            candidate['widgets'] = [lookup[key] for key in change.value]
            continue
        allowed = WIDGET_FIELDS if change.widget else EDITABLE - {'widgets'}
        if not change.path or change.path[0] not in allowed or (change.widget and not widget):
            raise HTTPException(422, 'La propiedad no admite edición desde el generador.')
        if change.widget and change.path[0] == 'config' and len(change.path) > 1 and change.path[1] not in chart_model.model_fields:
            raise HTTPException(422, 'Esta propiedad original debe conservarse sin cambios.')
        target = widget if change.widget else candidate
        for key in change.path[:-1]:
            if not isinstance(target.get(key), dict):
                raise HTTPException(422, 'La ruta necesita primero una configuración completa.')
            target = target[key]
        if change.action == 'delete':
            target.pop(change.path[-1], None)
        else:
            target[change.path[-1]] = deepcopy(change.value)
    touched = {change.widget for change in changes if change.widget and change.action in {'set', 'delete'}}
    for identifier in touched:
        widget = next((item for item in candidate['widgets'] if item['id'] == identifier), None)
        if widget is None:
            continue
        for change in changes:
            if change.widget != identifier or not change.path or change.action not in {'set', 'delete'}:
                continue
            root = change.path[0]
            if root == 'config':
                chart_model.model_validate(projection(chart_model, widget['config']))
            elif root in widget:
                TypeAdapter(widget_model.model_fields[root].rebuild_annotation()).validate_python(widget[root])
    if any(change.widget is None and change.path for change in changes):
        section = editor_document(candidate)
        section['widgets'] = []
        workspace_model.model_validate(projection(workspace_model, section))
    if len(candidate.get('widgets', [])) > 30:
        raise HTTPException(422, 'Una sección admite hasta 30 gráficos.')
    return candidate


def update_operations(before, after):
    absent, assignments, deletions = object(), {}, {}
    def visit(old, new, path):
        if identical(old, new):
            return
        key = '.'.join(path)
        if new is absent:
            deletions[key] = ''
        elif path[-1] != 'computed_columns' and isinstance(old, dict) and isinstance(new, dict) and all('.' not in name and not name.startswith('$') for name in old.keys() | new.keys()):
            for name in old.keys() | new.keys():
                visit(old.get(name, absent), new.get(name, absent), [*path, name])
        elif path == ['widgets'] and isinstance(old, list) and isinstance(new, list) and [item['id'] for item in old] == [item['id'] for item in new]:
            for index, (first, second) in enumerate(zip(old, new)):
                visit(first, second, [*path, str(index)])
        else:
            assignments[key] = deepcopy(new)
    for key in EDITABLE:
        visit(before.get(key, absent), after.get(key, absent), [key])
    return {**({'$set': assignments} if assignments else {}), **({'$unset': deletions} if deletions else {})}


def atomic_filter(workspace):
    return {'_id': workspace['_id'], 'owner': workspace['owner'], 'deletedAt': None,
            '$and': [{key: {'$eq': workspace[key], '$exists': True}} if key in workspace
                     else {key: {'$exists': False}} for key in sorted(EDITABLE)]}
