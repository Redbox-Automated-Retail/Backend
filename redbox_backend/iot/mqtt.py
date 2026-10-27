"""A small MQTT 3.1.1 broker standing in for AWS IoT Core.

Enough of the protocol for MQTTnet 3.1 clients: CONNECT/CONNACK, PUBLISH QoS 0/1 (QoS 2 acknowledged and
delivered once), SUBSCRIBE/SUBACK, UNSUBSCRIBE, PINGREQ, DISCONNECT, keep-alive. Sessions are always clean
(the Update Client connects with clean session); retained messages are not kept (it never sets retain).

AWS IoT behaviour that matters to the kiosk:
  * mutual TLS: the client certificate must be signed by our CA, not revoked, and its CN must equal the
    MQTT client id (the kiosk id) - like an AWS IoT policy restricting client ids to the thing name;
  * a second connection with the same client id disconnects the first;
  * Basic Ingest: publishes to `$aws/rules/<rule>` go to the rule handler only, never to subscribers;
  * wildcards do not match topics starting with `$`.
"""
import asyncio
import logging
import ssl
import struct

log = logging.getLogger("mqtt")

CONNECT, CONNACK, PUBLISH, PUBACK, PUBREC, PUBREL, PUBCOMP, SUBSCRIBE, SUBACK, UNSUBSCRIBE, UNSUBACK, PINGREQ, \
    PINGRESP, DISCONNECT = range(1, 15)


def _encode_len(n):
    out = bytearray()
    while True:
        b = n % 128
        n //= 128
        out.append(b | (0x80 if n else 0))
        if not n:
            return bytes(out)


def _str(s):
    b = s.encode("utf-8") if isinstance(s, str) else s
    return struct.pack("!H", len(b)) + b


def topic_matches(filter_, topic):
    if topic.startswith("$") and filter_[:1] in ("+", "#"):
        return False
    f, t = filter_.split("/"), topic.split("/")
    for i, part in enumerate(f):
        if part == "#":
            return True
        if i >= len(t):
            return False
        if part != "+" and part != t[i]:
            return False
    return len(f) == len(t)


class Client:
    def __init__(self, broker, reader, writer):
        self.broker, self.reader, self.writer = broker, reader, writer
        self.client_id = None
        self.subscriptions = {}
        self.packet_id = 0
        self.peer = writer.get_extra_info("peername")
        self.cert_cn = None
        self.alive = True
        self.lock = asyncio.Lock()

    def next_id(self):
        self.packet_id = self.packet_id % 65535 + 1
        return self.packet_id

    async def send(self, ptype, flags, body):
        async with self.lock:
            self.writer.write(bytes([(ptype << 4) | flags]) + _encode_len(len(body)) + body)
            await self.writer.drain()

    async def publish(self, topic, payload: bytes, qos=1):
        qos = min(qos, 1)
        body = _str(topic)
        if qos:
            body += struct.pack("!H", self.next_id())
        await self.send(PUBLISH, qos << 1, body + payload)

    async def read_packet(self, timeout):
        head = await asyncio.wait_for(self.reader.readexactly(1), timeout)
        mult, length = 1, 0
        while True:
            b = (await self.reader.readexactly(1))[0]
            length += (b & 0x7F) * mult
            if not b & 0x80:
                break
            mult *= 128
        body = await self.reader.readexactly(length) if length else b""
        return head[0] >> 4, head[0] & 0x0F, body

    def close(self):
        self.alive = False
        try:
            self.writer.close()
        except Exception:
            pass


class Broker:
    def __init__(self, on_rule=None, on_publish=None, on_connect=None, on_disconnect=None, authorize=None):
        self.clients = {}            # client id -> Client
        self.on_rule = on_rule       # async (client, rule, topic, payload)
        self.on_publish = on_publish  # async (client, topic, payload) for non-rule topics
        self.on_connect = on_connect
        self.on_disconnect = on_disconnect
        self.authorize = authorize   # async (client_id, cert_cn, peer_cert_der) -> bool

    async def serve(self, host, port, ssl_context=None):
        server = await asyncio.start_server(self._handle, host, port, ssl=ssl_context)
        log.info("MQTT listening on %s:%s (%s)", host, port, "TLS" if ssl_context else "plain")
        return server

    async def publish(self, topic, payload: bytes, qos=1):
        """Deliver to every matching subscriber; returns the number of deliveries."""
        n = 0
        for c in list(self.clients.values()):
            granted = [q for f, q in c.subscriptions.items() if topic_matches(f, topic)]
            if granted and c.alive:
                try:
                    await c.publish(topic, payload, min(max(granted), qos))
                    n += 1
                except Exception as e:
                    log.warning("deliver to %s failed: %s", c.client_id, e)
        return n

    async def _handle(self, reader, writer):
        c = Client(self, reader, writer)
        sslobj = writer.get_extra_info("ssl_object")
        der = None
        if sslobj is not None:
            der = sslobj.getpeercert(binary_form=True)
            info = sslobj.getpeercert() or {}
            for rdn in info.get("subject", ()):
                for k, v in rdn:
                    if k == "commonName":
                        c.cert_cn = v
        try:
            ptype, _, body = await c.read_packet(30)
            if ptype != CONNECT:
                return
            if not await self._connect(c, body, der):
                return
            keepalive = c.keepalive
            while c.alive:
                timeout = keepalive * 1.5 if keepalive else None
                try:
                    ptype, flags, body = await c.read_packet(timeout)
                except asyncio.TimeoutError:
                    log.info("%s keep-alive expired", c.client_id)
                    break
                if ptype == PUBLISH:
                    await self._on_publish(c, flags, body)
                elif ptype == PUBREL:
                    await c.send(PUBCOMP, 0, body[:2])
                elif ptype == SUBSCRIBE:
                    await self._subscribe(c, body)
                elif ptype == UNSUBSCRIBE:
                    pid = body[:2]
                    pos = 2
                    while pos < len(body):
                        n = struct.unpack("!H", body[pos:pos + 2])[0]
                        c.subscriptions.pop(body[pos + 2:pos + 2 + n].decode(), None)
                        pos += 2 + n
                    await c.send(UNSUBACK, 0, pid)
                elif ptype == PINGREQ:
                    await c.send(PINGRESP, 0, b"")
                elif ptype == DISCONNECT:
                    break
                # PUBACK/PUBREC/PUBCOMP from the client: nothing to do (no redelivery)
        except (asyncio.IncompleteReadError, ConnectionError, ssl.SSLError, asyncio.TimeoutError) as e:
            log.info("%s (%s) connection ended: %s", c.client_id, c.peer, type(e).__name__)
        except Exception:
            log.exception("MQTT connection %s failed", c.client_id)
        finally:
            c.close()
            if c.client_id and self.clients.get(c.client_id) is c:
                del self.clients[c.client_id]
                if self.on_disconnect:
                    await self.on_disconnect(c)

    async def _connect(self, c, body, der):
        pos = 2 + struct.unpack("!H", body[:2])[0]
        proto_level, flags = body[pos], body[pos + 1]
        c.keepalive = struct.unpack("!H", body[pos + 2:pos + 4])[0]
        pos += 4
        n = struct.unpack("!H", body[pos:pos + 2])[0]
        c.client_id = body[pos + 2:pos + 2 + n].decode("utf-8")
        if proto_level not in (3, 4):
            await c.send(CONNACK, 0, b"\x00\x01")
            return False
        if self.authorize and not await self.authorize(c.client_id, c.cert_cn, der):
            log.warning("refused client id %s (certificate CN %s) from %s", c.client_id, c.cert_cn, c.peer)
            await c.send(CONNACK, 0, b"\x00\x05")
            return False
        old = self.clients.get(c.client_id)
        if old:
            log.info("client id %s reconnected; dropping the old connection", c.client_id)
            old.close()
        self.clients[c.client_id] = c
        await c.send(CONNACK, 0, b"\x00\x00")
        log.info("client %s connected from %s (keep-alive %ss)", c.client_id, c.peer, c.keepalive)
        if self.on_connect:
            await self.on_connect(c)
        return True

    async def _on_publish(self, c, flags, body):
        qos = (flags >> 1) & 3
        n = struct.unpack("!H", body[:2])[0]
        topic = body[2:2 + n].decode("utf-8")
        pos = 2 + n
        pid = None
        if qos:
            pid = body[pos:pos + 2]
            pos += 2
        payload = body[pos:]
        if qos == 1:
            await c.send(PUBACK, 0, pid)
        elif qos == 2:
            await c.send(PUBREC, 0, pid)
        if topic.startswith("$aws/rules/"):
            rule = topic.split("/", 3)[2]
            if self.on_rule:
                asyncio.create_task(self._safe(self.on_rule(c, rule, topic, payload)))
            return
        await self.publish(topic, payload, qos)
        if self.on_publish:
            asyncio.create_task(self._safe(self.on_publish(c, topic, payload)))

    @staticmethod
    async def _safe(coro):
        try:
            await coro
        except Exception:
            log.exception("handler failed")

    async def _subscribe(self, c, body):
        pid = body[:2]
        pos, codes = 2, bytearray()
        while pos < len(body):
            n = struct.unpack("!H", body[pos:pos + 2])[0]
            topic = body[pos + 2:pos + 2 + n].decode("utf-8")
            qos = body[pos + 2 + n] & 3
            pos += 3 + n
            granted = min(qos, 1)
            c.subscriptions[topic] = granted
            codes.append(granted)
            log.info("%s subscribed to %s (QoS %s)", c.client_id, topic, granted)
        await c.send(SUBACK, 0, pid + bytes(codes))


def server_context(certfile, keyfile, cafile, require_client_cert=True):
    ctx = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH, cafile=cafile)
    ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    ctx.load_cert_chain(certfile, keyfile)
    ctx.verify_mode = ssl.CERT_REQUIRED if require_client_cert else ssl.CERT_OPTIONAL
    return ctx
