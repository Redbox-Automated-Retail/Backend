// Redbox legacy .NET Remoting backends, hosted on Mono with real System.Runtime.Remoting:
//
//  * Reservation broker   gtcp://<host>:7890/RegistrationBroker/Default.rem  (GenuineChannels TCP, binary formatter)
//      The kiosk calls IKioskRegister.Register(kioskId, itself, versionJson) and keeps the connection; the broker
//      later calls back IKioskOperations.Reserve3 / CancelReservation / Ping on it (docs/systems/kiosk-engine.md 6.4).
//  * Kiosk Services       http://<host>:8090/KioskServices/MessageService.rem  (HttpChannel, binary formatter)
//      IMessageService.Process(json) -> json (docs/systems/kiosk-engine.md 6.6). rb-cloudapi terminates TLS for
//      https://bl2.accessredbox.net/... and forwards binary requests here; it answers the REST mode itself.
//
// The host keeps no state of its own: registrations are reported to, and messages answered by, the Python
// backend's internal API (BACKEND_URL, header X-Internal-Token from /certs/internal.key). The dashboard drives
// server-initiated calls through the control API on CONTROL_PORT (internal network only):
//   GET  /broker/kiosks                       connected kiosks
//   POST /broker/reserve  {kioskId, cart}     -> Reserve3(ReservationCart3)  (cart = ReservationCart3 as JSON)
//   POST /broker/cancel   {kioskId, referenceNumber}
//   POST /broker/ping     {kioskId}
using System;
using System.Collections;
using System.Collections.Concurrent;
using System.Collections.Generic;
using System.Collections.Specialized;
using System.IO;
using System.Linq;
using System.Net;
using System.Runtime.Remoting;
using System.Runtime.Remoting.Channels;
using System.Runtime.Remoting.Channels.Http;
using System.Runtime.Serialization.Formatters;
using System.Text;
using System.Threading;
using System.Web.Script.Serialization;
using Belikov.GenuineChannels;
using Belikov.GenuineChannels.DotNetRemotingLayer;
using Belikov.GenuineChannels.GenuineTcp;
using Belikov.GenuineChannels.TransportContext;
using Redbox.KioskServices.ComponentModel;
using Redbox.Services.KioskBrokerServices.KioskShared.DomainObjects;
using Redbox.Services.KioskBrokerServices.KioskShared.ServiceInterfaces;

namespace Redbox.SelfHosted.Remoting
{
    internal static class Log
    {
        public static void Info(string format, params object[] args)
        {
            Console.WriteLine(DateTime.UtcNow.ToString("yyyy-MM-dd HH:mm:ss.fff") + " " + string.Format(format, args));
        }
    }

    internal static class Backend
    {
        static readonly string BaseUrl = (Environment.GetEnvironmentVariable("BACKEND_URL") ?? "http://rb-cloudapi:80").TrimEnd('/');
        static string _token;

        static string Token
        {
            get
            {
                if (_token == null)
                {
                    var path = Environment.GetEnvironmentVariable("INTERNAL_TOKEN_FILE") ?? "/certs/internal.key";
                    _token = File.Exists(path) ? File.ReadAllText(path).Trim() : "";
                }
                return _token;
            }
        }

        public static string Post(string path, string json, int timeoutMs = 30000)
        {
            var req = (HttpWebRequest)WebRequest.Create(BaseUrl + path);
            req.Method = "POST";
            req.ContentType = "application/json; charset=utf-8";
            req.Headers["X-Internal-Token"] = Token;
            req.Timeout = timeoutMs;
            var body = Encoding.UTF8.GetBytes(json ?? "");
            req.ContentLength = body.Length;
            using (var s = req.GetRequestStream()) s.Write(body, 0, body.Length);
            using (var resp = (HttpWebResponse)req.GetResponse())
            using (var r = new StreamReader(resp.GetResponseStream(), Encoding.UTF8))
                return r.ReadToEnd();
        }

        public static void PostQuietly(string path, object obj)
        {
            ThreadPool.QueueUserWorkItem(_ =>
            {
                try { Post(path, new JavaScriptSerializer().Serialize(obj)); }
                catch (Exception e) { Log.Info("backend {0} failed: {1}", path, e.Message); }
            });
        }
    }

    internal class KioskEntry
    {
        public int KioskId;
        public string Version;
        public IKioskOperations Kiosk;
        public DateTime RegisteredUtc;
        public string Host;
        public HostInformation HostInformation;
        /// <summary>Set when GenuineChannels reports the connection broken (it then tries to re-establish it);
        /// calls are refused until the kiosk is connected again instead of hanging until InvocationTimeout.</summary>
        public DateTime? LostUtc;
        public string LostReason;
        /// <summary>Set when a call was not answered in time (the connection may still look alive); cleared by the
        /// next answered call. Calls are still made, each bounded by the timeout.</summary>
        public DateTime? NoAnswerUtc;
        public string NoAnswerReason;
    }

    /// <summary>The broker object kiosks obtain with Activator.GetObject(typeof(IKioskRegister), url).</summary>
    public class KioskRegisterService : MarshalByRefObject, IKioskRegister
    {
        public static readonly KioskRegisterService Instance = new KioskRegisterService();
        internal readonly ConcurrentDictionary<int, KioskEntry> Kiosks = new ConcurrentDictionary<int, KioskEntry>();

        public override object InitializeLifetimeService() { return null; }

        public void Register(int kioskID, IKioskOperations kiosk, string kioskVersion)
        {
            HostInformation host = null;
            try { host = GenuineUtility.CurrentRemoteHost; } catch { }  // the connection this Register came in on
            if (host == null)
                try { host = GenuineUtility.FetchHostInformationFromMbr((MarshalByRefObject)kiosk); } catch { }
            var entry = new KioskEntry
            {
                KioskId = kioskID, Version = kioskVersion, Kiosk = kiosk, RegisteredUtc = DateTime.UtcNow,
                HostInformation = host, Host = host == null ? null : host.ToString()
            };
            Kiosks[kioskID] = entry;
            Log.Info("kiosk {0} registered from {1}: {2}", kioskID, entry.Host, kioskVersion);
            Backend.PostQuietly("/_internal/broker/registered", new Dictionary<string, object>
            {
                { "kioskId", kioskID }, { "version", kioskVersion }, { "host", entry.Host }
            });
        }

        public bool IsRegistered(int kioskID)
        {
            return Kiosks.ContainsKey(kioskID);
        }

        IEnumerable<KioskEntry> On(HostInformation host)
        {
            return Kiosks.Values.Where(k => k.HostInformation != null && (ReferenceEquals(k.HostInformation, host)
                                                                          || k.HostInformation.Uri == host.Uri)).ToList();
        }

        internal void Lost(HostInformation host, string reason)
        {
            foreach (var e in On(host))
                if (e.LostUtc == null)
                {
                    e.LostUtc = DateTime.UtcNow;
                    e.LostReason = reason;
                    Log.Info("kiosk {0} connection lost ({1})", e.KioskId, reason);
                }
        }

        internal void Restored(HostInformation host)
        {
            foreach (var e in On(host))
                if (e.LostUtc != null)
                {
                    e.LostUtc = null;
                    Log.Info("kiosk {0} connection re-established", e.KioskId);
                }
        }

        /// <summary>The kiosk did not answer a call in time. It stays registered (its connection may be fine and it
        /// would not register again), and the dashboard shows it as not answering until a call succeeds.</summary>
        internal void Unresponsive(KioskEntry e, string reason)
        {
            e.NoAnswerUtc = DateTime.UtcNow;
            e.NoAnswerReason = reason;
            Log.Info("kiosk {0} not answering: {1}", e.KioskId, reason);
        }

        internal void Dropped(HostInformation host, string reason)
        {
            foreach (var e in On(host))
            {
                KioskEntry removed;
                if (Kiosks.TryRemove(e.KioskId, out removed))
                {
                    Log.Info("kiosk {0} disconnected ({1})", e.KioskId, reason);
                    Backend.PostQuietly("/_internal/broker/disconnected", new Dictionary<string, object>
                    {
                        { "kioskId", e.KioskId }, { "reason", reason }
                    });
                }
            }
        }
    }

    /// <summary>Kiosk Services 1.81-era message endpoint; every message is answered by the Python backend.</summary>
    public class MessageService : MarshalByRefObject, IMessageService
    {
        public override object InitializeLifetimeService() { return null; }

        public string Process(string payload)
        {
            Log.Info("MessageService.Process {0} bytes", payload == null ? 0 : payload.Length);
            return Backend.Post("/_internal/kioskservices/process?transport=remoting", payload ?? "", 60000);
        }
    }

    internal static class ControlApi
    {
        static readonly JavaScriptSerializer Json = new JavaScriptSerializer { MaxJsonLength = int.MaxValue };

        public static void Start(int port)
        {
            var listener = new HttpListener();
            listener.Prefixes.Add("http://*:" + port + "/");
            listener.Start();
            Log.Info("control API on :{0}", port);
            new Thread(() =>
            {
                while (true)
                {
                    var ctx = listener.GetContext();
                    ThreadPool.QueueUserWorkItem(_ => Handle(ctx));
                }
            }) { IsBackground = true }.Start();
        }

        static void Handle(HttpListenerContext ctx)
        {
            int status = 200;
            object result;
            try
            {
                string body;
                using (var r = new StreamReader(ctx.Request.InputStream, Encoding.UTF8)) body = r.ReadToEnd();
                var args = string.IsNullOrEmpty(body) ? new Dictionary<string, object>() : Json.Deserialize<Dictionary<string, object>>(body);
                result = Dispatch(ctx.Request.HttpMethod, ctx.Request.Url.AbsolutePath, args, out status);
            }
            catch (KeyNotFoundException e)
            {
                status = 404;
                result = new Dictionary<string, object> { { "error", e.Message } };
            }
            catch (BrokerCallException e)
            {
                status = e.Status;
                result = new Dictionary<string, object> { { "error", e.Message }, { "state", e.State } };
            }
            catch (Exception e)
            {
                status = 500;
                result = new Dictionary<string, object> { { "error", e.GetType().Name + ": " + e.Message } };
                Log.Info("control API error: {0}", e);
            }
            var bytes = Encoding.UTF8.GetBytes(Json.Serialize(result));
            ctx.Response.StatusCode = status;
            ctx.Response.ContentType = "application/json";
            ctx.Response.ContentLength64 = bytes.Length;
            ctx.Response.OutputStream.Write(bytes, 0, bytes.Length);
            ctx.Response.Close();
        }

        static KioskEntry Kiosk(Dictionary<string, object> args)
        {
            var id = Convert.ToInt32(args["kioskId"]);
            KioskEntry e;
            if (!KioskRegisterService.Instance.Kiosks.TryGetValue(id, out e))
                throw new KeyNotFoundException("kiosk " + id + " is not connected to the broker");
            if (e.LostUtc != null)
                throw new BrokerCallException(503, "connection_lost", "kiosk " + id + " lost its broker connection at " +
                    e.LostUtc.Value.ToString("u") + " (" + e.LostReason + "); waiting for it to reconnect");
            return e;
        }

        static readonly int CallTimeout = Program.EnvInt("BROKER_CALL_TIMEOUT", 30), PingTimeout = Program.EnvInt("BROKER_PING_TIMEOUT", 10);

        /// <summary>A call into the kiosk, bounded: GenuineChannels would otherwise wait InvocationTimeout (120 s) on a
        /// connection that died without being noticed. A kiosk that does not answer in time is flagged (NoAnswerUtc).</summary>
        static T Call<T>(KioskEntry entry, string what, int seconds, Func<T> call)
        {
            T result = default(T);
            Exception error = null;
            var done = new ManualResetEvent(false);
            ThreadPool.QueueUserWorkItem(_ =>
            {
                try { result = call(); } catch (Exception e) { error = e; }
                done.Set();
            });
            if (!done.WaitOne(TimeSpan.FromSeconds(seconds)))
            {
                string reason = what + " not answered within " + seconds + " s";
                KioskRegisterService.Instance.Unresponsive(entry, reason);
                throw new BrokerCallException(504, "no_answer", "kiosk " + entry.KioskId + ": " + reason +
                    "; the call may still complete on the kiosk");
            }
            if (error != null) throw error;
            entry.NoAnswerUtc = null;
            entry.NoAnswerReason = null;
            return result;
        }

        static object Dispatch(string method, string path, Dictionary<string, object> args, out int status)
        {
            status = 200;
            if (method == "GET" && path == "/broker/kiosks")
                return KioskRegisterService.Instance.Kiosks.Values.OrderBy(k => k.KioskId).Select(k => new Dictionary<string, object>
                {
                    { "kioskId", k.KioskId }, { "version", k.Version }, { "host", k.Host },
                    { "registeredUtc", k.RegisteredUtc.ToString("o") },
                    { "lostUtc", k.LostUtc == null ? null : k.LostUtc.Value.ToString("o") }, { "lostReason", k.LostReason },
                    { "noAnswerUtc", k.NoAnswerUtc == null ? null : k.NoAnswerUtc.Value.ToString("o") }, { "noAnswerReason", k.NoAnswerReason }
                }).ToList();
            if (method == "GET" && path == "/health")
                return new Dictionary<string, object> { { "ok", true }, { "kiosks", KioskRegisterService.Instance.Kiosks.Count } };
            if (method == "POST" && path == "/broker/ping")
            {
                var entry = Kiosk(args);
                Call(entry, "Ping", PingTimeout, () => { entry.Kiosk.Ping(); return true; });
                return new Dictionary<string, object> { { "ok", true } };
            }
            if (method == "POST" && path == "/broker/reserve")
            {
                var entry = Kiosk(args);
                var cart = Json.ConvertToType<ReservationCart3>(args["cart"]);
                cart.KioskID = entry.KioskId;
                Log.Info("Reserve3 -> kiosk {0}, reference {1}, {2} titles", entry.KioskId, cart.ReferenceNumber,
                         cart.Titles == null ? 0 : cart.Titles.Count);
                return Call(entry, "Reserve3", CallTimeout, () => entry.Kiosk.Reserve3(cart));
            }
            if (method == "POST" && path == "/broker/cancel")
            {
                var entry = Kiosk(args);
                long reference = Convert.ToInt64(args["referenceNumber"]);
                return Call(entry, "CancelReservation", CallTimeout, () => entry.Kiosk.CancelReservation(reference));
            }
            status = 404;
            return new Dictionary<string, object> { { "error", "not found" } };
        }
    }

    internal class BrokerCallException : Exception
    {
        public readonly int Status;
        public readonly string State;
        public BrokerCallException(int status, string state, string message) : base(message) { Status = status; State = state; }
    }

    public static class Program
    {
        internal static int EnvInt(string name, int fallback)
        {
            int v;
            return int.TryParse(Environment.GetEnvironmentVariable(name), out v) ? v : fallback;
        }

        public static void Main()
        {
            int brokerPort = EnvInt("BROKER_PORT", 7890), httpPort = EnvInt("MESSAGESERVICE_PORT", 8090),
                controlPort = EnvInt("CONTROL_PORT", 8091);

            // Reservation broker: same channel parameters as the kiosk's BrokerServicesProxy, plus a listening port.
            var gprops = new ListDictionary
            {
                { "name", "gtcp" }, { "port", brokerPort }, { "ConnectTimeout", 5000 }, { "MaxTotalSize", 500000 },
                { "MaxContentSize", 500000 }, { "MaxQueuedItems", 500000 }, { "InvocationTimeout", 120000 },
                { "PersistentConnectionSendPingAfterInactivity", 20000 }
            };
            var gServer = new BinaryServerFormatterSinkProvider { TypeFilterLevel = TypeFilterLevel.Full };
            ChannelServices.RegisterChannel(new GenuineTcpChannel(gprops, new BinaryClientFormatterSinkProvider(), gServer), false);
            RemotingServices.Marshal(KioskRegisterService.Instance, "RegistrationBroker/Default.rem", typeof(IKioskRegister));
            GenuineGlobalEventProvider.GenuineChannelsGlobalEvent += (sender, e) =>
            {
                Log.Info("GenuineChannels event {0} {1} {2}", e.EventType, e.HostInformation,
                         e.SourceException == null ? "" : e.SourceException.Message);
                if (e.HostInformation == null) return;
                if (e.EventType == GenuineEventType.GeneralConnectionClosed || e.EventType == GenuineEventType.HostResourcesReleased)
                    KioskRegisterService.Instance.Dropped(e.HostInformation, e.EventType.ToString());
                else if (e.EventType == GenuineEventType.GeneralConnectionReestablishing)
                    KioskRegisterService.Instance.Lost(e.HostInformation,
                        e.SourceException == null ? e.EventType.ToString() : e.SourceException.Message);
                else if (e.EventType == GenuineEventType.GeneralConnectionEstablished)
                    KioskRegisterService.Instance.Restored(e.HostInformation);
            };
            Log.Info("broker on gtcp://0.0.0.0:{0}/RegistrationBroker/Default.rem", brokerPort);

            // Kiosk Services MessageService.
            var hprops = new ListDictionary { { "name", "http" }, { "port", httpPort } };
            var hServer = new BinaryServerFormatterSinkProvider { TypeFilterLevel = TypeFilterLevel.Full };
            ChannelServices.RegisterChannel(new HttpChannel(hprops, new BinaryClientFormatterSinkProvider(), hServer), false);
            RemotingServices.Marshal(new MessageService(), "KioskServices/MessageService.rem", typeof(IMessageService));
            Log.Info("MessageService on http://0.0.0.0:{0}/KioskServices/MessageService.rem", httpPort);

            ControlApi.Start(controlPort);
            Thread.Sleep(Timeout.Infinite);
        }
    }
}
