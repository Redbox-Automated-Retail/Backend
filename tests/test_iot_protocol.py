"""Protocol test of the IoT stack, acting exactly like UpdateClientService.API does on the wire.

Runs inside a container on the WSLC network (`scripts/rb.ps1 test`) against the running rb-cloudapi and
rb-iot. Needs the UCS passphrase (rb-cloudapi gets it from the decompiled source; the test gets it the same
way through UCS_ENCRYPTION_SOURCE or UCS_ENCRYPTION_PASSPHRASE) - without it the test is skipped.
"""
import base64
import json
import os
import socket
import ssl
import struct
import tempfile
import time
import urllib.request
import uuid

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.serialization import pkcs12

from redbox_backend import kioskcrypto

CLOUD = os.environ.get("CLOUDAPI_URL", "https://rb-cloudapi")
IOT = os.environ.get("IOT_HOST", "rb-iot")
IOT_ADMIN = os.environ.get("IOT_ADMIN_URL", f"http://{IOT}:8081")
CA = os.path.join(os.environ.get("CERT_DIR", "/certs"), "ca.crt")
KIOSK = int(os.environ.get("TEST_KIOSK_ID", "990001"))
MARKET = "Test Market"

pytestmark = pytest.mark.skipif(not kioskcrypto.passphrase(), reason="UCS passphrase not available")


def _ctx():
    return ssl.create_default_context(cafile=CA)


def _post(url, body, headers=None):
    req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json", **(headers or {})})
    with urllib.request.urlopen(req, context=_ctx() if url.startswith("https") else None, timeout=30) as r:
        return json.loads(r.read() or b"null")


class MqttClient:
    def __init__(self, certfile, keyfile, client_id):
        ctx = _ctx()
        ctx.load_cert_chain(certfile, keyfile)
        self.sock = ctx.wrap_socket(socket.create_connection((IOT, 8883), timeout=20), server_hostname=IOT)
        self.pid = 0
        vh = b"\x00\x04MQTT\x04\x02" + struct.pack("!H", 300)  # 3.1.1, clean session, keep-alive 300
        self._send(1, 0, vh + self._s(client_id))
        t, _, body = self.read()
        assert t == 2 and body[1] == 0, f"CONNACK refused: {body!r}"

    @staticmethod
    def _s(x):
        b = x.encode()
        return struct.pack("!H", len(b)) + b

    def _send(self, t, flags, body):
        n, enc = len(body), bytearray()
        while True:
            d, n = n % 128, n // 128
            enc.append(d | (0x80 if n else 0))
            if not n:
                break
        self.sock.sendall(bytes([(t << 4) | flags]) + bytes(enc) + body)

    def _recv(self, n):
        buf = b""
        while len(buf) < n:
            chunk = self.sock.recv(n - len(buf))
            if not chunk:
                raise ConnectionError("closed")
            buf += chunk
        return buf

    def read(self):
        h = self._recv(1)[0]
        mult = length = 0
        mult = 1
        while True:
            b = self._recv(1)[0]
            length += (b & 0x7F) * mult
            if not b & 0x80:
                break
            mult *= 128
        return h >> 4, h & 0x0F, self._recv(length) if length else b""

    def subscribe(self, topics):
        self.pid += 1
        body = struct.pack("!H", self.pid) + b"".join(self._s(t) + b"\x01" for t in topics)
        self._send(8, 2, body)
        t, _, b = self.read()
        assert t == 9 and all(c == 1 for c in b[2:]), "SUBACK"

    def publish(self, topic, obj, qos=1):
        self.pid += 1
        body = self._s(topic) + (struct.pack("!H", self.pid) if qos else b"") + json.dumps(obj).encode()
        self._send(3, qos << 1, body)
        if qos:
            t, _, _ = self.read()
            assert t == 4, "PUBACK"

    def next_message(self, timeout=15):
        self.sock.settimeout(timeout)
        while True:
            t, flags, body = self.read()
            if t != 3:
                continue
            n = struct.unpack("!H", body[:2])[0]
            topic = body[2:2 + n].decode()
            pos = 2 + n
            if (flags >> 1) & 3:
                self._send(4, 0, body[pos:pos + 2])
                pos += 2
            return topic, json.loads(body[pos:])

    def rpc(self, command, version, payload):
        req = {"command": command, "messageType": "Request", "version": version, "requestId": str(uuid.uuid4()),
               "origin": "UpdateServiceClient", "payload": payload, "sourceId": str(KIOSK),
               "requestTimeUtc": "2026-01-01T00:00:00Z"}
        self.publish("$aws/rules/kioskrestcall", req)
        while True:
            topic, msg = self.next_message()
            if msg.get("requestId") == req["requestId"] and msg.get("messageType") == "Response":
                assert topic == f"redbox/kiosk/{KIOSK}/command"
                return msg["payload"]


@pytest.fixture(scope="module")
def device():
    body = {"type": kioskcrypto.encrypt("kiosks"), "name": kioskcrypto.encrypt(str(KIOSK)), "thingGroupName": MARKET}
    headers = {"x-api-key": "test", "password": kioskcrypto.kiosk_password(str(KIOSK))}
    cert = _post(f"{CLOUD}/api/thing-types/certificates", body, headers)
    assert set(cert) == {"DeviceCertPfxBase64", "RootCa", "CertificateId"}
    key, leaf, _ = pkcs12.load_key_and_certificates(base64.b64decode(cert["DeviceCertPfxBase64"]),
                                                     kioskcrypto.certificate_password(str(KIOSK)).encode())
    d = tempfile.mkdtemp()
    cf, kf = os.path.join(d, "c.pem"), os.path.join(d, "k.pem")
    open(cf, "wb").write(leaf.public_bytes(serialization.Encoding.PEM))
    open(kf, "wb").write(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                           serialization.NoEncryption()))
    # get-certificates returns the same certificate; certificate-valid says true
    again = _post(f"{CLOUD}/api/thing-types/get-certificates", body, headers)
    assert again["CertificateId"] == cert["CertificateId"]
    valid = _post(f"{CLOUD}/api/thing-types/certificate-valid",
                  dict(body, certificateId=kioskcrypto.encrypt(cert["CertificateId"])), headers)
    assert valid is True
    return cf, kf, cert


def test_wrong_password_is_refused():
    body = {"type": kioskcrypto.encrypt("kiosks"), "name": kioskcrypto.encrypt(str(KIOSK)), "thingGroupName": MARKET}
    with pytest.raises(urllib.error.HTTPError) as e:
        _post(f"{CLOUD}/api/thing-types/get-certificates", body, {"password": "wrong"})
    assert e.value.code == 403


def test_connect_subscribe_and_rpc(device):
    cf, kf, _ = device
    c = MqttClient(cf, kf, str(KIOSK))
    c.subscribe([f"redbox/kiosk/{KIOSK}/command", f"redbox/kiosk/market/{MARKET}/command", "redbox/kiosk/all/command"])
    c.publish("$aws/rules/kioskucsregister", {"KioskId": KIOSK, "Version": "2.0", "UTCDate": "x",
                                              "DynamoDbTimeToLive": 0, "AssemblyVersion": "1.2.75.0"})

    status = c.rpc("GetConfigStatus", 1, None)
    assert status["StatusCode"] == 200 and status["Value"]["ConfigurationStatus"]["EnableUpdates"] is True

    changes = c.rpc("GetConfigChanges", 1, {"SettingChangesRequestId": str(uuid.uuid4()), "KioskId": KIOSK,
                                             "CurrentConfigurationVersionId": 0, "ConfigurationVersionHash": None,
                                             "RequestedConfigurationVersionId": None, "PageNumber": None})
    v = changes["Value"]
    assert v["VersionIsCurrent"] is False and v["OriginalConfigurationVersionId"] == 0
    doc = v["NewConfigurationSettingValues"]
    names = {(cat["Name"], s["Name"]) for cat in doc["Categories"] for s in cat["Settings"]}
    assert ("KioskClientDefault", "TransactionApiUrl") in names
    # Asking again with the version and hash we were given -> current
    again = c.rpc("GetConfigChanges", 1, {"SettingChangesRequestId": str(uuid.uuid4()), "KioskId": KIOSK,
                                           "CurrentConfigurationVersionId": doc["ConfigurationVersion"],
                                           "ConfigurationVersionHash": v["ConfigurationVersionHash"]})
    assert again["Value"]["VersionIsCurrent"] is True
    assert c.rpc("ActivateConfigVersion", 1, {"KioskId": KIOSK, "ConfigurationVersionId": doc["ConfigurationVersion"],
                                               "ModifiedBy": "System"})["StatusCode"] == 200

    seg = c.rpc("GetKioskSegments", 1, None)["Value"]["Segments"]
    assert any(s["SegmentationName"] == "All" for s in seg)
    assert c.rpc("BrokerRegister", 1, {"KioskId": KIOSK})["StatusCode"] == 200

    url = c.rpc("GetPresignedS3Url", 2, {"Key": "filesets/doesnotexist.zip", "Type": 0, "IsHead": True})
    assert isinstance(url, str) and "/s3/filesets/doesnotexist.zip?" in url

    fs = c.rpc("ReportFileSetVersions", 3, {"FileSetPollRequests": []})
    assert fs["StatusCode"] == 200 and isinstance(fs["Value"], list)

    # Time sync: the cloud answers with a SyncTimestampAndTimezone *request* to the kiosk.
    c.publish("$aws/rules/kioskrestcall", {"command": "SyncTimestampAndTimezone", "messageType": "Request",
                                           "version": 1, "requestId": str(uuid.uuid4()), "sourceId": str(KIOSK)})
    topic, msg = c.next_message()
    assert msg["command"] == "SyncTimestampAndTimezone" and msg["messageType"] == "Request"
    assert "UtcTimestamp" in msg["payload"]

    # Cloud -> kiosk request through the operator API; the kiosk answers on the instance topic.
    import threading
    result = {}
    t = threading.Thread(target=lambda: result.update(_post(f"{IOT_ADMIN}/send", {
        "target": str(KIOSK), "command": "Ping", "version": 2, "wait": 10})))
    t.start()
    topic, msg = c.next_message()
    assert msg["command"] == "Ping" and topic == f"redbox/kiosk/{KIOSK}/command"
    c.publish(f"redbox/updateservice-instance/{msg['sourceId']}/request",
              {"command": "Ping", "messageType": "Response", "version": 2, "requestId": msg["requestId"],
               "payload": json.dumps({"Data": None, "Error": None}), "sourceId": str(KIOSK)})
    t.join(15)
    assert result.get("response") == {"Data": None, "Error": None}


def test_mqtt_refuses_foreign_client_id(device):
    cf, kf, _ = device
    with pytest.raises(AssertionError):
        MqttClient(cf, kf, "12345")


def test_fileset_deploy_roundtrip(device):
    """Publish a file set, deploy it to the kiosk, then fetch every object the Update Client would."""
    import asyncio
    from redbox_backend import db, filesets

    async def make():
        await db.init(max_size=2, apply_schema=False)
        await filesets.ensure_fileset(77, "Test Fileset")
        rev = await filesets.create_revision(77, "test", "1.0.0.0", [("C:\\Temp\\rbtest\\hello.txt", b"hello kiosk\r\n"),
                                                                      ("C:\\Temp\\rbtest\\fileset.json", b"{}")])
        await filesets.deploy(77, rev, "kiosk", str(KIOSK), "ucs")
        await db.close()
        return rev
    rev = asyncio.run(make())
    cf, kf, _ = device
    c = MqttClient(cf, kf, str(KIOSK))
    c.subscribe([f"redbox/kiosk/{KIOSK}/command"])
    sets = c.rpc("ReportFileSetVersions", 3, {"FileSetPollRequests": []})["Value"]
    cs = next(s for s in sets if s["FileSetId"] == 77)
    assert cs["RevisionId"] == rev and cs["Action"] == 1
    url = c.rpc("GetPresignedS3Url", 2, {"Key": cs["Path"], "Type": 0, "IsHead": True})
    # The URL names the public S3 host; inside the test network go to the container instead.
    url = CLOUD + "/s3/" + url.split("/s3/", 1)[1]
    with urllib.request.urlopen(url, context=_ctx(), timeout=30) as r:
        data = r.read()
    import hashlib, io, zipfile
    assert base64.b64encode(hashlib.sha1(data).digest()).decode() == cs["FileHash"]
    z = zipfile.ZipFile(io.BytesIO(data))
    manifest = json.loads(z.read("revision"))
    assert json.loads(z.read("revision.json"))["RevisionId"] == rev
    assert {f["Name"] for f in manifest["Files"]} == {"hello.txt", "fileset.json"}
    # Once the kiosk reports the revision as active nothing more is sent.
    sets = c.rpc("ReportFileSetVersions", 3, {"FileSetPollRequests": [
        {"FileSetId": 77, "FileSetRevisionId": rev, "FileSetState": 0}]})["Value"]
    assert not any(s["FileSetId"] == 77 for s in sets)
