// SPDX-License-Identifier: GPL-2.0-or-later
// fragdex-scan for Windows: index Quake Live demos into a small .fdx file to upload on
// Fragdex's Import page. A WinForms front end for Scanner.cs, styled like the web app.
// Built with the C# compiler that ships with .NET Framework 4.x: app/tools/build_scanner_exe.ps1
//
//   fragdex-scan.exe [FOLDER ...]                        open the window (folders pre-listed)
//   fragdex-scan.exe --out FILE.fdx [--all] FOLDER ...   scan without a window (automation)
using System;
using System.Collections.Generic;
using System.Drawing;
using System.Drawing.Drawing2D;
using System.Drawing.Imaging;
using System.Globalization;
using System.IO;
using System.Runtime.InteropServices;
using System.Text;
using System.Threading;
using System.Windows.Forms;

[assembly: System.Reflection.AssemblyTitle("Fragdex Scan")]
[assembly: System.Reflection.AssemblyProduct("Fragdex")]
[assembly: System.Reflection.AssemblyCopyright("GPL-2.0-or-later")]
// .NET 4.6.2+ path handling: no 260-character limit (with app.manifest's longPathAware)
[assembly: System.Runtime.Versioning.TargetFramework(".NETFramework,Version=v4.6.2")]

namespace Fragdex
{
    static class Program
    {
        [DllImport("user32.dll")] static extern bool SetProcessDPIAware();
        [DllImport("kernel32.dll")] static extern bool AttachConsole(int pid);

        [STAThread]
        static int Main(string[] args)
        {
            if (args.Length > 0 && args[0].StartsWith("--")) return Cli(args);
            try { SetProcessDPIAware(); } catch (Exception) { }
            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);
            Application.Run(new MainForm(args));
            return 0;
        }

        static int Cli(string[] args)
        {
            AttachConsole(-1);
            string output = null, icon = null;
            bool all = false;
            var folders = new List<string>();
            for (int i = 0; i < args.Length; i++)
            {
                if (args[i] == "--out" && i + 1 < args.Length) output = args[++i];
                else if (args[i] == "--write-icon" && i + 1 < args.Length) icon = args[++i];
                else if (args[i] == "--all") all = true;
                else if (args[i] == "--version") { Console.WriteLine("fragdex-scan " + Application.ProductVersion); return 0; }
                else if (!args[i].StartsWith("--")) folders.Add(args[i]);
                else { Console.WriteLine("usage: fragdex-scan.exe --out FILE.fdx [--all] FOLDER ..."); return 2; }
            }
            if (icon != null) { Mark.WriteIco(icon); return 0; }
            if (output == null || folders.Count == 0) { Console.WriteLine("usage: fragdex-scan.exe --out FILE.fdx [--all] FOLDER ..."); return 2; }
            ScanSummary s;
            try { s = new Scanner().Run(folders, null, output, all, Application.ProductVersion, CancellationToken.None); }
            catch (Exception e) { Console.WriteLine("error: " + e.Message); return 1; }
            Console.WriteLine("found " + s.Found + ", scanned " + s.Scanned + ", players " + s.Players + ", errors " + s.Errors.Count);
            foreach (string e in s.Errors) Console.WriteLine("  skipped " + e);
            return 0;
        }
    }

    // ------------------------------------------------------------ colours (app/fragdex/static/app.css, dark theme)

    static class Theme
    {
        public static readonly Color Bg = Hex(0x0b0f14), Panel = Hex(0x10171f), Panel2 = Hex(0x151e28),
            Border = Hex(0x212b37), Border2 = Hex(0x2c3846), Text = Hex(0xe6edf3), Muted = Hex(0x8794a5),
            Faint = Hex(0x5a6676), Accent = Hex(0x1ff2a2), AccentInk = Hex(0x04150e), AccentText = Hex(0x34e39a),
            Warn = Hex(0xf5b40a), Danger = Hex(0xf2495c);

        static Color Hex(int v) { return Color.FromArgb((v >> 16) & 255, (v >> 8) & 255, v & 255); }

        public static Color Mix(Color a, Color b, double t)
        {
            return Color.FromArgb((int)(a.R + (b.R - a.R) * t), (int)(a.G + (b.G - a.G) * t), (int)(a.B + (b.B - a.B) * t));
        }

        public static GraphicsPath Round(RectangleF r, float radius)
        {
            var p = new GraphicsPath();
            float d = Math.Min(radius * 2, Math.Min(r.Width, r.Height));
            p.AddArc(r.X, r.Y, d, d, 180, 90);
            p.AddArc(r.Right - d, r.Y, d, d, 270, 90);
            p.AddArc(r.Right - d, r.Bottom - d, d, d, 0, 90);
            p.AddArc(r.X, r.Bottom - d, d, d, 90, 90);
            p.CloseFigure();
            return p;
        }

        public static float Scale(Graphics g) { return g.DpiX / 96f; }
    }

    // ------------------------------------------------------------ the crosshair mark (favicon.svg)

    static class Mark
    {
        public static void Draw(Graphics g, RectangleF r, bool box)
        {
            g.SmoothingMode = SmoothingMode.AntiAlias;
            float u = r.Width / 32f;
            if (box)
                using (GraphicsPath p = Theme.Round(new RectangleF(r.X + .5f * u, r.Y + .5f * u, 31 * u, 31 * u), 7 * u))
                using (var fill = new SolidBrush(Theme.Mix(Theme.Panel, Theme.Accent, .07)))
                using (var edge = new Pen(Theme.Border2, Math.Max(1, u)))
                {
                    g.FillPath(fill, p);
                    g.DrawPath(edge, p);
                }
            using (var pen = new Pen(Theme.Accent, 2 * u) { StartCap = LineCap.Round, EndCap = LineCap.Round })
            using (var dot = new SolidBrush(Theme.Accent))
            {
                float cx = r.X + 16 * u, cy = r.Y + 16 * u;
                g.DrawEllipse(pen, cx - 8 * u, cy - 8 * u, 16 * u, 16 * u);
                g.DrawLine(pen, cx, r.Y + 4.5f * u, cx, r.Y + 10.5f * u);
                g.DrawLine(pen, cx, r.Y + 21.5f * u, cx, r.Y + 27.5f * u);
                g.DrawLine(pen, r.X + 4.5f * u, cy, r.X + 10.5f * u, cy);
                g.DrawLine(pen, r.X + 21.5f * u, cy, r.X + 27.5f * u, cy);
                g.FillEllipse(dot, cx - 1.6f * u, cy - 1.6f * u, 3.2f * u, 3.2f * u);
            }
        }

        // .ico with PNG frames; the build embeds it as the exe's icon
        public static void WriteIco(string path)
        {
            int[] sizes = { 16, 24, 32, 48, 256 };
            var pngs = new List<byte[]>();
            foreach (int s in sizes)
                using (var bmp = new Bitmap(s, s, PixelFormat.Format32bppArgb))
                using (var ms = new MemoryStream())
                {
                    using (Graphics g = Graphics.FromImage(bmp)) Draw(g, new RectangleF(0, 0, s, s), true);
                    bmp.Save(ms, ImageFormat.Png);
                    pngs.Add(ms.ToArray());
                }
            using (var w = new BinaryWriter(File.Create(path)))
            {
                w.Write((short)0); w.Write((short)1); w.Write((short)sizes.Length);
                int offset = 6 + 16 * sizes.Length;
                for (int i = 0; i < sizes.Length; i++)
                {
                    w.Write((byte)(sizes[i] % 256)); w.Write((byte)(sizes[i] % 256));
                    w.Write((byte)0); w.Write((byte)0); w.Write((short)1); w.Write((short)32);
                    w.Write(pngs[i].Length); w.Write(offset);
                    offset += pngs[i].Length;
                }
                foreach (byte[] p in pngs) w.Write(p);
            }
        }
    }

    // ------------------------------------------------------------ controls

    sealed class FxButton : Control
    {
        public bool Primary, Danger;
        bool hover, down;

        public FxButton(string text, bool primary)
        {
            Text = text;
            Primary = primary;
            SetStyle(ControlStyles.UserPaint | ControlStyles.AllPaintingInWmPaint | ControlStyles.OptimizedDoubleBuffer |
                     ControlStyles.ResizeRedraw | ControlStyles.Selectable | ControlStyles.StandardClick, true);
            Font = new Font("Segoe UI Semibold", 10f);
            Cursor = Cursors.Hand;
            Size = new Size(120, 38);
            Margin = new Padding(8, 0, 0, 0);
        }

        protected override void OnMouseEnter(EventArgs e) { hover = true; Invalidate(); base.OnMouseEnter(e); }
        protected override void OnMouseLeave(EventArgs e) { hover = down = false; Invalidate(); base.OnMouseLeave(e); }
        protected override void OnMouseDown(MouseEventArgs e) { down = true; Invalidate(); base.OnMouseDown(e); }
        protected override void OnMouseUp(MouseEventArgs e) { down = false; Invalidate(); base.OnMouseUp(e); }
        protected override void OnEnabledChanged(EventArgs e) { Cursor = Enabled ? Cursors.Hand : Cursors.Default; Invalidate(); base.OnEnabledChanged(e); }
        protected override void OnGotFocus(EventArgs e) { Invalidate(); base.OnGotFocus(e); }
        protected override void OnLostFocus(EventArgs e) { Invalidate(); base.OnLostFocus(e); }
        protected override void OnTextChanged(EventArgs e) { Invalidate(); base.OnTextChanged(e); }

        protected override void OnKeyUp(KeyEventArgs e)
        {
            if (e.KeyCode == Keys.Space || e.KeyCode == Keys.Enter) PerformClick();
            base.OnKeyUp(e);
        }

        public void PerformClick() { if (Enabled && Visible) OnClick(EventArgs.Empty); }

        protected override AccessibleObject CreateAccessibilityInstance() { return new Accessible(this); }

        sealed class Accessible : ControlAccessibleObject
        {
            public Accessible(FxButton b) : base(b) { }
            public override AccessibleRole Role { get { return AccessibleRole.PushButton; } }
            public override string Name { get { return Owner.Text; } set { } }
            public override string DefaultAction { get { return "Press"; } }
            public override void DoDefaultAction() { ((FxButton)Owner).PerformClick(); }
        }

        protected override void OnPaint(PaintEventArgs e)
        {
            Graphics g = e.Graphics;
            float s = Theme.Scale(g);
            g.Clear(Parent.BackColor);
            g.SmoothingMode = SmoothingMode.AntiAlias;
            Color fill, edge, ink;
            if (Danger) { fill = Theme.Mix(Theme.Bg, Theme.Danger, hover ? .18 : .1); edge = Theme.Mix(Theme.Border, Theme.Danger, .45); ink = Theme.Danger; }
            else if (Primary) { fill = hover ? Theme.Mix(Theme.Accent, Color.White, .14) : Theme.Accent; edge = Theme.Accent; ink = Theme.AccentInk; }
            else { fill = hover ? Theme.Panel2 : Parent.BackColor; edge = hover ? Theme.Faint : Theme.Border2; ink = Theme.Text; }
            if (down) fill = Theme.Mix(fill, Color.Black, .12);
            if (!Enabled) { fill = Theme.Mix(Parent.BackColor, fill, .5); edge = Theme.Mix(Parent.BackColor, edge, .5); ink = Theme.Mix(Parent.BackColor, ink, .5); }
            var r = new RectangleF(.5f, .5f, Width - 1.5f, Height - 1.5f);
            using (GraphicsPath p = Theme.Round(r, 6 * s))
            using (var b = new SolidBrush(fill))
            using (var pen = new Pen(edge))
            {
                g.FillPath(b, p);
                g.DrawPath(pen, p);
            }
            if (Focused && ShowFocusCues)
                using (GraphicsPath p = Theme.Round(new RectangleF(2, 2, Width - 5, Height - 5), 5 * s))
                using (var pen = new Pen(Primary ? Theme.AccentInk : Theme.Accent, 1.5f * s))
                    g.DrawPath(pen, p);
            TextRenderer.DrawText(g, Text, Font, ClientRectangle, ink,
                TextFormatFlags.HorizontalCenter | TextFormatFlags.VerticalCenter | TextFormatFlags.NoPrefix | TextFormatFlags.SingleLine);
        }
    }

    sealed class FxCheck : CheckBox
    {
        public FxCheck(string text)
        {
            Text = text;
            SetStyle(ControlStyles.UserPaint | ControlStyles.AllPaintingInWmPaint | ControlStyles.OptimizedDoubleBuffer, true);
            Cursor = Cursors.Hand;
            Height = 28;
        }

        protected override void OnCheckedChanged(EventArgs e) { Invalidate(); base.OnCheckedChanged(e); }

        protected override void OnPaint(PaintEventArgs e)
        {
            Graphics g = e.Graphics;
            float s = Theme.Scale(g);
            g.Clear(Parent.BackColor);
            g.SmoothingMode = SmoothingMode.AntiAlias;
            float box = 16 * s, y = (Height - box) / 2;
            using (GraphicsPath p = Theme.Round(new RectangleF(.5f, y, box, box), 4 * s))
            using (var fill = new SolidBrush(Checked ? Theme.Accent : Theme.Panel))
            using (var pen = new Pen(Checked ? Theme.Accent : (Focused ? Theme.Accent : Theme.Border2)))
            {
                g.FillPath(fill, p);
                g.DrawPath(pen, p);
            }
            if (Checked)
                using (var pen = new Pen(Theme.AccentInk, 2 * s) { StartCap = LineCap.Round, EndCap = LineCap.Round, LineJoin = LineJoin.Round })
                    g.DrawLines(pen, new[] { new PointF(4 * s, y + 8.5f * s), new PointF(7 * s, y + 11.5f * s), new PointF(12.5f * s, y + 5 * s) });
            var tr = new Rectangle((int)(box + 9 * s), 0, Width - (int)(box + 9 * s), Height);
            TextRenderer.DrawText(g, Text, Font, tr, Checked ? Theme.Text : Theme.Muted,
                TextFormatFlags.VerticalCenter | TextFormatFlags.Left | TextFormatFlags.NoPrefix | TextFormatFlags.EndEllipsis);
        }
    }

    sealed class FxBar : Control
    {
        double value;
        bool busy;
        int phase;
        public double Value { get { return value; } set { this.value = Math.Max(0, Math.Min(1, value)); Invalidate(); } }
        public bool Busy { get { return busy; } set { busy = value; Invalidate(); } }
        public Color Fill = Theme.Accent;

        public FxBar()
        {
            SetStyle(ControlStyles.UserPaint | ControlStyles.AllPaintingInWmPaint | ControlStyles.OptimizedDoubleBuffer |
                     ControlStyles.ResizeRedraw, true);
            Height = 10;
        }

        public void Tick() { if (busy) { phase = (phase + 15) % 1000; Invalidate(); } }

        protected override void OnPaint(PaintEventArgs e)
        {
            Graphics g = e.Graphics;
            float s = Theme.Scale(g);
            g.Clear(Parent.BackColor);
            g.SmoothingMode = SmoothingMode.AntiAlias;
            var track = new RectangleF(.5f, .5f, Width - 1.5f, Height - 1.5f);
            using (GraphicsPath p = Theme.Round(track, 4 * s))
            using (var b = new SolidBrush(Theme.Panel2))
            using (var pen = new Pen(Theme.Border))
            {
                g.FillPath(b, p);
                g.DrawPath(pen, p);
                g.SetClip(p);
                using (var f = new SolidBrush(Fill))
                {
                    if (busy)
                    {
                        float seg = track.Width * .28f, x = (float)(phase / 1000.0 * (track.Width + seg) - seg);
                        g.FillRectangle(f, x, 0, seg, Height);
                    }
                    else if (value > 0)
                        g.FillRectangle(f, 0, 0, (float)(track.Width * value) + 1, Height);
                }
                g.ResetClip();
            }
        }
    }

    sealed class Header : Control
    {
        public Header()
        {
            SetStyle(ControlStyles.UserPaint | ControlStyles.AllPaintingInWmPaint | ControlStyles.OptimizedDoubleBuffer |
                     ControlStyles.ResizeRedraw, true);
            Height = 52;
        }

        protected override void OnPaint(PaintEventArgs e)
        {
            Graphics g = e.Graphics;
            float s = Theme.Scale(g);
            g.Clear(Parent.BackColor);
            float mark = 40 * s;
            Mark.Draw(g, new RectangleF(0, (Height - mark) / 2, mark, mark), true);
            int x = (int)(mark + 14 * s);
            using (var title = new Font("Segoe UI", 15f, FontStyle.Bold))
            using (var sub = new Font("Segoe UI", 8.5f))
            {
                var flags = TextFormatFlags.NoPrefix | TextFormatFlags.NoPadding | TextFormatFlags.SingleLine;
                Size t = TextRenderer.MeasureText(g, "FRAGDEX", title, Size.Empty, flags);
                int top = (int)(Height / 2f - t.Height + 3 * s);
                TextRenderer.DrawText(g, "FRAGDEX", title, new Point(x, top), Theme.Text, flags);
                TextRenderer.DrawText(g, "SCAN", title, new Point(x + t.Width + (int)(6 * s), top), Theme.AccentText, flags);
                TextRenderer.DrawText(g, "Index Quake Live demos into a small .fdx file for Fragdex's Import page", sub,
                    new Rectangle(x, (int)(Height / 2f + 5 * s), Width - x, (int)(20 * s)), Theme.Muted, flags | TextFormatFlags.EndEllipsis);
                Size v = TextRenderer.MeasureText(g, "v" + Application.ProductVersion, sub, Size.Empty, flags);
                TextRenderer.DrawText(g, "v" + Application.ProductVersion, sub, new Point(Width - v.Width, top + (int)(4 * s)), Theme.Faint, flags);
            }
        }
    }

    // ------------------------------------------------------------ folder picker (Vista+ dialog, multi-select)

    static class FolderPicker
    {
        [ComImport, Guid("DC1C5A9C-E88A-4dde-A5A1-60F82A20AEF7")] class FileOpenDialogCo { }

        [ComImport, Guid("d57c7288-d4ad-4768-be02-9d969532d960"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
        interface IFileOpenDialog
        {
            [PreserveSig] int Show(IntPtr parent);
            void SetFileTypes(uint count, IntPtr specs);
            void SetFileTypeIndex(uint index);
            void GetFileTypeIndex(out uint index);
            void Advise(IntPtr sink, out uint cookie);
            void Unadvise(uint cookie);
            void SetOptions(uint options);
            void GetOptions(out uint options);
            void SetDefaultFolder(IShellItem item);
            void SetFolder(IShellItem item);
            void GetFolder(out IShellItem item);
            void GetCurrentSelection(out IShellItem item);
            void SetFileName([MarshalAs(UnmanagedType.LPWStr)] string name);
            void GetFileName([MarshalAs(UnmanagedType.LPWStr)] out string name);
            void SetTitle([MarshalAs(UnmanagedType.LPWStr)] string title);
            void SetOkButtonLabel([MarshalAs(UnmanagedType.LPWStr)] string text);
            void SetFileNameLabel([MarshalAs(UnmanagedType.LPWStr)] string label);
            void GetResult(out IShellItem item);
            void AddPlace(IShellItem item, int place);
            void SetDefaultExtension([MarshalAs(UnmanagedType.LPWStr)] string ext);
            void Close(int hr);
            void SetClientGuid(ref Guid guid);
            void ClearClientData();
            void SetFilter(IntPtr filter);
            void GetResults(out IShellItemArray items);
            void GetSelectedItems(out IShellItemArray items);
        }

        [ComImport, Guid("43826d1e-e718-42ee-bc55-a1e261c37bfe"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
        interface IShellItem
        {
            void BindToHandler(IntPtr pbc, ref Guid bhid, ref Guid riid, out IntPtr ppv);
            void GetParent(out IShellItem parent);
            void GetDisplayName(uint sigdn, [MarshalAs(UnmanagedType.LPWStr)] out string name);
            void GetAttributes(uint mask, out uint attribs);
            void Compare(IShellItem other, uint hint, out int order);
        }

        [ComImport, Guid("b63ea76d-1f85-456f-a19c-48159efa858b"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
        interface IShellItemArray
        {
            void BindToHandler(IntPtr pbc, ref Guid bhid, ref Guid riid, out IntPtr ppv);
            void GetPropertyStore(int flags, ref Guid riid, out IntPtr ppv);
            void GetPropertyDescriptionList(IntPtr keyType, ref Guid riid, out IntPtr ppv);
            void GetAttributes(int flags, uint mask, out uint attribs);
            void GetCount(out uint count);
            void GetItemAt(uint index, out IShellItem item);
            void EnumItems(out IntPtr penum);
        }

        [DllImport("shell32.dll", CharSet = CharSet.Unicode)]
        static extern int SHCreateItemFromParsingName(string path, IntPtr pbc, [MarshalAs(UnmanagedType.LPStruct)] Guid riid,
                                                      out IShellItem item);

        const uint PickFolders = 0x20, ForceFileSystem = 0x40, AllowMultiSelect = 0x200, PathMustExist = 0x800;
        const uint SigdnFileSysPath = 0x80058000;

        public static string[] Pick(IWin32Window owner, string title, bool multi, string initial)
        {
            IFileOpenDialog dlg = null;
            try
            {
                dlg = (IFileOpenDialog)new FileOpenDialogCo();
                uint opts;
                dlg.GetOptions(out opts);
                dlg.SetOptions(opts | PickFolders | ForceFileSystem | PathMustExist | (multi ? AllowMultiSelect : 0));
                dlg.SetTitle(title);
                IShellItem start;
                if (!string.IsNullOrEmpty(initial) && Directory.Exists(initial) &&
                    SHCreateItemFromParsingName(initial, IntPtr.Zero, typeof(IShellItem).GUID, out start) == 0)
                    dlg.SetFolder(start);
                if (dlg.Show(owner.Handle) != 0) return null;   // cancelled
                IShellItemArray items;
                dlg.GetResults(out items);
                uint n;
                items.GetCount(out n);
                var paths = new List<string>();
                for (uint i = 0; i < n; i++)
                {
                    IShellItem it;
                    string p;
                    items.GetItemAt(i, out it);
                    it.GetDisplayName(SigdnFileSysPath, out p);
                    paths.Add(p);
                }
                return paths.ToArray();
            }
            catch (Exception)
            {
                using (var fb = new FolderBrowserDialog { Description = title, SelectedPath = initial ?? "" })
                    return fb.ShowDialog(owner) == DialogResult.OK ? new[] { fb.SelectedPath } : null;
            }
            finally
            {
                if (dlg != null) Marshal.ReleaseComObject(dlg);
            }
        }
    }

    // ------------------------------------------------------------ the window

    sealed class MainForm : Form
    {
        [DllImport("dwmapi.dll")] static extern int DwmSetWindowAttribute(IntPtr hwnd, int attr, ref int value, int size);

        static readonly string SettingsPath = Path.Combine(
            Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData), "Fragdex", "fragdex-scan.txt");

        readonly ListBox list = new ListBox();
        readonly Label hint = new Label();
        readonly Label foldersLabel = new Label();
        readonly TextBox outBox = new TextBox();
        readonly FxButton addBtn = new FxButton("Add folders…", false);
        readonly FxButton removeBtn = new FxButton("Remove", false);
        readonly FxButton browseBtn = new FxButton("Browse…", false);
        readonly FxButton processBtn = new FxButton("PROCESS", true);
        readonly FxCheck allBox = new FxCheck("Re-export demos that were exported before");
        readonly FxBar bar = new FxBar();
        readonly Label status = new Label();
        readonly LinkLabel openLink = new LinkLabel();
        readonly ToolTip tips = new ToolTip();
        readonly System.Windows.Forms.Timer timer = new System.Windows.Forms.Timer { Interval = 120 };

        Scanner scanner;
        CancellationTokenSource cancel;
        string lastOutput;
        bool closeWhenDone;
        DateTime started;

        public MainForm(string[] argFolders)
        {
            SuspendLayout();
            AutoScaleDimensions = new SizeF(96f, 96f);
            AutoScaleMode = AutoScaleMode.Dpi;
            Text = "Fragdex Scan";
            Font = new Font("Segoe UI", 9.75f);
            BackColor = Theme.Bg;
            ForeColor = Theme.Text;
            ClientSize = new Size(760, 520);
            MinimumSize = new Size(600, 440);
            StartPosition = FormStartPosition.CenterScreen;
            try { Icon = Icon.ExtractAssociatedIcon(Application.ExecutablePath); } catch (Exception) { }

            var root = new TableLayoutPanel { Dock = DockStyle.Fill, ColumnCount = 1, Padding = new Padding(22, 18, 22, 18) };
            root.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100));
            foreach (SizeType t in new[] { SizeType.AutoSize, SizeType.AutoSize, SizeType.Percent, SizeType.AutoSize,
                                           SizeType.AutoSize, SizeType.AutoSize, SizeType.AutoSize, SizeType.AutoSize })
                root.RowStyles.Add(new RowStyle(t, 100));

            var header = new Header { Dock = DockStyle.Fill, Margin = new Padding(0, 0, 0, 10) };
            root.Controls.Add(header, 0, 0);

            Section(foldersLabel, "DEMO FOLDERS");
            root.Controls.Add(foldersLabel, 0, 1);

            // folder list + its buttons
            list.BorderStyle = BorderStyle.None;
            list.BackColor = Theme.Panel;
            list.ForeColor = Theme.Text;
            list.Font = new Font("Consolas", 10f);
            list.DrawMode = DrawMode.OwnerDrawFixed;
            list.ItemHeight = 28;
            list.IntegralHeight = false;
            list.SelectionMode = SelectionMode.MultiExtended;
            list.AllowDrop = true;
            list.DrawItem += DrawFolder;
            list.SelectedIndexChanged += delegate { UpdateButtons(); };
            list.KeyDown += (s, e) => { if (e.KeyCode == Keys.Delete) RemoveSelected(); };
            list.DragEnter += OnDragEnter;
            list.DragDrop += OnDragDrop;
            hint.Text = "No folders yet. Add the folders that hold your demos (.dm_91, also inside .zip files), or drop them here.";
            hint.ForeColor = Theme.Faint;
            hint.BackColor = Theme.Panel;
            hint.TextAlign = ContentAlignment.MiddleCenter;
            hint.Dock = DockStyle.Fill;
            hint.Padding = new Padding(30, 0, 30, 0);
            hint.AllowDrop = true;
            hint.Cursor = Cursors.Hand;
            hint.Click += delegate { AddFolders(); };
            hint.DragEnter += OnDragEnter;
            hint.DragDrop += OnDragDrop;
            var listFrame = Frame(new Padding(4));
            ((Panel)listFrame.Controls[0]).Controls.Add(list);
            ((Panel)listFrame.Controls[0]).Controls.Add(hint);
            list.Dock = DockStyle.Fill;
            hint.BringToFront();

            var side = new FlowLayoutPanel { FlowDirection = FlowDirection.TopDown, AutoSize = true, WrapContents = false,
                                             Margin = new Padding(0), Padding = new Padding(0) };
            addBtn.Margin = new Padding(10, 0, 0, 8);
            removeBtn.Margin = new Padding(10, 0, 0, 8);
            addBtn.Width = removeBtn.Width = 136;
            addBtn.Click += delegate { AddFolders(); };
            removeBtn.Click += delegate { RemoveSelected(); };
            side.Controls.Add(addBtn);
            side.Controls.Add(removeBtn);
            var folderRow = new TableLayoutPanel { Dock = DockStyle.Fill, ColumnCount = 2, RowCount = 1, Margin = new Padding(0) };
            folderRow.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100));
            folderRow.ColumnStyles.Add(new ColumnStyle(SizeType.AutoSize));
            folderRow.RowStyles.Add(new RowStyle(SizeType.Percent, 100));
            folderRow.Controls.Add(listFrame, 0, 0);
            folderRow.Controls.Add(side, 1, 0);
            root.Controls.Add(folderRow, 0, 2);

            allBox.Dock = DockStyle.Fill;
            allBox.Margin = new Padding(0, 10, 0, 6);
            root.Controls.Add(allBox, 0, 3);

            bar.Dock = DockStyle.Fill;
            bar.Margin = new Padding(0, 4, 0, 8);
            root.Controls.Add(bar, 0, 4);

            status.Dock = DockStyle.Fill;
            status.AutoEllipsis = true;
            status.ForeColor = Theme.Muted;
            status.Font = new Font("Consolas", 9.5f);
            status.Height = 22;
            status.Margin = new Padding(0);
            status.TextAlign = ContentAlignment.MiddleLeft;
            openLink.Text = "Open folder";
            openLink.AutoSize = true;
            openLink.LinkColor = openLink.ActiveLinkColor = Theme.AccentText;
            openLink.VisitedLinkColor = Theme.AccentText;
            openLink.LinkBehavior = LinkBehavior.HoverUnderline;
            openLink.Margin = new Padding(12, 3, 0, 0);
            openLink.Visible = false;
            openLink.LinkClicked += delegate { OpenOutput(); };
            var statusRow = new TableLayoutPanel { Dock = DockStyle.Fill, ColumnCount = 2, AutoSize = true, Margin = new Padding(0) };
            statusRow.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100));
            statusRow.ColumnStyles.Add(new ColumnStyle(SizeType.AutoSize));
            statusRow.Controls.Add(status, 0, 0);
            statusRow.Controls.Add(openLink, 1, 0);
            root.Controls.Add(statusRow, 0, 5);

            var line = new Panel { Dock = DockStyle.Fill, Height = 1, BackColor = Theme.Border, Margin = new Padding(0, 14, 0, 14) };
            root.Controls.Add(line, 0, 6);

            // export folder next to Process
            var exportLabel = new Label();
            Section(exportLabel, "EXPORT TO");
            exportLabel.Anchor = AnchorStyles.Left;
            exportLabel.Margin = new Padding(0, 0, 12, 0);
            outBox.BorderStyle = BorderStyle.None;
            outBox.BackColor = Theme.Panel;
            outBox.ForeColor = Theme.Text;
            outBox.Font = new Font("Consolas", 10f);
            var outFrame = Frame(new Padding(10, 10, 10, 6));
            outFrame.Height = 38;
            outFrame.Dock = DockStyle.Fill;
            ((Panel)outFrame.Controls[0]).Controls.Add(outBox);
            outBox.Dock = DockStyle.Fill;
            browseBtn.Width = 104;
            browseBtn.Click += delegate { BrowseOutput(); };
            processBtn.Width = 140;
            processBtn.Margin = new Padding(10, 0, 0, 0);
            processBtn.Click += delegate { Process(); };
            var bottom = new TableLayoutPanel { Dock = DockStyle.Fill, ColumnCount = 4, AutoSize = true, Margin = new Padding(0) };
            bottom.ColumnStyles.Add(new ColumnStyle(SizeType.AutoSize));
            bottom.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100));
            bottom.ColumnStyles.Add(new ColumnStyle(SizeType.AutoSize));
            bottom.ColumnStyles.Add(new ColumnStyle(SizeType.AutoSize));
            bottom.Controls.Add(exportLabel, 0, 0);
            bottom.Controls.Add(outFrame, 1, 0);
            bottom.Controls.Add(browseBtn, 2, 0);
            bottom.Controls.Add(processBtn, 3, 0);
            root.Controls.Add(bottom, 0, 7);

            Controls.Add(root);
            AcceptButton = null;
            timer.Tick += delegate { Progress(); };
            tips.SetToolTip(processBtn, "Scan the folders and save a .fdx file in the export folder");
            tips.SetToolTip(allBox, "Normally demos exported before (remembered in fragdex-scan-state.json in the export folder) are skipped");
            ResumeLayout(false);
            PerformLayout();

            LoadSettings(argFolders);
            UpdateButtons();
            SetStatus("Add your demo folders, choose where to save, then press Process.", Theme.Muted);
        }

        static void Section(Label l, string text)
        {
            l.Text = text;
            l.AutoSize = true;
            l.ForeColor = Theme.Muted;
            l.Font = new Font("Segoe UI Semibold", 8.25f);
            l.Margin = new Padding(0, 6, 0, 8);
        }

        // 1px Border2 frame around a Panel-coloured inner panel
        static Panel Frame(Padding inner)
        {
            var outer = new Panel { BackColor = Theme.Border2, Padding = new Padding(1), Dock = DockStyle.Fill, Margin = new Padding(0) };
            var mid = new Panel { BackColor = Theme.Panel, Padding = inner, Dock = DockStyle.Fill };
            outer.Controls.Add(mid);
            return outer;
        }

        protected override void OnHandleCreated(EventArgs e)
        {
            base.OnHandleCreated(e);
            // dark title bar (Windows 10 1809+), in the app's own colours on Windows 11
            int on = 1, caption = ColorTranslator.ToWin32(Theme.Bg), text = ColorTranslator.ToWin32(Theme.Text);
            try
            {
                if (DwmSetWindowAttribute(Handle, 20, ref on, 4) != 0) DwmSetWindowAttribute(Handle, 19, ref on, 4);
                DwmSetWindowAttribute(Handle, 35, ref caption, 4);
                DwmSetWindowAttribute(Handle, 36, ref text, 4);
            }
            catch (Exception) { }
        }

        // ---- folder list

        void DrawFolder(object sender, DrawItemEventArgs e)
        {
            if (e.Index < 0) return;
            Graphics g = e.Graphics;
            string path = (string)list.Items[e.Index];
            bool sel = (e.State & DrawItemState.Selected) != 0;
            bool exists = Directory.Exists(path) || File.Exists(path);
            using (var b = new SolidBrush(sel ? Theme.Mix(Theme.Panel, Theme.Accent, .1) : Theme.Panel))
                g.FillRectangle(b, e.Bounds);
            if (sel)
                using (var b = new SolidBrush(Theme.Accent))
                    g.FillRectangle(b, e.Bounds.X, e.Bounds.Y + 4, 3, e.Bounds.Height - 8);
            var flags = TextFormatFlags.VerticalCenter | TextFormatFlags.Left | TextFormatFlags.NoPrefix |
                        TextFormatFlags.PathEllipsis | TextFormatFlags.SingleLine;
            int right = 0;
            if (!exists)
            {
                Size n = TextRenderer.MeasureText(g, "not found", Font);
                right = n.Width + 10;
                TextRenderer.DrawText(g, "not found", Font, new Rectangle(e.Bounds.Right - right, e.Bounds.Y, right, e.Bounds.Height),
                                      Theme.Danger, TextFormatFlags.VerticalCenter | TextFormatFlags.Left);
            }
            TextRenderer.DrawText(g, path, list.Font, new Rectangle(e.Bounds.X + 12, e.Bounds.Y, e.Bounds.Width - 20 - right, e.Bounds.Height),
                                  sel ? Theme.Text : (exists ? Theme.Text : Theme.Muted), flags);
        }

        void AddPaths(IEnumerable<string> paths)
        {
            foreach (string p in paths)
            {
                string full;
                try { full = Path.GetFullPath(p).TrimEnd('\\'); } catch (Exception) { continue; }
                if (full.EndsWith(":")) full += "\\";
                bool dup = false;
                foreach (string have in list.Items) dup |= string.Equals(have, full, StringComparison.OrdinalIgnoreCase);
                if (!dup) list.Items.Add(full);
            }
            UpdateButtons();
        }

        void AddFolders()
        {
            string start = list.SelectedItem as string ?? (list.Items.Count > 0 ? (string)list.Items[list.Items.Count - 1] : null);
            string[] picked = FolderPicker.Pick(this, "Add demo folders", true, start);
            if (picked != null) AddPaths(picked);
        }

        void RemoveSelected()
        {
            if (scanner != null) return;
            var gone = new List<object>();
            foreach (object o in list.SelectedItems) gone.Add(o);
            foreach (object o in gone) list.Items.Remove(o);
            UpdateButtons();
        }

        void OnDragEnter(object sender, DragEventArgs e)
        {
            e.Effect = scanner == null && e.Data.GetDataPresent(DataFormats.FileDrop) ? DragDropEffects.Copy : DragDropEffects.None;
        }

        void OnDragDrop(object sender, DragEventArgs e)
        {
            var paths = e.Data.GetData(DataFormats.FileDrop) as string[];
            if (paths == null) return;
            AddPaths(Array.FindAll(paths, p => Directory.Exists(p) || p.EndsWith(".zip", StringComparison.OrdinalIgnoreCase) ||
                                               System.Text.RegularExpressions.Regex.IsMatch(p, @"\.dm_[0-9]+$",
                                                   System.Text.RegularExpressions.RegexOptions.IgnoreCase)));
        }

        void BrowseOutput()
        {
            string[] picked = FolderPicker.Pick(this, "Save .fdx exports in", false, outBox.Text.Trim());
            if (picked != null && picked.Length > 0) outBox.Text = picked[0];
        }

        void UpdateButtons()
        {
            bool idle = scanner == null;
            hint.Visible = list.Items.Count == 0;
            foldersLabel.Text = list.Items.Count > 0 ? "DEMO FOLDERS  ·  " + list.Items.Count : "DEMO FOLDERS";
            addBtn.Enabled = browseBtn.Enabled = allBox.Enabled = idle;
            outBox.ReadOnly = !idle;
            list.AllowDrop = idle;
            removeBtn.Enabled = idle && list.SelectedItems.Count > 0;
            processBtn.Enabled = !idle || list.Items.Count > 0;
        }

        // ---- settings: %APPDATA%\Fragdex\fragdex-scan.txt

        void LoadSettings(string[] argFolders)
        {
            string output = null;
            var folders = new List<string>();
            bool saved = false;
            try
            {
                foreach (string line in File.ReadAllLines(SettingsPath, Encoding.UTF8))
                {
                    saved = true;
                    if (line.StartsWith("out=")) output = line.Substring(4);
                    else if (line.StartsWith("folder=")) folders.Add(line.Substring(7));
                }
            }
            catch (Exception) { }
            if (!saved) folders = Scanner.DefaultFolders();
            AddPaths(folders);
            AddPaths(argFolders);
            outBox.Text = string.IsNullOrEmpty(output)
                ? Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.MyDocuments), "Fragdex")
                : output;
        }

        void SaveSettings()
        {
            try
            {
                var sb = new StringBuilder();
                sb.Append("out=").Append(outBox.Text.Trim()).Append("\r\n");
                foreach (string f in list.Items) sb.Append("folder=").Append(f).Append("\r\n");
                Directory.CreateDirectory(Path.GetDirectoryName(SettingsPath));
                File.WriteAllText(SettingsPath, sb.ToString(), new UTF8Encoding(false));
            }
            catch (Exception) { }
        }

        protected override void OnFormClosing(FormClosingEventArgs e)
        {
            if (scanner != null)
            {
                closeWhenDone = true;   // stop cleanly so no half-written file is left behind
                cancel.Cancel();
                e.Cancel = true;
                return;
            }
            SaveSettings();
            base.OnFormClosing(e);
        }

        // ---- processing

        void SetStatus(string text, Color color)
        {
            status.Text = text;
            status.ForeColor = color;
        }

        void Process()
        {
            if (scanner != null)
            {
                cancel.Cancel();
                processBtn.Enabled = false;
                processBtn.Text = "STOPPING…";
                return;
            }
            string outDir = outBox.Text.Trim();
            if (outDir.Length == 0)
            {
                SetStatus("Choose a folder to save the export in.", Theme.Warn);
                return;
            }
            try { outDir = Path.GetFullPath(outDir); Directory.CreateDirectory(outDir); }
            catch (Exception ex)
            {
                SetStatus("Can't use that export folder: " + ex.Message, Theme.Danger);
                return;
            }
            outBox.Text = outDir;
            SaveSettings();
            var folders = new List<string>();
            foreach (string f in list.Items) folders.Add(f);
            bool all = allBox.Checked;

            scanner = new Scanner();
            cancel = new CancellationTokenSource();
            started = DateTime.UtcNow;
            lastOutput = null;
            openLink.Visible = false;
            tips.SetToolTip(status, null);
            bar.Fill = Theme.Accent;
            bar.Value = 0;
            bar.Busy = true;
            processBtn.Text = "CANCEL";
            processBtn.Primary = false;
            processBtn.Danger = true;
            UpdateButtons();
            SetStatus("Looking for demos…", Theme.Muted);
            timer.Start();

            Scanner sc = scanner;
            CancellationToken token = cancel.Token;
            var worker = new Thread(() =>
            {
                ScanSummary summary = null;
                Exception error = null;
                try { summary = sc.Run(folders, outDir, null, all, Application.ProductVersion, token); }
                catch (Exception ex) { error = ex is AggregateException ? ((AggregateException)ex).Flatten().InnerException : ex; }
                BeginInvoke(new Action(() => Finish(summary, error)));
            }) { IsBackground = true, Name = "scan" };
            worker.Start();
        }

        void Progress()
        {
            bar.Tick();
            Scanner sc = scanner;
            if (sc == null || sc.Total == 0 || sc.Phase != "Scanning") return;
            if (bar.Busy) bar.Busy = false;
            int done = sc.Done, total = sc.Total;
            bar.Value = (double)done / total;
            double secs = Math.Max(.001, (DateTime.UtcNow - started).TotalSeconds);
            SetStatus(string.Format(CultureInfo.CurrentCulture, "{0:N0} / {1:N0} demos  ·  {2:N0} players  ·  {3:N0} demos/s",
                                    done, total, sc.PlayerCount, done / secs), Theme.Muted);
        }

        void Finish(ScanSummary s, Exception error)
        {
            timer.Stop();
            scanner = null;
            bar.Busy = false;
            processBtn.Text = "PROCESS";
            processBtn.Primary = true;
            processBtn.Danger = false;
            UpdateButtons();
            if (closeWhenDone) { Close(); return; }

            if (error != null)
            {
                bar.Fill = Theme.Danger;
                bar.Value = 1;
                SetStatus("Stopped: " + error.Message, Theme.Danger);
                return;
            }
            if (s.Cancelled)
            {
                bar.Fill = Theme.Warn;
                SetStatus("Cancelled. Nothing was saved.", Theme.Warn);
                return;
            }
            if (s.Found == 0)
            {
                bar.Value = 0;
                SetStatus("No demos found in these folders.", Theme.Warn);
                return;
            }
            if (s.New == 0)
            {
                bar.Value = 1;
                SetStatus(string.Format(CultureInfo.CurrentCulture,
                    "Nothing new: all {0:N0} demos were exported before. Tick the box above to export them again.", s.Found), Theme.Muted);
                return;
            }
            bar.Value = 1;
            lastOutput = s.Output;
            openLink.Visible = true;
            int ok = s.Scanned - s.Errors.Count;
            string text = string.Format(CultureInfo.CurrentCulture, "Saved {0}  ·  {1:N0} demos, {2:N0} players  ·  {3} → {4}",
                                        Path.GetFileName(s.Output), ok, s.Players, Bytes(s.BytesIn), Bytes(s.BytesOut));
            if (s.Errors.Count > 0)
            {
                text += string.Format(CultureInfo.CurrentCulture, "  ·  {0:N0} unreadable", s.Errors.Count);
                tips.SetToolTip(status, "Skipped:\n" + string.Join("\n", s.Errors.GetRange(0, Math.Min(20, s.Errors.Count))));
            }
            SetStatus(text, s.Errors.Count > 0 ? Theme.Warn : Theme.AccentText);
        }

        static string Bytes(long n)
        {
            string[] units = { "B", "KB", "MB", "GB", "TB" };
            double v = n;
            int u = 0;
            while (v >= 1024 && u < units.Length - 1) { v /= 1024; u++; }
            return u == 0 ? n + " B" : v.ToString("0.0", CultureInfo.CurrentCulture) + " " + units[u];
        }

        void OpenOutput()
        {
            if (lastOutput == null) return;
            try { System.Diagnostics.Process.Start("explorer.exe", "/select,\"" + lastOutput + "\""); } catch (Exception) { }
        }
    }
}
