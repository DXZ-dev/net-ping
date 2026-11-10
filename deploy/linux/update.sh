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
"${GIT[@]}" fetch --depth 50 origin "$BRANCH"
git reset --hard "origin/$BRANCH"
bash "$SRC/deploy/linux/install.sh" --update
echo "[$(date -Is)] Actualización completada: $(git rev-parse --short HEAD)"
