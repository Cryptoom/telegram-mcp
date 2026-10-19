"""Delete guard against the real Telethon producer (not a hand-made get_messages).

tests/test_delete_chat_scope.py fakes get_messages itself, so it proves the consumer
only. Here Telethon's real get_messages/delete_messages run against an in-memory account
that numbers messages account-wide (private chats and basic groups) and can misbehave
the way Telethon documents Telegram may: omit entries or deduplicate IDs.
Adapted from upstream chigwell/telegram-mcp 11aa6d6 tests/test_delete_messages.py,
extended with basic groups (PeerChat) and short/deduplicated server replies.
"""

from datetime import datetime, timezone

import pytest
from telethon import TelegramClient, functions, types
from telethon.sessions import StringSession

from telegram_mcp.tools import messages

NOW = datetime(2026, 10, 19, 9, 0, tzinfo=timezone.utc)
IVAN = types.User(id=101, first_name="Ivan", access_hash=11)
MARIA = types.User(id=202, first_name="Maria", access_hash=22)
GROUP = types.Chat(id=303, title="G", photo=types.ChatPhotoEmpty(), participants_count=3,
                   date=NOW, version=1)


class AccountServer(TelegramClient):
    def __init__(self, peer_of_message, omit=(), dedupe=False):
        super().__init__(StringSession(), 12345, "dummy_hash")
        self.server = {
            mid: types.Message(id=mid, peer_id=peer, date=NOW, message="hi")
            for mid, peer in peer_of_message.items()
        }
        self.omit = set(omit)
        self.dedupe = dedupe
        self.requests = []

    async def __call__(self, request, ordered=False, flood_sleep_threshold=None):
        batch = request if isinstance(request, list) else [request]
        replies = [self._answer(r) for r in batch]
        return replies if isinstance(request, list) else replies[0]

    def _answer(self, request):
        self.requests.append(type(request).__name__)
        if isinstance(request, functions.messages.GetMessagesRequest):
            ids = [getattr(i, "id", i) for i in request.id]
            if self.dedupe:
                ids = list(dict.fromkeys(ids))
            found = [
                self.server.get(i) or types.MessageEmpty(id=i)
                for i in ids
                if i not in self.omit
            ]
            return types.messages.Messages(messages=found, topics=[], chats=[], users=[])
        if isinstance(request, functions.messages.DeleteMessagesRequest):
            gone = [i for i in request.id if self.server.pop(i, None)]
            return types.messages.AffectedMessages(pts=1, pts_count=len(gone))
        raise AssertionError(f"unexpected request {type(request).__name__}")


PEERS = {
    10: types.PeerUser(101), 11: types.PeerUser(101),   # chat with Ivan
    20: types.PeerUser(202),                            # chat with Maria
    30: types.PeerChat(303), 31: types.PeerChat(303),   # basic group
}
ENT = {101: IVAN, 202: MARIA, 303: GROUP}


def _install(monkeypatch, client):
    async def fake_resolve(chat_id, cl=None):
        return ENT[int(chat_id)]

    async def connected(cl=None):
        return None

    monkeypatch.delenv("TELEGRAM_ALLOWED_CHAT_IDS", raising=False)
    monkeypatch.setattr(messages, "get_client", lambda account=None: client)
    monkeypatch.setattr(messages, "resolve_entity", fake_resolve)
    monkeypatch.setattr(messages, "ensure_connected", connected)
    return client


@pytest.mark.asyncio
async def test_real_telethon_private_foreign_id_blocked(monkeypatch):
    c = _install(monkeypatch, AccountServer(PEERS))
    r = await messages.delete_messages_bulk(chat_id=101, message_ids=[10, 20])
    assert r == "Message(s) 20 are not in chat 101; nothing was deleted."
    assert set(c.server) == set(PEERS)
    assert "DeleteMessagesRequest" not in c.requests


@pytest.mark.asyncio
async def test_real_telethon_basic_group_foreign_and_own(monkeypatch):
    c = _install(monkeypatch, AccountServer(PEERS))
    r = await messages.delete_messages_bulk(chat_id=303, message_ids=[30, 10])
    assert "10" in r and "nothing was deleted" in r
    assert set(c.server) == set(PEERS)
    r = await messages.delete_messages_bulk(chat_id=303, message_ids=[30, 31])
    assert r == "Deleted 2 of 2 messages from chat 303."
    assert set(c.server) == {10, 11, 20}


@pytest.mark.asyncio
async def test_real_telethon_single_foreign_blocked(monkeypatch):
    c = _install(monkeypatch, AccountServer(PEERS))
    r = await messages.delete_message(chat_id=101, message_id=20)
    assert r == "Message 20 is not in chat 101; nothing was deleted."
    assert 20 in c.server


@pytest.mark.asyncio
async def test_server_omits_foreign_entry_fails_closed(monkeypatch):
    # Telegram "may decide to not send" invalid entries: a short reply must not
    # be read as "all IDs are ours".
    c = _install(monkeypatch, AccountServer(PEERS, omit={20}))
    r = await messages.delete_messages_bulk(chat_id=101, message_ids=[10, 20])
    assert "nothing was deleted" in r
    assert set(c.server) == set(PEERS)
    assert "DeleteMessagesRequest" not in c.requests


@pytest.mark.asyncio
async def test_server_omits_entry_single_delete_fails_closed(monkeypatch):
    c = _install(monkeypatch, AccountServer(PEERS, omit={20}))
    r = await messages.delete_message(chat_id=101, message_id=20)
    assert "nothing was deleted" in r and 20 in c.server


@pytest.mark.asyncio
async def test_deduplicating_server_names_the_foreign_id(monkeypatch):
    c = _install(monkeypatch, AccountServer(PEERS, dedupe=True))
    r = await messages.delete_messages_bulk(chat_id=101, message_ids=[10, 10, 20])
    assert r == "Message(s) 20 are not in chat 101; nothing was deleted."
    assert set(c.server) == set(PEERS)


@pytest.mark.asyncio
async def test_duplicates_of_own_ids_with_dedup_server_still_delete(monkeypatch):
    c = _install(monkeypatch, AccountServer(PEERS, dedupe=True))
    r = await messages.delete_messages_bulk(chat_id=101, message_ids=[10, 10, 11])
    assert "nothing was deleted" not in r
    assert set(c.server) == {20, 30, 31}
