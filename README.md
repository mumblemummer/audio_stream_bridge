# Audio Stream Bridge (Home Assistant Add-on)

스마트폰(Android / iOS / PC)의 **YouTube 순정 Cast 버튼, YouTube TV 코드 연결, iOS AirPlay, Android DLNA/UPnP** 오디오를 수신하여 Home Assistant 및 Music Assistant(MA)의 **선택한 스피커(예: Edifier ES300 2대 스테레오 페어)**로 실시간 무손실 중계하는 올인원 오디오 브릿지 애드온입니다.

---

## 🌟 주요 지원 기능

1. **YouTube 순정 Cast & TV 코드 페어링 (DIAL & Lounge 프로토콜):**
   * **순정 YouTube 앱 Cast 버튼 지원:** 안드로이드 및 아이폰의 YouTube 앱 또는 PC 크롬 YouTube 웹 플레이어에서 [Cast] 버튼을 터치하면 `Audio Stream Bridge`가 스마트 TV로 노출됩니다.
   * **12자리 TV 코드로 연결:** 대시보드에서 발급되는 12자리 TV 코드를 YouTube 앱의 **[설정 > TV로 시청 > TV 코드로 연결]**에 입력하면 멀티캐스트가 격리된 네트워크에서도 100% 즉시 페어링됩니다.
   * **`yt-dlp` + `ffmpeg` 실시간 오디오 디코더:** 유튜브 오디오 스트림을 최고 음질로 추출하여 무손실 44.1kHz 16-bit PCM으로 실시간 변환 후 스피커로 중계합니다.
2. **iOS (AirPlay 1/2 수신):**
   * `shairport-sync` 가상 렌더러를 통해 아이폰/아이패드 제어 센터에서 바로 스트리밍 수신.
3. **Android / Windows (DLNA/UPnP 수신):**
   * `upmpdcli` + `mpd`를 통해 BubbleUPnP, 삼성 뮤직 등 다양한 캐스트 앱의 오디오 수신.
4. **HA Ingress 대시보드:**
   * 별도 로그인 없이 Home Assistant 사이드바에서 즉시 접근 가능.
   * HA 내 모든 `media_player` 자동 검색, 다중 스피커 선택, 개별 볼륨 슬라이더.
   * 유튜브 URL 또는 곡명 검색어를 직접 입력해 즉시 스피커로 재생하는 퀵 플레이어 제공.
5. **다중 스피커 동기화 & Music Assistant 최적화:**
   * `http://<HA_IP>:8099/stream.wav`를 통해 2대의 Edifier ES300 또는 Music Assistant 그룹으로 딜레이 없이 스테레오 동시 출력.

---

## 🚀 Home Assistant 애드온 설치 방법

### 방법 A. 애드온 저장소 등록 후 설치 (가장 추천)

1. Home Assistant 웹 UI에서 **설정(Settings) > 애드온(Add-ons) > 애드온 스토어(Add-on Store)**로 이동합니다.
2. 우측 상단의 점 3개 메뉴(`⋮`)를 누르고 **저장소(Repositories)**를 선택합니다.
3. 아래 저장소 URL을 입력하고 **추가(Add)**를 클릭합니다:
   ```text
   https://github.com/mumblemummer/audio_stream_bridge
   ```
4. 저장소 목록을 닫고 페이지를 새로고침하면 상단에 **Audio Stream Bridge**가 나타납니다.
5. **설치(Install)**를 누릅니다 (약 2~3분 소요).
6. 설치 완료 후:
   * **시스템 시작 시 실행(Start on boot)**: 활성화
   * **사이드바에 표시(Show in sidebar)**: 활성화
7. **시작(Start)** 버튼을 누르면 모든 서비스가 기동됩니다!

---

### 방법 B. 로컬 폴더 직접 복사 (`/addons/local`)

기존 Samba Share나 SSH를 통해 수동으로 파일을 배치할 수도 있습니다:
```bash
# HA OS 호스트 또는 SSH 터미널 접속
mkdir -p /addons/local/audio_stream_bridge
# 본 리포지토리의 파일들을 위 경로에 복사
# HA 설정 > 애드온 스토어 > 저장소 새로고침 후 Local 섹션에서 설치
```

---

## 🎛️ 사용 방법

1. Home Assistant 사이드바에서 **Audio Bridge** 아이콘을 클릭합니다.
2. **동기화 출력 스피커 선택** 목록에서 소리를 출력할 스피커(예: `media_player.edifier_es300_left`, `media_player.edifier_es300_right` 또는 Music Assistant 그룹)를 체크합니다.
3. **오디오 재생하기:**
   * **방법 1 (YouTube 순정 앱):** 스마트폰 YouTube 앱 상단의 **[Cast]** 아이콘을 누르고 `Audio Stream Bridge`를 선택한 뒤 영상을 재생합니다.
   * **방법 2 (YouTube TV 코드 연결):** 스마트폰 YouTube 앱 **[설정 > TV로 시청 > TV 코드로 연결]**에 대시보드 상단에 보이는 12자리 숫자를 입력합니다.
   * **방법 3 (아이폰 AirPlay):** 제어 센터 우상단 오디오 카드를 쓸어내려 `Audio Stream Bridge`를 선택합니다.
   * **방법 4 (안드로이드 DLNA):** BubbleUPnP 등에서 `Audio Stream Bridge (DLNA)`를 출력 기기로 선택합니다.
   * **방법 5 (웹 UI 직접 입력):** 대시보드의 입력창에 원하는 유튜브 링크나 노래 제목을 넣고 [재생]을 누릅니다.

---

## 🌐 방화벽 포트 요약 (기존 설정 100% 유지)

기존 방화벽(OPNsense 등) 설정을 단 하나도 변경할 필요 없이 모든 포트가 기존과 동일하게 작동합니다:

| 프로토콜 | 포트 | 용도 |
| :--- | :---: | :--- |
| **mDNS (Avahi)** | UDP `5353` | AirPlay 기기 검색 |
| **SSDP 멀티캐스트** | UDP `1900` | DLNA 검색 & YouTube DIAL 탐색 (공용) |
| **Web UI & Live WAV & DIAL** | TCP `8099` | Ingress 대시보드, `/stream.wav`, YouTube DIAL API |
| **AirPlay RTSP & RTP** | TCP `5000`, `7000` / UDP `6000:6010` | 아이폰 무손실 오디오 수신 |
| **DLNA / UPnP** | TCP `49152` / MPD `6600` | 안드로이드 DLNA 오디오 수신 |
| **Snapcast** | TCP `1704`, `1705`, `1780` | Snapcast 무손실 서버 |
