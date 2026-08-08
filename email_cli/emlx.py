"""Locate and parse Mail.app .emlx files — where the message bodies live.

An .emlx file is a byte count on its first line, then the raw RFC 822
message, then an Apple plist of per-message flags. The RFC 822 part is
parsed with the stdlib email package (which handles multipart, base64,
quoted-printable, and charsets), preferring text/plain and falling back
to stripped text/html.
"""

from __future__ import annotations

import html
import re
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from pathlib import Path


def find_emlx(mailbox_path: Path, rowid: int) -> Path | None:
    """Find `<rowid>.emlx` (or `.partial.emlx`) under a .mbox directory.

    Layout: `<mbox>/<store-uuid>/Data/<digits>/Messages/<rowid>.emlx`,
    where <digits> are the digits of rowid // 1000, least significant
    first (39749 -> Data/9/3/Messages). Falls back to a full scan for
    layouts that don't match.
    """
    names = (f"{rowid}.emlx", f"{rowid}.partial.emlx")
    digits = []
    n = rowid // 1000
    while n:
        digits.append(str(n % 10))
        n //= 10
    try:
        stores = [p for p in mailbox_path.iterdir() if (p / "Data").is_dir()]
    except OSError:
        return None
    for store in stores:
        msgs = store.joinpath("Data", *digits) / "Messages"
        for name in names:
            candidate = msgs / name
            if candidate.is_file():
                return candidate
    for pattern in names:
        for found in mailbox_path.rglob(pattern):
            return found
    return None


def parse_emlx(data: bytes) -> EmailMessage:
    """Split off the emlx envelope and parse the RFC 822 message."""
    newline = data.index(b"\n")
    count = int(data[:newline].strip() or 0)
    start = newline + 1
    raw = data[start : start + count] if count else data[start:]
    return BytesParser(policy=policy.default).parsebytes(raw)


def extract_text(msg: EmailMessage) -> str:
    """Best-effort plain-text body: text/plain if present, else de-tagged
    text/html, else empty."""
    part = msg.get_body(preferencelist=("plain", "html"))
    if part is None:
        return ""
    try:
        content = part.get_content()
    except (LookupError, UnicodeDecodeError, KeyError):
        payload = part.get_payload(decode=True) or b""
        content = payload.decode("utf-8", errors="replace")
    content = content.replace("\r\n", "\n")
    if part.get_content_type() == "text/html":
        return strip_html(content)
    return content.strip()


def strip_html(text: str) -> str:
    text = re.sub(r"<(style|script)[^>]*>.*?</\1>", "", text, flags=re.I | re.S)
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.I)
    text = re.sub(r"</p>", "\n\n", text, flags=re.I)
    text = re.sub(r"</div>|</tr>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    text = html.unescape(text).replace("\xa0", " ")
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def read_body(mailbox_path: Path | None, rowid: int) -> str:
    """The plain-text body for a message, or '' if not downloaded locally."""
    if mailbox_path is None:
        return ""
    path = find_emlx(mailbox_path, rowid)
    if path is None:
        return ""
    try:
        return extract_text(parse_emlx(path.read_bytes()))
    except (OSError, ValueError):
        return ""
