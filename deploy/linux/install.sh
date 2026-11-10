#!/usr/bin/env bash
# =====================================================================
#  Instalador de NetPing para Debian 12+/Ubuntu 22.04+ (también dentro
#  de un contenedor LXC de Proxmox). Idempotente: vuelve a ejecutarlo
#  para actualizar; los datos en /var/lib/netping se conservan.
#
#  Uso:  sudo ./deploy/linux/install.sh [opciones]
#    --password CLAVE     contraseña inicial del panel (si no, se crea en la web)
#    --web-port 8080      puerto de la interfaz web
#    --dns-port 53        puerto DNS
#    --tz Zona/Ciudad     zona horaria (p. ej. America/Mexico_City)
#    --with-tailscale     instala Tailscale desde su repositorio oficial
#    --no-vpn             no instala WireGuard ni OpenVPN
# =====================================================================
set -euo pipefail

APP_DIR=/opt/netping
DATA_DIR=/var/lib/netping
ENV_FILE=/etc/netping.env
SERVICE=/etc/systemd/system/netping.service
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

PASSWORD="" WEB_PORT=8080 DNS_PORT=53 TZ_SET="" WITH_TS=0 WITH_VPN=1
while [[ $# -gt 0 ]]; do
  case "$1" in
    --password) PASSWORD="$2"; shift 2 ;;
    --web-port) WEB_PORT="$2"; shift 2 ;;
    --dns-port) DNS_PORT="$2"; shift 2 ;;
    --tz) TZ_SET="$2"; shift 2 ;;
    --with-tailscale) WITH_TS=1; shift ;;
    --no-vpn) WITH_VPN=0; shift ;;
    -h|--help) sed -n '2,16p' "$0"; exit 0 ;;
    *) echo "Opción desconocida: $1"; exit 1 ;;
  esac
done

c_ok() { printf '\e[32m✔\e[0m %s\n' "$*"; }
c_info() { printf '\e[36m➜\e[0m %s\n' "$*"; }
c_warn() { printf '\e[33m!\e[0m %s\n' "$*"; }

[[ $EUID -eq 0 ]] || { echo "Ejecuta como root: sudo $0"; exit 1; }
command -v apt-get >/dev/null || { echo "Este instalador requiere Debian/Ubuntu (apt)."; exit 1; }
[[ -f "$REPO_DIR/backend/netping/main.py" ]] || { echo "No encuentro el código en $REPO_DIR"; exit 1; }

c_info "Instalando dependencias del sistema…"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
PKGS=(python3 python3-venv python3-pip unbound dns-root-data iputils-ping traceroute whois iproute2 iptables ca-certificates curl tzdata rsync)
apt-cache show unbound-anchor >/dev/null 2>&1 && PKGS+=(unbound-anchor)
[[ $WITH_VPN -eq 1 ]] && PKGS+=(wireguard-tools openvpn)
apt-get install -y -qq --no-install-recommends "${PKGS[@]}" >/dev/null
c_ok "Paquetes instalados"

if [[ $WITH_TS -eq 1 ]] && ! command -v tailscale >/dev/null; then
  c_info "Instalando Tailscale (repositorio oficial)…"
  . /etc/os-release
  DIST="${ID}"; CODENAME="${VERSION_CODENAME}"
  curl -fsSL "https://pkgs.tailscale.com/stable/${DIST}/${CODENAME}.noarmor.gpg" -o /usr/share/keyrings/tailscale-archive-keyring.gpg
  curl -fsSL "https://pkgs.tailscale.com/stable/${DIST}/${CODENAME}.tailscale-keyring.list" -o /etc/apt/sources.list.d/tailscale.list
  apt-get update -qq && apt-get install -y -qq tailscale >/dev/null
  # NetPing gestiona su propio tailscaled si el del sistema no está activo
  c_ok "Tailscale instalado"
fi

# El servicio unbound del sistema ocuparía el puerto 53: NetPing lanza su propia instancia en 127.0.0.1:5335
if systemctl list-unit-files unbound.service >/dev/null 2>&1; then
  systemctl disable --now unbound >/dev/null 2>&1 || true
  c_ok "Servicio unbound del sistema desactivado (NetPing gestiona el suyo)"
fi

if [[ "$DNS_PORT" == "53" ]]; then
  c_info "Comprobando el puerto 53…"
  bash "$REPO_DIR/deploy/linux/free-port-53.sh" || c_warn "Revisa qué servicio usa el puerto 53 antes de continuar"
fi

c_info "Copiando NetPing a $APP_DIR…"
mkdir -p "$APP_DIR" "$DATA_DIR"
rsync -a --delete --exclude data --exclude .git --exclude '__pycache__' "$REPO_DIR/backend" "$REPO_DIR/frontend" "$APP_DIR/"
python3 -m venv "$APP_DIR/venv"
"$APP_DIR/venv/bin/pip" install -q --upgrade pip
"$APP_DIR/venv/bin/pip" install -q -r "$APP_DIR/backend/requirements.txt"
chmod 700 "$DATA_DIR"
c_ok "Entorno Python listo"

# reenvío IP para VPN (clientes que acceden a la LAN)
if [[ $WITH_VPN -eq 1 ]]; then
  echo "net.ipv4.ip_forward=1" > /etc/sysctl.d/99-netping.conf
  sysctl -q -p /etc/sysctl.d/99-netping.conf || c_warn "No se pudo activar ip_forward (normal en algunos LXC: actívalo en el host)"
fi

if [[ -n "$TZ_SET" ]]; then timedatectl set-timezone "$TZ_SET" 2>/dev/null || ln -sf "/usr/share/zoneinfo/$TZ_SET" /etc/localtime; fi

if [[ ! -f "$ENV_FILE" ]]; then
  cat > "$ENV_FILE" <<EOF
NETPING_DATA_DIR=$DATA_DIR
NETPING_FRONTEND_DIR=$APP_DIR/frontend
NETPING_WEB_PORT=$WEB_PORT
NETPING_DNS_PORT=$DNS_PORT
NETPING_LOG_LEVEL=INFO
EOF
  chmod 600 "$ENV_FILE"
fi
if [[ -n "$PASSWORD" ]]; then
  sed -i '/^NETPING_PASSWORD=/d' "$ENV_FILE"; echo "NETPING_PASSWORD=$PASSWORD" >> "$ENV_FILE"
fi

cat > "$SERVICE" <<EOF
[Unit]
Description=NetPing — DNS filtrante, Unbound, VPN y herramientas de red
Documentation=https://github.com/dxz-dev/net-ping
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
EnvironmentFile=$ENV_FILE
WorkingDirectory=$APP_DIR/backend
ExecStart=$APP_DIR/venv/bin/python -m netping
Restart=always
RestartSec=3
# Se ejecuta como root: gestiona interfaces VPN, iptables y el puerto 53.
LimitNOFILE=65535
ProtectHome=true
PrivateTmp=true
ProtectKernelModules=true
ProtectControlGroups=true

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now netping >/dev/null
systemctl restart netping
sleep 4
if systemctl is-active --quiet netping; then
  IP=$(ip -4 route get 1.1.1.1 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="src"){print $(i+1); exit}}')
  echo
  c_ok "NetPing está en marcha"
  echo "   Panel web:   http://${IP:-IP-DEL-EQUIPO}:${WEB_PORT}"
  echo "   Servidor DNS: ${IP:-IP-DEL-EQUIPO} (puerto ${DNS_PORT})"
  echo "   Registro:     journalctl -u netping -f"
  echo
  echo "Siguiente paso: en tu router pon ${IP:-esta IP} como único DNS del DHCP"
  echo "y abre el Asistente en el panel para optimizar la red."
else
  c_warn "El servicio no arrancó. Revisa: journalctl -u netping -n 50"
  exit 1
fi
