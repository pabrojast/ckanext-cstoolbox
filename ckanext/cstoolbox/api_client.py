"""HTTP client for the Quartex Citizen Science Toolbox API.

The upstream service publishes two endpoints behind a bearer token:

- ``GET /api/DataExport/GetSurveyViews?database=<db>`` — list of available
  SQL views inside an ODK database.
- ``GET /api/DataExport/GetSurveyViewData`` — observation rows for a given
  view, with optional date-window filtering.

This module wraps both calls with the same urllib + auth pattern used in
``ckanext-stationsdischarge`` and adds a tiny per-process TTL cache so the
dashboard does not re-fetch identical windows multiple times within a session.
"""

import datetime
import json
import logging
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

import ckan.plugins.toolkit as toolkit

log = logging.getLogger(__name__)

BASE_URL_ENV = "CKANEXT__CSTOOLBOX__API_BASE_URL"
TOKEN_ENV = "CKANEXT__CSTOOLBOX__API_TOKEN"
DATABASES_ENV = "CKANEXT__CSTOOLBOX__DATABASES"

DEFAULT_BASE_URL = "https://cstoolbox.quartex.co.za"
DEFAULT_DATABASES = ("odk_unesco", "odk_ihp", "odk_iw")

REQUEST_TIMEOUT = 30
CACHE_TTL_SECONDS = 30

# Quartex's IIS occasionally returns transient 404s / 5xx for valid endpoints.
# We retry these a couple of times with brief backoff before surfacing to the
# user — the upstream usually recovers within a second or two.
MAX_RETRIES = 3
RETRY_BACKOFF_S = (0.5, 1.5)  # tried in order between attempts
RETRYABLE_STATUS = {404, 408, 429, 500, 502, 503, 504}

_cache = {}
_cache_lock = threading.Lock()


def get_base_url():
    return os.environ.get(BASE_URL_ENV, DEFAULT_BASE_URL).rstrip("/")


def get_token():
    return os.environ.get(TOKEN_ENV, "")


def allowed_databases():
    raw = os.environ.get(DATABASES_ENV, "")
    if not raw.strip():
        return DEFAULT_DATABASES
    parts = tuple(p.strip() for p in raw.split(",") if p.strip())
    return parts or DEFAULT_DATABASES


def _require_token():
    token = get_token()
    if not token:
        raise toolkit.ValidationError(
            {"cstoolbox": [
                "CST Toolbox API token not configured "
                "(set CKANEXT__CSTOOLBOX__API_TOKEN)."
            ]}
        )
    return token


def _format_dt_param(value):
    """Accept ``date``/``datetime``/ISO string and return the Quartex format.

    Returns ``yyyy-MM-dd HH:mm:ss`` when a time component is present,
    otherwise ``yyyy-MM-dd``. ``None``/empty input returns ``None``.
    """
    if value in (None, ""):
        return None
    if isinstance(value, datetime.datetime):
        if value.hour or value.minute or value.second:
            return value.strftime("%Y-%m-%d %H:%M:%S")
        return value.strftime("%Y-%m-%d")
    if isinstance(value, datetime.date):
        return value.strftime("%Y-%m-%d")
    text = str(value).strip()
    if not text:
        return None
    # Accept ISO 8601 with 'T' and trailing 'Z'.
    text = text.replace("T", " ").replace("Z", "").strip()
    return text


def _request(path, params):
    """Run an authenticated GET against the CST Toolbox API.

    Retries on transient failures (HTTP 404/408/429/5xx or network errors)
    up to MAX_RETRIES times. Persistent failures are raised as a
    ``toolkit.ValidationError`` so blueprints can present a clean 4xx.
    """
    token = _require_token()
    base = get_base_url()
    query = urllib.parse.urlencode([(k, v) for k, v in params if v not in (None, "")])
    url = "%s%s?%s" % (base, path, query) if query else "%s%s" % (base, path)

    last_err_msg = None
    for attempt in range(1, MAX_RETRIES + 1):
        req = urllib.request.Request(url)
        req.add_header("Authorization", "Bearer " + token)
        req.add_header("Accept", "application/json")

        log.debug("CST Toolbox request (attempt %d/%d): %s",
                  attempt, MAX_RETRIES, url)

        try:
            with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
                body = resp.read().decode("utf-8")
            try:
                return json.loads(body)
            except (json.JSONDecodeError, ValueError) as e:
                log.error("CST Toolbox returned non-JSON: %s", body[:300])
                last_err_msg = "non-JSON response: %s" % e
                # Treat as transient — sometimes IIS sends an error page
                # instead of JSON during a hiccup.
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8", errors="replace") if e.fp else ""
            log.warning("CST Toolbox API %s on %s (attempt %d/%d): %s",
                        e.code, url, attempt, MAX_RETRIES, raw[:200])
            msg = "HTTP %s" % e.code
            if raw:
                try:
                    parsed = json.loads(raw)
                    if isinstance(parsed, dict):
                        msg += ": " + str(parsed.get("message") or parsed)[:300]
                    else:
                        msg += ": " + str(parsed)[:300]
                except (json.JSONDecodeError, ValueError):
                    msg += ": " + raw[:300]
            last_err_msg = msg
            if e.code not in RETRYABLE_STATUS:
                break  # non-transient — fail fast
        except urllib.error.URLError as e:
            log.warning("CST Toolbox network error on %s (attempt %d/%d): %s",
                        url, attempt, MAX_RETRIES, e.reason)
            last_err_msg = "network error: %s" % e.reason

        # Brief backoff before retrying (no sleep after the last attempt).
        if attempt < MAX_RETRIES:
            delay = RETRY_BACKOFF_S[min(attempt - 1, len(RETRY_BACKOFF_S) - 1)]
            time.sleep(delay)

    raise toolkit.ValidationError({
        "cstoolbox": [
            "CST Toolbox API failed after %d attempts (%s)"
            % (MAX_RETRIES, last_err_msg or "unknown error"),
        ],
    })


def _cache_get(key):
    with _cache_lock:
        entry = _cache.get(key)
        if entry is None:
            return None
        expires_at, value = entry
        if expires_at < time.time():
            _cache.pop(key, None)
            return None
        return value


def _cache_set(key, value):
    with _cache_lock:
        _cache[key] = (time.time() + CACHE_TTL_SECONDS, value)
        # Light eviction so the cache cannot grow without bound.
        if len(_cache) > 512:
            now = time.time()
            stale = [k for k, (exp, _v) in _cache.items() if exp < now]
            for k in stale:
                _cache.pop(k, None)


def get_views(database):
    """Return the list of views available in *database*.

    Each item has the keys ``table_schema`` and ``table_name``.
    """
    if database not in allowed_databases():
        raise toolkit.ValidationError(
            {"database": [
                "Database '%s' is not in the allowlist." % database
            ]}
        )

    cache_key = ("views", database)
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    result = _request(
        "/api/DataExport/GetSurveyViews",
        [("database", database)],
    )
    if not isinstance(result, list):
        raise toolkit.ValidationError(
            {"cstoolbox": ["GetSurveyViews did not return a JSON list."]}
        )
    _cache_set(cache_key, result)
    return result


def get_view_data(database, schema, view, start=None, end=None, date_field=None):
    """Return the observation rows for a single view.

    *start* and *end* may be ``datetime``, ``date``, or strings. They are
    forwarded to the upstream API as the ``startDateTime`` and
    ``endDateTime`` parameters in ``yyyy-MM-dd[ HH:mm:ss]`` form.
    """
    if database not in allowed_databases():
        raise toolkit.ValidationError(
            {"database": [
                "Database '%s' is not in the allowlist." % database
            ]}
        )

    start_s = _format_dt_param(start)
    end_s = _format_dt_param(end)

    cache_key = (
        "data", database, schema, view, start_s, end_s, date_field or "",
    )
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    params = [
        ("database", database),
        ("schema", schema),
        ("view", view),
    ]
    if start_s:
        params.append(("startDateTime", start_s))
    if end_s:
        params.append(("endDateTime", end_s))
    if date_field:
        params.append(("dateFieldName", date_field))

    result = _request("/api/DataExport/GetSurveyViewData", params)
    if not isinstance(result, list):
        raise toolkit.ValidationError(
            {"cstoolbox": ["GetSurveyViewData did not return a JSON list."]}
        )
    _cache_set(cache_key, result)
    return result


def cache_clear():
    """Drop every cached response. Mainly useful in tests / admin tooling."""
    with _cache_lock:
        _cache.clear()
