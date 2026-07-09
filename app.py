from __future__ import annotations  # Python 3.9 미만에서 tuple[str, str] 같은 타입 힌트 문법 허용

import os
import math
import json
from datetime import datetime
from urllib.parse import quote

import streamlit as st
import streamlit.components.v1 as components
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.font_manager as fm

# ---- 그래프 한글 폰트 설정 ----
# 배포 서버(리눅스)는 matplotlib이 한 번 스캔한 폰트 목록을 캐시해두기 때문에,
# packages.txt로 폰트를 새로 설치해도 그 캐시가 갱신되지 않아 여전히 한글이
# 깨지는 경우가 있다. 그래서 폰트 '이름'으로 찾기보다, 실제 폰트 '파일 경로'를
# 직접 찾아서 fm.fontManager.addfont()로 매 실행마다 강제로 등록한다.
# (이 방식은 캐시 여부와 무관하게 항상 확실하게 동작한다.)
_KOREAN_FONT_FILE_CANDIDATES = [
    "/usr/share/fonts/truetype/nanum/NanumGothic.ttf",       # 리눅스 배포 서버 (packages.txt: fonts-nanum)
    "/usr/share/fonts/truetype/nanum/NanumBarunGothic.ttf",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "C:/Windows/Fonts/malgun.ttf",                            # Windows
    "C:/Windows/Fonts/malgunbd.ttf",
    "/System/Library/Fonts/Supplemental/AppleGothic.ttf",     # macOS
]

_registered_font_name = None
for _font_path in _KOREAN_FONT_FILE_CANDIDATES:
    if os.path.exists(_font_path):
        try:
            fm.fontManager.addfont(_font_path)
            _registered_font_name = fm.FontProperties(fname=_font_path).get_name()
            break
        except Exception:
            continue

if _registered_font_name:
    plt.rcParams["font.family"] = _registered_font_name
else:
    # 위 경로들에서 못 찾았을 때의 최후 수단: koreanize-matplotlib 패키지가
    # 설치되어 있으면 그쪽 로직에 맡긴다 (그래도 안 되면 한글이 깨질 수 있음 —
    # 이 경우 packages.txt의 fonts-nanum이 실제로 설치됐는지, 앱을 완전히
    # 재부팅(Reboot app)했는지 확인이 필요하다).
    try:
        import koreanize_matplotlib  # noqa: F401
    except ImportError:
        pass

plt.rcParams["axes.unicode_minus"] = False


# =========================================================
# 1. 샘플 데이터 (인증키가 없거나 API 실패 시 폴백으로 사용됨)
# =========================================================
def load_sample_data():
    data = [
        # place_name, category, congestion_level, weather_suitability, traffic_congestion, has_event, event_name, noise_level, lat, lon
        # ※ 발표 데모에서 목적/옵션을 바꿀 때 순위가 눈에 띄게 바뀌도록,
        #   각 장소가 뚜렷한 개성(강점/약점)을 갖도록 값을 의도적으로 다르게 설계함.
        ["광화문", "광장", 3, 3, 2, 1, "광화문 문화마당 공연", 3, 37.5759, 126.9769],
        ["홍대입구", "번화가", 5, 3, 4, 1, "홍대 버스킹 페스티벌", 5, 37.5568, 126.9236],
        ["강남역", "번화가", 4, 2, 1, 0, "", 4, 37.4979, 127.0276],
        ["성수", "골목상권", 3, 3, 3, 1, "성수 팝업 아트마켓", 3, 37.5445, 127.0559],
        ["여의도한강공원", "공원", 3, 5, 3, 1, "여의도 봄꽃축제", 3, 37.5283, 126.9336],
        ["잠실", "복합시설", 3, 4, 2, 1, "롯데월드타워 야외공연", 4, 37.5133, 127.1028],
        ["명동", "번화가", 5, 3, 2, 1, "명동 크리스마스 마켓", 5, 37.5636, 126.9850],
        ["이태원", "번화가", 4, 3, 3, 1, "이태원 지구촌축제", 4, 37.5347, 126.9947],
        ["서울숲", "공원", 1, 5, 3, 0, "", 2, 37.5443, 127.0374],
        ["북촌한옥마을", "전통마을", 2, 5, 3, 0, "", 1, 37.5826, 126.9831],
    ]
    columns = [
        "place_name", "category", "congestion_level", "weather_suitability",
        "traffic_congestion", "has_event", "event_name", "noise_level",
        "latitude", "longitude",
    ]
    return pd.DataFrame(data, columns=columns)


# =========================================================
# 2-1. 서울시 실시간 도시데이터(citydata) API 연동
#
# ⚠️ 주의: AREA_NM_MAP의 값(공식 장소명)과 파싱 필드명(AREA_CONGEST_LVL 등)은
#    서울 열린데이터광장에서 내려받는 '실시간 도시데이터 매뉴얼.pdf' /
#    '서울시 주요 121장소 목록.xlsx' 로 반드시 한 번 대조 확인해야 합니다.
#    실제 응답 필드명이 다르면 parse_* 함수만 고치면 되도록 구조를 분리해뒀습니다.
# =========================================================
SEOUL_CITYDATA_BASE_URL = "http://openapi.seoul.go.kr:8088"

# 우리 샘플 장소명 -> 서울시 공식 AREA_NM (추정치 · 반드시 xlsx로 재확인!)
AREA_NM_MAP = {
    "광화문": "광화문·덕수궁",       # 샘플키로도 조회 가능한 유일한 장소
    "홍대입구": "홍대 관광특구",
    "강남역": "강남역",
    "성수": "성수카페거리",
    "여의도한강공원": "여의도한강공원",
    "잠실": "잠실 관광특구",
    "명동": "명동 관광특구",
    "이태원": "이태원 관광특구",
    "서울숲": "서울숲공원",
    "북촌한옥마을": "북촌한옥마을",
}

# 서울시 응답의 4단계 혼잡도 문구 -> 우리 앱의 1~5 congestion_level 스케일로 변환
CONGESTION_TEXT_TO_LEVEL = {
    "여유": 1,
    "보통": 2,
    "약간 붐빔": 4,
    "붐빔": 5,
}


@st.cache_data(ttl=300, show_spinner=False)
def fetch_seoul_citydata_raw(area_nm: str, api_key: str) -> dict | None:
    """서울시 실시간 도시데이터 citydata API를 호출한다.
    실패하거나 형식이 예상과 다르면 None을 반환해서, 호출부가 안전하게 샘플 값으로 대체하도록 한다.
    5분(ttl=300초) 캐싱을 걸어, 위젯을 조작할 때마다 매번 API를 재호출하지 않도록 한다."""
    import requests  # 이 함수 안에서만 필요하므로 지역 import (requests 미설치 환경에서도 앱 자체는 뜨도록)

    url = f"{SEOUL_CITYDATA_BASE_URL}/{api_key}/json/citydata/1/5/{quote(area_nm)}"
    try:
        res = requests.get(url, timeout=5)
        res.raise_for_status()
        payload = res.json()
    except Exception:
        return None

    if not isinstance(payload, dict):
        return None

    # 최상위 또는 CITYDATA 내부에 RESULT.CODE가 있고 'ERROR'가 포함되면 명백한 실패로 처리
    # (샘플키로 권한 없는 장소를 조회하면 이런 식의 에러 코드가 오는 경우가 많음)
    for result_holder in (payload, payload.get("CITYDATA") or {}):
        result = result_holder.get("RESULT") if isinstance(result_holder, dict) else None
        if isinstance(result, dict):
            code = str(result.get("CODE", "")).upper()
            if "ERROR" in code:
                return None

    city_data = payload.get("CITYDATA")
    if not city_data:
        return None
    return city_data


def parse_live_congestion_level(city_data: dict) -> int | None:
    """LIVE_PPLTN_STTS[0].AREA_CONGEST_LVL 문구를 1~5 스케일로 변환."""
    try:
        pop_info = city_data["LIVE_PPLTN_STTS"][0]
        lvl_text = pop_info.get("AREA_CONGEST_LVL")
        return CONGESTION_TEXT_TO_LEVEL.get(lvl_text)
    except (KeyError, IndexError, TypeError):
        return None


def parse_live_weather(city_data: dict) -> dict | None:
    """WEATHER_STTS[0]에서 기온/강수 메시지를 뽑아온다. 실패하면 None."""
    try:
        w = city_data["WEATHER_STTS"][0]
        return {
            "temp": w.get("TEMP"),
            "precip_msg": w.get("PCP_MSG"),
        }
    except (KeyError, IndexError, TypeError):
        return None


def parse_live_events(city_data: dict) -> list[str]:
    """EVENT_STTS에서 진행 중인 행사명 목록을 뽑아온다. 실패하면 빈 리스트."""
    try:
        events = city_data.get("EVENT_STTS") or []
        return [e.get("EVENT_NM", "").strip() for e in events if e.get("EVENT_NM")]
    except (TypeError, AttributeError):
        return []


def apply_live_seoul_data(sample_df: pd.DataFrame, api_key: str) -> tuple[pd.DataFrame, dict]:
    """샘플 데이터프레임을 기준으로, 매핑된 장소마다 실시간 API를 호출해서
    congestion_level(그리고 가능하면 행사 정보)을 실제 값으로 덮어쓴다.
    '성공'은 응답이 온 것만으로 판정하지 않고, 실제로 쓸 수 있는 값을 하나라도
    뽑아냈을 때만 인정한다 (그래야 권한 없는 장소를 '성공'으로 잘못 세지 않는다).
    반환값: (갱신된 df, {"success": [...], "failed": [...]} 상태 딕셔너리)"""
    df = sample_df.copy()
    status = {"success": [], "failed": []}

    for idx, row in df.iterrows():
        place_name = row["place_name"]
        area_nm = AREA_NM_MAP.get(place_name)
        if not area_nm:
            status["failed"].append(place_name)
            continue

        city_data = fetch_seoul_citydata_raw(area_nm, api_key)
        if city_data is None:
            status["failed"].append(place_name)
            continue

        congestion = parse_live_congestion_level(city_data)
        events = parse_live_events(city_data)

        if congestion is None and not events:
            # 응답은 왔지만 우리가 원하는 필드를 하나도 못 뽑아낸 경우 -> 실패로 처리
            status["failed"].append(place_name)
            continue

        if congestion is not None:
            df.at[idx, "congestion_level"] = congestion
        if events:
            df.at[idx, "has_event"] = 1
            df.at[idx, "event_name"] = events[0]

        status["success"].append(place_name)

    return df, status





# =========================================================
# 2. 다국어 지원용 매핑 (장소명 / 분류 / 행사명 / 목적)
# =========================================================
PLACE_NAME_EN = {
    "광화문": "Gwanghwamun",
    "홍대입구": "Hongdae",
    "강남역": "Gangnam Station",
    "성수": "Seongsu",
    "여의도한강공원": "Yeouido Hangang Park",
    "잠실": "Jamsil",
    "명동": "Myeongdong",
    "이태원": "Itaewon",
    "서울숲": "Seoul Forest",
    "북촌한옥마을": "Bukchon Hanok Village",
}

CATEGORY_EN = {
    "광장": "Plaza",
    "번화가": "Downtown",
    "골목상권": "Alley Market",
    "공원": "Park",
    "복합시설": "Complex",
    "전통마을": "Traditional Village",
}

EVENT_NAME_EN = {
    "광화문 문화마당 공연": "Gwanghwamun Culture Square Performance",
    "홍대 버스킹 페스티벌": "Hongdae Busking Festival",
    "성수 팝업 아트마켓": "Seongsu Pop-up Art Market",
    "여의도 봄꽃축제": "Yeouido Spring Flower Festival",
    "롯데월드타워 야외공연": "Lotte World Tower Outdoor Performance",
    "이태원 지구촌축제": "Itaewon Global Village Festival",
    "명동 크리스마스 마켓": "Myeongdong Christmas Market",
}

FACTOR_LABELS = {
    "congestion": {"ko": "혼잡도", "en": "Congestion"},
    "weather": {"ko": "날씨", "en": "Weather"},
    "traffic": {"ko": "교통", "en": "Traffic"},
    "noise": {"ko": "소음", "en": "Noise"},
    "event": {"ko": "행사", "en": "Event"},
}

STATUS_TEXT = {
    "ok": {"ko": "여유", "en": "Comfortable"},
    "mid": {"ko": "보통", "en": "Moderate"},
    "bad": {"ko": "혼잡", "en": "Crowded"},
}
STATUS_DOT_COLOR = {"ok": "#4C8B5A", "mid": "#B98A2E", "bad": "#B24A4A"}

EVENT_BADGE_TEXT = {
    "active": {"ko": "행사 진행중", "en": "Event Ongoing"},
    "inactive": {"ko": "행사 없음", "en": "No Event"},
}


# =========================================================
# 3. 목적/옵션별 기본 가중치 및 설명 (한/영)
#    - ACTIVITY: "방문 목적" — 실제 방문 행위 (산책, 데이트 등)
#    - OPTION: "선호 옵션" — 목적이라기보다 조건에 가까운 선택 (조용히, 혼잡 회피 등)
# =========================================================
ACTIVITY_KEYS = ["산책하기", "데이트/나들이", "문화생활", "맛집 탐방", "쇼핑", "야경 감상"]
OPTION_KEYS = [
    "조용히 쉬기", "사람 많은 곳 피하기", "이동이 편한 곳", "여유로운 분위기",
    "핫플레이스 위주", "실내 위주",
]

PURPOSE_WEIGHTS = {
    # --- 방문 목적 (활동) ---
    "산책하기":     {"congestion": 0.20, "weather": 0.35, "traffic": 0.10, "noise": 0.20, "event": 0.15},
    "데이트/나들이": {"congestion": 0.15, "weather": 0.25, "traffic": 0.15, "noise": 0.10, "event": 0.35},
    "문화생활":     {"congestion": 0.10, "weather": 0.10, "traffic": 0.20, "noise": 0.10, "event": 0.50},
    "맛집 탐방":    {"congestion": 0.20, "weather": 0.10, "traffic": 0.30, "noise": 0.15, "event": 0.25},
    "쇼핑":        {"congestion": 0.15, "weather": 0.10, "traffic": 0.30, "noise": 0.15, "event": 0.30},
    "야경 감상":    {"congestion": 0.25, "weather": 0.30, "traffic": 0.10, "noise": 0.15, "event": 0.20},
    # --- 선호 옵션 (조건) ---
    "조용히 쉬기":        {"congestion": 0.35, "weather": 0.15, "traffic": 0.10, "noise": 0.35, "event": 0.05},
    "사람 많은 곳 피하기": {"congestion": 0.45, "weather": 0.10, "traffic": 0.10, "noise": 0.35, "event": 0.00},
    "이동이 편한 곳":     {"congestion": 0.15, "weather": 0.10, "traffic": 0.50, "noise": 0.15, "event": 0.10},
    "여유로운 분위기":    {"congestion": 0.30, "weather": 0.25, "traffic": 0.10, "noise": 0.30, "event": 0.05},
    "핫플레이스 위주":    {"congestion": 0.05, "weather": 0.15, "traffic": 0.25, "noise": 0.15, "event": 0.40},
    "실내 위주":         {"congestion": 0.20, "weather": 0.05, "traffic": 0.30, "noise": 0.25, "event": 0.20},
}

PURPOSE_LABEL_EN = {
    "산책하기": "Walking",
    "데이트/나들이": "Date / Outing",
    "문화생활": "Culture",
    "맛집 탐방": "Food Tour",
    "쇼핑": "Shopping",
    "야경 감상": "Night View",
    "조용히 쉬기": "Quiet Rest",
    "사람 많은 곳 피하기": "Avoid Crowds",
    "이동이 편한 곳": "Easy Access",
    "여유로운 분위기": "Relaxed Vibe",
    "핫플레이스 위주": "Trendy Hotspots",
    "실내 위주": "Indoor-Focused",
}

PURPOSE_DESCRIPTIONS = {
    "ko": {
        "산책하기": "날씨·환경 적합도를 가장 중요하게 보고, 혼잡하지 않은 곳을 함께 고려해요.",
        "데이트/나들이": "분위기 좋은 날씨와 즐길거리(행사)가 있는 곳을 균형 있게 반영해요.",
        "문화생활": "진행 중인 문화행사와 접근성(교통)을 가장 중요하게 반영해요.",
        "맛집 탐방": "이동이 편하고 즐길 거리(행사·팝업)가 있는 곳을 우선으로 봐요.",
        "쇼핑": "접근성과 진행 중인 팝업·행사를 중요하게 반영해요.",
        "야경 감상": "날씨가 맑고 혼잡하지 않은, 야경 보기 좋은 곳을 찾아드려요.",
        "조용히 쉬기": "혼잡도와 소음이 낮은 장소를 우선으로 추천해요. 잠시 숨 돌리기 좋은 곳을 찾아드려요.",
        "사람 많은 곳 피하기": "혼잡도와 소음이 낮은 곳을 최우선으로, 한산한 장소를 찾아드려요.",
        "이동이 편한 곳": "교통 혼잡도가 낮아 이동이 수월한 곳을 우선으로 반영해요.",
        "여유로운 분위기": "혼잡도·소음이 낮고 날씨도 좋은, 전반적으로 여유로운 곳을 찾아드려요.",
        "핫플레이스 위주": "혼잡함은 크게 신경 쓰지 않고, 지금 화제인 행사·이벤트가 있는 활기찬 곳을 우선으로 찾아드려요.",
        "실내 위주": "날씨에 구애받지 않는 실내 중심 장소를, 접근성 좋은 곳 위주로 찾아드려요.",
    },
    "en": {
        "산책하기": "Focuses most on weather suitability, while also avoiding crowded spots.",
        "데이트/나들이": "Balances pleasant weather with fun things to do, like ongoing events.",
        "문화생활": "Weighs ongoing cultural events and transit access the most.",
        "맛집 탐방": "Prioritizes easy access and places with something extra going on.",
        "쇼핑": "Weighs accessibility and ongoing pop-ups or events.",
        "야경 감상": "Finds places with clear weather and low crowds — great for night views.",
        "조용히 쉬기": "Prioritizes places with low congestion and noise — perfect for catching your breath.",
        "사람 많은 곳 피하기": "Puts low congestion and low noise first, to find the calmest spots.",
        "이동이 편한 곳": "Prioritizes low traffic congestion, for easy access.",
        "여유로운 분위기": "Looks for low congestion, low noise, and good weather overall.",
        "핫플레이스 위주": "Doesn't mind the crowds — prioritizes lively spots with buzz-worthy events happening now.",
        "실내 위주": "Finds indoor-friendly places unaffected by weather, with good access.",
    },
}

PURPOSE_ICONS = {
    "산책하기": "🚶",
    "데이트/나들이": "💑",
    "문화생활": "🎭",
    "맛집 탐방": "🍽️",
    "쇼핑": "🛍️",
    "야경 감상": "🌃",
    "조용히 쉬기": "🧘",
    "사람 많은 곳 피하기": "🙅",
    "이동이 편한 곳": "🚗",
    "여유로운 분위기": "🌿",
    "핫플레이스 위주": "🔥",
    "실내 위주": "🏢",
}



# =========================================================
# 4. UI 고정 문구 (한/영)
# =========================================================
TEXTS = {
    "ko": {
        "hero_subtitle": "서울 실시간 도시데이터를 바탕으로, 방문 목적에 맞는 장소를 골라드려요.",
        "hero_caption": "",
        "live_badge": "실시간 서울 데이터",
        "sidebar_lang_header": "🌐 언어",
        "sidebar_purpose_header": "🎯 방문 목적 선택",
        "sidebar_purpose_question": "오늘 서울 방문의 목적은 무엇인가요?",
        "sidebar_option_header": "🧭 선호 옵션 (선택)",
        "sidebar_option_question": "특별히 원하는 조건이 있나요?",
        "option_none": "선택 안 함",
        "weight_expander_title": "⚖️ 가중치 직접 조정",
        "weight_caption": "각 항목을 얼마나 중요하게 볼지 %로 조정해보세요. 합계는 자동으로 정규화됩니다.",
        "filter_expander_title": "🎚️ 세부 조건 필터",
        "filter_congestion": "혼잡도 이 값 이하만 보기",
        "filter_noise": "소음 이 값 이하만 보기",
        "filter_traffic": "교통혼잡도 이 값 이하만 보기",
        "filter_weather": "날씨 적합도 이 값 이상만 보기",
        "filter_event_only": "행사 진행 중인 곳만 보기",
        "favorites_header": "❤️ 찜한 장소",
        "favorites_empty": "아직 찜한 장소가 없어요. 카드 아래 버튼으로 추가해보세요.",
        "favorites_reset": "찜 목록 초기화",
        "live_data_header": "🔴 실시간 데이터 연동",
        "live_data_key_label": "서울시 Open API 인증키",
        "live_data_key_placeholder": "발급받은 인증키를 입력하세요 (샘플키: sample)",
        "live_data_toggle_label": "실시간 데이터 사용",
        "live_data_caption": "인증키를 입력하고 켜면, 혼잡도·행사 정보를 서울시 실시간 데이터로 덮어씁니다.",
        "live_data_status": "🔴 실시간 반영 {success}곳 · 샘플로 대체 {failed}곳",
        "live_data_no_key": "인증키를 입력해야 실시간 데이터를 쓸 수 있어요.",
        "result_count": "전체 {total}곳 중 조건에 맞는 장소 {filtered}곳",
        "data_timestamp": "⏱ 데이터 기준: {time}",
        "top5_subheader": "지금 가장 추천하는 곳 TOP 5",
        "fav_button_add": "♡ 찜하기",
        "fav_button_remove": "♥ 찜 해제",
        "fav_mark": "♥ 찜한 장소",
        "detail_expander": "점수 상세 보기",
        "empty_result_warning": "조건에 맞는 장소가 없어요. 왼쪽 필터 조건을 완화해보세요.",
        "chart_subheader": "전체 장소별 추천 점수 비교",
        "chart_empty": "표시할 데이터가 없어요.",
        "chart_xlabel": "추천 점수",
        "chart_legend": "추천점수",
        "table_expander": "전체 장소 상세 데이터 보기",
        "table_columns": {
            "place_name": "장소명", "category": "분류",
            "congestion_level": "혼잡도(1~5)", "weather_suitability": "날씨적합도(1~5)",
            "traffic_congestion": "교통혼잡도(1~5)", "noise_level": "소음(1~5)",
            "has_event": "행사여부", "event_name": "행사명",
            "recommendation_score": "추천점수",
        },
        # --- 장소 상세 화면 ---
        "detail_button": "🔎 상세보기",
        "detail_back": "← 목록으로 돌아가기",
        "detail_hourly_header": "⏱️ 시간대별 혼잡도 추이 (예시 추정치)",
        "detail_hourly_caption": "실시간 API 연동 전까지는 현재 혼잡도를 바탕으로 만든 참고용 추정 곡선입니다.",
        "detail_hourly_header_live": "⏱️ 시간대별 혼잡도 추이 (실시간 기반 추정치)",
        "detail_hourly_caption_live": "실시간 API로 가져온 현재 혼잡도를 기준으로 계산한 참고용 추정 곡선입니다.",
        "detail_similar_header": "🔁 비슷한 분위기의 다른 장소",
        "detail_similar_empty": "같은 분류의 다른 장소가 없어요.",
        "detail_rating_header": "⭐ 방문 후기 남기기",
        "detail_rating_score_label": "만족도",
        "detail_rating_comment_label": "한 줄 후기 (선택)",
        "detail_rating_save": "후기 저장",
        "detail_rating_saved": "저장된 후기",
        # --- 코스 만들기 ---
        "course_add_button": "➕ 코스에 추가",
        "course_remove_button": "− 코스에서 빼기",
        "course_header": "🧭 오늘의 코스",
        "course_caption": "추천 카드에서 '코스에 추가'를 눌러 여러 장소를 하나의 동선으로 묶어보세요.",
        "course_empty": "아직 코스에 담은 장소가 없어요.",
        "course_sort_button": "📍 동선 순서로 정렬",
        "course_reset_button": "코스 초기화",
        "course_distance_label": "총 이동 거리(직선 기준) 약",
        "course_stop_label": "번째 코스",
        # --- 하루 일정 추천 ---
        "schedule_header": "🕐 하루 일정 추천",
        "schedule_caption": "시간대별로 어울리는 목적을 적용해 하루 동선을 자동으로 짜봤어요.",
        "schedule_slots": {"오전": "산책하기", "점심": "맛집 탐방", "오후": "문화생활", "저녁": "야경 감상"},
        # --- 찜 기반 맞춤 추천 ---
        "personalized_header": "🎯 회원님을 위한 맞춤 추천",
        "personalized_caption": "찜한 장소를 바탕으로 비슷한 분류의 장소를 더 찾아봤어요.",
        # --- 공유/내보내기 ---
        "share_header": "📤 추천 결과 공유하기",
        "share_caption": "아래 코드 블록 오른쪽 상단 복사 아이콘으로 바로 복사하거나, 파일로 내려받을 수 있어요.",
        "share_download_button": "텍스트 파일로 내려받기",
        "share_source_course": "(오늘의 코스 기준)",
        "share_source_top5": "(TOP 5 기준)",
    },
    "en": {
        "hero_subtitle": "Based on Seoul's real-time city data, we pick the right spot for your visit.",
        "hero_caption": "",
        "live_badge": "LIVE SEOUL DATA",
        "sidebar_lang_header": "🌐 Language",
        "sidebar_purpose_header": "🎯 Choose Your Purpose",
        "sidebar_purpose_question": "What's the purpose of your Seoul visit today?",
        "sidebar_option_header": "🧭 Preferences (optional)",
        "sidebar_option_question": "Any specific conditions you'd like?",
        "option_none": "None",
        "weight_expander_title": "⚖️ Adjust Weights",
        "weight_caption": "Adjust how much each factor matters, in %. The total is auto-normalized.",
        "filter_expander_title": "🎚️ Detailed Filters",
        "filter_congestion": "Show only congestion at or below",
        "filter_noise": "Show only noise at or below",
        "filter_traffic": "Show only traffic congestion at or below",
        "filter_weather": "Show only weather suitability at or above",
        "filter_event_only": "Show only places with an ongoing event",
        "favorites_header": "❤️ Favorites",
        "favorites_empty": "No favorites yet. Add one using the button under each card.",
        "favorites_reset": "Clear Favorites",
        "live_data_header": "🔴 Live Data Integration",
        "live_data_key_label": "Seoul Open API Key",
        "live_data_key_placeholder": "Enter your API key (sample key: sample)",
        "live_data_toggle_label": "Use Live Data",
        "live_data_caption": "Enter your key and turn this on to overwrite congestion/event info with Seoul's live data.",
        "live_data_status": "🔴 Live for {success} places · Fallback to sample for {failed}",
        "live_data_no_key": "Enter an API key to use live data.",
        "result_count": "{filtered} of {total} places match your filters",
        "data_timestamp": "⏱ Data as of: {time}",
        "top5_subheader": "Top 5 Recommended Right Now",
        "fav_button_add": "♡ Add to Favorites",
        "fav_button_remove": "♥ Remove Favorite",
        "fav_mark": "♥ Favorited",
        "detail_expander": "View Score Details",
        "empty_result_warning": "No places match your filters. Try loosening them on the left.",
        "chart_subheader": "Recommendation Score by Place",
        "chart_empty": "No data to show.",
        "chart_xlabel": "Recommendation Score",
        "chart_legend": "Score",
        "table_expander": "View Full Place Data",
        "table_columns": {
            "place_name": "Place", "category": "Category",
            "congestion_level": "Congestion (1-5)", "weather_suitability": "Weather Fit (1-5)",
            "traffic_congestion": "Traffic (1-5)", "noise_level": "Noise (1-5)",
            "has_event": "Event", "event_name": "Event Name",
            "recommendation_score": "Score",
        },
        # --- Place detail view ---
        "detail_button": "🔎 View Details",
        "detail_back": "← Back to list",
        "detail_hourly_header": "⏱️ Hourly Congestion Trend (illustrative estimate)",
        "detail_hourly_caption": "Until live API data is connected, this is a reference curve estimated from the current congestion level.",
        "detail_hourly_header_live": "⏱️ Hourly Congestion Trend (based on live data)",
        "detail_hourly_caption_live": "A reference curve calculated from the current congestion level pulled from the live API.",
        "detail_similar_header": "🔁 Similar Places",
        "detail_similar_empty": "No other places in the same category.",
        "detail_rating_header": "⭐ Leave a Review",
        "detail_rating_score_label": "Satisfaction",
        "detail_rating_comment_label": "Short comment (optional)",
        "detail_rating_save": "Save Review",
        "detail_rating_saved": "Saved review",
        # --- Course builder ---
        "course_add_button": "➕ Add to Course",
        "course_remove_button": "− Remove from Course",
        "course_header": "🧭 Today's Course",
        "course_caption": "Tap 'Add to Course' on any card to combine several places into one route.",
        "course_empty": "No places added to your course yet.",
        "course_sort_button": "📍 Sort by Route Order",
        "course_reset_button": "Clear Course",
        "course_distance_label": "Total straight-line distance ~",
        "course_stop_label": "Stop",
        # --- Daily schedule ---
        "schedule_header": "🕐 Suggested Daily Schedule",
        "schedule_caption": "We auto-built a day plan by applying a fitting purpose to each time slot.",
        "schedule_slots": {"Morning": "산책하기", "Lunch": "맛집 탐방", "Afternoon": "문화생활", "Evening": "야경 감상"},
        # --- Favorites-based picks ---
        "personalized_header": "🎯 Picked For You",
        "personalized_caption": "Based on your favorites, here are more places in a similar category.",
        # --- Share / export ---
        "share_header": "📤 Share Your Recommendations",
        "share_caption": "Use the copy icon on the code block below, or download it as a file.",
        "share_download_button": "Download as text file",
        "share_source_course": "(based on your course)",
        "share_source_top5": "(based on Top 5)",
    },
}


# =========================================================
# 5. 점수 계산 (커스텀 가중치 반영 + 항목별 기여도 계산)
# =========================================================
def calculate_scores(df: pd.DataFrame, weights: dict) -> pd.DataFrame:
    df = df.copy()

    df["congestion_suitability"] = 6 - df["congestion_level"]
    df["traffic_suitability"] = 6 - df["traffic_congestion"]
    df["noise_suitability"] = 6 - df["noise_level"]
    df["event_suitability"] = df["has_event"] * 5

    df["contrib_congestion"] = df["congestion_suitability"] * weights["congestion"] / 5 * 100
    df["contrib_weather"] = df["weather_suitability"] * weights["weather"] / 5 * 100
    df["contrib_traffic"] = df["traffic_suitability"] * weights["traffic"] / 5 * 100
    df["contrib_noise"] = df["noise_suitability"] * weights["noise"] / 5 * 100
    df["contrib_event"] = df["event_suitability"] * weights["event"] / 5 * 100

    df["recommendation_score"] = (
        df["contrib_congestion"] + df["contrib_weather"] + df["contrib_traffic"]
        + df["contrib_noise"] + df["contrib_event"]
    ).round(1)

    return df


# =========================================================
# 6. 추천 이유 자연어 생성 (한/영)
# =========================================================
def generate_reason(row: pd.Series, purpose: str, lang: str, option: str | None = None) -> str:
    reasons_ko, reasons_en = [], []

    if row["congestion_level"] <= 2:
        reasons_ko.append("혼잡도가 낮아 여유롭게 즐길 수 있어요")
        reasons_en.append("low congestion means you can enjoy it at ease")
    elif row["congestion_level"] >= 4 and option == "사람 많은 곳 피하기":
        reasons_ko.append("다만 현재 혼잡도는 다소 높은 편이에요")
        reasons_en.append("though congestion is currently a bit high")

    if row["weather_suitability"] >= 4:
        reasons_ko.append("야외활동을 하기 좋은 환경이에요")
        reasons_en.append("great conditions for outdoor activity")

    if row["noise_level"] <= 2 and option == "조용히 쉬기":
        reasons_ko.append("소음이 적어 조용히 쉬기에 적합해요")
        reasons_en.append("low noise makes it great for quiet rest")

    if row["traffic_congestion"] <= 2:
        reasons_ko.append("교통 혼잡도가 낮아 이동이 수월해요")
        reasons_en.append("low traffic congestion means easy access")

    if row["has_event"] == 1 and purpose in ["문화생활", "데이트/나들이", "맛집 탐방", "쇼핑"]:
        event_ko = row["event_name"]
        event_en = EVENT_NAME_EN.get(row["event_name"], row["event_name"])
        reasons_ko.append(f"'{event_ko}' 행사가 진행 중이라 즐길 거리가 많아요")
        reasons_en.append(f"the '{event_en}' event is happening, so there's plenty to enjoy")

    if not reasons_ko:
        reasons_ko.append("전반적으로 무난한 조건을 갖춘 장소예요")
        reasons_en.append("overall a solid, well-rounded choice")

    if lang == "en":
        return "; ".join(reasons_en).capitalize() + "."
    return " · ".join(reasons_ko) + "."


# =========================================================
# 7. 배지 / 카드 / breakdown 렌더링 헬퍼
# =========================================================
def level_status(value: int, low_is_good: bool) -> str:
    score = (6 - value) if low_is_good else value
    if score >= 4:
        return "ok"
    elif score == 3:
        return "mid"
    else:
        return "bad"


def render_badge(factor_key: str, status_code: str, lang: str) -> str:
    dot_color = STATUS_DOT_COLOR.get(status_code, "#9E9E9E")
    label = FACTOR_LABELS[factor_key][lang]
    status_text = STATUS_TEXT[status_code][lang]
    return (
        f"<span class='badge'>"
        f"<span class='badge-dot' style='background:{dot_color};'></span>"
        f"{label} {status_text}</span>"
    )


def render_event_badge(has_event: bool, lang: str) -> str:
    if has_event:
        return f"<span class='badge badge-event'>{EVENT_BADGE_TEXT['active'][lang]}</span>"
    return f"<span class='badge badge-muted'>{EVENT_BADGE_TEXT['inactive'][lang]}</span>"


def display_place_name(place_name: str, lang: str) -> str:
    return PLACE_NAME_EN.get(place_name, place_name) if lang == "en" else place_name


def display_category(category: str, lang: str) -> str:
    return CATEGORY_EN.get(category, category) if lang == "en" else category


def render_card(rank_no: int, row: pd.Series, purpose: str, is_favorite: bool, lang: str, option: str | None = None) -> str:
    badges_html = (
        render_badge("congestion", level_status(row["congestion_level"], low_is_good=True), lang)
        + render_badge("weather", level_status(row["weather_suitability"], low_is_good=False), lang)
        + render_badge("traffic", level_status(row["traffic_congestion"], low_is_good=True), lang)
        + render_badge("noise", level_status(row["noise_level"], low_is_good=True), lang)
        + render_event_badge(row["has_event"] == 1, lang)
    )

    reason_text = generate_reason(row, purpose, lang, option)
    score = row["recommendation_score"]
    score_pct = max(0, min(100, score))
    fav_mark = f"<span class='fav-mark'>{TEXTS[lang]['fav_mark']}</span>" if is_favorite else ""
    name_text = display_place_name(row["place_name"], lang)
    category_text = display_category(row["category"], lang)

    # 주의: Markdown은 한 줄이 4칸 이상 들여쓰기 되어 있으면 코드블록으로 해석하므로,
    # 아래 HTML은 줄마다 들여쓰기 없이(왼쪽 정렬) 이어붙인다.
    html_lines = [
        "<div class='place-card'>",
        f"<div class='place-card-rank'>{rank_no}</div>",
        "<div class='place-card-main'>",
        f"<div class='place-card-title'>{name_text} "
        f"<span class='place-card-category'>{category_text}</span> {fav_mark}</div>",
        f"<div class='place-card-badges'>{badges_html}</div>",
        f"<div class='place-card-reason'>{reason_text}</div>",
        "</div>",
        "<div class='place-card-score'>",
        f"<div class='score-number'>{score:.0f}</div>",
        "<div class='score-max'>/ 100</div>",
        "<div class='score-bar-bg'>",
        f"<div class='score-bar-fill' style='width:{score_pct}%;'></div>",
        "</div>",
        "</div>",
        "</div>",
    ]
    return "".join(html_lines)


def render_breakdown(row: pd.Series, lang: str) -> str:
    """항목별 점수 기여도를 미니 바 형태로 보여준다."""
    factors = [
        (FACTOR_LABELS["congestion"][lang], row["contrib_congestion"]),
        (FACTOR_LABELS["weather"][lang], row["contrib_weather"]),
        (FACTOR_LABELS["traffic"][lang], row["contrib_traffic"]),
        (FACTOR_LABELS["noise"][lang], row["contrib_noise"]),
        (FACTOR_LABELS["event"][lang], row["contrib_event"]),
    ]
    max_contrib = max(v for _, v in factors) or 1
    rows_html = ""
    for name, value in factors:
        pct = max(2, value / max_contrib * 100)
        point_unit = "점" if lang == "ko" else "pt"
        rows_html += (
            "<div class='breakdown-row'>"
            f"<div class='breakdown-label'>{name}</div>"
            "<div class='breakdown-bar-bg'>"
            f"<div class='breakdown-bar-fill' style='width:{pct}%;'></div>"
            "</div>"
            f"<div class='breakdown-value'>{value:.1f}{point_unit}</div>"
            "</div>"
        )
    return f"<div class='breakdown-box'>{rows_html}</div>"


def rerun_app():
    """Streamlit 버전에 따라 st.rerun() 또는 st.experimental_rerun()을 사용한다."""
    if hasattr(st, "rerun"):
        st.rerun()
    elif hasattr(st, "experimental_rerun"):
        st.experimental_rerun()


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """두 좌표 사이의 직선 거리(km)를 계산한다."""
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def find_similar_places(df: pd.DataFrame, place_name: str, n: int = 3) -> pd.DataFrame:
    """같은 분류(category)의 다른 장소를 점수순으로 최대 n개 반환한다."""
    target = df[df["place_name"] == place_name]
    if target.empty:
        return df.head(0)
    category = target.iloc[0]["category"]
    candidates = df[(df["category"] == category) & (df["place_name"] != place_name)]
    return candidates.sort_values("recommendation_score", ascending=False).head(n)


def synthetic_hourly_congestion(base_level: int) -> list[tuple[int, float]]:
    """실시간 API 연동 전까지, 현재 혼잡도를 기준으로 그럴듯한 시간대별 추이를 만들어낸다.
    실제 데이터가 아닌 예시용 추정치임을 화면에 함께 표기한다."""
    hours = list(range(8, 23))
    curve = []
    for h in hours:
        # 점심(12~13시), 저녁(18~20시)에 완만하게 더 붐비는 곡선
        bump = 0.9 * math.exp(-((h - 12.5) ** 2) / 6) + 1.1 * math.exp(-((h - 19) ** 2) / 8)
        value = base_level + bump - 0.6
        curve.append((h, max(1.0, min(5.0, value))))
    return curve


def build_share_text(rows: list[pd.Series], purpose_label: str, lang: str) -> str:
    """추천 결과를 복사/다운로드하기 좋은 일반 텍스트로 만든다."""
    lines = []
    title = "Seoul, now — 추천 결과" if lang == "ko" else "Seoul, now — Recommendations"
    lines.append(title)
    lines.append(f"({purpose_label})")
    lines.append("")
    for i, row in enumerate(rows, start=1):
        name = display_place_name(row["place_name"], lang)
        score_unit = "점" if lang == "ko" else "pts"
        lines.append(f"{i}. {name} — {row['recommendation_score']:.0f}{score_unit}")
    return "\n".join(lines)


def render_navigation_links(place_name_display: str, lat: float, lon: float, lang: str) -> str:
    """카카오맵/구글맵 길찾기로 바로 연결되는 링크를 만든다 (외부 API 키 불필요)."""
    encoded_name = quote(place_name_display)
    kakao_url = f"https://map.kakao.com/link/to/{encoded_name},{lat},{lon}"
    google_url = f"https://www.google.com/maps/dir/?api=1&destination={lat},{lon}"
    kakao_label = "🧭 카카오맵 길찾기" if lang == "ko" else "🧭 Kakao Map Directions"
    google_label = "🌐 Google 지도 길찾기" if lang == "ko" else "🌐 Google Maps Directions"
    return (
        "<div class='nav-links'>"
        f"<a href='{kakao_url}' target='_blank' rel='noopener noreferrer' class='nav-link-btn'>{kakao_label}</a>"
        f"<a href='{google_url}' target='_blank' rel='noopener noreferrer' class='nav-link-btn'>{google_label}</a>"
        "</div>"
    )


WEATHER_LABELS = {
    5: {"ko": ("☀️", "맑음"), "en": ("☀️", "Clear")},
    4: {"ko": ("🌤️", "대체로 맑음"), "en": ("🌤️", "Mostly Clear")},
    3: {"ko": ("☁️", "흐림"), "en": ("☁️", "Cloudy")},
    2: {"ko": ("🌦️", "가끔 비"), "en": ("🌦️", "Light Rain")},
    1: {"ko": ("🌧️", "비"), "en": ("🌧️", "Rain")},
}


def render_weather_line(weather_suitability: int, lang: str) -> str:
    """야외활동 적합도 점수를 실감나는 날씨 상태 문구로 보여준다 (실시간 날씨 API 연동 전까지 예시 표기)."""
    icon, label = WEATHER_LABELS.get(int(weather_suitability), WEATHER_LABELS[3])[lang]
    note = "예시 표기, 실시간 날씨 연동 예정" if lang == "ko" else "illustrative, live weather coming soon"
    return f"<div class='weather-line'>{icon} {label} <span class='weather-note'>({note})</span></div>"


# =========================================================
# 8. Streamlit 앱 UI
# =========================================================
st.set_page_config(
    page_title="Seoul, now",
    page_icon="🏙️",
    layout="wide",
    initial_sidebar_state="expanded",
)

# 지도(iframe)가 뒤늦게 로드되며 브라우저가 그 위치로 스크롤을 당기는 경우가 있어,
# 이 세션에서 처음 열렸을 때 사용자가 실제로 스크롤하기 전까지는 페이지 최상단을 유지한다.
if "did_initial_scroll" not in st.session_state:
    components.html(
        """
        <script>
        (function () {
            var win = window.parent;
            var doc = win.document;
            var userScrolled = false;

            function markUserScrolled() { userScrolled = true; }
            win.addEventListener('wheel', markUserScrolled, {passive: true});
            win.addEventListener('touchstart', markUserScrolled, {passive: true});
            win.addEventListener('keydown', function (e) {
                var keys = ['ArrowDown', 'ArrowUp', 'PageDown', 'PageUp', ' '];
                if (keys.indexOf(e.key) !== -1) userScrolled = true;
            });

            function forceTop() {
                if (userScrolled) return;
                try {
                    win.scrollTo(0, 0);
                    var candidates = [
                        doc.querySelector('[data-testid="stAppViewContainer"]'),
                        doc.querySelector('section.main'),
                        doc.querySelector('.main'),
                    ];
                    candidates.forEach(function (el) { if (el) el.scrollTo(0, 0); });
                } catch (e) {}
            }

            var ticks = 0;
            var timer = win.setInterval(function () {
                forceTop();
                ticks += 1;
                if (userScrolled || ticks > 60) {
                    win.clearInterval(timer);
                }
            }, 100); // 최대 약 6초 동안, 사용자가 스크롤하면 즉시 중단
        })();
        </script>
        """,
        height=0,
    )
    st.session_state.did_initial_scroll = True

# CSS만으로 색이 안 바뀌는 위젯(라벨/입력창/코드블록)이 있어,
# 매 실행마다 JS로 <style> 태그를 부모 문서 <head>에 직접 심어 확실히 덮어쓴다.
components.html(
    """
    <script>
    (function () {
        var doc = window.parent.document;
        var styleId = 'seoul-now-force-style';

        function injectStyle() {
            var old = doc.getElementById(styleId);
            if (old) old.remove();
            var style = doc.createElement('style');
            style.id = styleId;
            style.innerHTML = `
            label, label * ,
            [data-testid="stWidgetLabel"], [data-testid="stWidgetLabel"] * {
                color: #F4F6FF !important;
                opacity: 1 !important;
                -webkit-text-fill-color: #F4F6FF !important;
            }
            input[type="text"], textarea,
            input[type="text"]:focus, textarea:focus,
            input[type="text"]:hover, textarea:hover,
            input[type="text"]:active, textarea:active,
            input:not([type="checkbox"]):not([type="radio"]):not([type="range"]) {
                background-color: #171b34 !important;
                color: #F4F6FF !important;
                -webkit-text-fill-color: #F4F6FF !important;
                caret-color: #F4F6FF !important;
                border: 1px solid rgba(255,255,255,0.25) !important;
                border-radius: 8px !important;
                box-shadow: none !important;
                outline: none !important;
            }
            input[type="text"]:focus, textarea:focus,
            input:not([type="checkbox"]):not([type="radio"]):not([type="range"]):focus {
                border-color: #2DE1C2 !important;
            }
            input[type="text"]::placeholder, textarea::placeholder {
                color: rgba(244,246,255,0.45) !important;
                -webkit-text-fill-color: rgba(244,246,255,0.45) !important;
            }
            pre, code, [data-testid="stCodeBlock"] {
                background-color: #171b34 !important;
                border: 1px solid rgba(255,255,255,0.16) !important;
                border-radius: 10px !important;
            }
            pre *, code *, [data-testid="stCodeBlock"] * {
                color: #F4F6FF !important;
                background-color: transparent !important;
                -webkit-text-fill-color: #F4F6FF !important;
            }
        `;
            doc.head.appendChild(style);
        }

        injectStyle();

        var observer = new MutationObserver(function () {
            var ourStyle = doc.getElementById(styleId);
            if (!ourStyle || doc.head.lastElementChild !== ourStyle) {
                injectStyle();
            }
        });
        observer.observe(doc.head, {childList: true});
        window.parent.setTimeout(function () { observer.disconnect(); }, 15000);
    })();
    </script>
    """,
    height=0,
)

if "favorites" not in st.session_state:
    st.session_state.favorites = set()
if "selected_place" not in st.session_state:
    st.session_state.selected_place = None
if "course" not in st.session_state:
    st.session_state.course = []  # 순서가 중요하므로 리스트로 관리
if "ratings" not in st.session_state:
    st.session_state.ratings = {}  # {place_name: {"score": int, "comment": str}}

# ---- 전역 스타일 (야간 도시 컨셉: 딥 네이비 + 네온 틸 + 글래스모피즘) ----
st.markdown(
    """
    <style>
    /* ---- 전체 배경: 서울 야경을 연상시키는 그라데이션 + 은은한 글로우 ---- */
    [data-testid="stAppViewContainer"] {
        background:
            radial-gradient(ellipse 900px 500px at 8% -5%, rgba(108,92,231,0.30), transparent 60%),
            radial-gradient(ellipse 700px 500px at 95% 10%, rgba(45,225,194,0.16), transparent 55%),
            radial-gradient(ellipse 800px 600px at 50% 100%, rgba(255,122,89,0.08), transparent 60%),
            linear-gradient(180deg, #090B1F 0%, #0E1230 45%, #10142E 100%);
        color: #F4F6FF;
    }
    [data-testid="stHeader"] {
        background: transparent !important;
    }
    /* 사이드바 재표시(>>) 버튼 - 꽉 찬 민트색 배경 + 진한 아이콘으로 어떤 배경 위에서도 확실히 보이게 함 */
    [data-testid="collapsedControl"] {
        background: #2DE1C2 !important;
        border: none !important;
        border-radius: 8px !important;
        box-shadow: 0 2px 12px rgba(0,0,0,0.5) !important;
        opacity: 1 !important;
    }
    [data-testid="collapsedControl"] svg,
    [data-testid="collapsedControl"] * {
        color: #0B0E24 !important;
        fill: #0B0E24 !important;
        stroke: #0B0E24 !important;
    }
    [data-testid="collapsedControl"]:hover {
        background: #58ecd6 !important;
    }
    [data-testid="stSidebar"] {
        background: #0B0E24;
        border-right: 1px solid rgba(255,255,255,0.08);
    }
    [data-testid="stSidebar"] * { color: #F4F6FF; }
    [data-testid="stSidebar"] .stCaption, [data-testid="stSidebar"] small { color: rgba(244,246,255,0.55) !important; }
    [data-testid="stSidebar"] .block-container,
    [data-testid="stSidebarUserContent"],
    [data-testid="stSidebarContent"],
    [data-testid="stSidebar"] > div,
    [data-testid="stSidebar"] > div > div {
        padding-top: 0.3rem !important;
        margin-top: 0 !important;
    }
    [data-testid="stAppViewContainer"] .block-container,
    [data-testid="stAppViewContainer"] .main .block-container,
    [data-testid="stMainBlockContainer"],
    [data-testid="stAppViewContainer"] > div,
    [data-testid="stAppViewContainer"] > div > div,
    [data-testid="stAppViewContainer"] section,
    [data-testid="stAppViewContainer"] section > div {
        padding-top: 0.3rem !important;
        margin-top: 0 !important;
    }


    /* ---- 히어로 ---- */
    .hero { padding: 0 0 22px 0; position: relative; }
    .live-chip {
        display: inline-flex; align-items: center; gap: 6px;
        background: rgba(45,225,194,0.12); border: 1px solid rgba(45,225,194,0.4);
        color: #2DE1C2; font-size: 0.72rem; font-weight: 700; letter-spacing: 0.06em;
        padding: 4px 12px; border-radius: 999px; margin-bottom: 14px;
    }
    .live-dot {
        width: 7px; height: 7px; border-radius: 50%; background: #2DE1C2;
        box-shadow: 0 0 0 0 rgba(45,225,194,0.6);
        animation: pulse 1.8s infinite;
    }
    @keyframes pulse {
        0% { box-shadow: 0 0 0 0 rgba(45,225,194,0.55); }
        70% { box-shadow: 0 0 0 8px rgba(45,225,194,0); }
        100% { box-shadow: 0 0 0 0 rgba(45,225,194,0); }
    }
    .hero-title {
        font-size: 3.2rem !important; font-weight: 800 !important; letter-spacing: -0.03em !important;
        margin: 0 !important; line-height: 1.05 !important;
        background: linear-gradient(100deg, #FFFFFF 20%, #B9C2FF 55%, #2DE1C2 90%);
        -webkit-background-clip: text !important; background-clip: text !important; color: transparent !important;
    }
    .hero-subtitle { font-size: 1.05rem; color: rgba(244,246,255,0.78); margin-top: 12px; }
    .hero-caption { font-size: 0.85rem; color: rgba(244,246,255,0.42); margin-top: 4px; }
    .skyline {
        width: 100%; max-width: 640px; height: 46px; margin-top: 18px; opacity: 0.55;
    }

    /* ---- 목적 안내 배너 ---- */
    .purpose-banner {
        background: rgba(255,255,255,0.045);
        backdrop-filter: blur(10px);
        border: 1px solid rgba(255,255,255,0.10);
        border-left: 3px solid #2DE1C2;
        border-radius: 10px;
        padding: 12px 18px;
        margin-bottom: 14px;
        font-size: 0.95rem;
        color: rgba(244,246,255,0.9);
    }
    .result-count { font-size: 0.85rem; color: rgba(244,246,255,0.5); margin-bottom: 18px; }

    /* ---- 추천 카드 (글래스모피즘) ---- */
    .place-card {
        display: flex; align-items: center; gap: 18px;
        border: 1px solid rgba(255,255,255,0.10);
        border-radius: 16px; padding: 18px 22px; margin-bottom: 6px;
        background: rgba(255,255,255,0.045);
        backdrop-filter: blur(14px);
        box-shadow: 0 4px 24px rgba(0,0,0,0.25);
    }
    .place-card-rank {
        flex: 0 0 auto; width: 38px; height: 38px; border-radius: 50%;
        border: 1px solid rgba(45,225,194,0.4);
        background: rgba(45,225,194,0.08);
        display: flex; align-items: center; justify-content: center;
        font-weight: 700; font-size: 0.95rem; color: #2DE1C2;
    }
    .place-card-main { flex: 1; min-width: 0; }
    .place-card-title { font-size: 1.1rem; font-weight: 700; margin-bottom: 8px; color: #F4F6FF; }
    .place-card-category { font-weight: 400; color: rgba(244,246,255,0.5); font-size: 0.8rem; margin-left: 6px; }
    .fav-mark { font-size: 0.75rem; color: #FF7A59; margin-left: 8px; font-weight: 600; }
    .place-card-badges { margin-bottom: 8px; }
    .badge {
        display: inline-flex; align-items: center;
        background: rgba(255,255,255,0.07); border: 1px solid rgba(255,255,255,0.08);
        border-radius: 12px; color: rgba(244,246,255,0.85);
        padding: 3px 10px 3px 8px; font-size: 0.78rem;
        margin-right: 6px; margin-bottom: 4px; white-space: nowrap;
    }
    .badge-dot { width: 7px; height: 7px; border-radius: 50%; display: inline-block; margin-right: 5px; }
    .badge-event { background: rgba(255,122,89,0.14); border-color: rgba(255,122,89,0.4); color: #FF9478; font-weight: 600; }
    .badge-muted { color: rgba(244,246,255,0.4); }
    .place-card-reason { font-size: 0.85rem; color: rgba(244,246,255,0.7); }
    .place-card-score { flex: 0 0 auto; width: 100px; text-align: center; }
    .score-number {
        font-size: 2.2rem; font-weight: 800; line-height: 1;
        background: linear-gradient(120deg, #2DE1C2, #6C5CE7);
        -webkit-background-clip: text; background-clip: text; color: transparent;
    }
    .score-max { font-size: 0.72rem; color: rgba(244,246,255,0.4); margin-bottom: 6px; }
    .score-bar-bg { background: rgba(255,255,255,0.10); border-radius: 8px; height: 6px; width: 100%; overflow: hidden; }
    .score-bar-fill { background: linear-gradient(90deg, #2DE1C2, #6C5CE7); height: 100%; border-radius: 8px; }

    /* ---- 점수 breakdown ---- */
    .breakdown-box { padding: 6px 4px 2px 4px; }
    .breakdown-row { display: flex; align-items: center; gap: 10px; margin-bottom: 8px; }
    .breakdown-label { flex: 0 0 70px; font-size: 0.82rem; color: rgba(244,246,255,0.7); }
    .breakdown-bar-bg { flex: 1; background: rgba(255,255,255,0.08); border-radius: 6px; height: 10px; overflow: hidden; }
    .breakdown-bar-fill { background: linear-gradient(90deg, #6C5CE7, #2DE1C2); height: 100%; border-radius: 6px; }
    .breakdown-value { flex: 0 0 55px; font-size: 0.8rem; text-align: right; color: rgba(244,246,255,0.7); }

    .fav-sidebar-item { font-size: 0.88rem; padding: 3px 0; }

    /* ---- 길찾기 링크 ---- */
    .nav-links { display: flex; gap: 8px; flex-wrap: wrap; margin: 10px 0 4px 0; }
    .nav-link-btn {
        display: inline-flex; align-items: center; gap: 4px;
        background: rgba(45,225,194,0.10); border: 1px solid rgba(45,225,194,0.35);
        color: #2DE1C2 !important; text-decoration: none !important;
        padding: 6px 14px; border-radius: 999px; font-size: 0.82rem; font-weight: 600;
        transition: background 0.15s ease;
    }
    .nav-link-btn:hover { background: rgba(45,225,194,0.2); }

    /* ---- 날씨 상태 라인 ---- */
    .weather-line { font-size: 0.95rem; color: #F4F6FF; margin: 4px 0 2px 0; }
    .weather-note { font-size: 0.75rem; color: rgba(244,246,255,0.45); }

    /* ---- Streamlit 기본 위젯 톤 맞추기 ---- */
    .stButton>button {
        background: rgba(255,255,255,0.06); color: #F4F6FF;
        border: 1px solid rgba(255,255,255,0.16); border-radius: 999px;
    }
    .stButton>button:hover { border-color: #2DE1C2; color: #2DE1C2; }
    [data-testid="stExpander"] {
        background: rgba(255,255,255,0.03);
        border: 1px solid rgba(255,255,255,0.10);
        border-radius: 12px;
    }

    /* ---- Streamlit 기본 텍스트 요소가 다크 배경에 묻히지 않도록 대비 확보 ---- */
    [data-testid="stAppViewContainer"] h1,
    [data-testid="stAppViewContainer"] h2,
    [data-testid="stAppViewContainer"] h3,
    [data-testid="stAppViewContainer"] h4,
    [data-testid="stAppViewContainer"] h5,
    [data-testid="stAppViewContainer"] h6 {
        color: #F4F6FF !important;
    }
    [data-testid="stExpander"] summary,
    [data-testid="stExpander"] details summary,
    .streamlit-expanderHeader,
    details summary {
        background-color: rgba(255,255,255,0.05) !important;
        color: #F4F6FF !important;
    }
    [data-testid="stExpander"] summary *,
    .streamlit-expanderHeader *,
    details summary * {
        color: #F4F6FF !important;
        fill: #F4F6FF !important;
        background-color: transparent !important;
    }
    [data-testid="stExpander"] summary:hover,
    details summary:hover {
        background-color: rgba(255,255,255,0.09) !important;
    }
    /* Streamlit 기본 화살표 아이콘은 버전에 따라 위치가 어긋나거나 흐릿하게 나와서 그냥 숨긴다. */
    [data-testid="stExpander"] svg,
    .streamlit-expanderHeader svg,
    [data-testid="stExpanderToggleIcon"],
    [data-testid="stExpanderIcon"] {
        display: none !important;
    }
    [data-testid="stCaptionContainer"],
    [data-testid="stCaptionContainer"] p {
        color: rgba(244,246,255,0.62) !important;
    }
    [data-testid="stWidgetLabel"],
    [data-testid="stWidgetLabel"] p,
    [data-testid="stWidgetLabel"] label,
    [data-testid="stWidgetLabel"] span,
    [data-testid="stAppViewContainer"] label,
    [data-testid="stSidebar"] label {
        color: #F4F6FF !important;
        opacity: 1 !important;
    }
    [data-testid="stImage"] img { max-width: 820px; }

    /* ---- 텍스트 입력창 (한 줄 후기 등) — testid 대신 실제 input/textarea 태그를 직접 지정 ---- */
    [data-testid="stAppViewContainer"] input[type="text"],
    [data-testid="stAppViewContainer"] textarea,
    [data-testid="stSidebar"] input[type="text"],
    [data-testid="stTextInput"] input,
    [data-testid="stTextArea"] textarea {
        background: rgba(255,255,255,0.08) !important;
        color: #F4F6FF !important;
        border: 1px solid rgba(255,255,255,0.2) !important;
        border-radius: 8px !important;
        -webkit-text-fill-color: #F4F6FF !important;
    }
    [data-testid="stAppViewContainer"] input[type="text"]::placeholder,
    [data-testid="stAppViewContainer"] textarea::placeholder {
        color: rgba(244,246,255,0.4) !important;
    }

    /* ---- 슬라이더 (만족도 등) — 트랙/숫자 라벨을 테마 색상으로 ---- */
    [data-testid="stSlider"] [data-baseweb="slider"] div[role="slider"] {
        background-color: #2DE1C2 !important;
        border-color: #2DE1C2 !important;
    }
    [data-testid="stSlider"] * { color: #F4F6FF !important; }

    /* ---- 코드 블록 (공유 결과) — testid 대신 실제 pre/code 태그를 직접 지정 ---- */
    [data-testid="stAppViewContainer"] pre,
    [data-testid="stAppViewContainer"] code,
    [data-testid="stCodeBlock"] {
        background: rgba(255,255,255,0.07) !important;
        color: #F4F6FF !important;
        border: 1px solid rgba(255,255,255,0.14) !important;
        border-radius: 10px !important;
    }
    [data-testid="stAppViewContainer"] pre *,
    [data-testid="stAppViewContainer"] code * {
        color: #F4F6FF !important;
        background: transparent !important;
    }
    [data-testid="stCodeBlock"] button,
    [data-testid="stAppViewContainer"] pre button {
        background: rgba(255,255,255,0.1) !important;
        color: #F4F6FF !important;
    }

    /* ---- 다운로드 버튼 ---- */
    [data-testid="stDownloadButton"] button,
    .stDownloadButton > button {
        background: rgba(255,255,255,0.06) !important;
        color: #F4F6FF !important;
        border: 1px solid rgba(255,255,255,0.16) !important;
        border-radius: 999px !important;
    }
    [data-testid="stDownloadButton"] button:hover {
        border-color: #2DE1C2 !important;
        color: #2DE1C2 !important;
    }

    /* ---- st.table (전체 장소 상세 데이터) 다크 테마 적용 ---- */
    [data-testid="stAppViewContainer"] table {
        background: rgba(255,255,255,0.03) !important;
        border-collapse: collapse;
        width: 100%;
    }
    [data-testid="stAppViewContainer"] thead th {
        background: rgba(255,255,255,0.07) !important;
        color: #F4F6FF !important;
        border-bottom: 1px solid rgba(255,255,255,0.16) !important;
        padding: 8px 12px !important;
        text-align: left !important;
        font-weight: 700 !important;
    }
    [data-testid="stAppViewContainer"] tbody td,
    [data-testid="stAppViewContainer"] tbody th {
        background: transparent !important;
        color: rgba(244,246,255,0.85) !important;
        border-bottom: 1px solid rgba(255,255,255,0.08) !important;
        padding: 7px 12px !important;
    }
    [data-testid="stAppViewContainer"] tbody tr:hover td {
        background: rgba(45,225,194,0.06) !important;
    }
    </style>
    """,
    unsafe_allow_html=True,
)

# =========================================================
# 사이드바: 언어 선택 (맨 위, 토글 형태)
# =========================================================
st.sidebar.header("🌐 Language / 언어")
lang_choice = st.sidebar.radio(
    "Language / 언어",
    options=["한국어", "English"],
    key="lang_choice",
)
lang = "en" if lang_choice == "English" else "ko"
t = TEXTS[lang]

# ---- 상단 헤더 ----
skyline_svg = """<svg class="skyline" viewBox="0 0 640 46" xmlns="http://www.w3.org/2000/svg" preserveAspectRatio="none">
<rect x="0" y="20" width="14" height="26" fill="#B9C2FF"/>
<rect x="18" y="10" width="10" height="36" fill="#2DE1C2"/>
<rect x="32" y="26" width="16" height="20" fill="#B9C2FF"/>
<rect x="52" y="6" width="12" height="40" fill="#6C5CE7"/>
<rect x="68" y="18" width="10" height="28" fill="#B9C2FF"/>
<rect x="82" y="0" width="14" height="46" fill="#2DE1C2"/>
<rect x="100" y="22" width="18" height="24" fill="#B9C2FF"/>
<rect x="122" y="14" width="10" height="32" fill="#6C5CE7"/>
<rect x="136" y="28" width="20" height="18" fill="#B9C2FF"/>
<rect x="160" y="4" width="12" height="42" fill="#2DE1C2"/>
<rect x="176" y="20" width="14" height="26" fill="#B9C2FF"/>
</svg>"""

hero_lines = [
    "<div class='hero'>",
    f"<div class='live-chip'><span class='live-dot'></span>{t['live_badge']}</div>",
    "<p class='hero-title' style=\"font-size:3.2rem !important; font-weight:800 !important; "
    "line-height:1.05 !important; margin:0 !important; letter-spacing:-0.03em !important; "
    "background:linear-gradient(100deg,#FFFFFF 20%,#B9C2FF 55%,#2DE1C2 90%); "
    "-webkit-background-clip:text; background-clip:text; color:transparent;\">Seoul, now</p>",
    f"<p class='hero-subtitle'>{t['hero_subtitle']}</p>",
]
if t["hero_caption"]:
    hero_lines.append(f"<p class='hero-caption'>{t['hero_caption']}</p>")
hero_lines.append(skyline_svg.replace("\n", ""))
hero_lines.append("</div>")
st.markdown("".join(hero_lines), unsafe_allow_html=True)

# =========================================================
# 사이드바: 방문 목적 선택 (실제 방문 행위)
# =========================================================
st.sidebar.header(t["sidebar_purpose_header"])
purpose = st.sidebar.radio(
    t["sidebar_purpose_question"],
    options=ACTIVITY_KEYS,
    format_func=lambda p: PURPOSE_LABEL_EN[p] if lang == "en" else p,
    key="purpose_choice",
)

# =========================================================
# 사이드바: 선호 옵션 (목적이라기보다 조건에 가까운 선택, 선택사항)
# =========================================================
st.sidebar.header(t["sidebar_option_header"])
option_choice = st.sidebar.radio(
    t["sidebar_option_question"],
    options=[t["option_none"]] + OPTION_KEYS,
    format_func=lambda o: o if (lang == "ko" or o == t["option_none"]) else PURPOSE_LABEL_EN[o],
    key="option_choice",
)
selected_option = None if option_choice == t["option_none"] else option_choice

# --- 목적(+옵션) 가중치를 블렌딩하여 슬라이더 기본값 계산 ---
# 옵션을 고르면, 목적 가중치에 옵션 가중치를 60% 비율로 섞어 반영한다.
# (35%였을 때는 '여의도한강공원'처럼 전반적으로 고르게 좋은 장소가 거의 항상 1위를 차지해
#  옵션을 바꿔도 순위 체감이 잘 안 되는 문제가 있어 비율을 크게 올렸다.)
OPTION_BLEND_RATIO = 0.6


def blended_default_weights(purpose_key: str, option_key: str | None) -> dict:
    base = PURPOSE_WEIGHTS[purpose_key]
    if option_key is None:
        return base
    opt = PURPOSE_WEIGHTS[option_key]
    return {
        k: base[k] * (1 - OPTION_BLEND_RATIO) + opt[k] * OPTION_BLEND_RATIO
        for k in base
    }


# --- 가중치 커스터마이징 ---
with st.sidebar.expander(t["weight_expander_title"], expanded=False):
    st.caption(t["weight_caption"])
    default_w = blended_default_weights(purpose, selected_option)
    raw_weights = {}
    option_key_for_widget = selected_option or "none"
    for factor in ["congestion", "weather", "traffic", "noise", "event"]:
        raw_weights[factor] = st.slider(
            FACTOR_LABELS[factor][lang],
            min_value=0, max_value=100,
            value=int(round(default_w[factor] * 100)),
            step=5,
            key=f"weight_{factor}_{purpose}_{option_key_for_widget}",
        )
    total_w = sum(raw_weights.values()) or 1
    custom_weights = {k: v / total_w for k, v in raw_weights.items()}

# --- 필터링 슬라이더 ---
with st.sidebar.expander(t["filter_expander_title"], expanded=False):
    max_congestion = st.slider(t["filter_congestion"], 1, 5, 5, key="filter_congestion")
    max_noise = st.slider(t["filter_noise"], 1, 5, 5, key="filter_noise")
    max_traffic = st.slider(t["filter_traffic"], 1, 5, 5, key="filter_traffic")
    min_weather = st.slider(t["filter_weather"], 1, 5, 1, key="filter_weather")
    event_only = st.checkbox(t["filter_event_only"], value=False, key="filter_event")

# --- 찜한 장소 목록 ---
st.sidebar.header(t["favorites_header"])
if st.session_state.favorites:
    for fav_place in sorted(st.session_state.favorites):
        fav_display = display_place_name(fav_place, lang)
        fav_view_col, fav_remove_col = st.sidebar.columns([4, 1])
        with fav_view_col:
            if st.button(f"· {fav_display}", key=f"fav_goto_{fav_place}"):
                st.session_state.selected_place = fav_place
                rerun_app()
        with fav_remove_col:
            if st.button("✕", key=f"fav_remove_{fav_place}"):
                st.session_state.favorites.discard(fav_place)
                rerun_app()
    if st.sidebar.button(t["favorites_reset"]):
        st.session_state.favorites = set()
        rerun_app()
else:
    st.sidebar.caption(t["favorites_empty"])

# =========================================================
# 사이드바: 서울시 실시간 데이터 연동
# =========================================================
st.sidebar.header(t["live_data_header"])
# secrets.toml 자동완성 편의 기능은 환경에 따라 오류 배너를 유발할 수 있어 제거했다.
# 인증키는 매번 직접 입력하거나, 아래 안내대로 브라우저에 저장해두고 붙여넣으면 된다.
seoul_api_key = st.sidebar.text_input(
    t["live_data_key_label"],
    value="",
    type="password",
    placeholder=t["live_data_key_placeholder"],
    key="seoul_api_key_input",
)
use_live_data = st.sidebar.checkbox(t["live_data_toggle_label"], value=False, key="use_live_data")
st.sidebar.caption(t["live_data_caption"])
if use_live_data and not seoul_api_key:
    st.sidebar.warning(t["live_data_no_key"])
    use_live_data = False

# =========================================================
# 데이터 처리: 점수 계산 → 필터링 → 정렬
# =========================================================
df = load_sample_data()
live_status = None
if use_live_data and seoul_api_key:
    df, live_status = apply_live_seoul_data(df, seoul_api_key)

scored_df = calculate_scores(df, custom_weights)

filtered_df = scored_df[
    (scored_df["congestion_level"] <= max_congestion)
    & (scored_df["noise_level"] <= max_noise)
    & (scored_df["traffic_congestion"] <= max_traffic)
    & (scored_df["weather_suitability"] >= min_weather)
]
if event_only:
    filtered_df = filtered_df[filtered_df["has_event"] == 1]

ranked_df = filtered_df.sort_values("recommendation_score", ascending=False).reset_index(drop=True)

# --- 목적별 추천 기준 안내 ---
purpose_label = PURPOSE_LABEL_EN[purpose] if lang == "en" else purpose
banner_html = (
    f"{PURPOSE_ICONS[purpose]} <b>'{purpose_label}'</b> — {PURPOSE_DESCRIPTIONS[lang][purpose]}"
)
if selected_option:
    option_label = PURPOSE_LABEL_EN[selected_option] if lang == "en" else selected_option
    banner_html += (
        f"<br>{PURPOSE_ICONS[selected_option]} <b>'{option_label}'</b> — "
        f"{PURPOSE_DESCRIPTIONS[lang][selected_option]}"
    )

is_detail_mode = (
    st.session_state.selected_place is not None
    and st.session_state.selected_place in scored_df["place_name"].values
)

# =========================================================
# 상세 화면: 카드를 클릭해서 들어온 특정 장소의 확장 뷰
# =========================================================
if is_detail_mode:
    detail_row = scored_df[scored_df["place_name"] == st.session_state.selected_place].iloc[0]
    detail_sorted_all = scored_df.sort_values("recommendation_score", ascending=False).reset_index(drop=True)
    detail_rank = int(detail_sorted_all[detail_sorted_all["place_name"] == st.session_state.selected_place].index[0]) + 1
    detail_name = detail_row["place_name"]
    detail_is_fav = detail_name in st.session_state.favorites
    detail_in_course = detail_name in st.session_state.course

    if st.button(t["detail_back"], key="detail_back_btn"):
        st.session_state.selected_place = None
        rerun_app()

    st.markdown(render_card(detail_rank, detail_row, purpose, detail_is_fav, lang, selected_option), unsafe_allow_html=True)
    st.markdown(render_weather_line(detail_row["weather_suitability"], lang), unsafe_allow_html=True)
    detail_display_name = display_place_name(detail_name, lang)
    st.markdown(
        render_navigation_links(detail_display_name, detail_row["latitude"], detail_row["longitude"], lang),
        unsafe_allow_html=True,
    )

    action_col1, action_col2 = st.columns([1, 1])
    with action_col1:
        fav_label = t["fav_button_remove"] if detail_is_fav else t["fav_button_add"]
        if st.button(fav_label, key=f"detail_fav_{detail_name}"):
            if detail_is_fav:
                st.session_state.favorites.discard(detail_name)
            else:
                st.session_state.favorites.add(detail_name)
            rerun_app()
    with action_col2:
        course_label = t["course_remove_button"] if detail_in_course else t["course_add_button"]
        if st.button(course_label, key=f"detail_course_{detail_name}"):
            if detail_in_course:
                st.session_state.course.remove(detail_name)
            else:
                st.session_state.course.append(detail_name)
            rerun_app()

    st.markdown("<div style='margin-bottom:6px;'></div>", unsafe_allow_html=True)
    with st.expander(t["detail_expander"], expanded=True):
        st.markdown(render_breakdown(detail_row, lang), unsafe_allow_html=True)

    st.divider()

    # --- 시간대별 혼잡도 추이 (해당 장소의 실시간 반영 여부에 따라 문구 분기) ---
    is_live_for_this_place = (
        live_status is not None and detail_name in live_status.get("success", [])
    )
    hourly_header = t["detail_hourly_header_live"] if is_live_for_this_place else t["detail_hourly_header"]
    hourly_caption = t["detail_hourly_caption_live"] if is_live_for_this_place else t["detail_hourly_caption"]
    st.subheader(hourly_header)
    st.caption(hourly_caption)
    hourly = synthetic_hourly_congestion(detail_row["congestion_level"])
    hours = [h for h, _ in hourly]
    values = [v for _, v in hourly]

    fig_h, ax_h = plt.subplots(figsize=(8, 2.6), dpi=160)
    fig_h.patch.set_alpha(0.0)
    ax_h.set_facecolor("none")
    text_color = "#F4F6FF"
    ax_h.plot(hours, values, color="#2DE1C2", linewidth=2, marker="o", markersize=3, zorder=3)
    ax_h.fill_between(hours, values, min(values) - 0.3, color="#2DE1C2", alpha=0.12, zorder=2)
    ax_h.set_ylim(1, 5)
    ax_h.set_xticks(hours[::2])
    ax_h.tick_params(axis="x", colors=text_color, labelsize=8)
    ax_h.tick_params(axis="y", colors=text_color, labelsize=8)
    for spine_name in ["top", "right", "left"]:
        ax_h.spines[spine_name].set_visible(False)
    ax_h.spines["bottom"].set_color("#3A4066")
    ax_h.yaxis.grid(True, color="#3A4066", linewidth=0.5, alpha=0.4, zorder=0)
    ax_h.set_axisbelow(True)
    fig_h.tight_layout()
    st.pyplot(fig_h)

    st.divider()

    # --- 비슷한 분위기의 다른 장소 ---
    st.subheader(t["detail_similar_header"])
    similar_df = find_similar_places(scored_df, detail_name, n=3)
    if similar_df.empty:
        st.caption(t["detail_similar_empty"])
    else:
        sim_cols = st.columns(len(similar_df))
        for col, (_, srow) in zip(sim_cols, similar_df.iterrows()):
            with col:
                sim_name_display = display_place_name(srow["place_name"], lang)
                st.markdown(
                    f"**{sim_name_display}**  \n{srow['recommendation_score']:.0f}{'점' if lang == 'ko' else ' pts'}"
                )
                if st.button(t["detail_button"], key=f"similar_{srow['place_name']}"):
                    st.session_state.selected_place = srow["place_name"]
                    rerun_app()

    st.divider()

    # --- 방문 후기 남기기 ---
    st.subheader(t["detail_rating_header"])
    existing = st.session_state.ratings.get(detail_name, {"score": 3, "comment": ""})
    rating_score = st.slider(t["detail_rating_score_label"], 1, 5, existing["score"], key=f"rating_score_{detail_name}")
    rating_comment = st.text_input(t["detail_rating_comment_label"], value=existing["comment"], key=f"rating_comment_{detail_name}")
    if st.button(t["detail_rating_save"], key=f"rating_save_{detail_name}"):
        st.session_state.ratings[detail_name] = {"score": rating_score, "comment": rating_comment}
        rerun_app()
    if detail_name in st.session_state.ratings:
        saved = st.session_state.ratings[detail_name]
        stars = "⭐" * saved["score"]
        st.caption(f"{t['detail_rating_saved']}: {stars}" + (f" — {saved['comment']}" if saved["comment"] else ""))

else:
    # =========================================================
    # 목록 화면
    # =========================================================
    st.markdown(f"<div class='purpose-banner'>{banner_html}</div>", unsafe_allow_html=True)
    st.markdown(
        f"<div class='result-count'>{t['result_count'].format(total=len(scored_df), filtered=len(ranked_df))}</div>",
        unsafe_allow_html=True,
    )
    st.caption(t["data_timestamp"].format(time=datetime.now().strftime("%H:%M")))
    if live_status is not None:
        st.caption(
            t["live_data_status"].format(
                success=len(live_status["success"]), failed=len(live_status["failed"])
            )
        )

    # --- TOP 5 카드 (+ 찜하기 / 상세보기 / 코스에 추가 + breakdown expander) ---
    st.subheader(t["top5_subheader"])

    if ranked_df.empty:
        st.warning(t["empty_result_warning"])
    else:
        top_n = min(5, len(ranked_df))
        top5 = ranked_df.head(top_n)

        for i, row in top5.iterrows():
            place_name = row["place_name"]
            is_favorite = place_name in st.session_state.favorites
            in_course = place_name in st.session_state.course

            st.markdown(render_card(i + 1, row, purpose, is_favorite, lang, selected_option), unsafe_allow_html=True)

            fav_col, detail_col, course_col, exp_col = st.columns([1, 1, 1, 3])
            with fav_col:
                btn_label = t["fav_button_remove"] if is_favorite else t["fav_button_add"]
                if st.button(btn_label, key=f"fav_btn_{place_name}"):
                    if is_favorite:
                        st.session_state.favorites.discard(place_name)
                    else:
                        st.session_state.favorites.add(place_name)
                    rerun_app()
            with detail_col:
                if st.button(t["detail_button"], key=f"detail_btn_{place_name}"):
                    st.session_state.selected_place = place_name
                    rerun_app()
            with course_col:
                course_label = t["course_remove_button"] if in_course else t["course_add_button"]
                if st.button(course_label, key=f"course_btn_{place_name}"):
                    if in_course:
                        st.session_state.course.remove(place_name)
                    else:
                        st.session_state.course.append(place_name)
                    rerun_app()
            with exp_col:
                with st.expander(t["detail_expander"]):
                    st.markdown(render_breakdown(row, lang), unsafe_allow_html=True)

            nav_name = display_place_name(place_name, lang)
            st.markdown(
                render_navigation_links(nav_name, row["latitude"], row["longitude"], lang),
                unsafe_allow_html=True,
            )
            st.markdown("<div style='margin-bottom:14px;'></div>", unsafe_allow_html=True)

    st.divider()

    # =========================================================
    # 전체 장소 점수 막대그래프
    # =========================================================
    st.subheader(t["chart_subheader"])
    if ranked_df.empty:
        st.caption(t["chart_empty"])
    else:
        chart_source = ranked_df.copy()
        chart_source["display_name"] = chart_source["place_name"].apply(lambda p: display_place_name(p, lang))
        chart_df = chart_source.set_index("display_name")["recommendation_score"].sort_values(ascending=True)

        fig, ax = plt.subplots(figsize=(8, max(2.2, 0.5 * len(chart_df))), dpi=160)
        fig.patch.set_alpha(0.0)
        ax.set_facecolor("none")

        text_color = "#F4F6FF"
        bar_color = "#2DE1C2"

        bars = ax.barh(chart_df.index, chart_df.values, color=bar_color, height=0.55, zorder=3)

        ax.set_xlim(0, 100)
        ax.set_xlabel(t["chart_xlabel"], color=text_color, fontsize=9)
        ax.tick_params(axis="x", colors=text_color, labelsize=8)
        ax.tick_params(axis="y", colors=text_color, length=0, labelsize=8.5)
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        ax.spines["left"].set_visible(False)
        ax.spines["bottom"].set_color("#3A4066")
        ax.xaxis.grid(True, color="#3A4066", linewidth=0.6, alpha=0.5, zorder=0)
        ax.set_axisbelow(True)

        for bar, value in zip(bars, chart_df.values):
            ax.text(
                bar.get_width() + 1.5,
                bar.get_y() + bar.get_height() / 2,
                f"{value:.0f}",
                va="center",
                fontsize=8,
                color=text_color,
            )

        legend = ax.legend([t["chart_legend"]], loc="lower right", frameon=False, fontsize=8)
        for text in legend.get_texts():
            text.set_color(text_color)

        fig.tight_layout()
        st.pyplot(fig)

    st.divider()

    # =========================================================
    # 전체 데이터 표
    # =========================================================
    with st.expander(t["table_expander"]):
        display_cols = [
            "place_name", "category", "congestion_level", "weather_suitability",
            "traffic_congestion", "noise_level", "has_event", "event_name",
            "recommendation_score",
        ]
        table_df = ranked_df[display_cols].copy()
        table_df["place_name"] = table_df["place_name"].apply(lambda p: display_place_name(p, lang))
        table_df["category"] = table_df["category"].apply(lambda c: display_category(c, lang))
        if lang == "en":
            table_df["event_name"] = table_df["event_name"].apply(lambda e: EVENT_NAME_EN.get(e, e))
            table_df["has_event"] = table_df["has_event"].map({1: "Yes", 0: "No"})
        else:
            table_df["has_event"] = table_df["has_event"].map({1: "있음", 0: "없음"})

        table_df["recommendation_score"] = table_df["recommendation_score"].round().astype(int)
        final_table = table_df.rename(columns=t["table_columns"])
        final_table.index = [""] * len(final_table)  # st.table은 인덱스를 숨기는 옵션이 없어 빈 문자열로 대체
        st.table(final_table)

    st.divider()

    # =========================================================
    # 하루 일정 추천
    # =========================================================
    st.subheader(t["schedule_header"])
    st.caption(t["schedule_caption"])
    used_places = set()
    schedule_html = "<div class='place-card' style='display:block;'>"
    for slot_label, slot_purpose in t["schedule_slots"].items():
        slot_scored = calculate_scores(df, PURPOSE_WEIGHTS[slot_purpose])
        slot_sorted = slot_scored.sort_values("recommendation_score", ascending=False)
        pick = None
        for _, candidate in slot_sorted.iterrows():
            if candidate["place_name"] not in used_places:
                pick = candidate
                break
        if pick is None:
            continue
        used_places.add(pick["place_name"])
        pick_name = display_place_name(pick["place_name"], lang)
        slot_purpose_label = PURPOSE_LABEL_EN[slot_purpose] if lang == "en" else slot_purpose
        schedule_html += (
            "<div style='display:flex; justify-content:space-between; align-items:center; "
            "padding:8px 0; border-bottom:1px solid rgba(255,255,255,0.08);'>"
            f"<div><b>{slot_label}</b> · {PURPOSE_ICONS[slot_purpose]} {slot_purpose_label}</div>"
            f"<div>{pick_name} — {pick['recommendation_score']:.0f}{'점' if lang == 'ko' else ' pts'}</div>"
            "</div>"
        )
    schedule_html += "</div>"
    st.markdown(schedule_html, unsafe_allow_html=True)

    st.divider()

    # =========================================================
    # 오늘의 코스
    # =========================================================
    st.subheader(t["course_header"])
    st.caption(t["course_caption"])
    if not st.session_state.course:
        st.caption(t["course_empty"])
    else:
        course_rows = []
        for cname in st.session_state.course:
            match = scored_df[scored_df["place_name"] == cname]
            if not match.empty:
                course_rows.append(match.iloc[0])

        for idx_c, crow in enumerate(course_rows, start=1):
            cdisplay = display_place_name(crow["place_name"], lang)
            c_col1, c_col2 = st.columns([5, 1])
            with c_col1:
                st.markdown(
                    f"<span class='badge'>{idx_c}</span> **{cdisplay}** — {crow['recommendation_score']:.0f}"
                    + ("점" if lang == "ko" else " pts"),
                    unsafe_allow_html=True,
                )
            with c_col2:
                if st.button(t["course_remove_button"], key=f"course_remove_{crow['place_name']}"):
                    st.session_state.course.remove(crow["place_name"])
                    rerun_app()

        if len(course_rows) >= 2:
            total_dist = sum(
                haversine_km(
                    course_rows[i]["latitude"], course_rows[i]["longitude"],
                    course_rows[i + 1]["latitude"], course_rows[i + 1]["longitude"],
                )
                for i in range(len(course_rows) - 1)
            )
            st.caption(f"{t['course_distance_label']} {total_dist:.1f}km")

        sort_col, reset_col = st.columns([1, 1])
        with sort_col:
            if len(course_rows) >= 2 and st.button(t["course_sort_button"], key="course_sort_btn"):
                remaining = course_rows.copy()
                ordered = [remaining.pop(0)]
                while remaining:
                    last = ordered[-1]
                    nearest_idx = min(
                        range(len(remaining)),
                        key=lambda i: haversine_km(
                            last["latitude"], last["longitude"],
                            remaining[i]["latitude"], remaining[i]["longitude"],
                        ),
                    )
                    ordered.append(remaining.pop(nearest_idx))
                st.session_state.course = [r["place_name"] for r in ordered]
                rerun_app()
        with reset_col:
            if st.button(t["course_reset_button"], key="course_reset_btn"):
                st.session_state.course = []
                rerun_app()

    st.divider()

    # =========================================================
    # 찜 기반 맞춤 추천
    # =========================================================
    if st.session_state.favorites:
        fav_categories = df[df["place_name"].isin(st.session_state.favorites)]["category"]
        if not fav_categories.empty:
            top_category = fav_categories.value_counts().idxmax()
            personalized_candidates = scored_df[
                (scored_df["category"] == top_category)
                & (~scored_df["place_name"].isin(st.session_state.favorites))
            ].sort_values("recommendation_score", ascending=False).head(3)

            if not personalized_candidates.empty:
                st.subheader(t["personalized_header"])
                st.caption(t["personalized_caption"])
                pers_cols = st.columns(len(personalized_candidates))
                for col, (_, prow) in zip(pers_cols, personalized_candidates.iterrows()):
                    with col:
                        pname = display_place_name(prow["place_name"], lang)
                        st.markdown(
                            f"**{pname}**  \n{prow['recommendation_score']:.0f}{'점' if lang == 'ko' else ' pts'}"
                        )
                        if st.button(t["detail_button"], key=f"pers_{prow['place_name']}"):
                            st.session_state.selected_place = prow["place_name"]
                            rerun_app()
                st.divider()

    # =========================================================
    # 추천 결과 공유하기
    # =========================================================
    st.subheader(t["share_header"])
    st.caption(t["share_caption"])

    if st.session_state.course:
        share_rows = [scored_df[scored_df["place_name"] == p].iloc[0] for p in st.session_state.course if p in scored_df["place_name"].values]
        share_source_label = t["share_source_course"]
    else:
        share_rows = [row for _, row in ranked_df.head(5).iterrows()]
        share_source_label = t["share_source_top5"]

    if share_rows:
        share_text = build_share_text(share_rows, f"{purpose_label} {share_source_label}", lang)
        st.code(share_text)
        st.download_button(
            t["share_download_button"],
            data=share_text,
            file_name="seoul_now_recommendations.txt",
            mime="text/plain",
            key="share_download_btn",
        )
    else:
        st.caption(t["empty_result_warning"])
