# email-cli

Read **Apple Mail.app** email from the command line, straight from the
local store. No OAuth, no API keys, no scripting the app — Mail keeps all
envelope metadata in a SQLite database and the message bodies as .emlx
files, and this reads both directly. Works with every account Mail.app is
configured with (Gmail, Exchange, iCloud, IMAP), offline, and is
read-only by design: it cannot send, move, or delete anything.

Same shape as [`things`](https://github.com/IsakRein/Productivity.Things-Sync)
and [`tt`](https://github.com/IsakRein/Productivity.Timelines-Sync): a small
data layer, a rich-rendered CLI over the top.

```bash
email inbox
email search "invoice" --from stripe --since 2026-07-01
email show 39751
```

## Install

```bash
uv tool install "git+https://github.com/IsakRein/Productivity.Email-CLI"
```

For development:

```bash
uv sync --extra dev
uv run pytest
```

The terminal needs **Full Disk Access** (System Settings > Privacy &
Security) to read `~/Library/Mail`. `email doctor` tells you if it
doesn't have it.

## Commands

| Command | What |
|---|---|
| `status` | accounts and unread summary |
| `inbox [--account] [--unread]` | inbox messages across accounts |
| `unread [--account] [--folder]` | unread messages everywhere |
| `search <query> [--folder/--account/--from/--since/--unread/--flagged]` | subject/sender search |
| `show <id>` | one message in full, with its body |
| `thread <id>` | every message in a conversation |
| `accounts` | Mail.app accounts |
| `folders [--account] [--all]` | folders with message counts |
| `doctor` | verify the store is readable end to end |

Every read takes `--json`; lists take `--limit N` (default 25). Message
ids are Mail's own ROWIDs, shown in every table. `--account` accepts an
account name, username, or id prefix.

## How it works

- `~/Library/Mail/V*/MailData/Envelope Index` — SQLite with messages,
  subjects, addresses, mailboxes, recipients, attachments. Opened
  read-only.
- `~/Library/Mail/V*/<account>/<Folder>.mbox/.../Messages/<id>.emlx` —
  one file per downloaded message: a byte count, the raw RFC 822 message,
  then an Apple flags plist. Parsed with the stdlib `email` package,
  preferring text/plain and falling back to stripped HTML.
- `~/Library/Accounts/Accounts4.sqlite` — account display names
  ("Google", "Exchange"). Optional; UUIDs are shown when unreadable.

```
email_cli/
  db.py      — Envelope Index queries, account names, dataclasses
  emlx.py    — locate + parse .emlx bodies
  cli.py     — commands + rich rendering
```

## Limitations

- Read-only — no sending, moving, or deleting.
- Bodies exist only for messages Mail.app has downloaded locally.
- Mail.app must be (or have been) configured with the accounts.
