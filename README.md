<div align="center">

<img src="frontend/icon-192.png" width="96" alt="Bipon Net">

# Bipon Net

**Tu red, bajo control.** · *Your network, under control.*

Filtrado DNS al estilo **Pi-hole**, control por aplicaciones y perfiles al estilo **AdGuard Home**, y **Unbound** como resolutor recursivo propio — todo en un solo sistema, con control parental por dispositivo, VPN, modo gaming, router/firewall, portal cautivo y un asistente que analiza tu red y la configura por ti.

![Versión](https://img.shields.io/badge/versión-1.0.0-0ea5e9?style=for-the-badge)
![Licencia](https://img.shields.io/badge/licencia-MIT-22c55e?style=for-the-badge)
![Python](https://img.shields.io/badge/Python-3.11+-3776AB?style=for-the-badge&logo=python&logoColor=white)
![Docker](https://img.shields.io/badge/Docker-listo-2496ED?style=for-the-badge&logo=docker&logoColor=white)
![Proxmox](https://img.shields.io/badge/Proxmox-LXC-E57000?style=for-the-badge&logo=proxmox&logoColor=white)
![Idiomas](https://img.shields.io/badge/idiomas-ES%20%7C%20EN-a855f7?style=for-the-badge)

[Instalación](#-instalación) · [Requisitos mínimos](#-requisitos-mínimos) · [Funciones](#-funciones) · [Primeros pasos](#-primeros-pasos) · [CGNAT y acceso remoto](#-cgnat-puertos-cerrados-y-acceso-remoto) · [Solución de problemas](#-solución-de-problemas) · [English](#-english)

</div>

---

## ✨ Por qué Bipon Net

| | Pi-hole | AdGuard Home | **Bipon Net** |
|---|:-:|:-:|:-:|
| Bloqueo de anuncios por listas | ✅ | ✅ | ✅ |
| Bloqueo de apps (TikTok, Fortnite…) con iconos | — | ✅ | ✅ |
| Perfiles de control parental con horarios y tiempo de pantalla | — | parcial | ✅ |
| Unbound recursivo gestionado desde la web | manual | — | ✅ |
| DNS más rápido según tu ubicación + caché optimista | — | parcial | ✅ |
| Dispositivos en tiempo real, por marca y con jerarquía Proxmox | parcial | parcial | ✅ |
| Límite de velocidad por dispositivo | — | — | ✅ |
| VPN (WireGuard, OpenVPN, Tailscale) | — | — | ✅ |
| Detección de CGNAT / tipo de NAT y soluciones | — | — | ✅ |
| Modo gaming y streaming | — | — | ✅ |
| Portal cautivo con términos y condiciones | — | — | ✅ |
| Asistente que analiza la red y aplica la configuración | — | — | ✅ |
| Vigilante de lag con culpable y «Proteger mi partida» | — | — | ✅ |
| Bipon como puerta de enlace con un solo cable (sin tocar el router del proveedor) | — | — | ✅ |
| Rastreadores camuflados (CNAME) y avisos «esto rompió una app» | — | ✅ / — | ✅ |
| Autoridad certificadora local (HTTPS sin avisos en tu red) | — | — | ✅ |

---

## 🧩 Funciones

<table>
<tr><td width="50%" valign="top">

### 🛡️ Protección
- **DNS filtrante** UDP/TCP, IPv4/IPv6, **DoT (853)** y **DoH** (`/dns-query`).
- Listas por categoría (anuncios, seguridad, adultos, personalizadas) — **todas desactivadas al inicio** salvo que las elijas en el registro.
- Reglas propias, regex, bloqueo de **TLD**, bloqueo por **país** (GeoIP), protección contra **DNS rebinding**.
- **Bloqueo de aplicaciones** con iconos oficiales: redes sociales, mensajería, videollamadas, streaming, juegos, apuestas, IA, compras…
- **Rastreadores camuflados (CNAME cloaking)** y sugerencias de dominios bloqueados que rompen una app.
- **Detección de anomalías** (malware DGA, túneles DNS, ráfagas, IoT comprometido) con **cuarentena automática**.
- **Equipos que se saltan Bipon** (DNS puesto a mano o DNS cifrado externo), **intrusos** con IP fuera de tu red y **auditoría de puertos abiertos por UPnP**.
- **Autoridad certificadora local**: HTTPS de confianza para el panel, Proxmox, NAS… también por IP.

</td><td valign="top">

### 👨‍👩‍👧 Familia
- **Perfiles** (Niños, Adolescentes, Gamer, Trabajo, Invitados, IoT) con SafeSearch, YouTube restringido y horarios.
- **Tiempo de pantalla** diario por perfil y **pausa familiar**.
- **Control de Internet inmediato**: pausar un dispositivo corta en segundos (y al instante en modo router).
- **Actividad por dispositivo**: qué apps y webs usa.
- **Controlador de velocidad** por dispositivo (Proxmox, MikroTik, OpenWrt o Bipon como puerta de enlace).
- **Encender/apagar** equipos: Wake-on-LAN, enchufes de Home Assistant, Shelly o SSH.

</td></tr>
<tr><td valign="top">

### ⚡ Rendimiento
- **Optimizador**: mide los DNS desde tu ubicación y usa el más rápido (con proximidad de CDN).
- Caché LRU **optimista** + prefetch + precalentado de dominios de juegos.
- **Unbound** propio: recursivo, DoT o reenvío; DNSSEC, QNAME minimisation, serve-expired.
- **Modo gaming y streaming**: cero espera de DNS, infraestructura protegida, ping a regiones.
- **Unbound autoconfigurado** con los DNS más cercanos a tu ubicación y a tu hardware; caché persistente (< 1 ms tras reiniciar) y precalentada con los dominios de tu casa.
- **Vigilante de lag**: ping en vivo, picos con su posible culpable, **«Proteger mi partida»** y **preferencia IPv4**.
- **Calidad de conexión**: Wi-Fi débil por equipo, mapa de lag por hora, ruta a tu juego e **informe para reclamar al proveedor**.
- **Test de velocidad** con nota de **bufferbloat** (A+ a F).

</td><td valign="top">

### 🌐 Red
- **Dispositivos en tiempo real** (sin escanear a mano), fabricante y marca, mapa jerárquico **Proxmox → VMs/LXC**.
- **Modo automático** (IPv6 RDNSS): Bipon sin tocar el router; si se apaga, la red sigue con el DNS del router.
- **Servidor DHCP** (único o **principal con el del router de respaldo**), zonas/VLAN, **router y firewall** (nftables) con reversión automática.
- **Asistente «Bipon como puerta de enlace»**: control total de cable y Wi-Fi con un solo cable, sin tocar el router del proveedor.
- **Acceso al router** (OpenWrt, MikroTik, FRITZ!Box) sin tocar la configuración del ISP.
- **NAT**: tipo de NAT (RFC 5780 / consola), estados y sesiones; UPnP.
- **Puertos TCP/UDP** en uso y qué aplicación los usa.

</td></tr>
<tr><td valign="top">

### 🔐 VPN y acceso
- **WireGuard** (QR, PSK), **OpenVPN** (PKI, revocación), **Tailscale** (subnet router, exit node, SSH, Headscale) con **instalación en un clic** de la última versión.
- **VPS de relevo** para CGNAT.
- **Acceso local / externo**: elige desde dónde se abre el panel y quién puede usar el DNS.
- **Portal cautivo** con términos y condiciones personalizables.

</td><td valign="top">

### 🧰 Sistema
- Usuarios con **roles** (admin, padres, solo lectura), **2FA (TOTP)**, tokens de API, auditoría.
- **Notificaciones**: Telegram (con botones **Permitir / Bloquear**), Discord, Slack, ntfy, Gotify, email, webhook y **push**.
- **Copias de seguridad automáticas** con retención y restauración en un clic; **registros** del sistema; **buscador** (Ctrl+K).
- **Informes** semanales, **alta disponibilidad** (dos Bipon), Home Assistant, Proxmox.
- **Perfiles de configuración** para guardar y alternar escenarios.
- **Actualizaciones** desde GitHub con un botón (o automáticas).
- Interfaz en **español e inglés**, **9 temas** (glass, shadcn Zinc, Aurora, Neón, Nord…), panel editable y **guía interactiva**.

</td></tr>
</table>

---

## 📋 Requisitos mínimos

| | Mínimo | Recomendado |
|---|---|---|
| **CPU** | 1 núcleo (ARM64 o x86-64) | 2 núcleos |
| **RAM** | 512 MB | 1 GB (2 GB con muchas listas o modo router) |
| **Disco** | 2 GB libres | 8 GB (historial y estadísticas) |
| **Sistema** | Debian 12, Ubuntu 22.04, Raspberry Pi OS 12 (64 bits) · Docker 24+ · Proxmox VE 7/8 | Debian 12 / Ubuntu 24.04 |
| **Red** | IP fija en la LAN y **puerto 53 libre** | Cable Ethernet |
| **Python** (instalación nativa) | 3.11 | 3.11 o superior |

Ejemplos que funcionan bien: Raspberry Pi 4/5, mini PC Intel N100, NAS con Docker, LXC de Proxmox (2 CPU / 1 GB / 8 GB).

> **Opcional según la función:** `/dev/net/tun` para VPN (LXC: se puede activar desde la propia web), dos interfaces o VLAN para el modo router, HTTPS con certificado válido para notificaciones push.

---

## 🚀 Instalación

### 🐳 Docker

```bash
git clone https://github.com/DXZ-dev/net-ping.git bipon-net
```

```bash
cd bipon-net && cp .env.example .env
```

```bash
docker compose up -d --build
```

Abre `http://IP-DEL-EQUIPO:8080` y sigue el asistente de bienvenida. Los datos quedan en `./data`.

Servicios opcionales: `docker compose --profile cloudflared up -d` (Cloudflare Tunnel) y `docker compose --profile ddns up -d` (DuckDNS).

### 🐧 Linux (Debian / Ubuntu)

```bash
sudo apt install -y git
```

```bash
git clone https://github.com/DXZ-dev/net-ping.git bipon-net
```

```bash
cd bipon-net && sudo bash deploy/linux/install.sh --tz America/Santo_Domingo
```

Opciones: `--password CLAVE`, `--web-port 8080`, `--dns-port 53`, `--with-tailscale`, `--no-vpn`.

| Ruta | Contenido |
|---|---|
| `/opt/bipon-net` | aplicación y entorno Python |
| `/var/lib/bipon-net` | datos (configuración, base de datos, listas, VPN) |
| `/etc/bipon-net.env` | variables de entorno |
| `bipon-net.service` | servicio systemd |

```bash
journalctl -u bipon-net -f
```

Desinstalar: `sudo bash deploy/linux/uninstall.sh` (añade `--purge` para borrar los datos).

### 🟧 Proxmox (LXC)

En la **shell del host Proxmox**:

```bash
apt install -y git && git clone https://github.com/DXZ-dev/net-ping.git bipon-net
```

```bash
cd bipon-net && IP=192.168.1.53/24 GW=192.168.1.1 TZ=America/Santo_Domingo bash deploy/proxmox/create-lxc.sh
```

Crea un LXC Debian 12 no privilegiado (2 CPU, 1 GB, 8 GB), habilita **TUN** para la VPN e instala Bipon Net dentro. Variables: `CTID`, `CT_NAME`, `STORAGE`, `BRIDGE`, `VLAN`, `CORES`, `MEMORY`, `DISK`, `PASSWORD`, `TAILSCALE=1`.

> ¿Ya tenías el LXC sin TUN y la web muestra «Permisos de red limitados»? Conecta Proxmox en **Integraciones** y pulsa **Activar TUN**, o añade al `/etc/pve/lxc/<id>.conf`:
> ```
> lxc.cgroup2.devices.allow: c 10:200 rwm
> lxc.mount.entry: /dev/net/tun dev/net/tun none bind,create=file
> ```

### 🔄 Actualizar

- **Desde la web:** *Ajustes → Actualizaciones → Buscar actualizaciones* (o activa «¿Deseas recibir actualizaciones automáticas?»).
- **Linux/Proxmox a mano:** `sudo bash /opt/bipon-net/src/deploy/linux/update.sh`
- **Docker:** `git pull && docker compose up -d --build`

> **¿Venías de NetPing?** El instalador migra solo el servicio, los datos (`/var/lib/netping` → `/var/lib/bipon-net`) y la contraseña. Las variables `NETPING_*` siguen funcionando.

---

## 🧭 Primeros pasos

1. **Registro:** elige país y cómo configurar: **Autoconfigurar** (mide tu red y los DNS más cercanos y aplica lo mejor), **Recomendado** o **Manual**. Es un solo sistema con todas las funciones.
2. **Que la red use Bipon:** en el router, *DHCP → servidor DNS = IP de Bipon* (sin DNS secundario externo). Si tu router no lo permite, activa el **modo automático** o el **servidor DHCP** en *Red*.
3. **Asistente → Analizar mi red** y aplica las recomendaciones.
4. **Dispositivos:** ponles nombre y asígnales un **perfil**.
5. Activa la **guía del sistema** desde *Ajustes → Apariencia* cuando quieras repasarla.

### 🎮 Control total sin tocar el router del proveedor

Para limitar la velocidad, pausar Internet o cortar intrusos de **todos** los equipos (cable y Wi-Fi), su tráfico tiene que pasar por Bipon.
El asistente **Router y firewall → Bipon como puerta de enlace** lo hace con **un solo cable**:

```
Internet ── router del proveedor (módem + Wi-Fi + switch, sin cambios)
                 │
    tus equipos ─┴─ Bipon (puerta de enlace que reparte su DHCP)
```

1. Comprueba que todo esté listo (IP fija de Bipon, nftables, reenvío…).
2. Activa el reenvío con **reversión automática a los 60 s** si no confirmas.
3. Activa el DHCP de Bipon como **principal** (el del router queda de **respaldo**: si Bipon se apaga, los equipos vuelven solos al router en ≤ 30 min).

Para que los juegos tengan **NAT abierto**, pon la IP de Bipon en la **DMZ** del router y crea la redirección de puertos de la consola en Bipon.

---

## 🌍 CGNAT, puertos cerrados y acceso remoto

*NAT, CGNAT y acceso remoto → Analizar* compara la IP WAN con la pública, busca saltos `100.64.0.0/10`, mide el tipo de NAT (abierto / moderado / estricto) y muestra las sesiones NAT activas.

| Situación | Solución integrada |
|---|---|
| CGNAT y quieres entrar a tu red | **Tailscale** (sin abrir puertos, P2P cuando es posible) |
| CGNAT y necesitas puertos (juegos, NAT abierto) | **VPS de relevo** con WireGuard + DNAT |
| Publicar una web | **Cloudflare Tunnel** |
| Tienes IP pública | **Servidor WireGuard** + UPnP |
| Doble NAT | Módem del ISP en bridge o DMZ |

---

## 🔒 Seguridad

- Contraseñas con **scrypt**, sesiones HMAC, **2FA**, límite de intentos y registro de auditoría.
- Por defecto el panel y el DNS sólo responden a la **red local + VPN** (*Usuarios y acceso → Acceso local/externo*). **No expongas los puertos 8080 ni 53 a Internet.**
- Las configuraciones VPN importadas rechazan scripts (`PostUp`, `up`…); las claves privadas se guardan con permisos 600.
- El modo router aplica cambios con **reversión automática a los 60 s** si no los confirmas.

---

## 🛠️ Solución de problemas

| Problema | Solución |
|---|---|
| «No se pudo abrir el puerto 53» | `sudo ss -lunp \| grep :53`; en Ubuntu: `sudo bash deploy/linux/free-port-53.sh`. Detén Pi-hole/AdGuard/dnsmasq. |
| No aparecen consultas | Los equipos no usan Bipon: revisa el DNS del DHCP o activa el modo automático. |
| Se cuelan anuncios | DNS secundario externo en el router o DNS cifrado del navegador: activa «Evitar DNS cifrado del navegador». |
| No se ven dispositivos | Docker necesita `network_mode: host`; en Proxmox el LXC debe estar en el mismo puente/VLAN. |
| «Permisos de red limitados» | Falta `/dev/net/tun`: *VPN → Activar TUN* (vía Proxmox) o las líneas `lxc.*` de arriba. |
| Algo no funciona y no sabes qué | *Ajustes → Registros*: eventos, registro de la aplicación y de Unbound, con filtros. |
| Una web o app dejó de funcionar | *Bloqueo* muestra «¿Algo dejó de funcionar?» con los dominios bloqueados que se están reintentando. |
| Los horarios no cuadran | Ajusta la zona horaria en *Ajustes* o `TZ`. |

---

## 🧑‍💻 Desarrollo

```bash
cd backend && python3 -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt pytest
```

```bash
python -m pytest -q tests
```

```bash
BIPON_DATA_DIR=./data BIPON_DNS_PORT=5353 BIPON_WEB_PORT=8080 BIPON_FRONTEND_DIR=../frontend python -m bipon
```

Backend: Python 3.11 + FastAPI + asyncio (`backend/bipon/`). Frontend: SPA sin compilación (módulos ES, `frontend/`). API interactiva en `/api/docs`.

---

## 🇬🇧 English

**Bipon Net** is an all-in-one home and small-business network system: Pi-hole-style DNS filtering, AdGuard-style per-app blocking and parental profiles, a managed **Unbound** recursive resolver, real-time device inventory (with Proxmox hierarchy and brand detection), per-device speed limits, WireGuard / OpenVPN / Tailscale, CGNAT and NAT-type detection, gaming & streaming mode, captive portal, router/firewall mode, notifications, reports, 2FA and roles. The web UI is available in **English and Spanish** (switch it in the top bar or in *Settings*).

**Minimum requirements:** 1 CPU core, 512 MB RAM (1 GB recommended), 2 GB disk, Debian 12 / Ubuntu 22.04+ / Docker 24+ / Proxmox VE 7+, a static LAN IP and free port 53.

**Quick install:** Docker → `docker compose up -d --build`; Linux → `sudo bash deploy/linux/install.sh`; Proxmox host → `bash deploy/proxmox/create-lxc.sh`. Then open `http://DEVICE-IP:8080` and follow the setup wizard.

---

<div align="center">

**Licencia / License:** [MIT](LICENSE) · Iconos de marcas: [Simple Icons](https://simpleicons.org) (CC0)

</div>
