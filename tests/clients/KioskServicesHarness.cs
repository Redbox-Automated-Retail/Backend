// Drives the real Redbox.KioskServices.Proxy (KioskServicesProxy.ExecuteMessage) against the self-hosted MessageService.
// The proxy reads its URL, protocol and timeout from IMachineSettingsStore, which a fake supplies; everything else is the
// kiosk's own code: .NET Remoting HttpChannel + binary formatter for "Remoting", form POST payload=<json> for "REST".
using System;
using System.Collections.Generic;
using System.Net;
using System.Web.Script.Serialization;
using Redbox.Core;
using Redbox.KioskEngine.ComponentModel;
using Redbox.KioskServices.Proxy;

public class KsSettings : IMachineSettingsStore
{
    public static readonly Dictionary<string, object> Values = new Dictionary<string, object>();
    public T GetValue<T>(string path, string key) { return GetValue(path, key, default(T)); }
    public T GetValue<T>(string path, string key, T defaultValue)
    {
        object v;
        return Values.TryGetValue(key, out v) ? (T)v : defaultValue;
    }
    public void SetValue<T>(string path, string key, T value) { Values[key] = value; }
    public void RemoveKey(string path) { }
    public void RemoveValue(string path, string key) { }
}

public static class KioskServicesHarness
{
    public static int Run(string url, string protocol, string json)
    {
        KsSettings.Values["KioskServicesURL"] = url;
        KsSettings.Values["KioskServicesProtocol"] = (KioskServicesProtocol)Enum.Parse(typeof(KioskServicesProtocol), protocol);
        KsSettings.Values["KioskServicesTimeout"] = 30000;
        ServiceLocator.Instance.AddService(typeof(IMachineSettingsStore), new KsSettings());
        // Harness-only: accept the backend's private CA for https URLs (a real kiosk trusts it through its cert store).
        ServicePointManager.ServerCertificateValidationCallback = (s, c, ch, e) => true;
        ServicePointManager.SecurityProtocol = SecurityProtocolType.Tls12;

        IDictionary<string, object> result = KioskServicesProxy.Instance.ExecuteMessage(json);
        Console.WriteLine("RESULT " + new JavaScriptSerializer().Serialize(result));
        object ex;
        return result != null && !result.TryGetValue("ExceptionRaised", out ex) ? 0 : 1;
    }
}
