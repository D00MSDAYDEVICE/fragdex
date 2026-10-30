# Development

## Repository layout

```text
├── deployfragdex.sh          Linux installer (writes .env, starts Docker)
├── deployfragdex.ps1         Windows installer (Docker Desktop)
├── docker-compose.yml        app + Caddy (TLS) services
├── .env.example              all settings, documented
├── caddy/entrypoint.sh       builds the Caddyfile from TLS_MODE / SITE_HOST / ports
├── certs/                    your own cert.pem + key.pem for TLS_MODE=custom
├── docs/  mkdocs.yml         this documentation site
├── app/
│   ├── Dockerfile
│   ├── requirements.txt      runtime deps (requirements-dev.txt adds pytest)
│   ├── fragdex/              the FastAPI application
│   │   ├── main.py           routes, auth, CSRF, uploads, downloads
│   │   ├── db.py             SQLite schema, search, alt-account linking
│   │   ├── worker.py         import queue, watched folder, Steam refresher
│   │   ├── importers/        demo / log / spreadsheet / .fdx parsers
│   │   ├── scanner.py        fragdex-scan CLI (also bundled as a standalone script)
│   │   ├── downloads/        prebuilt fragdex-scan.exe served to users
│   │   └── templates/  static/
│   ├── scanner-win/          the Windows fragdex-scan app (C#, WinForms)
│   ├── tests/
│   └── tools/                Huffman table generators, Windows scanner build script
└── .github/workflows/        CI and the documentation site
```

## Run it locally

```bash
pip install -r app/requirements-dev.txt
cd app && DATA_DIR=./.dev TLS_MODE=off uvicorn fragdex.main:app --reload
```

The first admin password is printed in the console. Set `WATCH_DIR` to a folder to try the watched-folder import.

## Tests

```bash
python -m pytest app/tests
```

`PLAYERS_XLSX=…` and `REAL_DEMO_DIR=…` point the optional real-data tests at your own files. On Windows, with the exe built, a test checks that the Windows and Python scanners produce identical exports.

## Generated code

```bash
cd app
python tools/gen_huffman.py         # regenerates the Quake 3 Huffman table (already committed)
python tools/gen_huffman_cs.py      # copies it into the Windows scanner
python -m fragdex.scanner_build     # writes the standalone fragdex-scan.py
```

The demo parser decodes the Quake 3 network Huffman code. Its table is built from ioquake3's `msg_hData` frequencies with a port of ioquake3's adaptive Huffman code.

## The Windows scanner

`fragdex-scan.exe` is C# 5 / WinForms, a port of the Python parser, compiled with the `csc.exe` that ships with the .NET Framework in every Windows installation: no Visual Studio or SDK needed.

```powershell
powershell -File app\tools\build_scanner_exe.ps1
```

It writes `app/fragdex/downloads/fragdex-scan.exe`, which the server offers for download. Rebuild it after changing the parser, and commit the result.

## This documentation

The site is built with [MkDocs](https://www.mkdocs.org/) and [Material for MkDocs](https://squidfunk.github.io/mkdocs-material/). The pages are Markdown files in `docs/`, the navigation is in `mkdocs.yml`.

```bash
pip install mkdocs-material==9.7.6
mkdocs serve          # live preview at http://127.0.0.1:8000/fragdex/
mkdocs build --strict # what CI runs
```

Pushing changes under `docs/` or `mkdocs.yml` to `main` publishes the site to GitHub Pages through `.github/workflows/docs.yml`.

## Continuous integration

`.github/workflows/ci.yml` runs on every push and pull request: the test suite, a Python 3.8 compatibility check of the standalone scanner, a Docker image build with Caddyfile validation, and the Windows scanner build with its parity test.
