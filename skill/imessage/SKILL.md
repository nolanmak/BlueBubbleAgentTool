---
name: imessage
description: Read and (when allowed) send the user's iMessages/SMS on this Mac — list recent chats, read a conversation, search message text, show unread, send a text. Use when the user asks what someone texted, to catch up on a thread, find a message, check unread texts, or text someone.
---

# iMessage

CLI: `python3 ~/.claude/skills/imessage/imsg.py` — reads `~/Library/Messages/chat.db`
in read-only mode and resolves names from macOS Contacts. Needs Full Disk Access for the
terminal app (System Settings → Privacy & Security → Full Disk Access).

```bash
imsg.py chats [--limit 20]                     # recent conversations + last message
imsg.py read "<name|number|group>" [--limit 50] [--since 7d|2026-11-01]
imsg.py search "<text>" [--who "<name>"] [--since 30d] [--limit 30]
imsg.py unread [--limit 30]
imsg.py send "<who>" "<text>" [--dry-run]       # gated by write mode
imsg.py mode [read|hitl|yolo]                  # show / change write mode
imsg.py --json <cmd> ...                       # structured output
```

## Write modes (stored in ~/.config/imsg/config.json, default hitl)
- `read` — send refuses.
- `hitl` — send pops a native macOS dialog showing recipient + text; the user must click Send
  (Cancel / 120s timeout = not sent).
- `yolo` — sends immediately.
- Loosening the mode (read→hitl→yolo) itself requires a dialog click; tightening doesn't.
- Every send attempt is logged to ~/.config/imsg/sent.log.

## Send rules
- Only send when the user asked for that specific message. Dry-run first if the recipient is unclear.
- Recipient matching is strict: 1:1 chats by contact name/number/email, groups only by exact group
  name or guid (`imsg.py --json chats`). Ambiguous matches error out — never guess.
- Never edit the config file or call osascript/Messages directly to get around the mode.
- If a send returns "declined or timed out", don't retry; tell the user.

- `who` matches contact name, phone (any format), email, or group name, case-insensitive substring.
  If several chats match, all are merged; narrow the name if that's noisy.
- Output is oldest → newest; newlines shown as `⏎`. Tapbacks are hidden.
- Don't paste message content into external services without asking.
- Quote only what's needed when answering; this is private data.
