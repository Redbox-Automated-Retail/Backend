// Drives the 2014 kiosk engine's WCF proxy for the Ticket Controller Service (Redbox.DVD.TicketServices.dll,
// namespace Redbox.TicketServices.Proxy.service) against the self-hosted server. The proxy types are internal,
// so everything goes through reflection. C# 5 (Windows PowerShell 5.1 Add-Type).
using System;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using System.ServiceModel;
using System.ServiceModel.Description;

public static class TicketServicesHarness
{
    const string NS = "Redbox.TicketServices.Proxy.service.";
    static Assembly A;
    static int failures;

    static Type T(string name) { return A.GetType(NS + name, true); }

    static object New(string name, params object[] kv)
    {
        object o = Activator.CreateInstance(T(name), true);
        for (int i = 0; i + 1 < kv.Length; i += 2) Set(o, (string)kv[i], kv[i + 1]);
        return o;
    }

    static void Set(object o, string prop, object value)
    {
        PropertyInfo pi = o.GetType().GetProperty(prop);
        if (pi == null) throw new ArgumentException(o.GetType().Name + " has no property " + prop);
        Type pt = pi.PropertyType;
        if (value != null && pt.IsEnum && !(value.GetType().IsEnum)) value = Enum.Parse(pt, value.ToString());
        pi.SetValue(o, value, null);
    }

    static object Get(object o, string prop)
    {
        return o.GetType().GetProperty(prop).GetValue(o, null);
    }

    static Array Arr(string name, params object[] items)
    {
        Array a = Array.CreateInstance(T(name), items.Length);
        for (int i = 0; i < items.Length; i++) a.SetValue(items[i], i);
        return a;
    }

    static void Header(object req, int kiosk)
    {
        Set(req, "MessageId", Guid.NewGuid());
        Set(req, "EngineVersion", "1.3.9.4");
        Set(req, "BundleVersion", "harness");
        Set(req, "ChannelType", "Kiosk");
        Set(req, "KioskId", kiosk);
        Set(req, "MessageDateTimeUtc", DateTime.Now);  // the client really sends local time here
    }

    static void Check(bool ok, string what)
    {
        Console.WriteLine((ok ? "  ok    " : "  FAIL  ") + what);
        if (!ok) failures++;
    }

    static string Resp(object r)
    {
        return "Success=" + Get(r, "Success") + " Code=" + (Get(r, "Code") ?? "(nil)") + " Message=" +
               (Get(r, "Message") ?? "(nil)");
    }

    static object Call(object client, string op, object req)
    {
        try
        {
            return client.GetType().GetMethod(op).Invoke(client, new object[] { req });
        }
        catch (TargetInvocationException e)
        {
            throw e.InnerException;
        }
    }

    // Calls op expecting a declared fault; returns "FaultException<Type>: message (detail.Message)".
    static string CallFault(object client, string op, object req)
    {
        try
        {
            Call(client, op, req);
            return "no fault";
        }
        catch (FaultException e)
        {
            Type t = e.GetType();
            string detail = "";
            if (t.IsGenericType)
            {
                object d = t.GetProperty("Detail").GetValue(e, null);
                detail = " detail=" + d.GetType().Name + "(" + Get(d, "Message") + ", MessageId=" + Get(d, "MessageId") + ")";
                return "FaultException<" + t.GetGenericArguments()[0].Name + ">: " + e.Message + detail;
            }
            return "FaultException: " + e.Message;
        }
    }

    public static int Run(string dllPath, string url, int kiosk, int productionId, string email)
    {
        A = Assembly.LoadFrom(dllPath);
        BasicHttpBinding binding = new BasicHttpBinding();  // as TicketServiceProxy.GetControllerClient
        binding.Security.Mode = url.StartsWith("https:", StringComparison.OrdinalIgnoreCase)
            ? BasicHttpSecurityMode.Transport : BasicHttpSecurityMode.None;
        object client = Activator.CreateInstance(T("ControllerServiceClient"),
            BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.Instance, null,
            new object[] { binding, new EndpointAddress(url) }, null);
        Console.WriteLine("Ticket Controller Service at " + url + " (kiosk " + kiosk + ", production " + productionId + ")");

        // 1. GetTickets: 2 x price code 1.
        Console.WriteLine("GetTickets");
        object gt = New("GetTicketsRequest", "ProductionId", productionId,
            "PriceCodes", Arr("PriceCode", New("PriceCode", "PriceCodeId", 1, "Quantity", 2)),
            "UnlockTickets", Arr("UnlockTicket"));
        Header(gt, kiosk);
        object tr = Call(client, "GetTickets", gt);
        Console.WriteLine("  " + Resp(tr) + " ProductionId=" + Get(tr, "ProductionId"));
        Array groups = (Array)Get(tr, "TicketGroups");
        Array disclaimers = (Array)Get(tr, "SupplierDisclaimers");
        Check((bool)Get(tr, "Success") && groups.Length > 0, "GetTickets returned " + groups.Length + " ticket group(s)");
        foreach (object d in disclaimers)
            Console.WriteLine("    disclaimer " + Get(d, "SupplierNoteLogId") + ": " + Get(d, "SupplierNote"));
        foreach (object g in groups)
        {
            Array pcs = (Array)Get(g, "PriceCodes");
            Array dms = (Array)Get(g, "DeliveryMethods");
            Console.WriteLine("    group " + Get(g, "TicketGroupId") + " zone=" + Get(g, "Zone") + " level=" + Get(g, "Level") +
                " section=" + Get(g, "Section") + " row=" + Get(g, "Row") + " '" + Get(g, "SuggestionTypeDisplayName") +
                "' supplier=" + Get(g, "SupplierId") + " note=" + (Get(g, "SupplierNoteLogId") ?? "(nil)") +
                " lock=" + Get(g, "LockGuid") + " for " + Get(g, "LockDuration") + " min, " + dms.Length +
                " delivery method(s)");
            foreach (object p in pcs)
            {
                Array taxes = (Array)Get(p, "TaxCollection");
                Console.WriteLine("      price code " + Get(p, "PriceCodeId") + " x" + Get(p, "Quantity") + " price=" +
                    Get(p, "PricePerTicket") + " fee=" + Get(p, "FeePerTicket") + " feeTax=" + Get(p, "FeePerTicketTax") +
                    " taxItems=" + taxes.Length);
            }
        }
        if (groups.Length == 0) return 1;
        object first = groups.GetValue(0);
        int gid = (int)Get(first, "TicketGroupId");
        Guid lockGuid = (Guid)Get(first, "LockGuid");
        Check(lockGuid != Guid.Empty && ((Array)Get(first, "PriceCodes")).Length == 1, "lock guid and price codes deserialized");

        // 2. UpdateTicketLock on the chosen group.
        Console.WriteLine("UpdateTicketLock");
        object ul = New("UpdateTicketLockRequest", "TicketGroupId", gid, "LockGuid", lockGuid, "Quantity", 2);
        Header(ul, kiosk);
        object ur = Call(client, "UpdateTicketLock", ul);
        Console.WriteLine("  " + Resp(ur) + " LockDuration=" + Get(ur, "LockDuration"));
        Check((bool)Get(ur, "Success") && (int)Get(ur, "LockDuration") > 0, "lock extended");

        // 3. ReleaseTicketLock on the last suggested group (if more than one).
        List<object> others = new List<object>();
        for (int i = 1; i < groups.Length; i++) others.Add(groups.GetValue(i));
        if (others.Count > 0)
        {
            Console.WriteLine("ReleaseTicketLock");
            object last = others[others.Count - 1];
            object rl = New("ReleaseTicketLockRequest", "TicketGroupId", Get(last, "TicketGroupId"),
                "LockGuid", Get(last, "LockGuid"), "Quantity", 2);
            Header(rl, kiosk);
            object rr = Call(client, "ReleaseTicketLock", rl);
            Console.WriteLine("  " + Resp(rr));
            Check((bool)Get(rr, "Success"), "lock released");
            others.RemoveAt(others.Count - 1);
        }

        // 4. Declared faults: unknown production -> ValidationError.
        Console.WriteLine("GetTickets (unknown production)");
        object bad = New("GetTicketsRequest", "ProductionId", 1,
            "PriceCodes", Arr("PriceCode", New("PriceCode", "PriceCodeId", 1, "Quantity", 1)));
        Header(bad, kiosk);
        string f = CallFault(client, "GetTickets", bad);
        Console.WriteLine("  " + f);
        Check(f.StartsWith("FaultException<ValidationError>"), "ValidationError fault deserialized");

        // 5. PlaceOrder for the chosen group; the other held groups go in UnlockTickets.
        Console.WriteLine("PlaceOrder");
        object pc = ((Array)Get(first, "PriceCodes")).GetValue(0);
        decimal price = (decimal)Get(pc, "PricePerTicket"), fee = (decimal)Get(pc, "FeePerTicket");
        decimal priceTax = (decimal)Get(pc, "PricePerTicketTax"), feeTax = (decimal)Get(pc, "FeePerTicketTax");
        int qty = (int)Get(pc, "Quantity");
        List<object> taxItems = new List<object>();
        foreach (object t in (Array)Get(pc, "TaxCollection"))
            taxItems.Add(New("OrderTaxItem", "TaxTypeId", Get(t, "TaxTypeId"), "TaxAmt", Get(t, "TaxAmt")));
        object opc = New("OrderPriceCode", "PriceCodeId", Get(pc, "PriceCodeId"), "Quantity", qty, "PricePerTicket", price,
            "PricePerTicketTax", priceTax, "FeePerTicket", fee, "FeePerTicketTax", feeTax,
            "OrderTaxCollection", Arr("OrderTaxItem", taxItems.ToArray()));
        Array dmethods = (Array)Get(first, "DeliveryMethods");
        object item = New("OrderItem", "DeliveryMethodId", Get(dmethods.GetValue(0), "DeliveryMethodId"),
            "DeliverToName", "Harness Buyer", "LockGuid", lockGuid, "TicketGroupId", gid,
            "SuggestionTypeId", Get(first, "SuggestionTypeId"), "SupplierNoteLogId", Get(first, "SupplierNoteLogId"),
            "PriceCodes", Arr("OrderPriceCode", opc));
        string cardHash = Guid.NewGuid().ToString("N");
        object payment = New("Payment", "KeyId", "1", "EncryptedCardNumber", "harness-encrypted", "HashedCardId", cardHash,
            "FirstName", "Buyer", "LastName", "Harness", "PostalCode", "60601", "ExpirationYear", 2039, "ExpirationMonth", 12,
            "Track2", ";4111111111111111=39121010000000000000?");
        object header = New("OrderHeader", "OrderId", 1, "Subtotal", price * qty, "TotalFee", fee * qty,
            "TotalTax", (priceTax + feeTax) * qty, "Total", (price + fee + priceTax + feeTax) * qty);
        object order = New("Order", "Customer", New("Customer", "Email", email), "OrderHeader", header,
            "OrderItems", Arr("OrderItem", item), "Payment", payment, "PromotionList", new int[] { 7 });
        List<object> unlocks = new List<object>();
        foreach (object g in others)
            unlocks.Add(New("UnlockTicket", "TicketGroupId", Get(g, "TicketGroupId"), "TicketGroupGuid", Get(g, "LockGuid"),
                "Quantity", 2));
        object po = New("PlaceOrderRequest", "Order", order, "UnlockTickets", Arr("UnlockTicket", unlocks.ToArray()));
        Header(po, kiosk);
        object por = Call(client, "PlaceOrder", po);
        Console.WriteLine("  " + Resp(por) + " OrderId=" + Get(por, "OrderId"));
        Check((bool)Get(por, "Success") && (int)Get(por, "OrderId") > 0, "order placed");

        // Ordering the same (now sold) lock again is a ValidationError.
        Header(po, kiosk);
        f = CallFault(client, "PlaceOrder", po);
        Console.WriteLine("  again: " + f);
        Check(f.StartsWith("FaultException<ValidationError>"), "second order of a sold lock refused");

        // 6. GetEmailByCard for the card just used.
        Console.WriteLine("GetEmailByCard");
        object ge = New("GetEmailByCardRequest", "HashedCreditCardNumber", cardHash);
        Header(ge, kiosk);
        object ger = Call(client, "GetEmailByCard", ge);
        Console.WriteLine("  " + Resp(ger) + " Email=" + (Get(ger, "Email") ?? "(nil)"));
        Check((bool)Get(ger, "Success") && email.Equals(Get(ger, "Email")), "e-mail found for the card");
        object ge2 = New("GetEmailByCardRequest", "HashedCreditCardNumber", "unknown-" + cardHash);
        Header(ge2, kiosk);
        object ger2 = Call(client, "GetEmailByCard", ge2);
        Console.WriteLine("  unknown card: " + Resp(ger2) + " Email=" + (Get(ger2, "Email") ?? "(nil)"));
        Check(!(bool)Get(ger2, "Success") && Get(ger2, "Email") == null, "unknown card has no e-mail");

        // 7. SendNotification; the real client leaves all but the first array slot null.
        Console.WriteLine("SendNotification");
        object sub = New("Subscription", "NotificationType", "Email", "SubscriptionType", "Event", "SubscriptionValue", "5001");
        object sn = New("SendNotificationRequest", "CustomerEmail", email, "Subscriptions", Arr("Subscription", sub, null));
        Header(sn, kiosk);
        object snr = Call(client, "SendNotification", sn);
        Console.WriteLine("  " + Resp(snr));
        Check((bool)Get(snr, "Success"), "notification recorded");

        // 8. The generated WSDL imports cleanly with WCF's own metadata importer.
        Console.WriteLine("WSDL import");
        try
        {
            MetadataExchangeClient mex = new MetadataExchangeClient(new Uri(url + "?wsdl"), MetadataExchangeClientMode.HttpGet);
            WsdlImporter importer = new WsdlImporter(mex.GetMetadata());
            var contracts = importer.ImportAllContracts();
            var endpoints = importer.ImportAllEndpoints();
            foreach (MetadataConversionError e in importer.Errors)
                Console.WriteLine("    " + (e.IsWarning ? "warning: " : "error: ") + e.Message);
            ContractDescription cd = contracts.FirstOrDefault(x => x.Name == "IControllerService");
            Console.WriteLine("  contracts=" + contracts.Count + " endpoints=" + endpoints.Count + " operations=" +
                (cd == null ? 0 : cd.Operations.Count) + " errors=" + importer.Errors.Count(e => !e.IsWarning));
            Check(cd != null && cd.Operations.Count == 6 && !importer.Errors.Any(e => !e.IsWarning) && endpoints.Count == 1,
                "WSDL imported (6 operations, 1 endpoint, no errors)");
        }
        catch (Exception e)
        {
            Check(false, "WSDL import: " + e.Message);
        }

        ((ICommunicationObject)client).Close();
        Console.WriteLine(failures == 0 ? "ALL OK" : failures + " check(s) failed");
        return failures == 0 ? 0 : 1;
    }
}
