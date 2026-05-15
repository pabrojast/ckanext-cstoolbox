"""Template helpers exposed as ``h.cstoolbox_*`` inside Jinja."""

import re

import ckan.model as model
import ckan.plugins.toolkit as toolkit

DATABASE_LABELS = {
    "odk_unesco": "UNESCO",
    "odk_ihp": "IHP",
    "odk_iw": "International Waters",
}

SUBMISSION_STATUS_LABELS = {
    "draft": "Draft",
    "pending": "Pending Review",
    "approved": "Approved",
    "rejected": "Rejected",
}

CHART_TYPE_LABELS = {
    "line": "Line",
    "bar": "Bar",
    "area": "Area",
}

TIME_RANGE_LABELS = {
    "1w": "1 week",
    "1m": "1 month",
    "3m": "3 months",
    "6m": "6 months",
    "1y": "1 year",
    "all": "All time",
}


def cstoolbox_database_label(value):
    return DATABASE_LABELS.get(value, value or "—")


def cstoolbox_submission_status_label(value):
    return SUBMISSION_STATUS_LABELS.get(value, value or "—")


def cstoolbox_submission_badge_class(value):
    return {
        "draft": "badge-secondary",
        "pending": "badge-warning",
        "approved": "badge-success",
        "rejected": "badge-danger",
    }.get(value, "badge-secondary")


def cstoolbox_chart_type_label(value):
    return CHART_TYPE_LABELS.get(value, value or "Line")


def cstoolbox_time_range_label(value):
    return TIME_RANGE_LABELS.get(value, value or "—")


def cstoolbox_allowed_databases():
    """Return the ``(value, label)`` list for the database dropdown."""
    from ckanext.cstoolbox.api_client import allowed_databases
    return [(db, DATABASE_LABELS.get(db, db)) for db in allowed_databases()]


def cstoolbox_humanize(value):
    """Turn ``temp_degc`` into ``Temp Degc`` for a fallback display label."""
    if not value:
        return ""
    text = re.sub(r"[_\s]+", " ", str(value).strip())
    return text.title()


def cstoolbox_is_sysadmin():
    """Return whether the current user is a sysadmin (template-side check)."""
    try:
        user = toolkit.c.user
        if not user:
            return False
        user_obj = model.User.get(user)
        return bool(user_obj and user_obj.sysadmin)
    except Exception:
        return False


def get_helpers():
    return {
        "cstoolbox_database_label": cstoolbox_database_label,
        "cstoolbox_submission_status_label": cstoolbox_submission_status_label,
        "cstoolbox_submission_badge_class": cstoolbox_submission_badge_class,
        "cstoolbox_chart_type_label": cstoolbox_chart_type_label,
        "cstoolbox_time_range_label": cstoolbox_time_range_label,
        "cstoolbox_allowed_databases": cstoolbox_allowed_databases,
        "cstoolbox_humanize": cstoolbox_humanize,
        "cstoolbox_is_sysadmin": cstoolbox_is_sysadmin,
    }
