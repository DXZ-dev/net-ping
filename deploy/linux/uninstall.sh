#!/usr/bin/env bash
# Desinstala NetPing. Uso: sudo ./uninstall.sh [--purge]   (--purge borra también los datos)
set -euo pipefail
[[ $EUID -eq 0 ]] || { echo "Ejecuta como root."; exit 1; }
systemctl disable --now netping 2>/dev/null || true
rm -f /etc/systemd/system/netping.service
systemctl daemon-reload
rm -rf /opt/netping
if [[ "${1:-}" == "--purge" ]]; then
  rm -rf /var/lib/netping /etc/netping.env /etc/sysctl.d/99-netping.conf
  echo "Datos eliminados."
fi
bash "$(dirname "$0")/free-port-53.sh" --undo 2>/dev/null || true
echo "NetPing desinstalado."
