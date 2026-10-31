// Load a kiosk catalog the way the real engine does: Redbox.Rental.Data.ProfileData.LoadDataInstance() (VistaDB,
// ParseLuaValue, box art extraction) and Redbox.ProductLookupCatalog.Archive.Find() for inventory.data.
//
// The engine logs and skips a row it cannot read, so every table's loaded count is checked against the number of
// rows in the file (expected counts come from the runner). Titles and barcodes given on the command line are
// looked up and printed, and a title's box art is compared with the file the engine extracted.
using System;
using System.Collections;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Security.Cryptography;
using Redbox.Core;
using Redbox.ProductLookupCatalog;
using Redbox.Rental.Data;

public static class CatalogEngineHarness
{
    static int failures;

    static void Check(bool ok, string what)
    {
        Console.WriteLine((ok ? "PASS " : "FAIL ") + what);
        if (!ok) failures++;
    }

    static int CountOf(object instance, string property)
    {
        var p = instance.GetType().GetProperty(property);
        var v = p == null ? null : p.GetValue(instance, null) as ICollection;
        return v == null ? -1 : v.Count;
    }

    static object Lookup(object instance, string property, long key)
    {
        var dict = instance.GetType().GetProperty(property).GetValue(instance, null) as IDictionary;
        return dict != null && dict.Contains(key) ? dict[key] : null;
    }

    static string Show(object o)
    {
        if (o == null) return "null";
        var parts = new List<string>();
        foreach (var p in o.GetType().GetProperties())
        {
            object v;
            try { v = p.GetValue(o, null); } catch { continue; }
            var e = v as IEnumerable;
            if (e != null && !(v is string)) v = "[" + string.Join(", ", e.Cast<object>().Select(x => Convert.ToString(x)).ToArray()) + "]";
            parts.Add(p.Name + "=" + v);
        }
        return string.Join("; ", parts.ToArray());
    }

    static string Sha1(byte[] data)
    {
        using (var sha = SHA1.Create()) return BitConverter.ToString(sha.ComputeHash(data)).Replace("-", "");
    }

    /// expected: "ProductCatalog=11656,ProductGroup=9489,..." (file table -> rows); titles: product ids;
    /// barcodes: "barcode=product id"; art: "product id=local image file" (compared with what the engine extracted).
    public static int Run(string dataDir, string expected, string[] titles, string[] barcodes, string[] art)
    {
        LogHelper.Instance.Loggers.Add(StubFactory.Create<ILogger>());
        var profile = new ProfileData(dataDir, "ProfileImages");
        Check(profile.HasActive(), "profile.data present");
        var started = DateTime.Now;
        object inst = profile.LoadDataInstance();
        Console.WriteLine("  LoadDataInstance took " + (DateTime.Now - started).TotalSeconds.ToString("0.0") + " s");

        // file table -> ProfileDataInstance collection
        var map = new Dictionary<string, string> {
            { "ProductCatalog", "Titles" }, { "ProductGroup", "ProductGroups" }, { "Market", "Markets" },
            { "Store", "Stores" }, { "ProductType", "ProductTypes" }, { "ProductFamily", "ProductFamilies" },
            { "SubPlatform", "SubPlatforms" } };
        foreach (var pair in (expected ?? "").Split(new[] { ',' }, StringSplitOptions.RemoveEmptyEntries))
        {
            var kv = pair.Split('=');
            string prop;
            if (!map.TryGetValue(kv[0], out prop)) continue;
            int got = CountOf(inst, prop);
            Check(got == int.Parse(kv[1]), string.Format("{0}: engine loaded {1} of {2} rows ({3})", kv[0], got, kv[1], prop));
        }
        var version = inst.GetType().GetProperty("ProfileDataVersion").GetValue(inst, null);
        Console.WriteLine("  profile version: " + Show(version));

        foreach (var t in titles ?? new string[0])
        {
            var title = Lookup(inst, "Titles", long.Parse(t));
            Check(title != null, "title " + t + " loaded");
            if (title != null) Console.WriteLine("  " + Show(title));
            var rollup = Lookup(inst, "TitleRollupXREF", long.Parse(t));
            if (rollup != null) Console.WriteLine("  rollup: " + Show(rollup));
        }

        string images = Path.Combine(dataDir, "ProfileImages");
        foreach (var a in art ?? new string[0])
        {
            var kv = a.Split(new[] { '=' }, 2);
            var title = Lookup(inst, "Titles", long.Parse(kv[0]));
            string name = title == null ? null : Convert.ToString(title.GetType().GetProperty("ImageFile").GetValue(title, null));
            string path = name == null ? null : Path.Combine(images, name);
            bool same = path != null && File.Exists(path) && Sha1(File.ReadAllBytes(path)) == Sha1(File.ReadAllBytes(kv[1]));
            Check(same, "title " + kv[0] + " box art extracted by the engine as " + name + " (same bytes)");
        }

        string inventory = Path.Combine(dataDir, "inventory.data");
        Check(Archive.IsValid(inventory), "inventory.data is a valid archive");
        using (var archive = Archive.Open(inventory))
        {
            Console.WriteLine("  inventory.data: " + archive.Count + " records, created " + archive.GetCreatedOn() +
                              " on '" + archive.GetOriginMachine().Trim() + "'");
            foreach (var b in barcodes ?? new string[0])
            {
                var kv = b.Split('=');
                var inv = archive.Find(kv[0]);
                Check(inv != null && inv.TitleId.ToString() == kv[1],
                      "barcode " + kv[0] + " -> title " + (inv == null ? "(not found)" : inv.TitleId + " " + inv.Code));
            }
        }
        Console.WriteLine(failures == 0 ? "ALL CHECKS PASSED" : failures + " CHECK(S) FAILED");
        return failures == 0 ? 0 : 1;
    }
}
