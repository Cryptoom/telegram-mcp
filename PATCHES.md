# Local Patches (Cryptoom fork of chigwell/telegram-mcp)

Liste der lokalen Abweichungen gegenueber `upstream/main`. Bei jedem Upstream-Merge
(`git fetch upstream && git merge upstream/main`) pruefen, ob die Patches noch
greifen, und sie sonst neu anwenden.

## Patch 1: get_participants/get_full_chat gegen Telethon-Drift gehaertet (2026-08-19)

**Dateien:** `telegram_mcp/tools/groups.py` (`get_participants`), `telegram_mcp/tools/chats.py`
(`get_full_chat`).

**Problem 1 (get_participants):** `get_participants` rief
`cl.iter_participants(chat_id, limit=page_size, offset=offset)` auf. Die installierte
Telethon-Version (1.42.0) kennt kein `offset`-Kwarg mehr an `iter_participants`
(`ChatMethods.iter_participants()` nimmt nur `entity`, `limit`, `search`, `filter`, ...).
Jeder Aufruf mit `page > 0` scheiterte mit
`TypeError: ChatMethods.iter_participants() got an unexpected keyword argument 'offset'`
(Code GEN-ERR-793, live getroffen am 2026-08-19 gegen die Supergroup -1003957110353).

**Fix 1:** Pagination laeuft jetzt ueber `limit` allein, mit lokalem Slicing:
```python
async for participant in cl.iter_participants(chat_id, limit=offset + page_size):
    participants.append(participant)
participants = participants[offset:]
```
Fuer kleine bis mittlere Gruppen (der Use-Case dieses Tools) ist das akzeptabel. Fuer sehr
grosse Channels waere ein Aufruf von `functions.channels.GetParticipantsRequest` mit echtem
`offset`-Feld effizienter, aber `iter_participants` bleibt die einfachere, von Telethon
gewartete API-Oberflaeche.

**Problem 2 (get_full_chat):** `get_full_chat` fehlte der `@validate_id("chat_id")`-Decorator,
den alle anderen chat_id-Tools in derselben Datei tragen (siehe `leave_chat`,
`get_participants`). Ohne Validierung/Normalisierung landete ein rohes `chat_id` direkt in
`resolve_entity()`, das bei einer Supergroup-ID ohne warme Dialog-Cache mit
`ValueError: Could not resolve entity for '-1003957110353', including marked variants []`
scheiterte (Code CHAT-ERR-448, live getroffen am 2026-08-19).

**Fix 2:** `@validate_id("chat_id")` ergaenzt, konsistent mit den anderen Tools in
`chats.py`/`groups.py`. Der Decorator normalisiert String-IDs zu int und validiert den
Wertebereich, aendert an `resolve_entity()` selbst nichts. Die eigentliche
Cache-Warming-/Marked-ID-Fallback-Logik in `resolve_entity()` (siehe
`telegram_mcp/runtime.py`) war bereits korrekt und bleibt unangetastet; ob CHAT-ERR-448
allein durch die fehlende Normalisierung ausgeloest wurde oder ob die betroffene Supergroup
zum Fehlerzeitpunkt zusaetzlich noch keinen warmen Dialog-Cache hatte, ist mit den
vorliegenden Log-Daten nicht abschliessend zu trennen. Die Live-Verifikation nach einem
regulaeren Neustart des MCP-Servers zeigt, ob damit beide Tools zuverlaessig laufen.

**Test:** `tests/test_participants_pagination.py`. Mockt `iter_participants` exakt mit der
Telethon-1.42-Signatur (kein `offset`-Kwarg, sonst TypeError wie im echten Fehlerfall) und
prueft Pagination ueber mehrere Seiten inkl. Grenzfaellen (letzte Teil-Seite, Seite jenseits
des Endes). Ein zusaetzlicher Guard-Test importiert die echte installierte Telethon-Version
und stellt sicher, dass `offset` dort tatsaechlich fehlt, damit der Fix nicht an einer
zukuenftigen Telethon-Version vorbeizielt. Laeuft komplett offline, kein Telegram-Client,
keine Session.

**Wirksam nach:** MCP-Server-Neustart (Claude Code neu starten), damit `groups.py`/`chats.py`
neu geladen werden. NICHT in dieser Session/diesem Chip getestet, siehe Completion-Report.
