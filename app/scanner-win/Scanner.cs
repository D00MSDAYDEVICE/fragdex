// SPDX-License-Identifier: GPL-2.0-or-later
// Scanning engine of the Windows fragdex-scan: a C# port of fragdex/importers/demo.py and
// fragdex/scanner.py. Keep the two in step; tests/test_importers.py compares their output.
using System;
using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.IO.Compression;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;
using System.Threading.Tasks;
using Microsoft.Win32;

namespace Fragdex
{
    // ------------------------------------------------------------ Huffman bit reader

    sealed class BitReader
    {
        static readonly int Peek;
        static readonly int Mask;
        static readonly int[] Table;   // next Peek bits -> symbol | (code length << 16)

        static BitReader()
        {
            string[] codes = HuffmanTable.Codes;
            foreach (string c in codes) Peek = Math.Max(Peek, c.Length);
            Mask = (1 << Peek) - 1;
            Table = new int[1 << Peek];
            for (int sym = 0; sym < codes.Length; sym++)
            {
                string code = codes[sym];
                int k = code.Length, low = 0;
                for (int i = 0; i < k; i++) if (code[i] == '1') low |= 1 << i;
                for (int high = 0; high < 1 << (Peek - k); high++)
                    Table[low | (high << k)] = (sym < 256 ? sym : 0) | (k << 16);
            }
        }

        readonly byte[] d;   // message + 4 zero bytes
        readonly int size;   // in bits
        int bit;
        public bool End;     // ran past the message: the current read is invalid

        public BitReader(byte[] data, int length) { d = data; size = length * 8; }

        int RawBit()
        {
            if (bit >= size) { End = true; return 0; }
            int b = (d[bit >> 3] >> (bit & 7)) & 1;
            bit++;
            return b;
        }

        int HuffByte()
        {
            int pos = bit;
            if (pos >= size) { End = true; return 0; }
            int i = pos >> 3;
            int window = (d[i] | (d[i + 1] << 8) | (d[i + 2] << 16)) >> (pos & 7);
            int e = Table[window & Mask], k = e >> 16;
            if (k == 0 || pos + k > size) { End = true; return 0; }
            bit = pos + k;
            return e & 0xFFFF;
        }

        // MSG_ReadBits for a non-OOB message: low bits raw, then Huffman bytes
        public long ReadBits(int bits)
        {
            long value = 0;
            int nbits = bits & 7;
            for (int i = 0; i < nbits; i++) value |= (long)RawBit() << i;
            for (int i = 0; i < bits - nbits; i += 8) value |= (long)HuffByte() << (i + nbits);
            return value;
        }

        public int ReadByte() { return (int)ReadBits(8); }
        public int ReadShort() { return (short)ReadBits(16); }

        public string ReadString(int limit)
        {
            var bytes = new List<byte>();
            while (true)
            {
                int c = ReadByte();
                if (End || c == 0) break;
                if (bytes.Count < limit) bytes.Add((byte)c);
            }
            return Encoding.UTF8.GetString(bytes.ToArray());
        }
    }

    // ------------------------------------------------------------ name helpers (fragdex/importers/common.py)

    static class Names
    {
        static readonly Regex Color = new Regex(@"\^[0-9A-Za-z]");
        static readonly Regex TrailingColor = new Regex(@"(\^[0-9A-Za-z])+$");
        public static readonly Regex Steam64 = new Regex(@"^7656119[0-9]{10}$");
        public static readonly Regex Steam64Any = new Regex(@"(?<![0-9])7656119[0-9]{10}(?![0-9])");

        static bool IsSpace(char c) { return char.IsWhiteSpace(c) || (c >= '\x1c' && c <= '\x1f'); }

        // Python's " ".join(s.split())
        public static string Collapse(string s)
        {
            var sb = new StringBuilder(s.Length);
            bool pending = false;
            foreach (char c in s)
            {
                if (IsSpace(c)) { pending = sb.Length > 0; continue; }
                if (pending) { sb.Append(' '); pending = false; }
                sb.Append(c);
            }
            return sb.ToString();
        }

        // s[:n] counting code points, like Python
        public static string Truncate(string s, int n)
        {
            int i = 0;
            for (int count = 0; i < s.Length && count < n; count++)
                i += char.IsHighSurrogate(s[i]) && i + 1 < s.Length ? 2 : 1;
            return i < s.Length ? s.Substring(0, i) : s;
        }

        public static string Clean(string name) { return Collapse(Color.Replace(name ?? "", "")); }

        public static string Normalize(string name)
        {
            if (name == null) return "";
            string s = TrailingColor.Replace(Collapse(name), "").Trim();
            while (s.StartsWith("^7", StringComparison.Ordinal) && s.Length > 2) s = s.Substring(2);
            return Truncate(s, 64);
        }

        public static string FormatUtc(DateTime utc) { return utc.ToString("yyyy-MM-dd HH:mm:ss", CultureInfo.InvariantCulture); }
    }

    // ------------------------------------------------------------ demo parsing (fragdex/importers/demo.py)

    sealed class InfoString
    {
        public readonly List<string> Keys = new List<string>();
        public readonly Dictionary<string, string> Values = new Dictionary<string, string>();

        public InfoString(string s)
        {
            string[] parts = (s.StartsWith("\\") ? s.Substring(1) : s).Split('\\');
            for (int i = 0; i + 1 < parts.Length; i += 2)
            {
                if (!Values.ContainsKey(parts[i])) Keys.Add(parts[i]);
                Values[parts[i]] = parts[i + 1];
            }
        }

        public string Get(string key, string fallback)
        {
            string v;
            return Values.TryGetValue(key, out v) ? v : fallback;
        }
    }

    sealed class DemoParser
    {
        const int SvcNop = 1, SvcGamestate = 2, SvcConfigstring = 3, SvcServerCommand = 5, SvcEof = 8;
        const int MaxMsgLen = 1 << 20;
        static readonly Regex CsCommand = new Regex("^(cs|bcs0|bcs1|bcs2)\\s+([0-9]+)\\s+\"(.*)\"\\s*$", RegexOptions.Singleline);

        public readonly Dictionary<long, string> Configstrings = new Dictionary<long, string>();
        public readonly List<string[]> Players = new List<string[]>();   // [steam id, raw name], first-seen order
        readonly HashSet<string> seen = new HashSet<string>();
        readonly Dictionary<long, string> bcs = new Dictionary<long, string>();

        static string[] PlayerFrom(string s)
        {
            if (s.IndexOf('\\') < 0 || s.IndexOf("7656119", StringComparison.Ordinal) < 0) return null;
            var info = new InfoString(s);
            string name, sid;
            if (!info.Values.TryGetValue("n", out name)) return null;
            sid = info.Get("st", "");
            if (!Names.Steam64.IsMatch(sid))
            {
                sid = null;
                foreach (string k in info.Keys)
                    if (Names.Steam64.IsMatch(info.Values[k])) { sid = info.Values[k]; break; }
                if (sid == null)
                {
                    Match m = Names.Steam64Any.Match(s);
                    if (m.Success) sid = m.Value;
                }
            }
            return string.IsNullOrEmpty(sid) ? null : new[] { sid, name };
        }

        void SetCs(long index, string value)
        {
            Configstrings[index] = value;
            string[] p = PlayerFrom(value);
            if (p != null && seen.Add(p[0] + "\0" + p[1])) Players.Add(p);
        }

        void ServerCommand(string text)
        {
            Match m = CsCommand.Match(text);
            if (!m.Success) return;
            string cmd = m.Groups[1].Value, val = m.Groups[3].Value;
            long idx;
            if (!long.TryParse(m.Groups[2].Value, NumberStyles.None, CultureInfo.InvariantCulture, out idx)) return;
            string prev;
            if (cmd == "cs") SetCs(idx, val);
            else if (cmd == "bcs0") bcs[idx] = val;
            else if (cmd == "bcs1") bcs[idx] = (bcs.TryGetValue(idx, out prev) ? prev : "") + val;
            else
            {
                SetCs(idx, (bcs.TryGetValue(idx, out prev) ? prev : "") + val);
                bcs.Remove(idx);
            }
        }

        // Only the gamestate's configstrings and "cs" server commands are needed; both come
        // before any snapshot in a message, so decoding stops at the first snapshot/baseline.
        void ParseMessage(byte[] data, int length)
        {
            var r = new BitReader(data, length);
            r.ReadBits(32);   // reliable acknowledge
            if (r.End) return;
            while (true)
            {
                int cmd = r.ReadByte();
                if (r.End || cmd == SvcEof) return;
                if (cmd == SvcNop) continue;
                if (cmd == SvcServerCommand)
                {
                    r.ReadBits(32);
                    if (r.End) return;
                    ServerCommand(r.ReadString(1024));
                }
                else if (cmd == SvcGamestate)
                {
                    r.ReadBits(32);
                    if (r.End) return;
                    while (true)
                    {
                        int op = r.ReadByte();
                        if (r.End || op != SvcConfigstring) return;
                        int idx = r.ReadShort();
                        if (r.End) return;
                        SetCs(idx, r.ReadString(8192));
                    }
                }
                else return;   // snapshot, download or unknown
            }
        }

        public void Parse(byte[] file)
        {
            int pos = 0;
            while (pos + 8 <= file.Length)
            {
                int length = BitConverter.ToInt32(file, pos + 4);
                pos += 8;
                if (length < 0 || length > MaxMsgLen || pos + length > file.Length) break;
                var msg = new byte[length + 4];
                Buffer.BlockCopy(file, pos, msg, 0, length);
                pos += length;
                ParseMessage(msg, length);
            }
        }

        public InfoString ServerInfo
        {
            get { string s; return new InfoString(Configstrings.TryGetValue(0, out s) ? s : ""); }
        }
    }

    // ------------------------------------------------------------ the scan (fragdex/scanner.py)

    sealed class ScanItem
    {
        public string Path, Member, Name, Key;
        public long Size;
        public DateTime MtimeUtc;
    }

    sealed class ScanSummary
    {
        public int Found, New, Scanned, Players;
        public long BytesIn, BytesOut;
        public string Output;
        public bool Cancelled;
        public readonly List<string> Errors = new List<string>();
    }

    sealed class Scanner
    {
        public const string StateFile = "fragdex-scan-state.json";
        static readonly Regex DemoExt = new Regex(@"\.dm_[0-9]+$", RegexOptions.IgnoreCase);
        static readonly DateTime Epoch = new DateTime(1970, 1, 1, 0, 0, 0, DateTimeKind.Utc);

        // live progress, read by the UI thread
        public volatile int Total, Done, PlayerCount;
        public volatile string Phase = "";

        // ---- finding demos

        static IEnumerable<string> SteamRoots()
        {
            var roots = new List<string>();
            try
            {
                using (RegistryKey k = Registry.CurrentUser.OpenSubKey(@"Software\Valve\Steam"))
                    if (k != null && k.GetValue("SteamPath") is string) roots.Add((string)k.GetValue("SteamPath"));
            }
            catch (Exception) { }
            foreach (string env in new[] { "ProgramFiles(x86)", "ProgramFiles" })
            {
                string pf = Environment.GetEnvironmentVariable(env);
                if (!string.IsNullOrEmpty(pf)) roots.Add(System.IO.Path.Combine(pf, "Steam"));
            }
            var libraries = new List<string>();
            foreach (string root in roots)
            {
                if (!Directory.Exists(root)) continue;
                libraries.Add(root);
                try
                {
                    string vdf = File.ReadAllText(System.IO.Path.Combine(root, "steamapps", "libraryfolders.vdf"));
                    foreach (Match m in Regex.Matches(vdf, "\"path\"\\s+\"([^\"]+)\""))
                        libraries.Add(m.Groups[1].Value.Replace("\\\\", "\\"));
                }
                catch (Exception) { }
            }
            return libraries;
        }

        public static List<string> DefaultFolders()
        {
            var candidates = new List<string> { @"C:\demos",
                System.IO.Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), "demos") };
            foreach (string lib in SteamRoots())
            {
                string ql = System.IO.Path.Combine(lib, "steamapps", "common", "Quake Live");
                try
                {
                    foreach (string sub in Directory.GetDirectories(ql))
                        candidates.Add(System.IO.Path.Combine(sub, "baseq3", "demos"));
                }
                catch (Exception) { }
                candidates.Add(System.IO.Path.Combine(ql, "baseq3", "demos"));
            }
            var seen = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            var outList = new List<string>();
            foreach (string c in candidates)
            {
                string full;
                try { full = System.IO.Path.GetFullPath(c); } catch (Exception) { continue; }
                if (Directory.Exists(full) && seen.Add(full)) outList.Add(full);
            }
            return outList;
        }

        static void AddFile(string path, List<ScanItem> items)
        {
            string name = System.IO.Path.GetFileName(path);
            FileInfo fi;
            try { fi = new FileInfo(System.IO.Path.GetFullPath(path)); if (!fi.Exists) return; }
            catch (Exception) { return; }
            if (name.EndsWith(".zip", StringComparison.OrdinalIgnoreCase))
            {
                try
                {
                    using (ZipArchive z = ZipFile.OpenRead(fi.FullName))
                        foreach (ZipArchiveEntry e in z.Entries)
                        {
                            string full = e.FullName.Replace('\\', '/');
                            string baseName = full.Substring(full.LastIndexOf('/') + 1);
                            if (baseName.Length == 0 || !DemoExt.IsMatch(baseName)) continue;
                            items.Add(new ScanItem {
                                Path = fi.FullName, Member = e.FullName, Name = name + "/" + baseName, Size = e.Length,
                                MtimeUtc = e.LastWriteTime.UtcDateTime,
                                Key = fi.FullName + "|" + e.FullName + "|" + e.Length });
                        }
                }
                catch (Exception) { }
                return;
            }
            if (!DemoExt.IsMatch(name)) return;
            DateTime mtime = fi.LastWriteTimeUtc;
            items.Add(new ScanItem {
                Path = fi.FullName, Name = name, Size = fi.Length, MtimeUtc = mtime,
                Key = fi.FullName + "|" + fi.Length + "|" + (mtime.Ticks - Epoch.Ticks) / TimeSpan.TicksPerSecond });
        }

        [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode, Pack = 4)]
        struct FindData
        {
            public uint Attributes; public long Created, Accessed, Written; public uint SizeHigh, SizeLow, ReparseTag, Reserved1;
            [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 260)] public string Name;
            [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 14)] public string AltName;
        }
        [DllImport("kernel32.dll", CharSet = CharSet.Unicode)] static extern IntPtr FindFirstFileW(string path, out FindData data);
        [DllImport("kernel32.dll")] static extern bool FindClose(IntPtr h);

        // Junction or symbolic link? os.walk doesn't follow those (a junction back to a parent would
        // loop). Other reparse points, such as OneDrive placeholders, are ordinary folders.
        static bool IsLink(DirectoryInfo d)
        {
            if ((d.Attributes & FileAttributes.ReparsePoint) == 0) return false;
            string p = d.FullName;
            if (p.Length >= 248) p = p.StartsWith(@"\\") ? @"\\?\UNC\" + p.Substring(2) : @"\\?\" + p;
            FindData fd;
            IntPtr h = FindFirstFileW(p, out fd);
            if (h == new IntPtr(-1)) return true;   // can't tell: don't risk a loop
            FindClose(h);
            return fd.ReparseTag == 0xA0000003 || fd.ReparseTag == 0xA000000C;   // mount point / symlink
        }

        static void Walk(string dir, List<ScanItem> items)
        {
            string[] files; DirectoryInfo[] dirs;
            try { files = Directory.GetFiles(dir); dirs = new DirectoryInfo(dir).GetDirectories(); }
            catch (Exception) { return; }   // unreadable folder: skip, like os.walk
            Array.Sort(files, StringComparer.Ordinal);
            foreach (string f in files) AddFile(f, items);
            foreach (DirectoryInfo d in dirs) if (!IsLink(d)) Walk(d.FullName, items);
        }

        public static List<ScanItem> Collect(IEnumerable<string> folders)
        {
            var items = new List<ScanItem>();
            foreach (string f in folders)
            {
                if (File.Exists(f)) AddFile(f, items);
                else if (Directory.Exists(f)) Walk(f, items);
            }
            var keys = new HashSet<string>();   // overlapping folders list a file once
            return items.FindAll(i => keys.Add(i.Key));
        }

        // ---- scanning one demo

        sealed class Result
        {
            public string Sha, Map = "", Server = "", Error;
            public List<string[]> Rows = new List<string[]>();
        }

        static Result ScanItem(ScanItem item)
        {
            var res = new Result();
            try
            {
                byte[] data;
                if (item.Member != null)
                {
                    using (ZipArchive z = ZipFile.OpenRead(item.Path))
                    using (Stream s = z.GetEntry(item.Member).Open())
                    using (var ms = new MemoryStream())
                    {
                        s.CopyTo(ms);
                        data = ms.ToArray();
                    }
                }
                else data = File.ReadAllBytes(item.Path);
                using (SHA256 h = SHA256.Create())
                {
                    var sb = new StringBuilder(64);
                    foreach (byte b in h.ComputeHash(data)) sb.Append(b.ToString("x2"));
                    res.Sha = sb.ToString();
                }
                var p = new DemoParser();
                p.Parse(data);
                InfoString si = p.ServerInfo;
                string seenAt = null;
                long start;
                if (long.TryParse(si.Get("g_levelStartTime", "0").Trim(), NumberStyles.AllowLeadingSign,
                                  CultureInfo.InvariantCulture, out start) && start > 946684800 && start < 4102444800)
                    seenAt = Names.FormatUtc(Epoch.AddSeconds(start));
                if (seenAt == null) seenAt = Names.FormatUtc(item.MtimeUtc);
                res.Server = Names.Truncate(Names.Clean(si.Get("sv_hostname", "")), 80);
                res.Map = si.Get("mapname", "");
                var unique = new HashSet<string>();
                foreach (string[] pl in p.Players)
                {
                    string name = Names.Normalize(pl[1]);
                    if (unique.Add(pl[0] + "\0" + name)) res.Rows.Add(new[] { pl[0], name, "", seenAt });
                }
            }
            catch (Exception e) { res.Error = e.GetType().Name + ": " + e.Message; }
            return res;
        }

        // ---- state file: {"<key>": "<sha256>"}, shared with the Python fragdex-scan

        static readonly Regex StatePair = new Regex("\"((?:[^\"\\\\]|\\\\.)*)\"\\s*:\\s*\"([0-9a-f]{64})\"");

        static string JsonUnescape(string s)
        {
            if (s.IndexOf('\\') < 0) return s;
            var sb = new StringBuilder(s.Length);
            for (int i = 0; i < s.Length; i++)
            {
                char c = s[i];
                if (c != '\\' || i + 1 >= s.Length) { sb.Append(c); continue; }
                char n = s[++i];
                switch (n)
                {
                    case 'n': sb.Append('\n'); break;
                    case 'r': sb.Append('\r'); break;
                    case 't': sb.Append('\t'); break;
                    case 'b': sb.Append('\b'); break;
                    case 'f': sb.Append('\f'); break;
                    case 'u':
                        if (i + 4 < s.Length) { sb.Append((char)Convert.ToInt32(s.Substring(i + 1, 4), 16)); i += 4; }
                        break;
                    default: sb.Append(n); break;
                }
            }
            return sb.ToString();
        }

        static void JsonString(StringBuilder sb, string s, bool asciiOnly)
        {
            sb.Append('"');
            foreach (char c in s)
            {
                switch (c)
                {
                    case '"': sb.Append("\\\""); break;
                    case '\\': sb.Append("\\\\"); break;
                    case '\n': sb.Append("\\n"); break;
                    case '\r': sb.Append("\\r"); break;
                    case '\t': sb.Append("\\t"); break;
                    case '\b': sb.Append("\\b"); break;
                    case '\f': sb.Append("\\f"); break;
                    default:
                        if (c < 0x20 || (asciiOnly && c > 0x7e)) sb.Append("\\u").Append(((int)c).ToString("x4"));
                        else sb.Append(c);
                        break;
                }
            }
            sb.Append('"');
        }

        static Dictionary<string, string> LoadState(string path)
        {
            var state = new Dictionary<string, string>();
            try
            {
                foreach (Match m in StatePair.Matches(File.ReadAllText(path, Encoding.UTF8)))
                    state[JsonUnescape(m.Groups[1].Value)] = m.Groups[2].Value;
            }
            catch (Exception) { }
            return state;
        }

        static void SaveState(string path, Dictionary<string, string> state)
        {
            var sb = new StringBuilder("{");
            bool first = true;
            foreach (KeyValuePair<string, string> kv in state)
            {
                if (!first) sb.Append(", ");
                first = false;
                JsonString(sb, kv.Key, true);
                sb.Append(": ");
                JsonString(sb, kv.Value, true);
            }
            sb.Append('}');
            string tmp = path + ".tmp";
            File.WriteAllText(tmp, sb.ToString(), new UTF8Encoding(false));
            if (File.Exists(path)) File.Delete(path);
            File.Move(tmp, path);
        }

        // ---- the .fdx export (fragdex/importers/export.py)

        static string EntryJson(ScanItem item, Result r)
        {
            var sb = new StringBuilder(256 + r.Rows.Count * 64);
            sb.Append("{\"file\":"); JsonString(sb, item.Name, false);
            sb.Append(",\"kind\":\"demo\",\"sha256\":\"").Append(r.Sha).Append("\",\"size\":").Append(item.Size);
            sb.Append(",\"map\":"); JsonString(sb, r.Map, false);
            sb.Append(",\"server\":"); JsonString(sb, r.Server, false);
            sb.Append(",\"s\":[");
            for (int i = 0; i < r.Rows.Count; i++)
            {
                if (i > 0) sb.Append(',');
                sb.Append('[');
                for (int j = 0; j < 4; j++)
                {
                    if (j > 0) sb.Append(',');
                    JsonString(sb, r.Rows[i][j], false);
                }
                sb.Append(']');
            }
            return sb.Append("]}\n").ToString();
        }

        /// Scan folders into outputDir\fragdex-<stamp>.fdx (or output, when given).
        public ScanSummary Run(IList<string> folders, string outputDir, string output, bool includeAll,
                               string version, CancellationToken cancel)
        {
            var summary = new ScanSummary();
            output = output ?? System.IO.Path.Combine(outputDir,
                "fragdex-" + DateTime.Now.ToString("yyyyMMdd-HHmmss", CultureInfo.InvariantCulture) + ".fdx");
            output = System.IO.Path.GetFullPath(output);
            Directory.CreateDirectory(System.IO.Path.GetDirectoryName(output));
            string statePath = System.IO.Path.Combine(System.IO.Path.GetDirectoryName(output), StateFile);
            Dictionary<string, string> state = includeAll ? new Dictionary<string, string>() : LoadState(statePath);

            Phase = "Looking for demos…";
            List<ScanItem> items = Collect(folders);
            List<ScanItem> todo = items.FindAll(i => !state.ContainsKey(i.Key));
            summary.Found = items.Count;
            summary.New = todo.Count;
            Total = todo.Count;
            if (todo.Count == 0 || cancel.IsCancellationRequested)
            {
                summary.Cancelled = cancel.IsCancellationRequested;
                return summary;
            }

            Phase = "Scanning";
            string part = output + ".part";
            var players = new HashSet<string>();
            var gate = new object();
            try
            {
                using (var fs = new FileStream(part, FileMode.Create, FileAccess.Write))
                using (var gz = new GZipStream(fs, CompressionLevel.Optimal))
                using (var w = new StreamWriter(gz, new UTF8Encoding(false), 1 << 16))
                {
                    w.NewLine = "\n";
                    w.Write("{\"format\":\"fragdex-scan\",\"version\":1,\"created\":\"" + Names.FormatUtc(DateTime.UtcNow) +
                            "\",\"tool\":\"" + version + "-win\"}\n");
                    var opts = new ParallelOptions { MaxDegreeOfParallelism = Environment.ProcessorCount, CancellationToken = cancel };
                    Parallel.ForEach(todo, opts, item =>
                    {
                        Result r = ScanItem(item);
                        string json = r.Error == null ? EntryJson(item, r) : null;
                        lock (gate)
                        {
                            if (json == null) summary.Errors.Add(item.Name + ": " + r.Error);
                            else
                            {
                                w.Write(json);
                                foreach (string[] row in r.Rows) players.Add(row[0]);
                                state[item.Key] = r.Sha;
                                summary.BytesIn += item.Size;
                            }
                            summary.Scanned++;
                            PlayerCount = players.Count;
                            Done = summary.Scanned;
                        }
                    });
                }
            }
            catch (OperationCanceledException)
            {
                summary.Cancelled = true;
                try { File.Delete(part); } catch (Exception) { }
                return summary;
            }
            if (File.Exists(output)) File.Delete(output);
            File.Move(part, output);
            SaveState(statePath, state);
            summary.Players = players.Count;
            summary.Output = output;
            summary.BytesOut = new FileInfo(output).Length;
            return summary;
        }
    }
}
