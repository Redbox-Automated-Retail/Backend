// Every MessageType the 1.80 Redbox.KioskServices.Proxy ("2024" build, kop-v2-engine-Ultima) sends, through the
// proxy's own service methods: they build the request from engine state, call KioskServicesProxy.ExecuteMessage
// (the real transport), and parse the reply into IRemoteServiceResult / engine model objects. Engine state comes
// from EngineStubs (a session with a card and a one-title cart); queued messages are forwarded as the engine's
// KioskServices queue processor does (the queued object as JSON, through ExecuteMessage).
using System;
using System.Collections;
using System.Collections.Generic;
using System.Collections.ObjectModel;
using System.Linq;
using System.Net;
using System.Reflection;
using System.Security;
using System.Threading;
using Redbox.Core;
using Redbox.KioskEngine.ComponentModel;
using Redbox.KioskEngine.ComponentModel.KioskServices;
using Redbox.KioskServices.Proxy;
using Redbox.KioskServices.Proxy.ComponentModel;

public class CaseSettings : IMachineSettingsStore
{
    public static readonly Dictionary<string, object> Values = new Dictionary<string, object>(StringComparer.OrdinalIgnoreCase);
    public T GetValue<T>(string path, string key) { return GetValue(path, key, default(T)); }
    public T GetValue<T>(string path, string key, T defaultValue)
    {
        object v;
        if (Values.TryGetValue(path + "|" + key, out v) || Values.TryGetValue(key, out v))
        {
            if (v is T) return (T)v;
            try { return (T)Convert.ChangeType(v, typeof(T)); } catch { return defaultValue; }
        }
        return defaultValue;
    }
    public void SetValue<T>(string path, string key, T value) { Values[path + "|" + key] = value; }
    public void RemoveKey(string path) { }
    public void RemoveValue(string path, string key) { }
}

public static class KioskServicesCases
{
    public const int Kiosk = 990010;
    static readonly Guid SessionId = Guid.NewGuid();
    static readonly List<string> Queued = new List<string>();
    static int failures;
    static int loggedExceptions;

    static Type T(string fullName)
    {
        foreach (var a in AppDomain.CurrentDomain.GetAssemblies())
        {
            Type t = null;
            try { t = a.GetType(fullName, false); } catch { }
            if (t != null) return t;
        }
        throw new Exception("type not found: " + fullName);
    }

    static void Stub(string iface)
    {
        ServiceLocator.Instance.AddService(T(iface), StubFactory.Create(T(iface)));
    }

    public static void Setup(string url, string protocol)
    {
        var s = CaseSettings.Values;
        s["KioskServicesURL"] = url;
        s["KioskServicesProtocol"] = (KioskServicesProtocol)Enum.Parse(typeof(KioskServicesProtocol), protocol);
        s["KioskServicesTimeout"] = 30000;
        foreach (var k in new[] { "LiveCall", "GCLiveCall" }) s[k] = true;
        s["IsServerBusy"] = false;  // defaults to true until a reply says otherwise (HealthServicesProxy.UpdateOnlineStatus)
        s["Store|ID"] = Kiosk.ToString();
        s["ID"] = Kiosk.ToString();
        ServiceLocator.Instance.AddService(typeof(IMachineSettingsStore), new CaseSettings());
        ServicePointManager.ServerCertificateValidationCallback = (a, b, c, d) => true;  // harness only: local CA
        ServicePointManager.SecurityProtocol = SecurityProtocolType.Tls12;

        // Engine services the proxy asks for, as stubs.
        foreach (var n in new[] {
            "Redbox.KioskEngine.ComponentModel.ICallbackService", "Redbox.KioskEngine.ComponentModel.IMacroService",
            "Redbox.KioskEngine.ComponentModel.IQueueService", "Redbox.KioskEngine.ComponentModel.IShoppingSessionService",
            "Redbox.KioskEngine.ComponentModel.IResourceBundleService", "Redbox.KioskEngine.ComponentModel.IViewService",
            "Redbox.KioskEngine.ComponentModel.IObjectCacheService", "Redbox.KioskEngine.ComponentModel.IKernelExtensionService",
            "Redbox.KioskEngine.ComponentModel.IDeviceServiceClientHelper",
            "Redbox.Rental.Model.IConfiguration", "Redbox.Rental.Model.IApplicationState", "Redbox.Rental.Model.IStoreManager",
            "Redbox.Rental.Model.KioskClientService.IKioskClientService", "Redbox.Rental.Model.Session.IRentalSessionService",
            "Redbox.Rental.Model.ShoppingCart.IRentalShoppingCartService", "Redbox.Rental.Model.DataService.IDataService" })
        {
            try { Stub(n); } catch (Exception e) { Console.WriteLine("note: no stub for " + n + ": " + e.Message); }
        }
        // Lua tables (results handed to the engine's Lua scripts) need a real Lua state, as the engine's KernelService has.
        Stub("Redbox.KioskEngine.ComponentModel.IKernelService");
        var vm = Activator.CreateInstance(T("Redbox.Lua.LuaVirtualMachine"));
        var tableType = T("Redbox.Lua.LuaTable");
        StubHub.Set("IKernelService.NewTable", a => { lock (vm) return Activator.CreateInstance(tableType, vm); });
        StubHub.Set("ICallbackService.EnqueueCallback", a => { var e = (ICallbackEntry)a[0]; ThreadPool.QueueUserWorkItem(_ => e.Invoke()); return null; });
        StubHub.Set("IMacroService.get_Item", "1.80.0.12");
        // The engine's KioskServices queue processor sends the queued object's JSON through ExecuteMessage.
        Func<object[], object> queue = a =>
        {
            string type = (string)a[1];
            string json = a[2] is string ? (string)a[2] : Redbox.Core.ObjectExtensions.ToJson(a[2]);  // QueueService.ToEncryptedMessage
            IDictionary<string, object> reply = KioskServicesProxy.Instance.ExecuteMessage(json);
            lock (Queued) Queued.Add(type);
            Report("queued " + type, reply != null && reply.ContainsKey("Success") && reply["Success"] is bool && (bool)reply["Success"],
                   Dump.Of(reply, 3));
            return Activator.CreateInstance(T("Redbox.KioskEngine.ComponentModel.ErrorList"));
        };
        StubHub.Set("IQueueService.Enqueue", queue);
        StubHub.Set("IQueueService.EnqueueOnlyOne", queue);

        // A session: card holder, encrypted swipe, a one-title rental cart.
        StubHub.Set("IShoppingSession.get_StoreNumber", Kiosk.ToString());
        StubHub.Set("IShoppingSession.get_Id", SessionId);
        StubHub.Set("IShoppingCart.get_PostalCode", "60601");
        foreach (var p in new[] { "IEncryptedTrackData" })
        {
            StubHub.Set(p + ".get_FirstName", "Test");
            StubHub.Set(p + ".get_LastName", "Customer");
            StubHub.Set(p + ".get_LastFour", "4242");
            StubHub.Set(p + ".get_FirstSix", "424242");
            StubHub.Set(p + ".get_ExpiryYear", "39");
            StubHub.Set(p + ".get_ExpiryMonth", "12");
            StubHub.Set(p + ".get_CardHashId", "HARNESS-CARD-" + Kiosk);
            StubHub.Set(p + ".get_KSN", "FFFF9876543210E00001");
            StubHub.Set(p + ".get_ICEncData", "00112233445566778899AABBCCDDEEFF");
        }
        var trackType = T("Redbox.KioskEngine.ComponentModel.TrackData.IEncryptedTrackData");
        StubHub.Set("ISession.get_TrackData", StubFactory.Create(trackType));
        var itemType = T("Redbox.Rental.Model.ShoppingCart.IRentalShoppingCartTitleItem");
        StubHub.Set("IRentalShoppingCartTitleItem.get_ProductId", 11L);
        StubHub.Set("IRentalShoppingCartTitleItem.get_Barcode", "123456789");
        StubHub.Set("IPricingRecord.get_InitialNight", 1.85m);
        StubHub.Set("IPricingRecord.get_ExtraNight", 1.85m);
        StubHub.Set("IPricingRecord.get_NonReturn", 29.60m);
        StubHub.Set("IUpsellInfo.GetUpsellItemByRentalShoppingCartItem", (object)null);
        StubHub.Set("IRentalShoppingCart.get_Promotion", (object)null);
        StubHub.Set("IRentalShoppingCart.get_ServiceFee", (object)null);
        StubHub.Set("IRentalShoppingCart.get_ShoppingSessionId", SessionId);
        var items = (IList)Activator.CreateInstance(typeof(List<>).MakeGenericType(itemType));
        items.Add(StubFactory.Create(itemType));
        StubHub.Set("IRentalShoppingCart.get_TitleItems", items);
        var groupType = T("Redbox.Rental.Model.ShoppingCart.ISubTotalGroup");
        var groups = (IList)Activator.CreateInstance(typeof(List<>).MakeGenericType(groupType));
        groups.Add(StubFactory.Create(groupType));
        StubHub.Set("ISubTotalGroup.get_SubTotal", 1.85m);
        StubHub.Set("ISubTotalGroup.get_Amount", 1.85m);
        StubHub.Set("ISubTotalGroup.get_GrantTotal", 1.85m);
        StubHub.Set("ITotals.get_SubTotalGroups", groups);

        // The proxy's own log: printed with HARNESS_LOG=1; an exception it logs (and swallows) fails the case.
        bool verbose = Environment.GetEnvironmentVariable("HARNESS_LOG") == "1";
        StubHub.Set("ILogger.Log", a =>
        {
            string line = a[0] + (a.Length > 1 && a[1] is Exception ? " | " + a[1] : "");
            if (a.Length > 1 && a[1] is Exception) Interlocked.Increment(ref loggedExceptions);
            if (verbose || a.Length > 1 && a[1] is Exception) Console.WriteLine("  log: " + line);
            return null;
        });
        LogHelper.Instance.Loggers.Add(StubFactory.Create<ILogger>());
        ServiceLocator.Instance.AddStoreServices();
        ServiceLocator.Instance.AddAuthenticationServices();
        ServiceLocator.Instance.AddMarketingServices();
        ServiceLocator.Instance.AddInventoryServices();
        ServiceLocator.Instance.AddService(typeof(IHealthServices), HealthServicesProxy.Instance);
    }

    static void Report(string name, bool success, string detail)
    {
        Console.WriteLine("CASE " + name + " success=" + success + " " + (detail.Length > 1500 ? detail.Substring(0, 1500) + "…" : detail));
    }

    /// <summary>Run one proxy method; its RemoteServiceCallback result is printed.</summary>
    static void Run(string name, bool expectSuccess, Action<RemoteServiceCallback> call)
    {
        Run(name, expectSuccess, call, null);
    }

    static void Run(string name, bool expectSuccess, Action<RemoteServiceCallback> call, Func<IRemoteServiceResult, string> check)
    {
        int exceptionsBefore = loggedExceptions;
        var done = new ManualResetEvent(false);
        IRemoteServiceResult result = null;
        Exception error = null;
        try { call(r => { result = r; done.Set(); }); }
        catch (Exception e) { error = e; done.Set(); }
        if (!done.WaitOne(60000)) error = new TimeoutException("no callback in 60 s");
        if (error != null)
        {
            Report(name, false, "EXCEPTION " + error);
            failures++;
            return;
        }
        object profile;
        if (cpn == null && result.Properties != null && result.Properties.TryGetValue("CustomerProfileNumber", out profile) && profile != null)
            cpn = profile.ToString();  // the loyalty profile UpdateAccount created, used by the estimates
        string detail = "errors=" + Dump.Of(result.Errors, 3) + " props=" + Dump.Of(result.Properties, 4);
        Report(name, result.Success, detail);
        if (result.Success != expectSuccess) { failures++; Console.WriteLine("  ^ expected success=" + expectSuccess); }
        else if (loggedExceptions != exceptionsBefore) { failures++; Console.WriteLine("  ^ the proxy logged an exception"); }
        else if (check != null)
        {
            string problem = check(result);
            Console.WriteLine("  check: " + (problem ?? "ok"));
            if (problem != null) failures++;
        }
    }

    static object Svc(string iface) { return ServiceLocator.Instance.GetService(T(iface)); }

    static object Call(object target, string method, params object[] args)
    {
        var m = target.GetType().GetMethods().First(x => x.Name == method && x.GetParameters().Length >= args.Length);
        var ps = m.GetParameters();
        var full = new object[ps.Length];
        for (int i = 0; i < ps.Length; i++) full[i] = i < args.Length ? args[i] : ps[i].DefaultValue;
        return m.Invoke(target, full);
    }

    public static int RunAll(string url, string protocol, string only)
    {
        if (only != null && only.StartsWith("types:"))
        {
            var re = new System.Text.RegularExpressions.Regex(only.Substring(6));
            foreach (var a in AppDomain.CurrentDomain.GetAssemblies())
            {
                Type[] ts;
                try { ts = a.GetTypes(); } catch (ReflectionTypeLoadException e) { ts = e.Types.Where(x => x != null).ToArray(); }
                foreach (var t in ts) if (re.IsMatch(t.Name)) Console.WriteLine(t.FullName + "  [" + a.GetName().Name + "]");
            }
            return 0;
        }
        Setup(url, protocol);
        var store = Svc("Redbox.KioskServices.Proxy.ComponentModel.IStoreServices");
        var auth = Svc("Redbox.KioskServices.Proxy.ComponentModel.IAuthenticationServices");
        var marketing = Svc("Redbox.KioskServices.Proxy.ComponentModel.IMarketingServices");
        var inventory = Svc("Redbox.KioskServices.Proxy.ComponentModel.IInventoryServices");
        var merchant = MerchantServicesProxy.Instance;
        string ks = Kiosk.ToString(), sid = SessionId.ToString();
        var cases = new List<KeyValuePair<string, Action>>();
        Action<string, bool, Action<RemoteServiceCallback>> add = (n, ok, f) => cases.Add(new KeyValuePair<string, Action>(n, () => Run(n, ok, f)));

        add("GetPendingStates", true, cb => Call(store, "GetPendingStates", cb));
        add("GetPendingBanners", true, cb => Call(store, "GetPendingBanners", 14, cb));
        add("GetPendingKiosks", true, cb => Call(store, "GetPendingStores", 3, 14, cb));
        add("GetOpsMarket", true, cb => Call(store, "GetOpsMarket", ks, cb));
        add("Authenticate", true, cb => Call(auth, "Authenticate", ks, "tech", "s3cret-Pw!", false, cb));
        add("DiscountCodeValidation", true, cb => PromotionServices.Instance.ValidatePromotionCode(ks, "WELCOME", cb));
        add("DiscountCodeValidation-unknown", false, cb => PromotionServices.Instance.ValidatePromotionCode(ks, "NO-SUCH-" + Guid.NewGuid().ToString("N").Substring(0, 6), cb));
        cases.Add(new KeyValuePair<string, Action>("EmailConfirm", () =>
        {
            var done = new ManualResetEvent(false);
            IEmailConfirmResult r = null;
            Call(marketing, "SendEmailConfirm", (long)Kiosk, "harness@example.test", null,
                 new SendEmailConfirmCallback(x => { r = x; done.Set(); }));
            bool ok = done.WaitOne(60000) && r != null && r.Success;
            Report("EmailConfirm", ok, Dump.Of(r, 3));
            if (!ok) failures++;
        }));
        add("KioskOptIn", true, cb =>
        {
            var item = Call(marketing, "NewOptInItem");
            SetProp(item, "OptInMethodData", "harness@example.test");
            SetProp(item, "CampaignName", "Harness");
            SetProp(item, "TemplateName", "t");
            var list = (IList)Activator.CreateInstance(typeof(List<>).MakeGenericType(T("Redbox.KioskEngine.ComponentModel.KioskServices.IOptInItem")));
            list.Add(item);
            Call(marketing, "KioskOptIn", (long)Kiosk, list, cb);
        });
        add("UpdateAccount", true, cb =>
        {
            var pin = new SecureString();
            foreach (var c in "4321") pin.AppendChar(c);
            Call(PersonalizationServicesProxy.Instance, "UpdateAccount", Kiosk, null, "harness-" + Guid.NewGuid().ToString("N").Substring(0, 8) + "@example.test",
                 "312555" + new Random().Next(1000, 9999), pin, true, false, cb);
        });
        add("EstimateAccrual", true, cb => LoyaltyServicesProxy.Instance.EstimatePointsAccrued(Cpn(), cb));
        add("EstimateRedemption", true, cb => LoyaltyServicesProxy.Instance.EstimateRedemptionPoints(Cpn(), cb));
        cases.Add(new KeyValuePair<string, Action>("GetCustomerOffers", () => Run("GetCustomerOffers", true,
            cb => CustomerOfferProxy.Instance.GetOffers(ks, "en-US", "CartView", sid, cb), OfferExpiry)));
        add("PreAuth", true, cb => merchant.PreAuth(SessionId, null, cb));
        add("Authorize", true, cb => Call(merchant, "Authorize", SessionId, Enum.ToObject(T("Redbox.KioskEngine.ComponentModel.KioskServices.AuthorizeType"), 0), null, cb));
        add("GCAuthorize", true, cb => Call(merchant, "Authorize", SessionId, Enum.ToObject(T("Redbox.KioskEngine.ComponentModel.KioskServices.AuthorizeType"), 1), null, cb));
        add("Reconcile", true, cb => merchant.Reconcile(sid, null, cb));
        add("ResvAuth", true, cb => merchant.ReservationAuthorize(sid, null, Reservation(), cb));
        add("PickupValidation", true, cb => merchant.ValidatePickup(sid, new List<long> { Reservation() }, cb));
        add("PickupAuthorize", true, cb => merchant.PickupAuthorize(sid, null, Reservation(), false, cb));
        add("PickupReconcile", true, cb => merchant.PickupReconcile(sid, null, Reservation(), new Dictionary<string, ReadOnlyCollection<Redbox.BrokerServices.Proxy.ComponentModel.IProductItem>>(), cb));
        add("ModResvPickup", true, cb => merchant.ModResvPickup(ks, "harness@example.test", Reservation(), sid, true, cb));
        add("Return", true, cb => Call(inventory, "Return", ks, Enum.ToObject(T("Redbox.KioskServices.Proxy.ComponentModel.ReturnType"), 6), "123456789", 3, 12,
                                       DateTime.Now.ToString(), false, null, cb));
        add("SoftSync", true, cb =>
        {
            var it = Call(inventory, "CreateInventoryItem", "123456789", (int?)1, (int?)2, Enum.ToObject(T("Redbox.KioskServices.Proxy.ComponentModel.InventoryItemStatus"), 0));
            var l = (IList)Activator.CreateInstance(typeof(List<>).MakeGenericType(T("Redbox.KioskServices.Proxy.ComponentModel.IInventoryItem")));
            l.Add(it);
            var ro = Activator.CreateInstance(typeof(ReadOnlyCollection<>).MakeGenericType(T("Redbox.KioskServices.Proxy.ComponentModel.IInventoryItem")), l);
            Call(inventory, "SnapshotImmediate", ks, ro, cb);
        });
        add("DiscFraud", true, cb => Call(inventory, "UpdateFraudStatus", ks, "123456789", "true", DateTime.Now.ToString(), cb));
        add("DiscAction", true, cb => Call(inventory, "FmaDiscAction", ks, "tech", "thin", DateTime.Now.ToString(),
            new List<Dictionary<string, string>> { new Dictionary<string, string> { { "Barcode", "123456789" }, { "Action", "Remove" } } }, cb));

        foreach (var c in cases)
            if (string.IsNullOrEmpty(only) || only.Split(',').Contains(c.Key)) c.Value();
        Thread.Sleep(2000);  // queued forwards finish on worker threads
        Console.WriteLine("QUEUED " + string.Join(",", Queued));
        Console.WriteLine("FAILURES " + failures);
        return failures == 0 ? 0 : 1;
    }

    /// <summary>The setup's offer must arrive with its expiry (a .NET date literal the client's raw cast reads).</summary>
    static string OfferExpiry(IRemoteServiceResult r)
    {
        string expected = Environment.GetEnvironmentVariable("HARNESS_OFFER_EXPIRES");
        if (string.IsNullOrEmpty(expected)) return null;
        object offers;
        if (!r.Properties.TryGetValue("NewOffers", out offers) || !(offers is IEnumerable)) return "no NewOffers";
        foreach (var o in (IEnumerable)offers)
        {
            if (Convert.ToInt64(o.GetType().GetProperty("CampaignId").GetValue(o, null)) != Kiosk) continue;
            var got = (DateTime)o.GetType().GetProperty("ExpirationDate").GetValue(o, null);
            var want = DateTime.Parse(expected, null, System.Globalization.DateTimeStyles.AdjustToUniversal);
            Console.WriteLine("  offer ExpirationDate " + got.ToString("o") + " (" + got.Kind + "), expected " + want.ToString("o") + " UTC");
            return got.ToUniversalTime() == want ? null : "ExpirationDate mismatch";
        }
        return "harness offer not in NewOffers";
    }

    static void SetProp(object o, string name, object value)
    {
        var p = o.GetType().GetProperty(name);
        if (p != null && p.CanWrite) p.SetValue(o, value, null);
    }

    static string cpn;
    /// <summary>A loyalty profile created through the proxy (UpdateAccount), reused for the estimates.</summary>
    static string Cpn()
    {
        return cpn ?? (cpn = Environment.GetEnvironmentVariable("HARNESS_CPN"));
    }

    static long reservation;
    static long Reservation()
    {
        if (reservation == 0) long.TryParse(Environment.GetEnvironmentVariable("HARNESS_RESERVATION") ?? "0", out reservation);
        return reservation;
    }


}
