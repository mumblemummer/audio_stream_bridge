import asyncio
import json
import logging
import os
from pathlib import Path
import re
import socket
import subprocess
import threading
import time
from typing import Any, Callable, Dict, Optional
import urllib.parse
import uuid

import aiohttp

logger = logging.getLogger("YouTubeCast")

SSDP_ADDR = "239.255.255.250"
SSDP_PORT = 1900
FIFO_CAST = "/tmp/asb_fifo_cast"

LOUNGE_TOKEN_URL = "https://www.youtube.com/api/lounge/pairing/generate_screen_id"
LOUNGE_CODE_URL = "https://www.youtube.com/api/lounge/pairing/get_pairing_code"
LOUNGE_BC_URL = "https://www.youtube.com/api/lounge/bc/bind"


class YouTubeCastService:
    def __init__(
        self,
        host_ip: str,
        http_port: int = 8099,
        speaker_name: str = "Audio Stream Bridge",
        device_uuid: Optional[str] = None,
        on_playback_state_change: Optional[Callable[[Dict[str, Any]], None]] = None,
    ):
        self.host_ip = host_ip
        self.http_port = http_port
        self.speaker_name = speaker_name
        self.device_uuid = device_uuid or str(uuid.uuid4())
        self.on_playback_state_change = on_playback_state_change

        self.screen_id: str = ""
        self.lounge_token: str = ""
        self.pairing_code: str = ""
        self.session_id: str = ""
        self.gsession_id: str = ""

        self.current_video_id: Optional[str] = None
        self.current_metadata: Dict[str, Any] = {
            "title": "대기 중",
            "author": "YouTube Cast 대기 중",
            "thumbnail": "",
            "duration": 0,
            "is_playing": False,
        }

        self._ssdp_thread: Optional[threading.Thread] = None
        self._lounge_thread: Optional[threading.Thread] = None
        self._ffmpeg_proc: Optional[subprocess.Popen] = None
        self._running = False
        self._lock = threading.Lock()

    def start(self):
        self._running = True
        logger.info(f"YouTube Cast & DIAL 서비스 시작 (Host: {self.host_ip}:{self.http_port}, UUID: {self.device_uuid})")

        # 1. SSDP M-SEARCH 리스너 스레드 시작
        self._ssdp_thread = threading.Thread(target=self._ssdp_listener_worker, daemon=True)
        self._ssdp_thread.start()

        # 2. YouTube Lounge TV 코드 발급 및 이벤트 루프 스레드 시작
        self._lounge_thread = threading.Thread(target=self._lounge_worker, daemon=True)
        self._lounge_thread.start()

    def stop(self):
        self._running = False
        self.stop_playback()

    def stop_playback(self):
        with self._lock:
            if self._ffmpeg_proc and self._ffmpeg_proc.poll() is None:
                logger.info("기존 ffmpeg 오디오 스트림 프로세스 종료")
                try:
                    self._ffmpeg_proc.terminate()
                    self._ffmpeg_proc.wait(timeout=2)
                except Exception:
                    self._ffmpeg_proc.kill()
            self._ffmpeg_proc = None
            self.current_metadata["is_playing"] = False
            self._notify_state()

    def _notify_state(self):
        if self.on_playback_state_change:
            try:
                self.on_playback_state_change(self.current_metadata)
            except Exception as e:
                logger.debug(f"상태 콜백 오류: {e}")

    # --- SSDP DIAL M-SEARCH 리스너 ---
    def _ssdp_listener_worker(self):
        """UDP 1900 멀티캐스트 포트에서 스마트폰의 DIAL 검색 요청을 감지하여 200 OK 응답"""
        logger.info("DIAL SSDP 멀티캐스트 리스너 구동 중...")
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

        try:
            sock.bind(("", SSDP_PORT))
            mreq = socket.inet_aton(SSDP_ADDR) + socket.inet_aton("0.0.0.0")
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
        except Exception as e:
            logger.error(f"SSDP 멀티캐스트 소켓 바인딩 실패: {e}")
            return

        sock.settimeout(2.0)

        response_template = (
            "HTTP/1.1 200 OK\r\n"
            "CACHE-CONTROL: max-age=1800\r\n"
            "EXT:\r\n"
            f"LOCATION: http://{self.host_ip}:{self.http_port}/ssdp/device-desc.xml\r\n"
            "SERVER: Linux/UPnP/1.0 AudioStreamBridge/2.0\r\n"
            "ST: urn:dial-multiscreen-org:service:dial:1\r\n"
            f"USN: uuid:{self.device_uuid}::urn:dial-multiscreen-org:service:dial:1\r\n"
            "BOOTID.UPNP.ORG: 1\r\n"
            "CONFIGID.UPNP.ORG: 1\r\n"
            "\r\n"
        ).encode("utf-8")

        while self._running:
            try:
                data, addr = sock.recvfrom(2048)
                msg = data.decode("utf-8", errors="ignore")
                if "M-SEARCH" in msg and (
                    "urn:dial-multiscreen-org:service:dial:1" in msg
                    or "ssdp:all" in msg
                ):
                    sock.sendto(response_template, addr)
            except socket.timeout:
                continue
            except Exception as e:
                logger.debug(f"SSDP 루프 예외: {e}")
                time.sleep(1)

        sock.close()

    # --- DIAL XML 응답 문서 생성기 ---
    def get_device_description_xml(self) -> str:
        return f"""<?xml version="1.0" encoding="utf-8"?>
<root xmlns="urn:schemas-upnp-org:device-1-0">
  <specVersion>
    <major>1</major>
    <minor>0</minor>
  </specVersion>
  <device>
    <deviceType>urn:schemas-upnp-org:device:tvdevice:1</deviceType>
    <friendlyName>{self.speaker_name}</friendlyName>
    <manufacturer>Audio Stream Bridge</manufacturer>
    <modelName>ASB-Cast-2.0</modelName>
    <UDN>uuid:{self.device_uuid}</UDN>
    <serviceList>
      <service>
        <serviceType>urn:dial-multiscreen-org:service:dial:1</serviceType>
        <serviceId>urn:dial-multiscreen-org:serviceId:dial</serviceId>
        <controlURL>/apps</controlURL>
        <eventSubURL></eventSubURL>
        <SCPDURL>/dial.xml</SCPDURL>
      </service>
    </serviceList>
  </device>
</root>"""

    def get_app_status_xml(self) -> str:
        state_str = "running" if self.current_metadata.get("is_playing") else "stopped"
        return f"""<?xml version="1.0" encoding="UTF-8"?>
<service xmlns="urn:dial-multiscreen-org:schemas:dial">
  <name>YouTube</name>
  <options allowStop="true"/>
  <state>{state_str}</state>
  <link rel="run" href="run"/>
</service>"""

    # --- YouTube Lounge TV 코드 발급 및 세션 루프 ---
    def _lounge_worker(self):
        """YouTube Lounge API에 Screen을 등록하고 12자리 TV 페어링 코드를 갱신하며 스마트폰 명령 대기"""
        import urllib.request

        while self._running:
            try:
                # 1. Screen ID 및 Lounge Token 발급
                req = urllib.request.Request(
                    LOUNGE_TOKEN_URL,
                    data=b"",
                    headers={"User-Agent": "Mozilla/5.0 (SMART-TV; Linux; Tizen 6.0) AppleWebkit/537.36"},
                )
                with urllib.request.urlopen(req, timeout=10) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    self.screen_id = data.get("screenId", "")
                    self.lounge_token = data.get("loungeToken", "")

                if not self.screen_id:
                    time.sleep(10)
                    continue

                # 2. 12자리 TV 페어링 코드 발급
                code_data = urllib.parse.urlencode({"screen_id": self.screen_id}).encode("utf-8")
                code_req = urllib.request.Request(
                    LOUNGE_CODE_URL,
                    data=code_data,
                    headers={"User-Agent": "Mozilla/5.0 (SMART-TV; Linux; Tizen 6.0) AppleWebkit/537.36"},
                )
                with urllib.request.urlopen(code_req, timeout=10) as resp:
                    raw_code = resp.read().decode("utf-8").strip().replace('"', "")
                    # 12자리 코드를 3자리씩 보기 좋게 포맷 (예: 123 456 789 012)
                    if len(raw_code) == 12:
                        self.pairing_code = f"{raw_code[0:3]} {raw_code[3:6]} {raw_code[6:9]} {raw_code[9:12]}"
                    else:
                        self.pairing_code = raw_code
                    logger.info(f"YouTube TV 페어링 코드 발급됨: {self.pairing_code} (Screen ID: {self.screen_id[:8]}...)")

                # TV 코드는 일반적으로 약 15~30분간 유효하므로 주기적으로 재갱신
                # 백그라운드에서 주기적으로 대기 (페어링 코드는 UI를 통해 확인 가능)
                for _ in range(60):
                    if not self._running:
                        break
                    time.sleep(10)

            except Exception as e:
                logger.debug(f"YouTube Lounge 세션 루프 오류: {e}")
                time.sleep(10)

    # --- yt-dlp & ffmpeg 오디오 스트림 추출 및 재생 ---
    def play_youtube_url_or_id(self, query: str) -> bool:
        """URL, videoId 또는 검색어를 받아 오디오 스트림을 추출하고 FIFO 파이프로 송출"""
        logger.info(f"유튜브 오디오 재생 요청 수신: {query}")
        threading.Thread(target=self._play_worker, args=(query,), daemon=True).start()
        return True

    def _play_worker(self, query: str):
        target_url = query.strip()
        # videoId 단독 입력인 경우 URL 형태로 보정
        if re.match(r"^[a-zA-Z0-9_-]{11}$", target_url):
            target_url = f"https://www.youtube.com/watch?v={target_url}"
        elif not target_url.startswith("http"):
            # 검색어인 경우 ytsearch: 접두사 사용
            target_url = f"ytsearch1:{target_url}"

        try:
            import yt_dlp

            ydl_opts = {
                "format": "bestaudio/best",
                "noplaylist": True,
                "quiet": True,
                "no_warnings": True,
                "default_search": "ytsearch1",
            }

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(target_url, download=False)
                if "entries" in info and info["entries"]:
                    info = info["entries"][0]

                stream_url = info.get("url")
                title = info.get("title", "알 수 없는 곡")
                author = info.get("uploader", "YouTube")
                thumbnail = info.get("thumbnail", "")
                duration = info.get("duration", 0)
                video_id = info.get("id", "")

                if not stream_url:
                    logger.error("오디오 스트림 URL 추출 실패")
                    return

                logger.info(f"유튜브 오디오 추출 성공: [{title}] by [{author}] (ID: {video_id})")

                with self._lock:
                    self.stop_playback()
                    self.current_video_id = video_id
                    self.current_metadata = {
                        "title": title,
                        "author": author,
                        "thumbnail": thumbnail,
                        "duration": duration,
                        "is_playing": True,
                        "video_id": video_id,
                    }
                    self._notify_state()

                    # ffmpeg로 오디오 스트림을 읽어 실시간 44.1kHz 16-bit Stereo PCM 파이프로 디코딩
                    cmd = [
                        "ffmpeg",
                        "-reconnect", "1",
                        "-reconnect_streamed", "1",
                        "-reconnect_delay_max", "5",
                        "-i", stream_url,
                        "-f", "s16le",
                        "-acodec", "pcm_s16le",
                        "-ar", "44100",
                        "-ac", "2",
                        "-y",
                        FIFO_CAST,
                    ]
                    self._ffmpeg_proc = subprocess.Popen(
                        cmd,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                    )

            # ffmpeg 완료 대기
            if self._ffmpeg_proc:
                self._ffmpeg_proc.wait()
                logger.info("유튜브 오디오 스트림 재생 완료")
                with self._lock:
                    self.current_metadata["is_playing"] = False
                    self._notify_state()

        except Exception as e:
            logger.error(f"유튜브 오디오 재생 처리 실패: {e}")
            with self._lock:
                self.current_metadata["is_playing"] = False
                self._notify_state()
