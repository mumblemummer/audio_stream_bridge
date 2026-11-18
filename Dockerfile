ARG BUILD_FROM=ghcr.io/home-assistant/amd64-base-debian:bookworm
FROM ${BUILD_FROM}

ENV LANG=C.UTF-8 \
    DEBIAN_FRONTEND=noninteractive

# 1. 저장소 서명 키 다운로드에 필요한 필수 인증서 및 네트워크 패키지 설치
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    gnupg \
    && rm -rf /var/lib/apt/lists/*

# 2. upmpdcli 공식 저장소 및 GPG 키 등록 (amd64 / arm64 멀티 아키텍처 지원)
RUN mkdir -p /usr/share/keyrings \
    && curl -fsSL https://www.lesbonscomptes.com/pages/lesbonscomptes.gpg -o /usr/share/keyrings/lesbonscomptes.gpg \
    && ARCH=$(dpkg --print-architecture) \
    && if [ "$ARCH" = "arm64" ] || [ "$ARCH" = "armhf" ]; then \
         echo "deb [signed-by=/usr/share/keyrings/lesbonscomptes.gpg] http://www.lesbonscomptes.com/upmpdcli/downloads/raspbian/ bookworm main" > /etc/apt/sources.list.d/upmpdcli.list; \
       else \
         echo "deb [signed-by=/usr/share/keyrings/lesbonscomptes.gpg] http://www.lesbonscomptes.com/upmpdcli/downloads/debian/ bookworm main" > /etc/apt/sources.list.d/upmpdcli.list; \
       fi

# 3. 리눅스 오디오, mDNS, 스트리밍 데몬 및 필수 유틸리티(ffmpeg 포함) 설치
RUN apt-get update && apt-get install -y --no-install-recommends \
    dbus \
    avahi-daemon \
    avahi-utils \
    libnss-mdns \
    shairport-sync \
    mpd \
    upmpdcli \
    snapserver \
    supervisor \
    gettext-base \
    jq \
    procps \
    net-tools \
    ffmpeg \
    uuid-runtime \
    python3 \
    python3-pip \
    && rm -rf /var/lib/apt/lists/*

# 4. Python 백엔드 및 유튜브 오디오 스트리머 종속성 설치
RUN pip3 install --no-cache-dir --break-system-packages \
    fastapi \
    "uvicorn[standard]" \
    aiohttp \
    jinja2 \
    python-multipart \
    yt-dlp

# 5. 필요한 런타임 디렉터리 및 권한 준비
RUN mkdir -p /run/dbus /var/run/dbus /var/run/avahi-daemon /var/log/supervisor /data \
    && chown -R messagebus:messagebus /run/dbus /var/run/dbus \
    && chown -R avahi:avahi /var/run/avahi-daemon

# 6. 루트 파일시스템 복사
COPY rootfs /

# 7. 실행 권한 설정
RUN chmod +x /usr/local/bin/run.sh

# 포트 안내 (기존 방화벽 정책 그대로 활용)
# 8099: Ingress Web UI, Live WAV Stream & DIAL HTTP API
# 1704: Snapcast TCP Stream
# 1705: Snapcast Control
# 1780: Snapcast HTTP Web & Stream
# 5000: AirPlay RTSP
# 6600: MPD
# 49152: UPnP / DLNA
EXPOSE 8099 1704 1705 1780 5000 6600 49152

WORKDIR /app

CMD ["/usr/local/bin/run.sh"]
