using System;
using System.Collections.Generic;
using System.Collections.ObjectModel;
using System.Collections.Specialized;
using System.Diagnostics;
using System.DirectoryServices.AccountManagement;
using System.Net;
using System.Net.NetworkInformation;
using System.Net.Sockets;
using System.Windows.Forms;
using Microsoft.Win32;
using Redbox.Compression;
using Redbox.Core;
using Redbox.IIS;
using Redbox.Installer.Client;
using Redbox.Macros;
using Redbox.ScriptingHost.Framework;

namespace Redbox.Installer.Framework;

public class ScriptKernelObject : KernelObject
{
	public ReadOnlyCollection<Definition> prohibitedProcesses => InstallerProject.Instance.Definitions.GetProhibitedProcessesForNotify().AsReadOnly();

	public string[] networkInterfaces
	{
		get
		{
			List<string> list = new List<string>();
			NetworkInterface[] allNetworkInterfaces = NetworkInterface.GetAllNetworkInterfaces();
			foreach (NetworkInterface networkInterface in allNetworkInterfaces)
			{
				IPAddress iPAddress = null;
				foreach (UnicastIPAddressInformation unicastAddress in networkInterface.GetIPProperties().UnicastAddresses)
				{
					if (unicastAddress.Address.AddressFamily == AddressFamily.InterNetwork)
					{
						iPAddress = unicastAddress.Address;
						break;
					}
				}
				if (iPAddress != null)
				{
					list.Add($"{networkInterface.Name} ({iPAddress})");
				}
			}
			return list.ToArray();
		}
	}

	public string[] timezones
	{
		get
		{
			List<string> list = new List<string>();
			foreach (TimeZoneInfo systemTimeZone in TimeZoneInfo.GetSystemTimeZones())
			{
				list.Add($"{systemTimeZone.DisplayName} ({systemTimeZone.BaseUtcOffset})");
			}
			return list.ToArray();
		}
	}

	public IList<IIsApplicationPool> appPools => IIsApplicationPool.GetAll();

	public IList<IIsWebsite> webSites => IIsWebsite.GetAll();

	public IPAddress[] hostAddresses => Dns.GetHostAddresses(Dns.GetHostName());

	public StringDictionary macros => MacroContainer.Instance.Values;

	public InstallerProject project => InstallerProject.Instance;

	public string productVersion => InstallerProject.Instance.ProductVersion;

	public string productName => InstallerProject.Instance.ProductName;

	public string setupName => $"{InstallerProject.Instance.ProductName} {InstallerProject.Instance.ProductVersion} Setup";

	public void log(string message)
	{
		InstallerProject.Instance.ActiveConsole.WriteLine(message);
	}

	public void alert(string message)
	{
		MessageBox.Show(message, setupName, MessageBoxButtons.OK, MessageBoxIcon.Asterisk);
	}

	public void warning(string message)
	{
		MessageBox.Show(message, setupName, MessageBoxButtons.OK, MessageBoxIcon.Exclamation);
	}

	public bool confirm(string message)
	{
		DialogResult dialogResult = MessageBox.Show(message, setupName, MessageBoxButtons.YesNo, MessageBoxIcon.Question);
		return dialogResult == DialogResult.Yes;
	}

	public string expandConstantMacros(string value)
	{
		return MacroContainer.Instance.ExpandConstantMacros(value);
	}

	public string expandRuntimeMacros(string value)
	{
		return MacroContainer.Instance.ExpandRuntimeMacros(value);
	}

	public string extractCodeFromBrackets(string value, string prefix, string postfix)
	{
		return GlobalStringExtensions.ExtractCodeFromBrackets(value, prefix, postfix);
	}

	public void unzip(string sourceFile, string targetPath)
	{
		ZipHelper.ExtractAllFiles(sourceFile, targetPath);
	}

	// Fork: the host is AnyCPU and runs as a 64-bit process, so Registry.LocalMachine was the 64-bit view, while the
	// x86 Kiosk Engine and the front end read HKLM\SOFTWARE through WOW6432Node. Write the 32-bit view, as they do.
	public void SetLocalMachineRegistry(string path, string name, object value)
	{
		using RegistryKey baseKey = RegistryKey.OpenBaseKey(RegistryHive.LocalMachine, RegistryView.Registry32);
		using RegistryKey registryKey = baseKey.CreateSubKey(path);
		registryKey.SetValue(name, value);
	}

	// Fork: a top-level value from a JSON file (the kiosk profile page reads its KioskId), or an empty string when the
	// file or the value is missing (not null: JScript.NET would turn that into "undefined").
	public string readJsonValue(string fileName, string name)
	{
		try
		{
			if (!System.IO.File.Exists(fileName))
			{
				return string.Empty;
			}
			object parsed = new System.Web.Script.Serialization.JavaScriptSerializer().DeserializeObject(System.IO.File.ReadAllText(fileName));
			return parsed is IDictionary<string, object> dictionary && dictionary.TryGetValue(name, out object value) && value != null ? Convert.ToString(value, System.Globalization.CultureInfo.InvariantCulture) : string.Empty;
		}
		catch (Exception value)
		{
			InstallerProject.Instance.ActiveConsole.WriteLine(value);
			return string.Empty;
		}
	}

	public void launchprocessblocking(string name, string arguments)
	{
		try
		{
			ProcessStartInfo startInfo = new ProcessStartInfo(name, arguments);
			using Process process = new Process();
			process.StartInfo = startInfo;
			process.Start();
			process.WaitForExit();
		}
		catch (Exception value)
		{
			InstallerProject.Instance.ActiveConsole.WriteLine(value);
		}
	}

	public void launchprocess(string name, string arguments)
	{
		try
		{
			ProcessStartInfo startInfo = new ProcessStartInfo(name, arguments);
			using Process process = new Process();
			process.StartInfo = startInfo;
			process.Start();
		}
		catch (Exception value)
		{
			InstallerProject.Instance.ActiveConsole.WriteLine(value);
		}
	}

	public string encrypt(string input)
	{
		return PasswordHelper.Encrypt(input);
	}

	public bool validateUser(string username, string password)
	{
		string[] array = username.Split('\\');
		ContextType contextType;
		string name;
		if (array.Length == 1)
		{
			contextType = ContextType.Machine;
			name = Environment.MachineName;
		}
		else
		{
			if (array.Length != 2)
			{
				return false;
			}
			contextType = ContextType.Domain;
			name = array[0];
			username = array[1];
		}
		using PrincipalContext principalContext = new PrincipalContext(contextType, name);
		return principalContext.ValidateCredentials(username, password);
	}
}
