"""Read-only access to Apple Mail.app's local store.

Mail keeps all envelope metadata in a SQLite database at
`~/Library/Mail/V*/MailData/Envelope Index` and the message bodies beside
it as .emlx files. Everything here reads those files directly — no OAuth,
no APIs, no scripting the app. Account display names come from the system
accounts database when it's readable, falling back to bare UUIDs.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from urllib.parse import quote, unquote

MAIL_BASE = Path.home() / "Library" / "Mail"
ACCOUNTS_DB = Path.home() / "Library" / "Accounts" / "Accounts4.sqlite"

# Mailbox urls look like `imap://<account-uuid>/<percent-encoded folder path>`.
_URL_RE = re.compile(r"^(?:ews|imap|pop|local)://([^/]+)/(.+)$")

# Nothing in the store marks a mailbox as "the inbox" — Exchange accounts
# even localize the folder name on disk — so "inbox" matches these.
INBOX_NAMES = {"inbox", "inkorg"}


class MailError(Exception):
    """A user-facing data-access error."""


def mail_dir() -> Path:
    """The newest `~/Library/Mail/V*` directory (V10 on current macOS)."""
    if not MAIL_BASE.is_dir():
        raise MailError(f"no Mail.app data at {MAIL_BASE}")
    versions = sorted(
        (p for p in MAIL_BASE.iterdir() if re.fullmatch(r"V\d+", p.name)),
        key=lambda p: int(p.name[1:]),
    )
    if not versions:
        raise MailError(f"no V* directory under {MAIL_BASE}")
    return versions[-1]


def _connect_ro(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{quote(str(path))}?mode=ro", uri=True)


def account_names() -> dict[str, tuple[str, str]]:
    """Map Mail account UUID -> (display name, username/email).

    Read from the system accounts database; a Mail account's own row is
    often blank with the name on its parent (e.g. the Google account that
    owns the Gmail mail account). Missing or unreadable is fine — callers
    fall back to the UUID.
    """
    try:
        with _connect_ro(ACCOUNTS_DB) as db:
            rows = db.execute(
                """SELECT c.ZIDENTIFIER,
                          COALESCE(NULLIF(c.ZACCOUNTDESCRIPTION, ''), p.ZACCOUNTDESCRIPTION, ''),
                          COALESCE(NULLIF(c.ZUSERNAME, ''), p.ZUSERNAME, '')
                   FROM ZACCOUNT c LEFT JOIN ZACCOUNT p ON c.ZPARENTACCOUNT = p.Z_PK"""
            ).fetchall()
    except sqlite3.Error:
        return {}
    return {uuid: (name or user or "", user or "") for uuid, name, user in rows if uuid}


@dataclass(frozen=True)
class Account:
    id: str
    name: str          # display name, or the UUID when unknown
    username: str
    folders: int = 0
    total: int = 0
    unread: int = 0


@dataclass(frozen=True)
class Folder:
    rowid: int
    url: str
    account_id: str
    account: str
    name: str          # decoded folder path, e.g. "[Gmail]/Spam"
    total: int = 0
    unread: int = 0


@dataclass(frozen=True)
class Message:
    id: int
    subject: str
    sender: str
    sender_name: str
    date: datetime | None
    read: bool
    flagged: bool
    size: int
    folder: str
    account: str
    account_id: str
    conversation_id: int
    to: tuple[str, ...] = ()
    cc: tuple[str, ...] = ()
    attachments: tuple[str, ...] = ()
    body: str = ""

    @property
    def from_(self) -> str:
        return f"{self.sender_name} <{self.sender}>" if self.sender_name else self.sender


def to_json_dict(obj) -> dict:
    from dataclasses import asdict

    def jsonable(v):
        if isinstance(v, datetime):
            return v.isoformat()
        if isinstance(v, tuple):
            return list(v)
        return v

    return {k: jsonable(v) for k, v in asdict(obj).items()}


def _fmt_addr(address: str, comment: str) -> str:
    return f"{comment} <{address}>" if comment else address


_MESSAGE_SELECT = """
    SELECT m.ROWID, COALESCE(s.subject, ''), COALESCE(a.address, ''),
           COALESCE(a.comment, ''), m.date_received, m.read, m.flagged,
           m.size, m.conversation_id, mb.url
    FROM messages m
    JOIN mailboxes mb ON m.mailbox = mb.ROWID
    LEFT JOIN subjects s ON m.subject = s.ROWID
    LEFT JOIN addresses a ON m.sender = a.ROWID
"""


class Store:
    """One open handle on the Envelope Index plus the account-name map."""

    def __init__(self):
        self.root = mail_dir()
        path = self.root / "MailData" / "Envelope Index"
        if not path.exists():
            raise MailError(f"Mail database not found at {path}")
        try:
            self.db = _connect_ro(path)
            self.db.execute("SELECT 1 FROM mailboxes LIMIT 1")
        except sqlite3.Error as e:
            raise MailError(
                f"cannot read {path} ({e}) — the terminal likely needs Full Disk Access"
            )
        self.names = account_names()

    def account_label(self, uuid: str) -> str:
        name, _user = self.names.get(uuid, ("", ""))
        return name or uuid[:8]

    # ---------- mailboxes ----------

    def folders(self) -> list[Folder]:
        rows = self.db.execute(
            "SELECT ROWID, url, total_count, unread_count FROM mailboxes ORDER BY url"
        ).fetchall()
        out = []
        for rowid, url, total, unread in rows:
            m = _URL_RE.match(url)
            if not m:
                continue
            uuid, path = m.groups()
            out.append(
                Folder(
                    rowid=rowid,
                    url=url,
                    account_id=uuid,
                    account=self.account_label(uuid),
                    name=unquote(path),
                    total=total or 0,
                    unread=unread or 0,
                )
            )
        return out

    def accounts(self) -> list[Account]:
        by_id: dict[str, list[Folder]] = {}
        for f in self.folders():
            by_id.setdefault(f.account_id, []).append(f)
        out = []
        for uuid, fs in sorted(by_id.items(), key=lambda kv: self.account_label(kv[0]).lower()):
            name, user = self.names.get(uuid, ("", ""))
            out.append(
                Account(
                    id=uuid,
                    name=name or uuid,
                    username=user,
                    folders=len(fs),
                    total=sum(f.total for f in fs),
                    unread=sum(f.unread for f in fs),
                )
            )
        return out

    def resolve_account(self, ref: str) -> str:
        """Turn a user-supplied account reference (UUID prefix, name, or
        username substring) into an account UUID."""
        ids = {f.account_id for f in self.folders()}
        q = ref.lower()
        hits = {u for u in ids if u.lower().startswith(q)}
        if not hits:
            hits = {
                u for u in ids
                if q in self.names.get(u, ("", ""))[0].lower()
                or q in self.names.get(u, ("", ""))[1].lower()
            }
        if not hits:
            raise MailError(f"no account matching {ref!r}")
        if len(hits) > 1:
            labels = ", ".join(sorted(self.account_label(u) for u in hits))
            raise MailError(f"{ref!r} is ambiguous: {labels}")
        return hits.pop()

    def _mailbox_ids(self, account: str | None, folder: str | None) -> list[int] | None:
        """Mailbox ROWIDs matching the filters, or None for no filtering."""
        if not account and not folder:
            return None
        fs = self.folders()
        if account:
            uuid = self.resolve_account(account)
            fs = [f for f in fs if f.account_id == uuid]
        if folder:
            q = folder.lower()
            names = INBOX_NAMES if q == "inbox" else {q}
            exact = [
                f for f in fs
                if f.name.lower() in names or f.name.lower().split("/")[-1] in names
            ]
            fs = exact or [f for f in fs if q in f.name.lower()]
            if not fs:
                raise MailError(f"no folder matching {folder!r}")
        return [f.rowid for f in fs]

    # ---------- messages ----------

    def _messages(self, where: list[str], params: list, limit: int) -> list[Message]:
        sql = _MESSAGE_SELECT + " WHERE " + " AND ".join(where)
        sql += " ORDER BY m.date_received DESC LIMIT ?"
        rows = self.db.execute(sql, [*params, limit]).fetchall()
        return [self._row_to_message(r) for r in rows]

    def _row_to_message(self, r) -> Message:
        rowid, subject, addr, comment, ts, read, flagged, size, conv, url = r
        m = _URL_RE.match(url)
        uuid, path = m.groups() if m else ("", url)
        return Message(
            id=rowid,
            subject=subject,
            sender=addr,
            sender_name=comment,
            date=datetime.fromtimestamp(ts) if ts else None,
            read=bool(read),
            flagged=bool(flagged),
            size=size or 0,
            folder=unquote(path),
            account=self.account_label(uuid) if uuid else "",
            account_id=uuid,
            conversation_id=conv,
        )

    def search(
        self,
        query: str | None = None,
        *,
        folder: str | None = None,
        account: str | None = None,
        sender: str | None = None,
        since: str | None = None,
        unread: bool = False,
        flagged: bool = False,
        limit: int = 25,
    ) -> list[Message]:
        where, params = ["m.deleted = 0"], []
        if query:
            where.append("(s.subject LIKE ? OR a.address LIKE ? OR a.comment LIKE ?)")
            params += [f"%{query}%"] * 3
        if sender:
            where.append("(a.address LIKE ? OR a.comment LIKE ?)")
            params += [f"%{sender}%"] * 2
        if since:
            where.append("m.date_received >= ?")
            params.append(_parse_since(since))
        if unread:
            where.append("m.read = 0")
        if flagged:
            where.append("m.flagged = 1")
        boxes = self._mailbox_ids(account, folder)
        if boxes is not None:
            if not boxes:
                return []
            where.append(f"m.mailbox IN ({','.join('?' * len(boxes))})")
            params += boxes
        return self._messages(where, params, limit)

    def message(self, rowid: int) -> Message:
        row = self.db.execute(_MESSAGE_SELECT + " WHERE m.ROWID = ?", [rowid]).fetchone()
        if row is None:
            raise MailError(f"no message with id {rowid}")
        msg = self._row_to_message(row)
        recips = self.db.execute(
            """SELECT a.address, COALESCE(a.comment, ''), r.type
               FROM recipients r JOIN addresses a ON r.address = a.ROWID
               WHERE r.message = ? ORDER BY r.position""",
            [rowid],
        ).fetchall()
        atts = self.db.execute(
            "SELECT name FROM attachments WHERE message = ? AND name IS NOT NULL", [rowid]
        ).fetchall()
        from dataclasses import replace

        return replace(
            msg,
            to=tuple(_fmt_addr(a, c) for a, c, t in recips if t == 0),
            cc=tuple(_fmt_addr(a, c) for a, c, t in recips if t == 1),
            attachments=tuple(n for (n,) in atts),
        )

    def thread(self, rowid: int) -> list[Message]:
        """All messages in the given message's conversation, oldest first."""
        anchor = self.message(rowid)
        rows = self.db.execute(
            _MESSAGE_SELECT + " WHERE m.conversation_id = ? AND m.deleted = 0"
            " ORDER BY m.date_received ASC",
            [anchor.conversation_id],
        ).fetchall()
        return [self._row_to_message(r) for r in rows]

    # ---------- bodies ----------

    def mailbox_path(self, msg: Message) -> Path | None:
        """The on-disk .mbox directory for a message's mailbox.

        Each folder-path segment becomes `<segment>.mbox`; older layouts
        used the whole decoded path with a single .mbox suffix.
        """
        if not msg.account_id:
            return None
        base = self.root / msg.account_id
        nested = base.joinpath(*(f"{seg}.mbox" for seg in msg.folder.split("/")))
        if nested.is_dir():
            return nested
        flat = base / f"{msg.folder}.mbox"
        return flat if flat.is_dir() else None


def _parse_since(raw: str) -> float:
    try:
        return datetime.fromisoformat(raw).timestamp()
    except ValueError:
        raise MailError(f"--since takes an ISO date, not {raw!r}")
