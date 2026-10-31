using System;
using System.CodeDom.Compiler;
using System.Collections.Generic;
using System.Reflection;
using System.Text;
using System.Text.RegularExpressions;
using Microsoft.JScript;

namespace Redbox.ScriptingHost.Framework;

// Fork: replaces the original VsaScriptingHost, which hosted JScript.NET through Microsoft.JScript.Vsa.VsaEngine
// and the .NET 2 Microsoft.Vsa types. Those do not load on .NET 4, so the host stopped with "[I001] Unable to
// initialize setup". The scripts (page templates, <script>, if="...") are the same JScript.NET; they are now
// compiled with the JScript compiler that every .NET Framework 4 has (jsc.exe, through CodeDom) and run in this
// process. The class keeps its name and members so that the rest of the host is unchanged.
//
// The kernel objects (rix, page, Host) were AppGlobal items of the VSA engine. Here each becomes an untyped
// global variable of the compiled program, read from AppDomain data at its start, so the scripts still use them
// late bound (through KernelObject's IReflect, e.g. page.pathTextBox).
public class VsaScriptingHost : KernelObject, IDisposable
{
	private static readonly string[] m_defaultAssemblyReferences = new string[9] { "System.dll", "System.Core.dll", "System.Data.dll", "System.Drawing.dll", "System.Windows.Forms.dll", "System.Xml.dll", "System.ServiceProcess.dll", "System.Web.Extensions.dll", "Microsoft.JScript.dll" };

	private static readonly Regex m_importLine = new Regex("^\\s*import\\s+[\\w.]+\\s*;\\s*$", RegexOptions.Multiline);

	private readonly IDictionary<string, object> m_evaluationResults = new Dictionary<string, object>();

	private readonly IDictionary<string, IVsaKernelObject> m_instanceCache = new Dictionary<string, IVsaKernelObject>();

	private readonly List<Assembly> m_loadedAssemblies = new List<Assembly>();

	public bool IsRunning { get; private set; }

	public event EventHandler CompileError;

	public VsaScriptingHost()
	{
		SetKernelObject("Host", this);
	}

	public void Stop()
	{
	}

	public void Dispose()
	{
		m_instanceCache.Clear();
		m_loadedAssemblies.Clear();
	}

	public void ExecuteScript(string sourceText)
	{
		string slot = "rix-script-" + Guid.NewGuid().ToString("N");
		CompilerResults compilerResults = Compile(BuildProgram(sourceText, slot));
		if (compilerResults.Errors.HasErrors)
		{
			foreach (CompilerError error in compilerResults.Errors)
			{
				if (!error.IsWarning)
				{
					CompileError?.Invoke($"{error.ErrorNumber} line {error.Line}, column {error.Column}: {error.ErrorText}", EventArgs.Empty);
				}
			}
			return;
		}
		Assembly compiledAssembly = compilerResults.CompiledAssembly;
		m_loadedAssemblies.Add(compiledAssembly);
		foreach (KeyValuePair<string, IVsaKernelObject> item in m_instanceCache)
		{
			AppDomain.CurrentDomain.SetData(slot + ":" + item.Key, item.Value);
		}
		IsRunning = true;
		try
		{
			compiledAssembly.EntryPoint.Invoke(null, new object[1] { new string[0] });
		}
		catch (TargetInvocationException ex) when (ex.InnerException != null)
		{
			throw ex.InnerException;
		}
		finally
		{
			IsRunning = false;
			foreach (string key in m_instanceCache.Keys)
			{
				AppDomain.CurrentDomain.SetData(slot + ":" + key, null);
			}
		}
	}

	// The original went through Host.SetEvaluationResult; here the result comes back as AppDomain data, which needs
	// no late binding on the host object.
	public object EvaluateScript(string sourceText)
	{
		string text = "rix-result-" + Guid.NewGuid().ToString("N");
		ExecuteScript($"System.AppDomain.CurrentDomain.SetData( '{text}', ( {sourceText} ) );");
		object data = AppDomain.CurrentDomain.GetData(text);
		AppDomain.CurrentDomain.SetData(text, null);
		return data;
	}

	public void SetKernelObject(string itemName, IVsaKernelObject instance)
	{
		SetKernelObject(itemName, instance, useTypeInformation: false);
	}

	public void SetKernelObject(string itemName, IVsaKernelObject instance, bool useTypeInformation)
	{
		m_instanceCache[itemName] = instance;
	}

	public bool RemoveKernelObject(string itemName)
	{
		return m_instanceCache.Remove(itemName);
	}

	public bool ContainsKernelObject(string itemName)
	{
		return m_instanceCache.ContainsKey(itemName);
	}

	public IVsaKernelObject GetKernelObject(string itemName)
	{
		return m_instanceCache.ContainsKey(itemName) ? m_instanceCache[itemName] : null;
	}

	public object GetGlobalInstance(string name)
	{
		return m_instanceCache.ContainsKey(name) ? m_instanceCache[name] : null;
	}

	public void SetEvaluationResult(string key, object result)
	{
		m_evaluationResults[key] = result;
	}

	// import statements first (System always: unlike the VSA engine, jsc does not resolve System.* without it), then
	// one global per kernel object, then the script.
	private string BuildProgram(string sourceText, string slot)
	{
		StringBuilder stringBuilder = new StringBuilder();
		stringBuilder.AppendLine("import System;");
		foreach (Match item in m_importLine.Matches(sourceText))
		{
			stringBuilder.AppendLine(item.Value.Trim());
		}
		foreach (string key in m_instanceCache.Keys)
		{
			stringBuilder.AppendFormat("var {0} = System.AppDomain.CurrentDomain.GetData( '{1}:{0}' );\n", key, slot);
		}
		stringBuilder.AppendLine(m_importLine.Replace(sourceText, string.Empty));
		return stringBuilder.ToString();
	}

	private static CompilerResults Compile(string source)
	{
		CompilerParameters compilerParameters = new CompilerParameters
		{
			GenerateExecutable = true,
			GenerateInMemory = true,
			IncludeDebugInformation = false
		};
		// Not the host itself: the scripts reach its objects late bound, and the setup exe carries the whole payload.
		compilerParameters.ReferencedAssemblies.AddRange(m_defaultAssemblyReferences);
		using JScriptCodeProvider jScriptCodeProvider = new JScriptCodeProvider();
		return jScriptCodeProvider.CompileAssemblyFromSource(compilerParameters, source);
	}
}
