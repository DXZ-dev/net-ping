#!/usr/bin/env bash
# Libera el puerto 53 desactivando el "stub listener" de systemd-resolved
# (Ubuntu, Debian con resolved, Fedora…). El equipo seguirá resolviendo
# nombres con los DNS que le dé tu red. Uso: sudo ./free-port-53.sh [--undo]
set -euo pipefail

CONF=/etc/systemd/resolved.conf.d/netping.conf

if [[ $EUID -ne 0 ]]; then echo "Ejecuta como root (sudo)."; exit 1; fi

if [[ "${1:-}" == "--undo" ]]; then
  rm -f "$CONF"
  if [[ -e /run/systemd/resolve/stub-resolv.conf ]]; then ln -sf /run/systemd/resolve/stub-resolv.conf /etc/resolv.conf; fi
  systemctl restart systemd-resolved 2>/dev/null || true
  echo "Restaurado el stub listener de systemd-resolved."
  exit 0
fi

if ! systemctl is-active --quiet systemd-resolved 2>/dev/null; then
  echo "systemd-resolved no está activo: nada que hacer."
else
  mkdir -p "$(dirname "$CONF")"
  printf '[Resolve]\nDNSStubListener=no\n' > "$CONF"
  ln -sf /run/systemd/resolve/resolv.conf /etc/resolv.conf
  systemctl restart systemd-resolved
  echo "Stub listener de systemd-resolved desactivado."
fi

if command -v ss >/dev/null; then
  if ss -lunp 2>/dev/null | grep -qE '[:.]53\s'; then
    echo "AVISO: algo sigue usando el puerto 53:"
    ss -lunp | grep -E '[:.]53\s' || true
    echo "Detén ese servicio (dnsmasq, pihole-FTL, AdGuardHome, unbound…) antes de iniciar NetPing."
    exit 2
  fi
fi
echo "Puerto 53 libre."
