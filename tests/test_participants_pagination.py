"""Regression tests for get_participants pagination.

Telethon removed the ``offset`` keyword argument from ``iter_participants``
(present in older releases, gone in 1.42). get_participants must paginate by
fetching up to the end of the requested page and slicing locally, without
ever passing ``offset`` to the client. These tests run fully offline against
a fake client; no Telegram session is touched.
"""

import inspect
import json

import pytest

import telegram_mcp.tools.groups as groups


class FakeParticipant:
    def __init__(self, uid):
        self.id = uid
        self.first_name = f"User{uid}"
        self.last_name = ""


class FakeClient:
    """Mimics Telethon 1.42: iter_participants(entity, limit=None, ...) with NO offset kwarg."""

    def __init__(self, total):
        self.total = total
        self.calls = []

    def iter_participants(self, entity, limit=None):
        # Deliberately no **kwargs: passing offset= would raise TypeError,
        # exactly like the real Telethon 1.42 signature.
        self.calls.append({"entity": entity, "limit": limit})
        total = self.total

        async def gen():
            count = total if limit is None else min(limit, total)
            for i in range(count):
                yield FakeParticipant(i + 1)

        return gen()


@pytest.fixture
def fake_client(monkeypatch):
    client = FakeClient(total=25)
    monkeypatch.setattr(groups, "get_client", lambda account=None: client)

    async def noop(cl):
        return None

    monkeypatch.setattr(groups, "ensure_connected", noop)
    monkeypatch.setattr(groups, "is_multi_mode", lambda: False)
    return client


def parse_result(result):
    """Split the JSON payload from the appended pagination footer."""
    json_part = result.split("\n\n")[0]
    payload = json.loads(json_part)
    return [r["id"] for r in payload["results"]], result


@pytest.mark.asyncio
async def test_page_one_returns_first_slice(fake_client):
    result = await groups.get_participants(chat_id=-1003957110353, page=1, page_size=10)
    ids, text = parse_result(result)
    assert ids == list(range(1, 11))
    assert fake_client.calls[0]["limit"] == 10
    assert "Page 1" in text
    assert "page 2" in text  # has_more footer


@pytest.mark.asyncio
async def test_page_two_returns_second_slice(fake_client):
    result = await groups.get_participants(chat_id=-1003957110353, page=2, page_size=10)
    ids, text = parse_result(result)
    assert ids == list(range(11, 21))
    # Fetches up to the end of page 2, then slices locally.
    assert fake_client.calls[0]["limit"] == 20
    assert "Page 2" in text


@pytest.mark.asyncio
async def test_partial_last_page_has_no_more_marker(fake_client):
    result = await groups.get_participants(chat_id=-1003957110353, page=3, page_size=10)
    ids, text = parse_result(result)
    assert ids == list(range(21, 26))
    assert "more results" not in text


@pytest.mark.asyncio
async def test_page_beyond_end_returns_empty(fake_client):
    result = await groups.get_participants(chat_id=-1003957110353, page=4, page_size=10)
    payload = json.loads(result)
    assert payload["results"] == []


def test_real_telethon_has_no_offset_kwarg():
    """Guard: the installed Telethon must not accept offset, or the local
    slicing approach would be the wrong fix. Imports Telethon only, opens
    no client and no session."""
    from telethon.client.chats import ChatMethods

    params = inspect.signature(ChatMethods.iter_participants).parameters
    assert "offset" not in params
