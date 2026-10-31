#!/bin/sh
# Builds /etc/caddy/Caddyfile from .env settings, then runs Caddy.
#
# TLS_MODE:
#   auto     (default) per host in SITE_HOST:
#              public domain -> Let's Encrypt, falling back to a self-signed cert
#              public IP     -> Let's Encrypt IP certificate (6-day "shortlived"
#                               profile, auto-renewed), falling back to self-signed
#              private IP / LAN name -> self-signed (Caddy's local CA)
#   internal always self-signed
#   custom   use /certs/cert.pem + /certs/key.pem (mounted from ./certs)
#   off      plain HTTP on HTTP_PORT (put your own proxy in front)
#
# TRUSTED_PROXIES: IP ranges of a proxy in front of Caddy (e.g. with TLS_MODE=off), comma separated, or
#   private_ranges. Requests from them are trusted to name the real client in X-Forwarded-For.
#
# The app gets the client's address in X-Fragdex-Client-IP, which Caddy always sets itself, whatever the
# client sent: the address it connected from, or the one a trusted proxy reported.
set -eu

HTTP_PORT="${HTTP_PORT:-80}"
HTTPS_PORT="${HTTPS_PORT:-443}"
TLS_MODE="$(printf '%s' "${TLS_MODE:-auto}" | tr 'A-Z' 'a-z')"
SITE_HOST="$(printf '%s' "${SITE_HOST:-}" | tr ',' ' ')"
ACME_EMAIL="${ACME_EMAIL:-}"
ACME_CA="${ACME_CA:-}"
UPSTREAM="${UPSTREAM:-app:8000}"
TRUSTED_PROXIES="$(printf '%s' "${TRUSTED_PROXIES:-}" | tr ',' ' ')"
OUT=/etc/caddy/Caddyfile

is_ipv4() { printf '%s' "$1" | grep -Eq '^([0-9]{1,3}\.){3}[0-9]{1,3}$'; }
is_ipv6() { case "$1" in *:*) return 0 ;; *) return 1 ;; esac; }

is_private() {
  h="$1"
  if is_ipv4 "$h"; then
    case "$h" in
      10.*|127.*|192.168.*|169.254.*|172.1[6-9].*|172.2[0-9].*|172.3[01].*) return 0 ;;
      100.6[4-9].*|100.[7-9][0-9].*|100.1[01][0-9].*|100.12[0-7].*) return 0 ;;
    esac
    return 1
  fi
  if is_ipv6 "$h"; then
    case "$h" in
      ::1|[fF][cCdD]*|[fF][eE]80:*) return 0 ;;
    esac
    return 1
  fi
  case "$h" in
    localhost|*.localhost|*.local|*.lan|*.home|*.internal|*.test) return 0 ;;
    *.*) return 1 ;;
  esac
  return 0 # single-label names can't get public certificates
}

site_addr() {
  if is_ipv6 "$1"; then printf '[%s]' "$1"; else printf '%s' "$1"; fi
}

acme_issuer() {
  # $1 = extra line for the acme block
  if [ -z "$ACME_CA$ACME_EMAIL$1" ]; then
    printf '\t\tissuer acme\n'
    return 0
  fi
  printf '\t\tissuer acme {\n'
  [ -n "$ACME_CA" ] && printf '\t\t\tdir %s\n' "$ACME_CA"
  [ -n "$ACME_EMAIL" ] && printf '\t\t\temail %s\n' "$ACME_EMAIL"
  [ -n "$1" ] && printf '\t\t\t%s\n' "$1"
  printf '\t\t}\n'
}

tls_block() {
  h="$1"
  case "$TLS_MODE" in
    internal) printf '\ttls internal\n' ;;
    custom) printf '\ttls /certs/cert.pem /certs/key.pem\n' ;;
    *)
      if is_private "$h"; then
        printf '\ttls internal\n'
      elif is_ipv4 "$h" || is_ipv6 "$h"; then
        printf '\ttls {\n'
        acme_issuer "profile shortlived"
        printf '\t\tissuer internal\n\t}\n'
      else
        printf '\ttls {\n'
        acme_issuer ""
        printf '\t\tissuer internal\n\t}\n'
      fi
      ;;
  esac
}

if [ "$TLS_MODE" != "off" ] && [ -z "$SITE_HOST" ]; then
  echo "[fragdex] SITE_HOST is empty - using 'localhost' with a self-signed cert." >&2
  echo "[fragdex] Set SITE_HOST in .env to your domain or server IP (run ./deployfragdex.sh)." >&2
  SITE_HOST="localhost"
fi
if [ "$TLS_MODE" = "custom" ] && { [ ! -f /certs/cert.pem ] || [ ! -f /certs/key.pem ]; }; then
  echo "[fragdex] TLS_MODE=custom but ./certs/cert.pem or ./certs/key.pem is missing." >&2
  exit 1
fi

case "$TRUSTED_PROXIES" in
  *[!0-9A-Za-z:./_\ ]*)
    echo "[fragdex] TRUSTED_PROXIES must be IP ranges such as 10.0.0.0/8, or private_ranges." >&2
    exit 1 ;;
esac

first_host="${SITE_HOST%% *}"

{
  printf '{\n'
  printf '\thttp_port %s\n\thttps_port %s\n' "$HTTP_PORT" "$HTTPS_PORT"
  if [ "$TLS_MODE" = "off" ]; then
    printf '\tauto_https off\n'
  else
    # Browsers send no SNI when connecting to a bare IP; use this name's cert then.
    printf '\tdefault_sni %s\n' "$first_host"
  fi
  if [ -n "$TRUSTED_PROXIES" ]; then
    # right to left: the first address that isn't a trusted proxy is the client
    printf '\tservers {\n\t\ttrusted_proxies static %s\n\t\ttrusted_proxies_strict\n\t}\n' "$TRUSTED_PROXIES"
  fi
  printf '}\n\n'

  printf '(fragdex) {\n'
  printf '\tencode zstd gzip\n'
  printf '\theader -Server\n'
  printf '\treverse_proxy %s {\n\t\theader_up X-Fragdex-Client-IP {client_ip}\n\t}\n' "$UPSTREAM"
  printf '}\n\n'

  if [ "$TLS_MODE" = "off" ]; then
    printf ':%s {\n\timport fragdex\n}\n' "$HTTP_PORT"
  else
    for h in $SITE_HOST; do
      printf '%s {\n' "$(site_addr "$h")"
      printf '\timport fragdex\n'
      tls_block "$h"
      printf '}\n\n'
    done
  fi
} > "$OUT"

echo "[fragdex] TLS_MODE=$TLS_MODE SITE_HOST='$SITE_HOST' HTTP_PORT=$HTTP_PORT HTTPS_PORT=$HTTPS_PORT"
echo "[fragdex] Generated $OUT:"
cat "$OUT"

caddy fmt --overwrite "$OUT" >/dev/null 2>&1 || true
exec caddy run --config "$OUT" --adapter caddyfile
