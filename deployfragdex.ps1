# Interactive first-time setup for Windows hosts (Docker Desktop):
# writes .env and starts the containers.  Run from PowerShell:
#   powershell -ExecutionPolicy Bypass -File .\deployfragdex.ps1
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) { Write-Host "Docker Desktop is not installed."; exit 1 }
$ErrorActionPreference = "Continue"
docker info *> $null
$dockerUp = $LASTEXITCODE -eq 0
$ErrorActionPreference = "Stop"
if (-not $dockerUp) { Write-Host "Docker Desktop must be running (docker info failed)."; exit 1 }

$BuildImage = "python:3.12-slim"   # same base image as app\Dockerfile
$script:BuildWithHostNetwork = $false

# Can a container resolve and reach the internet? The image build (pip), Let's
# Encrypt and Steam lookups all need it.
function Test-ContainerNetwork {
    # Windows PowerShell turns redirected native stderr into errors under "Stop"
    $ErrorActionPreference = "Continue"
    docker run --rm --pull=missing $BuildImage python -c "import socket; socket.create_connection((socket.gethostbyname('pypi.org'), 443), 10)" *> $null
    return ($LASTEXITCODE -eq 0)
}

function Assert-ContainerNetwork {
    Write-Host "Checking that containers can reach the internet (pulls $BuildImage if needed)..."
    if (Test-ContainerNetwork) { Write-Host "  OK"; return }
    Write-Host ""
    Write-Host "  Containers can't resolve or reach pypi.org."
    try { [void][Net.Dns]::GetHostAddresses("pypi.org"); Write-Host "  This PC itself CAN, so it's Docker Desktop's networking/DNS." }
    catch { Write-Host "  This PC can't resolve pypi.org either - fix its internet/DNS first."; exit 1 }
    Write-Host ""
    Write-Host "  Usual fixes:"
    Write-Host "    - Disconnect any VPN / proxy software, or allow Docker through it, then restart Docker Desktop."
    Write-Host "    - Docker Desktop > Settings > Docker Engine: add  ""dns"": [""1.1.1.1"", ""8.8.8.8""]  then Apply & restart."
    Write-Host "    - Run  wsl --shutdown  and start Docker Desktop again."
    Write-Host ""
    Write-Host "  1) I fixed it - check again    2) Build using this PC's network this time    3) Quit"
    $c = Read-Host "  Choose [1]"
    switch ($c) {
        "2" { $script:BuildWithHostNetwork = $true; return }
        "3" { exit 1 }
        default {
            if (Test-ContainerNetwork) { Write-Host "  OK"; return }
            Write-Host "  Still failing - see README > Troubleshooting."; exit 1
        }
    }
}

$Project = "fragdex"   # "name:" in docker-compose.yml; volumes are fragdex_<volume>

# Before the project name was pinned, Compose named the volumes after the folder
# (e.g. fragdex-main_fragdex_data). Copy such data to the pinned names once.
function Move-OldVolumes {
    $ErrorActionPreference = "Continue"
    docker volume inspect "${Project}_fragdex_data" *> $null
    if ($LASTEXITCODE -eq 0) { return }
    $olds = @(docker volume ls -q --filter label=com.docker.compose.volume=fragdex_data 2> $null | Where-Object { $_ })
    if ($olds.Count -eq 0) { return }
    if ($olds.Count -gt 1) {
        Write-Host "Found data from more than one earlier Fragdex install:"
        $olds | ForEach-Object { Write-Host "  $_" }
        $oldVol = (Read-Host "Volume to keep (blank = start with an empty database)").Trim()
        if (-not $oldVol) { return }
        if ($olds -notcontains $oldVol) { Write-Host "No volume named $oldVol."; exit 1 }
    } else { $oldVol = $olds[0] }
    $old = $oldVol.Substring(0, $oldVol.Length - "_fragdex_data".Length)
    Write-Host "Moving the data of the earlier install ('$old') to the fixed project name '$Project'..."
    $ids = @(docker ps -aq --filter "label=com.docker.compose.project=$old" | Where-Object { $_ })
    if ($ids.Count) { docker rm -f @ids | Out-Null }   # the old containers hold the ports
    $moved = @()
    foreach ($v in "fragdex_data", "caddy_data", "caddy_config") {
        docker volume inspect "${old}_$v" *> $null
        if ($LASTEXITCODE -ne 0) { continue }
        docker volume create --label "com.docker.compose.project=$Project" --label "com.docker.compose.volume=$v" "${Project}_$v" | Out-Null
        docker run --rm -v "${old}_${v}:/from:ro" -v "${Project}_${v}:/to" $BuildImage cp -a /from/. /to/
        if ($LASTEXITCODE -ne 0) {
            Write-Host "  Copying ${old}_$v failed; nothing was deleted. Fix the error above and re-run this script."
            docker volume rm "${Project}_fragdex_data" "${Project}_caddy_data" "${Project}_caddy_config" *> $null
            exit 1
        }
        $moved += "${old}_$v"
    }
    docker network rm "${old}_default" *> $null
    Write-Host "  Done. The old volumes are kept; once Fragdex is working, free the space with:"
    Write-Host "    docker volume rm $($moved -join ' ')"
}

function Start-Stack {
    Move-OldVolumes
    if ($script:BuildWithHostNetwork) {
        docker build --network host -t fragdex:latest app
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
        docker compose up -d
    } else {
        docker compose up -d --build
    }
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}

Assert-ContainerNetwork

if (Test-Path .env) {
    $yn = Read-Host ".env already exists. Overwrite it? [y/N]"
    if ($yn -notmatch '^[Yy]') { Write-Host "Keeping existing .env. Starting..."; Start-Stack; exit 0 }
}

function Ask([string]$prompt, [string]$default) {
    $v = Read-Host "$prompt [$default]"
    if ([string]::IsNullOrWhiteSpace($v)) { return $default }
    return $v.Trim()
}

$lanIp = (Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
          Where-Object { $_.IPAddress -notmatch '^(127\.|169\.254\.)' -and $_.PrefixOrigin -ne 'WellKnown' } |
          Select-Object -First 1).IPAddress
if (-not $lanIp) { $lanIp = "localhost" }

Write-Host ""
Write-Host "How will people reach this site? Enter a domain name (fragdex.example.com),"
Write-Host "or your public IP if you don't have a domain. Your LAN IP looks like: $lanIp"
$siteHost  = Ask "Domain or IP" $lanIp
$httpPort  = Ask "HTTP port" "80"
$httpsPort = Ask "HTTPS port" "443"
Write-Host ""
Write-Host "TLS modes: auto (Let's Encrypt with self-signed fallback), internal (self-signed),"
Write-Host "           custom (your own .\certs\cert.pem + key.pem), off (plain HTTP)"
$tlsMode   = Ask "TLS mode" "auto"
$acmeEmail = ""
if ($tlsMode -eq "auto") { $acmeEmail = Ask "E-mail for Let's Encrypt notices (optional)" "" }
$adminUser = Ask "Admin username" "admin"
$chars = [char[]]'ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789'
$rng = [Security.Cryptography.RandomNumberGenerator]::Create()
$bytes = New-Object byte[] 20; $rng.GetBytes($bytes)
$adminPass = -join ($bytes | ForEach-Object { $chars[$_ % $chars.Length] })
$steamKey  = Ask "Steam Web API key (optional)" ""
$watchDir  = Ask "Folder to watch for demos, logs and .fdx exports" "C:\demos"
New-Item -ItemType Directory -Force -Path $watchDir, (Join-Path $PSScriptRoot "certs") | Out-Null

$values = @{
    SITE_HOST = $siteHost; HTTP_PORT = $httpPort; HTTPS_PORT = $httpsPort; TLS_MODE = $tlsMode
    ACME_EMAIL = $acmeEmail; ADMIN_USERNAME = $adminUser; ADMIN_PASSWORD = $adminPass
    STEAM_API_KEY = $steamKey; HOST_WATCH_DIR = $watchDir
}
$lines = Get-Content .env.example | ForEach-Object {
    if ($_ -match '^([A-Z_]+)=' -and $values.ContainsKey($Matches[1])) { "$($Matches[1])=$($values[$Matches[1]])" } else { $_ }
}
[IO.File]::WriteAllText((Join-Path $PSScriptRoot ".env"), ($lines -join "`n") + "`n", (New-Object Text.UTF8Encoding($false)))

Start-Stack

if ($tlsMode -eq "off") { $url = "http://$siteHost"; if ($httpPort -ne "80") { $url += ":$httpPort" } }
else { $url = "https://$siteHost"; if ($httpsPort -ne "443") { $url += ":$httpsPort" } }

Write-Host @"

  Fragdex is starting at:  $url

  Username: $adminUser
  Password: $adminPass

  Change the password under Account after logging in, then remove
  ADMIN_PASSWORD from .env. Demos, logs and .fdx files dropped into
  $watchDir are imported automatically.
  Logs:  docker compose logs -f
"@
