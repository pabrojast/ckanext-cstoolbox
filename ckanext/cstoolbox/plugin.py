"""CKAN extension for the UNESCO Citizen Science Toolbox.

Surfaces curated survey views from the Quartex CST Toolbox API as
browsable, dashboard-able, exportable resources inside CKAN.
"""

import logging

import ckan.plugins as plugins
import ckan.plugins.toolkit as toolkit

from ckanext.cstoolbox import helpers as cst_helpers

log = logging.getLogger(__name__)


class CSToolboxPlugin(plugins.SingletonPlugin):
    plugins.implements(plugins.IConfigurer)
    plugins.implements(plugins.IConfigurable)
    plugins.implements(plugins.IBlueprint)
    plugins.implements(plugins.IActions)
    plugins.implements(plugins.IAuthFunctions)
    plugins.implements(plugins.ITemplateHelpers)

    # ── IConfigurer ─────────────────────────────────

    def update_config(self, config):
        toolkit.add_template_directory(config, "templates")
        toolkit.add_public_directory(config, "public")

    # ── IConfigurable ───────────────────────────────

    def configure(self, config):
        from ckanext.cstoolbox import db as cst_db
        cst_db.init_db()

    # ── IBlueprint ──────────────────────────────────

    def get_blueprint(self):
        from ckanext.cstoolbox.blueprint import cst_surveys, cst_collections
        return [cst_surveys, cst_collections]

    # ── IActions ────────────────────────────────────

    def get_actions(self):
        from ckanext.cstoolbox import actions
        return {
            "cstoolbox_survey_create": actions.cstoolbox_survey_create,
            "cstoolbox_survey_show": actions.cstoolbox_survey_show,
            "cstoolbox_survey_update": actions.cstoolbox_survey_update,
            "cstoolbox_survey_delete": actions.cstoolbox_survey_delete,
            "cstoolbox_survey_list": actions.cstoolbox_survey_list,
            "cstoolbox_survey_data": actions.cstoolbox_survey_data,
            "cstoolbox_survey_geojson": actions.cstoolbox_survey_geojson,
            "cstoolbox_survey_csv": actions.cstoolbox_survey_csv,
            "cstoolbox_fetch_views": actions.cstoolbox_fetch_views,
            "cstoolbox_fetch_view_columns": actions.cstoolbox_fetch_view_columns,
            "cstoolbox_collection_create": actions.cstoolbox_collection_create,
            "cstoolbox_collection_show": actions.cstoolbox_collection_show,
            "cstoolbox_collection_update": actions.cstoolbox_collection_update,
            "cstoolbox_collection_delete": actions.cstoolbox_collection_delete,
            "cstoolbox_collection_list": actions.cstoolbox_collection_list,
            "cstoolbox_collection_geojson": actions.cstoolbox_collection_geojson,
            "cstoolbox_collection_csv": actions.cstoolbox_collection_csv,
        }

    # ── IAuthFunctions ──────────────────────────────

    def get_auth_functions(self):
        from ckanext.cstoolbox import auth
        return {
            "cstoolbox_survey_create": auth.cstoolbox_survey_create,
            "cstoolbox_survey_show": auth.cstoolbox_survey_show,
            "cstoolbox_survey_update": auth.cstoolbox_survey_update,
            "cstoolbox_survey_delete": auth.cstoolbox_survey_delete,
            "cstoolbox_survey_list": auth.cstoolbox_survey_list,
            "cstoolbox_survey_data": auth.cstoolbox_survey_data,
            "cstoolbox_survey_geojson": auth.cstoolbox_survey_geojson,
            "cstoolbox_survey_csv": auth.cstoolbox_survey_csv,
            "cstoolbox_fetch_views": auth.cstoolbox_fetch_views,
            "cstoolbox_fetch_view_columns": auth.cstoolbox_fetch_view_columns,
            "cstoolbox_collection_create": auth.cstoolbox_collection_create,
            "cstoolbox_collection_show": auth.cstoolbox_collection_show,
            "cstoolbox_collection_update": auth.cstoolbox_collection_update,
            "cstoolbox_collection_delete": auth.cstoolbox_collection_delete,
            "cstoolbox_collection_list": auth.cstoolbox_collection_list,
            "cstoolbox_collection_geojson": auth.cstoolbox_collection_geojson,
            "cstoolbox_collection_csv": auth.cstoolbox_collection_csv,
        }

    # ── ITemplateHelpers ────────────────────────────

    def get_helpers(self):
        return cst_helpers.get_helpers()
