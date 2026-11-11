#!/usr/bin/env bash
# Desinstala Bipon Net. Uso: sudo bash uninstall.sh [--purge]   (--purge borra también los datos)
set -euo pipefail
[[ $EUID -eq 0 ]] || { echo "Ejecuta como root."; exit 1; }
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
systemctl disable --now bipon-net 2>/dev/null || true
rm -f /etc/systemd/system/bipon-net.service
systemctl daemon-reload
if [[ "${1:-}" == "--purge" ]]; then
  rm -rf /var/lib/bipon-net /etc/bipon-net.env /etc/sysctl.d/99-bipon-net.conf
  echo "Datos eliminados."
fi
bash "$HERE/free-port-53.sh" --undo 2>/dev/null || true
rm -rf /opt/bipon-net/venv
[[ "$HERE" == /opt/bipon-net/src/* ]] || rm -rf /opt/bipon-net
echo "Bipon Net desinstalado."
