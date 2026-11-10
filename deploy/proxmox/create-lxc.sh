#!/usr/bin/env bash
# =====================================================================
#  Crea un contenedor LXC en Proxmox VE con Bipon Net instalado.
#  Ejecútalo EN EL HOST PROXMOX, desde la carpeta del repositorio:
#
#     bash deploy/proxmox/create-lxc.sh                       # DHCP
#     IP=192.168.1.53/24 GW=192.168.1.1 bash deploy/proxmox/create-lxc.sh   # IP fija (recomendado)
#
#  Variables (opcionales):
#     CTID       id del contenedor (por defecto, el siguiente libre)
#     CT_NAME    hostname (bipon-net)
#     STORAGE    almacenamiento del disco (local-lvm)
#     TPL_STORE  almacenamiento de plantillas (local)
#     BRIDGE     puente de red (vmbr0)
#     IP / GW    IP en formato CIDR y puerta de enlace; IP=dhcp por defecto
#     VLAN       etiqueta VLAN (opcional)
#     CORES=2 MEMORY=1024 DISK=8
#     PASSWORD   contraseña del panel (si no, se crea en la web)
#     TZ         zona horaria, p. ej. America/Mexico_City
#     TAILSCALE=1  instala Tailscale
# =====================================================================
set -euo pipefail

command -v pct >/dev/null || { echo "Este script debe ejecutarse en un host Proxmox VE."; exit 1; }
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

CTID="${CTID:-$(pvesh get /cluster/nextid)}"
CT_NAME="${CT_NAME:-bipon-net}"
STORAGE="${STORAGE:-local-lvm}"
TPL_STORE="${TPL_STORE:-local}"
BRIDGE="${BRIDGE:-vmbr0}"
IP="${IP:-dhcp}"
GW="${GW:-}"
VLAN="${VLAN:-}"
CORES="${CORES:-2}"
MEMORY="${MEMORY:-1024}"
DISK="${DISK:-8}"
PASSWORD="${PASSWORD:-}"
TZ_SET="${TZ:-}"
TAILSCALE="${TAILSCALE:-0}"

info() { printf '\e[36m➜\e[0m %s\n' "$*"; }
ok() { printf '\e[32m✔\e[0m %s\n' "$*"; }

info "Buscando la plantilla Debian 12 más reciente…"
pveam update >/dev/null
TEMPLATE="$(pveam available --section system | awk '/debian-12-standard/ {print $2}' | sort -V | tail -1)"
[[ -n "$TEMPLATE" ]] || { echo "No se encontró plantilla debian-12-standard"; exit 1; }
if ! pveam list "$TPL_STORE" | grep -q "$TEMPLATE"; then
  info "Descargando $TEMPLATE…"
  pveam download "$TPL_STORE" "$TEMPLATE" >/dev/null
fi
ok "Plantilla: $TEMPLATE"

NET="name=eth0,bridge=${BRIDGE},ip=${IP}"
[[ -n "$GW" && "$IP" != "dhcp" ]] && NET+=",gw=${GW}"
[[ -n "$VLAN" ]] && NET+=",tag=${VLAN}"

info "Creando contenedor $CTID ($CT_NAME)…"
pct create "$CTID" "${TPL_STORE}:vztmpl/${TEMPLATE}" \
  --hostname "$CT_NAME" --cores "$CORES" --memory "$MEMORY" --swap 512 \
  --rootfs "${STORAGE}:${DISK}" --net0 "$NET" \
  --unprivileged 1 --features nesting=1,keyctl=1 --onboot 1 \
  --nameserver 1.1.1.1 --description "Bipon Net — DNS filtrante, Unbound, VPN y herramientas de red" >/dev/null

# /dev/net/tun para WireGuard / OpenVPN / Tailscale dentro del contenedor
CONF="/etc/pve/lxc/${CTID}.conf"
cat >> "$CONF" <<'EOF'
lxc.cgroup2.devices.allow: c 10:200 rwm
lxc.mount.entry: /dev/net/tun dev/net/tun none bind,create=file
EOF
ok "Contenedor creado (TUN habilitado)"

# reenvío IP en el host (necesario para que los clientes VPN lleguen a la LAN)
sysctl -q -w net.ipv4.ip_forward=1 || true

pct start "$CTID"
info "Esperando a la red del contenedor…"
for _ in $(seq 1 30); do
  if pct exec "$CTID" -- ping -c1 -W1 1.1.1.1 >/dev/null 2>&1; then break; fi
  sleep 2
done

info "Copiando Bipon Net al contenedor…"
TMP="$(mktemp -d)"
tar -C "$REPO_DIR" --exclude=.git --exclude=data --exclude='__pycache__' -czf "$TMP/bipon-net.tgz" .
pct push "$CTID" "$TMP/bipon-net.tgz" /root/bipon-net.tgz
rm -rf "$TMP"

ARGS=()
[[ -n "$PASSWORD" ]] && ARGS+=(--password "$PASSWORD")
[[ -n "$TZ_SET" ]] && ARGS+=(--tz "$TZ_SET")
[[ "$TAILSCALE" == "1" ]] && ARGS+=(--with-tailscale)

ARGSTR=""
if [[ ${#ARGS[@]} -gt 0 ]]; then ARGSTR="$(printf '%q ' "${ARGS[@]}")"; fi

info "Instalando Bipon Net dentro del contenedor (2-4 min)…"
pct exec "$CTID" -- bash -c "apt-get update -qq && apt-get install -y -qq rsync >/dev/null && mkdir -p /root/bipon-net && tar -xzf /root/bipon-net.tgz -C /root/bipon-net && bash /root/bipon-net/deploy/linux/install.sh ${ARGSTR}"

CT_IP="$(pct exec "$CTID" -- bash -c "ip -4 -o addr show eth0 | awk '{print \$4}' | cut -d/ -f1")"
echo
ok "Bipon Net instalado en el contenedor $CTID"
echo "   Panel web:    http://${CT_IP}:8080"
echo "   Servidor DNS: ${CT_IP}"
[[ "$IP" == "dhcp" ]] && echo "   Consejo: reserva esta IP en el DHCP del router (un DNS debe tener IP fija)."
echo "   Configura ${CT_IP} como DNS en el DHCP de tu router."
