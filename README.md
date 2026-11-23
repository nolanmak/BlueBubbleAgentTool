# imsg

Let Claude Code (or Codex, or any agent with a shell) **read your iMessages** on macOS, and **send them only when you allow it**.

`imsg` is a single-file Python CLI plus an agent skill. It reads `~/Library/Messages/chat.db` in read-only mode, resolves names from your macOS Contacts, and sends through Messages.app. There are no dependencies beyond the Python 3 that ships with macOS.

```text
$ imsg.py chats --limit 2
2026-11-23 10:22  Grace Hopper
    Grace Hopper: found the bug, it was a moth
2026-11-23 09:35  Book Club
    me: next week works
```

## Commands

```bash
imsg.py chats  [--limit 20]                       # recent conversations + last message
imsg.py read   "<name|number|email|group>" [--limit 50] [--since 7d|2026-11-01]
imsg.py search "<text>" [--who "<name>"] [--since 30d] [--limit 30]
imsg.py unread [--limit 30]
imsg.py send   "<who>" "<text>" [--dry-run]       # gated by write mode
imsg.py mode   [read|hitl|yolo]                   # show / change write mode
imsg.py --json <command> ...                      # structured output for agents
```

Most message text on recent macOS lives in an `attributedBody` blob, not the `text` column. `imsg` decodes it, so reading and search see all of your messages. Tapback reactions are hidden.

## Write modes

The mode is stored in `~/.config/imsg/config.json` and defaults to `hitl`.

| Mode   | `send` behavior |
|--------|-----------------|
| `read` | Refuses to send. |
| `hitl` | Pops a native macOS dialog showing the recipient and the exact text. It sends only if you click **Send**. Cancel, or 2 minutes with no click, means nothing is sent. |
| `yolo` | Sends immediately. |

- **Loosening the mode** (`read` → `hitl` → `yolo`) pops its own approval dialog, so an agent can't quietly give itself yolo. Tightening never asks.
- **Recipient matching for sends is strict.** A name matches 1:1 chats by contact, number or email. A group matches only by its exact name or its chat guid (`imsg.py --json chats`). If more than one chat matches, the send fails instead of guessing. If the same person has iMessage, SMS and RCS threads, the most recently used one is picked.
- **Every send attempt is logged** to `~/.config/imsg/sent.log`.

> **Threat model:** the modes stop accidents and well-behaved agents. They are not a sandbox. An agent with unrestricted shell access could edit the config file or call AppleScript itself. The bundled skill tells it not to. For a hard guarantee, don't give the agent an unrestricted shell.

## Install

```bash
git clone https://github.com/nolanmak/imsg ~/imsg
~/imsg/install.sh        # symlinks the skill into ~/.claude/skills (and ~/.codex/skills if present)
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

The tests build a synthetic `chat.db` and AddressBook, so they never touch your real messages. Set `IMSG_DB`, `IMSG_ADDRESSBOOK` and `IMSG_CONFIG_DIR` to point the CLI somewhere else.

## License

MIT
