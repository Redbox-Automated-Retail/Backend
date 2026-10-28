// Stand-ins for the kiosk engine services a kiosk-side proxy asks the ServiceLocator for, so that the proxy's own
// methods (which build the request, call the server and parse the reply) can run outside the engine.
//
// StubFactory.Create(interface) emits a class whose every member calls StubHub.Invoke; StubHub returns a value
// registered for "<Interface>.<member>" or a default: empty lists/dictionaries for collection interfaces, another
// stub for other interfaces, default(T) otherwise. Only values a test registers are "data"; everything else is the
// neutral answer of an idle engine.
using System;
using System.Collections;
using System.Collections.Generic;
using System.Linq;
using System.Reflection;
using System.Reflection.Emit;
using System.Text;
using System.Threading;

public static class StubHub
{
    public static readonly Dictionary<string, Func<object[], object>> Values = new Dictionary<string, Func<object[], object>>();
    public static readonly List<string> Calls = new List<string>();
    public static bool Trace;

    public static void Set(string member, object value) { Values[member] = _ => value; }
    public static void Set(string member, Func<object[], object> f) { Values[member] = f; }

    /// <summary>For generic members (T GetValue&lt;T&gt;(...)): the function also gets the return type.</summary>
    public static readonly Dictionary<string, Func<Type, object[], object>> Typed = new Dictionary<string, Func<Type, object[], object>>();

    public static object Invoke(string iface, string member, Type ret, object[] args)
    {
        string key = iface + "." + member;
        if (Trace) lock (Calls) Calls.Add(key);
        Func<object[], object> f;
        if (Values.TryGetValue(key, out f)) return f(args);
        Func<Type, object[], object> g;
        if (Typed.TryGetValue(key, out g)) return g(ret, args) ?? Default(ret);
        return Default(ret);
    }

    public static object Default(Type t)
    {
        if (t == typeof(void) || t == typeof(string)) return null;
        if (t.IsValueType) return Activator.CreateInstance(t);
        if (t.IsArray) return Array.CreateInstance(t.GetElementType(), 0);
        if (t.IsGenericType)
        {
            var def = t.GetGenericTypeDefinition();
            var a = t.GetGenericArguments();
            if (def == typeof(IEnumerable<>) || def == typeof(IList<>) || def == typeof(ICollection<>) ||
                def == typeof(IReadOnlyList<>) || def == typeof(IReadOnlyCollection<>) || def == typeof(List<>))
                return Activator.CreateInstance(typeof(List<>).MakeGenericType(a));
            if (def == typeof(IDictionary<,>) || def == typeof(Dictionary<,>) || def == typeof(IReadOnlyDictionary<,>))
                return Activator.CreateInstance(typeof(Dictionary<,>).MakeGenericType(a));
            if (def == typeof(System.Collections.ObjectModel.ReadOnlyCollection<>))
                return Activator.CreateInstance(t, Activator.CreateInstance(typeof(List<>).MakeGenericType(a)));
            if (def == typeof(System.Threading.Tasks.Task<>))
                return typeof(System.Threading.Tasks.Task).GetMethod("FromResult").MakeGenericMethod(a).Invoke(null, new[] { Default(a[0]) });
        }
        if (t == typeof(System.Threading.Tasks.Task)) return System.Threading.Tasks.Task.FromResult(0);
        if (t == typeof(IEnumerable) || t == typeof(IList)) return new ArrayList();
        if (t.IsInterface) return StubFactory.Create(t);
        if (!t.IsAbstract && t.GetConstructor(Type.EmptyTypes) != null) return Activator.CreateInstance(t);
        return null;
    }
}

public static class StubFactory
{
    static readonly ModuleBuilder Module = AppDomain.CurrentDomain
        .DefineDynamicAssembly(new AssemblyName("EngineStubs.Dynamic"), AssemblyBuilderAccess.Run).DefineDynamicModule("m");
    static readonly Dictionary<Type, Type> Types = new Dictionary<Type, Type>();
    static readonly MethodInfo InvokeMethod = typeof(StubHub).GetMethod("Invoke");
    static readonly MethodInfo TypeFromHandle = typeof(Type).GetMethod("GetTypeFromHandle");

    public static object Create(Type iface)
    {
        Type t;
        lock (Types)
        {
            if (!Types.TryGetValue(iface, out t)) Types[iface] = t = Build(iface);
        }
        return Activator.CreateInstance(t);
    }

    public static T Create<T>() { return (T)Create(typeof(T)); }

    static Type Build(Type iface)
    {
        var tb = Module.DefineType("Stub_" + iface.FullName.Replace('.', '_').Replace('`', '_') + "_" + Types.Count,
                                   TypeAttributes.Public | TypeAttributes.Class);
        tb.AddInterfaceImplementation(iface);
        var all = new List<Type> { iface };
        all.AddRange(iface.GetInterfaces());
        foreach (var it in all.Distinct())
            foreach (var m in it.GetMethods())
                Implement(tb, it, iface, m);
        return tb.CreateType();
    }

    static void Implement(TypeBuilder tb, Type it, Type root, MethodInfo m)
    {
        var ps = m.GetParameters();
        var mb = tb.DefineMethod(it.FullName + "." + m.Name, MethodAttributes.Private | MethodAttributes.Virtual |
            MethodAttributes.Final | MethodAttributes.HideBySig | MethodAttributes.NewSlot);
        Type ret = m.ReturnType;
        Type[] pts = ps.Select(p => p.ParameterType).ToArray();
        if (m.IsGenericMethodDefinition)
        {
            var gargs = m.GetGenericArguments();
            var gps = mb.DefineGenericParameters(gargs.Select(g => g.Name).ToArray());
            Func<Type, Type> map = null;
            map = x =>
            {
                if (x.IsGenericParameter && x.DeclaringMethod != null) return gps[x.GenericParameterPosition];
                if (x.IsByRef) return map(x.GetElementType()).MakeByRefType();
                if (x.IsArray) return map(x.GetElementType()).MakeArrayType();
                if (x.IsGenericType && x.ContainsGenericParameters)
                    return x.GetGenericTypeDefinition().MakeGenericType(x.GetGenericArguments().Select(map).ToArray());
                return x;
            };
            ret = map(ret);
            pts = pts.Select(map).ToArray();
        }
        mb.SetReturnType(ret);
        mb.SetParameters(pts);
        var il = mb.GetILGenerator();
        il.Emit(OpCodes.Ldstr, root.Name);
        il.Emit(OpCodes.Ldstr, m.Name);
        if (ret == typeof(void)) il.Emit(OpCodes.Ldtoken, typeof(void)); else il.Emit(OpCodes.Ldtoken, ret);
        il.Emit(OpCodes.Call, TypeFromHandle);
        il.Emit(OpCodes.Ldc_I4, pts.Length);
        il.Emit(OpCodes.Newarr, typeof(object));
        for (int i = 0; i < pts.Length; i++)
        {
            if (pts[i].IsByRef || pts[i].IsPointer) continue;  // out/ref parameters are left as they are
            il.Emit(OpCodes.Dup);
            il.Emit(OpCodes.Ldc_I4, i);
            il.Emit(OpCodes.Ldarg, i + 1);
            if (pts[i].IsValueType || pts[i].IsGenericParameter) il.Emit(OpCodes.Box, pts[i]);
            il.Emit(OpCodes.Stelem_Ref);
        }
        il.Emit(OpCodes.Call, InvokeMethod);
        if (ret == typeof(void)) il.Emit(OpCodes.Pop);
        else il.Emit(OpCodes.Unbox_Any, ret);
        il.Emit(OpCodes.Ret);
        tb.DefineMethodOverride(mb, m);
    }
}

/// <summary>Readable dump of a result object graph (public properties, depth-limited).</summary>
public static class Dump
{
    public static string Of(object o, int depth = 4)
    {
        var sb = new StringBuilder();
        Write(sb, o, depth, new HashSet<object>(ReferenceEqualityComparer.Instance));
        return sb.ToString();
    }

    sealed class ReferenceEqualityComparer : IEqualityComparer<object>
    {
        public static readonly ReferenceEqualityComparer Instance = new ReferenceEqualityComparer();
        public new bool Equals(object a, object b) { return ReferenceEquals(a, b); }
        public int GetHashCode(object o) { return System.Runtime.CompilerServices.RuntimeHelpers.GetHashCode(o); }
    }

    static void Write(StringBuilder sb, object o, int depth, HashSet<object> seen)
    {
        if (o == null) { sb.Append("null"); return; }
        var t = o.GetType();
        if (o is string) { sb.Append('"').Append(o).Append('"'); return; }
        if (t.IsPrimitive || o is decimal || o is Enum || o is Guid || o is TimeSpan) { sb.Append(o); return; }
        if (o is DateTime) { sb.Append(((DateTime)o).ToString("o")); return; }
        if (depth <= 0 || !seen.Add(o)) { sb.Append("…"); return; }
        if (o is IDictionary)
        {
            sb.Append('{');
            bool first = true;
            foreach (DictionaryEntry e in (IDictionary)o)
            {
                if (!first) sb.Append(", ");
                first = false;
                sb.Append(e.Key).Append(": ");
                Write(sb, e.Value, depth - 1, seen);
            }
            sb.Append('}');
            return;
        }
        if (o is IEnumerable)
        {
            sb.Append('[');
            int n = 0;
            foreach (var x in (IEnumerable)o)
            {
                if (n++ > 0) sb.Append(", ");
                if (n > 12) { sb.Append("…"); break; }
                Write(sb, x, depth - 1, seen);
            }
            sb.Append(']');
            return;
        }
        sb.Append(t.Name).Append('{');
        bool f = true;
        foreach (var p in t.GetProperties(BindingFlags.Public | BindingFlags.Instance))
        {
            if (p.GetIndexParameters().Length > 0) continue;
            object v;
            try { v = p.GetValue(o, null); } catch (Exception e) { v = "<" + (e.InnerException ?? e).GetType().Name + ">"; }
            if (v == null) continue;
            if (!f) sb.Append(", ");
            f = false;
            sb.Append(p.Name).Append('=');
            Write(sb, v, depth - 1, seen);
        }
        sb.Append('}');
    }
}
