"""Settings files for kiosk/reprovision (no backend needed)."""
import json

from redbox_backend import certs, config, kioskprofile


def test_profile_maps_redbox_names_to_address(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CERT_DIR", str(tmp_path))
    certs.create_ca()
    doc = kioskprofile.profile(12345, address="10.0.0.5", market="testmarket", rewrites={"https://old": "https://new"})
    assert doc["Format"] == 1 and doc["KioskId"] == 12345 and doc["Market"] == "testmarket"
    names = doc["Hosts"]["Names"]
    assert doc["Hosts"]["Address"] == "10.0.0.5"
    for n in ("updatesvc.redbox.com", "bl2.accessredbox.net", "blt.accessredbox.net", "aj5a0v5j9z9ai-ats.iot.us-east-1.amazonaws.com"):
        assert n in names
    assert not any(n == "localhost" or n.startswith("rb-") for n in names)
    # every host the settings point at is in the server certificate
    assert set(names) <= set(config.all_hostnames())
    assert doc["CaCertificate"].startswith("-----BEGIN CERTIFICATE-----")
    eng = doc["Engine"]
    assert eng["Remote Services\\Reservation Services"]["BrokerServicesURL"] == \
        "gtcp://blt.accessredbox.net:7890/RegistrationBroker/Default.rem"
    assert eng["Remote Services\\Store Services"]["KioskServicesURL"].startswith("https://bl2.accessredbox.net/")
    assert doc["UpdateManager"]["UpdateServiceUrl"] == "srcp://updatesvc.redbox.com:7002"
    assert doc["UrlRewrites"] == {"https://old": "https://new"}
    json.dumps(doc)


def test_profile_with_base_domain_has_no_hosts_and_warns(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CERT_DIR", str(tmp_path))
    doc = kioskprofile.profile(7, base_domain="kiosk.example.net", include_ca=False)
    assert doc["Hosts"] is None and doc["CaCertificate"] is None
    assert doc["UpdateClient"]["DefaultProxyServiceUrl"] == "https://proxyservice.kiosk.example.net"
    assert doc["Engine"]["Remote Services\\Reservation Services"]["BrokerServicesURL"].startswith("gtcp://broker.kiosk.example.net:")
    assert "EXTRA_HOSTNAMES" in doc["Warnings"][0]


def test_credential_keys_only_when_configured(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CERT_DIR", str(tmp_path))
    monkeypatch.setattr(config, "UPDATE_SERVICE_KEY1", "{A}")
    monkeypatch.setattr(config, "UPDATE_SERVICE_KEY2", "{B}")
    doc = kioskprofile.profile(7, address="1.2.3.4", include_ca=False)
    assert doc["UpdateManager"]["CredentialKeys"] == {"Key1": "{A}", "Key2": "{B}"}
