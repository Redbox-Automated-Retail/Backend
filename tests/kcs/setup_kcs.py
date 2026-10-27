"""Prepare (or undo) a local run of the real Kiosk Client Service against the self-hosted cloud API.

    python setup_kcs.py install <kcs-bin-dir> <cloud-base-url>   # e.g. http://localhost:18081
    python setup_kcs.py restore

`install` writes <kcs-bin-dir>/appsettings.json (the shipped settings, with DefaultProxyServiceUrl set to
the cloud API) and %ProgramData%\\Redbox\\Configuration\\configuration.json pointing every KCS cloud
setting at <cloud-base-url>; an existing configuration.json is backed up first. `restore` puts the backup
back (or deletes the file it created). API keys are dummy test values.
"""
import json
import os
import shutil
import sys

PROGRAMDATA = os.environ.get("PROGRAMDATA", r"C:\ProgramData")
CONFIG_DIR = os.path.join(PROGRAMDATA, "Redbox", "Configuration")
CONFIG = os.path.join(CONFIG_DIR, "configuration.json")
BACKUP = CONFIG + ".kcs-test-backup"
MARKER = CONFIG + ".kcs-test-created"

APPSETTINGS = {
    "Logging": {"LogLevel": {"Default": "Warning"}},
    "Serilog": {
        "MinimumLevel": {"Default": "Information", "Override": {"Microsoft": "Fatal", "System": "Fatal"}},
        "WriteTo": [{"Name": "Async", "Args": {"configure": [{"Name": "RollingFile", "Args": {
            "pathFormat": "%PROGRAMDATA%\\Redbox\\KioskClient\\Logs\\kioskclient-{Date}.log",
            "outputTemplate": "{Timestamp:yyyy-MM-dd HH:mm:ss.fff}|{ThreadId}|{ActivityId}|{Level:u3}|{Message:j}{NewLine}{Exception}"}}]}}],
    },
    "AppSettings": {
        "ServiceUrl": "http://localhost:9001",
        "DataFilePath": ".\\Data",
        "UpdateManagerAddress": "127.0.0.1",
        "UpdateManagerPort": 7004,
        "HttpQueuePollingInterval": 30000,
        "HttpQueueMaxPollingInterval": 300000,
        "HttpQueueDepthPollingIntervalMinutes": 240,
        "HttpQueueDepthToAlert": 7,
        "HttpQueueAlertRetryThreshold": 30,
        "PlanogramFolder": "Planograms",
        "DefaultProxyServiceUrl": None,
        "UpdateClientServiceUrl": "http://localhost:6001",
        "ProfileDataSettings": {"RefreshIntervalHours": 25, "StartingRetryIntervalMinutes": 5, "MaxRetryIntervalMinutes": 30},
    },
    "AllowedHosts": "*",
}


def settings(base):
    """category -> {setting: value}: what a kiosk must point at the self-hosted cloud API."""
    return {
        "KioskClientDefault": {
            "TransactionApiUrl": base, "TransactionApiKey": "test-transaction-key",
            "InventoryApiUrl": base, "InventoryApiKey": "test-inventory-key",
            "KioskDataServiceUrl": base, "KioskDataServiceAPIKey": "test-kds-key",
            "ProductReelsApiUrl": base,
            "ProductGroupSortOrderType": "1", "PricingActive": "true", "PlanogramSyncEnabled": "true",
        },
        "KioskClientProxyApi": {"ProxyApiUrl": base, "ProxyApiKey": "test-proxy-key"},
        "KioskClientAds": {"AdsActive": "true", "AdServerUrl": base + "/api", "AdServerAPIKey": "test-ads-key",
                           "AdAnalyticsActive": "true"},
        "KioskClientSubscription": {"SubscriptionPlansActive": "true"},
        "Campaigns": {"JobActive": "true", "InCartsActive": "true", "StartScreensActive": "true", "RefreshInterval": "1"},
        "MarketingReturn": {"ReturnVisitPromoOfferEnabled": "true"},
    }


def configuration(base, kiosk_id=0, version=1):
    cats, sid = [], 1
    for cat, values in settings(base).items():
        entries = []
        for name, value in values.items():
            entries.append({"Name": name, "SettingId": sid, "DataType": "string", "SettingValues": [{
                "ConfigurationSettingValueId": sid, "EncryptionType": None, "Value": value, "SegmentId": 1,
                "SegmentName": "SelfHosted", "SegmentationName": "Default", "Rank": 1,
                "EffectiveDateTime": "2020-01-01T00:00:00", "ExpireDateTime": None}]})
            sid += 1
        cats.append({"Name": cat, "Settings": entries})
    return {"KioskConfigurationSettings": {"KioskId": kiosk_id, "ConfigurationVersion": version,
                                           "ConfigurationVersionHash": "selfhosted-test", "Categories": cats}}


def install(bin_dir, base):
    base = base.rstrip("/")
    app = json.loads(json.dumps(APPSETTINGS))
    app["AppSettings"]["DefaultProxyServiceUrl"] = base
    with open(os.path.join(bin_dir, "appsettings.json"), "w", encoding="utf-8") as f:
        json.dump(app, f, indent=2)
    os.makedirs(CONFIG_DIR, exist_ok=True)
    if os.path.exists(CONFIG) and not os.path.exists(BACKUP) and not os.path.exists(MARKER):
        shutil.copy2(CONFIG, BACKUP)
    elif not os.path.exists(CONFIG) and not os.path.exists(BACKUP):
        open(MARKER, "w").close()
    with open(CONFIG, "w", encoding="utf-8") as f:
        json.dump(configuration(base), f, indent=2)
    print("wrote", CONFIG)


def restore():
    if os.path.exists(BACKUP):
        shutil.move(BACKUP, CONFIG)
        print("restored", CONFIG)
    elif os.path.exists(MARKER):
        if os.path.exists(CONFIG):
            os.remove(CONFIG)
        os.remove(MARKER)
        print("removed", CONFIG)
    else:
        print("nothing to restore")


if __name__ == "__main__":
    if len(sys.argv) >= 4 and sys.argv[1] == "install":
        install(sys.argv[2], sys.argv[3])
    elif len(sys.argv) >= 2 and sys.argv[1] == "restore":
        restore()
    else:
        print(__doc__)
        sys.exit(2)
