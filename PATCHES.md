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

## Stand nach Merge 2026-09-16 (TG-01, `upstream/main` 30e4ba8, 138 Commits nachgezogen)

**Patch 1 (Server-CLI-Roots-Union): aktiv, neu integriert in eine gewachsene Zielfunktion.**
Upstream hat `_get_effective_allowed_roots_with_status` seit dem letzten Sync stark
umgebaut: Timeout-Handling fuer `roots/list` (`TELEGRAM_ROOTS_TIMEOUT_SECONDS`), ein neuer,
standardmaessig AUS-geschalteter Fallback-Mechanismus `TELEGRAM_ALLOW_SERVER_ROOTS_FALLBACK`
fuer den Fall, dass der Client eine LEERE Roots-Liste meldet oder `list_roots` unerwartet
fehlschlaegt, plus mehrere neue `ROOTS_STATUS_*`-Werte. Dieser neue Fallback deckt NUR den
leeren/fehlerhaften Fall ab, nicht unseren Fall (Client meldet nicht-leere Roots, z.B. das
Projekt-cwd von Claude Code, aber ohne den Outbox-Pfad). Der Merge hat unseren Union-Patch im
`if client_roots:`-Zweig unkonfliktiert uebernommen (git hat ihn als reine, additive Aenderung
gegenueber der Basis erkannt), die eigentliche Konflikt-Stelle lag nur bei der leeren-Liste-
Behandlung direkt darunter. Dort wurde die Upstream-Loesung (opt-in Fallback) uebernommen,
unser alter Kommentar ("wir fallen hier bewusst NICHT zurueck") ist damit ueberholt und wurde
durch Upstreams Kommentar ersetzt, der den neuen opt-in-Mechanismus korrekt beschreibt. Fund
in `telegram_mcp/runtime.py`, Zeile ca. 1890-1913 nach dem Merge.

**Patch 2 Fix 1 (`get_participants`-Pagination): upstream-merged, ABER unsere 10000er-
Sicherheitsgrenze ist eine offene Entscheidung.** Upstream hat exakt dasselbe Fix-Pattern
(`limit=offset+page_size` plus lokales Slicing) selbststaendig eingefuehrt, ohne
unsere zusaetzliche Obergrenze `offset + page_size <= 10000`. Da unser Guard textuell VOR der
eigentlichen Konflikt-Stelle lag (ausserhalb des Merge-Konflikts, rein additiv gegenueber der
gemeinsamen Basis), hat der Merge ihn unveraendert beibehalten: die 10000er-Grenze ist nach
diesem Merge weiterhin aktiv, obwohl Upstream sie nicht hat. Das war KEINE bewusste
Entscheidung dieses Chips, sondern ein Nebenprodukt der Merge-Mechanik. Offene Frage an Olli
im Completion-Report: Grenze beibehalten (Status quo nach diesem Merge) oder entfernen, um
naeher an Upstream zu bleiben. `tests/test_participants_pagination.py` (inkl. Guard-Test
gegen die real installierte Telethon-Signatur) bleibt vollstaendig gruen, unveraendert
uebernommen, kein Anpassungsbedarf.

**Patch 2 Fix 2 (`@validate_id("chat_id")` auf `get_full_chat`): aktiv, unveraendert
uebernommen.** `chats.py` hat beim Merge KEINEN Konflikt ausgeloest (Upstream hat diese
konkrete Zeile nicht angefasst), der Decorator steht unveraendert auf Zeile 733.

**`telethon>=1.45.0`:** `uv sync` erfolgreich, `telethon==1.45.0` installiert (MTProto Layer
229 laut Upstream-Commit `0f6253c`). Volle Testsuite (628 Tests) laeuft gruen gegen die neue
Version, keine weiteren Signatur-Drifts jenseits der beiden bekannten Patches gefunden.

**Neue Upstream-Faehigkeiten, fuer unsere Nutzung potenziell relevant** (volle Liste im
Completion-Report `00-Index/inbox/spawn-2026-09-16-tg-01-fork-update.md`): natives
Voice/Video-Note-Transkript-Tool (`TELEGRAM_TRANSCRIBE`, Groq- oder Telegram-Premium-Engine),
Session-Pool fuer mehrere gleichzeitige Clients auf demselben Account
(`TELEGRAM_SESSION_STRINGS`), Session-Lock (`TELEGRAM_SESSION_LOCK=exclusive|shared`),
opt-in Server-Roots-Fallback (`TELEGRAM_ALLOW_SERVER_ROOTS_FALLBACK`, siehe Patch 1 oben),
Roots-Timeout (`TELEGRAM_ROOTS_TIMEOUT_SECONDS`), Tool-Exposure-Allowlist im Read-Only-Modus
(`TELEGRAM_EXPOSED_TOOLS=read-only+send_message,...`), Forum-Topic-Tools, Reply-Quotes in
Message-Reads, `remove_user`-Tool fuer Gruppen (Eject ohne Bann), konfigurierbare
Geraete-Identitaet, HTTP/SSE-Transport.
