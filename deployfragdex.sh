#!/usr/bin/env bash
# Interactive first-time setup: writes .env and starts the containers.
set -euo pipefail
cd "$(dirname "$0")"

command -v docker >/dev/null || { echo "Docker is not installed."; exit 1; }
docker compose version >/dev/null 2>&1 || { echo "The Docker Compose plugin (docker compose) is required."; exit 1; }

BUILD_IMAGE="python:3.12-slim"   # same base image as app/Dockerfile
BUILD_WITH_HOST_NETWORK=0

# Can a container resolve and reach the internet? The image build (pip), Let's
# Encrypt and Steam lookups all need it.
container_dns_ok() {
  local t=""; command -v timeout >/dev/null && t="timeout 90"
  $t docker run --rm --pull=missing "$BUILD_IMAGE" \
    python -c "import socket; socket.create_connection((socket.gethostbyname('pypi.org'), 443), 10)" >/dev/null 2>&1
}

# The host's real upstream DNS servers (skipping local stubs such as systemd-resolved's 127.0.0.53).
upstream_dns() {
  local f=/etc/resolv.conf
  if grep -q '^nameserver 127\.' /etc/resolv.conf 2>/dev/null && [ -f /run/systemd/resolve/resolv.conf ]; then
    f=/run/systemd/resolve/resolv.conf
  fi
  awk '/^nameserver/ && $2 !~ /^127\./ && $2 !~ /:/ {print $2}' "$f" 2>/dev/null | head -n 3 || true
}

set_docker_dns() { # set_docker_dns ip [ip...]  -> writes "dns" into /etc/docker/daemon.json, restarts Docker
  local json="/etc/docker/daemon.json" list
  list="$(printf '"%s",' "$@")"; list="[${list%,}]"
  if [ -s "$json" ]; then
    command -v python3 >/dev/null || { echo "  python3 is needed to edit the existing $json - add \"dns\": $list yourself."; return 1; }
    sudo cp "$json" "$json.bak.$(date +%s)"
    sudo python3 -c 'import json,sys; p=sys.argv[1]; d=json.load(open(p)); d["dns"]=json.loads(sys.argv[2]); json.dump(d, open(p,"w"), indent=2)' "$json" "$list"
  else
    sudo mkdir -p /etc/docker
    printf '{\n  "dns": %s\n}\n' "$list" | sudo tee "$json" >/dev/null
  fi
  echo "  Wrote \"dns\": $list to $json; restarting Docker..."
  sudo systemctl restart docker
  sleep 3
}

check_container_network() {
  echo "Checking that containers can reach the internet (pulls $BUILD_IMAGE if needed)..."
  if container_dns_ok; then echo "  OK"; return 0; fi

  echo
  echo "  Containers on this host can't resolve or reach pypi.org."
  if getent hosts pypi.org >/dev/null 2>&1; then
    echo "  The host itself CAN, so this is Docker's container networking/DNS."
  else
    echo "  The host itself can't resolve pypi.org either - fix the server's internet/DNS first."
    exit 1
  fi
  grep -q '^nameserver 127\.' /etc/resolv.conf 2>/dev/null &&
    echo "  /etc/resolv.conf points at a local resolver (127.x), which containers can't use."

  local dns; dns="$(upstream_dns | tr '\n' ' ' || true)"; dns="${dns% }"
  [ -n "$dns" ] || dns="1.1.1.1 8.8.8.8"
  echo
  echo "  Fix options:"
  echo "    1) Point Docker at DNS servers $dns (edits /etc/docker/daemon.json, restarts Docker)"
  echo "    2) Restart Docker only (fixes it when a firewall such as ufw/firewalld was reloaded after Docker)"
  echo "    3) Build using the host's network this time (Let's Encrypt/Steam lookups may still fail)"
  echo "    4) Quit"
  local choice; read -r -p "  Choose [1]: " choice
  case "${choice:-1}" in
    1) # shellcheck disable=SC2086
       set_docker_dns $dns || exit 1 ;;
    2) sudo systemctl restart docker; sleep 3 ;;
    3) BUILD_WITH_HOST_NETWORK=1; return 0 ;;
    *) exit 1 ;;
  esac
  if container_dns_ok; then echo "  Fixed - containers can reach the internet now."; return 0; fi
  echo "  Still failing. Check that outbound DNS (port 53) and HTTPS from containers aren't blocked by a"
  echo "  firewall or your hosting provider, then re-run this script. Details: README > Troubleshooting."
  exit 1
}

PROJECT="fragdex"   # "name:" in docker-compose.yml; volumes are fragdex_<volume>

# Before the project name was pinned, Compose named the volumes after the folder
# (e.g. fragdex-main_fragdex_data). Copy such data to the pinned names once.
migrate_old_volumes() {
  docker volume inspect "${PROJECT}_fragdex_data" >/dev/null 2>&1 && return 0
  local olds old_vol old v ids moved=""
  olds="$(docker volume ls -q --filter label=com.docker.compose.volume=fragdex_data 2>/dev/null || true)"
  [ -n "$olds" ] || return 0
  if [ "$(printf '%s\n' "$olds" | wc -l)" -gt 1 ]; then
    echo "Found data from more than one earlier Fragdex install:"
    printf '  %s\n' $olds
    read -r -p "Volume to keep (blank = start with an empty database): " old_vol
    [ -n "$old_vol" ] || return 0
    printf '%s\n' "$olds" | grep -qx "$old_vol" || { echo "No volume named $old_vol."; exit 1; }
  else
    old_vol="$olds"
  fi
  old="${old_vol%_fragdex_data}"
  echo "Moving the data of the earlier install ('$old') to the fixed project name '$PROJECT'..."
  ids="$(docker ps -aq --filter "label=com.docker.compose.project=$old")"
  if [ -n "$ids" ]; then docker rm -f $ids >/dev/null; fi   # the old containers hold the ports
  for v in fragdex_data caddy_data caddy_config; do
    docker volume inspect "${old}_$v" >/dev/null 2>&1 || continue
    docker volume create --label "com.docker.compose.project=$PROJECT" \
      --label "com.docker.compose.volume=$v" "${PROJECT}_$v" >/dev/null
    docker run --rm -v "${old}_$v:/from:ro" -v "${PROJECT}_$v:/to" "$BUILD_IMAGE" cp -a /from/. /to/ || {
      echo "  Copying ${old}_$v failed; nothing was deleted. Fix the error above and re-run this script."
      docker volume rm "${PROJECT}_fragdex_data" "${PROJECT}_caddy_data" "${PROJECT}_caddy_config" >/dev/null 2>&1 || true
      exit 1
    }
    moved="$moved ${old}_$v"
  done
  docker network rm "${old}_default" >/dev/null 2>&1 || true
  echo "  Done. The old volumes are kept; once Fragdex is working, free the space with:"
  echo "    docker volume rm${moved}"
}

start_stack() {
  migrate_old_volumes
  if [ "$BUILD_WITH_HOST_NETWORK" = 1 ]; then
    docker build --network host -t fragdex:latest app
    docker compose up -d
  else
    docker compose up -d --build
  fi
}

check_container_network

if [ -f .env ]; then
  read -r -p ".env already exists. Overwrite it? [y/N] " yn
  [[ "$yn" =~ ^[Yy] ]] || { echo "Keeping existing .env. Starting..."; start_stack; exit 0; }
fi

ask() { # ask VAR "Prompt" default
  local __v; read -r -p "$2 [$3]: " __v; printf -v "$1" '%s' "${__v:-$3}"
}

lan_ip="$(hostname -I 2>/dev/null | awk '{print $1}')"
echo
echo "How will people reach this site? Enter a domain name (fragdex.example.com),"
echo "or your server's public IP if you don't have a domain."
echo "Your LAN IP looks like: ${lan_ip:-unknown}"
ask SITE_HOST "Domain or IP" "${lan_ip:-localhost}"
ask HTTP_PORT "HTTP port" "80"
ask HTTPS_PORT "HTTPS port" "443"
echo
echo "TLS modes: auto (Let's Encrypt with self-signed fallback), internal (self-signed),"
echo "           custom (your own ./certs/cert.pem + key.pem), off (plain HTTP)"
ask TLS_MODE "TLS mode" "auto"
ACME_EMAIL=""
if [ "$TLS_MODE" = "auto" ]; then ask ACME_EMAIL "E-mail for Let's Encrypt notices (optional)" ""; fi
ask ADMIN_USERNAME "Admin username" "admin"
ADMIN_PASSWORD="$(LC_ALL=C tr -dc 'A-Za-z0-9' </dev/urandom | head -c 20 || true)"
ask STEAM_API_KEY "Steam Web API key (optional)" ""
ask HOST_WATCH_DIR "Folder to watch for demos, logs and .fdx exports" "/home/demos"
mkdir -p certs
if [ ! -d "$HOST_WATCH_DIR" ]; then
  mkdir -p "$HOST_WATCH_DIR" 2>/dev/null || {
    echo "Creating $HOST_WATCH_DIR needs root:"
    sudo mkdir -p "$HOST_WATCH_DIR" && sudo chown "$(id -u):$(id -g)" "$HOST_WATCH_DIR"
  }
fi

sed -e "s|^SITE_HOST=.*|SITE_HOST=${SITE_HOST}|" \
    -e "s|^HTTP_PORT=.*|HTTP_PORT=${HTTP_PORT}|" \
    -e "s|^HTTPS_PORT=.*|HTTPS_PORT=${HTTPS_PORT}|" \
    -e "s|^TLS_MODE=.*|TLS_MODE=${TLS_MODE}|" \
    -e "s|^ACME_EMAIL=.*|ACME_EMAIL=${ACME_EMAIL}|" \
    -e "s|^ADMIN_USERNAME=.*|ADMIN_USERNAME=${ADMIN_USERNAME}|" \
    -e "s|^ADMIN_PASSWORD=.*|ADMIN_PASSWORD=${ADMIN_PASSWORD}|" \
    -e "s|^STEAM_API_KEY=.*|STEAM_API_KEY=${STEAM_API_KEY}|" \
    -e "s|^HOST_WATCH_DIR=.*|HOST_WATCH_DIR=${HOST_WATCH_DIR}|" \
    .env.example > .env
chmod 600 .env

start_stack

if [ "$TLS_MODE" = "off" ]; then
  url="http://${SITE_HOST}"; [ "$HTTP_PORT" = "80" ] || url="${url}:${HTTP_PORT}"
else
  url="https://${SITE_HOST}"; [ "$HTTPS_PORT" = "443" ] || url="${url}:${HTTPS_PORT}"
fi
cat <<EOF

  Fragdex is starting at:  ${url}

  Username: ${ADMIN_USERNAME}
  Password: ${ADMIN_PASSWORD}

  Change the password under Account after logging in, then remove
  ADMIN_PASSWORD from .env. If the certificate is self-signed your browser
  will warn once - that's expected without a domain/public IP.
  Logs:  docker compose logs -f
EOF
