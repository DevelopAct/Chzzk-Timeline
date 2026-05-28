import sys
import json
import requests

def load_config():
    try:
        with open("config.json", "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        print("❌ 에러: config.json 파일을 찾을 수 없습니다.")
        sys.exit(1)
    except json.JSONDecodeError:
        print("❌ 에러: config.json 파일의 형식이 올바르지 않습니다.")
        sys.exit(1)

CONFIG = load_config()

def get_chzzk_vod_list(channel_id, limit=10):
    url = f"https://api.chzzk.naver.com/service/v1/channels/{channel_id}/videos"
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Origin": "https://chzzk.naver.com",
        "Referer": f"https://chzzk.naver.com/video/{channel_id}",
        "Accept": "application/json, text/plain, */*"
    }
    
    params = {
        "sortType": "LATEST",
        "pagingIndex": 0,
        "size": limit
    }
    
    try:
        response = requests.get(url, headers=headers, params=params, timeout=10)
        if response.status_code == 200:
            data = response.json()
            if data.get("code") == 200:
                return data.get("content", {}).get("data", [])
            else:
                print(f"⚠️ 치지직 API 반환 에러: {data.get('message', '알 수 없는 오류')}")
                return []
        else:
            print(f"❌ 치지직 API 우회 호출 실패 (상태 코드: {response.status_code})")
            return []
    except Exception as e:
        print(f"❌ 네트워크 통신 오류: {e}", file=sys.stderr)
        return []

def select_chzzk_vod(channel_id):
    print("📡 치지직 안정화 우회 API로 다시보기(VOD) 목록을 불러오는 중...")
    vod_list = get_chzzk_vod_list(channel_id, limit=10)
    
    if not vod_list:
        print("⚠️ 업로드된 다시보기가 없거나 채널을 찾을 수 없습니다.")
        print("💡 [팁] config.json에 등록한 TARGET_CHANNEL_ID가 32자리 해시값이 맞는지 확인하세요.")
        return None
        
    print("\n==================================================")
    print(f"🎬 최근 VOD 리스트 (총 {len(vod_list)}개 발견)")
    print("==================================================")
    
    for idx, video in enumerate(vod_list):
        title = video.get("videoTitle", "제목 없음")
        duration = video.get("duration", 0)
        hours = duration // 3600
        minutes = (duration % 3600) // 60
        
        print(f"[{idx}] {title} ({hours}시간 {minutes}분)")
    print("==================================================")
    
    while True:
        try:
            user_input = input("\n👉 분석할 영상의 번호(인덱스)를 입력하세요: ").strip()
            selected_idx = int(user_input)
            
            if 0 <= selected_idx < len(vod_list):
                selected_video = vod_list[selected_idx]
                video_no = selected_video.get("videoNo")
                video_title = selected_video.get("videoTitle", "방송다시보기")
                video_duration = selected_video.get("duration", 0)
                
                full_vod_url = f"https://chzzk.naver.com/video/{video_no}"
                
                print(f"\n🎯 [선택 완료] {video_title} 작업을 시작합니다.")
                
                return full_vod_url, video_title, video_duration
            else:
                print(f"❌ 0부터 {len(vod_list)-1} 사이의 번호를 입력해주세요.")
        except ValueError:
            print("❌ 올바른 숫자를 입력해주세요.")