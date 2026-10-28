// Build-time patch of GenuineChannels.dll for Mono (server side only; kiosks keep their own copy).
//
// GenuineUniversalServerTransportSink's static constructor tunes a Microsoft-internal buffer pool by reflection
// (System.Runtime.Remoting.Channels.CoreChannel._bufferPool / System.IO.ByteBufferAllocator). Mono's
// System.Runtime.Remoting has neither type, so the constructor throws and no GenuineTcp channel can be created.
// The pool is only a performance tweak, so its body is replaced by a plain `ret`. Nothing else changes; the
// assembly is not strong-named, so its identity on the wire stays the same.
//   mono PatchGenuineChannels.exe <in GenuineChannels.dll> <out GenuineChannels.dll>
using System;
using System.Linq;
using Mono.Cecil;
using Mono.Cecil.Cil;

public static class PatchGenuineChannels
{
    public static int Main(string[] args)
    {
        var asm = AssemblyDefinition.ReadAssembly(args[0]);
        var type = asm.MainModule.GetType("Belikov.GenuineChannels.DotNetRemotingLayer.GenuineUniversalServerTransportSink");
        var cctor = type?.Methods.FirstOrDefault(m => m.IsConstructor && m.IsStatic);
        if (cctor == null)
        {
            Console.Error.WriteLine("static constructor not found");
            return 1;
        }
        cctor.Body.Instructions.Clear();
        cctor.Body.Variables.Clear();
        cctor.Body.ExceptionHandlers.Clear();
        cctor.Body.GetILProcessor().Append(Instruction.Create(OpCodes.Ret));
        asm.Write(args[1]);
        Console.WriteLine("patched " + type.FullName + "..cctor -> ret");
        return 0;
    }
}
