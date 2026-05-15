"""Flask blueprints for the cstoolbox plugin."""

import json
import logging

from flask import Blueprint, Response

import ckan.model as model
import ckan.plugins.toolkit as toolkit
import ckan.lib.helpers as h

log = logging.getLogger(__name__)

cst_surveys = Blueprint(
    "cstoolbox_surveys",
    __name__,
    url_prefix="/cstoolbox-survey",
    template_folder="templates",
)

cst_collections = Blueprint(
    "cstoolbox_collections",
    __name__,
    url_prefix="/cstoolbox-collection",
    template_folder="templates",
)


# ─────────────────────────────────────────────────────────
# Shared helpers
# ─────────────────────────────────────────────────────────

def _ctx():
    return {
        "model": model,
        "session": model.Session,
        "user": toolkit.g.user,
    }


def _organizations():
    try:
        return toolkit.get_action("organization_list_for_user")(
            _ctx(), {"permission": "create_dataset"}
        )
    except Exception:
        return []


def _format_errors(errors):
    out = {}
    for field, msgs in errors.items():
        if isinstance(msgs, list):
            out[field] = "; ".join(str(m) for m in msgs)
        else:
            out[field] = str(msgs)
    return out


def _json_response(payload, status=200, mimetype="application/json", headers=None):
    base_headers = {"Access-Control-Allow-Origin": "*"}
    if headers:
        base_headers.update(headers)
    return Response(
        json.dumps(payload, ensure_ascii=False, default=str),
        status=status,
        mimetype=mimetype,
        headers=base_headers,
    )


def _parse_columns_from_form(form):
    """Parse the survey form's column rows.

    Each row submits ``col_name_<i>``, ``col_label_<i>``, ``col_unit_<i>``,
    ``col_chart_<i>``. A row is included only if ``col_include_<i>`` is set
    or ``col_name_<i>`` is non-empty (back-compat).
    """
    cols = []
    i = 0
    while True:
        name = form.get("col_name_%d" % i)
        if name is None:
            break
        included = form.get("col_include_%d" % i) is not None
        if included and name.strip():
            cols.append({
                "column_name": name.strip(),
                "label": (form.get("col_label_%d" % i, "") or "").strip(),
                "unit": (form.get("col_unit_%d" % i, "") or "").strip(),
                "chart_type": (form.get("col_chart_%d" % i, "line") or "line").strip(),
                "sort_order": i,
            })
        i += 1
    return cols


# ─────────────────────────────────────────────────────────
# Survey routes
# ─────────────────────────────────────────────────────────

@cst_surveys.route("/", methods=["GET"])
def index():
    ctx = _ctx()
    data_dict = {
        "q": toolkit.request.args.get("q", ""),
        "database": toolkit.request.args.get("database", ""),
        "submission_status": toolkit.request.args.get("submission_status", ""),
        "org_id": toolkit.request.args.get("org_id", ""),
        "order_by": toolkit.request.args.get("order_by", "modified"),
        "limit": toolkit.request.args.get("limit", "50"),
        "offset": toolkit.request.args.get("offset", "0"),
    }
    try:
        result = toolkit.get_action("cstoolbox_survey_list")(ctx, data_dict)
    except toolkit.NotAuthorized:
        toolkit.abort(403, "Not authorized")
    except Exception as e:
        log.error("Error listing surveys: %s", e)
        result = {"results": [], "count": 0}

    extra_vars = {
        "surveys": result["results"],
        "count": result["count"],
        "q": data_dict["q"],
        "database": data_dict["database"],
        "submission_status": data_dict["submission_status"],
        "org_id": data_dict["org_id"],
        "organizations": _organizations(),
    }
    return toolkit.render("cstoolbox/survey_index.html", extra_vars=extra_vars)


@cst_surveys.route("/new", methods=["GET", "POST"])
def new():
    ctx = _ctx()
    try:
        toolkit.check_access("cstoolbox_survey_create", ctx, {})
    except toolkit.NotAuthorized:
        toolkit.abort(403, "Not authorized to register surveys")

    errors, error_summary, data = {}, {}, {}

    if toolkit.request.method == "POST":
        data = dict(toolkit.request.form)
        data["columns"] = _parse_columns_from_form(toolkit.request.form)
        submission_action = data.pop("submission_action", "draft")
        data["submission_action"] = submission_action
        try:
            survey = toolkit.get_action("cstoolbox_survey_create")(ctx, data)
            h.flash_success("Survey registered.")
            return h.redirect_to("cstoolbox_surveys.show", name=survey["name"])
        except toolkit.ValidationError as e:
            errors = e.error_dict or {}
            error_summary = _format_errors(errors)
        except toolkit.NotAuthorized:
            toolkit.abort(403, "Not authorized")

    return toolkit.render("cstoolbox/survey_edit_base.html", extra_vars={
        "data": data,
        "errors": errors,
        "error_summary": error_summary,
        "organizations": _organizations(),
        "form_action": toolkit.url_for("cstoolbox_surveys.new"),
        "is_edit": False,
    })


@cst_surveys.route("/<name>", methods=["GET"])
def show(name):
    ctx = _ctx()
    try:
        survey = toolkit.get_action("cstoolbox_survey_show")(ctx, {"id": name})
    except toolkit.ObjectNotFound:
        toolkit.abort(404, "Survey not found")
    except toolkit.NotAuthorized:
        toolkit.abort(403, "Not authorized")

    org = None
    if survey.get("owner_org"):
        try:
            org = toolkit.get_action("organization_show")(ctx, {"id": survey["owner_org"]})
        except Exception:
            org = None

    return toolkit.render("cstoolbox/survey_read.html", extra_vars={
        "survey": survey,
        "org": org,
    })


@cst_surveys.route("/<name>/edit", methods=["GET", "POST"])
def edit(name):
    ctx = _ctx()
    try:
        survey = toolkit.get_action("cstoolbox_survey_show")(ctx, {"id": name})
    except toolkit.ObjectNotFound:
        toolkit.abort(404, "Survey not found")
    except toolkit.NotAuthorized:
        toolkit.abort(403, "Not authorized")

    try:
        toolkit.check_access("cstoolbox_survey_update", ctx, {"id": survey["id"]})
    except toolkit.NotAuthorized:
        toolkit.abort(403, "Not authorized to edit this survey")

    errors, error_summary, data = {}, {}, survey

    if toolkit.request.method == "POST":
        data = dict(toolkit.request.form)
        data["id"] = survey["id"]
        data["columns"] = _parse_columns_from_form(toolkit.request.form)
        submission_action = data.pop("submission_action", None)
        if submission_action:
            data["submission_action"] = submission_action
        try:
            updated = toolkit.get_action("cstoolbox_survey_update")(ctx, data)
            h.flash_success("Survey updated.")
            return h.redirect_to("cstoolbox_surveys.show", name=updated["name"])
        except toolkit.ValidationError as e:
            errors = e.error_dict or {}
            error_summary = _format_errors(errors)
        except toolkit.NotAuthorized:
            toolkit.abort(403, "Not authorized")

    return toolkit.render("cstoolbox/survey_edit_base.html", extra_vars={
        "data": data,
        "errors": errors,
        "error_summary": error_summary,
        "organizations": _organizations(),
        "form_action": toolkit.url_for("cstoolbox_surveys.edit", name=name),
        "is_edit": True,
    })


@cst_surveys.route("/<name>/delete", methods=["GET", "POST"])
def delete(name):
    ctx = _ctx()
    try:
        survey = toolkit.get_action("cstoolbox_survey_show")(ctx, {"id": name})
    except toolkit.ObjectNotFound:
        toolkit.abort(404, "Survey not found")
    except toolkit.NotAuthorized:
        toolkit.abort(403, "Not authorized")

    try:
        toolkit.check_access("cstoolbox_survey_delete", ctx, {"id": survey["id"]})
    except toolkit.NotAuthorized:
        toolkit.abort(403, "Only sysadmins can delete surveys")

    if toolkit.request.method == "POST":
        try:
            toolkit.get_action("cstoolbox_survey_delete")(ctx, {"id": survey["id"]})
            h.flash_success("Survey '%s' deleted." % survey["title"])
            return h.redirect_to("cstoolbox_surveys.index")
        except Exception as e:
            h.flash_error("Error deleting survey: %s" % e)
            return h.redirect_to("cstoolbox_surveys.show", name=name)

    return toolkit.render("cstoolbox/survey_confirm_delete.html", extra_vars={
        "survey": survey,
    })


@cst_surveys.route("/<name>/dashboard", methods=["GET"])
def dashboard(name):
    ctx = _ctx()
    try:
        survey = toolkit.get_action("cstoolbox_survey_show")(ctx, {"id": name})
    except toolkit.ObjectNotFound:
        toolkit.abort(404, "Survey not found")
    except toolkit.NotAuthorized:
        toolkit.abort(403, "Not authorized")

    return toolkit.render("cstoolbox/survey_dashboard.html", extra_vars={
        "survey": survey,
        "data_url": toolkit.url_for("cstoolbox_surveys.data", name=survey["name"]),
        "geojson_url": toolkit.url_for("cstoolbox_surveys.geojson", name=survey["name"]),
        "csv_url": toolkit.url_for("cstoolbox_surveys.csv_export", name=survey["name"]),
    })


@cst_surveys.route("/<name>/data", methods=["GET"])
def data(name):
    ctx = _ctx()
    data_dict = {
        "id": name,
        "start": toolkit.request.args.get("start", ""),
        "end": toolkit.request.args.get("end", ""),
        "time_range": toolkit.request.args.get("time_range", ""),
        "columns": toolkit.request.args.get("columns", ""),
        "site": toolkit.request.args.get("site", ""),
    }
    try:
        result = toolkit.get_action("cstoolbox_survey_data")(ctx, data_dict)
    except toolkit.ObjectNotFound:
        return _json_response({"error": "Survey not found"}, status=404)
    except toolkit.NotAuthorized:
        return _json_response({"error": "Not authorized"}, status=403)
    except toolkit.ValidationError as e:
        return _json_response({"error": e.error_dict}, status=400)
    except Exception as e:
        log.exception("Error fetching survey data: %s", e)
        return _json_response({"error": str(e)}, status=500)
    return _json_response(result)


@cst_surveys.route("/<name>/geojson", methods=["GET"])
def geojson(name):
    ctx = _ctx()
    data_dict = {
        "id": name,
        "start": toolkit.request.args.get("start", ""),
        "end": toolkit.request.args.get("end", ""),
        "time_range": toolkit.request.args.get("time_range", ""),
        "mode": toolkit.request.args.get("mode", ""),
        "time_property": toolkit.request.args.get("time_property", ""),
    }
    try:
        result = toolkit.get_action("cstoolbox_survey_geojson")(ctx, data_dict)
    except toolkit.ObjectNotFound:
        return _json_response({"error": "Survey not found"}, status=404)
    except toolkit.ValidationError as e:
        return _json_response({"error": e.error_dict}, status=400)
    except Exception as e:
        log.exception("Error building survey GeoJSON: %s", e)
        return _json_response({"error": str(e)}, status=500)
    return _json_response(
        result,
        mimetype="application/geo+json",
        headers={
            "Content-Disposition": "inline; filename=cstoolbox_%s.geojson" % name,
        },
    )


@cst_surveys.route("/<name>/csv", methods=["GET"])
def csv_export(name):
    ctx = _ctx()
    data_dict = {
        "id": name,
        "start": toolkit.request.args.get("start", ""),
        "end": toolkit.request.args.get("end", ""),
        "time_range": toolkit.request.args.get("time_range", ""),
    }
    try:
        result = toolkit.get_action("cstoolbox_survey_csv")(ctx, data_dict)
    except toolkit.ObjectNotFound:
        return _json_response({"error": "Survey not found"}, status=404)
    except Exception as e:
        log.exception("Error building survey CSV: %s", e)
        return _json_response({"error": str(e)}, status=500)
    return Response(
        result.get("csv_content", ""),
        mimetype="text/csv",
        headers={
            "Content-Disposition": "attachment; filename=cstoolbox_%s.csv" % name,
            "Access-Control-Allow-Origin": "*",
        },
    )


@cst_surveys.route("/api/views", methods=["GET"])
def api_views():
    ctx = _ctx()
    database = toolkit.request.args.get("database", "").strip()
    if not database:
        return _json_response({"error": "database is required"}, status=400)
    try:
        result = toolkit.get_action("cstoolbox_fetch_views")(ctx, {"database": database})
    except toolkit.NotAuthorized:
        return _json_response({"error": "Not authorized"}, status=403)
    except toolkit.ValidationError as e:
        return _json_response({"error": e.error_dict}, status=400)
    except Exception as e:
        log.exception("Error fetching views: %s", e)
        return _json_response({"error": str(e)}, status=500)
    return _json_response(result)


@cst_surveys.route("/api/columns", methods=["GET"])
def api_columns():
    ctx = _ctx()
    data_dict = {
        "database": toolkit.request.args.get("database", "").strip(),
        "schema": toolkit.request.args.get("schema", "").strip(),
        "view": toolkit.request.args.get("view", "").strip(),
    }
    try:
        result = toolkit.get_action("cstoolbox_fetch_view_columns")(ctx, data_dict)
    except toolkit.NotAuthorized:
        return _json_response({"error": "Not authorized"}, status=403)
    except toolkit.ValidationError as e:
        return _json_response({"error": e.error_dict}, status=400)
    except Exception as e:
        log.exception("Error sampling view columns: %s", e)
        return _json_response({"error": str(e)}, status=500)
    return _json_response(result)


# ─────────────────────────────────────────────────────────
# Collection routes
# ─────────────────────────────────────────────────────────

def _approved_surveys():
    ctx = _ctx()
    try:
        out = toolkit.get_action("cstoolbox_survey_list")(ctx, {
            "submission_status": "approved",
            "limit": "10000",
        })
        return out.get("results", [])
    except Exception:
        return []


@cst_collections.route("/", methods=["GET"])
def coll_index():
    ctx = _ctx()
    data_dict = {
        "q": toolkit.request.args.get("q", ""),
        "owner_org": toolkit.request.args.get("owner_org", ""),
        "limit": toolkit.request.args.get("limit", "50"),
        "offset": toolkit.request.args.get("offset", "0"),
    }
    try:
        result = toolkit.get_action("cstoolbox_collection_list")(ctx, data_dict)
    except toolkit.NotAuthorized:
        toolkit.abort(403, "Not authorized")
    except Exception as e:
        log.error("Error listing collections: %s", e)
        result = {"results": [], "count": 0}

    return toolkit.render("cstoolbox/collection_index.html", extra_vars={
        "collections": result["results"],
        "count": result["count"],
        "q": data_dict["q"],
        "organizations": _organizations(),
    })


@cst_collections.route("/new", methods=["GET", "POST"])
def coll_new():
    ctx = _ctx()
    try:
        toolkit.check_access("cstoolbox_collection_create", ctx, {})
    except toolkit.NotAuthorized:
        toolkit.abort(403, "Not authorized to create collections")

    errors, error_summary, data = {}, {}, {}

    if toolkit.request.method == "POST":
        data = dict(toolkit.request.form)
        data["survey_ids"] = toolkit.request.form.getlist("survey_ids")
        try:
            coll = toolkit.get_action("cstoolbox_collection_create")(ctx, data)
            h.flash_success("Collection created.")
            return h.redirect_to("cstoolbox_collections.coll_show", name=coll["name"])
        except toolkit.ValidationError as e:
            errors = e.error_dict or {}
            error_summary = _format_errors(errors)
        except toolkit.NotAuthorized:
            toolkit.abort(403, "Not authorized")

    return toolkit.render("cstoolbox/collection_edit_base.html", extra_vars={
        "data": data,
        "errors": errors,
        "error_summary": error_summary,
        "organizations": _organizations(),
        "all_surveys": _approved_surveys(),
        "form_action": toolkit.url_for("cstoolbox_collections.coll_new"),
        "is_edit": False,
    })


@cst_collections.route("/<name>", methods=["GET"])
def coll_show(name):
    ctx = _ctx()
    try:
        coll = toolkit.get_action("cstoolbox_collection_show")(ctx, {"id": name})
    except toolkit.ObjectNotFound:
        toolkit.abort(404, "Collection not found")
    except toolkit.NotAuthorized:
        toolkit.abort(403, "Not authorized")

    return toolkit.render("cstoolbox/collection_read.html", extra_vars={
        "collection": coll,
        "geojson_url": toolkit.url_for("cstoolbox_collections.coll_geojson", name=coll["name"]),
        "csv_url": toolkit.url_for("cstoolbox_collections.coll_csv", name=coll["name"]),
        "dashboard_url": toolkit.url_for("cstoolbox_collections.coll_dashboard", name=coll["name"]),
    })


@cst_collections.route("/<name>/edit", methods=["GET", "POST"])
def coll_edit(name):
    ctx = _ctx()
    try:
        coll = toolkit.get_action("cstoolbox_collection_show")(ctx, {"id": name})
    except toolkit.ObjectNotFound:
        toolkit.abort(404, "Collection not found")
    except toolkit.NotAuthorized:
        toolkit.abort(403, "Not authorized")

    try:
        toolkit.check_access("cstoolbox_collection_update", ctx, {"id": coll["id"]})
    except toolkit.NotAuthorized:
        toolkit.abort(403, "Not authorized to edit this collection")

    errors, error_summary, data = {}, {}, coll

    if toolkit.request.method == "POST":
        data = dict(toolkit.request.form)
        data["id"] = coll["id"]
        data["survey_ids"] = toolkit.request.form.getlist("survey_ids")
        try:
            updated = toolkit.get_action("cstoolbox_collection_update")(ctx, data)
            h.flash_success("Collection updated.")
            return h.redirect_to("cstoolbox_collections.coll_show", name=updated["name"])
        except toolkit.ValidationError as e:
            errors = e.error_dict or {}
            error_summary = _format_errors(errors)
        except toolkit.NotAuthorized:
            toolkit.abort(403, "Not authorized")

    return toolkit.render("cstoolbox/collection_edit_base.html", extra_vars={
        "data": data,
        "errors": errors,
        "error_summary": error_summary,
        "organizations": _organizations(),
        "all_surveys": _approved_surveys(),
        "form_action": toolkit.url_for("cstoolbox_collections.coll_edit", name=name),
        "is_edit": True,
    })


@cst_collections.route("/<name>/delete", methods=["GET", "POST"])
def coll_delete(name):
    ctx = _ctx()
    try:
        coll = toolkit.get_action("cstoolbox_collection_show")(ctx, {"id": name})
    except toolkit.ObjectNotFound:
        toolkit.abort(404, "Collection not found")
    except toolkit.NotAuthorized:
        toolkit.abort(403, "Not authorized")

    try:
        toolkit.check_access("cstoolbox_collection_delete", ctx, {"id": coll["id"]})
    except toolkit.NotAuthorized:
        toolkit.abort(403, "Not authorized to delete this collection")

    if toolkit.request.method == "POST":
        try:
            toolkit.get_action("cstoolbox_collection_delete")(ctx, {"id": coll["id"]})
            h.flash_success("Collection '%s' deleted." % coll["title"])
            return h.redirect_to("cstoolbox_collections.coll_index")
        except Exception as e:
            h.flash_error("Error deleting collection: %s" % e)
            return h.redirect_to("cstoolbox_collections.coll_show", name=name)

    return toolkit.render("cstoolbox/collection_confirm_delete.html", extra_vars={
        "collection": coll,
    })


@cst_collections.route("/<name>/dashboard", methods=["GET"])
def coll_dashboard(name):
    ctx = _ctx()
    try:
        coll = toolkit.get_action("cstoolbox_collection_show")(ctx, {"id": name})
    except toolkit.ObjectNotFound:
        toolkit.abort(404, "Collection not found")
    except toolkit.NotAuthorized:
        toolkit.abort(403, "Not authorized")

    return toolkit.render("cstoolbox/collection_dashboard.html", extra_vars={
        "collection": coll,
        "geojson_url": toolkit.url_for("cstoolbox_collections.coll_geojson", name=coll["name"]),
        "csv_url": toolkit.url_for("cstoolbox_collections.coll_csv", name=coll["name"]),
    })


@cst_collections.route("/<name>/geojson", methods=["GET"])
def coll_geojson(name):
    ctx = _ctx()
    data_dict = {
        "id": name,
        "mode": toolkit.request.args.get("mode", ""),
        "time_property": toolkit.request.args.get("time_property", ""),
        "time_range": toolkit.request.args.get("time_range", ""),
        "start": toolkit.request.args.get("start", ""),
        "end": toolkit.request.args.get("end", ""),
    }
    try:
        result = toolkit.get_action("cstoolbox_collection_geojson")(ctx, data_dict)
    except toolkit.ObjectNotFound:
        return _json_response({"error": "Collection not found"}, status=404)
    except Exception as e:
        log.exception("Error building collection GeoJSON: %s", e)
        return _json_response({"error": str(e)}, status=500)
    return _json_response(
        result,
        mimetype="application/geo+json",
        headers={
            "Content-Disposition": "inline; filename=cstoolbox_collection_%s.geojson" % name,
        },
    )


@cst_collections.route("/<name>/csv", methods=["GET"])
def coll_csv(name):
    ctx = _ctx()
    data_dict = {
        "id": name,
        "time_range": toolkit.request.args.get("time_range", ""),
        "start": toolkit.request.args.get("start", ""),
        "end": toolkit.request.args.get("end", ""),
    }
    try:
        result = toolkit.get_action("cstoolbox_collection_csv")(ctx, data_dict)
    except toolkit.ObjectNotFound:
        return _json_response({"error": "Collection not found"}, status=404)
    except Exception as e:
        log.exception("Error building collection CSV: %s", e)
        return _json_response({"error": str(e)}, status=500)
    return Response(
        result.get("csv_content", ""),
        mimetype="text/csv",
        headers={
            "Content-Disposition": "attachment; filename=cstoolbox_collection_%s.csv" % name,
            "Access-Control-Allow-Origin": "*",
        },
    )
