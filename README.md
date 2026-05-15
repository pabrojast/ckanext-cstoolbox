# ckanext-cstoolbox

CKAN extension that surfaces UNESCO **Citizen Science Toolbox (CST)** survey
data published by Quartex (`https://cstoolbox.quartex.co.za`) as curated,
viewable, exportable resources inside a CKAN site.

## What it does

- Admins register **CST Surveys** in CKAN by picking a database
  (`odk_unesco`, `odk_ihp`, `odk_iw`), schema, and view returned by the
  upstream `GetSurveyViews` endpoint. Per-survey metadata (title, description,
  organisation owner, date field, columns to chart) is stored locally.
- End users browse the curated surveys, open an interactive dashboard for
  each one (Leaflet map of observation locations + Chart.js charts per
  configured measurement column + filterable data table), and download the
  data as GeoJSON or CSV with a date-window filter applied.
- **CST Collections** group multiple surveys for combined GeoJSON / CSV
  export, including a Terria-time-slider compatible "expanded" mode where
  every observation row becomes its own time-stamped Feature.

Observation data is fetched live from the Quartex `GetSurveyViewData`
endpoint on every request, with a small per-worker TTL cache so a single
browsing session does not hammer the upstream API.

## Configuration

Two environment variables are read by the extension:

| Variable | Required | Default | Description |
|---|---|---|---|
| `CKANEXT__CSTOOLBOX__API_BASE_URL` | no | `https://cstoolbox.quartex.co.za` | Base URL of the CST Toolbox API |
| `CKANEXT__CSTOOLBOX__API_TOKEN` | **yes** | — | Bearer token for the CST Toolbox API |
| `CKANEXT__CSTOOLBOX__DATABASES` | no | `odk_unesco,odk_ihp,odk_iw` | CSV allowlist of databases admins may register surveys against |

## Installation

```bash
pip install -e /path/to/ckanext-cstoolbox
```

Add `cstoolbox` to your CKAN `ckan.plugins` setting (typically in
`production.ini`, or via the `CKAN__PLUGINS` env var if you use the
`envvars` plugin).

On first startup the plugin creates four tables: `cst_surveys`,
`cst_survey_columns`, `cst_collections`, `cst_collection_surveys`.

## URLs

- `/cstoolbox-survey` — list of curated surveys
- `/cstoolbox-survey/<name>/dashboard` — map + charts + table
- `/cstoolbox-survey/<name>/geojson` — GeoJSON download
- `/cstoolbox-survey/<name>/csv` — CSV download
- `/cstoolbox-collection` — list of survey collections
- `/cstoolbox-collection/<name>/dashboard` — multi-survey overview
- `/cstoolbox-collection/<name>/geojson` — combined GeoJSON
