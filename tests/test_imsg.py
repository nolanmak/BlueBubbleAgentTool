"""Tests run against a synthetic chat.db + AddressBook; no real messages are touched."""
import json, os, sqlite3, subprocess, sys, tempfile, unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "skill", "imessage", "imsg.py")
sys.path.insert(0, os.path.dirname(SCRIPT))
import imsg  # noqa: E402

NS = 10**9
T0 = 785_000_000  # seconds since 2001-01-01 (late 2025)


def typedstream(text):
    """Minimal NSAttributedString typedstream like Messages writes."""
    b = text.encode()
    n = bytes([len(b)]) if len(b) < 0x80 else b"\x81" + len(b).to_bytes(2, "little")
    return (b"\x04\x0bstreamtyped\x81\xe8\x03\x84\x01@\x84\x84\x84\x12NSAttributedString\x00"
            b"\x84\x84\x08NSObject\x00\x85\x92\x84\x84\x84\x08NSString\x01\x94\x84\x01+" + n + b + b"\x86\x84")


def build_fixture(d):
    db = sqlite3.connect(os.path.join(d, "chat.db"))
    db.executescript("""
    create table handle(ROWID integer primary key, id text);
    create table chat(ROWID integer primary key, guid text, display_name text, chat_identifier text, service_name text);
    create table chat_handle_join(chat_id int, handle_id int);
    create table message(ROWID integer primary key, date int, is_from_me int, text text, attributedBody blob,
      handle_id int, cache_has_attachments int default 0, is_read int default 1, associated_message_type int default 0);
    create table chat_message_join(chat_id int, message_id int);
    insert into handle values (1,'+15550001111'),(2,'+15550002222'),(3,'ada@example.com');
    insert into chat values
      (1,'any;-;+15550001111',null,'+15550001111','iMessage'),
      (2,'any;-;+15550001111',null,'+15550001111','SMS'),
      (3,'any;+;chat123',null,'chat123','iMessage'),
      (4,'any;+;chat456','Book Club 🚀','chat456','iMessage'),
      (5,'any;-;ada@example.com',null,'ada@example.com','iMessage');
    insert into chat_handle_join values (1,1),(2,1),(3,1),(3,2),(4,2),(4,3),(5,3);
    """)
    msgs = [  # rowid, chat, date, from_me, text, body, handle, read, assoc
        (1, 1, T0 + 10, 0, "plain text hello", None, 1, 1, 0),
        (2, 1, T0 + 20, 1, None, typedstream("decoded from blob"), 1, 1, 0),
        (3, 2, T0 + 5, 0, "old sms", None, 1, 1, 0),
        (4, 3, T0 + 30, 0, None, typedstream("group says hi\nline two"), 2, 0, 0),
        (5, 4, T0 + 40, 0, None, typedstream("x" * 300), 3, 1, 0),
        (6, 1, T0 + 50, 0, "Loved “hello”", None, 1, 1, 2000),
    ]
    for r, c, t, me, txt, body, h, rd, a in msgs:
        db.execute("insert into message values (?,?,?,?,?,?,0,?,?)", (r, t * NS, me, txt, body, h, rd, a))
        db.execute("insert into chat_message_join values (?,?)", (c, r))
    db.commit()
    ab_dir = os.path.join(d, "Sources", "X"); os.makedirs(ab_dir)
    ab = sqlite3.connect(os.path.join(ab_dir, "AddressBook-v22.abcddb"))
    ab.executescript("""
    create table ZABCDRECORD(Z_PK integer primary key, ZFIRSTNAME text, ZLASTNAME text, ZORGANIZATION text);
    create table ZABCDPHONENUMBER(ZOWNER int, ZFULLNUMBER text);
    create table ZABCDEMAILADDRESS(ZOWNER int, ZADDRESS text);
    insert into ZABCDRECORD values (1,'Grace','Hopper',null),(2,'Alan','Turing',null),(3,'Ada','Lovelace',null);
    insert into ZABCDPHONENUMBER values (1,'(555) 000-1111'),(2,'+1 555 000 2222');
    insert into ZABCDEMAILADDRESS values (3,'Ada@Example.com');
    """)
    ab.commit()


class Base(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        build_fixture(cls.tmp.name)
        cls.env = dict(os.environ,
                       IMSG_DB=os.path.join(cls.tmp.name, "chat.db"),
                       IMSG_ADDRESSBOOK=os.path.join(cls.tmp.name, "Sources/*/AddressBook-v22.abcddb"),
                       IMSG_CONFIG_DIR=os.path.join(cls.tmp.name, "cfg"),
                       IMSG_DIALOG_TIMEOUT="1")

    def run_cli(self, *args):
        return subprocess.run([sys.executable, SCRIPT, *args], env=self.env, capture_output=True, text=True)

    def set_mode(self, m):
        os.makedirs(self.env["IMSG_CONFIG_DIR"], exist_ok=True)
        with open(os.path.join(self.env["IMSG_CONFIG_DIR"], "config.json"), "w") as f:
            json.dump({"mode": m}, f)


class Unit(unittest.TestCase):
    def test_decode_short_and_long(self):
        self.assertEqual(imsg.decode_body(typedstream("hi there")), "hi there")
        self.assertEqual(imsg.decode_body(typedstream("y" * 500)), "y" * 500)
        self.assertIsNone(imsg.decode_body(None))
        self.assertIsNone(imsg.decode_body(b"garbage"))

    def test_norm(self):
        self.assertEqual(imsg.norm("+1 (555) 000-1111"), "5550001111")
        self.assertEqual(imsg.norm("A@B.com"), "a@b.com")


class Read(Base):
    def test_chats_lists_names_and_hides_tapbacks(self):
        out = json.loads(self.run_cli("--json", "chats").stdout)
        self.assertEqual(out[0]["chat"], "Grace Hopper")
        self.assertNotIn("Loved", out[0]["preview"])
        self.assertIn("Book Club 🚀", [c["chat"] for c in out])

    def test_read_by_name_merges_chats_oldest_first(self):
        msgs = json.loads(self.run_cli("--json", "read", "grace").stdout)
        texts = [m["text"] for m in msgs]
        self.assertEqual(texts[0], "old sms")
        self.assertIn("decoded from blob", texts)
        self.assertIn("group says hi\nline two", texts)

    def test_read_by_phone_any_format(self):
        msgs = json.loads(self.run_cli("--json", "read", "555-000-2222").stdout)
        self.assertTrue(any(m["from"] == "Alan Turing" for m in msgs))

    def test_search_decodes_blobs(self):
        msgs = json.loads(self.run_cli("--json", "search", "DECODED").stdout)
        self.assertEqual([m["text"] for m in msgs], ["decoded from blob"])

    def test_unread(self):
        msgs = json.loads(self.run_cli("--json", "unread").stdout)
        self.assertEqual([m["id"] for m in msgs], [4])

    def test_text_output_is_one_line_per_message(self):
        out = self.run_cli("read", "Alan").stdout
        self.assertIn("group says hi ⏎ line two", out)


class Send(Base):
    def test_read_mode_refuses(self):
        self.set_mode("read")
        r = self.run_cli("send", "Grace", "hi")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("disabled", r.stderr)

    def test_person_picks_most_recent_1to1_not_group(self):
        self.set_mode("hitl")
        r = self.run_cli("send", "Grace", "hi", "--dry-run")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("To: Grace Hopper (iMessage)", r.stdout)

    def test_group_only_by_exact_name(self):
        self.set_mode("hitl")
        self.assertIn("To: Book Club 🚀", self.run_cli("send", "Book Club", "hi", "--dry-run").stdout)
        self.assertIn("To: Book Club 🚀", self.run_cli("send", "BOOK CLUB", "hi", "--dry-run").stdout)
        self.assertNotEqual(self.run_cli("send", "Book", "hi", "--dry-run").returncode, 0)

    def test_ambiguous_recipient_errors(self):
        self.set_mode("hitl")
        r = self.run_cli("send", "a", "hi", "--dry-run")  # matches several people
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("matches", r.stderr)

    def test_unnamed_group_by_guid(self):
        self.set_mode("hitl")
        r = self.run_cli("send", "any;+;chat123", "hi", "--dry-run")
        self.assertIn("Grace Hopper, Alan Turing", r.stdout)

    def test_empty_message_refused(self):
        self.set_mode("yolo")
        self.assertNotEqual(self.run_cli("send", "Grace", "  ", "--dry-run").returncode, 0)

    def test_tightening_mode_needs_no_dialog(self):
        self.set_mode("yolo")
        r = self.run_cli("mode", "read")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.run_cli("mode").stdout.strip(), "read")


@unittest.skipUnless(sys.platform == "darwin", "needs osascript")
class Dialog(unittest.TestCase):
    """Runs the real dialog AppleScript with `display dialog` stubbed by the record it returns."""

    def check(self, stub, expected):
        status, detail = imsg.human_confirm("t", "b", dialog=stub)
        self.assertEqual(status, expected, detail)

    def test_clicking_send_approves(self):  # regression: this path always failed before
        self.check('{button returned:"Send", gave up:false}', "approved")

    def test_timeout(self):
        self.check('{button returned:"", gave up:true}', "timeout")

    def test_dont_send_is_declined(self):
        self.check('{button returned:"Don\'t Send", gave up:false}', "declined")

    def test_dialog_error_is_reported_not_declined(self):
        self.check('run script "error number -128"', "error")

    def test_real_dialog_script_compiles(self):
        r = subprocess.run(["osacompile", "-o", "/dev/null", "-e", imsg.dialog_script("Send", 5)],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)


class SendLog(Base):
    def log_lines(self):
        with open(os.path.join(self.env["IMSG_CONFIG_DIR"], "sent.log")) as f:
            return [json.loads(l) for l in f]

    def test_refusal_is_logged(self):
        self.set_mode("read")
        self.run_cli("send", "Grace", "hi")
        last = self.log_lines()[-1]
        self.assertEqual((last["action"], last["status"]), ("send", "refused"))

    def test_dialog_timeout_is_logged(self):
        self.set_mode("hitl")
        r = self.run_cli("send", "Grace", "hello there")
        self.assertIn("timeout", r.stderr)
        last = self.log_lines()[-1]
        self.assertEqual((last["status"], last["to"], last["text"]), ("timeout", "Grace Hopper", "hello there"))


if __name__ == "__main__":
    unittest.main()
