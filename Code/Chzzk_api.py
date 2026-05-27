import sys
import requests

def get_chzzk_vod_list(channel_id, limit=10):
    """
    치지직 내부 API를 사용해 채널의 최근 VOD 목록을 가져옵니다.
    """
    url = f"https://api.chzzk.naver.com/service/v1/channels/{channel_id}/videos"
    
    # 봇 차단 우회 및 현재 치지직 웹 프론트엔드와 동일한 레퍼러 정렬
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Origin": "https://chzzk.naver.com",
        "Referer": f"https://chzzk.naver.com/video/{channel_id}"
    }
    
    # 💡 [핵심 수정]: page -> pagingIndex 로 수정하고 필수 정렬 파라미터 보완
    params = {
        "sortType": "LATEST",
        "pagingIndex": 0,
        "size": limit
    }
    
    try:
        response = requests.get(url, headers=headers, params=params, timeout=10)
        if response.status_code == 200:
            data = response.json()
            # 치지직 응답 구조: code 200 내부 content -> data 안에 리스트가 담겨옵니다.
            if data.get("code") == 200:
                return data.get("content", {}).get("data", [])
            else:
                print(f"⚠️ 치지직 API 반환 에러: {data.get('message')}")
                return []
        else:
            print(f"❌ 치지직 API 호출 실패 (상태 코드: {response.status_code})")
            return []
    except Exception as e:
        print(f"❌ 네트워크 통신 오류: {e}", file=sys.stderr)
        return []

def select_chzzk_vod(channel_id):
    """
    VOD 목록을 인덱스와 함께 보여주고, 사용자에게 입력을 받아 선택된 VOD 주소를 반환합니다.
    """
    print("📡 치지직 다시보기(VOD) 목록을 불러오는 중...")
    vod_list = get_chzzk_vod_list(channel_id, limit=10)
    
    if not vod_list:
        print("⚠️ 업로드된 다시보기가 없거나 채널을 찾을 수 없습니다.")
        print("💡 [팁] config.json에 등록한 TARGET_CHANNEL_ID가 스트리머 이름이 아닌 32자리 해시값인지 꼭 확인하세요.")
        return None
        
    print("\n==================================================")
    print(f"🎬 최근 VOD 리스트 (총 {len(vod_list)}개 발견)")
    print("==================================================")
    
    # 목록 출력
    for idx, video in enumerate(vod_list):
        title = video.get("videoTitle", "제목 없음")
        duration = video.get("duration", 0)
        # 초 단위를 시간/분으로 가독성 있게 표현
        hours = duration // 3600
        minutes = (duration % 3600) // 60
        
        print(f"[{idx}] {title} ({hours}시간 {minutes}분)")
    print("==================================================")
    
    # 사용자 인덱스 선택 입력 받기
    while True:
        try:
            user_input = input("\n👉 분석할 영상의 번호(인덱스)를 입력하세요: ").strip()
            selected_idx = int(user_input)
            
            if 0 <= selected_idx < len(vod_list):
                selected_video = vod_list[selected_idx]
                video_no = selected_video.get("videoNo")
                # 💡 추가: 제목 가져오기
                video_title = selected_video.get("videoTitle", "방송다시보기")
                
                full_vod_url = f"https://chzzk.naver.com/video/{video_no}"
                
                print(f"\n🎯 [선택 완료] {video_title} 작업을 시작합니다.")
                
                # 💡 수정: url과 제목을 함께 반환
                return full_vod_url, video_title, duration
            else:
                print(f"❌ 0부터 {len(vod_list)-1} 사이의 번호를 입력해주세요.")
        except ValueError:
            print("❌ 올바른 숫자를 입력해주세요.")
