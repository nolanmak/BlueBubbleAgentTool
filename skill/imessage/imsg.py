#!/usr/bin/env python3
"""Read-only iMessage CLI over ~/Library/Messages/chat.db.

  imsg.py chats  [--limit 20]
  imsg.py read   <who> [--limit 50] [--since 7d]
  imsg.py search <text> [--who X] [--limit 30] [--since 30d]
  imsg.py unread [--limit 30]
  imsg.py send   <who> <text> [--dry-run]     # gated by write mode
  imsg.py mode   [read|hitl|yolo]             # show / change write mode
  --json on any command for machine output.
"""
import argparse, datetime as dt, glob, json, os, re, sqlite3, subprocess, sys

DB = os.path.expanduser(os.environ.get("IMSG_DB", "~/Library/Messages/chat.db"))
AB = os.path.expanduser(os.environ.get("IMSG_ADDRESSBOOK", "~/Library/Application Support/AddressBook/Sources/*/AddressBook-v22.abcddb"))
EPOCH = dt.datetime(2001, 1, 1, tzinfo=dt.timezone.utc)
CFG_DIR = os.path.expanduser(os.environ.get("IMSG_CONFIG_DIR", "~/.config/imsg"))
CFG = os.path.join(CFG_DIR, "config.json")
LOG = os.path.join(CFG_DIR, "sent.log")
MODES = ("read", "hitl", "yolo")  # strictest first
DIALOG_TIMEOUT = int(os.environ.get("IMSG_DIALOG_TIMEOUT", "120"))


def connect():
    try:
        return sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    except sqlite3.OperationalError as e:
        sys.exit(f"Can't open {DB} ({e}). Grant Full Disk Access to your terminal app.")


def norm(h):
    if not h:
        return ""
    if "@" in h:
        return h.lower()
    d = re.sub(r"\D", "", h)
    return d[-10:]


def load_contacts():
    names = {}
    for path in glob.glob(AB):
        try:
            c = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
            people = {r[0]: " ".join(x for x in r[1:] if x) or None for r in c.execute(
                "select Z_PK, ZFIRSTNAME, ZLASTNAME from ZABCDRECORD")}
            orgs = dict(c.execute("select Z_PK, ZORGANIZATION from ZABCDRECORD"))
            for owner, val in list(c.execute("select ZOWNER, ZFULLNUMBER from ZABCDPHONENUMBER")) + \
                    list(c.execute("select ZOWNER, ZADDRESS from ZABCDEMAILADDRESS")):
                n = people.get(owner) or orgs.get(owner)
                if n and val:
                    names.setdefault(norm(val), n)
        except sqlite3.Error:
            pass
    return names


def decode_body(blob):
    """Pull plain text out of an NSAttributedString typedstream blob."""
    if not blob:
        return None
    i = blob.find(b"NSString")
    if i < 0:
        return None
    j = blob.find(b"\x2b", i)  # '+' marks the string
    if j < 0:
        return None
    j += 1
    n = blob[j]
    if n == 0x81:
        n = int.from_bytes(blob[j + 1:j + 3], "little"); j += 3
    elif n == 0x82:
        n = int.from_bytes(blob[j + 1:j + 5], "little"); j += 5
    else:
        j += 1
    return blob[j:j + n].decode("utf-8", "replace")


def ts(v):
    if not v:
        return None
    sec = v / 1e9 if v > 1e12 else v
    return (EPOCH + dt.timedelta(seconds=sec)).astimezone()


def since_to_apple(s):
    if not s:
        return None
    m = re.fullmatch(r"(\d+)([hdw])", s)
    if m:
        delta = dt.timedelta(**{{"h": "hours", "d": "days", "w": "weeks"}[m[2]]: int(m[1])})
        t = dt.datetime.now(dt.timezone.utc) - delta
    else:
        t = dt.datetime.fromisoformat(s).astimezone(dt.timezone.utc)
    return int((t - EPOCH).total_seconds() * 1e9)


class Ctx:
    def __init__(self):
        self.db = connect()
        self.names = load_contacts()

    def name(self, handle):
        return self.names.get(norm(handle)) or handle

    def chat_label(self, chat_id):
        row = self.db.execute("select display_name, chat_identifier from chat where ROWID=?", (chat_id,)).fetchone()
        if row and row[0]:
            return row[0]
        hs = [r[0] for r in self.db.execute(
            "select h.id from chat_handle_join j join handle h on h.ROWID=j.handle_id where j.chat_id=?", (chat_id,))]
        if hs:
            return ", ".join(self.name(h) for h in hs)
        return row[1] if row else "?"

    def match_chats(self, who):
        """Chat ROWIDs whose name, participants, or identifier match `who`."""
        w = who.lower()
        wn = norm(who) if re.search(r"\d{4}", who) else None
        out = set()
        for cid, dn, ident in self.db.execute("select ROWID, display_name, chat_identifier from chat"):
            if (dn and w in dn.lower()) or (ident and w in ident.lower()) or (wn and wn in norm(ident or "")):
                out.add(cid)
        for cid, h in self.db.execute(
                "select j.chat_id, h.id from chat_handle_join j join handle h on h.ROWID=j.handle_id"):
            if w in self.name(h).lower() or w in h.lower() or (wn and wn == norm(h)):
                out.add(cid)
        return out


MSG_SQL = """
select m.ROWID, m.date, m.is_from_me, m.text, m.attributedBody, h.id, cmj.chat_id,
       m.cache_has_attachments, m.is_read, m.associated_message_type
from message m
join chat_message_join cmj on cmj.message_id = m.ROWID
left join handle h on h.ROWID = m.handle_id
"""


def row_to_msg(ctx, r):
    text = r[3] or decode_body(r[4]) or ""
    text = text.replace("￼", "").strip()
    if r[7] and not text:
        text = "[attachment]"
    elif r[7]:
        text += " [attachment]"
    t = ts(r[1])
    return {
        "id": r[0], "time": t.isoformat(timespec="minutes") if t else None,
        "from": "me" if r[2] else ctx.name(r[5]), "chat": ctx.chat_label(r[6]),
        "text": text, "read": bool(r[8]) or bool(r[2]),
    }


def print_msgs(msgs, as_json, show_chat=True):
    if as_json:
        print(json.dumps(msgs, ensure_ascii=False, indent=1)); return
    for m in msgs:
        chat = f" [{m['chat']}]" if show_chat else ""
        print(f"{m['time'][:16].replace('T', ' ')}{chat} {m['from']}: {m['text'].replace(chr(10), ' ⏎ ')}")


def cmd_chats(ctx, a):
    rows = ctx.db.execute("""
        select cmj.chat_id, max(m.date) d from chat_message_join cmj
        join message m on m.ROWID=cmj.message_id group by cmj.chat_id order by d desc limit ?""", (a.limit,)).fetchall()
    out = []
    for cid, d in rows:
        last = ctx.db.execute(MSG_SQL + " where cmj.chat_id=? and m.associated_message_type=0 order by m.date desc limit 1",
                              (cid,)).fetchone()
        m = row_to_msg(ctx, last) if last else {"text": "", "from": ""}
        out.append({"chat": ctx.chat_label(cid), "guid": ctx.db.execute("select guid from chat where ROWID=?", (cid,)).fetchone()[0], "last": ts(d).isoformat(timespec="minutes"),
                    "preview": f"{m['from']}: {m['text'][:80].replace(chr(10), ' ')}"})
    if a.json:
        print(json.dumps(out, ensure_ascii=False, indent=1))
    else:
        for c in out:
            print(f"{c['last'][:16].replace('T', ' ')}  {c['chat']}\n    {c['preview']}")


def query_msgs(ctx, where, params, limit):
    sql = MSG_SQL + " where m.associated_message_type=0 " + where + " order by m.date desc limit ?"
    rows = ctx.db.execute(sql, (*params, limit)).fetchall()
    return [row_to_msg(ctx, r) for r in reversed(rows)]


def chat_filter(ctx, who):
    ids = ctx.match_chats(who)
    if not ids:
        sys.exit(f"No chat matches '{who}'. Try `imsg.py chats`.")
    return f" and cmj.chat_id in ({','.join(map(str, ids))})", []


def cmd_read(ctx, a):
    where, params = chat_filter(ctx, a.who)
    s = since_to_apple(a.since)
    if s:
        where += " and m.date >= ?"; params.append(s)
    print_msgs(query_msgs(ctx, where, params, a.limit), a.json)


def cmd_search(ctx, a):
    where, params = (chat_filter(ctx, a.who) if a.who else ("", []))
    s = since_to_apple(a.since)
    if s:
        where += " and m.date >= ?"; params.append(s)
    needle = a.text.lower()
    sql = MSG_SQL + " where m.associated_message_type=0 " + where + " order by m.date desc"
    hits = []
    for r in ctx.db.execute(sql, params):
        t = r[3] or decode_body(r[4]) or ""
        if needle in t.lower():
            hits.append(row_to_msg(ctx, r))
            if len(hits) >= a.limit:
                break
    print_msgs(list(reversed(hits)), a.json)


def cmd_unread(ctx, a):
    print_msgs(query_msgs(ctx, " and m.is_read=0 and m.is_from_me=0", [], a.limit), a.json)


# ---------- write path ----------

def get_mode():
    try:
        m = json.load(open(CFG)).get("mode")
        return m if m in MODES else "hitl"
    except (OSError, ValueError):
        return "hitl"


def set_mode(m):
    os.makedirs(CFG_DIR, exist_ok=True)
    json.dump({"mode": m}, open(CFG, "w"))


def osa(script, *args):
    r = subprocess.run(["osascript", "-e", script, *args], capture_output=True, text=True)
    return r.returncode, r.stdout.strip(), r.stderr.strip()


def human_confirm(title, body, ok="Send"):
    """Native macOS dialog; only a human click returns True. Times out to False."""
    script = f"""on run argv
  activate
  display dialog (item 2 of argv) with title (item 1 of argv) buttons {{"Cancel", "{ok}"}} \
    default button "Cancel" cancel button "Cancel" with icon caution giving up after {DIALOG_TIMEOUT}
  if gave up of result then return "timeout"
  return button returned of result
end run"""
    rc, out, _ = osa(script, title, body)
    return rc == 0 and out == ok


def cmd_mode(ctx, a):
    cur = get_mode()
    if not a.new:
        print(cur); return
    if MODES.index(a.new) > MODES.index(cur) and not human_confirm(
            "iMessage write mode", f"Allow Claude to change iMessage mode from {cur} to {a.new}?", ok="Allow"):
        sys.exit(f"Mode change to {a.new} not approved; still {cur}.")
    set_mode(a.new)
    print(f"mode: {cur} -> {a.new}")


def resolve_one_chat(ctx, who):
    """Strict matching for sends: 1:1 chats by contact/handle, groups only by their own name."""
    w = who.lower()
    wn = norm(who) if re.search(r"\d{4}", who) else None
    members = {}
    for cid, h in ctx.db.execute(
            "select j.chat_id, h.id from chat_handle_join j join handle h on h.ROWID=j.handle_id"):
        members.setdefault(cid, []).append(h)
    ids = []
    for cid, dn, guid in ctx.db.execute("select ROWID, display_name, guid from chat"):
        hs = members.get(cid, [])
        if dn:
            if w == dn.lower():
                ids.append(cid)
        elif len(hs) == 1:
            h = hs[0]
            if w in ctx.name(h).lower() or w == h.lower() or (wn and wn == norm(h)):
                ids.append(cid)
    if not ids:
        sys.exit(f"No 1:1 chat or exactly-named group matches '{who}'. "
                 "For an unnamed group, pass its guid (see `imsg.py --json chats`).")
    if len(ids) > 1:
        # same person on several services (iMessage/SMS/RCS): pick the most recently used chat
        people = {ctx.name(members[i][0]) for i in ids if len(members.get(i, [])) == 1}
        if len(people) == 1 and all(len(members.get(i, [])) == 1 for i in ids):
            ids = [ctx.db.execute(
                f"select cmj.chat_id from chat_message_join cmj join message m on m.ROWID=cmj.message_id "
                f"where cmj.chat_id in ({','.join(map(str, ids))}) order by m.date desc limit 1").fetchone()[0]]
        else:
            opts = "\n  ".join(f"{ctx.chat_label(i)}" for i in ids[:15])
            sys.exit(f"'{who}' matches {len(ids)} different chats; be more specific:\n  {opts}")
    guid, svc = ctx.db.execute("select guid, service_name from chat where ROWID=?", (ids[0],)).fetchone()
    return ids[0], guid, svc


def cmd_send(ctx, a):
    mode = get_mode()
    if mode == "read":
        sys.exit("Write mode is 'read': sending is disabled. The user can enable it with `imsg.py mode hitl`.")
    if a.who.startswith("any;"):
        row = ctx.db.execute("select ROWID, guid, service_name from chat where guid=?", (a.who,)).fetchone()
        if not row:
            sys.exit(f"No chat with guid {a.who}")
        cid, guid, svc = row
    else:
        cid, guid, svc = resolve_one_chat(ctx, a.who)
    label = ctx.chat_label(cid)
    text = a.text.replace("\\n", "\n")
    if not text.strip():
        sys.exit("Refusing to send an empty message.")
    summary = f"To: {label} ({svc})\n\n{text}"
    if a.dry_run:
        print(f"[dry-run, mode={mode}] would send ->\n{summary}"); return
    if mode == "hitl" and not human_confirm("Claude wants to send an iMessage", summary):
        sys.exit("Not sent: declined or timed out in the approval dialog.")
    rc, _, err = osa("""on run argv
  tell application "Messages" to send (item 1 of argv) to chat id (item 2 of argv)
end run""", text, guid)
    os.makedirs(CFG_DIR, exist_ok=True)
    with open(LOG, "a") as f:
        f.write(json.dumps({"time": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
                            "mode": mode, "to": label, "guid": guid, "text": text, "ok": rc == 0, "err": err}) + "\n")
    if rc != 0:
        sys.exit(f"Send failed: {err}")
    print(f"Sent to {label}.")


def main():
    p = argparse.ArgumentParser(description="Read-only iMessage access")
    p.add_argument("--json", action="store_true")
    sp = p.add_subparsers(dest="cmd", required=True)
    c = sp.add_parser("chats"); c.add_argument("--limit", type=int, default=20)
    r = sp.add_parser("read"); r.add_argument("who"); r.add_argument("--limit", type=int, default=50); r.add_argument("--since")
    s = sp.add_parser("search"); s.add_argument("text"); s.add_argument("--who"); s.add_argument("--limit", type=int, default=30); s.add_argument("--since")
    u = sp.add_parser("unread"); u.add_argument("--limit", type=int, default=30)
    w = sp.add_parser("send"); w.add_argument("who"); w.add_argument("text"); w.add_argument("--dry-run", action="store_true")
    mo = sp.add_parser("mode"); mo.add_argument("new", nargs="?", choices=MODES)
    for x in (c, r, s, u, w, mo):
        x.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
    a = p.parse_args()
    {"chats": cmd_chats, "read": cmd_read, "search": cmd_search, "unread": cmd_unread, "send": cmd_send, "mode": cmd_mode}[a.cmd](Ctx(), a)


if __name__ == "__main__":
    main()
