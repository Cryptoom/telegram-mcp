# Local Patches (Cryptoom fork of chigwell/telegram-mcp)

Liste der lokalen Abweichungen gegenüber `upstream/main`. Bei jedem Upstream-Merge
(`git fetch upstream && git merge upstream/main`) prüfen, ob die Patches noch
greifen, und sie sonst neu anwenden.

## Patch 1: Server-CLI-Roots mit Client-MCP-Roots unionen (2026-06-05)

**Datei:** `telegram_mcp/runtime.py`, Funktion `_get_effective_allowed_roots_with_status`.

**Problem:** Der MCP-Server fragt den Client (z.B. Claude Code) nach dessen MCP-Roots.
Sobald der Client welche meldet (Claude Code meldet das aktuelle Projekt-cwd),
**ersetzte** der Upstream-Code die per CLI übergebenen `SERVER_ALLOWED_ROOTS` komplett
(`if client_roots: return client_roots`). Dadurch war ein explizit per CLI gesetzter
Outbox-Root (`uv run main.py /Users/.../.local/share/telegram-mcp/outbox`) faktisch
wirkungslos: `send_file`/`send_voice` aus der Outbox lieferten
`Path is outside allowed roots.`, weil nur das Client-cwd erlaubt war.

**Fix:** Die Server-CLI-Roots werden mit den Client-Roots **unioniert** statt ersetzt:
```python
return _dedupe_paths(client_roots + fallback_roots), ROOTS_STATUS_READY
```
Dadurch bleibt die operator-konfigurierte Outbox projektübergreifend nutzbar, während
das Client-cwd ebenfalls erlaubt bleibt. Der explizite Client-Deny-all-Pfad (leere
Roots-Liste) bleibt **unangetastet** und wird weiter respektiert (Security-Signal).

**Begründung / Security:** Der Union fügt nur die Pfade hinzu, die der Operator selbst
explizit per CLI gesetzt hat (ein kontrollierter, leerer Staging-Ordner). Das ist exakt
der Zweck des CLI-Args. Das Upstream-Verhalten "Client gewinnt" ist konservativer
(Least-Privilege), widerspricht aber der expliziten Operator-Absicht beim Setzen eines
CLI-Roots.

**Test:** `tests/test_file_path_security.py::test_client_roots_union_server_allowlist`
(ersetzt den früheren `test_client_roots_replace_server_allowlist`, der das alte
Replace-Verhalten festschrieb). Erwartet jetzt
`roots == [client_root, server_root]` und dass beide Roots auflösen.

**Wirksam nach:** MCP-Server-Neustart (Claude Code neu starten), damit `runtime.py`
neu geladen wird.

## Patch 2: get_participants/get_full_chat gegen Telethon-Drift gehärtet (2026-08-19)

**Dateien:** `telegram_mcp/tools/groups.py` (`get_participants`), `telegram_mcp/tools/chats.py`
(`get_full_chat`).

**Problem 1 (get_participants):** `get_participants` rief
`cl.iter_participants(chat_id, limit=page_size, offset=offset)` auf. Die installierte
Telethon-Version (1.42.0) kennt kein `offset`-Kwarg mehr an `iter_participants`
(`ChatMethods.iter_participants()` nimmt nur `entity`, `limit`, `search`, `filter`, ...).
Jeder Aufruf mit `page > 0` scheiterte mit
`TypeError: ChatMethods.iter_participants() got an unexpected keyword argument 'offset'`
(Code GEN-ERR-793, live getroffen am 2026-08-19 gegen die Supergroup -1003957110353).

**Fix 1:** Pagination läuft jetzt über `limit` allein, mit lokalem Slicing:
```python
async for participant in cl.iter_participants(chat_id, limit=offset + page_size):
    participants.append(participant)
participants = participants[offset:]
```
Für kleine bis mittlere Gruppen (der Use-Case dieses Tools) ist das akzeptabel. Für sehr
große Channels wäre ein Aufruf von `functions.channels.GetParticipantsRequest` mit echtem
`offset`-Feld effizienter, aber `iter_participants` bleibt die einfachere, von Telethon
gewartete API-Oberfläche. Weil der Fetch jetzt O(offset + page_size) statt O(page_size)
kostet, ist zusätzlich eine harte Obergrenze eingezogen: `offset + page_size` darf 10000
nicht überschreiten, sonst liefert das Tool einen klaren Fehler statt unbegrenzt Teilnehmer
in den Speicher zu ziehen.

**Problem 2 (get_full_chat):** `get_full_chat` fehlte der `@validate_id("chat_id")`-Decorator,
den alle anderen chat_id-Tools in derselben Datei tragen (siehe `leave_chat`,
`get_participants`). Ohne Validierung/Normalisierung landete ein rohes `chat_id` direkt in
`resolve_entity()`, das bei einer Supergroup-ID ohne warmen Dialog-Cache mit
`ValueError: Could not resolve entity for '-1003957110353', including marked variants []`
scheiterte (Code CHAT-ERR-448, live getroffen am 2026-08-19).

**Fix 2:** `@validate_id("chat_id")` ergänzt, konsistent mit den anderen Tools in
`chats.py`/`groups.py`. Der Decorator normalisiert String-IDs zu int und validiert den
Wertebereich, ändert an `resolve_entity()` selbst nichts. Die eigentliche
Cache-Warming-/Marked-ID-Fallback-Logik in `resolve_entity()` (siehe
`telegram_mcp/runtime.py`) war bereits korrekt und bleibt unangetastet; ob CHAT-ERR-448
allein durch die fehlende Normalisierung ausgelöst wurde oder ob die betroffene Supergroup
zum Fehlerzeitpunkt zusätzlich noch keinen warmen Dialog-Cache hatte, ist mit den
vorliegenden Log-Daten nicht abschließend zu trennen. Die Live-Verifikation nach einem
regulären Neustart des MCP-Servers zeigt, ob damit beide Tools zuverlässig laufen.

**Test:** `tests/test_participants_pagination.py`. Mockt `iter_participants` exakt mit der
Telethon-1.42-Signatur (kein `offset`-Kwarg, sonst TypeError wie im echten Fehlerfall) und
prüft Pagination über mehrere Seiten inkl. Grenzfällen (letzte Teilseite, Seite jenseits
des Endes). Ein zusätzlicher Guard-Test importiert die echte installierte Telethon-Version
und stellt sicher, dass `offset` dort tatsächlich fehlt, damit der Fix nicht an einer
zukünftigen Telethon-Version vorbeizielt. Läuft komplett offline, kein Telegram-Client,
keine Session.

**Wirksam nach:** MCP-Server-Neustart (Claude Code neu starten), damit `groups.py`/`chats.py`
neu geladen werden. NICHT in dieser Session/diesem Chip getestet, siehe Completion-Report.
