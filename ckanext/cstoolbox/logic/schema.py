"""Navl validation schemas for cstoolbox actions."""

import re

from ckan.plugins import toolkit
from ckan.lib.navl.dictization_functions import missing, StopOnError

not_empty = toolkit.get_validator("not_empty")
ignore_missing = toolkit.get_validator("ignore_missing")
unicode_safe = toolkit.get_validator("unicode_safe")

_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,99}$")

_TIME_RANGE_PRESETS = ("1w", "1m", "3m", "6m", "1y", "all")
_GEOJSON_MODES = ("expanded", "compact")
_SUBMISSION_STATUSES = ("draft", "pending", "approved", "rejected")
_CHART_TYPES = ("line", "bar", "area")


def _navl_slug(key, data, errors, context):
    value = data.get(key)
    if not value:
        return
    value = str(value).strip().lower()
    if not _SLUG_RE.match(value):
        errors[key].append(
            "Slug must contain only lowercase letters, digits and hyphens "
            "(2-100 chars)."
        )
        raise StopOnError
    data[key] = value


def _navl_identifier(key, data, errors, context):
    value = data.get(key, missing)
    if value is missing or value is None:
        return
    if isinstance(value, str):
        value = value.strip()
    if value == "":
        return
    if not _IDENT_RE.match(value):
        errors[key].append(
            "Must start with a letter/underscore and contain only "
            "letters, digits or underscores."
        )
        raise StopOnError
    data[key] = value


def _navl_database(key, data, errors, context):
    """Restrict the database field to the configured allowlist."""
    value = data.get(key)
    if not value:
        return
    from ckanext.cstoolbox.api_client import allowed_databases
    allowed = allowed_databases()
    if value not in allowed:
        errors[key].append(
            "Must be one of: %s" % ", ".join(allowed)
        )
        raise StopOnError


def _navl_time_range(key, data, errors, context):
    value = data.get(key, missing)
    if value is missing or value is None or value == "":
        data[key] = "90d"
        return
    val = str(value).strip().lower()
    # Accept the legacy stationsdischarge presets too — anything plausible is
    # passed through, only an outright invalid token is rejected.
    if val not in _TIME_RANGE_PRESETS and not re.match(
        r"^(\d+)(h|d|w|m|y)$", val
    ):
        errors[key].append(
            "Must be a preset like 1w/1m/3m/6m/1y/all or e.g. 30d."
        )
        raise StopOnError
    data[key] = val


def _navl_geojson_mode(key, data, errors, context):
    value = data.get(key, missing)
    if value is missing or value is None or value == "":
        data[key] = "expanded"
        return
    val = str(value).strip().lower()
    if val not in _GEOJSON_MODES:
        errors[key].append(
            "Must be one of: %s" % ", ".join(_GEOJSON_MODES)
        )
        raise StopOnError
    data[key] = val


def _navl_chart_type(key, data, errors, context):
    value = data.get(key, missing)
    if value is missing or value is None or value == "":
        data[key] = "line"
        return
    val = str(value).strip().lower()
    if val not in _CHART_TYPES:
        errors[key].append(
            "Must be one of: %s" % ", ".join(_CHART_TYPES)
        )
        raise StopOnError
    data[key] = val


def _navl_submission_status(key, data, errors, context):
    value = data.get(key, missing)
    if value is missing or value is None or value == "":
        return
    val = str(value).strip().lower()
    if val not in _SUBMISSION_STATUSES:
        errors[key].append(
            "Must be one of: %s" % ", ".join(_SUBMISSION_STATUSES)
        )
        raise StopOnError
    data[key] = val


def _navl_survey_name_validator(key, data, errors, context):
    """Ensure the slug is unique."""
    value = data.get(key)
    if not value:
        return
    from ckanext.cstoolbox import db as _db
    existing = _db.CSTSurvey.get(name=value)
    if existing:
        survey_id = context.get("survey_id")
        if not survey_id or existing.id != survey_id:
            errors[key].append("A survey with URL '%s' already exists." % value)
            raise StopOnError


def _navl_upstream_unique(key, data, errors, context):
    """Validate (database, schema, view_name) uniqueness on the database field."""
    if key != "database":
        return
    database = data.get("database")
    schema = data.get("schema")
    view_name = data.get("view_name")
    if not (database and schema and view_name):
        return
    from ckanext.cstoolbox import db as _db
    existing = _db.CSTSurvey.get_by_upstream(database, schema, view_name)
    if existing:
        survey_id = context.get("survey_id")
        if not survey_id or existing.id != survey_id:
            errors["database"].append(
                "A survey for %s.%s in '%s' already exists "
                "(remove it first to register a new one)."
                % (schema, view_name, database)
            )
            raise StopOnError


def _navl_collection_name_validator(key, data, errors, context):
    value = data.get(key)
    if not value:
        return
    from ckanext.cstoolbox import db as _db
    existing = _db.CSTCollection.get(name=value)
    if existing:
        coll_id = context.get("collection_id")
        if not coll_id or existing.id != coll_id:
            errors[key].append("A collection with URL '%s' already exists." % value)
            raise StopOnError


# ── Public schemas ──────────────────────────────────────

def survey_create_schema():
    return {
        "title": [not_empty, unicode_safe],
        "name": [ignore_missing, unicode_safe, _navl_slug, _navl_survey_name_validator],
        "description": [ignore_missing, unicode_safe],
        "owner_org": [ignore_missing, unicode_safe],

        "database": [not_empty, unicode_safe, _navl_database, _navl_upstream_unique],
        "schema": [not_empty, unicode_safe, _navl_identifier],
        "view_name": [not_empty, unicode_safe, _navl_identifier],

        "date_field": [ignore_missing, unicode_safe, _navl_identifier],
        "lat_field": [ignore_missing, unicode_safe, _navl_identifier],
        "lon_field": [ignore_missing, unicode_safe, _navl_identifier],
        "site_field": [ignore_missing, unicode_safe, _navl_identifier],

        "default_time_range": [ignore_missing, _navl_time_range],
        "submission_status": [ignore_missing, _navl_submission_status],
    }


def survey_update_schema():
    schema = survey_create_schema()
    schema["id"] = [not_empty, unicode_safe]
    for field in (
        "title", "name", "database", "schema", "view_name",
        "default_time_range",
    ):
        schema[field] = [ignore_missing] + [v for v in schema[field]
                                            if v is not ignore_missing]
    return schema


def collection_create_schema():
    return {
        "title": [not_empty, unicode_safe],
        "name": [ignore_missing, unicode_safe, _navl_slug, _navl_collection_name_validator],
        "description": [ignore_missing, unicode_safe],
        "owner_org": [ignore_missing, unicode_safe],
        "time_range": [ignore_missing, _navl_time_range],
        "export_format": [ignore_missing, unicode_safe],
        "geojson_mode": [ignore_missing, _navl_geojson_mode],
        "time_property": [ignore_missing, unicode_safe, _navl_identifier],
    }


def collection_update_schema():
    schema = collection_create_schema()
    schema["id"] = [not_empty, unicode_safe]
    schema["title"] = [ignore_missing] + [v for v in schema["title"]
                                          if v is not not_empty]
    return schema


# Exposed for actions
chart_type_validator = _navl_chart_type
