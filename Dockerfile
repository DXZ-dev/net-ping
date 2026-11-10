# Bipon Net — imagen todo-en-uno: DNS filtrante + Unbound + VPN (WireGuard, OpenVPN, Tailscale)
FROM python:3.12-slim-bookworm

ARG WITH_TAILSCALE=1
ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    BIPON_DATA_DIR=/data \
    BIPON_FRONTEND_DIR=/app/frontend \
    BIPON_WEB_PORT=8080

RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      unbound unbound-anchor dns-root-data \
      iputils-ping traceroute whois iproute2 iptables nftables conntrack \
      wireguard-tools openvpn \
      ca-certificates curl gnupg tzdata procps \
 && if [ "$WITH_TAILSCALE" = "1" ]; then \
      curl -fsSL https://pkgs.tailscale.com/stable/debian/bookworm.noarmor.gpg -o /usr/share/keyrings/tailscale-archive-keyring.gpg \
      && curl -fsSL https://pkgs.tailscale.com/stable/debian/bookworm.tailscale-keyring.list -o /etc/apt/sources.list.d/tailscale.list \
      && apt-get update && apt-get install -y --no-install-recommends tailscale; \
    fi \
 && apt-get purge -y gnupg && apt-get autoremove -y \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY backend/requirements.txt /app/backend/requirements.txt
RUN pip install -r /app/backend/requirements.txt

COPY backend /app/backend
COPY frontend /app/frontend

WORKDIR /app/backend
VOLUME ["/data"]
EXPOSE 53/udp 53/tcp 8080/tcp

HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
  CMD python -c "import os,urllib.request;urllib.request.urlopen('http://127.0.0.1:%s/api/auth/status' % os.environ.get('BIPON_WEB_PORT','8080'), timeout=4)" || exit 1

CMD ["python", "-m", "bipon"]
