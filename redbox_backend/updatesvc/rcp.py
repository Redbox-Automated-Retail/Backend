"""Server side of Redbox's line protocol (Redbox.IPC.Framework, `rcp://` plain / `srcp://` TLS).

Framing, from software/src/Common/Redbox.IPC.Framework (ClientSession.cs, ClientCommand.cs, ServerSession.cs):
  * ASCII, CRLF-terminated lines. On connect the server writes `Welcome! <name>, Version <v>, <copyright>`.
  * The client writes one command line; the server answers with zero or more message lines and then a
    status line `203 Command Success. (Execution Time:<ms>)` or `545 Command failed. ...`.
  * Errors are message lines `|*[CODE] ERROR: description|details*|`.
  * `quit` is answered with `Goodbye!` and the connection is closed.
  * Command syntax: `<command> <form> key: value key: 'quoted value' ...`; quoted values escape `\\` and `\'`.
"""
import asyncio
import logging
import ssl
import time

log = logging.getLogger("rcp")

WELCOME = "Welcome! Redbox Update Service (self-hosted), Version 3.0.0.0, Copyright (c) Redbox self-hosted backend"


class CommandError(Exception):
    def __init__(self, code, description, details=""):
        super().__init__(description)
        self.code, self.description, self.details = code, description, details


def format_error(code, description, details="", warning=False):
    kind = "WARNING" if warning else "ERROR"
    text = f"|*[{code}] {kind}: {description}"
    if details:
        text += f"|{details}"
    return text.replace("\r", " ").replace("\n", " ") + "*|"


def tokenize(line):
    """Split a command line into (words, params). Words are the leading bare tokens (command, form)."""
    words, params = [], {}
    i, n = 0, len(line)
    pending_key = None
    while i < n:
        c = line[i]
        if c.isspace():
            i += 1
            continue
        if c in "'\"":
            quote = c
            i += 1
            buf = []
            while i < n and line[i] != quote:
                if line[i] == "\\" and i + 1 < n and line[i + 1] in ("\\", quote):
                    buf.append(line[i + 1])
                    i += 2
                    continue
                buf.append(line[i])
                i += 1
            i += 1  # closing quote
            token, quoted = "".join(buf), True
        else:
            j = i
            while j < n and not line[j].isspace() and line[j] not in "'\"":
                j += 1
                if pending_key is None and line[j - 1] == ":":
                    break  # `key:` may be glued to its value (`h:'...'`)
            token, quoted = line[i:j], False
            i = j
        if pending_key is not None:
            params[pending_key] = token
            pending_key = None
        elif not quoted and token.endswith(":") and len(token) > 1:
            pending_key = token[:-1].lower()
        else:
            if params:
                # A bare token after parameters: tolerate it as a flag.
                params[token.lower()] = ""
            else:
                words.append(token.lower())
    if pending_key is not None:
        params[pending_key] = ""
    return words, params


class Reply:
    def __init__(self):
        self.lines = []
        self.errors = []

    def message(self, line):
        self.lines.append(line)

    def error(self, code, description, details="", warning=False):
        self.errors.append(format_error(code, description, details, warning))


class RcpServer:
    """Accepts connections and passes each command to `handler(words, params, reply, peer)` (async)."""

    def __init__(self, handler, name="rcp"):
        self.handler = handler
        self.name = name

    async def serve(self, host, port, ssl_context=None):
        server = await asyncio.start_server(self._client, host, port, ssl=ssl_context, limit=64 * 1024 * 1024)
        log.info("%s listening on %s:%s (%s)", self.name, host, port, "TLS" if ssl_context else "plain")
        return server

    async def _client(self, reader, writer):
        peer = writer.get_extra_info("peername")
        log.info("connection from %s", peer)

        async def write_line(text):
            writer.write(text.encode("ascii", "replace") + b"\r\n")
            await writer.drain()

        try:
            await write_line(WELCOME)
            while True:
                raw = await reader.readline()
                if not raw:
                    break
                line = raw.decode("ascii", "replace").strip()
                if not line:
                    continue
                words, params = tokenize(line)
                if words[:1] == ["quit"]:
                    await write_line("Goodbye!")
                    break
                started = time.monotonic()
                reply = Reply()
                ok = True
                try:
                    ok = await self.handler(words, params, reply, peer) is not False
                except CommandError as e:
                    reply.error(e.code, e.description, e.details)
                    ok = False
                except Exception as e:  # never kill the connection on a handler bug
                    log.exception("command failed: %s", line[:200])
                    reply.error("S999", "An unhandled exception was raised by the update service.", str(e))
                    ok = False
                if reply.errors and ok is True and any("ERROR:" in x for x in reply.errors):
                    ok = False
                ms = int((time.monotonic() - started) * 1000)
                for msg in reply.lines + reply.errors:
                    await write_line(msg)
                await write_line(f"203 Command Success. (Execution Time:{ms})" if ok
                                 else f"545 Command failed. (Execution Time:{ms})")
                log.info("%s: %s -> %s (%d lines, %d ms)", peer, " ".join(words) or "?", "203" if ok else "545",
                         len(reply.lines), ms)
        except (ConnectionError, asyncio.IncompleteReadError, ssl.SSLError) as e:
            log.info("connection %s closed: %s", peer, e)
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass


def server_ssl_context(certfile, keyfile):
    ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.load_cert_chain(certfile, keyfile)
    return ctx
