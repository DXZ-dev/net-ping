#!/usr/bin/env bash
# Libera el puerto 53 para Bipon Net: desactiva el "stub listener" de
# systemd-resolved (el equipo sigue resolviendo nombres con los DNS de tu red)
# y detiene cualquier otro servicio que ocupe el puerto.
# Uso: sudo ./free-port-53.sh [--undo]
set -euo pipefail

CONF=/etc/systemd/resolved.conf.d/bipon-net.conf

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

# ------------------------------------------------------------------
# Libera el puerto 53 de forma automática: detiene y desactiva el servicio
# que lo ocupe (dnsmasq, pihole-FTL, AdGuardHome, bind9, unbound, una
# instalación anterior de NetPing/Bipon…) o termina el proceso suelto.
# No toca Docker/libvirt (pararlos tumbaría otros contenedores o VMs).
# ------------------------------------------------------------------
command -v ss >/dev/null || { echo "Sin 'ss' (iproute2): no se puede comprobar el puerto 53."; exit 0; }

pids53() { ss -H -lntup 2>/dev/null | awk '$5 ~ /[:.]53$/' | grep -oE 'pid=[0-9]+' | cut -d= -f2 | sort -u || true; }
unit_of() { local u; u=$(ps -o unit= -p "$1" 2>/dev/null | tr -d ' '); [[ -z "$u" || "$u" == "-" ]] && u=$(grep -oE '[^/]+\.service' "/proc/$1/cgroup" 2>/dev/null | tail -1); echo "$u"; }

for _ in 1 2 3; do
  PIDS=$(pids53)
  [[ -z "$PIDS" ]] && break
  for pid in $PIDS; do
    name=$(cat "/proc/$pid/comm" 2>/dev/null || echo "?")
    unit=$(unit_of "$pid")
    case "$unit" in
      docker.service|containerd.service|libvirtd.service|virtqemud.service)
        echo "AVISO: el puerto 53 lo usa $name ($unit): es un contenedor o red virtual; deténlo tú (no se toca para no afectar a otros servicios)."
        continue ;;
    esac
    if [[ -n "$unit" && "$unit" != *"session"* && "$unit" != user@* ]]; then
      echo "➜ El puerto 53 lo usa $name (servicio $unit): deteniéndolo y desactivándolo…"
      systemctl disable --now "$unit" >/dev/null 2>&1 || systemctl stop "$unit" >/dev/null 2>&1 || true
    else
      echo "➜ El puerto 53 lo usa el proceso $name (PID $pid): terminándolo…"
      kill "$pid" 2>/dev/null || true
    fi
  done
  for _ in 1 2 3 4 5 6; do [[ -z "$(pids53)" ]] && break; sleep 1; done
  for pid in $(pids53); do
    case "$(unit_of "$pid")" in docker.service|containerd.service|libvirtd.service|virtqemud.service) ;; *) kill -9 "$pid" 2>/dev/null || true ;; esac
  done
  sleep 1
done

if [[ -n "$(pids53)" ]]; then
  echo "AVISO: el puerto 53 sigue ocupado:"
  ss -lntup 2>/dev/null | awk '$5 ~ /[:.]53$/' || true
  exit 2
fi
echo "Puerto 53 libre."
