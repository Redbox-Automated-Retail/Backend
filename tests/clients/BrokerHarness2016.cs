// The 2014-2016 engines' reservation broker client, Redbox.DVD.BrokerServices.dll (a kernel extension, with
// GenuineChannels and the proxy ILMerged in and internalized), against rb-remoting. Everything in it is internal,
// so it is driven by reflection: ReservationServicesProxy.Register(store, prices, ReservationRequestCallback3), and
// the callback answers the broker's Reserve3 as the engine's rental code would (a barcode per title, "empty" for
// titles ending in 0). Engine services are EngineStubs stubs: settings (the broker URL), callback queue,
// configuration.
using System;
using System.Collections;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Linq.Expressions;
using System.Reflection;
using System.Threading;
using Redbox.Core;

public static class BrokerHarness2016
{
    static Assembly broker;
    const BindingFlags All = BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance | BindingFlags.Static;

    static Type T(string name)
    {
        var t = broker.GetType(name, false);
        if (t != null) return t;
        foreach (var a in AppDomain.CurrentDomain.GetAssemblies())
        {
            try { t = a.GetType(name, false); } catch { }
            if (t != null) return t;
        }
        throw new Exception("type not found: " + name);
    }

    /// <summary>Built as an exe in the engine's bin\ (run-broker-harness-2016.ps1), so that, as for kioskengine.exe, the
    /// application base is bin\: the binary formatter binds KioskShared there by simple name.</summary>
    public static int Main(string[] argv)
    {
        try { return Run(argv[0], argv[1], int.Parse(argv[2]), int.Parse(argv[3])); }
        catch (Exception e) { Console.WriteLine((e.InnerException ?? e).ToString()); return 2; }
    }

    public static int Run(string brokerDll, string url, int kioskId, int seconds)
    {
        broker = Assembly.LoadFrom(brokerDll);
        Assembly.Load("Redbox.KioskEngine.ComponentModel");  // from bin\ (the application base)
        StubHub.Typed["IMachineSettingsStore.GetValue"] = (ret, a) =>
        {
            string key = (string)a[1];
            if (key == "BrokerServicesURL") return url;
            return a.Length > 2 ? a[2] : null;
        };
        StubHub.Set("ICallbackService.EnqueueCallback", a =>
        {
            var entry = a[0];
            ThreadPool.QueueUserWorkItem(_ =>
            {
                try { entry.GetType().GetMethod("Invoke").Invoke(entry, null); }
                catch (Exception e) { Console.WriteLine("EVENT callback-error " + (e.InnerException ?? e)); }
            });
            return null;
        });
        foreach (var n in new[] { "Redbox.KioskEngine.ComponentModel.IMachineSettingsStore",
                                  "Redbox.KioskEngine.ComponentModel.ICallbackService",
                                  "Redbox.KioskEngine.ComponentModel.IConfigurationService" })
            ServiceLocator.Instance.AddService(T(n), StubFactory.Create(T(n)));

        var rsp = T("Redbox.BrokerServices.Proxy.ReservationServicesProxy");
        var proxy = rsp.GetProperty("Instance", All).GetValue(null, null);
        var cbType = T("Redbox.BrokerServices.Proxy.ComponentModel.ReservationRequestCallback3");
        var register = rsp.GetMethods(All).First(m => m.Name == "Register" && m.GetParameters()[2].ParameterType == cbType);

        // A delegate of the internal callback type that hands its arguments to Reserve(object[]).
        var invoke = cbType.GetMethod("Invoke");
        var ps = invoke.GetParameters().Select(p => Expression.Parameter(p.ParameterType, p.Name)).ToArray();
        var args = Expression.NewArrayInit(typeof(object), ps.Select(p => Expression.Convert(p, typeof(object))));
        var body = Expression.Convert(Expression.Call(typeof(BrokerHarness2016).GetMethod("Reserve"), args), invoke.ReturnType);
        var callback = Expression.Lambda(cbType, body, ps).Compile();

        var prices = new Dictionary<string, IDictionary<string, IDictionary<string, decimal>>>
        {
            { "rental", new Dictionary<string, IDictionary<string, decimal>> { { "dvd", new Dictionary<string, decimal> { { "initial", 1.20m } } } } }
        };
        register.Invoke(proxy, new object[] { kioskId.ToString(), prices, callback });
        Console.WriteLine("EVENT ready");
        Thread.Sleep(seconds * 1000);
        var bsp = T("Redbox.BrokerServices.Proxy.BrokerServicesProxy");
        bsp.GetMethod("Reset", All).Invoke(bsp.GetProperty("Instance", All).GetValue(null, null), null);
        Console.WriteLine("EVENT done");
        return 0;
    }

    /// <summary>The engine side of Reserve3: hold a disc per title (titles ending in 0 are out of stock).</summary>
    public static object Reserve(object[] a)
    {
        long reference = (long)a[2];
        var titles = (IEnumerable)a[18];
        Console.WriteLine("EVENT reserve ref=" + reference + " email=" + a[1] + " card=" + a[0] + " grandTotal=" + a[10] +
                          " reserveDate=" + ((DateTime)a[3]).ToString("o") + " titles=" + titles.Cast<object>().Count());
        var result = Activator.CreateInstance(T("Redbox.BrokerServices.Proxy.LocalReservationResult"), true);
        var items = (IList)result.GetType().GetProperty("Items").GetValue(result, null);
        foreach (var t in titles)
        {
            int id = (int)t.GetType().GetProperty("TitleID").GetValue(t, null);
            var item = Activator.CreateInstance(T("Redbox.BrokerServices.Proxy.ReservedItem"), true);
            item.GetType().GetProperty("ProductId").SetValue(item, id, null);
            item.GetType().GetProperty("Barcode").SetValue(item, id % 10 == 0 ? "empty" : "H" + reference + "-" + id, null);
            items.Add(item);
        }
        result.GetType().GetProperty("Success").SetValue(result, true, null);
        return result;
    }
}
