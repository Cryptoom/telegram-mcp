"""Offline regression tests: delete tools only delete messages of the given chat.

Private chats and basic groups share one account-wide message counter, so a bare
ID deletes the message from whichever chat holds it. The fake client records every
delete and answers get_messages(entity, ids=...) with None for IDs of other chats.
"""

from types import SimpleNamespace

import pytest
from telethon.tl import functions, types

from telegram_mcp import runtime
from telegram_mcp.tools import messages

PRIVATE = types.User(id=41, access_hash=111)
BASIC_GROUP = types.Chat(
    id=555, title="Basic Group", photo=None, participants_count=3, date=None, version=1
)
CHANNEL = types.Channel(
    id=777, title="Channel", photo=None, date=None, megagroup=False, access_hash=67890
)
ENTITIES = {41: PRIVATE, 555: BASIC_GROUP, 777: CHANNEL}
# IDs that belong to each chat; anything else is "another chat" or nonexistent.
OWN = {41: {10, 11, 12}, 555: {20, 21}, 777: {30, 31}}


class FakeDeleteClient:
    def __init__(self):
        self.raw_requests = []
        self.helper_deletes = []

    async def get_messages(self, entity, ids=None):
        own = OWN[entity.id]
        return [SimpleNamespace(id=i) if i in own else None for i in ids]

    async def __call__(self, request):
        self.raw_requests.append(request)
        return SimpleNamespace(pts_count=len(request.id))

    async def delete_messages(self, entity, message_ids, revoke=True):
        self.helper_deletes.append((entity, message_ids))
        return [SimpleNamespace(pts_count=1)]

    @property
    def deletes(self):
        return len(self.raw_requests) + len(self.helper_deletes)


def _patch(monkeypatch, client):
    async def fake_resolve(entity_id, cl):
        return ENTITIES[entity_id]

    async def fake_connected(cl):
        return None

    monkeypatch.setattr(messages, "get_client", lambda account=None: client)
    monkeypatch.setattr(messages, "resolve_entity", fake_resolve)
    monkeypatch.setattr(messages, "ensure_connected", fake_connected)


@pytest.mark.asyncio
@pytest.mark.parametrize("chat_id", [41, 555])
async def test_single_delete_foreign_id_sends_no_delete(monkeypatch, chat_id):
    client = FakeDeleteClient()
    _patch(monkeypatch, client)

    result = await messages.delete_message(chat_id=chat_id, message_id=99, account=None)

    assert client.deletes == 0
    assert "not in chat" in result and "nothing was deleted" in result


@pytest.mark.asyncio
async def test_single_delete_own_id_deletes(monkeypatch):
    client = FakeDeleteClient()
    _patch(monkeypatch, client)

    result = await messages.delete_message(chat_id=41, message_id=10, account=None)

    assert client.helper_deletes == [(PRIVATE, 10)]
    assert result == "Message 10 deleted."


@pytest.mark.asyncio
@pytest.mark.parametrize("chat_id, own", [(41, [10, 11]), (555, [20, 21])])
async def test_bulk_mixed_ids_aborts_without_partial_delete(monkeypatch, chat_id, own):
    client = FakeDeleteClient()
    _patch(monkeypatch, client)

    result = await messages.delete_messages_bulk(
        chat_id=chat_id, message_ids=own + [99], account=None
    )

    assert client.deletes == 0
    assert "99" in result and "nothing was deleted" in result


@pytest.mark.asyncio
@pytest.mark.parametrize("chat_id, own", [(41, [10, 11, 12]), (555, [20, 21])])
@pytest.mark.parametrize("revoke", [True, False])
async def test_bulk_own_ids_send_exactly_one_request_with_revoke(
    monkeypatch, chat_id, own, revoke
):
    client = FakeDeleteClient()
    _patch(monkeypatch, client)

    result = await messages.delete_messages_bulk(
        chat_id=chat_id, message_ids=own, revoke=revoke, account=None
    )

    (req,) = client.raw_requests
    assert isinstance(req, functions.messages.DeleteMessagesRequest)
    assert req.id == own and req.revoke is revoke
    assert f"Deleted {len(own)} of {len(own)} messages" in result


@pytest.mark.asyncio
async def test_bulk_channel_uses_channel_request_without_revoke_and_skips_lookup(monkeypatch):
    client = FakeDeleteClient()

    async def boom(entity, ids=None):
        raise AssertionError("channel deletes must not need the lookup")

    client.get_messages = boom
    _patch(monkeypatch, client)

    await messages.delete_messages_bulk(chat_id=777, message_ids=[30, 31], account=None)

    (req,) = client.raw_requests
    assert isinstance(req, functions.channels.DeleteMessagesRequest)
    assert req.id == [30, 31] and not hasattr(req, "revoke")


@pytest.mark.asyncio
async def test_bulk_nonexistent_id_aborts(monkeypatch):
    client = FakeDeleteClient()
    _patch(monkeypatch, client)

    result = await messages.delete_messages_bulk(chat_id=41, message_ids=[12345], account=None)

    assert client.deletes == 0
    assert "12345" in result


@pytest.mark.asyncio
async def test_bulk_duplicates_of_own_id_still_one_request(monkeypatch):
    client = FakeDeleteClient()
    _patch(monkeypatch, client)

    await messages.delete_messages_bulk(chat_id=41, message_ids=[10, 10], account=None)

    assert len(client.raw_requests) == 1


@pytest.mark.asyncio
async def test_bulk_empty_list_sends_no_delete(monkeypatch):
    client = FakeDeleteClient()
    _patch(monkeypatch, client)

    await messages.delete_messages_bulk(chat_id=41, message_ids=[], account=None)

    assert client.deletes == 0
