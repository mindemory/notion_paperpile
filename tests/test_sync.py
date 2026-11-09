import json

import pytest
import requests

import library
import paperpile_api as sync
from notion_api import NotionClient, NotionError, plain_text


def paper(key, title, authors=(("Curtis", "Clayton"),), year=2020, venue="J. Neurosci."):
    return library.Paper(key, "article", title, [library.Author(f, l) for l, f in authors], year, venue)


def page(pid, title, label="Curtis, 2020", created="2023-01-01T00:00:00.000Z", **props):
    base = {
        "Author, Year": {"type": "title", "title": [{"plain_text": label}]},
        "Title": {"type": "rich_text", "rich_text": [{"plain_text": title}] if title else []},
        "Journal": {"type": "rich_text", "rich_text": [{"plain_text": "J. Neurosci."}]},
        "Status": {"type": "select", "select": {"name": "Unread"}},
        "Topic": {"type": "multi_select", "multi_select": []},
        "Summary": {"type": "rich_text", "rich_text": []},
    }
    base.update(props)
    return {"id": pid, "created_time": created, "properties": base}


class FakeClient:
    def __init__(self, pages, with_content=()):
        self.pages = pages
        self.with_content = set(with_content)
        self.created, self.updated, self.archived = [], [], []

    def page_has_content(self, page_id):
        return page_id in self.with_content

    def query_database(self, database_id):
        return self.pages

    def create_page(self, payload):
        self.created.append(payload)
        return {"id": f"new-{len(self.created)}"}

    def update_page(self, page_id, properties):
        self.updated.append((page_id, properties))
        return {}

    def archive_page(self, page_id):
        self.archived.append(page_id)
        return {}


@pytest.fixture
def template():
    return sync.load_template()


def created_titles(client):
    return [plain_text(p["properties"]["Title"]) for p in client.created]


def test_adds_only_missing_papers(template):
    client = FakeClient([page("p1", "{TMS} Does Not Increase {BOLD} Activity")])
    papers = [
        paper("A", "TMS does not increase BOLD activity"),  # already there, braces differ
        paper("B", "Persistent activity in the prefrontal cortex"),
        paper("C", "Persistent Activity in the Prefrontal Cortex"),  # same paper twice in Paperpile
        paper("D", "Frontal eye fields and working memory", venue=""),
    ]
    assert sync.run_sync(client, "db", papers, template, log=lambda _: None) == 0
    assert created_titles(client) == ["Persistent activity in the prefrontal cortex", "Frontal eye fields and working memory"]


def test_dry_run_writes_nothing(template):
    client = FakeClient([])
    lines = []
    sync.run_sync(client, "db", [paper("A", "One"), paper("B", "Two")], template, dry_run=True, log=lines.append)
    assert client.created == []
    assert any("would add" in line and "One" in line for line in lines)


def test_limit(template):
    client = FakeClient([])
    sync.run_sync(client, "db", [paper(k, k * 3) for k in "ABC"], template, limit=2, log=lambda _: None)
    assert len(client.created) == 2


def test_page_payload(template):
    p = paper("A", "Persistent activity", authors=(("Curtis", "Clayton"), ("D'Esposito", "Mark")), year=2003, venue="")
    body = sync.build_page(template, "db-123", p)
    assert body["parent"] == {"database_id": "db-123"}
    props = body["properties"]
    assert props["Author, Year"]["title"][0]["text"]["content"] == "Curtis & D'Esposito, 2003"
    assert props["Title"]["rich_text"][0]["text"]["content"] == "Persistent activity"
    assert props["Journal"] == {"rich_text": []}  # no venue: leave the cell empty
    assert props["Status"]["select"]["name"] == "Unread"  # template defaults survive
    json.dumps(body)  # serialisable as a request body
    assert template["parent"]["database_id"] != "db-123"  # template itself untouched


def test_refuses_to_flood_a_populated_database(template):
    client = FakeClient([page(f"p{i}", f"Old paper {i}") for i in range(30)])
    papers = [paper(f"N{i}", f"New paper {i}") for i in range(10)]
    assert sync.run_sync(client, "db", papers, template, log=lambda _: None) == 2
    assert client.created == []
    assert sync.run_sync(client, "db", papers, template, force=True, log=lambda _: None) == 0
    assert len(client.created) == 10


def test_stops_when_title_column_is_missing(template):
    pages = [page("p1", "")]
    for p in pages:
        del p["properties"]["Title"]
    with pytest.raises(SystemExit):
        sync.plan_sync(pages, [paper("A", "Anything")])


def test_failed_creates_are_reported(template):
    class Flaky(FakeClient):
        def create_page(self, payload):
            raise NotionError(400, "validation_error: bad property")

    assert sync.run_sync(Flaky([]), "db", [paper("A", "One")], template, log=lambda _: None) == 1


def test_tidy_rewrites_legacy_rows():
    old = page("p1", "{TMS} and {EEG}", label="Popov et. al.,2021")
    updates = sync.tidy_updates(old)
    assert plain_text(updates["Author, Year"]) == "Popov et al., 2021"
    assert plain_text(updates["Title"]) == "TMS and EEG"
    assert sync.tidy_updates(page("p2", "Clean title", label="Popov et al., 2021")) == {}


def test_user_data_detection(template):
    assert not sync.has_user_data(page("p", "T"), template)
    assert sync.has_user_data(page("p", "T", Status={"type": "select", "select": {"name": "Read"}}), template)
    assert sync.has_user_data(page("p", "T", Topic={"type": "multi_select", "multi_select": [{"name": "WM"}]}), template)
    assert sync.has_user_data(page("p", "T", Summary={"type": "rich_text", "rich_text": [{"plain_text": "notes"}]}), template)
    assert not sync.has_user_data(page("p", "T", Edited={"type": "last_edited_time", "last_edited_time": "2024"}), template)


def test_dedupe_keeps_annotated_or_oldest_copy(template):
    read = {"type": "select", "select": {"name": "Read"}}
    pages = [
        page("a1", "Alpha oscillations", created="2022-05-01"),
        page("a2", "{Alpha} Oscillations", created="2024-07-06"),
        page("b1", "Beta bursts", created="2022-05-01"),
        page("b2", "Beta Bursts", created="2024-07-06", Status=read),
        page("c1", "Gamma", created="2022-01-01", Status=read),
        page("c2", "gamma", created="2024-01-01", Summary={"type": "rich_text", "rich_text": [{"plain_text": "x"}]}),
        page("d1", "Unique paper"),
    ]
    to_archive, conflicts = sync.plan_dedupe(pages, template)
    assert sorted(p["id"] for p in to_archive) == ["a2", "b1"]
    assert [[p["id"] for p in g] for g in conflicts] == [["c1", "c2"]]

    client = FakeClient(pages)
    assert sync.run_dedupe(client, "db", template, dry_run=True, log=lambda _: None) == 0
    assert client.archived == []
    sync.run_dedupe(client, "db", template, log=lambda _: None)
    assert sorted(client.archived) == ["a2", "b1"]


def test_dedupe_keeps_copy_with_notes_inside_the_page(template):
    pages = [
        page("e1", "Theta bursts", created="2022-05-01"),
        page("e2", "{Theta} Bursts", created="2024-07-06"),
        page("f1", "Delta waves", created="2022-05-01"),
        page("f2", "Delta Waves", created="2024-07-06"),
    ]
    client = FakeClient(pages, with_content={"e2", "f1", "f2"})
    sync.run_dedupe(client, "db", template, log=lambda _: None)
    assert client.archived == ["e1"]  # f1 and f2 both have notes: left for a human


def test_credentials(monkeypatch, tmp_path):
    legacy = tmp_path / "notion_ids.csv"
    legacy.write_text("\ndatabase-id\nsecret-token\n")
    monkeypatch.delenv("NOTION_TOKEN", raising=False)
    monkeypatch.delenv("NOTION_DATABASE_ID", raising=False)
    assert sync.load_credentials(legacy) == ("secret-token", "database-id")
    monkeypatch.setenv("NOTION_TOKEN", "env-token")
    monkeypatch.setenv("NOTION_DATABASE_ID", "env-db")
    assert sync.load_credentials(legacy) == ("env-token", "env-db")
    monkeypatch.delenv("NOTION_TOKEN")
    with pytest.raises(SystemExit):
        sync.load_credentials(tmp_path / "missing.csv")


# ---------------------------------------------------------------- HTTP client


class Response:
    def __init__(self, status, payload=None, headers=None):
        self.status_code = status
        self._payload = payload or {}
        self.headers = headers or {}
        self.text = json.dumps(self._payload)

    def json(self):
        return self._payload


class Session:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.calls = []
        self.headers = {}

    def request(self, method, url, json=None, timeout=None):
        self.calls.append((method, url, json))
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def client_for(session, sleeps):
    return NotionClient("tok", session=session, sleep=sleeps.append, clock=lambda: 0.0, min_interval=0)


def test_client_paginates_and_sets_headers():
    session = Session(
        Response(200, {"results": [{"id": 1}], "has_more": True, "next_cursor": "c2"}),
        Response(200, {"results": [{"id": 2}], "has_more": False, "next_cursor": None}),
    )
    pages = client_for(session, []).query_database("db")
    assert [p["id"] for p in pages] == [1, 2]
    assert session.calls[1][2]["start_cursor"] == "c2"
    assert session.headers["Authorization"] == "Bearer tok"
    assert session.headers["Notion-Version"] == "2022-02-22"


def test_client_backs_off_when_rate_limited():
    sleeps = []
    session = Session(Response(429, {"code": "rate_limited"}, {"Retry-After": "2"}), Response(200, {"id": "p"}))
    assert client_for(session, sleeps).create_page({"x": 1}) == {"id": "p"}
    assert 2.0 in sleeps


def test_client_does_not_retry_ambiguous_create_failures():
    session = Session(Response(502, {"message": "bad gateway"}), Response(200, {"id": "dup"}))
    with pytest.raises(NotionError):
        client_for(session, []).create_page({"x": 1})
    assert len(session.calls) == 1

    session = Session(requests.ConnectionError("reset"), Response(200, {"id": "p"}))
    with pytest.raises(NotionError):
        client_for(session, []).create_page({"x": 1})


def test_client_retries_reads_after_server_errors():
    session = Session(Response(502), requests.ConnectionError("reset"), Response(200, {"results": [], "has_more": False}))
    assert client_for(session, []).query_database("db") == []
    assert len(session.calls) == 3


def test_client_detects_page_content():
    blank = {"type": "paragraph", "paragraph": {"text": []}, "has_children": False}
    note = {"type": "paragraph", "paragraph": {"rich_text": [{"plain_text": "key result"}]}}
    heading = {"type": "heading_2", "heading_2": {"text": []}}
    session = Session(
        Response(200, {"results": [], "has_more": False}),
        Response(200, {"results": [blank, blank], "has_more": False}),
        Response(200, {"results": [blank, note], "has_more": False}),
        Response(200, {"results": [heading], "has_more": False}),
        Response(200, {"results": [blank], "has_more": True}),
    )
    client = client_for(session, [])
    assert [client.page_has_content("p") for _ in range(5)] == [False, False, True, True, True]
    assert session.calls[0][:2] == ("GET", "https://api.notion.com/v1/blocks/p/children?page_size=20")


def test_client_raises_on_client_errors():
    session = Session(Response(400, {"code": "validation_error", "message": "Title is not a property"}))
    with pytest.raises(NotionError, match="validation_error"):
        client_for(session, []).update_page("p", {})
