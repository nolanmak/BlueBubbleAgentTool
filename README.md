# BlueBubbleAgentTool

Let Claude Code (or Codex, or any agent with a shell) **read your iMessages** on macOS, and **send them only when you allow it**.

`bluebubble` is a single-file Python CLI plus an agent skill. It reads `~/Library/Messages/chat.db` in read-only mode, resolves names from your macOS Contacts, and sends through Messages.app. There are no dependencies beyond the Python 3 that ships with macOS.

```text
$ bluebubble.py chats --limit 2
2026-11-23 10:22  Grace Hopper
    Grace Hopper: found the bug, it was a moth
2026-11-23 09:35  Book Club
    me: next week works
```

> Not affiliated with [BlueBubbles](https://bluebubbles.app). This tool needs no server and keeps System Integrity Protection on; it uses only `chat.db` and AppleScript.

## Commands

```bash
bluebubble.py chats  [--limit 20]                       # recent conversations + last message
bluebubble.py read   "<name|number|email|group|A, B>" [--limit 50] [--since 7d|2026-11-01]
bluebubble.py search "<text>" [--who "<name>"] [--since 30d] [--limit 30]
bluebubble.py unread [--limit 30]
bluebubble.py send   "<who>" ["<text>"] [--file PATH ...] [--dry-run]   # gated by write mode
bluebubble.py mode   [read|hitl|yolo]                   # show / change write mode
bluebubble.py --json <command> ...                      # structured output for agents
```

Most message text on recent macOS lives in an `attributedBody` blob, not the `text` column. `bluebubble` decodes it, so reading and search see all of your messages. Tapback reactions are hidden. Received attachments show as `[attachment: name]`; `--json` adds each file's local path and type so an agent can open it.

### Sending files

```bash
bluebubble.py send "Grace" "here's the deck" --file ~/Downloads/deck.pdf --file ~/Desktop/photo.jpg
```

Messages can't read files from most folders (the send fails with error 25), so each file is first copied into `~/Library/Messages/Attachments/bluebubble/`; the copy is kept because the sent message points at it. The approval dialog lists every file. The text goes first, then each file, and each one counts as sent only once its transfer completes in `chat.db`. If an item fails, the rest are not attempted and the log records which ones went out. Limit: 100 MB per file.

## Write modes

The mode is stored in `~/.config/bluebubble/config.json` and defaults to `hitl`.

| Mode   | `send` behavior |
|--------|-----------------|
| `read` | Refuses to send. |
| `hitl` | Pops a native macOS dialog showing the recipient and the exact text. It sends only if you click **Send**. **Don't Send**, or 2 minutes with no click, means nothing is sent. |
| `yolo` | Sends immediately. |

- **Loosening the mode** (`read` → `hitl` → `yolo`) pops its own approval dialog, so an agent can't quietly give itself yolo. Tightening never asks.
- **Recipient matching for sends is strict.** `"Grace"` means Grace's 1:1 chat (by contact name, number or email). `"Grace, Alan"` (or `Grace + Alan`) means the group whose members are *exactly* Grace and Alan; bigger groups that include them are listed, never picked. A group's own name works too, ignoring case and emoji. If more than one chat matches, the send fails instead of guessing. If the same people have iMessage, SMS and RCS threads, the most recently used one is picked.
- **Every send attempt is logged** to `~/.config/bluebubble/sent.log` with its outcome (refused, declined, timeout, error, confirmed, unconfirmed). A send counts as confirmed only once it appears in `chat.db`.

> **Threat model:** the modes stop accidents and well-behaved agents. They are not a sandbox. An agent with unrestricted shell access could edit the config file or call AppleScript itself. The bundled skill tells it not to. For a hard guarantee, don't give the agent an unrestricted shell.

## Install

```bash
git clone https://github.com/nolanmak/BlueBubbleAgentTool ~/BlueBubbleAgentTool
~/BlueBubbleAgentTool/install.sh        # symlinks the skill into ~/.claude/skills (and ~/.codex/skills if present)
```

Then:

1. **Full Disk Access.** Grant it to your terminal app (System Settings → Privacy & Security → Full Disk Access) so it can read `chat.db` and Contacts.
2. **Automation permission.** The first send asks to let your terminal control **Messages**. Allow it.
3. **Ask your agent** things like *"what did Grace text me today?"* or *"text Book Club that I'm running late"*.

## Why a CLI and not an MCP server?

The safety gate is a native dialog that only a human can click. That works the same whether the agent calls a CLI or an MCP tool. A CLI is simpler, has no server to run, and works in any agent with a shell. Wrapping it as an MCP server would be about 50 lines if you need it for Claude Desktop.

## Development

```bash
python3 -m unittest discover -s tests -v
```

The tests build a synthetic `chat.db` and AddressBook, so they never touch your real messages. Set `BUBBLE_DB`, `BUBBLE_ADDRESSBOOK` and `BUBBLE_CONFIG_DIR` to point the CLI somewhere else.

## License

MIT
