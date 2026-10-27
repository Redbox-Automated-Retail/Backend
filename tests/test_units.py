"""Unit tests that need no running services."""
import hashlib

from redbox_backend import dotnet, kioskcrypto
from redbox_backend.iot.mqtt import topic_matches
from redbox_backend.updatesvc import commands, rcp, repo


def test_tokenizer_matches_client_command_strings():
    # Exactly as Redbox.UpdateService.Client.UpdateService.GetChangeSet / Poll build them.
    words, p = rcp.tokenize("u p n: '59394' c: '{\"a\":\"B\",\"it\\'s\":\"x\\\\y\"}' h:'ABC'")
    assert words == ["u", "p"]
    assert p == {"n": "59394", "c": '{"a":"B","it\'s":"x\\y"}', "h": "ABC"}
    words, p = rcp.tokenize("u pr n:'1' v:'2' c:'[]' h:'H'")
    assert words == ["u", "pr"] and p == {"n": "1", "v": "2", "c": "[]", "h": "H"}
    words, p = rcp.tokenize("installer log guid:'g' name:'FinishInstallerData' value:'{}' h: '{}'")
    assert p["guid"] == "g" and p["h"] == "{}"


def test_patch_names_follow_the_store_format():
    # update-manager.md §7.2, verified against the image
    assert repo.path_hash(r"C:\Program Files\redbox\REDS\Update Manager\bin", "bitsadmin.exe").startswith("E0609285")


def test_credential_hash():
    assert commands.credential_hash("59394", "k1", "k2") == hashlib.sha1(b"59394-k1-k2").hexdigest().upper()


def test_redbox_datetime_roundtrip():
    d = dotnet.parse_redbox_datetime({"theDate": 638342357081076401, "dateTimeKind": "Utc",
                                      "dateString": "2023-10-30T04:08:28.1076401Z"})
    assert d.year == 2023 and d.minute == 8
    obj = dotnet.redbox_datetime(d)
    assert obj["theDate"] // 10 == 638342357081076401 // 10


def test_kiosk_password_is_deterministic():
    assert kioskcrypto.kiosk_password("59394") == kioskcrypto.certificate_password("59394")
    assert kioskcrypto.kiosk_password("59394") != kioskcrypto.kiosk_password("59395")


def test_topic_matching():
    assert topic_matches("redbox/kiosk/+/command", "redbox/kiosk/1/command")
    assert topic_matches("#", "redbox/x")
    assert not topic_matches("#", "$aws/rules/kioskping")
    assert not topic_matches("redbox/kiosk/1/command", "redbox/kiosk/2/command")
