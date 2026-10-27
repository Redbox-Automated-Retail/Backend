// Drives the real decompiled Update Manager assemblies (Redbox.UpdateService.Client, Redbox.UpdateManager.*)
// against the self-hosted Update Service. Compiled at run time by run-updatemgr-harness.ps1 (C# 5).
//
// It does what updatemgrd's UpdateService.StartDownloads / FinishDownloads do, with one difference: it skips
// writing the BITS bandwidth group policy under HKLM (needs administrator rights; the kiosk runs as SYSTEM).
// Downloads go through real BITS jobs; verification, tar unpacking, revlog updates, xdelta3 patching,
// staging and activation are the shipped code paths.
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Threading;
using Redbox.Core;
using Redbox.IPC.Framework;
using Redbox.UpdateManager.BITS;
using Redbox.UpdateManager.ComponentModel;
using Redbox.UpdateManager.Environment;
using Redbox.UpdateService.Client;
using Redbox.UpdateService.Model;

public static class UpdateManagerHarness
{
    static int failures;

    static void Check(bool ok, string what)
    {
        Console.WriteLine((ok ? "PASS " : "FAIL ") + what);
        if (!ok) failures++;
    }

    static void Dump(Redbox.UpdateManager.ComponentModel.ErrorList errors, string what)
    {
        foreach (var e in errors)
            Console.WriteLine("  " + what + ": " + e.Code + " " + e.Description + " " + e.Details);
    }

    public static int Run(string url, string store, string root, string[] activate, bool serverPoll, int rounds, string updatemgrdExe)
    {
        // As updatemgrd's Program does before anything else.
        System.Net.ServicePointManager.SecurityProtocol = System.Net.SecurityProtocolType.Tls12;
        var macro = new MacroService();
        ServiceLocator.Instance.AddService(typeof(IMacroService), macro);
        macro["StoreNumber"] = store;
        Redbox.UpdateManager.Remoting.UpdateService.Instance.Initialize(url, TimeSpan.FromSeconds(120.0), store, 60);
        DataStoreService.Instance.Initialize(Path.Combine(root, ".store\\.data"));
        QueueService.Instance.Initialize(Path.Combine(root, ".store\\.data"));
        TransferService.Instance.Initialize();
        RepositoryService.Instance.Initialize(Path.Combine(root, ".store\\.repository"));

        var repos = ServiceLocator.Instance.GetService<IRepositoryService>();
        var transfers = ServiceLocator.Instance.GetService<ITransferService>();
        var data = ServiceLocator.Instance.GetService<IDataStoreService>();
        var updater = ServiceLocator.Instance.GetService<IUpdateService>();

        for (int round = 0; round < rounds; round++)
        {
            Dump(updater.FinishDownloads(), "FinishDownloads");
            var heads = new Dictionary<string, string>();
            foreach (var name in repos.GetAllRepositories())
            {
                var head = repos.GetHeadRevision(name);
                if (!string.IsNullOrEmpty(head)) heads[name] = head;
            }
            Store storeInfo;
            List<ChangeSet> jobs;
            ClientCommandResult result;
            using (var client = Redbox.UpdateService.Client.UpdateService.GetService(url, 120000))
                result = client.GetChangeSet(store, heads, out storeInfo, out jobs);
            Check(result.Success, "round " + round + ": u p succeeded (" + jobs.Count + " change sets) " + result.StatusMessage);
            foreach (var err in result.Errors) Console.WriteLine("  u p error: " + err.Code + " " + err.Description + " " + err.Details);
            if (jobs.Count == 0) break;
            Check(storeInfo != null && storeInfo.Number == store, "Store record parsed (number " + (storeInfo == null ? "null" : storeInfo.Number) + ")");
            var created = new List<ITransferJob>();
            foreach (var cs in jobs)
            {
                Console.WriteLine("  change set " + cs.Name + " head " + cs.Head + " revisions " + cs.Revisions.Count + " items " + cs.Items.Count + " archive " + cs.IsArchive);
                if (!repos.ContainsRepository(cs.Name)) repos.AddRepository(cs.Name);
                ITransferJob job;
                var errs = transfers.CreateDownloadJob("<~" + cs.Name + "-" + cs.Head.Substring(0, 8) + "~>", out job);
                errs.AddRange(job.SetMinimumRetryDelay(60));
                errs.AddRange(job.SetNoProgressTimeout(storeInfo.NoProgressTimeout));
                errs.AddRange(job.AddItem(cs.Url, Path.GetTempFileName()));
                errs.AddRange(job.Resume());
                Dump(errs, "BITS " + cs.Name);
                data.Set(job.ID, cs);
                created.Add(job);
            }
            var deadline = DateTime.UtcNow.AddMinutes(10);
            while (DateTime.UtcNow < deadline)
            {
                List<ITransferJob> current;
                transfers.GetJobs(out current, false);
                var mine = current.Where(j => created.Any(c => c.ID == j.ID)).ToList();
                if (mine.All(j => j.Status == TransferStatus.Transferred || j.Status == TransferStatus.Error))
                {
                    foreach (var j in mine) Console.WriteLine("  BITS " + j.Name + " " + j.Status + " " + j.TotalBytesTransfered + " bytes");
                    break;
                }
                Thread.Sleep(1000);
            }
        }
        Dump(updater.FinishDownloads(), "FinishDownloads");

        foreach (var name in repos.GetAllRepositories())
            Console.WriteLine("  repository " + name + " head " + repos.GetHeadRevision(name) + " (" + repos.GetAllChanges(name).Count + " changes)");

        foreach (var name in activate)
        {
            if (!repos.ContainsRepository(name)) { Check(false, name + " exists"); continue; }
            var e1 = repos.UpdateToHead(name);
            Dump(e1, "UpdateToHead " + name);
            Check(!e1.ContainsError(), name + ": staged to head (xdelta3 / seed, SHA-1 verified)");
            bool deferred;
            var e2 = repos.ActivateToHead(name, out deferred);
            Dump(e2, "ActivateToHead " + name);
            Check(!e2.ContainsError(), name + ": activated (deferred move " + deferred + ")");
            Check(repos.GetActiveRevision(name) == repos.GetHeadRevision(name), name + ": .active == head");
            var v = repos.Verify(name);
            foreach (var x in v) Console.WriteLine("  verify " + name + ": " + x.Code + " " + x.Description);
        }

        if (serverPoll)
        {
            var requests = new List<PollRequest>
            {
                new PollRequest { PollRequestType = PollRequestType.ConfigFiles, SyncId = 0, Data = "[]" },
                new PollRequest { PollRequestType = PollRequestType.StoreInfo, SyncId = 0, Data = new ClientStoreInfo {
                    TimeZoneInfoString = TimeZoneInfo.Local.ToSerializedString(), UTCDateTime = DateTime.UtcNow.AddHours(-2),
                    Version = "3.0.0.43", Info = new Dictionary<string, string> { { "OS Version", Environment.OSVersion.ToString() } } }.ToJson() },
                new PollRequest { PollRequestType = PollRequestType.StoreFiles, SyncId = 0, Data = "2,1,1" },
                new PollRequest { PollRequestType = PollRequestType.FileSets, SyncId = 0, Data = "" },
                new PollRequest { PollRequestType = PollRequestType.StatusMessage, SyncId = 0, Data = new Redbox.UpdateService.Model.StatusMessage {
                    Type = Redbox.UpdateService.Model.StatusMessage.StatusMessageType.Info, Key = "harness", SubKey = "test", Description = "harness status", Data = "hello", Encode = false, TimeStamp = DateTime.UtcNow }.ToJson() },
            };
            List<PollReply> replies;
            ClientCommandResult r;
            using (var client = Redbox.UpdateService.Client.UpdateService.GetService(url, 120000))
                r = client.Poll(store, 2, requests, out replies);
            Check(r.Success && r.Errors.Count == 0, "u pr succeeded with " + replies.Count + " replies " + r.StatusMessage);
            foreach (var reply in replies)
            {
                string what = reply.PollReplyType + " (SId " + reply.SyncId + ")";
                try
                {
                    switch (reply.PollReplyType)
                    {
                        case PollReplyType.ConfigFileChangeSet:
                            var cfg = reply.Data.ToObject<List<ConfigFileChangeSetData>>();
                            Check(cfg.Count > 0, what + ": " + cfg.Count + " config files, first " + cfg[0].Name + " gen " + cfg[0].GenerationOID + " with " + cfg[0].ConfigurationData.Count + " settings");
                            break;
                        case PollReplyType.StoreInfoChangeSet:
                            var si = reply.Data.ToObject<StoreInfoChangeSetData>();
                            Check(si.CorrectDateTime, what + ": CorrectDateTime=" + si.CorrectDateTime + " CurrentUTCDateTime=" + si.CurrentUTCDateTime.ToString("o"));
                            break;
                        case PollReplyType.StoreFileChangeSet:
                            var sf = reply.Data.ToObject<Redbox.UpdateManager.Environment.StoreFileChangeSetData>();
                            Check(Convert.FromBase64String(sf.Base64Data).ToASCIISHA1Hash() == sf.DataHash, what + ": store file " + sf.Path + " hash OK");
                            break;
                        case PollReplyType.FileSetChangeSet:
                            CheckFileSet(reply.Data, updatemgrdExe, what);
                            break;
                        default:
                            Console.WriteLine("  reply " + what + ": " + (reply.Data ?? "").Substring(0, Math.Min(200, (reply.Data ?? "").Length)));
                            break;
                    }
                }
                catch (Exception ex) { Check(false, what + " parse: " + ex.Message); }
            }
        }
        Console.WriteLine(failures == 0 ? "ALL CHECKS PASSED" : failures + " CHECK(S) FAILED");
        return failures;
    }

    // Parses a FileSetChangeSet with updatemgrd's own (internal) model classes, then downloads the revision
    // zip and every file zip from DownloadUrl/Path and checks the hex SHA-1 values like FileSetDownloader does.
    static void CheckFileSet(string data, string updatemgrdExe, string what)
    {
        if (string.IsNullOrEmpty(updatemgrdExe)) { Console.WriteLine("  reply " + what + " (no -UpdatemgrdExe, not parsed)"); return; }
        var asm = System.Reflection.Assembly.LoadFrom(updatemgrdExe);
        var csType = asm.GetType("Redbox.UpdateManager.FileSets.ClientFileSetRevisionChangeSet", true);
        var revType = asm.GetType("Redbox.UpdateManager.FileSets.ClientFileSetRevision", true);
        object cs = data.ToObject(csType);
        Func<object, string, object> get = (o, n) => o.GetType().GetProperty(n).GetValue(o, null);
        string downloadUrl = (string)get(cs, "DownloadUrl"), path = (string)get(cs, "Path"), fileHash = (string)get(cs, "FileHash");
        Check(get(cs, "RevisionId") != null && !string.IsNullOrEmpty(path), what + ": parsed as updatemgrd ClientFileSetRevisionChangeSet (revision " + get(cs, "RevisionId") + ", ActiveOn " + get(cs, "ActiveOn") + ")");
        using (var web = new System.Net.WebClient())
        {
            byte[] zip = web.DownloadData(downloadUrl + "/" + path);
            Check(zip.ToASCIISHA1Hash() == fileHash, what + ": revision zip SHA-1 matches FileHash " + fileHash);
            using (var archive = new System.IO.Compression.ZipArchive(new MemoryStream(zip)))
            using (var reader = new StreamReader(archive.GetEntry("revision").Open()))
            {
                object rev = reader.ReadToEnd().ToObject(revType);
                var files = (System.Collections.IEnumerable)get(rev, "Files");
                int n = 0;
                foreach (var f in files)
                {
                    byte[] fz = web.DownloadData(downloadUrl + "/" + (string)get(f, "Path"));
                    Check(fz.ToASCIISHA1Hash() == (string)get(f, "FileHash"), what + ": file " + get(f, "Name") + " zip SHA-1 OK, destination " + get(f, "FileDestination"));
                    n++;
                }
                Check(n > 0, what + ": manifest lists " + n + " files");
            }
        }
    }
}
