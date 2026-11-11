#!/usr/bin/env bash
# =====================================================================
#  Instalador de Bipon Net para Debian 12+/Ubuntu 22.04+ (también LXC
#  de Proxmox). Idempotente: ejecútalo de nuevo para actualizar.
#  Migra automáticamente una instalación anterior de «NetPing».
#
#  Uso:  sudo bash deploy/linux/install.sh [opciones]
#    --password CLAVE     contraseña inicial (si no, se crea en la web)
#    --web-port 8080      puerto de la interfaz web
#    --dns-port 53        puerto DNS
#    --tz Zona/Ciudad     zona horaria (p. ej. America/Santo_Domingo)
#    --with-tailscale     instala Tailscale (repositorio oficial)
#    --no-vpn             no instala WireGuard ni OpenVPN
#    --update             modo actualización (lo usa el botón «Actualizar»)
# =====================================================================
set -euo pipefail

APP_DIR=/opt/bipon-net
SRC_DIR=$APP_DIR/src
DATA_DIR=/var/lib/bipon-net
ENV_FILE=/etc/bipon-net.env
SERVICE=/etc/systemd/system/bipon-net.service
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

PASSWORD="" WEB_PORT=8080 DNS_PORT=53 TZ_SET="" WITH_TS=0 WITH_VPN=1 UPDATE=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --password) PASSWORD="$2"; shift 2 ;;
    --web-port) WEB_PORT="$2"; shift 2 ;;
    --dns-port) DNS_PORT="$2"; shift 2 ;;
    --tz) TZ_SET="$2"; shift 2 ;;
    --with-tailscale) WITH_TS=1; shift ;;
    --no-vpn) WITH_VPN=0; shift ;;
    --update) UPDATE=1; shift ;;
    -h|--help) sed -n '2,18p' "$0"; exit 0 ;;
    *) echo "Opción desconocida: $1"; exit 1 ;;
  esac
done

c_ok() { printf '\e[32m✔\e[0m %s\n' "$*"; }
c_info() { printf '\e[36m➜\e[0m %s\n' "$*"; }
c_warn() { printf '\e[33m!\e[0m %s\n' "$*"; }

[[ $EUID -eq 0 ]] || { echo "Ejecuta como root: sudo bash $0"; exit 1; }
command -v apt-get >/dev/null || { echo "Este instalador requiere Debian/Ubuntu (apt)."; exit 1; }
[[ -f "$REPO_DIR/backend/bipon/main.py" ]] || { echo "No encuentro el código en $REPO_DIR"; exit 1; }

# ---------------------------------------------------------------- dependencias
c_info "Instalando dependencias del sistema…"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
PKGS=(python3 python3-venv python3-pip unbound dns-root-data iputils-ping traceroute whois iproute2 iptables nftables conntrack
      ca-certificates curl tzdata rsync git openssh-client)
apt-cache show unbound-anchor >/dev/null 2>&1 && PKGS+=(unbound-anchor)
[[ $WITH_VPN -eq 1 ]] && PKGS+=(wireguard-tools openvpn)
apt-get install -y -qq --no-install-recommends "${PKGS[@]}" >/dev/null
c_ok "Paquetes instalados"
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' \
  || { echo "Bipon Net necesita Python 3.9 o superior (Debian 11+ / Ubuntu 22.04+)."; exit 1; }

if [[ $WITH_TS -eq 1 ]] && ! command -v tailscale >/dev/null; then
  c_info "Instalando Tailscale (repositorio oficial)…"
  . /etc/os-release
  curl -fsSL "https://pkgs.tailscale.com/stable/${ID}/${VERSION_CODENAME}.noarmor.gpg" -o /usr/share/keyrings/tailscale-archive-keyring.gpg
  curl -fsSL "https://pkgs.tailscale.com/stable/${ID}/${VERSION_CODENAME}.tailscale-keyring.list" -o /etc/apt/sources.list.d/tailscale.list
  apt-get update -qq && apt-get install -y -qq tailscale >/dev/null
  c_ok "Tailscale instalado"
fi

# ---------------------------------------------------------------- migración desde NetPing
if [[ -f /etc/systemd/system/netping.service ]]; then
  c_info "Migrando la instalación anterior de NetPing…"
  systemctl disable --now netping >/dev/null 2>&1 || true
  rm -f /etc/systemd/system/netping.service
  systemctl daemon-reload
fi
if [[ -d /var/lib/netping && ! -d "$DATA_DIR" ]]; then
  mv /var/lib/netping "$DATA_DIR"
  c_ok "Datos migrados a $DATA_DIR (usuarios, listas, dispositivos y configuración se conservan)"
fi
MIGR_PW=""
if [[ -f /etc/netping.env ]]; then
  MIGR_PW="$(grep -E '^NETPING_PASSWORD=' /etc/netping.env | sed 's/^NETPING_/BIPON_/' || true)"
  rm -f /etc/netping.env
fi
rm -rf /opt/netping
[[ -f /etc/sysctl.d/99-netping.conf ]] && mv /etc/sysctl.d/99-netping.conf /etc/sysctl.d/99-bipon-net.conf

# El servicio unbound del sistema ocuparía el puerto 53: Bipon ejecuta su propia instancia en 127.0.0.1:5335
if systemctl list-unit-files unbound.service >/dev/null 2>&1; then
  systemctl disable --now unbound >/dev/null 2>&1 || true
fi
if [[ "$DNS_PORT" == "53" ]]; then
  c_info "Liberando el puerto 53…"
  # Nuestro propio servicio se reinicia al final: se detiene para que no cuente como conflicto.
  systemctl stop bipon-net >/dev/null 2>&1 || true
  if bash "$REPO_DIR/deploy/linux/free-port-53.sh"; then
    c_ok "Puerto 53 libre"
  else
    c_warn "El puerto 53 sigue ocupado (ver arriba): Bipon Net arrancará, pero sin DNS hasta que lo liberes"
  fi
fi

# ---------------------------------------------------------------- código (copia git para las actualizaciones)
mkdir -p "$APP_DIR" "$DATA_DIR"
if [[ "$REPO_DIR" != "$SRC_DIR" ]]; then
  c_info "Copiando Bipon Net a $SRC_DIR…"
  mkdir -p "$SRC_DIR"
  rsync -a --delete --exclude data --exclude '__pycache__' --exclude '.venv' "$REPO_DIR/" "$SRC_DIR/"
fi
git config --global --add safe.directory "$SRC_DIR" 2>/dev/null || true
git -C "$SRC_DIR" rev-parse HEAD > "$SRC_DIR/COMMIT" 2>/dev/null || true

python3 -m venv "$APP_DIR/venv"
"$APP_DIR/venv/bin/pip" install -q --upgrade pip
"$APP_DIR/venv/bin/pip" install -q -r "$SRC_DIR/backend/requirements.txt"
chmod 700 "$DATA_DIR"
c_ok "Entorno Python listo"

if [[ $WITH_VPN -eq 1 ]]; then
  echo "net.ipv4.ip_forward=1" > /etc/sysctl.d/99-bipon-net.conf
  sysctl -q -p /etc/sysctl.d/99-bipon-net.conf 2>/dev/null || c_warn "No se pudo activar ip_forward (normal en algunos LXC: actívalo en el host)"
fi
if [[ -n "$TZ_SET" ]]; then timedatectl set-timezone "$TZ_SET" 2>/dev/null || ln -sf "/usr/share/zoneinfo/$TZ_SET" /etc/localtime; fi

if [[ ! -f "$ENV_FILE" ]]; then
  {
    echo "BIPON_DATA_DIR=$DATA_DIR"
    echo "BIPON_FRONTEND_DIR=$SRC_DIR/frontend"
    echo "BIPON_SOURCE_DIR=$SRC_DIR"
    echo "BIPON_WEB_PORT=$WEB_PORT"
    echo "BIPON_DNS_PORT=$DNS_PORT"
    echo "BIPON_LOG_LEVEL=INFO"
    [[ -n "$MIGR_PW" ]] && echo "$MIGR_PW"
  } > "$ENV_FILE"
  chmod 600 "$ENV_FILE"
fi
if [[ -n "$PASSWORD" ]]; then
  sed -i '/^BIPON_PASSWORD=/d' "$ENV_FILE"; echo "BIPON_PASSWORD=$PASSWORD" >> "$ENV_FILE"
fi

cat > "$SERVICE" <<EOF
[Unit]
Description=Bipon Net — DNS filtrante, control parental, VPN y herramientas de red
Documentation=https://github.com/DXZ-dev/net-ping
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
EnvironmentFile=$ENV_FILE
WorkingDirectory=$SRC_DIR/backend
ExecStart=$APP_DIR/venv/bin/python -m bipon
Restart=always
RestartSec=3
# Se ejecuta como root: gestiona interfaces VPN, firewall, DHCP y el puerto 53.
LimitNOFILE=65535
ProtectHome=true
PrivateTmp=true
Nice=-5

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable bipon-net >/dev/null 2>&1
systemctl restart bipon-net
sleep 4
if systemctl is-active --quiet bipon-net; then
  IP=$(ip -4 route get 1.1.1.1 2>/dev/null | awk '{for(i=1;i<=NF;i++) if($i=="src"){print $(i+1); exit}}')
  echo
  c_ok "Bipon Net está en marcha"
  echo "   Panel web:    http://${IP:-IP-DEL-EQUIPO}:${WEB_PORT}   (HTTPS: https://${IP:-IP}:8443)"
  echo "   Servidor DNS: ${IP:-IP-DEL-EQUIPO} (puerto ${DNS_PORT})"
  echo "   Registro:     journalctl -u bipon-net -f"
  [[ $UPDATE -eq 0 ]] && echo && echo "Abre el panel: el asistente de bienvenida te guiará en la configuración."
else
  c_warn "El servicio no arrancó. Revisa: journalctl -u bipon-net -n 50"
  exit 1
fi
exit 0
