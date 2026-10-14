"""What a chat's questions returned, kept beside its log.

**Why it exists** *(2026-10-13, the tool revamp, `sandbox/limits/REVAMP_SPEC.md` §D)*.
A `query_data` answer is cut to fit the conversation: twenty rows, fifty
columns. Until this module the rest of the result was nowhere. Seeing row 21
meant running the query again, which on a warehouse is a second bill for the
same answer; a finding could copy only what the agent had been shown; and
`review_queries` resent every logged answer word for word, which a chat of
fourteen questions took past `tools.RESULT_BUDGET` (30,741 characters, refused).

So each data question's **whole result is kept**, as a Parquet file under the
chat's own folder (`runlog.companion`), with a short **handle** the answer
names. The next page comes from the kept copy rather than from a second run,
SQL reads it as a table by its handle (`agent/reserved.py`, registered by
`agent/handlers.py`), and a finding copies its rows from it.

Four rules, the user's (2026-10-13):

1. **Every backend, warehouses included, up to a cap.** Over :data:`KEEP_ROWS`,
   :data:`KEEP_CELLS` or :data:`KEEP_BYTES` nothing is kept and the answer says
   so: asking again runs the query again. The cap is checked on the count
   before anything is fetched, so a result too big to keep costs nothing extra.
2. **A kept result belongs to one chat and goes with it** (`runlog.delete`).
   Another chat reaches it through a finding that cites it, or by curating
   that chat; never through a pool of every query.
3. **Written through `core.io`**, as every table portia writes is.
4. **Never committed.** `.portia/` is the project's own business, so each
   chat's folder carries a ``.gitignore`` that ignores the folder itself.

**The handle is not the question's number, on purpose.** `review_queries`
numbers a chat's questions by their position in the log, and findings cite
them that way (`findings.QUERY_REF`). That position is fixed only once the
call is in the log, and the log is written at the edges, after the fact and in
whatever order a harness that runs calls side by side delivers them, so the
handler that answers cannot know it. A handle shaped like ``q7`` that was
usually, but not always, question 7 would invite exactly the wrong citation.
So a handle is a few letters with no digit in it (:data:`HANDLE`), the answer
that kept it says it, the log holds that answer, and `findings.review` reads
the handle back beside the number. The two never have to be inferred from one
another.

**Which chat a result belongs to** is the one this process is logging to
(:func:`attach`): the window sets it for each exchange, `cli/chat` for its run
and `cli/serve` once for its session. When nothing has, it is the newest chat,
which is the rule `review_queries` always had.

Nothing here ranks, reads a value, or decides what a result means: it writes a
table, finds it again and reads its rows back in order.
"""

from __future__ import annotations

import json
import os
import re
import secrets
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from portia import catalog, runlog
from portia.core import backend, cancel
from portia.core import io as core_io
from portia.core.table import Table, quote_ident, subquery

#: The most rows a kept result may have. **A placeholder**, like the two below:
#: chosen so that what is kept stays a few megabytes and a warehouse result is
#: fetched in seconds, not tuned against a session yet.
KEEP_ROWS = 100_000

#: The most values (rows times columns) a kept result may have. The bound that
#: matters on a wide table: a ``SELECT *`` of 17,544 rows of 1,579 columns is
#: 27 million values, and fetching those down from a warehouse to keep them
#: would cost more than asking again ever could.
KEEP_CELLS = 1_000_000

#: The largest file a kept result may be, in bytes, checked once it is written.
#: Long text values can make a result that is small in values large on disk.
KEEP_BYTES = 20_000_000

#: Why a result was not kept. Codes, worded by `agent/handlers.py` from
#: `prompts/notices/`, because this module sits below the layer that loads prompts.
NO_CHAT = "no_chat"
TOO_MANY_ROWS = "too_many_rows"
TOO_MANY_VALUES = "too_many_values"
TOO_LARGE = "too_large"
NOT_WRITTEN = "not_written"

#: What a handle starts with. In portia's own prefix, as `portia_columns` and
#: `portia_tables` are: a reserved name wins over a source of the same name
#: (`agent/reserved.py`), so it has to be a name nobody's data uses.
HANDLE_PREFIX = "portia_result_"

#: The letters a handle is made of: consonants, so a handle is never a word, and
#: no digits, so it is never mistaken for a question's number (module docstring).
HANDLE_LETTERS = "bcdfghjkmnpqrstvwxz"

#: How many letters follow the prefix: 19⁴ = 130,321 handles, drawn at random
#: and checked against the chat's own folder, which is the only place two may
#: not meet. Two chats may hold the same handle; each reads its own first.
HANDLE_LENGTH = 4

#: A handle, as the agent may type it: any case, since an unquoted SQL name has none.
HANDLE = re.compile(rf"{HANDLE_PREFIX}[{HANDLE_LETTERS}]{{{HANDLE_LENGTH}}}", flags=re.IGNORECASE)

#: The two files a kept result is: the rows, and what they answered.
DATA_SUFFIX = ".parquet"
META_SUFFIX = ".json"

#: What a chat's folder ignores: everything in it, itself included.
IGNORE_FILE = ".gitignore"
IGNORE_ALL = "*\n"


@dataclass(frozen=True)
class Kept:
    """One kept result: where its rows are and what produced them."""

    handle: str
    #: The log of the chat it belongs to.
    log: Path
    tool: str
    question: str
    sql: str
    inputs: list[str]
    n_rows: int
    columns: list[str]
    #: When it was kept, ISO, to the second.
    at: str

    @property
    def n_columns(self) -> int:
        return len(self.columns)

    @property
    def path(self) -> Path:
        return runlog.companion(self.log) / f"{self.handle}{DATA_SUFFIX}"


@dataclass(frozen=True)
class NotKept:
    """Why a result was not kept, as a code, and the number that decided it."""

    reason: str
    #: The size that was over its cap, or the first line of what went wrong.
    detail: Any = None


# --- which chat -------------------------------------------------------------

_attached: Path | None = None


def attach(log: str | Path | None) -> Path | None:
    """Make ``log`` the chat this process's data questions belong to. Returns the previous one.

    **A module slot, as `tools._stop` is, and for its reason**: the harness runs
    a tool body on a task of its own, so a context variable set where the
    exchange starts never reaches it. The window runs one exchange at a time
    and sets this around each; `cli/serve` holds one session per process and
    sets it once. ``None`` gives the default back.
    """
    global _attached
    previous, _attached = _attached, (Path(log) if log is not None else None)
    return previous


def log_for(portia_dir: str | Path = catalog.DEFAULT_DIR, chat: str | None = None) -> Path | None:
    """The log of the chat a call belongs to: ``chat`` by name, else the attached one, else the newest.

    The attached log counts only when it is this project's: a slot is one per
    process, and a process that opened two projects must not keep one's
    results in the other's chat.
    """
    if chat:
        return runlog.find(chat, portia_dir)
    if _attached is not None and _inside(_attached, portia_dir):
        return _attached
    logs = runlog.logs_in(portia_dir, kind=runlog.CHAT)
    return logs[0] if logs else None


def _inside(log: Path, portia_dir: str | Path) -> bool:
    try:
        log.resolve().relative_to(Path(portia_dir).resolve())
    except ValueError:
        return False
    return True


# --- keeping ----------------------------------------------------------------


def keep(
    table: Table,
    *,
    log: Path | None,
    tool: str,
    question: str,
    sql: str,
    inputs: list[str],
    n_rows: int,
    columns: list[str],
) -> Kept | NotKept:
    """Keep ``table``, the whole result of one question, beside ``log``. Never raises for size.

    ``n_rows`` and ``columns`` are what the caller already measured, so the
    cap is checked before a single row is fetched: a result over it is
    answered from the query as before and only the answer says it was not
    kept. A result that cannot be written (a value DuckDB cannot store, a full
    disk) is not kept either, and says why; the answer to the question it
    belongs to has been measured and does not fail for it.
    """
    if log is None:
        return NotKept(NO_CHAT)
    if n_rows > KEEP_ROWS:
        return NotKept(TOO_MANY_ROWS, n_rows)
    if n_rows * max(1, len(columns)) > KEEP_CELLS:
        return NotKept(TOO_MANY_VALUES, n_rows * len(columns))
    folder = runlog.companion(log)
    data: Path | None = None
    try:
        folder.mkdir(parents=True, exist_ok=True)
        ignore = folder / IGNORE_FILE
        if not ignore.exists():
            ignore.write_text(IGNORE_ALL, encoding="utf-8")
        handle = _new_handle(folder)
        data = folder / f"{handle}{DATA_SUFFIX}"
        _write(table, data, n_rows=n_rows)
        size = data.stat().st_size
        if size > KEEP_BYTES:
            data.unlink(missing_ok=True)
            return NotKept(TOO_LARGE, size)
        kept = Kept(
            handle=handle,
            log=Path(log),
            tool=tool,
            question=question,
            sql=sql,
            inputs=list(inputs),
            n_rows=n_rows,
            columns=list(columns),
            at=datetime.now().astimezone().isoformat(timespec="seconds"),
        )
        meta = folder / f"{handle}{META_SUFFIX}"
        record = {k: v for k, v in asdict(kept).items() if k != "log"}
        partial = meta.with_suffix(".partial")
        partial.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
        os.replace(partial, meta)
    except cancel.Cancelled:
        if data is not None:
            data.unlink(missing_ok=True)
        raise
    except Exception as exc:  # noqa: BLE001 - said in the answer, which stands without it
        if data is not None:
            data.unlink(missing_ok=True)
        return NotKept(NOT_WRITTEN, str(exc).strip().splitlines()[0] if str(exc).strip() else "")
    return kept


def _new_handle(folder: Path) -> str:
    """A handle no result in this chat's folder has."""
    while True:
        letters = "".join(secrets.choice(HANDLE_LETTERS) for _ in range(HANDLE_LENGTH))
        handle = f"{HANDLE_PREFIX}{letters}"
        if (
            not (folder / f"{handle}{META_SUFFIX}").exists()
            and not (folder / f"{handle}{DATA_SUFFIX}").exists()
        ):
            return handle


def _write(table: Table, path: Path, *, n_rows: int) -> None:
    """``table``'s rows into a Parquet file at ``path``, through `core.io.write_table`.

    **Through a connection of its own, by way of `Table.head`**, for two
    reasons that are the same reason. A warehouse result cannot be written
    where it is (`write_table` refuses a remote table, and a file is the copy
    the user asked for here); and a local result may sit on a connection with
    no file access, which is what the SQL sandbox's tables and portia's own
    reserved tables are. So the rows come out once, through the capped exit,
    at most :data:`KEEP_CELLS` of them by the check in :func:`keep`, and go
    into a fresh local DuckDB that may write. A local result is put back into
    the types its query produced, which a trip through pandas would otherwise
    move (a ``DATE`` arrives as a timestamp): the repair `ops/sql.py` makes for
    the same crossing. A warehouse result keeps the types DuckDB reads off the
    values, which is what the copy can know.
    """
    frame = table.head(n_rows)
    types = {} if backend.is_remote(table.con) else table.dtypes
    con = backend.LOCAL.open()
    cancel.watch(con)
    try:
        local = Table.from_frame(frame, "kept", con)
        if types and list(types) == local.columns:
            select = ", ".join(
                f"CAST({quote_ident(column)} AS {kind}) AS {quote_ident(column)}"
                for column, kind in types.items()
            )
            local = Table(local.name, f"SELECT {select} FROM {subquery(local.query)}", con)
        core_io.write_table(local, path)
    finally:
        con.close()


# --- finding one again ------------------------------------------------------


def kept_in(log: Path | None) -> list[Kept]:
    """Every result kept for this chat, oldest first."""
    if log is None:
        return []
    folder = runlog.companion(log)
    if not folder.is_dir():
        return []
    found = []
    for meta in folder.glob(f"*{META_SUFFIX}"):
        kept = _read(log, meta)
        if kept is not None:
            found.append(kept)
    return sorted(found, key=lambda k: (k.at, k.handle))


def find(log: Path | None, handle: str) -> Kept | None:
    """The result ``handle`` names in this chat, if it was kept here and is still whole."""
    if log is None or not HANDLE.fullmatch(handle or ""):
        return None
    return _read(log, runlog.companion(log) / f"{handle.lower()}{META_SUFFIX}")


def same(log: Path | None, sql: str, inputs: list[str]) -> Kept | None:
    """The newest result this chat kept for the same SQL over the same inputs, if any.

    What a call asking for the next page reads instead of running the query
    again. Exact text: a query written differently is a different question,
    and running it is the right answer.
    """
    wanted = (sql.strip(), [str(name) for name in inputs])
    matches = [k for k in kept_in(log) if (k.sql.strip(), k.inputs) == wanted]
    return matches[-1] if matches else None


def _read(log: Path, meta: Path) -> Kept | None:
    """A kept result from its two files, or ``None`` when either is missing or unreadable."""
    try:
        record = json.loads(meta.read_text(encoding="utf-8"))
        kept = Kept(
            handle=str(record["handle"]),
            log=Path(log),
            tool=str(record.get("tool") or ""),
            question=str(record.get("question") or ""),
            sql=str(record.get("sql") or ""),
            inputs=[str(name) for name in record.get("inputs") or []],
            n_rows=int(record["n_rows"]),
            columns=[str(column) for column in record.get("columns") or []],
            at=str(record.get("at") or ""),
        )
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return kept if kept.path.is_file() else None


def table(kept: Kept) -> Table:
    """The kept rows as a `Table`, on a local connection of their own that may read the file.

    Its own, because the connection a reserved table is usually built on has
    no file access (`agent/reserved.connection`); the SQL sandbox copies this
    table in like any other input and the agent's query never sees the file.
    """
    con = backend.LOCAL.open()
    cancel.watch(con)
    return core_io.load_table(kept.path, con, name=kept.handle)
