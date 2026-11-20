#!/usr/bin/env bash
# Levanta Ciberrol con Docker Compose (reconstruye la imagen siempre).
#
#   ./up.sh          desarrollo / servidor propio  (docker-compose.yml, con Caddy incluido)
#   ./up.sh prod     producción con caddy-docker-proxy (docker-compose.prod.yml)
#
# Opciones:
#   --pull           hace "git pull" antes de levantar
set -euo pipefail

cd "$(dirname "$0")"

MODE="dev"
PULL=0
for arg in "$@"; do
  case "$arg" in
    prod|production) MODE="prod" ;;
    dev) MODE="dev" ;;
    --pull) PULL=1 ;;
    -h|--help) sed -n '2,8p' "$0" | sed -e 's/^# //' -e 's/^#$//'; exit 0 ;;
    *) echo "Argumento desconocido: $arg (usa --help)" >&2; exit 1 ;;
  esac
done

if [[ "$MODE" == "prod" ]]; then
  COMPOSE=(docker compose -f docker-compose.prod.yml)
else
  COMPOSE=(docker compose -f docker-compose.yml)
fi

die() { echo "❌ $*" >&2; exit 1; }
info() { echo "▶ $*"; }

command -v docker >/dev/null || die "Docker no está instalado."
docker compose version >/dev/null 2>&1 || die "Falta el plugin 'docker compose'."

# --- .env --------------------------------------------------------------------------------
if [[ ! -f .env ]]; then
  if [[ "$MODE" == "prod" ]]; then
    die "Falta .env. Cópialo desde .env.example y define al menos DOMAIN y POSTGRES_PASSWORD."
  fi
  # No se genera una clave nueva: si el volumen de Postgres ya existe, quedaría con otra clave.
  info "No hay .env: se usan los valores por defecto (ver .env.example para configurarlo)."
fi

envval() { [[ -f .env ]] || return 0; grep -E "^$1=" .env | tail -1 | cut -d= -f2- | sed -e 's/^["'\'']//' -e 's/["'\'']$//'; }

if [[ "$MODE" == "prod" ]]; then
  [[ -n "$(envval DOMAIN)" ]] || die "Define DOMAIN en .env (p. ej. DOMAIN=rol.midominio.cl)."
  pg=$(envval POSTGRES_PASSWORD)
  [[ -n "$pg" && "$pg" != "cambia-esta-clave" ]] || die "Define una POSTGRES_PASSWORD propia en .env."
  net=$(envval CADDY_NETWORK); net=${net:-caddy}
  docker network inspect "$net" >/dev/null 2>&1 \
    || die "No existe la red '$net' de caddy-docker-proxy. Revisa CADDY_NETWORK en .env o levanta el proxy primero."
fi

# --- código ------------------------------------------------------------------------------
if [[ "$PULL" == 1 ]]; then
  info "git pull"
  git pull --ff-only
fi

# --- levantar ----------------------------------------------------------------------------
info "Construyendo y levantando ($MODE)…"
"${COMPOSE[@]}" up -d --build --remove-orphans

info "Esperando a que la app responda…"
version=""
for _ in $(seq 1 60); do
  version=$("${COMPOSE[@]}" exec -T app python -c \
    "import json,urllib.request;print(json.load(urllib.request.urlopen('http://127.0.0.1:8000/healthz',timeout=2))['version'])" \
    2>/dev/null || true)
  [[ -n "$version" ]] && break
  sleep 1
done
if [[ -z "$version" ]]; then
  "${COMPOSE[@]}" logs --tail 50 app
  die "La app no respondió en 60 s (logs arriba)."
fi

echo
echo "✅ Ciberrol en marcha — versión v${version}"
"${COMPOSE[@]}" ps --format '   {{.Service}}: {{.Status}}'

if [[ "$MODE" == "prod" ]]; then
  url="https://$(envval DOMAIN)"
else
  url=$(envval PUBLIC_URL); url=${url:-http://localhost}
fi
echo "   Admin:         ${url}/admin"
echo "   Comprobación:  curl -s ${url}/healthz   (debe decir \"version\":\"${version}\")"

# La clave del admin solo se imprime la primera vez que se crea la cuenta.
creds=$("${COMPOSE[@]}" logs app 2>/dev/null | grep -A1 "Usuario:" | sed 's/^.*| *//' | tail -2 || true)
if [[ -n "$creds" ]]; then
  echo
  echo "🔑 Cuenta admin creada en este arranque (guárdala, no se vuelve a mostrar):"
  echo "$creds" | sed 's/^/   /'
else
  echo "   Clave admin olvidada: ${COMPOSE[*]} exec app python -m ciberrol.reset_admin"
fi
