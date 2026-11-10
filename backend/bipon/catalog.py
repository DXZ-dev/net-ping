"""Catálogo de aplicaciones/servicios bloqueables y listas de bloqueo recomendadas.

Cada servicio se bloquea por dominio (incluyendo todos sus subdominios)."""
from __future__ import annotations

SERVICES: list[dict] = [
    # --- Redes sociales ---
    {"id": "facebook", "name": "Facebook", "category": "Redes sociales", "domains": [
        "facebook.com", "facebook.net", "fbcdn.net", "fbsbx.com", "fb.com", "fb.me", "facebook.de",
        "fbcdn.com", "messenger.com", "m.me", "facebookcorewwwi.onion"]},
    {"id": "instagram", "name": "Instagram", "category": "Redes sociales", "domains": [
        "instagram.com", "cdninstagram.com", "ig.me", "instagr.am", "instagram.net"]},
    {"id": "tiktok", "name": "TikTok", "category": "Redes sociales", "domains": [
        "tiktok.com", "tiktokv.com", "tiktokcdn.com", "tiktokcdn-us.com", "tiktokv.us", "tiktokw.us",
        "byteoversea.com", "ibytedtos.com", "ibyteimg.com", "ttwstatic.com", "musical.ly", "muscdn.com",
        "tiktok-row.net", "tik-tokapi.com", "bytedapm.com", "byteimg.com", "isnssdk.com", "ttlivecdn.com"]},
    {"id": "snapchat", "name": "Snapchat", "category": "Redes sociales", "domains": [
        "snapchat.com", "snap.com", "sc-cdn.net", "snapads.com", "snapkit.com", "snap-dev.net",
        "feelinsonice-hrd.appspot.com", "bitmoji.com", "sc-static.net", "snapcdn.io"]},
    {"id": "twitter", "name": "X / Twitter", "category": "Redes sociales", "domains": [
        "twitter.com", "x.com", "twimg.com", "t.co", "twttr.com", "twitter.co", "twitterinc.com", "pscp.tv"]},
    {"id": "reddit", "name": "Reddit", "category": "Redes sociales", "domains": [
        "reddit.com", "redd.it", "redditmedia.com", "redditstatic.com", "reddituploads.com", "redditspace.com"]},
    {"id": "pinterest", "name": "Pinterest", "category": "Redes sociales", "domains": [
        "pinterest.com", "pinimg.com", "pin.it", "pinterest.es", "pinterest.co.uk", "pinterest.com.mx"]},
    {"id": "linkedin", "name": "LinkedIn", "category": "Redes sociales", "domains": [
        "linkedin.com", "licdn.com", "lnkd.in"]},
    {"id": "threads", "name": "Threads", "category": "Redes sociales", "domains": ["threads.net", "threads.com"]},
    {"id": "tumblr", "name": "Tumblr", "category": "Redes sociales", "domains": ["tumblr.com"]},
    {"id": "bereal", "name": "BeReal", "category": "Redes sociales", "domains": ["bereal.com", "bere.al", "bereal.team"]},
    # --- Mensajería ---
    {"id": "whatsapp", "name": "WhatsApp", "category": "Mensajería", "domains": [
        "whatsapp.com", "whatsapp.net", "wa.me", "whatsapp.biz", "wl.co"]},
    {"id": "telegram", "name": "Telegram", "category": "Mensajería", "domains": [
        "telegram.org", "telegram.me", "t.me", "telegra.ph", "telesco.pe", "tdesktop.com", "telegram.dog"]},
    {"id": "discord", "name": "Discord", "category": "Mensajería", "domains": [
        "discord.com", "discord.gg", "discordapp.com", "discordapp.net", "discord.media", "discordcdn.com",
        "discord.dev", "discord.new", "discord.gift", "discordstatus.com", "dis.gd"]},
    {"id": "signal", "name": "Signal", "category": "Mensajería", "domains": [
        "signal.org", "whispersystems.org", "signal.art", "signal.me"]},
    {"id": "skype", "name": "Skype", "category": "Mensajería", "domains": ["skype.com", "skypeassets.com"]},
    {"id": "wechat", "name": "WeChat", "category": "Mensajería", "domains": ["wechat.com", "weixin.qq.com", "wx.qq.com"]},
    {"id": "messenger", "name": "Messenger", "category": "Mensajería", "domains": [
        "messenger.com", "m.me", "msngr.com", "fbmessenger.com"]},
    {"id": "line", "name": "LINE", "category": "Mensajería", "domains": ["line.me", "line-scdn.net", "line-apps.com", "naver.jp", "lin.ee"]},
    {"id": "viber", "name": "Viber", "category": "Mensajería", "domains": ["viber.com", "cdn.viber.com", "viber.io"]},
    {"id": "kakaotalk", "name": "KakaoTalk", "category": "Mensajería", "domains": ["kakao.com", "kakaocdn.net", "kakaotalk.com"]},
    {"id": "zoom", "name": "Zoom", "category": "Videollamadas", "domains": ["zoom.us", "zoom.com", "zoomgov.com", "zoomcdn.com"]},
    {"id": "teams", "name": "Microsoft Teams", "category": "Videollamadas", "domains": [
        "teams.microsoft.com", "teams.live.com", "teams.cdn.office.net", "skype.com", "lync.com"]},
    {"id": "meet", "name": "Google Meet", "category": "Videollamadas", "domains": ["meet.google.com", "meet.googleapis.com"]},
    {"id": "slack", "name": "Slack", "category": "Videollamadas", "domains": ["slack.com", "slack-edge.com", "slack-msgs.com", "slack-imgs.com"]},
    {"id": "bluesky", "name": "Bluesky", "category": "Redes sociales", "domains": ["bsky.app", "bsky.social", "bsky.network"]},
    {"id": "kwai", "name": "Kwai", "category": "Redes sociales", "domains": ["kwai.com", "kwai.net", "kwaicdn.com", "kuaishou.com"]},
    {"id": "likee", "name": "Likee", "category": "Redes sociales", "domains": ["likee.video", "like-video.com", "likeevideo.com"]},
    {"id": "vk", "name": "VK", "category": "Redes sociales", "domains": ["vk.com", "vk.me", "userapi.com", "vkuser.net"]},
    {"id": "weibo", "name": "Weibo", "category": "Redes sociales", "domains": ["weibo.com", "weibo.cn", "sinaimg.cn"]},
    # --- Vídeo y streaming ---
    {"id": "youtubekids", "name": "YouTube Kids", "category": "Vídeo y streaming", "domains": ["youtubekids.com"]},
    {"id": "youtubemusic", "name": "YouTube Music", "category": "Vídeo y streaming", "domains": ["music.youtube.com"]},
    {"id": "youtube", "name": "YouTube", "category": "Vídeo y streaming", "domains": [
        "youtube.com", "youtu.be", "ytimg.com", "googlevideo.com", "youtube-nocookie.com",
        "youtubei.googleapis.com", "youtube.googleapis.com", "yt.be", "ggpht.com"]},
    {"id": "netflix", "name": "Netflix", "category": "Vídeo y streaming", "domains": [
        "netflix.com", "netflix.net", "nflxext.com", "nflximg.com", "nflximg.net", "nflxvideo.net",
        "nflxso.net", "nflxsearch.net"]},
    {"id": "twitch", "name": "Twitch", "category": "Vídeo y streaming", "domains": [
        "twitch.tv", "ttvnw.net", "jtvnw.net", "twitchcdn.net", "twitchsvc.net", "ext-twitch.tv", "twitchapps.com"]},
    {"id": "disneyplus", "name": "Disney+", "category": "Vídeo y streaming", "domains": [
        "disneyplus.com", "disney-plus.net", "dssott.com", "bamgrid.com", "disneystreaming.com"]},
    {"id": "primevideo", "name": "Prime Video", "category": "Vídeo y streaming", "domains": [
        "primevideo.com", "aiv-cdn.net", "aiv-delivery.net", "amazonvideo.com", "pv-cdn.net"]},
    {"id": "hbomax", "name": "Max (HBO)", "category": "Vídeo y streaming", "domains": [
        "max.com", "hbomax.com", "hbo.com", "hbomaxcdn.com", "discomax.com"]},
    {"id": "spotify", "name": "Spotify", "category": "Vídeo y streaming", "domains": [
        "spotify.com", "scdn.co", "spotifycdn.com", "spotifycdn.net", "spoti.fi", "pscdn.co", "audio-ak-spotify-com.akamaized.net"]},
    {"id": "kick", "name": "Kick", "category": "Vídeo y streaming", "domains": ["kick.com"]},
    {"id": "crunchyroll", "name": "Crunchyroll", "category": "Vídeo y streaming", "domains": ["crunchyroll.com", "vrv.co"]},
    # --- Juegos ---
    {"id": "fortnite", "name": "Fortnite / Epic Games", "category": "Juegos", "domains": [
        "epicgames.com", "epicgames.dev", "fortnite.com", "unrealengine.com", "epicgames.net", "ol.epicgames.com",
        "easyanticheat.net", "eac-cdn.com"]},
    {"id": "roblox", "name": "Roblox", "category": "Juegos", "domains": [
        "roblox.com", "rbxcdn.com", "rbx.com", "robloxlabs.com", "roblox.qq.com", "rbxtrk.com", "roblox.cn"]},
    {"id": "minecraft", "name": "Minecraft", "category": "Juegos", "domains": [
        "minecraft.net", "mojang.com", "minecraftservices.com", "minecraft-services.net", "playfabapi.com"]},
    {"id": "steam", "name": "Steam", "category": "Juegos", "domains": [
        "steampowered.com", "steamcommunity.com", "steamstatic.com", "steamcontent.com", "steamserver.net",
        "steamgames.com", "steamusercontent.com", "valvesoftware.com", "steam-chat.com"]},
    {"id": "playstation", "name": "PlayStation Network", "category": "Juegos", "domains": [
        "playstation.com", "playstation.net", "sonyentertainmentnetwork.com", "psn.com", "scea.com"]},
    {"id": "xbox", "name": "Xbox Live", "category": "Juegos", "domains": [
        "xbox.com", "xboxlive.com", "xboxservices.com", "xboxab.com", "gamepass.com"]},
    {"id": "nintendo", "name": "Nintendo", "category": "Juegos", "domains": ["nintendo.com", "nintendo.net", "nintendo.co.jp"]},
    {"id": "leagueoflegends", "name": "League of Legends / Riot", "category": "Juegos", "domains": [
        "riotgames.com", "leagueoflegends.com", "riotcdn.net", "pvp.net", "valorant.com", "lolstatic.com"]},
    {"id": "freefire", "name": "Free Fire", "category": "Juegos", "domains": ["freefiremobile.com", "garena.com", "ff.garena.com"]},
    {"id": "pubg", "name": "PUBG", "category": "Juegos", "domains": ["pubg.com", "pubgmobile.com", "igamecj.com", "proximabeta.com"]},
    {"id": "clashroyale", "name": "Supercell (Clash, Brawl Stars)", "category": "Juegos", "domains": [
        "supercell.com", "supercell.net", "clashroyaleapp.com", "clashofclans.com", "brawlstars.com", "supercellid.com"]},
    # --- Compras / Apuestas ---
    {"id": "amazon", "name": "Amazon", "category": "Compras", "domains": ["amazon.com", "amazon.es", "amazon.com.mx", "amazon.de", "amazon.co.uk", "media-amazon.com", "ssl-images-amazon.com"]},
    {"id": "aliexpress", "name": "AliExpress / Temu / Shein", "category": "Compras", "domains": [
        "aliexpress.com", "aliexpress.us", "alicdn.com", "temu.com", "kwcdn.com", "shein.com", "ltwebstatic.com"]},
    {"id": "gambling", "name": "Apuestas (principales)", "category": "Apuestas", "domains": [
        "bet365.com", "betfair.com", "williamhill.com", "pokerstars.com", "codere.com", "betway.com", "888.com",
        "bwin.com", "stake.com", "1xbet.com", "sportium.es", "caliente.mx", "betsson.com", "rushbet.co"]},
    # --- IA ---
    {"id": "chatgpt", "name": "ChatGPT / OpenAI", "category": "Inteligencia artificial", "domains": [
        "openai.com", "chatgpt.com", "oaistatic.com", "oaiusercontent.com"]},
    # --- Citas ---
    {"id": "tinder", "name": "Tinder / apps de citas", "category": "Citas", "domains": [
        "tinder.com", "gotinder.com", "bumble.com", "badoo.com", "hinge.co", "grindr.com", "okcupid.com"]},
    # --- Elusión de controles ---
    {"id": "vpn_proxy", "name": "VPN y proxys públicos", "category": "Elusión", "domains": [
        "nordvpn.com", "nordvpn.net", "expressvpn.com", "surfshark.com", "protonvpn.com", "proton.me",
        "privateinternetaccess.com", "windscribe.com", "hide.me", "tunnelbear.com", "hotspotshield.com",
        "cyberghostvpn.com", "psiphon.ca", "psiphon3.com", "ultrasurf.us", "hidemyass.com", "vpnbook.com",
        "croxyproxy.com", "hidester.com", "kproxy.com", "proxysite.com", "torproject.org"]},
    {"id": "public_doh", "name": "DNS cifrado externo (DoH)", "category": "Elusión", "domains": [
        "dns.google", "cloudflare-dns.com", "mozilla.cloudflare-dns.com", "dns.quad9.net", "doh.opendns.com",
        "dns.adguard-dns.com", "dns.nextdns.io", "doh.cleanbrowsing.org", "doh.mullvad.net", "dns.mullvad.net",
        "chrome.cloudflare-dns.com", "doh.dns.sb", "dns.controld.com", "freedns.controld.com"]},
]

SERVICE_MAP = {s["id"]: s for s in SERVICES}

# Reescrituras CNAME para forzar búsqueda segura
SAFESEARCH: dict[str, str] = {
    **{d: "forcesafesearch.google.com" for d in (
        "www.google.com", "google.com", "www.google.es", "google.es", "www.google.com.mx", "google.com.mx",
        "www.google.com.ar", "www.google.com.co", "www.google.cl", "www.google.com.pe", "www.google.co.ve",
        "www.google.com.do", "www.google.com.ec", "www.google.com.uy", "www.google.com.py", "www.google.com.bo",
        "www.google.co.cr", "www.google.com.pa", "www.google.com.gt", "www.google.com.sv", "www.google.hn",
        "www.google.com.ni", "www.google.com.pr", "www.google.co.uk", "www.google.de", "www.google.fr",
        "www.google.it", "www.google.ca", "www.google.com.br", "www.google.pt", "www.google.co.in")},
    "www.bing.com": "strict.bing.com",
    "bing.com": "strict.bing.com",
    "duckduckgo.com": "safe.duckduckgo.com",
    "www.duckduckgo.com": "safe.duckduckgo.com",
    "start.duckduckgo.com": "safe.duckduckgo.com",
    "search.brave.com": "search.brave4u.com",
    "pixabay.com": "safesearch.pixabay.com",
}
YOUTUBE_RESTRICT: dict[str, str] = {
    d: "restrictmoderate.youtube.com" for d in (
        "www.youtube.com", "m.youtube.com", "youtube.com", "youtubei.googleapis.com",
        "youtube.googleapis.com", "www.youtube-nocookie.com")
}

# Listas de bloqueo recomendadas (se ofrecen en la interfaz)
RECOMMENDED_LISTS: list[dict] = [
    {"name": "StevenBlack Unified (anuncios + malware)", "category": "ads", "default": True,
     "url": "https://raw.githubusercontent.com/StevenBlack/hosts/master/hosts"},
    {"name": "AdGuard DNS filter", "category": "ads", "default": True,
     "url": "https://adguardteam.github.io/AdGuardSDNSFilter/Filters/filter.txt"},
    {"name": "HaGeZi Multi PRO (equilibrado)", "category": "ads", "default": False,
     "url": "https://raw.githubusercontent.com/hagezi/dns-blocklists/main/adblock/pro.txt"},
    {"name": "OISD Small", "category": "ads", "default": False,
     "url": "https://small.oisd.nl/domainswild"},
    {"name": "HaGeZi Threat Intelligence (malware, phishing)", "category": "security", "default": True,
     "url": "https://raw.githubusercontent.com/hagezi/dns-blocklists/main/adblock/tif.mini.txt"},
    {"name": "Phishing Army", "category": "security", "default": False,
     "url": "https://phishing.army/download/phishing_army_blocklist.txt"},
    {"name": "URLhaus malware (abuse.ch)", "category": "security", "default": True,
     "url": "https://urlhaus.abuse.ch/downloads/hostfile/"},
    {"name": "HaGeZi Tracking nativo (Windows/Office)", "category": "ads", "default": False,
     "url": "https://raw.githubusercontent.com/hagezi/dns-blocklists/main/adblock/native.winoffice.txt"},
    {"name": "StevenBlack contenido adulto", "category": "adult", "default": False,
     "url": "https://raw.githubusercontent.com/StevenBlack/hosts/master/alternates/porn-only/hosts"},
    {"name": "HaGeZi Apuestas", "category": "adult", "default": False,
     "url": "https://raw.githubusercontent.com/hagezi/dns-blocklists/main/adblock/gambling.txt"},
]

# Resolvers DNS públicos (anycast: se enruta automáticamente al nodo más cercano)
DNS_PRESETS: dict[str, dict] = {
    "unbound": {"name": "Unbound local (recursivo, máxima privacidad)", "servers": ["127.0.0.1:5335"], "group": "Local"},
    "cloudflare": {"name": "Cloudflare", "servers": ["1.1.1.1", "1.0.0.1"], "group": "Rápidos"},
    "google": {"name": "Google Public DNS", "servers": ["8.8.8.8", "8.8.4.4"], "group": "Rápidos"},
    "quad9": {"name": "Quad9 (bloquea malware)", "servers": ["9.9.9.9", "149.112.112.112"], "group": "Seguridad"},
    "adguard": {"name": "AdGuard DNS (bloquea anuncios)", "servers": ["94.140.14.14", "94.140.15.15"], "group": "Seguridad"},
    "opendns": {"name": "OpenDNS (Cisco)", "servers": ["208.67.222.222", "208.67.220.220"], "group": "Rápidos"},
    "controld": {"name": "Control D (sin filtro)", "servers": ["76.76.2.0", "76.76.10.0"], "group": "Rápidos"},
    "cloudflare-family": {"name": "Cloudflare Familia (malware + adultos)", "servers": ["1.1.1.3", "1.0.0.3"], "group": "Familia"},
    "adguard-family": {"name": "AdGuard Familia", "servers": ["94.140.14.15", "94.140.15.16"], "group": "Familia"},
    "opendns-family": {"name": "OpenDNS FamilyShield", "servers": ["208.67.222.123", "208.67.220.123"], "group": "Familia"},
    "cleanbrowsing-family": {"name": "CleanBrowsing Familia", "servers": ["185.228.168.168", "185.228.169.168"], "group": "Familia"},
    "cloudflare-dot": {"name": "Cloudflare DNS-over-TLS", "servers": ["tls://1.1.1.1#cloudflare-dns.com", "tls://1.0.0.1#cloudflare-dns.com"], "group": "Cifrados"},
    "cloudflare-doh": {"name": "Cloudflare DNS-over-HTTPS", "servers": ["https://1.1.1.1/dns-query", "https://1.0.0.1/dns-query"], "group": "Cifrados"},
    "google-doh": {"name": "Google DNS-over-HTTPS", "servers": ["https://8.8.8.8/dns-query", "https://8.8.4.4/dns-query"], "group": "Cifrados"},
    "quad9-dot": {"name": "Quad9 DNS-over-TLS", "servers": ["tls://9.9.9.9#dns.quad9.net", "tls://149.112.112.112#dns.quad9.net"], "group": "Cifrados"},
    "quad9-doh": {"name": "Quad9 DNS-over-HTTPS", "servers": ["https://9.9.9.9/dns-query"], "group": "Cifrados"},
    "custom": {"name": "Personalizado", "servers": [], "group": "Otros"},
}

# Candidatos que el optimizador de latencia evalúa (sólo UDP, sin filtrado)
OPTIMIZER_CANDIDATES = ["unbound", "cloudflare", "google", "quad9", "opendns", "controld", "adguard"]

# Formas de bloquear/forzar comportamientos DNS en navegadores y sistemas
DOH_CANARY = "use-application-dns.net"
ICLOUD_RELAY = ("mask.icloud.com", "mask-h2.icloud.com")
