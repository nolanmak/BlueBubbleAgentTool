#!/usr/bin/env python3
"""iMessage CLI over ~/Library/Messages/chat.db.

  bluebubble.py chats  [--limit 20]
  bluebubble.py read   <who> [--limit 50] [--since 7d]
  bluebubble.py search <text> [--who X] [--limit 30] [--since 30d]
  bluebubble.py unread [--limit 30]
  bluebubble.py send   <who> <text> [--dry-run]     # gated by write mode
  bluebubble.py mode   [read|hitl|yolo]             # show / change write mode
  --json on any command for machine output.
"""
import argparse, datetime as dt, glob, json, os, re, sqlite3, subprocess, sys, time

DB = os.path.expanduser(os.environ.get("BUBBLE_DB", "~/Library/Messages/chat.db"))
AB = os.path.expanduser(os.environ.get("BUBBLE_ADDRESSBOOK", "~/Library/Application Support/AddressBook/Sources/*/AddressBook-v22.abcddb"))
EPOCH = dt.datetime(2001, 1, 1, tzinfo=dt.timezone.utc)
CFG_DIR = os.path.expanduser(os.environ.get("BUBBLE_CONFIG_DIR", "~/.config/bluebubble"))
CFG = os.path.join(CFG_DIR, "config.json")
LOG = os.path.join(CFG_DIR, "sent.log")
MODES = ("read", "hitl", "yolo")  # strictest first
SEND_TIMEOUT = int(os.environ.get("BUBBLE_SEND_TIMEOUT", "60"))
CONFIRM_WAIT = int(os.environ.get("BUBBLE_CONFIRM_WAIT", "20"))
DIALOG_TIMEOUT = int(os.environ.get("BUBBLE_DIALOG_TIMEOUT", "120"))


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


def split_people(who):
    return [p.strip() for p in re.split(r"\s*(?:,|\+|&)\s*", who) if p.strip()]


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

    def members(self):
        if not hasattr(self, "_members"):
            self._members = {}
            for cid, h in self.db.execute(
                    "select j.chat_id, h.id from chat_handle_join j join handle h on h.ROWID=j.handle_id"):
                self._members.setdefault(cid, []).append(h)
        return self._members

    def person_matches(self, q, handle):
        q = q.strip().lower()
        qn = norm(q) if re.search(r"\d{4}", q) else None
        return bool(q) and (q in self.name(handle).lower() or q == handle.lower() or bool(qn and qn == norm(handle)))

    def most_recent(self, ids):
        return self.db.execute(
            f"select cmj.chat_id from chat_message_join cmj join message m on m.ROWID=cmj.message_id "
            f"where cmj.chat_id in ({','.join(map(str, ids))}) order by m.date desc limit 1").fetchone()[0]

    def match_chats(self, who):
        """Chat ROWIDs whose name, participants, or identifier match `who`.
        "A, B" (or "A + B") means chats that include every named person."""
        people = split_people(who)
        if len(people) > 1:
            return {cid for cid, hs in self.members().items()
                    if all(any(self.person_matches(q, h) for h in hs) for q in people)}
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
        out.append({"chat": ctx.chat_label(cid), "members": [ctx.name(h) for h in ctx.members().get(cid, [])], "guid": ctx.db.execute("select guid from chat where ROWID=?", (cid,)).fetchone()[0], "last": ts(d).isoformat(timespec="minutes"),
                    "preview": f"{m['from']}: {m['text'][:80].replace(chr(10), ' ')}"})
    if a.json:
        print(json.dumps(out, ensure_ascii=False, indent=1))
    else:
        for c in out:
            who = ", ".join(c["members"])
            extra = f"  ({who})" if c["members"] and who != c["chat"] else ""
            print(f"{c['last'][:16].replace('T', ' ')}  {c['chat']}{extra}\n    {c['preview']}")


def query_msgs(ctx, where, params, limit):
    sql = MSG_SQL + " where m.associated_message_type=0 " + where + " order by m.date desc limit ?"
    rows = ctx.db.execute(sql, (*params, limit)).fetchall()
    return [row_to_msg(ctx, r) for r in reversed(rows)]


def chat_filter(ctx, who):
    ids = ctx.match_chats(who)
    if not ids:
        sys.exit(f"No chat matches '{who}'. Try `bluebubble.py chats`.")
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


def dialog_script(ok, timeout, dialog=None):
    """AppleScript for the approval dialog. `dialog` lets tests stub out the real display dialog."""
    dialog = dialog or (f'display dialog (item 2 of argv) with title (item 1 of argv) '
                        f'buttons {{"Don\'t {ok}", "{ok}"}} default button "{ok}" '
                        f'with icon caution giving up after {timeout}')
    return f"""on run argv
  activate
  try
    set r to {dialog}
  on error errMsg number errNum
    return "error:" & errNum & " " & errMsg
  end try
  if gave up of r then return "timeout"
  return "button:" & (button returned of r)
end run"""


def human_confirm(title, body, ok="Send", dialog=None):
    """Native macOS dialog. Returns (status, detail): approved | declined | timeout | error."""
    t0 = time.time()
    rc, out, err = osa(dialog_script(ok, DIALOG_TIMEOUT, dialog), title, body)
    human_confirm.elapsed = round(time.time() - t0, 1)
    if rc != 0:
        return "error", err or f"osascript exit {rc}"
    human_confirm.raw = out
    if out == f"button:{ok}":
        return "approved", ""
    if out == f"button:Don't {ok}":
        return "declined", ""
    if out == "timeout":
        return "timeout", ""
    return "error", f"dialog returned {out!r}"


def log_attempt(**fields):
    os.makedirs(CFG_DIR, exist_ok=True)
    with open(LOG, "a") as f:
        f.write(json.dumps({"time": dt.datetime.now().astimezone().isoformat(timespec="seconds"), **fields},
                           ensure_ascii=False) + "\n")


def cmd_mode(ctx, a):
    cur = get_mode()
    if not a.new:
        print(cur); return
    if MODES.index(a.new) > MODES.index(cur):
        status, detail = human_confirm(
            "iMessage write mode", f"Allow Claude to change iMessage mode from {cur} to {a.new}?", ok="Allow")
        log_attempt(action="mode", from_mode=cur, to_mode=a.new, status=status, detail=detail,
                    dialog_s=human_confirm.elapsed)
        if status != "approved":
            sys.exit(f"Mode change to {a.new} not approved ({status}{': ' + detail if detail else ''}); still {cur}.")
    set_mode(a.new)
    print(f"mode: {cur} -> {a.new}")


def loose(name):
    """Case-, emoji- and punctuation-insensitive form of a group name."""
    return " ".join(re.sub(r"[^\w\s]", " ", name.casefold()).split())


def resolve_one_chat(ctx, who):
    """Strict matching for sends. "Name" → that person's 1:1 chat or a group with that name.
    "A, B" → the group whose members are exactly A and B (each name must match one member)."""
    members = ctx.members()
    people = split_people(who)
    ids = []
    if len(people) > 1:
        supersets = []
        for cid, hs in members.items():
            if not all(any(ctx.person_matches(q, h) for h in hs) for q in people):
                continue
            exact = len(hs) == len(people) and all(any(ctx.person_matches(q, h) for q in people) for h in hs)
            (ids if exact else supersets).append(cid)
        if not ids:
            hint = ""
            if supersets:
                opts = "\n  ".join(f"{ctx.chat_label(i)} [{', '.join(ctx.name(h) for h in members[i])}]  "
                                   f"guid={ctx.db.execute('select guid from chat where ROWID=?', (i,)).fetchone()[0]}"
                                   for i in supersets[:15])
                hint = f"\nGroups that also include other people (send by guid if you mean one):\n  {opts}"
            sys.exit(f"No group has exactly: {', '.join(people)}.{hint}")
    else:
        for cid, dn in ctx.db.execute("select ROWID, display_name from chat"):
            hs = members.get(cid, [])
            if dn:
                if loose(who) and loose(who) == loose(dn):
                    ids.append(cid)
            elif len(hs) == 1 and ctx.person_matches(who, hs[0]):
                ids.append(cid)
        if not ids:
            sys.exit(f"No 1:1 chat or group named '{who}'. For a group, list its members: \"A, B\".")
    if len(ids) > 1:
        # same people on several services (iMessage/SMS/RCS): pick the most recently used chat
        sets = {frozenset(ctx.name(h) for h in members.get(i, [])) for i in ids}
        if len(sets) == 1:
            ids = [ctx.most_recent(ids)]
        else:
            opts = "\n  ".join(f"{ctx.chat_label(i)} [{', '.join(ctx.name(h) for h in members.get(i, []))}]" for i in ids[:15])
            sys.exit(f"'{who}' matches {len(ids)} different chats; be more specific:\n  {opts}")
    guid, svc = ctx.db.execute("select guid, service_name from chat where ROWID=?", (ids[0],)).fetchone()
    return ids[0], guid, svc


SEND_SCRIPT = """on run argv
  tell application "Messages" to send (item 1 of argv) to chat id (item 2 of argv)
end run"""


def confirm_in_db(chat_id, text, after_ns, wait_s):
    """Poll chat.db for our outgoing message; osascript's exit code alone isn't proof of a send."""
    deadline = time.time() + wait_s
    while True:
        db = connect()
        for t, body, sent, err in db.execute(
                "select m.text, m.attributedBody, m.is_sent, m.error from message m "
                "join chat_message_join cmj on cmj.message_id = m.ROWID "
                "where cmj.chat_id=? and m.is_from_me=1 and m.date>=?", (chat_id, after_ns)):
            if (t or decode_body(body) or "").strip() == text.strip():
                return ("error" if err else "confirmed"), (f"chat.db error {err}" if err else "")
        db.close()
        if time.time() >= deadline:
            return "unconfirmed", f"not in chat.db after {wait_s}s"
        time.sleep(1)


def cmd_send(ctx, a):
    mode = get_mode()
    base = {"action": "send", "mode": mode, "who": a.who}
    if mode == "read":
        log_attempt(**base, status="refused", detail="read mode")
        sys.exit("Write mode is 'read': sending is disabled. The user can enable it with `bluebubble.py mode hitl`.")
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
    base.update(to=label, guid=guid, text=text)
    summary = f"To: {label} ({svc})\n\n{text}"
    if a.dry_run:
        print(f"[dry-run, mode={mode}] would send ->\n{summary}"); return
    if mode == "hitl":
        status, detail = human_confirm("Claude wants to send an iMessage", summary)
        if status != "approved":
            log_attempt(**base, status=status, detail=detail, dialog_s=human_confirm.elapsed,
                        dialog_raw=getattr(human_confirm, "raw", None))
            sys.exit(f"Not sent: approval dialog {status}{': ' + detail if detail else ''}.")
    start_ns = int((dt.datetime.now(dt.timezone.utc) - EPOCH).total_seconds() * 1e9) - 5 * 10**9
    # "--" stops osascript option parsing so text starting with "-" is treated as data.
    try:
        r = subprocess.run(["osascript", "-e", SEND_SCRIPT, "--", text, guid],
                           capture_output=True, text=True, timeout=SEND_TIMEOUT)
        rc, err = r.returncode, r.stderr.strip()
    except subprocess.TimeoutExpired:
        rc, err = None, f"osascript timed out after {SEND_TIMEOUT}s (often a pending macOS Automation prompt)"
    status, detail = confirm_in_db(cid, text, start_ns, CONFIRM_WAIT if rc in (0, None) else 3)
    if status != "confirmed" and err:
        detail = f"{err}; {detail}"
    log_attempt(**base, status=status, osascript_rc=rc, detail=detail,
                dialog_s=getattr(human_confirm, "elapsed", None) if mode == "hitl" else None)
    if status == "confirmed":
        print(f"Sent to {label} (confirmed in chat.db).")
    elif status == "unconfirmed":
        sys.exit(f"Send not confirmed: {detail}. It may still arrive. Check `bluebubble.py read` before retrying.")
    else:
        sys.exit(f"Send failed: {detail}")

def main():
    p = argparse.ArgumentParser(description="iMessage access for agents")
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
