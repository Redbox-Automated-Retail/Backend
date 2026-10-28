// Every MessageType the 2014-2016 engines' Redbox.DVD.KioskServices.dll (1.8.0.37, a kernel extension with the
// proxy, its ComponentModel and IMessageService ILMerged in and internalized) sends, through the proxies' own
// methods, against the self-hosted MessageService over Remoting or REST. All of it is internal, so it is driven by
// reflection; callbacks are delegates of its internal RemoteServiceCallback type. Engine state comes from EngineStubs
// (settings, a shopping session with a card and a one-disc rental group, track data); queued messages are forwarded as
// the engine's queue does (the object as Redbox.Core JSON, through ExecuteMessage). Built as an exe in the engine's
// bin\ (run-kioskservices-cases-2016.ps1), so the application base is bin\ as for kioskengine.exe.
using System;
using System.Collections;
using System.Collections.Generic;
using System.Collections.ObjectModel;
using System.IO;
using System.Linq;
using System.Linq.Expressions;
using System.Net;
using System.Reflection;
using System.Threading;
using Redbox.Core;

public static class KioskServicesCases2016
{
    const BindingFlags All = BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance | BindingFlags.Static;
    const int Kiosk = 990016;
    static Assembly ks;
    static int failures, loggedExceptions;
    static readonly List<string> Queued = new List<string>();
    static readonly string SessionId = Guid.NewGuid().ToString();

    static Type T(string name)
    {
        var t = ks.GetType(name, false);
        if (t != null) return t;
        foreach (var a in AppDomain.CurrentDomain.GetAssemblies())
        {
            try { t = a.GetType(name, false); } catch { }
            if (t != null) return t;
        }
        throw new Exception("type not found: " + name);
    }

    static object Instance(string cls) { return T(cls).GetProperty("Instance", All).GetValue(null, null); }

    public static int Main(string[] argv)
    {
        try { return Run(argv[0], argv[1], argv[2], argv.Length > 3 ? argv[3] : ""); }
        catch (Exception e) { Console.WriteLine((e.InnerException ?? e).ToString()); return 2; }
    }

    static void Stub(string iface) { ServiceLocator.Instance.AddService(T(iface), StubFactory.Create(T(iface))); }

    static void Setup(string url, string protocol)
    {
        ServicePointManager.ServerCertificateValidationCallback = (a, b, c, d) => true;  // harness only: local CA
        ServicePointManager.SecurityProtocol = SecurityProtocolType.Tls12;
        var settings = new Dictionary<string, object>
        {
            { "KioskServicesURL", url }, { "KioskServicesTimeout", 30000 }, { "ID", Kiosk.ToString() },
            { "LiveCall", true }, { "GCLiveCall", true }, { "IsServerBusy", false }
        };
        StubHub.Typed["IMachineSettingsStore.GetValue"] = (ret, a) =>
        {
            string key = (string)a[1];
            if (key == "KioskServicesProtocol") return Enum.Parse(ret, protocol);
            object v;
            if (settings.TryGetValue(key, out v)) return ret.IsInstanceOfType(v) ? v : Convert.ChangeType(v, ret);
            return a.Length > 2 ? a[2] : null;
        };
        StubHub.Set("IMachineSettingsStore.SetValue", a => { settings[(string)a[1]] = a[2]; return null; });
        StubHub.Set("ICallbackService.EnqueueCallback", a =>
        {
            var entry = a[0];
            ThreadPool.QueueUserWorkItem(_ => entry.GetType().GetMethod("Invoke").Invoke(entry, null));
            return null;
        });
        StubHub.Set("IMacroService.get_Item", "1.7.0.9");
        var toJson = typeof(ServiceLocator).Assembly.GetTypes().SelectMany(t => t.GetMethods(BindingFlags.Public | BindingFlags.Static))
            .First(m => m.Name == "ToJson" && m.GetParameters().Length == 1 && m.GetParameters()[0].ParameterType == typeof(object));
        Func<object[], object> queue = a =>
        {
            string type = (string)a[1];
            string json = a[2] as string ?? (string)toJson.Invoke(null, new[] { a[2] });
            var reply = (IDictionary<string, object>)Instance("Redbox.KioskServices.Proxy.KioskServicesProxy").GetType()
                .GetMethod("ExecuteMessage", new[] { typeof(string) }).Invoke(Instance("Redbox.KioskServices.Proxy.KioskServicesProxy"), new object[] { json });
            lock (Queued) Queued.Add(type);
            object ok;
            Report("queued " + type, reply != null && reply.TryGetValue("Success", out ok) && ok is bool && (bool)ok, Dump.Of(reply, 3));
            return Activator.CreateInstance(T("Redbox.KioskEngine.ComponentModel.ErrorList"));
        };
        StubHub.Set("IQueueService.Enqueue", queue);
        StubHub.Set("IQueueService.EnqueueOnlyOne", queue);
        // A session: card holder and swipe, one rental group with one DVD.
        StubHub.Set("IShoppingSession.get_StoreNumber", Kiosk.ToString());
        StubHub.Set("IShoppingSession.get_Id", SessionId);
        StubHub.Set("IShoppingCart.get_FirstName", "Test");
        StubHub.Set("IShoppingCart.get_LastName", "Customer");
        StubHub.Set("IShoppingCart.get_PostalCode", "60601");
        StubHub.Set("IShoppingCart.get_ExpiryYear", "39");
        StubHub.Set("IShoppingCart.get_ExpiryMonth", "12");
        StubHub.Set("IShoppingCart.get_Email", "harness2016@example.test");
        StubHub.Set("ITrackDataService.GenerateCardHashId", "HARNESS-CARD-" + Kiosk);
        StubHub.Set("ITrackDataService.GetNumber", "ENCRYPTED-NUMBER");
        StubHub.Set("ITrackDataService.GetKeyId", 1);
        var groupType = T("Redbox.KioskEngine.ComponentModel.IShoppingCartLineItemGroup");
        var itemType = T("Redbox.KioskEngine.ComponentModel.IShoppingCartLineItem");
        var items = (IList)Activator.CreateInstance(typeof(List<>).MakeGenericType(itemType));
        items.Add(StubFactory.Create(itemType));
        StubHub.Set("IShoppingCartLineItemGroup.get_Items", items);
        StubHub.Set("IShoppingCartLineItem.get_Barcode", "123456789");
        StubHub.Set("IShoppingCartLineItem.get_ProductFamily", "Movie");
        StubHub.Set("IShoppingCartLineItem.get_ProductType", "DVD");
        StubHub.Set("IShoppingCartLineItem.get_Prices", new Dictionary<string, decimal> { { "initial_night", 1.99m } });
        StubHub.Set("IShoppingCartTotals.get_SubTotal", 1.99m);
        StubHub.Set("IShoppingCartTotals.get_DiscountedSubtotal", 1.99m);
        StubHub.Set("IShoppingCartTotals.get_GrandTotal", 1.99m);
        var groupsProp = T("Redbox.KioskEngine.ComponentModel.IShoppingCart").GetProperty("Groups");
        var groups = (IDictionary)Activator.CreateInstance(typeof(Dictionary<,>).MakeGenericType(typeof(string), groupType));
        groups["rental"] = StubFactory.Create(groupType);
        StubHub.Set("IShoppingCart.get_Groups", groupsProp.PropertyType.IsInstanceOfType(groups) ? (object)groups : null);
        foreach (var n in new[] { "IMachineSettingsStore", "ICallbackService", "IMacroService", "ITrackDataService", "IShoppingSessionService",
                                  "IQueueService", "IResourceBundleService", "IKernelService", "IKernelExtensionService",
                                  "IObjectCacheService", "IEngineApplication" })
            try { Stub("Redbox.KioskEngine.ComponentModel." + n); } catch (Exception e) { Console.WriteLine("note: " + e.Message); }
        // Lua tables handed to the engine's scripts need a real Lua state, as the engine's KernelService has.
        var vm = Activator.CreateInstance(T("Redbox.Lua.LuaVirtualMachine"));
        var tableType = T("Redbox.Lua.LuaTable");
        StubHub.Set("IKernelService.NewTable", a => { lock (vm) return Activator.CreateInstance(tableType, vm); });
        // The proxy's own log; an exception it swallows fails the case.
        bool verbose = Environment.GetEnvironmentVariable("HARNESS_LOG") == "1";
        StubHub.Set("ILogger.Log", a =>
        {
            bool ex = a.Length > 1 && a[1] is Exception;
            if (ex) Interlocked.Increment(ref loggedExceptions);
            if (verbose || ex) Console.WriteLine("  log: " + a[0] + (ex ? " | " + a[1] : ""));
            return null;
        });
        var logHelper = T("Redbox.Core.LogHelper").GetProperty("Instance").GetValue(null, null);
        var loggers = logHelper.GetType().GetProperty("Loggers");
        if (loggers != null) ((IList)loggers.GetValue(logHelper, null)).Add(StubFactory.Create(T("Redbox.Core.ILogger")));
        else logHelper.GetType().GetProperty("Logger").SetValue(logHelper, StubFactory.Create(T("Redbox.Core.ILogger")), null);
    }

    static void Report(string name, bool success, string detail)
    {
        Console.WriteLine("CASE " + name + " success=" + success + " " + (detail.Length > 1500 ? detail.Substring(0, 1500) + "…" : detail));
    }

    /// <summary>Invoke cls.method(args...) with a RemoteServiceCallback as the last parameter; wait for it.</summary>
    static void Run(string name, bool expectSuccess, string cls, string method, params object[] args)
    {
        int before = loggedExceptions;
        var target = Instance("Redbox.KioskServices.Proxy." + cls);
        var m = target.GetType().GetMethods(All).First(x => x.Name == method && x.GetParameters().Length == args.Length + 1);
        var cbType = m.GetParameters().Last().ParameterType;
        var done = new ManualResetEvent(false);
        object result = null;
        var p = Expression.Parameter(cbType.GetMethod("Invoke").GetParameters()[0].ParameterType, "r");
        Action<object> handler = r => { result = r; done.Set(); };
        var cb = Expression.Lambda(cbType, Expression.Invoke(Expression.Constant(handler), Expression.Convert(p, typeof(object))), p).Compile();
        try { m.Invoke(target, args.Concat(new object[] { cb }).ToArray()); }
        catch (Exception e) { Report(name, false, "EXCEPTION " + (e.InnerException ?? e)); failures++; return; }
        if (!done.WaitOne(60000)) { Report(name, false, "no callback in 60 s"); failures++; return; }
        bool success = (bool)result.GetType().GetProperty("Success").GetValue(result, null);
        Report(name, success, "errors=" + Dump.Of(result.GetType().GetProperty("Errors").GetValue(result, null), 3) +
                              " props=" + Dump.Of(result.GetType().GetProperty("Properties").GetValue(result, null), 4));
        if (success != expectSuccess) { failures++; Console.WriteLine("  ^ expected success=" + expectSuccess); }
        else if (loggedExceptions != before) { failures++; Console.WriteLine("  ^ the proxy logged an exception"); }
    }

    static object E(string type, string value) { return Enum.Parse(T(type), value); }

    public static int Run(string ksDll, string url, string protocol, string only)
    {
        ks = Assembly.LoadFrom(ksDll);
        Assembly.Load("Redbox.KioskEngine.ComponentModel");  // from bin\ (the application base)
        Assembly.Load("Redbox.Lua");
        Setup(url, protocol);
        long reservation = long.Parse(Environment.GetEnvironmentVariable("HARNESS_RESERVATION") ?? "0");
        string k = Kiosk.ToString(), now = DateTime.Now.ToString(), email = "harness2016-" + Guid.NewGuid().ToString("N").Substring(0, 8) + "@example.test";
        var cases = new List<KeyValuePair<string, Action>>();
        Action<string, bool, string, string, object[]> add = (n, ok, c, m, a) => cases.Add(new KeyValuePair<string, Action>(n, () => Run(n, ok, c, m, a)));

        add("GetPendingStates", true, "StoreServicesProxy", "GetPendingStates", new object[0]);
        add("GetPendingBanners", true, "StoreServicesProxy", "GetPendingBanners", new object[] { 14 });
        add("GetPendingKiosks", true, "StoreServicesProxy", "GetPendingStores", new object[] { 3, 14 });
        add("GetOpsMarket", true, "StoreServicesProxy", "GetOpsMarket", new object[] { k });
        add("Authenticate", true, "AuthenticationServicesProxy", "Authenticate", new object[] { k, "tech", "s3cret-Pw!", false });
        add("DiscountCodeValidation", true, "PromotionServicesProxy", "ValidatePromotionCode", new object[] { k, "WELCOME" });
        add("EmailConfirm", true, "MarketingOptinServicesProxy", "SendEmailConfirm", new object[] { k, email });
        cases.Add(new KeyValuePair<string, Action>("KioskOptIn", () =>
        {
            var promo = Instance("Redbox.KioskServices.Proxy.PromotionServicesProxy");
            var item = promo.GetType().GetMethod("NewOptInItem", All).Invoke(promo, null);
            foreach (var kv in new Dictionary<string, object> { { "OptInMethodData", email }, { "CampaignName", "Harness" }, { "TemplateName", "t" } })
            {
                var pr = item.GetType().GetProperty(kv.Key, All);
                if (pr != null && pr.CanWrite) pr.SetValue(item, kv.Value, null);
            }
            var list = (IList)Activator.CreateInstance(typeof(List<>).MakeGenericType(T("Redbox.KioskServices.Proxy.ComponentModel.IOptInItem")));
            list.Add(item);
            Run("KioskOptIn", true, "PromotionServicesProxy", "KioskOptIn", k, list, false);
        }));
        add("Authorize", true, "MerchantServicesProxy", "Authorize", new object[] { SessionId });
        add("GCAuthorize", true, "MerchantServicesProxy", "GCAuthorize", new object[] { SessionId });
        add("Reconcile", true, "MerchantServicesProxy", "Reconcile", new object[] { SessionId });
        add("ResvAuth", true, "MerchantServicesProxy", "ReservationAuthorize", new object[] { SessionId, reservation });
        add("PickupValidation", true, "MerchantServicesProxy", "ValidatePickup", new object[] { SessionId, new List<long> { reservation } });
        add("GetCustomer", true, "DigitalServicesProxy", "GetCustomer", new object[] { SessionId, "HARNESS-CARD-" + Kiosk });
        add("RBiFreeTrialRequest", true, "DigitalServicesProxy", "CreateRBIFreeTrial", new object[] { SessionId, email, "Accepted" });
        add("GetCustomerCredits", true, "DigitalServicesProxy", "GetCustomerCredits", new object[] { SessionId });
        add("Return", true, "InventoryServicesProxy", "Return", new object[] { k, E("Redbox.KioskServices.Proxy.ComponentModel.ReturnType", "KioskReturn"), "123456789", 3, 12, now });
        cases.Add(new KeyValuePair<string, Action>("SoftSync", () =>
        {
            var inv = Instance("Redbox.KioskServices.Proxy.InventoryServicesProxy");
            var it = inv.GetType().GetMethod("CreateInventoryItem", All).Invoke(inv, new object[] { "123456789", (int?)1, (int?)2,
                E("Redbox.KioskServices.Proxy.ComponentModel.InventoryItemStatus", "In") });
            var itf = T("Redbox.KioskServices.Proxy.ComponentModel.IInventoryItem");
            var l = (IList)Activator.CreateInstance(typeof(List<>).MakeGenericType(itf));
            l.Add(it);
            Run("SoftSync", true, "InventoryServicesProxy", "SnapshotImmediate", k, Activator.CreateInstance(typeof(ReadOnlyCollection<>).MakeGenericType(itf), l));
        }));
        add("RentalCount", true, "InventoryServicesProxy", "GetRentalCount", new object[] { k });
        add("UnplayableReturn", true, "InventoryServicesProxy", "MarkAsUnplayable", new object[] { k, "123456789", email, now });
        add("DiscFraud", true, "InventoryServicesProxy", "UpdateFraudStatus", new object[] { k, "123456789", "true", now });
        add("DiscAction", true, "InventoryServicesProxy", "FmaDiscAction", new object[] { k, "tech", "thin", now,
            new List<Dictionary<string, string>> { new Dictionary<string, string> { { "Barcode", "123456789" }, { "Action", "Remove" } } } });
        add("KioskAlert", true, "HealthServicesProxy", "SendAlert", new object[] { k, "TitleEmpty", "X", "harness alert", DateTime.Now });
        add("AuditEvent", true, "HealthServicesProxy", "SendAuditEvent", new object[] { k, "Login", DateTime.Now, "tech", "harness", "FieldMaintenance" });
        cases.Add(new KeyValuePair<string, Action>("KioskPing", () =>
        {
            // Ping has no callback: it sends KioskPing and updates the online status from the reply.
            int before = loggedExceptions;
            var health = Instance("Redbox.KioskServices.Proxy.HealthServicesProxy");
            health.GetType().GetMethod("Ping", All).Invoke(health, new object[] { k });
            Thread.Sleep(3000);
            bool ok = loggedExceptions == before;
            Report("KioskPing", ok, "(no callback; the server's events show the message)");
            if (!ok) failures++;
        }));

        foreach (var c in cases)
            if (string.IsNullOrEmpty(only) || only.Split(',').Contains(c.Key)) c.Value();
        Thread.Sleep(2000);
        Console.WriteLine("QUEUED " + string.Join(",", Queued));
        Console.WriteLine("FAILURES " + failures);
        return failures == 0 ? 0 : 1;
    }
}
