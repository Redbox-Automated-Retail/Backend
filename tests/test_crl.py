"""CRL distribution point in the certificates the local CA issues, and the CRL behind it (no backend needed)."""
from cryptography import x509

from redbox_backend import certs, config


def test_issued_certificates_point_at_a_valid_crl(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CERT_DIR", str(tmp_path))
    certs.ensure_all()
    ca = x509.load_pem_x509_certificate((tmp_path / "ca.crt").read_bytes())
    server = x509.load_pem_x509_certificate((tmp_path / "server.crt").read_bytes())  # leaf first, then the CA
    _, _, device = certs.issue_device_certificate(990001, "pw")
    for cert in (server, device):
        cdp = cert.extensions.get_extension_for_class(x509.CRLDistributionPoints).value
        assert [n.value for n in cdp[0].full_name] == [config.CRL_URL]
    crl = x509.load_der_x509_crl(certs.crl_der())
    assert crl.is_signature_valid(ca.public_key())
    assert crl.issuer == ca.subject and len(list(crl)) == 0
    assert certs.crl_der() == certs.crl_der()          # cached until a day before nextUpdate


def test_server_certificate_is_reissued_when_the_crl_url_changes(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "CERT_DIR", str(tmp_path))
    certs.ensure_all()
    first = (tmp_path / "server.crt").read_bytes()
    monkeypatch.setattr(config, "CRL_URL", "http://crl.example.test/ca.crl")
    certs.create_server_cert()
    cert = x509.load_pem_x509_certificate((tmp_path / "server.crt").read_bytes())
    assert (tmp_path / "server.crt").read_bytes() != first
    cdp = cert.extensions.get_extension_for_class(x509.CRLDistributionPoints).value
    assert cdp[0].full_name[0].value == "http://crl.example.test/ca.crl"


def test_cloudapi_serves_the_crl(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from redbox_backend.cloudapi.app import create_app
    monkeypatch.setattr(config, "CERT_DIR", str(tmp_path))
    certs.ensure_all()
    r = TestClient(create_app()).get("/crl/ca.crl")   # no lifespan: no database needed
    assert r.status_code == 200 and r.headers["content-type"] == "application/pkix-crl"
    assert x509.load_der_x509_crl(r.content).issuer == certs.load_ca()[0].subject
