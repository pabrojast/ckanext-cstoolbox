"""Authorization functions for cstoolbox actions.

Mirrors the stationsdischarge auth model:
- ``show``/``list``/``geojson``/``csv``/``data`` are anonymous-allowed for
  *approved* surveys; for non-approved surveys the author, org editors+, and
  sysadmins may view them.
- ``create`` requires org editor+; only sysadmins may publish directly.
- ``update`` requires author / org editor+ / sysadmin. Only sysadmins may
  approve, reject, or publish.
- ``delete`` requires sysadmin.
"""

import logging

import ckan.model as model
import ckan.plugins.toolkit as toolkit

log = logging.getLogger(__name__)


def _is_sysadmin(context):
    user = context.get("user")
    if not user:
        return False
    user_obj = model.User.get(user)
    return bool(user_obj and user_obj.sysadmin)


def _is_org_member(user_name, org_id, role="editor"):
    if not user_name or not org_id:
        return False
    try:
        members = toolkit.get_action("member_list")(
            {"ignore_auth": True},
            {"id": org_id, "object_type": "user"},
        )
        user_obj = model.User.get(user_name)
        if not user_obj:
            return False
        hierarchy = {"member": 0, "editor": 1, "admin": 2}
        min_level = hierarchy.get(role, 1)
        for member_id, _, member_role in members:
            if member_id == user_obj.id:
                if hierarchy.get(member_role, 0) >= min_level:
                    return True
        return False
    except Exception:
        return False


# ── Survey ──────────────────────────────────────────────

@toolkit.auth_allow_anonymous_access
def cstoolbox_survey_show(context, data_dict):
    """Public for approved surveys, otherwise author / org editor+ / sysadmin."""
    from ckanext.cstoolbox.db import CSTSurvey

    ref = data_dict.get("id") or data_dict.get("name")
    if not ref:
        return {"success": True}

    survey = CSTSurvey.get(id=ref) or CSTSurvey.get(name=ref)
    if not survey:
        return {"success": True}

    if survey.submission_status == "approved":
        return {"success": True}

    if _is_sysadmin(context):
        return {"success": True}

    user = context.get("user")
    if user:
        user_obj = model.User.get(user)
        if user_obj and survey.user_id == user_obj.id:
            return {"success": True}
        if _is_org_member(user, survey.owner_org, "editor"):
            return {"success": True}

    return {"success": False, "msg": "Not authorized to view this survey."}


def cstoolbox_survey_create(context, data_dict):
    if _is_sysadmin(context):
        return {"success": True}
    user = context.get("user")
    if not user:
        return {"success": False, "msg": "Must be logged in to register surveys."}

    submission_action = data_dict.get("submission_action")
    if submission_action == "publish":
        return {"success": False, "msg": "Only sysadmins can publish surveys directly."}

    org_id = data_dict.get("owner_org")
    if org_id and _is_org_member(user, org_id, "editor"):
        return {"success": True}
    return {"success": False, "msg": "Not authorized to register surveys in this organization."}


def cstoolbox_survey_update(context, data_dict):
    if _is_sysadmin(context):
        return {"success": True}

    submission_action = data_dict.get("submission_action")
    if submission_action in ("approve", "reject", "publish"):
        return {"success": False, "msg": "Only sysadmins can approve, reject, or publish surveys."}

    user = context.get("user")
    if not user:
        return {"success": False, "msg": "Must be logged in."}

    from ckanext.cstoolbox.db import CSTSurvey
    ref = data_dict.get("id") or data_dict.get("name")
    survey = None
    if ref:
        survey = CSTSurvey.get(id=ref) or CSTSurvey.get(name=ref)
    if survey:
        user_obj = model.User.get(user)
        if user_obj and survey.user_id == user_obj.id:
            return {"success": True}
        if _is_org_member(user, survey.owner_org, "editor"):
            return {"success": True}
    return {"success": False, "msg": "Not authorized to update this survey."}


def cstoolbox_survey_delete(context, data_dict):
    if _is_sysadmin(context):
        return {"success": True}
    return {"success": False, "msg": "Only sysadmins can delete surveys."}


@toolkit.auth_allow_anonymous_access
def cstoolbox_survey_list(context, data_dict):
    return {"success": True}


@toolkit.auth_allow_anonymous_access
def cstoolbox_survey_data(context, data_dict):
    return cstoolbox_survey_show(context, data_dict)


@toolkit.auth_allow_anonymous_access
def cstoolbox_survey_geojson(context, data_dict):
    return cstoolbox_survey_show(context, data_dict)


@toolkit.auth_allow_anonymous_access
def cstoolbox_survey_csv(context, data_dict):
    return cstoolbox_survey_show(context, data_dict)


# Admin helpers used by the create form.
def cstoolbox_fetch_views(context, data_dict):
    return cstoolbox_survey_create(context, data_dict)


def cstoolbox_fetch_view_columns(context, data_dict):
    return cstoolbox_survey_create(context, data_dict)


# ── Collection ──────────────────────────────────────────

def cstoolbox_collection_create(context, data_dict):
    if _is_sysadmin(context):
        return {"success": True}
    user = context.get("user")
    if not user:
        return {"success": False, "msg": "Must be logged in to create collections."}
    org_id = data_dict.get("owner_org")
    if org_id and _is_org_member(user, org_id, "editor"):
        return {"success": True}
    return {"success": False, "msg": "Not authorized to create collections in this organization."}


@toolkit.auth_allow_anonymous_access
def cstoolbox_collection_show(context, data_dict):
    return {"success": True}


def cstoolbox_collection_update(context, data_dict):
    if _is_sysadmin(context):
        return {"success": True}
    user = context.get("user")
    if not user:
        return {"success": False, "msg": "Must be logged in."}

    from ckanext.cstoolbox.db import CSTCollection
    ref = data_dict.get("id") or data_dict.get("name")
    coll = None
    if ref:
        coll = CSTCollection.get(id=ref) or CSTCollection.get(name=ref)
    if coll:
        user_obj = model.User.get(user)
        if user_obj and coll.user_id == user_obj.id:
            return {"success": True}
        if _is_org_member(user, coll.owner_org, "editor"):
            return {"success": True}
    return {"success": False, "msg": "Not authorized to update this collection."}


def cstoolbox_collection_delete(context, data_dict):
    if _is_sysadmin(context):
        return {"success": True}
    user = context.get("user")
    if not user:
        return {"success": False, "msg": "Must be logged in."}
    from ckanext.cstoolbox.db import CSTCollection
    ref = data_dict.get("id") or data_dict.get("name")
    coll = None
    if ref:
        coll = CSTCollection.get(id=ref) or CSTCollection.get(name=ref)
    if coll:
        user_obj = model.User.get(user)
        if user_obj and coll.user_id == user_obj.id:
            return {"success": True}
    return {"success": False, "msg": "Not authorized to delete this collection."}


@toolkit.auth_allow_anonymous_access
def cstoolbox_collection_list(context, data_dict):
    return {"success": True}


@toolkit.auth_allow_anonymous_access
def cstoolbox_collection_geojson(context, data_dict):
    return {"success": True}


@toolkit.auth_allow_anonymous_access
def cstoolbox_collection_csv(context, data_dict):
    return {"success": True}
