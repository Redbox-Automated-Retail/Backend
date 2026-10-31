using System;
using System.Collections.Generic;
using System.IO;
using System.Runtime.Serialization.Formatters.Binary;
using System.Text;
using System.Text.RegularExpressions;
using System.Xml;
using Redbox.Compression;
using Redbox.Core;

// The RIX compiler side, which Redbox did not ship (the decompiled FileEntryDefinition.SaveToResource is cut short):
// turns a setup XML and the files it names into one setup exe, in the layout the host reads
// (software/docs/systems/store-installer.md section 2.1):
//   PREREQ/SCRIPT  the setup XML, LZMA ("LZMA alone": 5 property bytes, 8-byte length, stream)
//   PREREQ/PROPS   BinaryFormatter Dictionary<string,string> of -D properties
//   FILE/NNNN      every <file> that is not external, numbered in script order (resource-name="NNNN" is added to
//                  the element); LZMA-compressed when the element has compression="lzma"
internal static class Program
{
	private static int Main(string[] args)
	{
		if (args.Length < 4)
		{
			Console.Error.WriteLine("usage: rixc <setup.xml> <source dir> <host.exe> <out setup.exe> [-D:name=value ...]");
			return 2;
		}
		try
		{
			Build(args[0], args[1], args[2], args[3], ParseProperties(args, 4));
			return 0;
		}
		catch (Exception ex)
		{
			Console.Error.WriteLine("rixc: " + ex.Message);
			return 1;
		}
	}

	private static Dictionary<string, string> ParseProperties(string[] args, int start)
	{
		Dictionary<string, string> properties = new Dictionary<string, string>();
		for (int i = start; i < args.Length; i++)
		{
			Match match = Regex.Match(args[i], "^-D:([^=]+)=(.*)$");
			if (!match.Success)
			{
				throw new ArgumentException("unknown argument " + args[i]);
			}
			properties[match.Groups[1].Value] = match.Groups[2].Value;
		}
		return properties;
	}

	private static void Build(string scriptPath, string sourceDir, string hostPath, string outPath, Dictionary<string, string> properties)
	{
		XmlDocument xml = new XmlDocument { PreserveWhitespace = true };
		xml.Load(scriptPath);
		Dictionary<string, string> values = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
		foreach (XmlElement property in xml.DocumentElement.SelectNodes("property"))
		{
			values[property.GetAttribute("name")] = property.GetAttribute("value");
		}
		foreach (KeyValuePair<string, string> property in properties)
		{
			values[property.Key] = property.Value;
		}

		string tempPath = outPath + ".tmp";
		File.Copy(hostPath, tempPath, overwrite: true);
		ResourceUpdateSession session = new ResourceUpdateSession(tempPath);
		CompressionAlgorithm lzma = CompressionAlgorithm.GetAlgorithm(CompressionType.LZMA);
		int number = 0;
		long total = 0;
		foreach (XmlElement file in xml.DocumentElement.SelectNodes("file"))
		{
			if (file.GetAttribute("external") == "true")
			{
				Console.WriteLine($"  external  {file.GetAttribute("source-path")}");
				continue;
			}
			string source = Path.Combine(sourceDir, Expand(file.GetAttribute("source-path"), values));
			if (!File.Exists(source))
			{
				throw new FileNotFoundException($"<file id=\"{file.GetAttribute("id")}\">: {source} not found");
			}
			byte[] data = File.ReadAllBytes(source);
			if (file.GetAttribute("compression") == "lzma")
			{
				data = lzma.Compress(data);
			}
			string name = (++number).ToString("0000");
			file.SetAttribute("resource-name", name);
			session.AddResource("FILE", name, data);
			total += data.Length;
			Console.WriteLine($"  FILE/{name} {data.Length,10:n0}  {file.GetAttribute("source-path")}");
		}

		string script = xml.OuterXml;
		foreach (char c in script)
		{
			if (c > 0x7f)
			{
				throw new InvalidDataException($"the setup XML must be ASCII (the host decodes it as ASCII); found U+{(int)c:X4}");
			}
		}
		session.AddResource("PREREQ", "SCRIPT", lzma.Compress(Encoding.ASCII.GetBytes(script)));
		using (MemoryStream stream = new MemoryStream())
		{
			new BinaryFormatter().Serialize(stream, properties);
			session.AddResource("PREREQ", "PROPS", stream.ToArray());
		}
		session.Save();
		File.Copy(tempPath, outPath, overwrite: true);
		File.Delete(tempPath);
		Console.WriteLine($"{outPath}: {number} files, {total:n0} bytes of payload, {properties.Count} properties");
	}

	private static string Expand(string value, Dictionary<string, string> values)
	{
		for (int i = 0; i < 5; i++)
		{
			value = Regex.Replace(value, "\\$[{(]([^})]+)[})]", m => values.TryGetValue(m.Groups[1].Value, out string v) ? v : m.Value);
		}
		return value;
	}
}
