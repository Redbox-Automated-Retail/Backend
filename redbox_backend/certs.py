"""Local PKI: one CA, one server certificate for every backend host name, and IoT device certificates.

`python -m redbox_backend certs [--force]` creates in CERT_DIR (idempotent):
  ca.crt / ca.key           root CA ("Redbox Self-Hosted Root CA"); install ca.crt on the kiosk
  server.crt / server.key   leaf for every name in config.all_hostnames() (+ EXTRA_HOSTNAMES/EXTRA_IPS)
  s3.key                    random HMAC key for presigned S3 URLs
The IoT certificate service (cloudapi) signs kiosk device certificates with the same CA at run time.
"""
import argparse
import datetime as dt
import ipaddress
import logging
import os
import pathlib
import secrets

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

from . import config

log = logging.getLogger("certs")


def cert_dir():
    p = pathlib.Path(config.CERT_DIR)
    p.mkdir(parents=True, exist_ok=True)
    return p


def _write_key(path, key):
    path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL,
                                       serialization.NoEncryption()))
    os.chmod(path, 0o600)


def _now():
    return dt.datetime.now(dt.timezone.utc)


def load_ca():
    d = cert_dir()
    cert = x509.load_pem_x509_certificate((d / "ca.crt").read_bytes())
    key = serialization.load_pem_private_key((d / "ca.key").read_bytes(), password=None)
    return cert, key


def create_ca(force=False):
    d = cert_dir()
    if (d / "ca.crt").exists() and (d / "ca.key").exists() and not force:
        return load_ca()
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Redbox Self-Hosted Root CA"),
                      x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Redbox self-hosted backend")])
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(_now() - dt.timedelta(days=1)).not_valid_after(_now() + dt.timedelta(days=3650))
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
            .add_extension(x509.KeyUsage(digital_signature=True, key_cert_sign=True, crl_sign=True,
                                         content_commitment=False, key_encipherment=False, data_encipherment=False,
                                         key_agreement=False, encipher_only=False, decipher_only=False), critical=True)
            .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
            .sign(key, hashes.SHA256()))
    _write_key(d / "ca.key", key)
    (d / "ca.crt").write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    # DER copy for Windows (certutil -addstore Root ca.cer)
    (d / "ca.cer").write_bytes(cert.public_bytes(serialization.Encoding.DER))
    log.info("created CA %s", d / "ca.crt")
    return cert, key


def _leaf(ca_cert, ca_key, common_name, sans, server=True, days=1825):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    builder = (x509.CertificateBuilder()
               .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)]))
               .issuer_name(ca_cert.subject).public_key(key.public_key())
               .serial_number(x509.random_serial_number())
               .not_valid_before(_now() - dt.timedelta(days=1)).not_valid_after(_now() + dt.timedelta(days=days))
               .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
               .add_extension(x509.KeyUsage(digital_signature=True, key_encipherment=True, content_commitment=False,
                                            data_encipherment=False, key_agreement=False, key_cert_sign=False,
                                            crl_sign=False, encipher_only=False, decipher_only=False), critical=True)
               .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH if server
                                                     else ExtendedKeyUsageOID.CLIENT_AUTH]), critical=False)
               .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False))
    if sans:
        builder = builder.add_extension(x509.SubjectAlternativeName(sans), critical=False)
    return builder.sign(ca_key, hashes.SHA256()), key


def create_server_cert(force=False):
    d = cert_dir()
    names = config.all_hostnames()
    marker = d / "server.names"
    wanted = "\n".join(names + ["ip:" + i for i in config.EXTRA_IPS])
    if (d / "server.crt").exists() and marker.exists() and marker.read_text() == wanted and not force:
        return
    ca_cert, ca_key = create_ca()
    sans = [x509.DNSName(n) for n in names]
    for ip in ["127.0.0.1"] + config.EXTRA_IPS:
        sans.append(x509.IPAddress(ipaddress.ip_address(ip)))
    cert, key = _leaf(ca_cert, ca_key, names[0], sans, server=True)
    _write_key(d / "server.key", key)
    (d / "server.crt").write_bytes(cert.public_bytes(serialization.Encoding.PEM) +
                                   ca_cert.public_bytes(serialization.Encoding.PEM))
    marker.write_text(wanted)
    log.info("created server certificate for %s", ", ".join(names))


def s3_key():
    if config.S3_SIGNING_KEY:
        return config.S3_SIGNING_KEY.encode()
    p = cert_dir() / "s3.key"
    if not p.exists():
        p.write_text(secrets.token_hex(32))
    return p.read_text().strip().encode()


def issue_device_certificate(kiosk_id, pfx_password):
    """Sign a client certificate for an IoT thing (CN = kiosk id) and return (pfx_bytes, certificate_id)."""
    ca_cert, ca_key = load_ca()
    cert, key = _leaf(ca_cert, ca_key, str(kiosk_id), [x509.DNSName(f"kiosk-{kiosk_id}")], server=False, days=3650)
    # .NET Core 2.2 on Windows reads 3DES/SHA1 PKCS#12 reliably; AES-based PKCS#12 needs newer Windows APIs.
    encryption = (serialization.PrivateFormat.PKCS12.encryption_builder()
                  .kdf_rounds(2048).key_cert_algorithm(pkcs12.PBES.PBESv1SHA1And3KeyTripleDESCBC)
                  .hmac_hash(hashes.SHA1()).build(pfx_password.encode()))
    pfx = pkcs12.serialize_key_and_certificates(f"kiosk-{kiosk_id}".encode(), key, cert, [ca_cert], encryption)
    certificate_id = cert.fingerprint(hashes.SHA256()).hex()
    return pfx, certificate_id, cert


def internal_token():
    """Shared secret for service-to-service calls (/_internal/*), kept in the certs volume."""
    p = cert_dir() / "internal.key"
    if not p.exists():
        p.write_text(secrets.token_hex(32))
        os.chmod(p, 0o644)  # read by the Mono remoting host, which runs as another user
    return p.read_text().strip()


def ca_pem():
    return (cert_dir() / "ca.crt").read_text()


def ensure_all(force=False):
    create_ca(force=force)
    create_server_cert(force=force)
    s3_key()
    internal_token()


def main(argv):
    ap = argparse.ArgumentParser(prog="certs")
    ap.add_argument("--force", action="store_true", help="regenerate the CA and server certificate")
    ap.add_argument("--export", help="copy ca.crt/ca.cer to this directory")
    args = ap.parse_args(argv)
    ensure_all(force=args.force)
    if args.export:
        out = pathlib.Path(args.export)
        out.mkdir(parents=True, exist_ok=True)
        for n in ("ca.crt", "ca.cer"):
            (out / n).write_bytes((cert_dir() / n).read_bytes())
        log.info("exported CA to %s", out)
    print((cert_dir() / "ca.crt").read_text())
