"""SQLAlchemy models for the CST Toolbox plugin.

Four tables, mirroring the (station, station_telemetry_key, dataset,
dataset_station) shape from ``ckanext-stationsdischarge`` but adapted for
row-oriented survey data:

- ``cst_surveys`` — one curated reference to an upstream Quartex view.
- ``cst_survey_columns`` — which measurement columns to chart for a survey,
  with friendly label + unit.
- ``cst_collections`` — optional grouping of surveys for combined exports.
- ``cst_collection_surveys`` — M:N join.
"""

import datetime
import logging
import uuid

from sqlalchemy import Column, types

from ckan import model
from ckan.model.domain_object import DomainObject

try:
    from ckan.plugins.toolkit import BaseModel
except ImportError:
    from ckan.model.meta import metadata
    from sqlalchemy.ext.declarative import declarative_base

    BaseModel = declarative_base(metadata=metadata)

log = logging.getLogger(__name__)


def _make_uuid():
    return str(uuid.uuid4())


# ── Survey ──────────────────────────────────────────────

class CSTSurvey(DomainObject, BaseModel):
    """A curated reference to a Quartex view."""

    __tablename__ = "cst_surveys"

    id = Column(types.UnicodeText, primary_key=True, default=_make_uuid)

    # Identity / presentation
    name = Column(types.UnicodeText, nullable=False, unique=True)
    title = Column(types.UnicodeText, nullable=False)
    description = Column(types.UnicodeText)
    owner_org = Column(types.UnicodeText)

    # Upstream reference
    database = Column(types.UnicodeText, nullable=False)
    schema = Column(types.UnicodeText, nullable=False)
    view_name = Column(types.UnicodeText, nullable=False)

    # Row layout
    date_field = Column(types.UnicodeText, default="obs_date")
    lat_field = Column(types.UnicodeText, default="latitude")
    lon_field = Column(types.UnicodeText, default="longitude")
    site_field = Column(types.UnicodeText, default="site_name")

    # Dashboard / GeoJSON defaults
    default_time_range = Column(types.UnicodeText, default="90d")

    # Submission workflow (same vocabulary as stationsdischarge)
    submission_status = Column(types.UnicodeText, default="draft")
    submitted_at = Column(types.DateTime)
    reviewed_at = Column(types.DateTime)
    reviewed_by = Column(types.UnicodeText)

    user_id = Column(types.UnicodeText)
    created = Column(types.DateTime, default=datetime.datetime.utcnow)
    modified = Column(types.DateTime, default=datetime.datetime.utcnow)

    @classmethod
    def get(cls, **kw):
        return model.Session.query(cls).filter_by(**kw).first()

    @classmethod
    def get_by_upstream(cls, database, schema, view_name):
        return (
            model.Session.query(cls)
            .filter(
                cls.database == database,
                cls.schema == schema,
                cls.view_name == view_name,
            )
            .first()
        )

    @classmethod
    def list_surveys(
        cls,
        org_id=None,
        database=None,
        submission_status=None,
        q=None,
        order_by="modified",
        limit=100,
        offset=0,
    ):
        query = model.Session.query(cls)
        if org_id:
            query = query.filter(cls.owner_org == org_id)
        if database:
            query = query.filter(cls.database == database)
        if submission_status:
            query = query.filter(cls.submission_status == submission_status)
        if q:
            q_escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            q_like = f"%{q_escaped}%"
            query = query.filter(
                cls.title.ilike(q_like, escape="\\")
                | cls.description.ilike(q_like, escape="\\")
                | cls.view_name.ilike(q_like, escape="\\")
            )

        if order_by == "title":
            query = query.order_by(cls.title)
        elif order_by == "created":
            query = query.order_by(cls.created.desc())
        else:
            query = query.order_by(cls.modified.desc())

        total = query.count()
        results = query.offset(offset).limit(limit).all()
        return results, total

    def as_dict(self):
        d = {}
        for col in self.__table__.columns:
            val = getattr(self, col.name, None)
            if isinstance(val, datetime.datetime):
                val = val.isoformat()
            d[col.name] = val
        d["columns"] = [c.as_dict() for c in CSTSurveyColumn.get_by_survey(self.id)]
        return d


class CSTSurveyColumn(DomainObject, BaseModel):
    """A measurement column to chart for a survey."""

    __tablename__ = "cst_survey_columns"

    id = Column(types.UnicodeText, primary_key=True, default=_make_uuid)
    survey_id = Column(types.UnicodeText, nullable=False, index=True)
    column_name = Column(types.UnicodeText, nullable=False)
    label = Column(types.UnicodeText)
    unit = Column(types.UnicodeText)
    chart_type = Column(types.UnicodeText, default="line")
    sort_order = Column(types.Integer, default=0)
    created = Column(types.DateTime, default=datetime.datetime.utcnow)

    @classmethod
    def get_by_survey(cls, survey_id):
        return (
            model.Session.query(cls)
            .filter(cls.survey_id == survey_id)
            .order_by(cls.sort_order, cls.created)
            .all()
        )

    @classmethod
    def delete_by_survey(cls, survey_id):
        model.Session.query(cls).filter(cls.survey_id == survey_id).delete()

    def as_dict(self):
        d = {}
        for col in self.__table__.columns:
            val = getattr(self, col.name, None)
            if isinstance(val, datetime.datetime):
                val = val.isoformat()
            d[col.name] = val
        return d


# ── Collection ──────────────────────────────────────────

class CSTCollection(DomainObject, BaseModel):
    """A grouping of surveys for combined export."""

    __tablename__ = "cst_collections"

    id = Column(types.UnicodeText, primary_key=True, default=_make_uuid)
    name = Column(types.UnicodeText, nullable=False, unique=True)
    title = Column(types.UnicodeText, nullable=False)
    description = Column(types.UnicodeText)
    owner_org = Column(types.UnicodeText)

    # Export defaults
    time_range = Column(types.UnicodeText, default="90d")
    export_format = Column(types.UnicodeText, default="geojson")
    geojson_mode = Column(types.UnicodeText, default="expanded")
    time_property = Column(types.UnicodeText, default="date")

    user_id = Column(types.UnicodeText)
    created = Column(types.DateTime, default=datetime.datetime.utcnow)
    modified = Column(types.DateTime, default=datetime.datetime.utcnow)

    @classmethod
    def get(cls, **kw):
        return model.Session.query(cls).filter_by(**kw).first()

    @classmethod
    def list_collections(cls, owner_org=None, q=None, limit=100, offset=0):
        query = model.Session.query(cls)
        if owner_org:
            query = query.filter(cls.owner_org == owner_org)
        if q:
            q_escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            q_like = f"%{q_escaped}%"
            query = query.filter(
                cls.title.ilike(q_like, escape="\\")
                | cls.description.ilike(q_like, escape="\\")
            )
        query = query.order_by(cls.modified.desc())
        total = query.count()
        results = query.offset(offset).limit(limit).all()
        return results, total

    def as_dict(self):
        d = {}
        for col in self.__table__.columns:
            val = getattr(self, col.name, None)
            if isinstance(val, datetime.datetime):
                val = val.isoformat()
            d[col.name] = val
        d["surveys"] = [
            s.as_dict() for s in CSTCollectionSurvey.get_by_collection(self.id)
        ]
        return d


class CSTCollectionSurvey(DomainObject, BaseModel):
    """Association between a collection and a survey."""

    __tablename__ = "cst_collection_surveys"

    id = Column(types.UnicodeText, primary_key=True, default=_make_uuid)
    collection_id = Column(types.UnicodeText, nullable=False, index=True)
    survey_id = Column(types.UnicodeText, nullable=False)
    sort_order = Column(types.Integer, default=0)

    @classmethod
    def get_by_collection(cls, collection_id):
        return (
            model.Session.query(cls)
            .filter(cls.collection_id == collection_id)
            .order_by(cls.sort_order)
            .all()
        )

    @classmethod
    def delete_by_collection(cls, collection_id):
        model.Session.query(cls).filter(cls.collection_id == collection_id).delete()

    def as_dict(self):
        d = {}
        for col in self.__table__.columns:
            val = getattr(self, col.name, None)
            if isinstance(val, datetime.datetime):
                val = val.isoformat()
            d[col.name] = val
        return d


# ── Initialisation ──────────────────────────────────────

def init_db():
    """Create plugin tables and back-fill any new columns on existing ones."""
    import sqlalchemy as sa

    try:
        engine = model.meta.engine
        inspector = sa.inspect(engine)
        existing = inspector.get_table_names()

        for tbl in (CSTSurvey, CSTSurveyColumn, CSTCollection, CSTCollectionSurvey):
            if tbl.__tablename__ not in existing:
                tbl.__table__.create(engine)
                log.info("cstoolbox: Created table '%s'", tbl.__tablename__)
            else:
                _add_missing_columns(engine, inspector, tbl)
    except Exception as e:
        log.exception("cstoolbox: Error initializing DB: %s", e)
        raise


def _add_missing_columns(engine, inspector, tbl):
    """ALTER TABLE ... ADD COLUMN for model-only columns. PostgreSQL only."""
    import sqlalchemy as sa

    db_cols = {c["name"] for c in inspector.get_columns(tbl.__tablename__)}
    for col in tbl.__table__.columns:
        if col.name in db_cols:
            continue
        col_type = col.type.compile(dialect=engine.dialect)
        default_clause = ""
        if col.default is not None and getattr(col.default, "is_scalar", False):
            val = col.default.arg
            if isinstance(val, str):
                default_clause = " DEFAULT '%s'" % val.replace("'", "''")
            elif isinstance(val, (int, float)):
                default_clause = " DEFAULT %s" % val
        stmt = 'ALTER TABLE %s ADD COLUMN %s %s%s' % (
            tbl.__tablename__, col.name, col_type, default_clause,
        )
        with engine.begin() as conn:
            conn.execute(sa.text(stmt))
        log.info("cstoolbox: Added column '%s.%s'", tbl.__tablename__, col.name)
