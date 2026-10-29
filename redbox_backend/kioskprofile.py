"""Per-kiosk settings for the reprovisioning tool (kiosk/reprovision, `rb-reprovision apply <file>`).

`profile(kiosk_id, address, ...)` returns the JSON document the tool reads (format 1, see
kiosk/reprovision/README.md). With the default host names every URL keeps the original Redbox name and the
hosts file maps those names to `address`; with `base_domain` the URLs use `<service>.<base_domain>` names that
the kiosk resolves itself (DNS), and no hosts entries are written.
"""
import datetime as dt

from . import certs, config

FORMAT = 1

# Redbox host name -> short name used with a base domain.
SERVICE_HOSTS = {
    "updatesvc.redbox.com": "updatesvc",
    "proxyservice.kiosk.prod.redbox.com": "proxyservice",
    "transactionservice.kiosk.prodp.redbox.com": "transactionservice",
    "kioskinventory.kiosk.prod.redbox.com": "kioskinventory",
    "dataservice.kiosk.prod.redbox.com": "dataservice",
    "adserver.kiosk.prod.redbox.com": "adserver",
    "reels.product.prod.redbox.com": "reels",
    "iotcertificateservice.kiosk.prod.redbox.com": "iotcertificateservice",
    "files.kiosk.prod.redbox.com": "files",
    "tickets-dev.redbox.com": "tickets",
    "blt.accessredbox.net": "broker",
    "bl2.accessredbox.net": "kioskservices",
    "aj5a0v5j9z9ai-ats.iot.us-east-1.amazonaws.com": "iot",
    "a2zxs75ezdmapj-ats.iot.us-east-1.amazonaws.com": "iot-staging",
}


def kiosk_hostnames():
    """Every name a kiosk resolves to this backend (the certificate's names without the container names)."""
    return [h for h in config.all_hostnames() if h != "localhost" and not h.startswith("rb-")]


def profile(kiosk_id, address=None, market=None, base_domain=None, rewrites=None, broker_port=7890,
            include_ca=True, comment=None):
    def host(name):
        return f"{SERVICE_HOSTS[name]}.{base_domain}" if base_domain else name

    doc = {
        "Format": FORMAT,
        "Comment": comment or f"kiosk {kiosk_id} -> {address or base_domain}",
        "Generated": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "KioskId": int(kiosk_id) if kiosk_id is not None else None,
        "Market": market,
        "Hosts": None if base_domain or not address else {"Address": address, "Names": kiosk_hostnames()},
        "CaCertificate": (certs.cert_dir() / "ca.crt").read_text() if include_ca else None,
        "Engine": {
            "Remote Services\\Reservation Services": {
                "BrokerServicesURL": f"gtcp://{host('blt.accessredbox.net')}:{broker_port}/RegistrationBroker/Default.rem"},
            "Remote Services\\Store Services": {
                "KioskServicesURL": f"https://{host('bl2.accessredbox.net')}/KioskServices/MessageService.rem"},
        },
        "UpdateManager": {
            "UpdateServiceUrl": f"srcp://{host('updatesvc.redbox.com')}:{config.UPDATE_SERVICE_PORT}",
            "CredentialKeys": ({"Key1": config.UPDATE_SERVICE_KEY1, "Key2": config.UPDATE_SERVICE_KEY2}
                               if config.UPDATE_SERVICE_KEY1 and config.UPDATE_SERVICE_KEY2 else None),
        },
        "UpdateClient": {
            "IoTCertificateServiceUrl": f"https://{host('iotcertificateservice.kiosk.prod.redbox.com')}",
            "DefaultProxyServiceUrl": f"https://{host('proxyservice.kiosk.prod.redbox.com')}",
            # Production overlay endpoint; the base appsettings.json has the staging one.
            "IoTBrokerEndpoint": host("aj5a0v5j9z9ai-ats.iot.us-east-1.amazonaws.com"),
        },
        "KioskClient": {"DefaultProxyServiceUrl": f"https://{host('proxyservice.kiosk.prod.redbox.com')}"},
        "DeviceService": {"KioskDataApiUrl": f"https://{host('dataservice.kiosk.prod.redbox.com')}"},
        "UrlRewrites": dict(rewrites or {}),
    }
    if base_domain:
        names = set(config.all_hostnames())
        missing = sorted({host(h) for h in SERVICE_HOSTS} - names)
        if missing:
            doc["Warnings"] = ["not in the server certificate (add to EXTRA_HOSTNAMES): " + ", ".join(missing)]
    return doc
