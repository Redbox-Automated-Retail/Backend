"""Settings shared by every service, read from environment variables.

Host names default to the original Redbox names so that a kiosk only needs hosts-file entries and the
local CA; set the variables to use your own names instead (see README.md).
"""
import os


def _env(name, default=None):
    value = os.environ.get(name)
    return default if value in (None, "") else value


def _env_bool(name, default=False):
    value = _env(name)
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


DATABASE_URL = _env("DATABASE_URL", "postgresql://redbox:redbox@rb-db:5432/redbox")
DATA_DIR = _env("DATA_DIR", "/data")
CERT_DIR = _env("CERT_DIR", "/certs")
LOG_LEVEL = _env("LOG_LEVEL", "INFO")

# Legacy Update Service (updatemgrd).
UPDATE_SERVICE_PORT = int(_env("UPDATE_SERVICE_PORT", "7002"))
UPDATE_SERVICE_TLS = _env_bool("UPDATE_SERVICE_TLS", True)
UPDATE_SERVICE_PLAIN_PORT = int(_env("UPDATE_SERVICE_PLAIN_PORT", "0"))  # optional extra rcp:// listener
UPDATE_CONTENT_PORT = int(_env("UPDATE_CONTENT_PORT", "7080"))
# Base URL BITS uses to fetch change sets and file-set content (must be reachable from the kiosk).
UPDATE_CONTENT_BASE_URL = _env("UPDATE_CONTENT_BASE_URL", "http://updatesvc.redbox.com:7080")
# The two credential keys compiled into Redbox.UpdateService.Client.dll (or from usdata.active).
# When both are set, `h:` hashes are verified; otherwise every kiosk is accepted and mismatches are logged.
UPDATE_SERVICE_KEY1 = _env("UPDATE_SERVICE_KEY1")
UPDATE_SERVICE_KEY2 = _env("UPDATE_SERVICE_KEY2")

# Store installer (`installer start`): hosts-file lines it is given (pipe separated). Built from the host
# names below and BACKEND_PUBLIC_IP when empty.
INSTALLER_HOST_ENTRIES = _env("INSTALLER_HOST_ENTRIES", "")
BACKEND_PUBLIC_IP = _env("BACKEND_PUBLIC_IP", "127.0.0.1")

# Kiosk cloud HTTPS APIs.
CLOUD_API_PORT = int(_env("CLOUD_API_PORT", "443"))
# Extra plain-HTTP listener of the same app (the 2014 Ticket Services client uses http://...:80); 0 = off.
CLOUD_API_HTTP_PORT = int(_env("CLOUD_API_HTTP_PORT", "80"))
# Base URL of the S3 stand-in that presigned URLs point at.
S3_BASE_URL = _env("S3_BASE_URL", "https://files.kiosk.prod.redbox.com")
S3_SIGNING_KEY = _env("S3_SIGNING_KEY", "")  # generated into CERT_DIR when empty

# MQTT broker standing in for AWS IoT Core.
MQTT_PORT = int(_env("MQTT_PORT", "8883"))
MQTT_PLAIN_PORT = int(_env("MQTT_PLAIN_PORT", "0"))  # optional plain listener for debugging
MQTT_REQUIRE_CLIENT_CERT = _env_bool("MQTT_REQUIRE_CLIENT_CERT", True)
IOT_ADMIN_PORT = int(_env("IOT_ADMIN_PORT", "8081"))
IOT_INSTANCE_ID = _env("IOT_INSTANCE_ID", "selfhosted-updateservice-1")

# Card authorisation behaviour of the Transaction API stand-in: "approve", "decline" or "rules".
PAYMENT_MODE = _env("PAYMENT_MODE", "approve")

# Host names put into the server certificate (comma separated, in addition to the defaults).
DEFAULT_HOSTNAMES = [
    "updatesvc.redbox.com",
    "proxyservice.kiosk.prod.redbox.com",
    "transactionservice.kiosk.prodp.redbox.com",
    "kioskinventory.kiosk.prod.redbox.com",
    "dataservice.kiosk.prod.redbox.com",
    "adserver.kiosk.prod.redbox.com",
    "reels.product.prod.redbox.com",
    "iotcertificateservice.kiosk.prod.redbox.com",
    "files.kiosk.prod.redbox.com",
    "tickets-dev.redbox.com",
    # Kiosk Engine: legacy reservation broker (gtcp, no TLS) and Kiosk Services (https, rb-cloudapi)
    "blt.accessredbox.net",
    "bl2.accessredbox.net",
    "aj5a0v5j9z9ai-ats.iot.us-east-1.amazonaws.com",
    "a2zxs75ezdmapj-ats.iot.us-east-1.amazonaws.com",
    "localhost",
    # container names on the WSLC network (tests, service-to-service calls)
    "rb-cloudapi", "rb-updatesvc", "rb-iot",
]
EXTRA_HOSTNAMES = [h.strip() for h in (_env("EXTRA_HOSTNAMES", "") or "").split(",") if h.strip()]
EXTRA_IPS = [h.strip() for h in (_env("EXTRA_IPS", "") or "").split(",") if h.strip()]


def all_hostnames():
    seen = []
    for name in DEFAULT_HOSTNAMES + EXTRA_HOSTNAMES:
        if name not in seen:
            seen.append(name)
    return seen
