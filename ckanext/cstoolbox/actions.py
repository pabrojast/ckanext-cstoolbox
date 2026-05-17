"""CKAN actions for the cstoolbox plugin.

Two halves:

1. **Surveys** — CRUD over locally-curated survey references plus the live
   data proxy (``cstoolbox_survey_data``), GeoJSON, and CSV builders.
2. **Collections** — CRUD over groups of surveys and a combined GeoJSON
   export with an optional Terria-time-slider "expanded" mode.

Plus two admin helpers used by the create form:
``cstoolbox_fetch_views`` and ``cstoolbox_fetch_view_columns``.
"""

import csv
import datetime
import io
import logging
import re
import time
import uuid

import ckan.model as model
import ckan.plugins.toolkit as toolkit

from ckanext.cstoolbox import db as cst_db
from ckanext.cstoolbox import api_client
from ckanext.cstoolbox.logic.schema import (
    survey_create_schema,
    survey_update_schema,
    collection_create_schema,
    collection_update_schema,
)

log = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────
# Generic helpers
# ─────────────────────────────────────────────────────────

def _validate_data(data_dict, schema, context):
    from ckan.lib.navl.dictization_functions import validate
    return validate(data_dict, schema, context)


def _slugify(title):
    slug = (title or "").lower().strip()
    slug = re.sub(r"[^\w\s-]", "", slug)
    slug = re.sub(r"[\s_]+", "-", slug)
    slug = re.sub(r"-+", "-", slug)
    return slug[:100].strip("-") or "survey"


def _unique_slug(base, exists_fn):
    """Return ``base`` if available, otherwise append ``-2``, ``-3`` etc."""
    candidate = base
    counter = 2
    while exists_fn(candidate):
        candidate = "%s-%d" % (base, counter)
        counter += 1
    return candidate


def _is_number(value):
    try:
        float(value)
        return True
    except (TypeError, ValueError):
        return False


def _safe_float(value):
    """Coerce *value* to a finite ``float`` or return ``None``.

    Rejects NaN/±Infinity (which crash TerriaJS when fed as lat/lon) and
    empty/garbage input. Used to harden every geometry path.
    """
    if value is None or value == "":
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if f != f or f == float("inf") or f == float("-inf"):
        return None
    return f


def _looks_like_iso_date(value):
    if not isinstance(value, str):
        return False
    return bool(re.match(r"^\d{4}-\d{2}-\d{2}([T ]\d{2}:\d{2}(:\d{2})?)?$", value))


# ─────────────────────────────────────────────────────────
# Time-range presets
# ─────────────────────────────────────────────────────────

_TIME_RANGE_PRESETS = {
    "1w": datetime.timedelta(weeks=1),
    "1m": datetime.timedelta(days=30),
    "3m": datetime.timedelta(days=90),
    "6m": datetime.timedelta(days=182),
    "1y": datetime.timedelta(days=365),
    # 'all' → no start/end
}


def _resolve_window(data_dict, default_preset=None):
    """Return ``(start, end, label)`` for date filtering.

    Priority: explicit ``start`` / ``end`` query params (ISO-ish strings) →
    ``time_range`` preset → survey default.
    """
    start = (data_dict.get("start") or "").strip() or None
    end = (data_dict.get("end") or "").strip() or None

    preset = (data_dict.get("time_range") or "").strip().lower() or None
    if not start and not end:
        if not preset:
            preset = (default_preset or "").lower() or None
        if preset and preset in _TIME_RANGE_PRESETS:
            end_dt = datetime.datetime.utcnow()
            start_dt = end_dt - _TIME_RANGE_PRESETS[preset]
            return start_dt, end_dt, preset
        if preset == "all":
            return None, None, "all"

    return start, end, preset or "custom"


# ─────────────────────────────────────────────────────────
# Survey columns helpers
# ─────────────────────────────────────────────────────────

_META_COLUMNS = {
    "_uri", "_core_uri", "_top_level_auri", "valid", "exclude",
    "imgs1", "imgs2", "img1", "img2", "img3", "img4",
}


def _save_columns(survey_id, columns):
    """Replace the column-list for a survey.

    ``columns`` is a list of ``{column_name, label, unit, chart_type, sort_order}``
    dicts (extra keys are ignored).
    """
    cst_db.CSTSurveyColumn.delete_by_survey(survey_id)
    for i, col_data in enumerate(columns or []):
        name = (col_data.get("column_name") or "").strip()
        if not name:
            continue
        col = cst_db.CSTSurveyColumn()
        col.id = str(uuid.uuid4())
        col.survey_id = survey_id
        col.column_name = name
        col.label = (col_data.get("label") or "").strip() or None
        col.unit = (col_data.get("unit") or "").strip() or None
        chart_type = (col_data.get("chart_type") or "line").strip().lower()
        col.chart_type = chart_type if chart_type in ("line", "bar", "area") else "line"
        try:
            col.sort_order = int(col_data.get("sort_order", i))
        except (TypeError, ValueError):
            col.sort_order = i
        col.save()
    model.Session.commit()


# ─────────────────────────────────────────────────────────
# Survey CRUD
# ─────────────────────────────────────────────────────────

def cstoolbox_survey_create(context, data_dict):
    """Register a new curated survey reference.

    :param title: Display title (required)
    :param database: Upstream database (required, allowlisted)
    :param schema: Upstream schema (required)
    :param view_name: Upstream view name (required)
    :param ... other identity/presentation fields (see schema)
    :param columns: List of measurement columns to chart
    """
    toolkit.check_access("cstoolbox_survey_create", context, data_dict)

    if not data_dict.get("name"):
        base = _slugify(data_dict.get("title", "") or data_dict.get("view_name", ""))
        data_dict["name"] = _unique_slug(
            base, lambda n: cst_db.CSTSurvey.get(name=n) is not None
        )

    clean, errors = _validate_data(data_dict, survey_create_schema(), context)
    if errors:
        raise toolkit.ValidationError(errors)

    now = datetime.datetime.utcnow()
    user_obj = model.User.get(context.get("user"))

    survey = cst_db.CSTSurvey()
    survey.id = str(uuid.uuid4())
    for field, value in clean.items():
        if hasattr(survey, field) and value is not None:
            setattr(survey, field, value)

    survey.user_id = user_obj.id if user_obj else None
    survey.created = now
    survey.modified = now

    if not survey.submission_status:
        survey.submission_status = "draft"

    submission_action = data_dict.get("submission_action")
    if submission_action == "submit":
        survey.submission_status = "pending"
        survey.submitted_at = now
    elif submission_action == "publish":
        from ckanext.cstoolbox.auth import _is_sysadmin
        if _is_sysadmin(context):
            survey.submission_status = "approved"
            survey.reviewed_at = now
            survey.reviewed_by = user_obj.id if user_obj else None

    try:
        survey.save()
        model.Session.commit()
    except Exception as e:
        model.Session.rollback()
        if "unique" in str(e).lower() or "duplicate" in str(e).lower():
            raise toolkit.ValidationError(
                {"name": ["A survey with this URL already exists."]}
            )
        raise

    _save_columns(survey.id, data_dict.get("columns", []))

    log.info("cstoolbox: created survey '%s' (%s)", survey.title, survey.id)
    return survey.as_dict()


def cstoolbox_survey_show(context, data_dict):
    ref = data_dict.get("id") or data_dict.get("name")
    if not ref:
        raise toolkit.ValidationError({"id": ["Missing value"]})

    survey = cst_db.CSTSurvey.get(id=ref) or cst_db.CSTSurvey.get(name=ref)
    if not survey:
        raise toolkit.ObjectNotFound("Survey not found: %s" % ref)

    toolkit.check_access("cstoolbox_survey_show", context, data_dict)
    return survey.as_dict()


def cstoolbox_survey_update(context, data_dict):
    sid = data_dict.get("id")
    if not sid:
        raise toolkit.ValidationError({"id": ["Missing value"]})

    survey = cst_db.CSTSurvey.get(id=sid)
    if not survey:
        raise toolkit.ObjectNotFound("Survey not found: %s" % sid)

    toolkit.check_access("cstoolbox_survey_update", context, data_dict)

    context = dict(context, survey_id=survey.id)
    clean, errors = _validate_data(data_dict, survey_update_schema(), context)
    if errors:
        raise toolkit.ValidationError(errors)

    now = datetime.datetime.utcnow()
    submission_action = data_dict.get("submission_action")
    if submission_action == "submit":
        clean["submission_status"] = "pending"
        clean["submitted_at"] = now
    elif submission_action in ("publish", "approve"):
        from ckanext.cstoolbox.auth import _is_sysadmin
        if not _is_sysadmin(context):
            raise toolkit.NotAuthorized(
                "Only sysadmins can approve or publish surveys."
            )
        clean["submission_status"] = "approved"
        clean["reviewed_at"] = now
        user_obj = model.User.get(context.get("user"))
        clean["reviewed_by"] = user_obj.id if user_obj else None
    elif submission_action == "reject":
        from ckanext.cstoolbox.auth import _is_sysadmin
        if not _is_sysadmin(context):
            raise toolkit.NotAuthorized("Only sysadmins can reject surveys.")
        clean["submission_status"] = "rejected"
        clean["reviewed_at"] = now
        user_obj = model.User.get(context.get("user"))
        clean["reviewed_by"] = user_obj.id if user_obj else None
    elif submission_action == "draft":
        clean["submission_status"] = "draft"

    for field, value in clean.items():
        if field == "id":
            continue
        if hasattr(survey, field) and value is not None:
            setattr(survey, field, value)

    survey.modified = now

    try:
        survey.save()
        model.Session.commit()
    except Exception as e:
        model.Session.rollback()
        if "unique" in str(e).lower() or "duplicate" in str(e).lower():
            raise toolkit.ValidationError(
                {"name": ["A survey with this URL already exists."]}
            )
        raise

    if "columns" in data_dict:
        _save_columns(survey.id, data_dict["columns"])

    log.info("cstoolbox: updated survey '%s' (%s)", survey.title, survey.id)
    return survey.as_dict()


def cstoolbox_survey_delete(context, data_dict):
    sid = data_dict.get("id")
    if not sid:
        raise toolkit.ValidationError({"id": ["Missing value"]})

    survey = cst_db.CSTSurvey.get(id=sid)
    if not survey:
        raise toolkit.ObjectNotFound("Survey not found: %s" % sid)

    toolkit.check_access("cstoolbox_survey_delete", context, data_dict)

    cst_db.CSTSurveyColumn.delete_by_survey(survey.id)
    survey.delete()
    model.Session.commit()
    log.info("cstoolbox: deleted survey '%s' (%s)", survey.title, sid)
    return {}


def cstoolbox_survey_list(context, data_dict):
    toolkit.check_access("cstoolbox_survey_list", context, data_dict)

    user = context.get("user")
    user_obj = model.User.get(user) if user else None
    is_sysadmin = bool(user_obj and user_obj.sysadmin)

    try:
        limit = int(data_dict.get("limit", 100))
        offset = int(data_dict.get("offset", 0))
    except (ValueError, TypeError):
        raise toolkit.ValidationError(
            {"message": ["limit and offset must be valid integers"]}
        )

    results, total = cst_db.CSTSurvey.list_surveys(
        org_id=data_dict.get("org_id") or None,
        database=data_dict.get("database") or None,
        submission_status=data_dict.get("submission_status") or None,
        q=data_dict.get("q") or None,
        order_by=data_dict.get("order_by", "modified"),
        limit=limit,
        offset=offset,
    )

    visible = []
    for s in results:
        d = s.as_dict()
        if d["submission_status"] == "approved":
            visible.append(d)
        elif is_sysadmin:
            visible.append(d)
        elif user_obj and d.get("user_id") == user_obj.id:
            visible.append(d)
    return {"results": visible, "count": len(visible), "total": total}


# ─────────────────────────────────────────────────────────
# Survey data proxy + GeoJSON / CSV
# ─────────────────────────────────────────────────────────

def _resolve_survey(data_dict):
    ref = data_dict.get("id") or data_dict.get("name")
    if not ref:
        raise toolkit.ValidationError({"id": ["Missing value"]})
    survey = cst_db.CSTSurvey.get(id=ref) or cst_db.CSTSurvey.get(name=ref)
    if not survey:
        raise toolkit.ObjectNotFound("Survey not found: %s" % ref)
    return survey


def _filter_columns(rows, allowed_columns, survey):
    """Keep only the columns we want to expose to the client.

    Always preserve geometry/date/identity columns; drop the meta junk
    (``_uri``, image blobs) unless explicitly requested in *allowed_columns*.
    """
    keep_always = {
        survey.date_field, survey.lat_field, survey.lon_field, survey.site_field,
        "sample_date", "sample_time", "timestamp", "obs_date",
        "member_state", "sampling_group", "group_members", "instrument", "comment",
    }
    if allowed_columns:
        keep = keep_always | set(allowed_columns)
    else:
        keep = None  # caller wants everything

    out = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        if keep is None:
            # Drop only the obvious meta/blob columns by default.
            new_row = {
                k: v for k, v in row.items()
                if k not in _META_COLUMNS or k in (
                    survey.lat_field, survey.lon_field, survey.date_field,
                )
            }
        else:
            new_row = {k: v for k, v in row.items() if k in keep}
        out.append(new_row)
    return out


def cstoolbox_survey_data(context, data_dict):
    """Live-proxy observation rows from Quartex with optional date window.

    :param id: Survey UUID or slug (required)
    :param start: ISO-ish start datetime (optional)
    :param end: ISO-ish end datetime (optional)
    :param time_range: Preset (1w/1m/3m/6m/1y/all) (optional)
    :param columns: CSV whitelist of column names to keep (optional)
    """
    survey = _resolve_survey(data_dict)
    toolkit.check_access("cstoolbox_survey_data", context, {
        "id": survey.id,
        "name": survey.name,
        "submission_status": survey.submission_status,
        "owner_org": survey.owner_org,
        "user_id": survey.user_id,
    })

    start, end, label = _resolve_window(
        data_dict, default_preset=survey.default_time_range,
    )

    rows = api_client.get_view_data(
        survey.database, survey.schema, survey.view_name,
        start=start, end=end, date_field=survey.date_field,
    )

    column_filter = data_dict.get("columns")
    if isinstance(column_filter, str):
        column_filter = [c.strip() for c in column_filter.split(",") if c.strip()]
    rows = _filter_columns(rows, column_filter, survey)

    # Site filter (case-sensitive exact match list).
    site_filter = data_dict.get("site")
    if site_filter:
        if isinstance(site_filter, str):
            site_filter = [s.strip() for s in site_filter.split(",") if s.strip()]
        site_field = survey.site_field
        rows = [r for r in rows if r.get(site_field) in site_filter]

    survey_dict = survey.as_dict()
    return {
        "survey": {
            "id": survey_dict["id"],
            "name": survey_dict["name"],
            "title": survey_dict["title"],
            "database": survey_dict["database"],
            "schema": survey_dict["schema"],
            "view_name": survey_dict["view_name"],
            "date_field": survey_dict["date_field"],
            "lat_field": survey_dict["lat_field"],
            "lon_field": survey_dict["lon_field"],
            "site_field": survey_dict["site_field"],
        },
        "columns": survey_dict["columns"],
        "window": {
            "start": str(start) if start else None,
            "end": str(end) if end else None,
            "label": label,
        },
        "rows": rows,
        "row_count": len(rows),
        "generated_at": int(time.time()),
    }


def _row_iso_timestamp(row, survey):
    """Best-effort ISO 8601 timestamp from a row.

    Combines ``date_field`` with the optional ``sample_time``/``time``
    sibling when both are present, otherwise returns the date-only string.
    """
    date_value = row.get(survey.date_field)
    if not date_value:
        return None
    date_s = str(date_value)
    # If the value already looks like a full ISO timestamp, take it.
    if "T" in date_s or len(date_s) > 10:
        return date_s.replace(" ", "T")
    # Look for a sibling time column.
    time_s = None
    for cand in ("sample_time", "obs_time", "time"):
        v = row.get(cand)
        if v:
            time_s = str(v)
            break
    if time_s:
        # Quartex sometimes returns "03:08:00" — keep just the HH:MM:SS bit.
        time_s = time_s.split(".")[0]
        return "%sT%sZ" % (date_s, time_s)
    return "%sT00:00:00Z" % date_s


def _row_coords(row, survey):
    """Return ``[lon, lat]`` for a row, or ``None`` if not renderable.

    Filters NaN, ±Infinity, and out-of-range values — any of which would
    crash TerriaJS / Leaflet downstream.
    """
    lat = _safe_float(row.get(survey.lat_field))
    lon = _safe_float(row.get(survey.lon_field))
    if lat is None or lon is None:
        return None
    if lat < -90 or lat > 90 or lon < -180 or lon > 180:
        return None
    return [lon, lat]


def _clean_props(row, survey, time_property):
    """Build the GeoJSON Feature properties dict for one row."""
    props = {}
    for k, v in row.items():
        if v is None:
            continue
        # Drop image/meta blobs from properties to keep Terria styling clean.
        if k in _META_COLUMNS:
            continue
        # Round floats lightly so we don't dump 10-digit precision.
        if isinstance(v, float):
            props[k] = round(v, 6)
        else:
            props[k] = v
    # Always include an ISO time property suitable for Terria.
    iso = _row_iso_timestamp(row, survey)
    if iso:
        props[time_property or "date"] = iso
    return props


def cstoolbox_survey_geojson(context, data_dict):
    """Return one Feature per observation row in the given date window."""
    survey = _resolve_survey(data_dict)
    toolkit.check_access("cstoolbox_survey_geojson", context, {
        "id": survey.id,
        "name": survey.name,
        "submission_status": survey.submission_status,
        "owner_org": survey.owner_org,
        "user_id": survey.user_id,
    })

    start, end, label = _resolve_window(
        data_dict, default_preset=survey.default_time_range,
    )

    rows = api_client.get_view_data(
        survey.database, survey.schema, survey.view_name,
        start=start, end=end, date_field=survey.date_field,
    )

    time_property = (data_dict.get("time_property") or "date").strip() or "date"
    mode = (data_dict.get("mode") or "expanded").strip().lower()
    if mode not in ("expanded", "compact"):
        mode = "expanded"

    features = []

    if mode == "expanded":
        for row in rows:
            coords = _row_coords(row, survey)
            if not coords:
                continue
            features.append({
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": coords},
                "properties": _clean_props(row, survey, time_property),
            })
    else:
        # Compact: one feature per site with latest values + series for
        # configured numeric columns.
        configured_cols = [c.column_name for c in cst_db.CSTSurveyColumn.get_by_survey(survey.id)]
        if not configured_cols:
            # Auto-detect numeric columns from the first row.
            if rows:
                configured_cols = [
                    k for k, v in rows[0].items()
                    if _is_number(v) and k not in (survey.lat_field, survey.lon_field)
                ]

        site_buckets = {}
        for row in rows:
            site = row.get(survey.site_field) or "(unspecified)"
            site_buckets.setdefault(site, []).append(row)

        for site, site_rows in site_buckets.items():
            # Sort by timestamp ascending so series[-1] is latest.
            site_rows.sort(key=lambda r: str(r.get(survey.date_field) or ""))
            last = site_rows[-1]
            coords = _row_coords(last, survey)
            if not coords:
                # Try to fall back to any row in the bucket with valid geom.
                for r in site_rows:
                    coords = _row_coords(r, survey)
                    if coords:
                        break
            if not coords:
                continue
            props = _clean_props(last, survey, time_property)
            props["_site"] = site
            series = {}
            for col in configured_cols:
                seq = []
                for r in site_rows:
                    iso = _row_iso_timestamp(r, survey)
                    val = r.get(col)
                    if iso and _is_number(val):
                        seq.append([iso, float(val)])
                if seq:
                    series[col] = seq
            if series:
                props["series"] = series
            features.append({
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": coords},
                "properties": props,
            })

    return {
        "type": "FeatureCollection",
        "features": features,
        "survey": {
            "id": survey.id,
            "name": survey.name,
            "title": survey.title,
            "database": survey.database,
            "schema": survey.schema,
            "view_name": survey.view_name,
        },
        "window": {
            "start": str(start) if start else None,
            "end": str(end) if end else None,
            "label": label,
            "date_field": survey.date_field,
        },
        "terria": {"type": "geojson", "timeProperty": time_property},
    }


def cstoolbox_survey_csv(context, data_dict):
    """Return rows as CSV. Returns a dict with ``csv_content``."""
    survey = _resolve_survey(data_dict)
    toolkit.check_access("cstoolbox_survey_csv", context, {
        "id": survey.id,
        "name": survey.name,
        "submission_status": survey.submission_status,
        "owner_org": survey.owner_org,
        "user_id": survey.user_id,
    })

    start, end, _ = _resolve_window(
        data_dict, default_preset=survey.default_time_range,
    )
    rows = api_client.get_view_data(
        survey.database, survey.schema, survey.view_name,
        start=start, end=end, date_field=survey.date_field,
    )

    if not rows:
        return {"csv_content": "", "row_count": 0,
                "survey": {"name": survey.name, "title": survey.title}}

    # Build a stable header order: identity/geometry/date first, then numeric, then rest.
    first = rows[0]
    keys = list(first.keys())
    priority = [
        survey.site_field, "member_state", "sampling_group", "group_members",
        survey.date_field, "sample_time", "timestamp",
        survey.lat_field, survey.lon_field,
    ]
    seen = set()
    ordered = []
    for k in priority:
        if k in keys and k not in seen:
            ordered.append(k); seen.add(k)
    for k in keys:
        if k not in seen and k not in _META_COLUMNS:
            ordered.append(k); seen.add(k)

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(ordered)
    lat_key = survey.lat_field
    lon_key = survey.lon_field
    for row in rows:
        out = []
        for k in ordered:
            v = row.get(k, "")
            if v is None:
                out.append("")
                continue
            # Sanitise lat/lon so TerriaJS never sees NaN/Inf cells.
            if k == lat_key or k == lon_key:
                f = _safe_float(v)
                out.append("" if f is None else f)
                continue
            out.append(v)
        writer.writerow(out)

    return {
        "csv_content": buf.getvalue(),
        "row_count": len(rows),
        "survey": {
            "id": survey.id,
            "name": survey.name,
            "title": survey.title,
        },
    }


# ─────────────────────────────────────────────────────────
# Admin helpers — view picker + column auto-detect
# ─────────────────────────────────────────────────────────

def cstoolbox_fetch_views(context, data_dict):
    """Proxy ``GetSurveyViews`` for the create form's view picker."""
    toolkit.check_access("cstoolbox_fetch_views", context, data_dict)

    database = (data_dict.get("database") or "").strip()
    if not database:
        raise toolkit.ValidationError({"database": ["Missing value"]})
    views = api_client.get_views(database)
    return {"database": database, "views": views}


def _classify_column(name, sample_value):
    """Heuristic classifier for column auto-detection."""
    if sample_value is None:
        return "unknown"
    if name in _META_COLUMNS:
        return "meta"
    if name in ("latitude", "longitude", "lat", "lon"):
        return "geo"
    if isinstance(sample_value, bool):
        return "boolean"
    if _is_number(sample_value):
        return "numeric"
    if isinstance(sample_value, str) and _looks_like_iso_date(sample_value):
        return "date"
    return "categorical"


_UNIT_HINTS = (
    ("μs_cm", "µS/cm"), ("us_cm", "µS/cm"),
    ("mgl", "mg/L"), ("ppm", "ppm"), ("ntu", "NTU"),
    ("degc", "°C"), ("masl", "m a.s.l."),
    ("dgh", "dGH"), ("dkh", "dKH"),
)


def _humanize_label(name):
    """Turn ``electrical_conductivity_μs_cm`` into ``Electrical Conductivity``."""
    base = name
    unit = None
    for suffix, label in _UNIT_HINTS:
        if base.lower().endswith("_" + suffix.lower()):
            base = base[: -len(suffix) - 1]
            unit = label
            break
    title = re.sub(r"[_\s]+", " ", base).strip().title()
    return title, unit


def cstoolbox_fetch_view_columns(context, data_dict):
    """Sample one row from a view and classify each column.

    Returns a list of ``{column_name, label, unit, kind}`` suggestions plus
    a recommended ``date_field`` (``sample_date`` if present, else
    ``obs_date``, else the first date-looking column).
    """
    toolkit.check_access("cstoolbox_fetch_view_columns", context, data_dict)

    database = (data_dict.get("database") or "").strip()
    schema = (data_dict.get("schema") or "").strip()
    view = (data_dict.get("view") or "").strip()
    if not (database and schema and view):
        raise toolkit.ValidationError(
            {"database": ["database, schema, and view are required"]}
        )

    # We only need a small slice — Quartex returns the full view, so we
    # take whatever it gives us and inspect just the first row.
    rows = api_client.get_view_data(database, schema, view)
    if not rows:
        return {"columns": [], "recommended_date_field": "obs_date"}

    first = rows[0]
    columns = []
    date_candidate = None
    lat_candidate = None
    lon_candidate = None
    site_candidate = None
    for name, value in first.items():
        kind = _classify_column(name, value)
        if kind == "date" and not date_candidate:
            if name in ("sample_date", "obs_date"):
                date_candidate = name
            elif date_candidate is None:
                date_candidate = name
        if kind == "geo":
            if "lat" in name.lower() and not lat_candidate:
                lat_candidate = name
            elif "lon" in name.lower() and not lon_candidate:
                lon_candidate = name
        if not site_candidate and name in ("site_name", "site", "station_name"):
            site_candidate = name
        label, unit = _humanize_label(name) if kind == "numeric" else (
            re.sub(r"[_\s]+", " ", name).title(), None,
        )
        columns.append({
            "column_name": name,
            "kind": kind,
            "label": label,
            "unit": unit,
            "sample": value if kind != "meta" else None,
            "suggested_for_chart": kind == "numeric",
        })

    return {
        "columns": columns,
        "recommended_date_field": date_candidate or "obs_date",
        "recommended_lat_field": lat_candidate or "latitude",
        "recommended_lon_field": lon_candidate or "longitude",
        "recommended_site_field": site_candidate or "site_name",
        "row_sample": first,
    }


# ─────────────────────────────────────────────────────────
# Collection CRUD
# ─────────────────────────────────────────────────────────

def _save_collection_surveys(collection_id, survey_refs):
    """Replace the surveys associated with a collection.

    *survey_refs* is a list of survey ids or names.
    """
    cst_db.CSTCollectionSurvey.delete_by_collection(collection_id)
    for idx, ref in enumerate(survey_refs or []):
        if not ref:
            continue
        survey = cst_db.CSTSurvey.get(id=ref) or cst_db.CSTSurvey.get(name=ref)
        if not survey:
            continue
        assoc = cst_db.CSTCollectionSurvey(
            collection_id=collection_id,
            survey_id=survey.id,
            sort_order=idx,
        )
        model.Session.add(assoc)


def cstoolbox_collection_create(context, data_dict):
    toolkit.check_access("cstoolbox_collection_create", context, data_dict)

    if not data_dict.get("name"):
        base = _slugify(data_dict.get("title", ""))
        data_dict["name"] = _unique_slug(
            base, lambda n: cst_db.CSTCollection.get(name=n) is not None
        )

    clean, errors = _validate_data(data_dict, collection_create_schema(), context)
    if errors:
        raise toolkit.ValidationError(errors)

    user_obj = model.User.get(context.get("user"))

    coll = cst_db.CSTCollection(
        title=clean["title"],
        name=clean["name"],
        description=clean.get("description") or "",
        owner_org=clean.get("owner_org") or "",
        time_range=clean.get("time_range") or "90d",
        export_format=clean.get("export_format") or "geojson",
        geojson_mode=clean.get("geojson_mode") or "expanded",
        time_property=clean.get("time_property") or "date",
        user_id=user_obj.id if user_obj else None,
    )
    model.Session.add(coll)
    model.Session.flush()

    refs = data_dict.get("survey_ids") or []
    if isinstance(refs, str):
        refs = [s.strip() for s in refs.split(",") if s.strip()]
    _save_collection_surveys(coll.id, refs)

    model.Session.commit()
    log.info("cstoolbox: created collection '%s' (%s)", coll.title, coll.id)
    return coll.as_dict()


def cstoolbox_collection_show(context, data_dict):
    ref = data_dict.get("id") or data_dict.get("name")
    if not ref:
        raise toolkit.ValidationError({"id": ["Missing value"]})

    coll = cst_db.CSTCollection.get(id=ref) or cst_db.CSTCollection.get(name=ref)
    if not coll:
        raise toolkit.ObjectNotFound("Collection not found: %s" % ref)

    toolkit.check_access("cstoolbox_collection_show", context, data_dict)

    result = coll.as_dict()
    assocs = cst_db.CSTCollectionSurvey.get_by_collection(coll.id)
    surveys = []
    for a in assocs:
        s = cst_db.CSTSurvey.get(id=a.survey_id)
        if s:
            surveys.append(s.as_dict())
    result["surveys_detail"] = surveys
    return result


def cstoolbox_collection_update(context, data_dict):
    cid = data_dict.get("id")
    if not cid:
        raise toolkit.ValidationError({"id": ["Missing value"]})

    coll = cst_db.CSTCollection.get(id=cid)
    if not coll:
        raise toolkit.ObjectNotFound("Collection not found: %s" % cid)

    toolkit.check_access("cstoolbox_collection_update", context, {"id": coll.id})

    ctx = dict(context, collection_id=coll.id)
    clean, errors = _validate_data(data_dict, collection_update_schema(), ctx)
    if errors:
        raise toolkit.ValidationError(errors)

    updatable = (
        "title", "name", "description", "owner_org",
        "time_range", "export_format", "geojson_mode", "time_property",
    )
    for field in updatable:
        if field in clean and clean[field] is not None:
            setattr(coll, field, clean[field])

    coll.modified = datetime.datetime.utcnow()

    if "survey_ids" in data_dict:
        refs = data_dict["survey_ids"]
        if isinstance(refs, str):
            refs = [s.strip() for s in refs.split(",") if s.strip()]
        _save_collection_surveys(coll.id, refs)

    model.Session.commit()
    return coll.as_dict()


def cstoolbox_collection_delete(context, data_dict):
    cid = data_dict.get("id")
    if not cid:
        raise toolkit.ValidationError({"id": ["Missing value"]})

    coll = cst_db.CSTCollection.get(id=cid)
    if not coll:
        raise toolkit.ObjectNotFound("Collection not found: %s" % cid)

    toolkit.check_access("cstoolbox_collection_delete", context, {"id": coll.id})

    cst_db.CSTCollectionSurvey.delete_by_collection(coll.id)
    model.Session.delete(coll)
    model.Session.commit()
    return {"success": True}


def cstoolbox_collection_list(context, data_dict):
    toolkit.check_access("cstoolbox_collection_list", context, data_dict)

    try:
        limit = int(data_dict.get("limit", 100))
        offset = int(data_dict.get("offset", 0))
    except (ValueError, TypeError):
        limit, offset = 100, 0

    results, total = cst_db.CSTCollection.list_collections(
        owner_org=data_dict.get("owner_org") or None,
        q=data_dict.get("q") or None,
        limit=limit,
        offset=offset,
    )
    return {"results": [c.as_dict() for c in results], "count": total}


def cstoolbox_collection_geojson(context, data_dict):
    coll_data = cstoolbox_collection_show(context, data_dict)
    toolkit.check_access("cstoolbox_collection_geojson", context, data_dict)

    mode = (data_dict.get("mode") or coll_data.get("geojson_mode") or "expanded").lower()
    if mode not in ("expanded", "compact"):
        mode = "expanded"
    time_property = (
        data_dict.get("time_property") or coll_data.get("time_property") or "date"
    )
    time_range = data_dict.get("time_range") or coll_data.get("time_range")

    features = []
    for survey_dict in coll_data.get("surveys_detail", []):
        sub_data = {
            "id": survey_dict["id"],
            "mode": mode,
            "time_property": time_property,
            "time_range": time_range,
            "start": data_dict.get("start"),
            "end": data_dict.get("end"),
        }
        try:
            geojson = cstoolbox_survey_geojson(context, sub_data)
        except Exception as e:
            log.warning("cstoolbox: collection skipped survey '%s': %s",
                        survey_dict.get("name"), e)
            continue
        # Tag each feature with its source survey so consumers can split.
        for feat in geojson.get("features", []):
            props = feat.setdefault("properties", {})
            props.setdefault("_survey", survey_dict["name"])
            props.setdefault("_survey_title", survey_dict["title"])
            features.append(feat)

    return {
        "type": "FeatureCollection",
        "features": features,
        "collection": {
            "id": coll_data["id"],
            "name": coll_data["name"],
            "title": coll_data["title"],
            "mode": mode,
        },
        "terria": {"type": "geojson", "timeProperty": time_property},
    }


def cstoolbox_collection_csv(context, data_dict):
    """Single, uniform CSV for a collection — Terria-friendly.

    The previous implementation concatenated one CSV block per survey with
    blank lines and ``# Survey:`` comment headers. TerriaJS' CSV catalog
    parser does not understand that; it reads every row against the first
    header, so the comment + secondary headers are parsed as garbage data
    with NaN coordinates and the renderer crashes.

    The new shape:
      - one header row
      - columns: ``_survey, _survey_title, _site, latitude, longitude,
        date, time, <numeric measurements alphabetically>,
        <other columns alphabetically>``
      - lat/lon coerced to clean floats; rows with invalid coords are dropped
      - no blank lines, no comments
      - each survey's site column (which differs by view: ``site`` vs
        ``site_name``) is normalised into ``_site`` for cross-survey
        colour-by support
    """
    coll_data = cstoolbox_collection_show(context, data_dict)
    toolkit.check_access("cstoolbox_collection_csv", context, data_dict)

    BASE_COLS = ["_survey", "_survey_title", "_site",
                 "latitude", "longitude", "date", "time"]

    # First pass: collect rows + survey metadata so we can compute the
    # full superset of columns.
    rows_out = []  # list of dicts already augmented with _survey/_site/etc.
    extra_columns = set()  # all upstream column names we've seen

    for survey_dict in coll_data.get("surveys_detail", []):
        sub_data = {
            "id": survey_dict["id"],
            "time_range": data_dict.get("time_range") or coll_data.get("time_range"),
            "start": data_dict.get("start"),
            "end": data_dict.get("end"),
        }
        try:
            block = cstoolbox_survey_data(context, sub_data)
        except Exception as e:
            log.warning(
                "cstoolbox: collection csv skipped '%s': %s",
                survey_dict.get("name"), e,
            )
            continue

        survey_meta = block.get("survey", {})
        site_field = survey_meta.get("site_field") or "site_name"
        lat_field = survey_meta.get("lat_field") or "latitude"
        lon_field = survey_meta.get("lon_field") or "longitude"
        date_field = survey_meta.get("date_field") or "obs_date"
        time_field = "sample_time" if "sample_time" in (
            block.get("rows", [{}])[0] if block.get("rows") else {}
        ) else "time"

        for row in block.get("rows", []):
            lat = _safe_float(row.get(lat_field))
            lon = _safe_float(row.get(lon_field))
            if lat is None or lon is None:
                continue  # Terria can't render rows without geometry

            out_row = {
                "_survey": survey_dict.get("name", ""),
                "_survey_title": survey_dict.get("title", ""),
                "_site": row.get(site_field) or "",
                "latitude": lat,
                "longitude": lon,
                "date": row.get(date_field) or "",
                "time": row.get(time_field) or "",
            }
            # Copy through every other column (skip meta/blob columns).
            for k, v in row.items():
                if k in _META_COLUMNS:
                    continue
                if k in (lat_field, lon_field, date_field, time_field, site_field):
                    continue
                out_row[k] = v
                extra_columns.add(k)
            rows_out.append(out_row)

    # Choose stable column order: base, then numeric measurements, then rest.
    numeric_cols = []
    other_cols = []
    for col in sorted(extra_columns):
        # A column is "numeric" if any non-empty value parses as a float.
        is_num = False
        for r in rows_out:
            v = r.get(col)
            if v is None or v == "":
                continue
            if isinstance(v, (int, float)) or _is_number(v):
                is_num = True
            break
        (numeric_cols if is_num else other_cols).append(col)
    headers = BASE_COLS + numeric_cols + other_cols

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(headers)
    for r in rows_out:
        writer.writerow([
            "" if r.get(h) is None else r.get(h)
            for h in headers
        ])

    return {
        "csv_content": buf.getvalue(),
        "row_count": len(rows_out),
        "collection": {
            "id": coll_data["id"],
            "name": coll_data["name"],
            "title": coll_data["title"],
        },
    }
