#!/usr/bin/env bash
set -e

echo "[Audio Stream Bridge] Starting initialization..."

CONFIG_PATH="/data/options.json"
DATA_DIR="/data"

# 옵션 로드 (기본값 fallback 포함)
if [ -f "$CONFIG_PATH" ]; then
    SPEAKER_NAME=$(jq -r '.speaker_name // "Audio Stream Bridge"' "$CONFIG_PATH")
    AUDIO_SAMPLE_RATE=$(jq -r '.audio_sample_rate // "44100:16:2"' "$CONFIG_PATH")
else
    SPEAKER_NAME="Audio Stream Bridge"
    AUDIO_SAMPLE_RATE="44100:16:2"
fi

export SPEAKER_NAME
export AUDIO_SAMPLE_RATE

echo "[Audio Stream Bridge] Speaker Name: ${SPEAKER_NAME}"
echo "[Audio Stream Bridge] Audio Sample Rate: ${AUDIO_SAMPLE_RATE}"
echo "[Audio Stream Bridge] DLNA/UPnP Port: 49152 (Fixed)"
echo "[Audio Stream Bridge] DIAL / YouTube Cast Port: 8099"

# host_network: true 환경에서 supervisor 도메인 해석 보장
if ! grep -q "supervisor" /etc/hosts 2>/dev/null; then
    echo "172.30.32.2 supervisor" >> /etc/hosts 2>/dev/null || true
fi
export SUPERVISOR_TOKEN="${SUPERVISOR_TOKEN:-}"
export HASSIO_TOKEN="${HASSIO_TOKEN:-}"

# 영구 저장 스피커 목록 파일 초기화
mkdir -p "$DATA_DIR"
if [ ! -f "$DATA_DIR/selected_speakers.json" ]; then
    echo "[]" > "$DATA_DIR/selected_speakers.json"
fi

# DIAL 고유 기기 UUID 생성 및 영구 보관
if [ ! -f "$DATA_DIR/device_uuid.txt" ]; then
    if command -v uuidgen >/dev/null 2>&1; then
        uuidgen > "$DATA_DIR/device_uuid.txt"
    else
        cat /proc/sys/kernel/random/uuid > "$DATA_DIR/device_uuid.txt" 2>/dev/null || echo "c23f2b4b-9711-4f12-a160-b8c19139820f" > "$DATA_DIR/device_uuid.txt"
    fi
fi
DEVICE_UUID=$(cat "$DATA_DIR/device_uuid.txt" | tr -d '\r\n')
export DEVICE_UUID
echo "[Audio Stream Bridge] Device UUID: ${DEVICE_UUID}"

# D-Bus 및 시스템 런타임 디렉터리 준비
mkdir -p /run/dbus /var/run/dbus /var/run/avahi-daemon /run/mpd /var/lib/mpd/music /var/lib/mpd/playlists /var/lib/snapserver /var/log/supervisor
rm -f /run/dbus/system_bus_socket /run/dbus/pid /var/run/avahi-daemon/pid /run/mpd/asb_pid /var/run/asb_upmpdcli.pid

# D-Bus 머신 ID 생성 보장
if [ ! -f /var/lib/dbus/machine-id ]; then
    dbus-uuidgen --ensure=/var/lib/dbus/machine-id
fi

# Named FIFO 파이프 생성 (AirPlay, DLNA, YouTube Cast, Snapcast)
echo "[Audio Stream Bridge] Setting up FIFO pipes..."
rm -f /tmp/asb_fifo_airplay /tmp/asb_fifo_dlna /tmp/asb_fifo_cast /tmp/asb_fifo_snap
mkfifo -m 666 /tmp/asb_fifo_airplay
mkfifo -m 666 /tmp/asb_fifo_dlna
mkfifo -m 666 /tmp/asb_fifo_cast
mkfifo -m 666 /tmp/asb_fifo_snap

# 설정 템플릿 환경변수 치환
echo "[Audio Stream Bridge] Generating configuration files from templates..."
envsubst '${SPEAKER_NAME} ${AUDIO_SAMPLE_RATE}' < /etc/shairport-sync.conf.template > /etc/shairport-sync.conf
envsubst '${SPEAKER_NAME} ${AUDIO_SAMPLE_RATE}' < /etc/mpd.conf.template > /etc/mpd.conf
envsubst '${SPEAKER_NAME} ${AUDIO_SAMPLE_RATE}' < /etc/upmpdcli.conf.template > /etc/upmpdcli.conf
envsubst '${SPEAKER_NAME} ${AUDIO_SAMPLE_RATE}' < /etc/snapserver.conf.template > /etc/snapserver.conf

# Avahi가 도커 브릿지(172.30.32.1)를 광고하지 않고 실제 LAN IP(192.168.1.x)만 mDNS로 광고하도록 설정
mkdir -p /etc/avahi
cat << 'EOF' > /etc/avahi/avahi-daemon.conf
[server]
use-ipv4=yes
use-ipv6=no
deny-interfaces=hassio,docker0,veth0
enable-dbus=yes
disallow-other-stacks=no

[wide-area]
enable-wide-area=yes

[publish]
publish-addresses=yes
publish-hinfo=yes
publish-workstation=no
publish-domain=yes

[reflector]
enable-reflector=no
EOF

# supervisord 설정 복사 확인
if [ ! -f /etc/supervisord.conf ]; then
    cp /rootfs/etc/supervisord.conf /etc/supervisord.conf 2>/dev/null || true
fi

echo "[Audio Stream Bridge] Initialization complete. Launching Supervisord..."
exec /usr/bin/supervisord -c /etc/supervisord.conf
