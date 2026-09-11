"""Serve layer — route tests for `app/server.py`.

Retrieval is stubbed (see `conftest.client`), so these cover what the Serve layer
is actually responsible for: input validation, POST-redirect-GET, and rendering.
"""

import os
import time

from pathlib import Path

import pytest
import urlvestigia
from app import hosting, server


def test_home_renders(client):
    response = client.get("/")

    assert response.status_code == 200
    assert "URLvestigia" in response.text
    assert "saved_searches" in response.text


def test_healthz_names_the_app_the_process_and_when_it_loaded(client):
    """A socket probe cannot tell this dashboard from an unrelated app on the same
    port, or from a leftover serving pre-edit code out of a process nothing holds a
    handle to. These three facts are what quickstart.ipynb tells them apart with."""
    body = client.get("/healthz").json()

    assert body["app"] == hosting.NAME
    assert body["pid"] == os.getpid()  # TestClient runs the app in this process
    assert body["started"] <= time.time()


def test_empty_table_shows_a_prompt(client):
    assert "No searches yet" in client.get("/").text


# --- POST-redirect-GET -----------------------------------------------------

def test_search_redirects_and_saves(client):
    response = client.post("/search", data={"text": "cloudera cdp"},
                           follow_redirects=False)

    assert response.status_code == 303
    assert client.search_calls[0]["text"] == "cloudera cdp"
    assert "cloudera cdp" in client.get("/").text


@pytest.mark.parametrize("route,payload", [
    ("/search", {"text": "iceberg"}),
    ("/dedupe", {}),
    ("/store", {}),
    ("/clear", {}),
])
def test_mutations_always_redirect(client, route, payload):
    """Every mutation ends in a 303 so a reload cannot re-run it."""
    response = client.post(route, data=payload, follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"].startswith("/")


def test_blank_query_never_reaches_retrieval(client):
    response = client.post("/search", data={"text": "   "}, follow_redirects=False)

    assert response.status_code == 303
    assert client.search_calls == []


# --- input validation ------------------------------------------------------

def test_max_results_is_clamped(client):
    client.post("/search", data={"text": "a", "max_results": 9999})
    client.post("/search", data={"text": "b", "max_results": 0})

    assert client.search_calls[0]["max_results"] == 50
    assert client.search_calls[1]["max_results"] == 1


@pytest.mark.parametrize("field,bad,expected", [
    ("timelimit", "century", None),        # "" → None by the time it reaches ddgs
    ("safesearch", "extremely", "moderate"),
    ("region", "mars-mars", "wt-wt"),
])
def test_invalid_options_fall_back_to_the_default(client, field, bad, expected):
    """`_pick` whitelists every option, so nothing user-supplied reaches a search
    engine unchecked."""
    client.post("/search", data={"text": "a", field: bad})

    assert client.search_calls[0][field] == expected


def test_unknown_engine_is_dropped(client):
    client.post("/search", data={"text": "a", "backend": ["duckduckgo", "evilcorp"]})

    assert client.search_calls[0]["backend"] == "duckduckgo"


def test_no_engine_selected_falls_back_to_every_engine(client):
    """Unticking everything means "ask them all", not "ask the first one".

    Resilience, not coverage: a wider selection can return *fewer* URLs than the best
    single engine (see docs/ARCHITECTURE.md), but a blocked engine stops being fatal.
    """
    client.post("/search", data={"text": "a"})

    assert client.search_calls[0]["backend"] == ",".join(server.OPTIONS["backend"])


def test_engine_order_and_uniqueness_are_preserved(client):
    """The stored order matches what was asked, deduplicated — a reproducible
    record, not a priority chain ddgs honors (see docs/ARCHITECTURE.md)."""
    client.post("/search", data={
        "text": "a",
        "backend": ["yahoo", "duckduckgo", "yahoo", "startpage"],
    })

    assert client.search_calls[0]["backend"] == "yahoo,duckduckgo,startpage"


# --- provider selection ----------------------------------------------------

@pytest.mark.parametrize("provider", server.OPTIONS["provider"])
def test_every_ui_provider_is_accepted(client, provider):
    """A typo here is a radio button that silently searches the web instead.

    Parametrized from OPTIONS rather than a copy of it, so a provider added to the
    form is covered the moment it is offered — this list had already drifted once.
    """
    client.post("/search", data={"text": "a", "provider": provider})

    assert client.search_calls[0]["provider"] == provider


def test_every_offered_provider_is_implemented():
    """The form may offer a subset of what retrieval/ implements, but never a superset.
    Offering a provider with no registry entry would silently search the web under
    another name, and `_pick`'s whitelist would not catch it because the name *is*
    whitelisted. The two are equal today; the subset check is what holds if a corpus
    is ever withheld from the UI again."""
    assert set(server.OPTIONS["provider"]) <= set(urlvestigia.REGISTRY)


def test_unknown_provider_falls_back_to_the_default(client):
    client.post("/search", data={"text": "a", "provider": "evilcorp"})

    assert client.search_calls[0]["provider"] == "ddgs"


def test_no_provider_selected_defaults_to_web(client):
    """Existing clients post no `provider` field at all."""
    client.post("/search", data={"text": "a"})

    assert client.search_calls[0]["provider"] == "ddgs"


def test_unsupported_options_are_stored_null_not_as_posted(client, temp_db):
    """The governed record must not claim a filter that never ran.

    A hidden control still posts its value — CSS cannot prevent that — so the
    server is the only place this can be enforced. Wikipedia applies no time
    window and no safe search, so both must land as NULL however the form arrives.
    """
    client.post("/search", data={
        "text": "a", "provider": "wikipedia",
        "timelimit": "w", "safesearch": "on", "region": "kr-kr",
        "backend": ["yahoo"],
    })

    row = temp_db.list_searches()[0]
    assert row["provider"] == "wikipedia"
    assert row["region"] == "kr-kr"      # supported, applied
    assert row["timelimit"] is None      # not supported
    assert row["safesearch"] is None     # not supported
    assert row["backend"] is None        # not supported


def test_supported_but_unset_is_stored_empty_not_null(client, temp_db):
    """The distinction NULL carries only works if "" carries the other half: a web
    search with no time window is not the same record as a corpus that has none."""
    client.post("/search", data={"text": "a", "provider": "ddgs", "timelimit": ""})

    row = temp_db.list_searches()[0]

    assert row["timelimit"] == ""
    assert row["safesearch"] == "moderate"
    assert row["backend"] == ",".join(server.OPTIONS["backend"])


def test_scholarly_provider_records_its_time_window(client, temp_db):
    """arXiv supports a period even though it supports no region."""
    client.post("/search", data={
        "text": "a", "provider": "arxiv", "timelimit": "y", "region": "kr-kr"})

    row = temp_db.list_searches()[0]
    assert row["timelimit"] == "y"
    assert row["region"] is None


# --- result handling -------------------------------------------------------

def test_retrieval_error_surfaces_as_a_message(client, monkeypatch):
    def boom(text, **kwargs):
        raise RuntimeError("rate limited")

    monkeypatch.setattr(server.urlvestigia, "text_to_urls", boom)
    response = client.post("/search", data={"text": "a"}, follow_redirects=False)

    assert response.status_code == 303
    body = client.get(response.headers["location"]).text
    # The literal prefix is load-bearing: the template keys its red styling off it.
    assert "Error" in body
    assert "rate limited" in body


def test_a_failure_names_the_provider_and_engines(client, monkeypatch):
    """The library's own text says nothing about what was searched. Without this the
    user cannot tell a blocked engine from a query with no matches."""
    def boom(text, **kwargs):
        raise RuntimeError("rate limited")

    monkeypatch.setattr(server.urlvestigia, "text_to_urls", boom)
    response = client.post("/search", data={
        "text": "a", "provider": "ddgs", "backend": ["duckduckgo", "yahoo"],
    }, follow_redirects=False)

    body = client.get(response.headers["location"]).text
    assert "Web (duckduckgo + yahoo)" in body


def test_every_engine_failing_names_each_one_and_why(client, monkeypatch):
    """The demo failure this exists for: ddgs reports "all engines blocked" as an
    empty search, so without this it renders as a calm "No results found." — and a
    dead network becomes indistinguishable from a corpus with nothing in it."""
    def boom(text, **kwargs):
        raise server.urlvestigia.EngineError(
            [("duckduckgo", "HTTP 403"), ("yahoo", "timed out")])

    monkeypatch.setattr(server.urlvestigia, "text_to_urls", boom)
    response = client.post("/search", data={
        "text": "a", "provider": "ddgs", "backend": ["duckduckgo", "yahoo"],
    }, follow_redirects=False)

    body = client.get(response.headers["location"]).text
    assert "Error" in body
    assert "no engine answered" in body
    assert "duckduckgo: HTTP 403" in body
    assert "yahoo: timed out" in body


def test_a_failure_names_a_non_web_provider_without_engines(client, monkeypatch):
    """`backend` is a ddgs concept — naming it for arXiv would claim an engine chain
    that never ran, the same false claim the NULL storage exists to prevent."""
    def boom(text, **kwargs):
        raise RuntimeError("timed out")

    monkeypatch.setattr(server.urlvestigia, "text_to_urls", boom)
    response = client.post("/search", data={"text": "a", "provider": "arxiv"},
                           follow_redirects=False)

    # Asserted on the redirect target rather than the rendered page, whose engine
    # checkboxes name every engine regardless of what was searched.
    message = response.headers["location"]
    assert "arXiv%20search%20failed" in message
    assert "duckduckgo" not in message


def test_no_results_is_reported_not_silently_saved(client, monkeypatch):
    """An empty result set must not be persisted as a successful search."""
    monkeypatch.setattr(server.urlvestigia, "text_to_urls", lambda text, **kw: [])
    response = client.post("/search", data={"text": "a"}, follow_redirects=False)

    assert "No results found" in client.get(response.headers["location"]).text
    assert "No searches yet" in client.get("/").text


def test_results_render_as_links(client):
    client.post("/search", data={"text": "iceberg"})
    body = client.get("/").text

    assert 'href="https://example.com/iceberg/0"' in body
    assert 'rel="noopener noreferrer"' in body  # target=_blank without this leaks


# --- table rendering -------------------------------------------------------

def test_all_engines_selected_renders_every_engine(client):
    """A full selection is listed, not summarised as "any".

    Three reasons the summary was wrong: it hid which engines were asked, it
    collided with the Time column's "any" (which means "no time limit") in the
    next cell, and it was computed against the *current* engine list — so adding
    a fifth engine would have retroactively re-labelled every historical row that
    had asked for four.
    """
    client.post("/search", data={
        "text": "a",
        "backend": ["duckduckgo", "yahoo", "startpage", "yandex"],
    })
    body = client.get("/").text

    assert ">duckduckgo+yahoo+startpage+yandex<" in body
    # Scoped to the engine cell: the Time column's "any" is a different claim and
    # a legitimate one. Needing this scoping is the collision itself.
    assert 'col-engine">any<' not in body


def test_engine_label_preserves_selection_order(client):
    """The stored order is the order asked; the label must not sort or normalise
    it, or the column stops matching the `backend` column it renders."""
    client.post("/search", data={
        "text": "a", "backend": ["startpage", "duckduckgo"]})

    assert ">startpage+duckduckgo<" in client.get("/").text


def test_single_engine_renders_its_name(client):
    client.post("/search", data={"text": "a", "backend": ["yahoo"]})

    assert ">yahoo<" in client.get("/").text


def test_provider_column_shows_a_readable_label(client):
    client.post("/search", data={"text": "a", "provider": "arxiv"})

    assert ">arXiv<" in client.get("/").text


def test_a_retired_provider_would_still_have_a_label(client, temp_db):
    """PROVIDER_LABELS covers every provider ever searched, not just the ones on
    offer, so withdrawing one from the UI can never relabel history with the bare id.
    Every provider is offered today, so this exercises the label path rather than a
    live retirement — it is the guarantee that has to survive the next one."""
    temp_db.save_search("a", ["https://example.org/1"], provider="openalex")

    body = client.get("/").text
    assert ">OpenAlex<" in body
    assert ">openalex<" not in body


def test_unsupported_and_unset_render_differently(client):
    """The table has to distinguish "this corpus has no time filter" from "it has
    one and you did not use it". Collapsing them is the reporting half of the same
    false claim the NULL storage prevents."""
    client.post("/search", data={"text": "web", "provider": "ddgs", "timelimit": ""})
    client.post("/search", data={"text": "wiki", "provider": "wikipedia"})
    body = client.get("/").text

    # "any" now appears only in the Time column — the engine column lists its
    # engines instead of summarising them, so the word has one meaning again.
    assert ">any<" in body      # ddgs: supports a window, none chosen
    assert "&mdash;" in body    # wikipedia: no window to choose


def test_provider_controls_are_hidden_by_generated_css(client):
    """The visibility rules come from the same matrix the server enforces, so a
    control can never be offered for an option the server would discard."""
    body = client.get("/").text

    assert ".sentence:has(#pv-wikipedia:checked) .opt-timelimit" in body
    assert ".sentence:has(#pv-arxiv:checked) .opt-region" in body
    # ddgs applies every option, so it hides nothing.
    assert ".sentence:has(#pv-ddgs:checked)" not in body


def test_openalex_renders_a_pill_and_hides_what_it_cannot_apply(client):
    """OpenAlex applies `timelimit` and nothing else, so the form must offer it and
    hide the other three. The pill comes from _providers() and the rules from
    p.unsupported — the same matrix — so this catches a provider added to OPTIONS
    with no matching entry in SUPPORTS."""
    body = client.get("/").text

    assert 'id="pv-openalex"' in body
    for option in ("region", "safesearch", "backend"):
        assert f".sentence:has(#pv-openalex:checked) .opt-{option}" in body
    assert ".sentence:has(#pv-openalex:checked) .opt-timelimit" not in body


def test_query_text_is_escaped(client):
    """Jinja2 autoescaping is the only thing standing between a search box and
    stored XSS. Assert it rather than assume it."""
    client.post("/search", data={"text": "<script>alert(1)</script>"})
    body = client.get("/").text

    assert "<script>alert(1)</script>" not in body
    assert "&lt;script&gt;" in body


# --- row actions -----------------------------------------------------------

def test_delete_removes_one_row(client):
    # Distinctive queries: the rendered page also shows the database path, and a
    # generic word like "remove" matches the pytest tmp_path in it.
    client.post("/search", data={"text": "zeta-survivor-query"})
    client.post("/search", data={"text": "omega-doomed-query"})

    doomed = next(r["id"] for r in server.db.list_searches()
                  if r["query"] == "omega-doomed-query")
    client.post(f"/delete/{doomed}")

    body = client.get("/").text
    assert "zeta-survivor-query" in body
    assert "omega-doomed-query" not in body


def test_delete_of_a_missing_id_is_harmless(client):
    response = client.post("/delete/999999", follow_redirects=False)

    assert response.status_code == 303


def test_dedupe_reports_when_there_is_nothing_to_do(client):
    response = client.post("/dedupe", follow_redirects=False)

    assert "No duplicate URLs found" in client.get(response.headers["location"]).text


def test_clear_empties_the_table(client):
    client.post("/search", data={"text": "a"})
    client.post("/clear")

    assert "No searches yet" in client.get("/").text


# --- Store -----------------------------------------------------------------

def test_store_writes_a_snapshot_and_reports_its_contents(client):
    client.post("/search", data={"text": "a"})
    response = client.post("/store", follow_redirects=False)

    written = list((server.backup.DEFAULT_DIR).glob("urlvestigia-*.db"))
    assert len(written) == 1
    body = client.get(response.headers["location"]).text
    assert "Stored" in body
    assert "1 searches, 3 URLs" in body


# --- download --------------------------------------------------------------

def test_download_returns_the_database_as_an_attachment(client):
    """`Content-Disposition: attachment` is the entire mechanism — it is what
    makes the browser offer Save As instead of rendering the bytes. A browser
    cannot be given a picker for the server's disk, so this is what "choose
    where it goes" has to mean."""
    client.post("/search", data={"text": "a"})

    response = client.get("/download")

    assert response.status_code == 200
    disposition = response.headers["content-disposition"]
    assert "attachment" in disposition
    assert "urlvestigia-" in disposition and ".db" in disposition
    # A real SQLite file, not an error page rendered with a 200.
    assert response.content.startswith(b"SQLite format 3\x00")


def test_download_carries_the_rows(client, temp_db):
    """Served through db.backup(), so what lands is a consistent snapshot rather
    than whatever the live file happened to contain mid-write."""
    import sqlite3

    client.post("/search", data={"text": "downloadable"})
    payload = client.get("/download").content

    path = temp_db.DB_PATH.parent / "downloaded.db"
    path.write_bytes(payload)
    conn = sqlite3.connect(path)
    try:
        assert conn.execute("SELECT query FROM searches").fetchone()[0] == "downloadable"
    finally:
        conn.close()


def test_download_leaves_no_file_in_the_backups_directory(client):
    """Store owns backups/. Downloading must not add a file there on every click —
    the snapshot it sends is temporary and deleted once the response is out."""
    before = set(server.backup.DEFAULT_DIR.glob("urlvestigia-*.db"))
    client.post("/search", data={"text": "a"})

    client.get("/download")

    assert set(server.backup.DEFAULT_DIR.glob("urlvestigia-*.db")) == before


def test_download_before_any_search_explains_itself(client, temp_db):
    """No store on disk yet: a bare 500 would tell the user nothing.

    The redirect is read from the Location header rather than followed — with the
    database file gone, rendering `/` would fail on its own, which is a different
    situation than the one under test.
    """
    from urllib.parse import unquote

    temp_db.DB_PATH.unlink(missing_ok=True)

    response = client.get("/download", follow_redirects=False)

    assert response.status_code == 303
    assert "nothing to download" in unquote(response.headers["location"])


def test_store_leaves_the_live_table_untouched(client):
    """Storing is the one header action that changes nothing in the store — the
    guarantee that makes it safe to sit beside Clear all."""
    client.post("/search", data={"text": "zeta-survivor-query"})
    before = server.db.stats()

    client.post("/store")

    assert server.db.stats() == before
    assert "zeta-survivor-query" in client.get("/").text


def test_store_reports_a_second_press_as_a_failure_not_a_success(client, monkeypatch):
    """Snapshot names are second-resolution, so a double-click writes nothing the
    second time. Reporting that as "Stored" would claim a backup that does not
    exist — the same false claim the NULL option columns exist to prevent."""
    monkeypatch.setattr(server.backup, "snapshot",
                        lambda *a, **kw: (_ for _ in ()).throw(FileExistsError("x.db")))
    response = client.post("/store", follow_redirects=False)

    body = client.get(response.headers["location"]).text
    assert "Error" in body  # the template keys its red styling off this prefix
    assert "Stored" not in body


def test_store_failure_surfaces_as_a_message(client, monkeypatch):
    def boom(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(server.backup, "snapshot", boom)
    response = client.post("/store", follow_redirects=False)

    body = client.get(response.headers["location"]).text
    assert "Error" in body
    assert "disk full" in body


def test_store_button_is_rendered(client):
    body = client.get("/").text

    assert 'action="/store"' in body
    assert ">Store<" in body


def test_running_the_module_as_a_script_starts_a_server():
    """A Cloudera AI Application runs `python app/server.py` — the `--script` in
    .cicd/deploy.sh — not a uvicorn command line. Without a __main__ block the
    platform starts the file, imports it, exits 0, and reports a deployment that
    never listened. Asserted against the source because the alternative is a test
    that binds a port and never returns."""
    source = Path(server.__file__).read_text(encoding="utf-8")

    assert 'if __name__ == "__main__":' in source
    assert "uvicorn.run(" in source
    # The binding rule stays hosting.py's, here as everywhere else.
    assert "host=hosting.host()" in source
    assert "port=hosting.port()" in source
