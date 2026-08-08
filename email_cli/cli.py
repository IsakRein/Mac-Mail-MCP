"""`email` CLI — read Apple Mail.app email from the terminal.

Everything is read straight from Mail's local SQLite store and .emlx
files, so it works offline and needs no credentials — only a Mail.app
that has synced the accounts, and Full Disk Access for the terminal.
Read-only by design: it cannot send, move, or delete anything.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from datetime import date
from types import SimpleNamespace
from typing import Callable

from rich import box
from rich.console import Console
from rich.table import Table
from rich.text import Text

from email_cli import emlx
from email_cli.db import INBOX_NAMES, MailError, Message, Store, to_json_dict

console = Console()
err_console = Console(stderr=True)


class CliError(Exception):
    """A user-facing argument-parsing error."""


@dataclass
class Flag:
    name: str            # long option, e.g. "--folder"
    takes_value: bool
    help: str
    metavar: str = ""    # placeholder shown in help for value flags

    @property
    def attr(self) -> str:
        return self.name.lstrip("-").replace("-", "_")


@dataclass
class Arg:
    name: str            # positional name, e.g. "id"
    help: str
    required: bool = True
    variadic: bool = False   # captures all remaining positionals, space-joined


@dataclass
class Command:
    name: str
    help: str
    func: Callable[[SimpleNamespace], int]
    flags: list[Flag] = field(default_factory=list)
    args: list[Arg] = field(default_factory=list)


def open_store() -> Store:
    try:
        return Store()
    except MailError as e:
        err_console.print(f"[bold red]error:[/bold red] {e}")
        raise SystemExit(1)


# ---------- rendering ----------


def _date_cell(m: Message) -> Text:
    if m.date is None:
        return Text("")
    if m.date.date() == date.today():
        return Text(m.date.strftime("%H:%M"), style="yellow")
    return Text(m.date.strftime("%Y-%m-%d"))


def _mark_cell(m: Message) -> Text:
    mark = Text()
    mark.append(" " if m.read else "*", style="bold cyan")
    if m.flagged:
        mark.append("!", style="bold red")
    return mark


def messages_table(messages: list[Message]) -> Table:
    table = Table(box=box.SIMPLE_HEAD, header_style="bold", expand=False)
    table.add_column("ID", style="dim", no_wrap=True)
    table.add_column("", no_wrap=True)
    table.add_column("Date", no_wrap=True)
    table.add_column("From", max_width=30, no_wrap=True)
    table.add_column("Subject", max_width=60)
    table.add_column("Where", style="dim", no_wrap=True)
    for m in messages:
        where = m.account
        if m.folder.split("/")[-1].lower() not in INBOX_NAMES:
            where += f"/{m.folder.split('/')[-1]}"
        table.add_row(
            str(m.id),
            _mark_cell(m),
            _date_cell(m),
            Text(m.sender_name or m.sender, style="" if m.read else "bold"),
            Text(m.subject or "(no subject)", style="" if m.read else "bold"),
            where,
        )
    return table


def _print_messages(messages: list[Message], args: SimpleNamespace) -> int:
    if getattr(args, "json", False):
        print(json.dumps([to_json_dict(m) for m in messages], indent=2))
        return 0
    if not messages:
        console.print("[dim]no messages[/dim]")
        return 0
    console.print(messages_table(messages))
    return 0


def _limit(args: SimpleNamespace, default: int = 25) -> int:
    if not getattr(args, "limit", None):
        return default
    try:
        return int(args.limit)
    except ValueError:
        raise CliError("--limit must be an integer")


# ---------- commands ----------


def cmd_status(args: SimpleNamespace) -> int:
    store = open_store()
    accounts = store.accounts()
    console.print(f"[bold]source:[/bold]   [dim]{store.root}[/dim]")
    console.print(
        f"[bold]accounts:[/bold] {len(accounts)}   "
        f"[bold]messages:[/bold] {sum(a.total for a in accounts)}"
    )
    unread = sum(a.unread for a in accounts)
    line = Text("unread:   ", style="bold")
    line.append(str(unread), style="yellow" if unread else "dim")
    console.print(line)
    for a in accounts:
        if a.unread:
            console.print(f"  [cyan]{a.name}[/cyan]  [yellow]{a.unread}[/yellow] unread")
    return 0


def cmd_accounts(args: SimpleNamespace) -> int:
    store = open_store()
    accounts = store.accounts()
    if args.json:
        print(json.dumps([to_json_dict(a) for a in accounts], indent=2))
        return 0
    table = Table(box=box.SIMPLE_HEAD, header_style="bold", expand=False)
    table.add_column("ID", style="dim", no_wrap=True)
    table.add_column("Account")
    table.add_column("User", style="dim")
    table.add_column("Folders", justify="right", no_wrap=True)
    table.add_column("Messages", justify="right", no_wrap=True)
    table.add_column("Unread", justify="right", no_wrap=True)
    for a in accounts:
        table.add_row(
            a.id[:8],
            Text(a.name, style="cyan"),
            a.username,
            str(a.folders),
            str(a.total) if a.total else "",
            Text(str(a.unread), style="yellow") if a.unread else Text(""),
        )
    console.print(table)
    return 0


def cmd_folders(args: SimpleNamespace) -> int:
    store = open_store()
    try:
        folders = store.folders()
        if args.account:
            uuid = store.resolve_account(args.account)
            folders = [f for f in folders if f.account_id == uuid]
    except MailError as e:
        err_console.print(f"[bold red]error:[/bold red] {e}")
        return 1
    if not args.all:
        folders = [f for f in folders if f.total]
    folders.sort(key=lambda f: (f.account.lower(), f.name.lower()))
    if args.json:
        print(json.dumps([to_json_dict(f) for f in folders], indent=2))
        return 0
    if not folders:
        console.print("[dim]no folders[/dim]")
        return 0
    table = Table(box=box.SIMPLE_HEAD, header_style="bold", expand=False)
    table.add_column("Account", style="cyan", no_wrap=True)
    table.add_column("Folder")
    table.add_column("Messages", justify="right", no_wrap=True)
    table.add_column("Unread", justify="right", no_wrap=True)
    for f in folders:
        table.add_row(
            f.account,
            f.name,
            str(f.total) if f.total else "",
            Text(str(f.unread), style="yellow") if f.unread else Text(""),
        )
    console.print(table)
    return 0


def cmd_inbox(args: SimpleNamespace) -> int:
    store = open_store()
    try:
        messages = store.search(
            folder="inbox",
            account=args.account,
            unread=args.unread,
            limit=_limit(args),
        )
    except MailError as e:
        err_console.print(f"[bold red]error:[/bold red] {e}")
        return 1
    return _print_messages(messages, args)


def cmd_unread(args: SimpleNamespace) -> int:
    store = open_store()
    try:
        messages = store.search(
            account=args.account, folder=args.folder, unread=True, limit=_limit(args)
        )
    except MailError as e:
        err_console.print(f"[bold red]error:[/bold red] {e}")
        return 1
    return _print_messages(messages, args)


def cmd_search(args: SimpleNamespace) -> int:
    store = open_store()
    try:
        messages = store.search(
            args.query,
            folder=args.folder,
            account=args.account,
            sender=getattr(args, "from"),
            since=args.since,
            unread=args.unread,
            flagged=args.flagged,
            limit=_limit(args),
        )
    except MailError as e:
        err_console.print(f"[bold red]error:[/bold red] {e}")
        return 1
    return _print_messages(messages, args)


def _detail(label: str, value) -> None:
    if value:
        console.print(f"  [bold]{label}:[/bold] {value}")


def _message_id(raw: str) -> int:
    try:
        return int(raw)
    except ValueError:
        raise CliError(f"<id> is the numeric id the tables show, not {raw!r}")


def cmd_show(args: SimpleNamespace) -> int:
    store = open_store()
    try:
        m = store.message(_message_id(args.id))
    except MailError as e:
        err_console.print(f"[bold red]error:[/bold red] {e}")
        return 1
    body = emlx.read_body(store.mailbox_path(m), m.id)
    if args.json:
        d = to_json_dict(m)
        d["body"] = body
        print(json.dumps(d, indent=2))
        return 0
    console.print(Text(m.subject or "(no subject)", style="bold"))
    console.print(f"  [dim]{m.id}  ·  {m.account}/{m.folder}[/dim]")
    _detail("from", m.from_)
    _detail("to", ", ".join(m.to))
    _detail("cc", ", ".join(m.cc))
    _detail("date", m.date.strftime("%Y-%m-%d %H:%M") if m.date else None)
    _detail("status", ("read" if m.read else "unread") + (", flagged" if m.flagged else ""))
    _detail("attachments", ", ".join(m.attachments))
    console.print()
    if body:
        for line in body.splitlines():
            console.print(f"  {line}", markup=False, highlight=False)
    else:
        console.print("  [dim](body not downloaded locally)[/dim]")
    return 0


def cmd_thread(args: SimpleNamespace) -> int:
    store = open_store()
    try:
        messages = store.thread(_message_id(args.id))
    except MailError as e:
        err_console.print(f"[bold red]error:[/bold red] {e}")
        return 1
    return _print_messages(messages, args)


def cmd_doctor(args: SimpleNamespace) -> int:
    ok = True

    def check(label: str, passed: bool, detail: str = "") -> None:
        nonlocal ok
        mark = "[green]  ok[/green]" if passed else "[red]fail[/red]"
        console.print(f"  {mark}  {label}")
        if detail:
            console.print(f"        [dim]{detail}[/dim]")
        if not passed:
            ok = False

    console.print("[bold]email doctor[/bold]")
    console.print()
    try:
        store = Store()
    except MailError as e:
        check("Mail store readable", False, str(e))
        console.print()
        console.print("status: [bold red]FAIL[/bold red]")
        return 1
    folders = store.folders()
    total = sum(f.total for f in folders)
    check("Mail store readable", True, f"{store.root} — {len(folders)} mailboxes, {total} messages")
    check(
        "account names resolved",
        bool(store.names),
        "" if store.names else "could not read ~/Library/Accounts/Accounts4.sqlite; showing UUIDs",
    )
    sample = store.search(limit=10)
    if sample:
        hit = next(
            (m for m in sample if emlx.read_body(store.mailbox_path(m), m.id)), None
        )
        check(
            "message bodies readable",
            hit is not None,
            f"message {hit.id}" if hit else
            f"none of the {len(sample)} newest messages have a local body "
            "— not downloaded, or no Full Disk Access",
        )
    else:
        check("message bodies readable", False, "no messages found to sample")
    console.print()
    console.print(
        "status: [bold green]OK[/bold green]" if ok
        else "status: [bold red]FAIL[/bold red] [dim]— fix the items above[/dim]"
    )
    return 0 if ok else 1


# ---------- command registry + hand-rolled parsing (rich-rendered help) ----------

PROG = "email"
DESCRIPTION = (
    "Read Apple Mail.app email from the terminal — straight from the local "
    "store, offline, read-only, no credentials."
)

_JSON = Flag("--json", False, "emit JSON (uncolored)")
_ACCOUNT = Flag("--account", True, "filter by account (name, user, or id prefix)", "REF")
_LIMIT = Flag("--limit", True, "max rows (default 25)", "N")

COMMANDS: list[Command] = [
    Command("status", "Summary of accounts and unread mail", cmd_status),
    Command(
        "inbox",
        "List inbox messages",
        cmd_inbox,
        [_ACCOUNT, Flag("--unread", False, "unread only"), _LIMIT, _JSON],
    ),
    Command(
        "unread",
        "List unread messages everywhere",
        cmd_unread,
        [_ACCOUNT, Flag("--folder", True, "filter by folder name", "NAME"), _LIMIT, _JSON],
    ),
    Command(
        "search",
        "Search messages by subject/sender",
        cmd_search,
        [
            Flag("--folder", True, "filter by folder name", "NAME"),
            _ACCOUNT,
            Flag("--from", True, "sender address/name substring", "WHO"),
            Flag("--since", True, "only after this date (yyyy-mm-dd)", "DATE"),
            Flag("--unread", False, "unread only"),
            Flag("--flagged", False, "flagged only"),
            _LIMIT,
            _JSON,
        ],
        [Arg("query", "subject/sender substring", required=False, variadic=True)],
    ),
    Command(
        "show",
        "Show a message in full, with its body",
        cmd_show,
        [_JSON],
        [Arg("id", "message id (from the tables)")],
    ),
    Command(
        "thread",
        "List every message in a message's conversation",
        cmd_thread,
        [_JSON],
        [Arg("id", "id of any message in the thread")],
    ),
    Command("accounts", "List Mail.app accounts", cmd_accounts, [_JSON]),
    Command(
        "folders",
        "List folders with message counts",
        cmd_folders,
        [_ACCOUNT, Flag("--all", False, "include empty folders"), _JSON],
    ),
    Command("doctor", "Verify the Mail store is readable end to end", cmd_doctor),
]
COMMANDS_BY_NAME = {c.name: c for c in COMMANDS}


def _flag_label(f: Flag) -> str:
    return f"{f.name} {f.metavar}".strip() if f.takes_value else f.name


def _arg_token(a: Arg) -> str:
    """Plain label, e.g. '<id>' or '[query…]'. Not markup-safe — wrap in
    Text (table cells) or escape the leading bracket (markup strings)."""
    tok = f"{a.name}…" if a.variadic else a.name
    return f"<{tok}>" if a.required else f"[{tok}]"


def _usage(cmd: Command) -> str:
    # markup-string context: escape the leading '[' of optional tokens
    parts = [_arg_token(a).replace("[", "\\[") for a in cmd.args]
    parts += [f"\\[{_flag_label(f)}]" for f in cmd.flags]
    return " ".join(parts)


def print_help() -> None:
    console.print(f"[bold]{PROG}[/bold] — Apple Mail, from the terminal")
    console.print(f"[dim]{DESCRIPTION}[/dim]")
    console.print()
    console.print(f"[bold]Usage:[/bold] {PROG} [cyan]<command>[/cyan] [dim]\\[args/options][/dim]")
    console.print()
    table = Table(box=box.SIMPLE, show_header=False, expand=False, pad_edge=False)
    table.add_column("Command", style="bold cyan", no_wrap=True)
    table.add_column("Description")
    for c in COMMANDS:
        table.add_row(c.name, c.help)
    console.print(table)
    console.print(f"Run [bold]{PROG} <command> -h[/bold] for command options.")


def print_command_help(cmd: Command) -> None:
    console.print(
        f"[bold]Usage:[/bold] {PROG} [cyan]{cmd.name}[/cyan] [dim]{_usage(cmd)}[/dim]".rstrip()
    )
    console.print(f"[dim]{cmd.help}[/dim]")
    rows = [(_arg_token(a), a.help) for a in cmd.args]
    rows += [(_flag_label(f), f.help) for f in cmd.flags]
    if rows:
        console.print()
        table = Table(box=box.SIMPLE, show_header=False, expand=False, pad_edge=False)
        table.add_column("", style="bold", no_wrap=True)
        table.add_column("Help")
        for label, helptext in rows:
            table.add_row(Text(label), helptext)  # Text: no markup parsing on labels
        console.print(table)


def parse_command(cmd: Command, argv: list[str]) -> SimpleNamespace:
    """Parse a command's positionals + flags into a namespace. Raises CliError
    on bad input; raises SystemExit(0) after printing help for -h/--help."""
    ns = SimpleNamespace(**{f.attr: (None if f.takes_value else False) for f in cmd.flags})
    for a in cmd.args:
        setattr(ns, a.name, None)
    by_name = {f.name: f for f in cmd.flags}
    positionals: list[str] = []
    i = 0
    while i < len(argv):
        tok = argv[i]
        if tok in ("-h", "--help"):
            print_command_help(cmd)
            raise SystemExit(0)
        if tok.startswith("--"):
            name, eq, inline = tok.partition("=")
            f = by_name.get(name)
            if f is None:
                raise CliError(f"unknown option {tok!r} for `{cmd.name}`")
            if f.takes_value:
                if eq:
                    value = inline
                else:
                    i += 1
                    if i >= len(argv):
                        raise CliError(f"{name} requires a value")
                    value = argv[i]
                setattr(ns, f.attr, value)
            else:
                if eq:
                    raise CliError(f"{name} takes no value")
                setattr(ns, f.attr, True)
        else:
            positionals.append(tok)
        i += 1
    # map positionals onto declared args (last arg may be variadic)
    for idx, a in enumerate(cmd.args):
        if a.variadic:
            rest = positionals[idx:]
            setattr(ns, a.name, " ".join(rest) if rest else None)
            break
        if idx < len(positionals):
            setattr(ns, a.name, positionals[idx])
    else:
        extra = positionals[len(cmd.args):]
        if extra:
            raise CliError(f"unexpected argument {extra[0]!r} for `{cmd.name}`")
    for a in cmd.args:
        if a.required and not getattr(ns, a.name):
            raise CliError(f"missing argument <{a.name}> for `{cmd.name}`")
    return ns


def main() -> int:
    argv = sys.argv[1:]
    if not argv or argv[0] in ("-h", "--help", "help"):
        print_help()
        return 0
    name, rest = argv[0], argv[1:]
    cmd = COMMANDS_BY_NAME.get(name)
    if cmd is None:
        err_console.print(f"[bold red]error:[/bold red] unknown command {name!r}")
        console.print()
        print_help()
        return 2
    try:
        ns = parse_command(cmd, rest)
        return cmd.func(ns)
    except CliError as e:
        err_console.print(f"[bold red]error:[/bold red] {e}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
