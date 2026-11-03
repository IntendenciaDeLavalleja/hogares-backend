"""Validate the shape of incomplete forms without applying final submission rules."""

import json
import math

from marshmallow import ValidationError, fields

from ..schemas import application_schema

MAX_DRAFT_BYTES = 40 * 1024


def _validate_value(value, field, path):
    if isinstance(field, fields.Nested):
        if not isinstance(value, dict):
            raise ValidationError({path: ["Se esperaba un objeto."]})
        _validate_object(value, field.schema.fields, path)
    elif isinstance(field, fields.List):
        if not isinstance(value, list) or len(value) > 30:
            raise ValidationError({path: ["La lista no es válida o es demasiado larga."]})
        for index, entry in enumerate(value):
            _validate_value(entry, field.inner, f"{path}.{index}")
    elif isinstance(field, fields.Boolean):
        if value is not None and not isinstance(value, bool):
            raise ValidationError({path: ["Se esperaba una respuesta sí/no."]})
    elif isinstance(field, (fields.String, fields.Date)):
        if value is not None and (not isinstance(value, str) or len(value) > 5000):
            raise ValidationError({path: ["El texto no es válido o es demasiado largo."]})
    elif isinstance(field, (fields.Integer, fields.Float)):
        if value is not None and (
            isinstance(value, bool) or not isinstance(value, (str, int, float))
            or (isinstance(value, str) and len(value) > 100)
            or (isinstance(value, (int, float)) and not math.isfinite(value))
        ):
            raise ValidationError({path: ["El valor numérico no es válido."]})


def _validate_object(value, allowed_fields, prefix=""):
    unknown = set(value) - set(allowed_fields)
    if unknown:
        raise ValidationError({prefix or "_schema": ["El formulario contiene campos desconocidos."]})
    for key, entry in value.items():
        _validate_value(entry, allowed_fields[key], f"{prefix}.{key}" if prefix else key)


def validate_draft_snapshot(value):
    if not isinstance(value, dict):
        raise ValidationError({"_schema": ["Se esperaba un objeto JSON."]})
    _validate_object(value, application_schema.fields)
    if len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")) > MAX_DRAFT_BYTES:
        raise ValidationError({"_schema": ["El formulario supera el tamaño máximo permitido."]})
    return value


def merge_draft_snapshot(previous, changes):
    def merge(old, new):
        merged = dict(old or {})
        for key, value in new.items():
            if isinstance(value, dict) and isinstance(merged.get(key), dict):
                merged[key] = merge(merged[key], value)
            else:
                merged[key] = value
        return merged

    return validate_draft_snapshot(merge(previous, changes))
