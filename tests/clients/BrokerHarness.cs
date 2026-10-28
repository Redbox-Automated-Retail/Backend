// Plays a kiosk engine against the self-hosted reservation broker using the real decompiled
// Redbox.BrokerServices.Proxy (BrokerServicesProxy + ReservationServicesProxy) and GenuineChannels on .NET Framework.
// Compiled at run time by run-broker-harness.ps1 (C# 5).
//
// The engine services the proxy asks the ServiceLocator for are stood in by fakes:
//   IMachineSettingsStore  "Remote Services\Reservation Services\BrokerServicesURL" (and defaults for the rest)
//   IConfiguration         UseLegacyBrokerService = true
//   ICallbackService       runs each callback entry at once on a worker thread (the engine runs them on its UI thread)
// The rental side's reservation/cancel callbacks are local functions that "reserve" a disc per title.
// Every callback the broker makes is printed as a line starting with "EVENT ".
using System;
using System.Collections.Generic;
using System.Reflection;
using System.Reflection.Emit;
using System.Threading;
using Redbox.BrokerServices.Proxy;
using Redbox.BrokerServices.Proxy.ComponentModel;
using Redbox.Core;
using Redbox.KioskEngine.ComponentModel;
using Redbox.Services.KioskBrokerServices.KioskShared.DomainObjects;

public class FakeSettings : IMachineSettingsStore
{
    public static string BrokerUrl;
    public T GetValue<T>(string path, string key) { return GetValue(path, key, default(T)); }
    public T GetValue<T>(string path, string key, T defaultValue)
    {
        if (key == "BrokerServicesURL") return (T)(object)BrokerUrl;
        return defaultValue;
    }
    public void SetValue<T>(string path, string key, T value) { }
    public void RemoveKey(string path) { }
    public void RemoveValue(string path, string key) { }
}

/// <summary>Runs every callback entry at once on a worker thread (the engine runs them on its UI thread).</summary>
public class FakeCallbacks : ICallbackService
{
    public int QueueCount { get { return 0; } }
    public void Reset() { }
    public void Flush() { }
    public void Resume() { }
    public void Suspend() { }
    public bool InvokeNextCallback() { return false; }
    public void EnqueueCallback(ICallbackEntry entry)
    {
        ThreadPool.QueueUserWorkItem(_ =>
        {
            try { entry.Invoke(); }
            catch (Exception e) { Console.WriteLine("EVENT callback-error " + e); }
        });
    }
    public DateTime? GetEnqueuedDateTimeFromNextCallbackEntry() { return null; }
    public ICallbackServiceStatistics GetStatics() { return null; }
    public void ResetStatistics() { }
    public void LogStatistics() { }
    public void LogCallbackTracking() { }
}

/// <summary>Builds a class implementing an interface whose members return defaults, except chosen bool getters.</summary>
public static class InterfaceStub
{
    public static object Create(Type iface, HashSet<string> trueGetters)
    {
        var ab = AppDomain.CurrentDomain.DefineDynamicAssembly(new AssemblyName("Stub" + iface.Name), AssemblyBuilderAccess.Run);
        var tb = ab.DefineDynamicModule("m").DefineType(iface.Name + "Stub", TypeAttributes.Public | TypeAttributes.Class);
        tb.AddInterfaceImplementation(iface);
        var all = new List<Type> { iface };
        all.AddRange(iface.GetInterfaces());
        foreach (var it in all)
        {
            foreach (var m in it.GetMethods())
            {
                var ps = m.GetParameters();
                var mb = tb.DefineMethod(it.Name + "." + m.Name, MethodAttributes.Private | MethodAttributes.Virtual |
                    MethodAttributes.Final | MethodAttributes.HideBySig | MethodAttributes.NewSlot, m.ReturnType,
                    Array.ConvertAll(ps, x => x.ParameterType));
                var il = mb.GetILGenerator();
                if (m.ReturnType != typeof(void))
                {
                    if (m.ReturnType == typeof(bool) && trueGetters.Contains(m.Name)) il.Emit(OpCodes.Ldc_I4_1);
                    else
                    {
                        var local = il.DeclareLocal(m.ReturnType);
                        il.Emit(OpCodes.Ldloca_S, local);
                        il.Emit(OpCodes.Initobj, m.ReturnType);
                        il.Emit(OpCodes.Ldloc, local);
                    }
                }
                il.Emit(OpCodes.Ret);
                tb.DefineMethodOverride(mb, m);
            }
        }
        return Activator.CreateInstance(tb.CreateType());
    }
}

public static class BrokerHarness
{
    public static IReservationResult Reserve(string cardId, string email, long referenceNumber, DateTime reserveDate,
        IDictionary<string, List<int>> productsToReserve, bool appliedCredit, bool appliedHiveOnlinePromotion,
        bool appliedTitleMarketing, string customerNumber, decimal discountedSubTotal, decimal grandTotal,
        List<int> historyTitleIDs, decimal hiveOnlinePromotionDiscount, bool isMultiNightPricing,
        int numberOfCreditsRemaining, string promoCode, decimal subTotal, decimal tax,
        List<ReservationTitle3> reservationTitles, bool isGc, List<string> gcIds, List<string> ccIds, decimal taxRate,
        string zip, bool isMdv, bool isLoyaltyRedemption, decimal DefaultServiceFee, decimal ActualServiceFee,
        bool authorizeAtPickup, ReservationCardHash cardHashData, List<ReservationCardHash> reservationCardHashes)
    {
        var result = ReservationServicesProxy.Instance.NewReservationResult();
        result.Items = new List<IReservedItem>();
        foreach (var t in reservationTitles)
        {
            var item = ReservationServicesProxy.Instance.NewReservedItem();
            item.ProductId = t.TitleID;
            // Title ids ending in 0 are "out of stock" to exercise Reservable = false.
            item.Barcode = t.TitleID % 10 == 0 ? "empty" : "H" + referenceNumber + "-" + t.TitleID;
            result.Items.Add(item);
        }
        result.Success = true;
        Console.WriteLine("EVENT reserve ref=" + referenceNumber + " email=" + email + " titles=" + reservationTitles.Count +
                          " grandTotal=" + grandTotal + " reserveDate=" + reserveDate.ToString("o") +
                          " cardHash=" + (cardHashData == null ? "" : cardHashData.Last4));
        return result;
    }

    public static ILocalCancelReservationResult Cancel(long referenceNumber, bool cancelOnServer)
    {
        var r = ReservationServicesProxy.Instance.NewCancelReservationResult();
        r.Success = referenceNumber > 0;
        r.ErrorMessage = r.Success ? null : "unknown reservation";
        Console.WriteLine("EVENT cancel ref=" + referenceNumber);
        return r;
    }

    public static int Run(string url, int kioskId, int seconds)
    {
        FakeSettings.BrokerUrl = url;
        ServiceLocator.Instance.AddService(typeof(IMachineSettingsStore), new FakeSettings());
        ServiceLocator.Instance.AddService(typeof(ICallbackService), new FakeCallbacks());
        ServiceLocator.Instance.AddService(typeof(Redbox.Rental.Model.IConfiguration),
            InterfaceStub.Create(typeof(Redbox.Rental.Model.IConfiguration), new HashSet<string> { "get_UseLegacyBrokerService" }));

        var reserve = (ReservationRequestCallback3)Delegate.CreateDelegate(typeof(ReservationRequestCallback3),
            typeof(BrokerHarness).GetMethod("Reserve"));
        var cancel = (CancelReservationCallback)Delegate.CreateDelegate(typeof(CancelReservationCallback),
            typeof(BrokerHarness).GetMethod("Cancel"));
        var prices = new Dictionary<string, IDictionary<string, IDictionary<string, decimal>>>
        {
            { "rental", new Dictionary<string, IDictionary<string, decimal>> {
                { "dvd", new Dictionary<string, decimal> { { "initial", 1.99m } } } } }
        };
        try
        {
            // What the rental app does at start-up (ReservationService -> IReservationServices.Register).
            ReservationServicesProxy.Instance.Register(kioskId.ToString(), prices, reserve, cancel);
        }
        catch (Exception e)
        {
            // Register reads rental Configuration.Instance, which needs the whole engine; set the callbacks the
            // same way it does and register the broker proxy directly.
            Console.WriteLine("ReservationServicesProxy.Register needs the engine (" + e.GetType().Name + "); registering BrokerServicesProxy directly");
            var t = typeof(ReservationServicesProxy);
            t.GetProperty("RequestCallback3", BindingFlags.Instance | BindingFlags.NonPublic).SetValue(ReservationServicesProxy.Instance, reserve, null);
            t.GetProperty("CancelRequestCallback", BindingFlags.Instance | BindingFlags.NonPublic).SetValue(ReservationServicesProxy.Instance, cancel, null);
            var version = "{\"Runtime\":\"Kiosk Engine\",\"Version\":\"1.88.0.80\",\"PricingPolicy\":{\"rental\":{\"dvd\":{\"initial\":1.99}}},\"GiftCardsEnabled\":false,\"RecoOnPickup\":false,\"AuthorizeAtPickup\":true,\"CancelReservation\":true,\"Features\":1}";
            bool ok = BrokerServicesProxy.Instance.Register(kioskId, version);
            Console.WriteLine("EVENT registered " + ok);
            if (!ok) return 1;
        }
        Console.WriteLine("EVENT ready");
        Thread.Sleep(TimeSpan.FromSeconds(seconds));
        BrokerServicesProxy.Instance.Reset();
        Console.WriteLine("EVENT done");
        return 0;
    }
}
