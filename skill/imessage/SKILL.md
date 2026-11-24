---
name: imessage
description: Read and (when allowed) send the user's iMessages/SMS on this Mac — list recent chats, read a conversation, search message text, show unread, send a text. Use when the user asks what someone texted, to catch up on a thread, find a message, check unread texts, or text someone.
---

# iMessage

CLI: `python3 ~/.claude/skills/imessage/bluebubble.py` — reads `~/Library/Messages/chat.db`
in read-only mode and resolves names from macOS Contacts. Needs Full Disk Access for the
terminal app (System Settings → Privacy & Security → Full Disk Access).

```bash
bluebubble.py chats [--limit 20]                     # recent conversations + last message
bluebubble.py read "<name|number|group|A, B>" [--limit 50] [--since 7d|2026-11-01]
bluebubble.py search "<text>" [--who "<name>"] [--since 30d] [--limit 30]
bluebubble.py unread [--limit 30]
bluebubble.py send "<who>" "<text>" [--dry-run]       # gated by write mode
bluebubble.py mode [read|hitl|yolo]                  # show / change write mode
bluebubble.py --json <cmd> ...                       # structured output
```

## Write modes (stored in ~/.config/bluebubble/config.json, default hitl)
- `read` — send refuses.
- `hitl` — send pops a native macOS dialog showing recipient + text; the user must click Send
  (Don't Send / 120s timeout = not sent).
- `yolo` — sends immediately.
- Loosening the mode (read→hitl→yolo) itself requires a dialog click; tightening doesn't.
- Every send attempt is logged to ~/.config/bluebubble/sent.log.

## Send rules
- Only send when the user asked for that specific message. Dry-run first if the recipient is unclear.
- **Recipient matching for sends is strict.** `"Grace"` means Grace's 1:1 chat (by contact name, number or email). `"Grace, Alan"` (or `Grace + Alan`) means the group whose members are *exactly* Grace and Alan; bigger groups that include them are listed, never picked. A group's own name works too, ignoring case and emoji. If more than one chat matches, the send fails instead of guessing. If the same people have iMessage, SMS and RCS threads, the most recently used one is picked.
- Never edit the config file or call osascript/Messages directly to get around the mode.
- If a send returns "declined or timed out", don't retry; tell the user.

- `who` matches contact name, phone (any format), email, or group name, case-insensitive substring.
  If several chats match, all are merged; narrow the name if that's noisy.
- Output is oldest → newest; newlines shown as `⏎`. Tapbacks are hidden.
- Don't paste message content into external services without asking.
- Quote only what's needed when answering; this is private data.
