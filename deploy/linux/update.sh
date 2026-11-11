#!/usr/bin/env bash
# Actualiza Bipon Net desde GitHub (lo lanza el botón «Actualizar» del panel).
# Conserva datos y configuración (/var/lib/bipon-net y /etc/bipon-net.env).
set -euo pipefail
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
BRANCH="${BIPON_BRANCH:-main}"
cd "$SRC"
echo "[$(date -Is)] Actualizando Bipon Net (rama $BRANCH) en $SRC"
GIT=(git)
if [[ -n "${BIPON_GIT_TOKEN:-}" ]]; then
  GIT=(git -c "http.extraheader=Authorization: Bearer ${BIPON_GIT_TOKEN}")
fi
REPO_URL="${BIPON_REPO_URL:-https://github.com/${BIPON_REPO:-DXZ-dev/net-ping}.git}"
command -v git >/dev/null || { apt-get update -qq && apt-get install -y -qq git >/dev/null; }
git config --global --add safe.directory "$SRC" 2>/dev/null || true
if [[ ! -d "$SRC/.git" ]]; then
  # instalación copiada sin historial git (script de Proxmox, zip): se convierte en repositorio
  echo "Inicializando repositorio git en $SRC"
  git init -q "$SRC"
fi
if git remote get-url origin >/dev/null 2>&1; then
  git remote set-url origin "$REPO_URL"
else
  git remote add origin "$REPO_URL"
fi
"${GIT[@]}" fetch --depth 50 origin "$BRANCH"
git reset --hard "origin/$BRANCH"
bash "$SRC/deploy/linux/install.sh" --update
echo "[$(date -Is)] Actualización completada: $(git rev-parse --short HEAD)"
