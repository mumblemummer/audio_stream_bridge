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
import urllib.error
import urllib.parse
import urllib.request
import uuid

import aiohttp

logger = logging.getLogger("YouTubeCast")

SSDP_ADDR = "239.255.255.250"
SSDP_PORT = 1900
FIFO_CAST = "/tmp/asb_fifo_cast"

LOUNGE_SCREEN_URL = "https://www.youtube.com/api/lounge/pairing/generate_screen_id"
LOUNGE_TOKEN_URL = "https://www.youtube.com/api/lounge/pairing/get_lounge_token_batch"
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
        on_audio_chunk: Optional[Callable[[bytes], None]] = None,
    ):
        self.host_ip = host_ip
        self.http_port = http_port
        self.speaker_name = speaker_name
        self.device_uuid = device_uuid or str(uuid.uuid4())
        self.on_playback_state_change = on_playback_state_change
        self.on_audio_chunk = on_audio_chunk

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
        self._lounge_listener_thread: Optional[threading.Thread] = None
        self._ffmpeg_proc: Optional[subprocess.Popen] = None
        self._running = False
        self._lock = threading.RLock()

    def start(self):
        self._running = True
        logger.info(f"YouTube Cast & DIAL 서비스 시작 (Host: {self.host_ip}:{self.http_port}, UUID: {self.device_uuid})")

        # 1. SSDP M-SEARCH 리스너 스레드 시작
        self._ssdp_thread = threading.Thread(target=self._ssdp_listener_worker, daemon=True)
        self._ssdp_thread.start()

        # 2. YouTube Lounge TV 코드 발급 및 세션 바인딩 스레드 시작
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

    def get_app_status_xml(self, app_name: str = "YouTube") -> str:
        return f"""<?xml version="1.0" encoding="UTF-8"?>
<service xmlns="urn:dial-multiscreen-org:schemas:dial" dialVer="2.1">
  <name>{app_name}</name>
  <options allowStop="true"/>
  <state>running</state>
  <link rel="run" href="run"/>
  <additionalData>
    <brand>AudioStreamBridge</brand>
    <model>ASB-2.0</model>
    <screenId>{self.screen_id}</screenId>
    <deviceId>{self.device_uuid}</deviceId>
    <loungeToken>{self.lounge_token}</loungeToken>
    <loungeTokenRefreshIntervalMs>1500000</loungeTokenRefreshIntervalMs>
  </additionalData>
</service>"""

    def register_pairing_code_from_phone(self, pairing_code: str):
        """스마트폰이 DIAL을 통해 보내온 pairingCode를 구글 Lounge 서버에 등록"""
        try:
            url = "https://www.youtube.com/api/lounge/pairing/register_pairing_code"
            data = urllib.parse.urlencode({
                "screen_id": self.screen_id,
                "pairing_code": pairing_code,
                "access_type": "permanent",
            }).encode("utf-8")
            req = urllib.request.Request(
                url,
                data=data,
                headers={
                    "User-Agent": "Mozilla/5.0 (SMART-TV; Linux; Tizen 6.0) AppleWebkit/537.36",
                    "X-YouTube-LoungeId-Token": self.lounge_token,
                },
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                logger.info(f"스마트폰 pairingCode 구글 서버 등록 성공: {pairing_code}")
        except Exception as e:
            logger.debug(f"pairingCode 등록 오류: {e}")

    # --- YouTube Lounge TV 코드 발급 및 세션 루프 ---
    def _lounge_worker(self):
        """YouTube Lounge API에 Screen을 등록하고 12자리 TV 페어링 코드 발급 및 bind 수신"""
        while self._running:
            try:
                # 1. Screen ID 발급 (plain text string)
                req1 = urllib.request.Request(
                    LOUNGE_SCREEN_URL,
                    data=b"",
                    headers={"User-Agent": "Mozilla/5.0 (SMART-TV; Linux; Tizen 6.0) AppleWebkit/537.36"},
                )
                with urllib.request.urlopen(req1, timeout=10) as resp:
                    self.screen_id = resp.read().decode("utf-8").strip().replace('"', '')

                if not self.screen_id:
                    time.sleep(5)
                    continue

                # 2. Lounge Token 발급
                tok_data = urllib.parse.urlencode({"screen_ids": self.screen_id}).encode("utf-8")
                req2 = urllib.request.Request(
                    LOUNGE_TOKEN_URL,
                    data=tok_data,
                    headers={"User-Agent": "Mozilla/5.0 (SMART-TV; Linux; Tizen 6.0) AppleWebkit/537.36"},
                )
                with urllib.request.urlopen(req2, timeout=10) as resp:
                    tok_json = json.loads(resp.read().decode("utf-8"))
                    screens = tok_json.get("screens", [])
                    if screens:
                        self.lounge_token = screens[0].get("loungeToken", "")

                # 3. 12자리 TV 페어링 코드 발급
                code_data = urllib.parse.urlencode({
                    "screen_id": self.screen_id,
                    "access_type": "permanent",
                }).encode("utf-8")
                req3 = urllib.request.Request(
                    LOUNGE_CODE_URL,
                    data=code_data,
                    headers={"User-Agent": "Mozilla/5.0 (SMART-TV; Linux; Tizen 6.0) AppleWebkit/537.36"},
                )
                with urllib.request.urlopen(req3, timeout=10) as resp:
                    raw_code = resp.read().decode("utf-8").strip().replace('"', "")
                    if len(raw_code) == 12:
                        self.pairing_code = f"{raw_code[0:3]} {raw_code[3:6]} {raw_code[6:9]} {raw_code[9:12]}"
                    else:
                        self.pairing_code = raw_code
                    logger.info(f"YouTube TV 페어링 코드 발급 성공: {self.pairing_code} (Screen ID: {self.screen_id})")

                # 4. YouTube Lounge bc/bind 세션 초기화
                if self.lounge_token:
                    self._init_lounge_bind_session()

                # TV 코드는 주기적으로 갱신 대기 (약 15분)
                for _ in range(90):
                    if not self._running:
                        break
                    time.sleep(10)

            except Exception as e:
                logger.error(f"YouTube Lounge TV 코드 발급 오류: {e}")
                time.sleep(10)

    def _init_lounge_bind_session(self):
        """Lounge 화면 바인드 세션을 등록하고 실시간 명령 청취 리스너 구동"""
        try:
            params = {
                "device": "LOUNGE_SCREEN",
                "id": self.screen_id,
                "name": self.speaker_name,
                "app": "lb",
                "mdx-version": "3",
                "theme": "cl",
                "capabilities": "dsp,mic,dpa,mus,que,atp,pas",
                "RID": "0",
                "VER": "8",
                "CVER": "1",
            }
            url = f"{LOUNGE_BC_URL}?" + urllib.parse.urlencode(params)
            req = urllib.request.Request(
                url,
                data=b"count=0",
                headers={
                    "User-Agent": "Mozilla/5.0 (SMART-TV; Linux; Tizen 6.0) AppleWebkit/537.36",
                    "Content-Type": "application/x-www-form-urlencoded",
                    "X-YouTube-LoungeId-Token": self.lounge_token,
                },
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                body = resp.read().decode("utf-8", errors="ignore")
                sid_match = re.search(r'\[0,\["c","([A-Za-z0-9_-]+)"', body)
                gsid_match = re.search(r'\[1,\["S","([A-Za-z0-9_-]+)"', body)
                if sid_match:
                    self.session_id = sid_match.group(1)
                if gsid_match:
                    self.gsession_id = gsid_match.group(1)
                logger.info(f"YouTube Lounge 바인드 세션 수립 완료 (SID: {self.session_id}, GSID: {self.gsession_id[:8]}...)")

            # 실시간 명령 수신 BrowserChannel Back-channel (GET RID=rpc) 스레드 시작
            if self.session_id and self.gsession_id:
                if not (self._lounge_listener_thread and self._lounge_listener_thread.is_alive()):
                    self._lounge_listener_thread = threading.Thread(target=self._lounge_listen_worker, daemon=True)
                    self._lounge_listener_thread.start()

        except Exception as e:
            logger.debug(f"Lounge 바인드 초기화 오류: {e}")

    def _lounge_listen_worker(self):
        """BrowserChannel Back-channel (GET RID=rpc)으로 스마트폰 명령을 실시간 스트리밍 수신"""
        logger.info("YouTube Lounge Back-channel 실시간 명령 리스너 시작 (GET RID=rpc)")
        aid = 2
        forward_rid = 1

        while self._running and self.session_id and self.gsession_id:
            try:
                listen_params = {
                    "device": "LOUNGE_SCREEN",
                    "id": self.screen_id,
                    "name": self.speaker_name,
                    "app": "lb",
                    "mdx-version": "3",
                    "theme": "cl",
                    "capabilities": "dsp,mic,dpa,mus,que,atp,pas",
                    "RID": "rpc",
                    "SID": self.session_id,
                    "gsessionid": self.gsession_id,
                    "CI": "0",
                    "AID": str(aid),
                    "TYPE": "xmlhttp",
                    "VER": "8",
                    "CVER": "1",
                }
                bind_url = f"{LOUNGE_BC_URL}?" + urllib.parse.urlencode(listen_params)
                req = urllib.request.Request(
                    bind_url,
                    headers={
                        "User-Agent": "Mozilla/5.0 (SMART-TV; Linux; Tizen 6.0) AppleWebkit/537.36",
                        "X-YouTube-LoungeId-Token": self.lounge_token,
                    },
                )
                with urllib.request.urlopen(req, timeout=60) as resp:
                    buffer = ""
                    for raw_line in resp:
                        if not self._running:
                            break
                        line = raw_line.decode("utf-8", errors="ignore").strip()
                        if not line:
                            continue
                        buffer += line

                        # AID 번호 추출
                        aid_matches = re.findall(r'\[(\d+),\[', line)
                        if aid_matches:
                            aid = max(aid, int(aid_matches[-1]))

                        # Keep-alive([0,-1,0] 등) 이외의 모든 실제 Lounge 이벤트 로깅
                        if "[" in line and not line.startswith("[0,-1"):
                            logger.info(f"[Lounge 이벤트] {line[:250]}")

                        vid_match = re.search(r'["\']videoId["\']\s*:\s*["\']([a-zA-Z0-9_-]{11})["\']', line)
                        if vid_match:
                            vid = vid_match.group(1)
                            if vid != self.current_video_id or not self.current_metadata.get("is_playing"):
                                logger.info(f"스마트폰으로부터 YouTube 재생 명령 수신 -> videoId={vid}")
                                self.play_youtube_url_or_id(vid)
                                self._send_lounge_state(forward_rid, vid, state=1)
                                forward_rid += 1
                        elif '"pause"' in line or '"stopVideo"' in line:
                            logger.info("스마트폰으로부터 일시정지/중지 명령 수신")
                            self.stop_playback()
                            if self.current_video_id:
                                self._send_lounge_state(forward_rid, self.current_video_id, state=2)
                                forward_rid += 1
                        elif '"loungeStatus"' in line and "REMOTE_CONTROL" not in line:
                            # 스마트폰(REMOTE_CONTROL)이 연결 해제를 눌러 방에서 나간 경우
                            if self.current_metadata.get("is_playing"):
                                logger.info("스마트폰 캐스트 연결 해제 감지 (loungeStatus) -> 재생 중지")
                                self.stop_playback()
                        elif '"remoteDisconnected"' in line or '"onRemoteDisconnected"' in line:
                            if self.current_metadata.get("is_playing"):
                                logger.info("스마트폰 캐스트 연결 해제 이벤트 수신 -> 재생 중지")
                                self.stop_playback()
                        elif '"play"' in line and self.current_video_id:
                            if not self.current_metadata.get("is_playing"):
                                logger.info("스마트폰으로부터 재개(play) 명령 수신")
                                self.play_youtube_url_or_id(self.current_video_id)
                                self._send_lounge_state(forward_rid, self.current_video_id, state=1)
                                forward_rid += 1

            except socket.timeout:
                continue
            except urllib.error.HTTPError as he:
                logger.warning(f"Lounge Back-channel HTTP 오류 ({he.code}): 세션 재수립")
                if he.code in (400, 401, 404, 410):
                    break
                time.sleep(2)
            except Exception as e:
                logger.debug(f"Lounge Back-channel 재연결 대기: {e}")
                time.sleep(2)

    def _send_lounge_state(self, rid: int, video_id: str, state: int = 1):
        """스마트폰 UI에 현재 재생 상태(1: 재생중, 2: 일시정지)를 알려 컨트롤 활성화"""
        try:
            url = (
                f"{LOUNGE_BC_URL}?RID={rid}&SID={self.session_id}&VER=8&CVER=1"
                f"&gsessionid={self.gsession_id}"
            )
            payload = urllib.parse.urlencode({
                "count": "1",
                "req0__sc": "onStateChange",
                "req0_currentTime": "0",
                "req0_state": str(state),
                "req0_duration": "200",
                "req0_cpn": "asb_cast",
                "req0_videoId": video_id,
            }).encode("utf-8")
            req = urllib.request.Request(
                url,
                data=payload,
                headers={
                    "User-Agent": "Mozilla/5.0 (SMART-TV; Linux; Tizen 6.0) AppleWebkit/537.36",
                    "Content-Type": "application/x-www-form-urlencoded",
                    "X-YouTube-LoungeId-Token": self.lounge_token,
                },
            )
            urllib.request.urlopen(req, timeout=5).read()
        except Exception as e:
            logger.debug(f"Lounge 상태 회신 오류: {e}")

    # --- yt-dlp & ffmpeg 오디오 스트림 추출 및 재생 ---
    def play_youtube_url_or_id(self, query: str) -> bool:
        """URL, videoId 또는 검색어를 받아 오디오 스트림을 추출하고 FIFO 파이프로 송출"""
        logger.info(f"유튜브 오디오 재생 요청 수신: {query}")
        threading.Thread(target=self._play_worker, args=(query,), daemon=True).start()
        return True

    def _play_worker(self, query: str):
        target_url = query.strip()
        if re.match(r"^[a-zA-Z0-9_-]{11}$", target_url):
            target_url = f"https://www.youtube.com/watch?v={target_url}"
        elif not target_url.startswith("http"):
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

                self.stop_playback()

                with self._lock:
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

                    # ffmpeg에 -re(실시간 1배속 페이싱)를 적용하여 1초 만에 디코딩 완료되어 끊기는 현상 방지
                    cmd = [
                        "ffmpeg",
                        "-re",
                        "-reconnect", "1",
                        "-reconnect_streamed", "1",
                        "-reconnect_delay_max", "5",
                        "-i", stream_url,
                        "-f", "s16le",
                        "-acodec", "pcm_s16le",
                        "-ar", "44100",
                        "-ac", "2",
                        "pipe:1",
                    ]
                    logger.info(f"ffmpeg 오디오 디코더 실행: {' '.join(cmd[:6])} -> pipe:1")
                    self._ffmpeg_proc = subprocess.Popen(
                        cmd,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.DEVNULL,
                        bufsize=65536,
                    )

            # 백그라운드에서 ffmpeg stdout PCM 데이터를 직접 읽어 오디오 허브로 전송
            proc = self._ffmpeg_proc
            if proc and proc.stdout:
                while self._running:
                    chunk = proc.stdout.read(8192)
                    if not chunk:
                        break
                    if self.on_audio_chunk:
                        self.on_audio_chunk(chunk)

                proc.wait()
                logger.info(f"유튜브 오디오 스트림 재생 완료 (Exit code: {proc.returncode})")
                with self._lock:
                    if self._ffmpeg_proc == proc:
                        self.current_metadata["is_playing"] = False
                        self._ffmpeg_proc = None
                        self._notify_state()

        except Exception as e:
            logger.error(f"유튜브 오디오 재생 처리 실패: {e}")
            with self._lock:
                self.current_metadata["is_playing"] = False
                self._notify_state()
