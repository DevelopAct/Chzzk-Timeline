import os
import sys
import shutil
import math
import signal
import subprocess
import time
import urllib.request
from Chzzk_api import select_chzzk_vod
from Timeline import (
    load_config,
    download_chzzk_vod_audio,
    transcribe_chzzk_audio,
    generate_chzzk_timeline
)

def force_auto_install_ollama():
    """시스템에 Ollama가 없으면 웹에서 무인 다운로드 및 무소음(Silent) 자동 설치"""
    try:
        # Ollama가 이미 설치되어 환경변수에 잡혀있는지 체크
        subprocess.run(["ollama", "--version"], capture_output=True, check=True)
        return  # 이미 있으면 함수 탈출
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass

    # 기본 설치 예상 경로도 추가 체크
    user_home = os.environ.get("USERPROFILE", "")
    local_app_data = os.environ.get("LOCALAPPDATA", "")
    default_paths = [
        os.path.join(local_app_data, "Programs", "Ollama", "ollama.exe"),
        os.path.join(user_home, "AppData", "Local", "Programs", "Ollama", "ollama.exe")
    ]
    for p in default_paths:
        if os.path.exists(p):
            return

    print("\n⚠️ [최초 실행] 시스템에 Ollama 엔진이 감지되지 않았습니다.")
    print("📥 파이썬이 Ollama 공식 설치 파일을 자동으로 원격 다운로드합니다...")
    
    installer_path = os.path.join(os.getcwd(), "OllamaSetup.exe")
    url = "https://ollama.com/download/OllamaSetup.exe"
    
    try:
        # 1. 설치 파일 다운로드
        if not os.path.exists(installer_path):
            urllib.request.urlretrieve(url, installer_path)
            print("✅ 다운로드 완료! 백그라운드 무소음(Silent) 설치를 시작합니다.")
        
        # 2. 무소음 인스톨러 매개변수 작동 (Inno Setup 기반 규격 적용)
        # /SP- /VERYSILENT /NORESTART 옵션으로 창 안 띄우고 조용히 설치 진행
        print("🚀 Ollama 설치 진행 중... (약 10초~20초 소요)")
        subprocess.run([installer_path, "/SP-", "/VERYSILENT", "/NORESTART"], check=True)
        
        print("🎉 Ollama 설치가 완벽히 마무리되었습니다!")
        
        # 3. 인스톨러 잔해 제거
        if os.path.exists(installer_path):
            os.remove(installer_path)
            
        print("💡 [필독] 터미널(VS Code나 CMD)을 완전히 종료한 후 다시 켜야 'ollama' 명령어가 최종 인식됩니다.")
        print("💡 터미널을 재시작하고 다시 이 프로그램을 실행해 주세요! 프로그램을 종료합니다.")
        sys.exit(0)
        
    except Exception as e:
        print(f"❌ 자동으로 Ollama 설치를 실패했습니다. 사유: {e}")
        print("💡 대안: https://ollama.com/download/windows 에서 수동으로 다운받아 설치해 주세요.")
        sys.exit(1)

def ensure_ollama_service():
    """Ollama 백그라운드 서버가 돌고 있는지 확인하고 꺼져있으면 켜기"""
    import socket
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(1)
    try:
        s.connect(('127.0.0.1', 11434))
        s.close()
    except:
        print("\n📥 [안내] 로컬 Ollama 백그라운드 서버를 구동합니다...")
        if sys.platform == "win32":
            subprocess.Popen(["ollama", "serve"], creationflags=subprocess.CREATE_NO_WINDOW)
        else:
            subprocess.Popen(["ollama", "serve"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(4)  # 엔진 준비 대기 시간

def run_pure_test():
    # 0. Ollama 엔진 존재 여부 점검 및 원격 무인 설치 자동 처리
    force_auto_install_ollama()
    ensure_ollama_service()

    # 1. 설정 로드 (★ 튜플 객체 바인딩 오류 원천 차단)
    config_data = load_config()
    
    # load_config()가 튜플(예: (id, key))을 반환하는지, 딕셔너리를 반환하는지 체크하여 유연하게 처리
    if isinstance(config_data, tuple):
        TARGET_CHANNEL_ID = config_data[0]  # 첫 번째 인자가 채널 ID인 경우
    elif isinstance(config_data, dict):
        TARGET_CHANNEL_ID = config_data.get("TARGET_CHANNEL_ID", "채널_ID_입력")
    else:
        TARGET_CHANNEL_ID = "채널_ID_입력"

    VOICE_PALETTE_BASE_DIR = os.path.join(os.getcwd(), "voicepalette")
    os.makedirs(VOICE_PALETTE_BASE_DIR, exist_ok=True)

    # API에서 URL, 제목, 전체 길이를 반환받음
    vod_data = select_chzzk_vod(TARGET_CHANNEL_ID)
    if not vod_data:
        print("❌ 유효한 영상 주소가 확보되지 않아 프로그램을 종료합니다.")
        return
        
    chzzk_url, actual_title, total_duration = vod_data

    vod_id = chzzk_url.split("/")[-1].split("?")[0] if "/" in chzzk_url else "unknown"
    folder_name = f"VOD_{vod_id}"
    specific_palette_dir = os.path.join(VOICE_PALETTE_BASE_DIR, folder_name)

    # 사용자 범위 입력
    start_percent, end_percent = 0.0, 100.0
    while True:
        try:
            user_range = input(
                "📊 분석할 VOD 범위를 %~% 형식으로 입력하세요 (예: 10~15 또는 0~5): "
            ).strip()

            if "~" in user_range:
                parts = user_range.split("~")
                start_percent = float(parts[0].strip())
                end_percent = float(parts[1].strip())
            else:
                start_percent = 0.0
                end_percent = float(user_range)

            if 0.0 <= start_percent < end_percent <= 100.0:
                break
            print("❌ 0~100 사이의 올바른 범위를 입력하세요.")
        except ValueError:
            print("❌ 숫자 형식을 확인하세요.")

    os.makedirs(specific_palette_dir, exist_ok=True)
    
    # 15분(900초) 단위 분할 로직
    start_sec = (start_percent / 100) * total_duration
    end_sec = (end_percent / 100) * total_duration
    chunk_duration = 900
    num_chunks = math.ceil((end_sec - start_sec) / chunk_duration)
    
    all_final_lines = []
    print(f"\n🚀 총 {num_chunks}개 구간으로 나누어 분석을 시작합니다.")

    for i in range(num_chunks):
        curr_start_sec = start_sec + (i * chunk_duration)
        curr_end_sec = min(curr_start_sec + chunk_duration, end_sec)
        
        curr_start_p = (curr_start_sec / total_duration) * 100
        curr_end_p = (curr_end_sec / total_duration) * 100
        
        print(f"\n⏱️ [구간 {i+1}/{num_chunks}] 처리 중: {curr_start_p:.2f}% ~ {curr_end_p:.2f}%")

        cached_script_path = os.path.join(
            specific_palette_dir,
            f"cached_raw_script_{int(curr_start_p)}_{int(curr_end_p)}.txt"
        )

        if os.path.exists(cached_script_path):
            print("✨ [보이스 파레트 적중] 이전에 분석 완료된 구간입니다.")
            with open(cached_script_path, "r", encoding="utf-8") as f:
                full_script = f.read()
        else:
            print("🚀 [최초 분석] 다운로드 및 Whisper 분석을 시작합니다...")
            audio_mp3_path = download_chzzk_vod_audio(
                chzzk_url=chzzk_url,
                vod_id=vod_id, 
                start_percent=curr_start_p,
                end_percent=curr_end_p
            )
            
            if not audio_mp3_path or not os.path.exists(audio_mp3_path):
                print("❌ 다운로드 실패.")
                continue

            full_script = transcribe_chzzk_audio(
                audio_mp3_path,
                chzzk_url=chzzk_url,
                start_percent=curr_start_p
            )

            with open(cached_script_path, "w", encoding="utf-8") as f:
                f.write(full_script)
            print("💾 대본 캐시 저장 완료.")

        # 로컬 Ollama 모델 호출 
        result_timeline = generate_chzzk_timeline(
            full_script,
            actual_title=actual_title,
            chzzk_url=chzzk_url
        )

        lines = [line.strip() for line in result_timeline.split("\n") if line.strip()]
        
        # 첫 번째 구간이 아니면 오프닝 인사말 제거
        if i > 0:
            lines = [line for line in lines if "방송 시작 인사" not in line and not line.startswith("[00:00:00]")]
            
        all_final_lines.extend(lines)

    # 결과 병합 및 저장
    new_header = f"[00:00:00] {actual_title}"
    if all_final_lines:
        if all_final_lines[0].startswith("[00:00:00]"):
            all_final_lines[0] = new_header
        else:
            all_final_lines.insert(0, new_header)
    else:
        all_final_lines = [new_header]

    final_timeline = "\n".join(all_final_lines)
    output_path = os.path.join(specific_palette_dir, f"output_timeline_{int(start_percent)}_{int(end_percent)}.txt")

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(final_timeline)

    print("\n==================================================")
    print("🎯 [완성] 로컬 Ollama가 빌드한 타임라인 결과")
    print("==================================================")
    print(final_timeline)
    print("==================================================")
    print(f"💾 최종 타임라인 결과 파일이 '{output_path}'로 안전하게 출력되었습니다!")

def signal_handler(sig, frame):
    print("\n🛑 [강제 종료] 프로그램을 즉시 종료합니다...")
    sys.exit(0)

if __name__ == "__main__":
    signal.signal(signal.SIGINT, signal_handler)
    try:
        run_pure_test()
    except Exception as e:
        print(f"\n❌ 에러 발생: {e}")