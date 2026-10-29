// rb-reprovision: point a Redbox kiosk at a backend in one step, and undo it.
//
//   rb-reprovision show   <settings.json> [--reds DIR]                what would change (nothing is written)
//   rb-reprovision apply  <settings.json> [--reds DIR] [--no-restart] apply, with a backup and a manifest
//   (show/apply: --address IP replaces the settings' Hosts.Address)
//   rb-reprovision revert [--no-restart]                              undo everything the manifest records
//   rb-reprovision status                                             what the manifest records
//   common: [--state DIR]  manifest and backups (default %ProgramData%\Redbox\reprovision)
//
// The settings file (format: README.md next to this file) is generated per kiosk by the backend
// (`admin kiosk-profile`, or the dashboard). Built for .NET 2.0/3.5 and x86 (build.ps1), so it runs on old and
// current kiosks alike, and in the 32-bit registry view that the kiosk's x86 programs use.
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Reflection;
using System.Security.Cryptography.X509Certificates;
using System.Security.Principal;
using System.ServiceProcess;
using System.Text;
using System.Text.RegularExpressions;
using Microsoft.Win32;

namespace Redbox.Reprovision
{
    abstract class Change
    {
        public string Component;
        public List<string> Notes = new List<string>();
        public string Service;          // Windows service to restart when this change is applied or reverted
        public abstract bool Changes { get; }
        public abstract string Target { get; }
        public abstract void Apply(Manifest m);
    }

    // A text file rewritten in place (encoding, BOM and line endings kept); also a created or replaced binary file.
    class FileChange : Change
    {
        public string Path;
        public byte[] Before;           // null = does not exist
        public byte[] After;            // null = leave as is
        public bool RecordOnly;         // not written, only backed up for revert (usdata.active)
        public bool Skip;               // not installed / key not found: nothing to do
        public override bool Changes { get { return After != null && (Before == null || !Same(Before, After)); } }
        public override string Target { get { return Path; } }
        public override void Apply(Manifest m)
        {
            m.RecordFile(Path);
            if (After == null) return;
            Directory.CreateDirectory(System.IO.Path.GetDirectoryName(Path));
            File.WriteAllBytes(Path, After);
        }
        static bool Same(byte[] a, byte[] b)
        {
            if (a.Length != b.Length) return false;
            for (int i = 0; i < a.Length; i++) if (a[i] != b[i]) return false;
            return true;
        }
    }

    class RegistryChange : Change
    {
        public string Key;              // under HKLM, 32-bit view (this process is x86)
        public string Name;
        public object Value;            // string or string[]
        public object Current;
        public override bool Changes { get { return !Equal(Current, Value); } }
        public override string Target { get { return "HKLM\\" + Key + " : " + Name; } }
        public override void Apply(Manifest m)
        {
            m.RecordRegistry(Key, Name);
            using (var k = m.CreateKey(Key))
            {
                if (Value is string[]) k.SetValue(Name, Value, RegistryValueKind.MultiString);
                else k.SetValue(Name, (string)Value, RegistryValueKind.String);
            }
        }
        public static bool Equal(object a, object b)
        {
            if (a == null || b == null) return a == b;
            var sa = a as string[]; var sb = b as string[];
            if (sa != null || sb != null)
            {
                if (sa == null || sb == null || sa.Length != sb.Length) return false;
                for (int i = 0; i < sa.Length; i++) if (sa[i] != sb[i]) return false;
                return true;
            }
            return Convert.ToString(a) == Convert.ToString(b);
        }
    }

    class CertificateChange : Change
    {
        public X509Certificate2 Certificate;
        public List<X509Certificate2> Replaces = new List<X509Certificate2>();
        public bool Present;
        public override bool Changes { get { return !Present || Replaces.Count > 0; } }
        public override string Target { get { return "LocalMachine\\Root : " + Certificate.Subject; } }
        public override void Apply(Manifest m)
        {
            var store = new X509Store(StoreName.Root, StoreLocation.LocalMachine);
            store.Open(OpenFlags.ReadWrite);
            try
            {
                foreach (var old in Replaces)
                {
                    m.RecordCertificateRemoved(old);
                    store.Remove(old);
                }
                if (!Present)
                {
                    store.Add(Certificate);
                    m.RecordCertificateAdded(Certificate);
                }
            }
            finally { store.Close(); }
        }
    }

    // ------------------------------------------------------------------------------------------- manifest

    class Manifest
    {
        public readonly string Dir;
        public Dictionary<string, object> Data;
        List<object> Entries { get { return (List<object>)Data["Entries"]; } }

        public Manifest(string dir)
        {
            Dir = dir;
            string p = System.IO.Path.Combine(dir, "manifest.json");
            if (File.Exists(p)) Data = (Dictionary<string, object>)Json.Parse(File.ReadAllText(p, Encoding.UTF8));
            else
            {
                Data = new Dictionary<string, object>();
                Data["Format"] = 1L;
                Data["Created"] = DateTime.Now.ToString("s");
                Data["Entries"] = new List<object>();
                Data["Services"] = new List<object>();
            }
        }

        public bool Exists { get { return File.Exists(System.IO.Path.Combine(Dir, "manifest.json")); } }

        public void Save()
        {
            Data["Updated"] = DateTime.Now.ToString("s");
            Directory.CreateDirectory(Dir);
            File.WriteAllText(System.IO.Path.Combine(Dir, "manifest.json"), Json.Write(Data) + "\n", new UTF8Encoding(false));
        }

        Dictionary<string, object> Find(string kind, string a, string b)
        {
            foreach (Dictionary<string, object> e in Entries)
            {
                if (Json.Str(e, "Kind") != kind) continue;
                if (kind == "file" && string.Equals(Json.Str(e, "Path"), a, StringComparison.OrdinalIgnoreCase)) return e;
                if (kind == "registry" && string.Equals(Json.Str(e, "Key"), a, StringComparison.OrdinalIgnoreCase) && Json.Str(e, "Name") == b) return e;
                if (kind == "key" && string.Equals(Json.Str(e, "Key"), a, StringComparison.OrdinalIgnoreCase)) return e;
                if (kind == "cert-added" && Json.Str(e, "Thumbprint") == a) return e;
            }
            return null;
        }

        void Add(Dictionary<string, object> e) { Entries.Add(e); Save(); }

        public void AddService(string name)
        {
            var l = (List<object>)Data["Services"];
            if (name != null && !l.Contains(name)) { l.Add(name); Save(); }
        }

        // The first state seen is kept: a second apply on top of the first still reverts to the original.
        public void RecordFile(string path)
        {
            if (Find("file", path, null) != null) return;
            var e = new Dictionary<string, object>();
            e["Kind"] = "file";
            e["Path"] = path;
            e["Backup"] = null;
            if (File.Exists(path))
            {
                string rel = "backup\\" + Entries.Count.ToString("0000") + "-" + System.IO.Path.GetFileName(path);
                Directory.CreateDirectory(System.IO.Path.Combine(Dir, "backup"));
                File.Copy(path, System.IO.Path.Combine(Dir, rel), true);
                e["Backup"] = rel;
            }
            Add(e);
        }

        public void RecordRegistry(string key, string name)
        {
            if (Find("registry", key, name) != null) return;
            var e = new Dictionary<string, object>();
            e["Kind"] = "registry";
            e["Key"] = key;
            e["Name"] = name;
            e["Previous"] = null;
            e["PreviousKind"] = "none";
            using (var k = Registry.LocalMachine.OpenSubKey(key))
            {
                if (k != null && Array.IndexOf(k.GetValueNames(), name) >= 0)
                {
                    var kind = k.GetValueKind(name);
                    object v = k.GetValue(name, null, RegistryValueOptions.DoNotExpandEnvironmentNames);
                    e["PreviousKind"] = kind.ToString();
                    if (v is string[]) { var l = new List<object>(); foreach (var s in (string[])v) l.Add(s); e["Previous"] = l; }
                    else if (v is byte[]) e["Previous"] = Convert.ToBase64String((byte[])v);
                    else if (v is int || v is long) e["Previous"] = Convert.ToInt64(v);
                    else e["Previous"] = Convert.ToString(v);
                }
            }
            Add(e);
        }

        public RegistryKey CreateKey(string key)
        {
            // Record each missing key, outermost first, so revert can remove what it created.
            var parts = key.Split('\\');
            string path = "";
            foreach (var p in parts)
            {
                path = path.Length == 0 ? p : path + "\\" + p;
                using (var k = Registry.LocalMachine.OpenSubKey(path))
                {
                    if (k == null && Find("key", path, null) == null)
                    {
                        var e = new Dictionary<string, object>();
                        e["Kind"] = "key";
                        e["Key"] = path;
                        Add(e);
                    }
                }
            }
            return Registry.LocalMachine.CreateSubKey(key);
        }

        public void RecordCertificateAdded(X509Certificate2 c)
        {
            if (Find("cert-added", c.Thumbprint, null) != null) return;
            var e = new Dictionary<string, object>();
            e["Kind"] = "cert-added";
            e["Thumbprint"] = c.Thumbprint;
            e["Subject"] = c.Subject;
            Add(e);
        }

        public void RecordCertificateRemoved(X509Certificate2 c)
        {
            string rel = "backup\\" + Entries.Count.ToString("0000") + "-" + c.Thumbprint + ".cer";
            Directory.CreateDirectory(System.IO.Path.Combine(Dir, "backup"));
            File.WriteAllBytes(System.IO.Path.Combine(Dir, rel), c.RawData);
            var e = new Dictionary<string, object>();
            e["Kind"] = "cert-removed";
            e["Thumbprint"] = c.Thumbprint;
            e["Subject"] = c.Subject;
            e["Backup"] = rel;
            Add(e);
        }

        public List<string> Revert()
        {
            var log = new List<string>();
            var entries = Entries;
            for (int i = entries.Count - 1; i >= 0; i--)
            {
                var e = (Dictionary<string, object>)entries[i];
                string kind = Json.Str(e, "Kind");
                try
                {
                    if (kind == "file") log.Add(RevertFile(e));
                    else if (kind == "registry") log.Add(RevertRegistry(e));
                    else if (kind == "key") log.Add(RevertKey(e));
                    else if (kind == "cert-added" || kind == "cert-removed") log.Add(RevertCertificate(e, kind));
                }
                catch (Exception ex) { log.Add("FAILED " + kind + " " + (Json.Str(e, "Path") ?? Json.Str(e, "Key") ?? Json.Str(e, "Thumbprint")) + ": " + ex.Message); }
            }
            return log;
        }

        string RevertFile(Dictionary<string, object> e)
        {
            string path = Json.Str(e, "Path"), backup = Json.Str(e, "Backup");
            if (backup == null)
            {
                if (File.Exists(path)) { File.Delete(path); return "deleted " + path; }
                return "absent " + path;
            }
            File.Copy(System.IO.Path.Combine(Dir, backup), path, true);
            return "restored " + path;
        }

        string RevertRegistry(Dictionary<string, object> e)
        {
            string key = Json.Str(e, "Key"), name = Json.Str(e, "Name"), kind = Json.Str(e, "PreviousKind");
            using (var k = Registry.LocalMachine.OpenSubKey(key, true))
            {
                if (k == null) return "absent HKLM\\" + key;
                if (kind == "none")
                {
                    k.DeleteValue(name, false);
                    return "removed HKLM\\" + key + " : " + name;
                }
                object prev = ((Dictionary<string, object>)e)["Previous"];
                var vk = (RegistryValueKind)Enum.Parse(typeof(RegistryValueKind), kind);
                if (vk == RegistryValueKind.MultiString) { var l = new List<string>(); foreach (var o in (List<object>)prev) l.Add((string)o); k.SetValue(name, l.ToArray(), vk); }
                else if (vk == RegistryValueKind.Binary) k.SetValue(name, Convert.FromBase64String((string)prev), vk);
                else if (vk == RegistryValueKind.DWord) k.SetValue(name, (int)Convert.ToInt64(prev), vk);
                else if (vk == RegistryValueKind.QWord) k.SetValue(name, Convert.ToInt64(prev), vk);
                else k.SetValue(name, (string)prev, vk);
                return "restored HKLM\\" + key + " : " + name;
            }
        }

        string RevertKey(Dictionary<string, object> e)
        {
            // Only removed when empty: whatever the kiosk wrote into it since is kept.
            string key = Json.Str(e, "Key");
            using (var k = Registry.LocalMachine.OpenSubKey(key))
            {
                if (k == null) return "absent HKLM\\" + key;
                if (k.GetValueNames().Length > 0 || k.SubKeyCount > 0) return "kept HKLM\\" + key + " (not empty)";
            }
            Registry.LocalMachine.DeleteSubKey(key, false);
            return "removed key HKLM\\" + key;
        }

        string RevertCertificate(Dictionary<string, object> e, string kind)
        {
            var store = new X509Store(StoreName.Root, StoreLocation.LocalMachine);
            store.Open(OpenFlags.ReadWrite);
            try
            {
                if (kind == "cert-added")
                {
                    foreach (var c in store.Certificates.Find(X509FindType.FindByThumbprint, Json.Str(e, "Thumbprint"), false))
                        store.Remove(c);
                    return "removed certificate " + Json.Str(e, "Subject");
                }
                store.Add(new X509Certificate2(File.ReadAllBytes(System.IO.Path.Combine(Dir, Json.Str(e, "Backup")))));
                return "restored certificate " + Json.Str(e, "Subject");
            }
            finally { store.Close(); }
        }
    }

    // -------------------------------------------------------------------------------------------- planning

    class Planner
    {
        public string Reds;
        public Dictionary<string, object> Settings;
        public List<Change> Changes = new List<Change>();
        Dictionary<string, FileChange> files = new Dictionary<string, FileChange>(StringComparer.OrdinalIgnoreCase);
        Dictionary<string, string> texts = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
        Dictionary<string, Encoding> encodings = new Dictionary<string, Encoding>(StringComparer.OrdinalIgnoreCase);
        Dictionary<string, string> originals = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);

        const string EngineKey = "SOFTWARE\\Redbox\\REDS\\Kiosk Engine";
        const string HostsTag = "# redbox-self-hosted";

        public void Plan()
        {
            string kioskId = Json.Str(Settings, "KioskId");
            string market = Json.Str(Settings, "Market");

            // Hosts file
            var hosts = Json.Obj(Settings, "Hosts");
            if (hosts != null)
            {
                string path = System.IO.Path.Combine(Environment.GetEnvironmentVariable("SystemRoot"), "System32\\drivers\\etc\\hosts");
                string text = Text(path) ?? "";
                var keep = new List<string>();
                foreach (var line in text.Replace("\r\n", "\n").Split('\n'))
                    if (!line.TrimEnd().EndsWith(HostsTag)) keep.Add(line);
                while (keep.Count > 0 && keep[keep.Count - 1].Trim().Length == 0) keep.RemoveAt(keep.Count - 1);
                string address = Json.Str(hosts, "Address");
                var names = Json.List(hosts, "Names") ?? new List<object>();
                foreach (var n in names) keep.Add(address + " " + n + " " + HostsTag);
                SetText("hosts", path, string.Join("\r\n", keep.ToArray()) + "\r\n", null).Notes.Add(names.Count + " names -> " + address);
            }

            // CA certificate
            string pem = Json.Str(Settings, "CaCertificate");
            if (pem != null) Changes.Add(PlanCertificate(pem));

            // Kiosk Engine machine settings (also read by updatemgrd, the Update Client and the Device Service).
            if (kioskId != null) Reg("engine", EngineKey + "\\Store", "ID", kioskId);
            if (market != null) Reg("engine", EngineKey + "\\Store", "Market", market);
            var engine = Json.Obj(Settings, "Engine");
            if (engine != null)
                foreach (var sub in engine)
                    foreach (var kv in (Dictionary<string, object>)sub.Value)
                    {
                        if (kv.Value == null) continue;
                        object v = kv.Value is List<object> ? (object)((List<object>)kv.Value).ConvertAll<string>(Convert.ToString).ToArray() : Convert.ToString(kv.Value);
                        Reg("engine", EngineKey + (sub.Key.Length > 0 ? "\\" + sub.Key : ""), kv.Key, v);
                    }

            // URL rewrites in every configuration file the kiosk programs read (before the key settings below).
            var rewrites = Json.Obj(Settings, "UrlRewrites");
            if (rewrites != null && rewrites.Count > 0)
                foreach (var f in RewriteTargets())
                {
                    string t = Text(f.Key);
                    if (t == null) continue;
                    string n = t;
                    foreach (var kv in rewrites) n = n.Replace(kv.Key, Convert.ToString(kv.Value));
                    if (n != t) SetText(f.Value, f.Key, n, Service(f.Value)).Notes.Add("URL rewrites");
                }

            // Update Manager: the Update Service URL (daemon and its tools) and the credential keys.
            var um = Json.Obj(Settings, "UpdateManager");
            if (um != null)
            {
                string url = Json.Str(um, "UpdateServiceUrl");
                if (url != null)
                    foreach (var exe in new[] { "updatemgrd", "update-console", "updatemgr-ui" })
                        XmlSetting("update-manager", R("Update Manager\\bin\\" + exe + ".exe.config"), "UpdateServiceUrl", url,
                                   exe == "updatemgrd" ? "updatemgrd$default" : null);
                var keys = Json.Obj(um, "CredentialKeys");
                if (keys != null) PlanCredentialKeys(Json.Str(keys, "Key1"), Json.Str(keys, "Key2"));
            }

            // Update Client and Kiosk Client: appsettings keys, each where it takes effect: the Production overlay
            // when it sets the key (it wins over the base file), else appsettings.json.
            foreach (var c in new[] { new[] { "UpdateClient", "update-client", "Update Client\\bin", "updateclient$service" },
                                      new[] { "KioskClient", "kiosk-client", "Kiosk Client\\bin", "kioskclient$service" } })
            {
                var sect = Json.Obj(Settings, c[0]);
                if (sect == null) continue;
                string overlay = R(c[2] + "\\appsettings.Production.json");
                foreach (var kv in sect)
                {
                    if (kv.Value == null) continue;
                    string file = HasJsonKey(overlay, kv.Key) ? overlay : R(c[2] + "\\appsettings.json");
                    JsonKey(c[1], file, kv.Key, Convert.ToString(kv.Value), c[3], true);
                }
            }

            // Device Service: the Kiosk Data Service it reports to (the engine rewrites this file too, from
            // system.config's KioskDataServicesUrl, when it checks the device status).
            var ds = Json.Obj(Settings, "DeviceService");
            if (ds != null)
            {
                string api = Json.Str(ds, "KioskDataApiUrl");
                string path = R("DeviceService\\bin\\Data\\KioskData.json");
                if (api != null) JsonKey("device-service", path, "ApiUrl", api, "device$service", true);
                if (kioskId != null && Text(path) != null) JsonNumber("device-service", path, "KioskId", kioskId, "device$service");
            }
        }

        string R(string rel) { return System.IO.Path.Combine(Reds, rel); }

        static string Service(string component)
        {
            switch (component)
            {
                case "update-client": return "updateclient$service";
                case "kiosk-client": return "kioskclient$service";
                case "device-service": return "device$service";
                case "update-manager": return "updatemgrd$default";
            }
            return null;
        }

        IEnumerable<KeyValuePair<string, string>> RewriteTargets()
        {
            var list = new List<KeyValuePair<string, string>>();
            Action<string, string, string> add = delegate(string dir, string pattern, string component)
            {
                if (Directory.Exists(dir))
                    foreach (var f in Directory.GetFiles(dir, pattern)) list.Add(new KeyValuePair<string, string>(f, component));
            };
            add(R("Update Manager\\bin"), "*.exe.config", "update-manager");
            add(R("Update Client\\bin"), "appsettings*.json", "update-client");
            add(R("Kiosk Client\\bin"), "appsettings*.json", "kiosk-client");
            add(R("DeviceService\\bin"), "appsettings*.json", "device-service");
            add(R("DeviceService\\bin\\Data"), "*.json", "device-service");
            add(R("Kiosk Engine\\data"), "*.config", "engine");
            add(R("Kiosk Engine\\bin"), "kioskengine.exe.config", "engine");
            string pd = Environment.GetFolderPath(Environment.SpecialFolder.CommonApplicationData);
            add(System.IO.Path.Combine(pd, "Redbox\\KioskClient\\Configuration"), "*", "kiosk-client");
            return list;
        }

        // ---- file text helpers

        string Text(string path)
        {
            string t;
            if (texts.TryGetValue(path, out t)) return t;
            if (!File.Exists(path)) return null;
            byte[] b = File.ReadAllBytes(path);
            Encoding enc;
            int skip = 0;
            if (b.Length >= 3 && b[0] == 0xEF && b[1] == 0xBB && b[2] == 0xBF) { enc = new UTF8Encoding(true); skip = 3; }
            else if (b.Length >= 2 && b[0] == 0xFF && b[1] == 0xFE) { enc = new UnicodeEncoding(false, true); skip = 2; }
            else enc = new UTF8Encoding(false);
            t = enc.GetString(b, skip, b.Length - skip);
            texts[path] = t;
            originals[path] = t;
            encodings[path] = enc;
            var fc = new FileChange { Component = "", Path = path, Before = b };
            files[path] = fc;
            return t;
        }

        FileChange SetText(string component, string path, string text, string service)
        {
            Text(path);
            FileChange fc;
            if (!files.TryGetValue(path, out fc))
            {
                fc = new FileChange { Path = path, Before = null };
                files[path] = fc;
                encodings[path] = new UTF8Encoding(false);
            }
            if (!Changes.Contains(fc)) Changes.Add(fc);
            if (string.IsNullOrEmpty(fc.Component)) fc.Component = component;
            if (fc.Service == null) fc.Service = service;
            texts[path] = text;
            string orig;
            if (fc.Before != null && originals.TryGetValue(path, out orig) && orig == text) { fc.After = fc.Before; return fc; }
            Encoding enc = encodings[path];
            byte[] pre = enc.GetPreamble();
            byte[] body = enc.GetBytes(text);
            var all = new byte[pre.Length + body.Length];
            pre.CopyTo(all, 0);
            body.CopyTo(all, pre.Length);
            fc.After = all;
            return fc;
        }

        void Missing(string component, string path, string what)
        {
            var c = new FileChange { Component = component, Path = path, Skip = true };
            c.Notes.Add(what);
            Changes.Add(c);
        }

        void JsonKey(string component, string path, string key, string value, string service, bool required)
        {
            string t = Text(path);
            if (t == null) { if (required) Missing(component, path, "not installed"); return; }
            int n = 0;
            string old = null;
            var rx = new Regex("(\"" + Regex.Escape(key) + "\"\\s*:\\s*)(\"(?:[^\"\\\\]|\\\\.)*\"|null)");
            var lines = t.Split('\n');
            for (int i = 0; i < lines.Length; i++)
            {
                if (lines[i].TrimStart().StartsWith("//")) continue;
                lines[i] = rx.Replace(lines[i], delegate(Match m) { n++; old = m.Groups[2].Value; return m.Groups[1].Value + Json.Quote(value); });
            }
            if (n == 0) { if (required) Missing(component, path, key + ": not in the file, not added"); return; }
            string nt = string.Join("\n", lines);
            SetText(component, path, nt, service).Notes.Add(key + ": " + (nt != t ? old + " -> " + Json.Quote(value) : "already " + old));
        }

        bool HasJsonKey(string path, string key)
        {
            string t = Text(path);
            if (t == null) return false;
            var rx = new Regex("\"" + Regex.Escape(key) + "\"\\s*:");
            foreach (var line in t.Split('\n'))
                if (!line.TrimStart().StartsWith("//") && rx.IsMatch(line)) return true;
            return false;
        }

        void JsonNumber(string component, string path, string key, string value, string service)
        {
            string t = Text(path);
            var rx = new Regex("(\"" + Regex.Escape(key) + "\"\\s*:\\s*)(-?\\d+)");
            string old = null;
            string nt = rx.Replace(t, delegate(Match m) { old = m.Groups[2].Value; return m.Groups[1].Value + value; });
            if (old != null) SetText(component, path, nt, service).Notes.Add(key + ": " + (nt != t ? old + " -> " + value : "already " + value));
        }

        void XmlSetting(string component, string path, string name, string value, string service)
        {
            string t = Text(path);
            if (t == null) return;
            var rx = new Regex("(<setting\\s+name=\"" + Regex.Escape(name) + "\"[^>]*>\\s*<value>)([^<]*)(</value>)");
            string old = null;
            string esc = value.Replace("&", "&amp;").Replace("<", "&lt;").Replace(">", "&gt;");
            string nt = rx.Replace(t, delegate(Match m) { old = m.Groups[2].Value; return m.Groups[1].Value + esc + m.Groups[3].Value; });
            if (old == null) { Missing(component, path, name + ": not in the file, not added"); return; }
            SetText(component, path, nt, service).Notes.Add(name + ": " + (nt != t ? old + " -> " + value : "already " + value));
        }

        void Reg(string component, string key, string name, object value)
        {
            object current = null;
            using (var k = Registry.LocalMachine.OpenSubKey(key))
                if (k != null) current = k.GetValue(name, null, RegistryValueOptions.DoNotExpandEnvironmentNames);
            var c = new RegistryChange { Component = component, Key = key, Name = name, Value = value, Current = current };
            c.Notes.Add(Show(current) + " -> " + Show(value));
            Changes.Add(c);
        }

        static string Show(object v)
        {
            if (v == null) return "(none)";
            if (v is string[]) return "[" + string.Join(", ", (string[])v) + "]";
            return "\"" + v + "\"";
        }

        CertificateChange PlanCertificate(string pem)
        {
            string b64 = Regex.Replace(pem, "-----[^-]+-----|\\s", "");
            var cert = new X509Certificate2(Convert.FromBase64String(b64));
            var c = new CertificateChange { Component = "ca", Certificate = cert };
            var store = new X509Store(StoreName.Root, StoreLocation.LocalMachine);
            store.Open(OpenFlags.ReadOnly);
            try
            {
                foreach (var x in store.Certificates)
                {
                    if (x.Thumbprint == cert.Thumbprint) c.Present = true;
                    else if (x.Subject == cert.Subject) c.Replaces.Add(x);
                }
            }
            finally { store.Close(); }
            c.Notes.Add((c.Present ? "already trusted" : "add") + " " + cert.Thumbprint);
            foreach (var x in c.Replaces) c.Notes.Add("replace " + x.Thumbprint + " (same subject, expires " + x.NotAfter.ToString("yyyy-MM-dd") + ")");
            return c;
        }

        void PlanCredentialKeys(string key1, string key2)
        {
            // updatemgrd reads usdata.active next to Redbox.UpdateService.Client.dll: Redbox.Core's Encrypt() of
            // {"Key1":..,"Key2":..}; a usdata.staged file replaces it on next use. The kiosk's own Redbox.Core
            // does the encryption, so no Redbox key is kept here.
            // Redbox.Core is a DLL on some kiosks and merged (ILMerge) into the Update Manager's assemblies on others.
            string bin = R("Update Manager\\bin");
            if (!Directory.Exists(bin)) { Missing("update-manager", bin, "credential keys: not installed"); return; }
            byte[] plain = Encoding.UTF8.GetBytes("{\"Key1\":" + Json.Quote(key1) + ",\"Key2\":" + Json.Quote(key2) + "}");
            byte[] enc = null;
            string why = "no Redbox.Core.ByteArrayExtensions found";
            foreach (var name in new[] { "Redbox.Core.dll", "Redbox.UpdateService.Client.dll", "updatemgrd.exe", "update-console.exe" })
            {
                string f = System.IO.Path.Combine(bin, name);
                if (!File.Exists(f)) continue;
                try
                {
                    var t = Assembly.LoadFrom(f).GetType("Redbox.Core.ByteArrayExtensions", false);
                    var mi = t == null ? null : t.GetMethod("Encrypt", new[] { typeof(byte[]) });
                    if (mi == null) continue;
                    enc = (byte[])mi.Invoke(null, new object[] { plain });
                    break;
                }
                catch (Exception ex) { why = name + ": " + ex.GetBaseException().Message; }
            }
            if (enc == null) { Missing("update-manager", bin, "credential keys: cannot use the kiosk's Encrypt() (" + why + ")"); return; }
            string staged = System.IO.Path.Combine(bin, "usdata.staged");
            string active = System.IO.Path.Combine(bin, "usdata.active");
            var a = new FileChange { Component = "update-manager", Path = active, Before = File.Exists(active) ? File.ReadAllBytes(active) : null, After = null, RecordOnly = true };
            a.Notes.Add("recorded (updatemgrd replaces it with usdata.staged)");
            Changes.Add(a);
            var s = new FileChange { Component = "update-manager", Path = staged, Before = File.Exists(staged) ? File.ReadAllBytes(staged) : null, After = enc, Service = "updatemgrd$default" };
            s.Notes.Add("credential keys (encrypted with the kiosk's Redbox.Core)");
            Changes.Add(s);
        }
    }

    // ------------------------------------------------------------------------------------------------ main

    static class Program
    {
        static int Main(string[] args)
        {
            try { return Run(new List<string>(args)); }
            catch (Exception ex)
            {
                Console.Error.WriteLine("error: " + ex.Message);
                return 2;
            }
        }

        static string Option(List<string> args, string name)
        {
            int i = args.IndexOf(name);
            if (i < 0 || i + 1 >= args.Count) return null;
            string v = args[i + 1];
            args.RemoveRange(i, 2);
            return v;
        }

        static bool Flag(List<string> args, string name)
        {
            return args.Remove(name);
        }

        static int Run(List<string> args)
        {
            string state = Option(args, "--state") ?? Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.CommonApplicationData), "Redbox\\reprovision");
            string reds = Option(args, "--reds");
            bool noRestart = Flag(args, "--no-restart");
            string address = Option(args, "--address");
            string cmd = args.Count > 0 ? args[0] : "help";

            if (cmd == "show" || cmd == "apply")
            {
                if (args.Count < 2) throw new ArgumentException("usage: rb-reprovision " + cmd + " <settings.json>");
                var settings = (Dictionary<string, object>)Json.Parse(File.ReadAllText(args[1], Encoding.UTF8));
                if (Json.Str(settings, "Format") != "1") throw new ArgumentException("unsupported settings Format " + Json.Str(settings, "Format"));
                if (address != null && Json.Obj(settings, "Hosts") != null) Json.Obj(settings, "Hosts")["Address"] = address;
                var p = new Planner { Settings = settings, Reds = reds ?? Json.Str(settings, "RedsPath") ?? FindReds() };
                Console.WriteLine("REDS: " + p.Reds + "   kiosk " + (Json.Str(settings, "KioskId") ?? "(unchanged)") +
                                  "   settings: " + (Json.Str(settings, "Comment") ?? args[1]));
                p.Plan();
                int n = 0;
                foreach (var c in p.Changes)
                {
                    var fc = c as FileChange;
                    string mark = c.Changes ? "change" : fc != null && fc.Skip ? "skip" : fc != null && fc.RecordOnly ? "record" : "same";
                    if (c.Changes) n++;
                    Console.WriteLine(string.Format("  [{0,-6}] {1,-15} {2}", mark, c.Component, c.Target));
                    foreach (var note in c.Notes) Console.WriteLine("             " + note);
                }
                Console.WriteLine(n + " change(s)");
                if (cmd == "show") return 0;
                RequireAdmin();
                var m = new Manifest(state);
                m.Data["Reds"] = p.Reds;
                m.Data["Settings"] = Path.GetFullPath(args[1]);
                m.Save();
                var services = new List<string>();
                bool engine = false;
                foreach (var c in p.Changes)
                {
                    if (!c.Changes && !(c is FileChange && ((FileChange)c).RecordOnly)) continue;
                    c.Apply(m);
                    if (c.Service != null && !services.Contains(c.Service)) { services.Add(c.Service); m.AddService(c.Service); }
                    if (c.Component == "engine" || c.Component == "ca" || c.Component == "hosts") engine = true;
                }
                Console.WriteLine("applied; manifest and backups in " + state);
                Restart(services, noRestart);
                if (engine) Console.WriteLine("restart the Kiosk Engine (or the kiosk) for its settings, the hosts file and the CA to take effect");
                return 0;
            }
            if (cmd == "revert")
            {
                RequireAdmin();
                var m = new Manifest(state);
                if (!m.Exists) { Console.WriteLine("nothing to revert (no manifest in " + state + ")"); return 0; }
                foreach (var line in m.Revert()) Console.WriteLine("  " + line);
                var services = new List<string>();
                foreach (var s in (List<object>)m.Data["Services"]) services.Add((string)s);
                string done = state.TrimEnd('\\') + ".reverted-" + DateTime.Now.ToString("yyyyMMdd-HHmmss");
                Directory.Move(state, done);
                Console.WriteLine("reverted; manifest and backups kept in " + done);
                Restart(services, noRestart);
                Console.WriteLine("restart the Kiosk Engine (or the kiosk) for the restored settings to take effect");
                return 0;
            }
            if (cmd == "status")
            {
                var m = new Manifest(state);
                if (!m.Exists) { Console.WriteLine("not reprovisioned (no manifest in " + state + ")"); return 0; }
                Console.WriteLine(Json.Write(m.Data));
                return 0;
            }
            Console.WriteLine("usage: rb-reprovision show|apply <settings.json> [--address IP] [--reds DIR] [--no-restart] [--state DIR]");
            Console.WriteLine("       rb-reprovision revert [--no-restart] [--state DIR]");
            Console.WriteLine("       rb-reprovision status [--state DIR]");
            return 1;
        }

        static string FindReds()
        {
            foreach (var root in new[] { Environment.GetEnvironmentVariable("ProgramW6432"), Environment.GetEnvironmentVariable("ProgramFiles"),
                                         "C:\\Program Files", "C:\\Program Files (x86)" })
            {
                if (string.IsNullOrEmpty(root)) continue;
                string p = Path.Combine(root, "Redbox\\REDS");
                if (Directory.Exists(p)) return p;
            }
            throw new DirectoryNotFoundException("no Redbox\\REDS folder found; pass --reds");
        }

        static void RequireAdmin()
        {
            if (!new WindowsPrincipal(WindowsIdentity.GetCurrent()).IsInRole(WindowsBuiltInRole.Administrator))
                throw new UnauthorizedAccessException("run this from an elevated (administrator) prompt");
        }

        static void Restart(List<string> services, bool skip)
        {
            foreach (var name in services)
            {
                if (skip) { Console.WriteLine("  restart " + name + " yourself (--no-restart)"); continue; }
                bool installed = false;
                foreach (var s in ServiceController.GetServices()) if (string.Equals(s.ServiceName, name, StringComparison.OrdinalIgnoreCase)) installed = true;
                if (!installed) { Console.WriteLine("  " + name + ": not installed"); continue; }
                try
                {
                    using (var sc = new ServiceController(name))
                    {
                        var st = sc.Status;
                        if (st != ServiceControllerStatus.Running) { Console.WriteLine("  " + name + " is " + st + "; left as is"); continue; }
                        sc.Stop();
                        sc.WaitForStatus(ServiceControllerStatus.Stopped, TimeSpan.FromSeconds(90));
                        sc.Start();
                        sc.WaitForStatus(ServiceControllerStatus.Running, TimeSpan.FromSeconds(90));
                        Console.WriteLine("  restarted " + name);
                    }
                }
                catch (InvalidOperationException ex) { Console.WriteLine("  " + name + ": " + ex.GetBaseException().Message); }
                catch (System.ServiceProcess.TimeoutException) { Console.WriteLine("  " + name + ": timed out restarting"); }
            }
        }
    }
}
