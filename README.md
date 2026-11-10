# NetPing

**El sistema de red doméstica todo en uno.** Filtrado DNS al estilo Pi-hole, control por aplicaciones y perfiles al estilo AdGuard Home, **Unbound** como resolutor recursivo, y además: optimización de latencia según tu ubicación, modo gaming y streaming, detección de CGNAT, VPN (WireGuard, OpenVPN y Tailscale), mapa de dispositivos, test de velocidad con bufferbloat y un **asistente que analiza tu red y aplica la configuración óptima**.

Funciona en **Docker**, en **Linux** (Debian/Ubuntu con systemd) y en **Proxmox** (contenedor LXC), con una interfaz web en español con efecto glass, modo claro u oscuro y diseño adaptado al móvil.

---

## Índice

1. [Qué incluye](#qué-incluye)
2. [Requisitos](#requisitos)
3. [Instalación con Docker](#instalación-con-docker)
4. [Instalación en Linux (Debian/Ubuntu)](#instalación-en-linux-debianubuntu)
5. [Instalación en Proxmox (LXC)](#instalación-en-proxmox-lxc)
6. [Primeros pasos: que toda la red use NetPing](#primeros-pasos-que-toda-la-red-use-netping)
7. [Guía de módulos](#guía-de-módulos)
8. [CGNAT, puertos cerrados y acceso remoto](#cgnat-puertos-cerrados-y-acceso-remoto)
9. [Gaming y streaming sin lag](#gaming-y-streaming-sin-lag)
10. [Seguridad](#seguridad)
11. [Arquitectura](#arquitectura)
12. [Solución de problemas](#solución-de-problemas)
13. [Desarrollo](#desarrollo)

---

## Qué incluye

| Área | Funciones |
|---|---|
| **Asistente** | Analiza dispositivos, latencia, DNS más rápido desde tu zona, proximidad a las CDN, NAT/CGNAT, MTU, hardware y bufferbloat. Da una **puntuación de salud** y recomendaciones que aplicas con un clic. |
| **DNS filtrante** | Servidor UDP/TCP propio con listas (hosts, dominios, Adblock `\|\|dominio^`, comodines), reglas para permitir y bloquear, regex, 0.0.0.0 o NXDOMAIN, registros DNS locales, reenvío condicional y PTR de los dispositivos. |
| **Latencia mínima** | Caché LRU con **caché optimista** (responde al instante aunque el dato haya caducado y lo refresca en segundo plano), **prefetch**, estrategia *más rápido / paralelo / en orden*, upstreams UDP, TCP, **DoT** y **DoH**, y un **optimizador** que mide los resolvers desde tu ubicación y elige el mejor (también de forma automática). |
| **Unbound** | Instancia propia gestionada desde la web: modo recursivo, reenvío cifrado (DoT) o reenvío simple; DNSSEC, minimización de QNAME, protección contra rebinding, serve-expired, cachés y número de hilos, estadísticas, registro y validación de la configuración antes de aplicarla. |
| **Control parental** | **Perfiles** (Niños, Adolescentes, Gamer, Invitados, IoT) con bloqueo de más de 40 apps (TikTok, Instagram, Fortnite, Roblox, YouTube…), SafeSearch forzado, YouTube restringido, filtro de contenido adulto, horarios que pueden cruzar la medianoche y pausa de Internet por dispositivo. Bloquea también VPN y DNS cifrado para que no se pueda saltar el filtro. |
| **Dispositivos** | Descubrimiento automático (ARP), fabricante (OUI), tipo de equipo, **mapa de red interactivo**, latencia, consultas, ping, escaneo de puertos (con enlace a sus paneles web) y Wake-on-LAN. |
| **Gaming y streaming** | Mantiene en caché los dominios de login, matchmaking, voz y CDN de Steam, Xbox, PSN, Riot, Epic, Discord, Netflix, YouTube, Twitch y otras plataformas. Protege esa infraestructura de las listas de bloqueo, mide el ping a regiones de servidores de juego y la proximidad de las CDN, y aplica QoS con **CAKE**. |
| **NAT y CGNAT** | Detección de CGNAT y doble NAT, tipo de NAT por STUN (abierto, moderado o estricto), UPnP-IGD (lista, abre y cierra puertos), MTU óptimo, IPv6 y soluciones según tu caso. |
| **VPN** | **WireGuard** (servidores con peers, QR, PSK y estadísticas, más túneles importados), **OpenVPN** (PKI propia, revocación, `.ovpn` y perfiles cliente) y **Tailscale** (subnet router, exit node, SSH, Headscale, netcheck). Incluye un generador de **VPS de relevo** para redes con CGNAT. |
| **Velocidad** | Test de Internet contra el nodo más cercano de Cloudflare (descarga, subida, ping, jitter) con **nota de bufferbloat de A+ a F**, test de la red local desde el navegador e historial. |
| **Herramientas** | Diagnóstico guiado, ping y traceroute en vivo, consultas DNS (con DNSSEC), benchmark de DNS, latencia global, escáner de puertos, HTTP/TLS (certificado y cabeceras), whois y Wake-on-LAN. |
| **Monitor** | Latencia y pérdida hacia el router, Internet y el DNS, medidas de forma continua, con gráficas, disponibilidad y aviso de cortes. |
| **Sistema** | Inicio de sesión protegido (scrypt y cookie HMAC), copia de seguridad y restauración, registro de eventos y API REST documentada en `/api/docs`. |

---

## Requisitos

- Un equipo encendido 24/7 en tu red: Raspberry Pi 4/5, mini PC, NAS, servidor Proxmox, etc. Con **512 MB de RAM** basta; se recomienda 1 GB.
- **IP fija** para ese equipo (resérvala en el DHCP del router).
- El **puerto 53** libre. Si tu sistema usa `systemd-resolved`, el instalador lo libera por ti (ver [Solución de problemas](#solución-de-problemas)).

---

## Instalación con Docker

```bash
git clone https://github.com/dxz-dev/net-ping.git
```

```bash
cd net-ping && cp .env.example .env
```

Edita `.env` (zona horaria `TZ`, y opcionalmente `NETPING_PASSWORD`) y arranca:

```bash
docker compose up -d --build
```

Abre `http://IP-DEL-SERVIDOR:8080`. Si no definiste contraseña, la web te pedirá crearla.

Notas:

- El contenedor usa la **red del host** (`network_mode: host`). Es necesario para ver los dispositivos, servir DNS en la IP real, Wake-on-LAN, UPnP y VPN.
- `cap_add: NET_ADMIN, NET_RAW` y `/dev/net/tun` son necesarios para la VPN y las herramientas de red.
- Si el puerto 53 está ocupado en el host (Ubuntu), libéralo primero:

  ```bash
  sudo ./deploy/linux/free-port-53.sh
  ```

- Los datos persisten en `./data`.
- Para actualizar:

  ```bash
  git pull && docker compose up -d --build
  ```

**Servicios opcionales:**

```bash
docker compose --profile cloudflared up -d
```

Publica paneles web con Cloudflare Tunnel, sin abrir puertos (sirve aunque tengas CGNAT). Requiere `CLOUDFLARE_TUNNEL_TOKEN` en `.env`.

```bash
docker compose --profile ddns up -d
```

DNS dinámico con DuckDNS. Requiere `DUCKDNS_SUBDOMAINS` y `DUCKDNS_TOKEN` en `.env`.

---

## Instalación en Linux (Debian/Ubuntu)

Para Debian 12+ y Ubuntu 22.04+, en máquina física, VM o LXC:

```bash
git clone https://github.com/dxz-dev/net-ping.git
```

```bash
cd net-ping && sudo ./deploy/linux/install.sh --tz America/Mexico_City
```

Opciones: `--password CLAVE`, `--web-port 8080`, `--dns-port 53`, `--with-tailscale` y `--no-vpn`.

El instalador:

1. Instala las dependencias: Python, Unbound, WireGuard, OpenVPN e iproute2.
2. Desactiva el servicio `unbound` del sistema (NetPing ejecuta el suyo en `127.0.0.1:5335`) y libera el puerto 53.
3. Copia la aplicación a `/opt/netping` con su entorno virtual. Los datos van a `/var/lib/netping` y la configuración a `/etc/netping.env`.
4. Crea y arranca el servicio `netping.service`.

Comandos útiles:

```bash
journalctl -u netping -f
```

```bash
sudo systemctl restart netping
```

Para actualizar, haz `git pull` y vuelve a ejecutar `install.sh`; los datos se conservan. Para desinstalar:

```bash
sudo ./deploy/linux/uninstall.sh
```

Añade `--purge` para borrar también los datos.

---

## Instalación en Proxmox (LXC)

En la **shell del host Proxmox**:

```bash
git clone https://github.com/dxz-dev/net-ping.git
```

Con IP fija (recomendado para un servidor DNS):

```bash
cd net-ping && IP=192.168.1.53/24 GW=192.168.1.1 TZ=America/Mexico_City bash deploy/proxmox/create-lxc.sh
```

O con DHCP:

```bash
cd net-ping && bash deploy/proxmox/create-lxc.sh
```

El script descarga la plantilla Debian 12, crea un **LXC no privilegiado** (2 CPU, 1 GB de RAM y 8 GB de disco por defecto), habilita **TUN** para la VPN, copia NetPing dentro y ejecuta el instalador de Linux.

Variables disponibles: `CTID`, `CT_NAME`, `STORAGE`, `BRIDGE`, `VLAN`, `CORES`, `MEMORY`, `DISK`, `PASSWORD` y `TAILSCALE=1`.

> Si lo prefieres, crea tú el LXC Debian 12 y ejecuta dentro el instalador de Linux. Para la VPN añade al `/etc/pve/lxc/<id>.conf`:
>
> ```
> lxc.cgroup2.devices.allow: c 10:200 rwm
> lxc.mount.entry: /dev/net/tun dev/net/tun none bind,create=file
> ```

---

## Primeros pasos: que toda la red use NetPing

1. **Router → DHCP → servidor DNS = IP de NetPing.** No pongas un DNS secundario externo como 8.8.8.8: los dispositivos lo usarían a veces y se colarían anuncios.
   - Si tu router no permite cambiar el DNS del DHCP, configura el DNS a mano en cada dispositivo, o desactiva el DHCP del router y usa uno en otro equipo.
2. Reconecta los dispositivos (o espera a que renueven la IP) para que tomen el nuevo DNS.
3. Abre **Asistente → Analizar mi red** y aplica las recomendaciones.
4. En **Dispositivos**, pon nombre a los equipos y asígnales un **perfil** (por ejemplo, «Niños» para las tablets de los niños).
5. Si tienes consolas o juegas en PC, revisa **Gaming y streaming**.

---

## Guía de módulos

- **Panel:** consultas, porcentaje bloqueado, tiempo de respuesta, latencia de Internet, dominios y dispositivos más activos, estado del sistema y eventos. Desde aquí permites o bloqueas un dominio con un clic.
- **Consultas:** registro en vivo con filtros por dispositivo, estado y dominio. Cada fila tiene un botón «Permitir siempre» o «Bloquear siempre».
- **Bloqueo:**
  - *Listas:* activa, desactiva y actualiza listas, o añade una recomendada. Cada lista tiene una categoría (anuncios, seguridad, adultos o personalizada) y cada perfil decide qué categorías aplica.
  - *Reglas propias:* permitir, bloquear o regex.
  - *Aplicaciones:* bloqueo global de apps.
  - *Comprobar dominio:* explica por qué un dominio se bloquea o no.
- **Control parental:** perfiles con plantillas, apps y horarios. Pausar el bloqueo de anuncios **no** desactiva el control parental.
- **DNS y latencia:** catálogo de resolvers (rápidos, seguridad, familia y cifrados), servidores propios, estrategia, optimizador, caché, registros locales y reenvío condicional.
- **Unbound:** modo, seguridad, rendimiento, configuración avanzada, acciones y estadísticas.

---

## CGNAT, puertos cerrados y acceso remoto

Muchos proveedores, sobre todo de fibra y móvil, usan **CGNAT**: tu router no tiene una IP pública propia, así que **no puedes abrir puertos**. Ve a **NAT y CGNAT → Analizar**. NetPing:

- Compara la IP WAN del router (vía UPnP) con tu IP pública.
- Busca saltos en `100.64.0.0/10` dentro del traceroute.
- Mide el tipo de NAT con STUN.
- Te dice si tienes **IP pública, CGNAT o doble NAT**.

Soluciones integradas, ordenadas según tu caso:

| Situación | Solución recomendada |
|---|---|
| CGNAT y quieres entrar a tu red | **Tailscale** (módulo VPN): no necesita abrir puertos y hace conexión directa P2P cuando es posible. Activa «Anunciar mi LAN» y «Exit node». |
| CGNAT y quieres publicar un servicio web | **Cloudflare Tunnel** (`--profile cloudflared`). |
| CGNAT y necesitas puertos (servidor de juego, NAT abierto) | **VPS de relevo** (VPN → VPS de relevo): genera las configuraciones de WireGuard y las reglas DNAT. Elige un VPS cercano. |
| Tienes IPv6 | Usa IPv6 para el acceso entrante. |
| Doble NAT | Pon el módem del ISP en modo bridge o en DMZ. |
| Tienes IP pública | **Servidor WireGuard** (VPN → WireGuard), con el puerto abierto vía UPnP desde NetPing. |
| Definitiva | Pide a tu ISP salir de CGNAT o una IP pública. |

Con cualquier VPN, pon la **IP de NetPing como DNS de los clientes** para tener bloqueo de anuncios y control parental también fuera de casa.

---

## Gaming y streaming sin lag

- **DNS al instante:** los dominios de login, matchmaking, voz y CDN se resuelven por adelantado y se renuevan antes de caducar, así que nunca esperas al DNS al buscar partida o al empezar un vídeo.
- **Nada se rompe:** la infraestructura de las plataformas está protegida frente a listas agresivas. El control parental por perfil sí puede bloquearla.
- **CDN más cercana:** Gaming → *Proximidad de CDN* comprueba qué DNS te envía al servidor de Netflix o YouTube más cercano, y lo puedes aplicar con un clic. El asistente lo tiene en cuenta.
- **Región de juego:** mide el ping a cada región para elegir la de menor latencia en el juego.
- **Bufferbloat:** si el test de velocidad da C, D o F, el ping se dispara cuando alguien descarga. La solución es SQM/QoS (CAKE) al 90-95 % de tu velocidad, en el router o en NetPing si actúa como gateway.
- **Perfil «Gamer»** para consolas y PC: sin filtros que interfieran.
- **NAT estricto:** activa UPnP y una IP fija para la consola. Con CGNAT, mira la sección anterior.

> Ningún software puede bajar el ping por debajo del mínimo físico que marcan la distancia y tu ISP. NetPing elimina todo lo que **añade** latencia: esperas de DNS, CDN lejanas, bufferbloat y NAT estricto.

---

## Seguridad

- El panel exige contraseña (hash scrypt) y sesiones firmadas con HMAC, con limitación de intentos fallidos. Cambiar la contraseña cierra todas las sesiones.
- **No expongas el puerto 8080 ni el 53 a Internet.** Para acceder desde fuera usa la VPN o Cloudflare Tunnel con Zero Trust.
- NetPing se ejecuta como root (Linux) o con `NET_ADMIN` (Docker) porque gestiona interfaces VPN, iptables y el puerto 53.
- Las configuraciones importadas de WireGuard y OpenVPN rechazan scripts (`PostUp`, `up`, `script-security`…).
- Las claves privadas VPN se guardan con permisos 600 en el directorio de datos. Haz tu copia de ese directorio aparte: la copia de la web **no** incluye claves.

---

## Arquitectura

```
┌───────────────────────────── NetPing (un proceso Python, asyncio) ─────────────────────────────┐
│                                                                                                │
│  Interfaz web (SPA, :8080) ──► API REST (FastAPI) ──► Contexto                                 │
│                                                          │                                     │
│  Clientes LAN ──DNS :53──► Servidor DNS ──► Política (perfil, horario, pausa del dispositivo)  │
│                              │                   │                                             │
│                              │            Motor de filtrado (listas por categoría, reglas,     │
│                              │            regex, apps, SafeSearch, dominios protegidos)        │
│                              ▼                                                                 │
│                        Caché LRU (optimista + prefetch + precalentado gaming)                  │
│                              ▼                                                                 │
│                        Pool de upstreams (fastest/parallel/failover · UDP/TCP/DoT/DoH)         │
│                              ▼                                                                 │
│                        Unbound (127.0.0.1:5335, gestionado) ──► servidores raíz / DoT          │
│                                                                                                │
│  Tareas: listas · escaneo ARP · monitor · optimizador · precalentado · VPN (wg/openvpn/ts)     │
│  Datos: /data (settings.json, netping.db SQLite WAL, listas, unbound, vpn)                     │
└────────────────────────────────────────────────────────────────────────────────────────────────┘
```

- `backend/netping/dns/`: servidor DNS, caché, motor de filtrado y clientes upstream.
- `backend/netping/advisor.py`: el asistente. `gaming.py`, `nat.py`, `vpn.py`, `unbound.py`, `devices.py`, `tools.py`, `speedtest.py` y `monitor.py` contienen el resto de módulos.
- `frontend/`: SPA sin dependencias ni paso de compilación (módulos ES y CSS propio).

---

## Solución de problemas

| Problema | Solución |
|---|---|
| «No se pudo abrir el puerto DNS 53» | Otro servicio lo usa. `sudo ss -lunp \| grep :53` te dice cuál. Ubuntu: `sudo ./deploy/linux/free-port-53.sh`. Detén Pi-hole, AdGuard Home o dnsmasq si los tenías. |
| No aparecen consultas | Los dispositivos no usan NetPing. Revisa el DNS del DHCP del router y renueva la IP del dispositivo. |
| Se cuelan anuncios | Hay un DNS secundario externo en el router, o el navegador usa DNS cifrado. Activa «Evitar DNS cifrado del navegador» y bloquea la app «DNS cifrado externo» en el perfil. |
| Una web o app no funciona | Consultas → busca el dominio en rojo → «Permitir siempre», o Bloqueo → *Comprobar dominio*. |
| No se detectan dispositivos | En Docker hace falta `network_mode: host`. En Proxmox, el LXC debe estar en el mismo puente o VLAN que tu LAN. |
| La VPN no arranca | Faltan `/dev/net/tun` o `NET_ADMIN`. En Proxmox, revisa las líneas `lxc.*` del contenedor. |
| Los horarios no coinciden | Ajusta `TZ` (Docker) o `--tz` (Linux). |
| Unbound en «detenido» | Unbound → Registro. Prueba el modo «Reenvío cifrado» si tu red bloquea la recursión. |

---

## Desarrollo

```bash
cd backend && python3 -m venv .venv && . .venv/bin/activate
```

```bash
pip install -r requirements.txt pytest
```

```bash
python -m pytest -q tests
```

```bash
NETPING_DATA_DIR=./data NETPING_DNS_PORT=5353 NETPING_WEB_PORT=8080 python -m netping
```

Para probar el DNS en ese puerto:

```bash
dig @127.0.0.1 -p 5353 doubleclick.net
```

API interactiva: `http://localhost:8080/api/docs`.

**Licencia:** MIT.
