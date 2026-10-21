"""
app.py
------
Climate Budget Tagging - Dash app.

Run locally:      python app.py                 then open http://127.0.0.1:8050
Production:       Posit Connect (manifest.json; it serves `server` below)

Structure
  app.py            this file: the Dash object, page frame and routing
  callbacks.py      every callback
  src/pages.py      layouts (Overview, Tag, Dashboard, Download data, Methodologies)
  src/methodology.py  the Methodologies page
  src/constants.py  names, schemas, colours, country list, environment variable names
  src/utils.py      formatting, charts and reusable components
  src/queries.py    ALL storage access (Unity Catalog via Databricks SQL)
  src/pipeline.py   reading uploads, cleaning, running the taggers, background jobs
  src/taxonomy_method/  green_budget_tagger*.py, rio_marker_tagger.py, eu_taxonomy_tagger.py; shared/
                    the original tagging engine - copied unchanged

Routing is query-string based (?page=home|tag|dashboard|data|methodologies) so it also works behind Posit Connect,
which serves apps below a sub-path.
"""

from __future__ import annotations

import json
import logging
import os
import warnings

try:                                                       # optional: a local .env file for development
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:                                        # pragma: no cover
    pass

from dash import Dash, Input, Output, dcc, html

from src import constants as C

warnings.filterwarnings("ignore", category=DeprecationWarning)
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s: %(message)s")

app = Dash(
    __name__,
    suppress_callback_exceptions=True,          # pages are rendered on demand, so their ids don't exist at start-up
    title=C.APP_NAME,
    update_title=None,                          # keep the tab title steady while callbacks run
    meta_tags=[{"name": "viewport", "content": "width=device-width, initial-scale=1"},
               {"name": "description", "content": "Tag national budgets with Rio Markers and the EU Taxonomy and compare countries."}],
)
server = app.server

FAVICON_URL = app.get_asset_url("favicon.ico")
FONT_URL = app.get_asset_url("fonts/fira-sans-latin-400-normal.woff2")
FONT_BOLD_URL = app.get_asset_url("fonts/fira-sans-latin-600-normal.woff2")

app.index_string = """<!DOCTYPE html>
<html lang="en">
    <head>
        {%metas%}
        <title>{%title%}</title>
        <link rel="icon" type="image/x-icon" href=\"""" + FAVICON_URL + """\">
        <link rel="preload" as="font" type="font/woff2" crossorigin href=\"""" + FONT_URL + """\">
        <link rel="preload" as="font" type="font/woff2" crossorigin href=\"""" + FONT_BOLD_URL + """\">
        {%css%}
    </head>
    <body>
        <noscript>This tool needs JavaScript. Please enable it in your browser.</noscript>
        {%app_entry%}
        <footer>
            {%config%}
            {%scripts%}
            {%renderer%}
        </footer>
    </body>
</html>
"""

app.layout = html.Div([
    dcc.Location(id="url", refresh=False),
    # Small things the browser remembers for this tab only: which background job is ours, and the upload preview.
    dcc.Store(id="job-store", storage_type="session"),
    dcc.Store(id="upload-store", storage_type="session"),
    html.Div(id="page-content"),
    html.Div(id="title-sink", hidden=True),
])

_TITLES = {key: "%s - %s" % (label, C.APP_NAME) for key, label, _href in C.PAGES}

app.clientside_callback(
    """
    function(search) {
        const titles = %s;
        const page = new URLSearchParams(search || "").get("page") || %s;
        document.title = titles[page] || %s;
        return "";
    }
    """ % (json.dumps(_TITLES), json.dumps(C.DEFAULT_PAGE), json.dumps(C.APP_NAME)),
    Output("title-sink", "children"),
    Input("url", "search"),
)

import callbacks  # noqa: E402,F401  (registers the callbacks on the app above)


def main() -> None:
    app.run(debug=False, host="0.0.0.0", port=8050)


if __name__ == "__main__":
    main()
