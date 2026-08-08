from base64 import b64encode

from email_cli.emlx import extract_text, find_emlx, parse_emlx, strip_html


def emlx_bytes(message: bytes) -> bytes:
    plist = b'<?xml version="1.0"?><plist><dict/></plist>\n'
    return str(len(message)).encode() + b"\n" + message + plist


PLAIN = (
    b"From: Alice <alice@example.com>\r\n"
    b"To: bob@example.com\r\n"
    b"Subject: Hello\r\n"
    b"Content-Type: text/plain; charset=utf-8\r\n"
    b"\r\n"
    b"Hi Bob,\r\nSee you tomorrow.\r\n"
)


def test_parse_emlx_strips_envelope():
    msg = parse_emlx(emlx_bytes(PLAIN))
    assert msg["Subject"] == "Hello"
    assert extract_text(msg) == "Hi Bob,\nSee you tomorrow."


def test_multipart_prefers_plain():
    html_part = b64encode(b"<p>Hi <b>Bob</b></p>")
    message = (
        b"From: alice@example.com\r\n"
        b"Subject: Multi\r\n"
        b'Content-Type: multipart/alternative; boundary="XX"\r\n'
        b"\r\n"
        b"--XX\r\n"
        b"Content-Type: text/plain; charset=utf-8\r\n"
        b"Content-Transfer-Encoding: quoted-printable\r\n"
        b"\r\n"
        b"Hi Bob =E2=80=94 hello\r\n"
        b"--XX\r\n"
        b"Content-Type: text/html\r\n"
        b"Content-Transfer-Encoding: base64\r\n"
        b"\r\n" + html_part + b"\r\n"
        b"--XX--\r\n"
    )
    msg = parse_emlx(emlx_bytes(message))
    assert extract_text(msg) == "Hi Bob — hello"


def test_html_only_is_stripped():
    message = (
        b"From: a@example.com\r\n"
        b"Content-Type: text/html\r\n"
        b"\r\n"
        b"<html><style>p{}</style><p>One&nbsp;&amp;<br>Two</p></html>\r\n"
    )
    assert extract_text(parse_emlx(emlx_bytes(message))) == "One &\nTwo"


def test_strip_html_collapses_blank_lines():
    assert strip_html("<div>a</div><div></div><p></p><p>b</p>") == "a\n\nb"


def test_find_emlx_digit_scheme(tmp_path):
    # rowid 39749 -> digits of 39 reversed -> Data/9/3/Messages
    target = tmp_path / "store-uuid" / "Data" / "9" / "3" / "Messages"
    target.mkdir(parents=True)
    (target / "39749.partial.emlx").write_bytes(b"0\n")
    assert find_emlx(tmp_path, 39749) == target / "39749.partial.emlx"
    assert find_emlx(tmp_path, 12) is None


def test_find_emlx_small_rowid_no_subdirs(tmp_path):
    target = tmp_path / "store-uuid" / "Data" / "Messages"
    target.mkdir(parents=True)
    (target / "130.emlx").write_bytes(b"0\n")
    assert find_emlx(tmp_path, 130) == target / "130.emlx"
