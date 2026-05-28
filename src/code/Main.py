import os
import sys
import math
import signal
from Chzzk_api import select_chzzk_vod
from Timeline import (
    load_config,
    download_chzzk_vod_audio,
    transcribe_chzzk_audio,
    generate_chzzk_timeline,
)

def repair_final_timeline_layout(file_path):
    if not os.path.exists(file_path):
        return

    with open(file_path, "r", encoding="utf-8") as f:
        lines = f.readlines()

    repaired_lines = []
    
    for line in lines:
        line = line.strip()
        if not line:
            continue
            
        if line.startswith("[") and ";" in line and line.endswith("]"):
            if repaired_lines and repaired_lines[-1] != "":
                repaired_lines.append("")
            repaired_lines.append(line)
        else:
            repaired_lines.append(line)

    with open(file_path, "w", encoding="utf-8") as f:
        f.write("\n".join(repaired_lines))

def run_pure_test():
    TARGET_CHANNEL_ID, GEMINI_API_KEY = load_config()
    
    if not GEMINI_API_KEY or GEMINI_API_KEY == "YOUR_GEMINI_API_KEY":
        print("❌ Gemini API 키가 설정되지 않았습니다. config.json을 확인하세요.")
        return

    VOICE_PALETTE_BASE_DIR = os.path.join(os.getcwd(), "voicepalette")
    os.makedirs(VOICE_PALETTE_BASE_DIR, exist_ok=True)

    vod_data = select_chzzk_vod(TARGET_CHANNEL_ID)
    if not vod_data:
        print("❌ 유효한 영상 주소가 확보되지 않아 프로그램을 종료합니다.")
        return
        
    chzzk_url, actual_title, total_duration = vod_data

    vod_id = chzzk_url.split("/")[-1].split("?")[0] if "/" in chzzk_url else "unknown"
    folder_name = f"VOD_{vod_id}"
    specific_palette_dir = os.path.join(VOICE_PALETTE_BASE_DIR, folder_name)

    while True:
        try:
            user_range = input(
                "\n📊 분석할 VOD 범위를 %~% 형식으로 입력하세요 (예: 0~100 또는 10~20): "
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
    
    start_sec = (start_percent / 100) * total_duration
    end_sec = (end_percent / 100) * total_duration
    chunk_duration = 3600  
    num_chunks = math.ceil((end_sec - start_sec) / chunk_duration)
    
    all_final_lines = []
    print(f"\n🚀 총 {num_chunks}개 구간으로 분할하여 전체 분석 연산을 가동합니다.")

    for i in range(num_chunks):
        curr_start_sec = start_sec + (i * chunk_duration)
        curr_end_sec = min(curr_start_sec + chunk_duration, end_sec)
        
        curr_start_p = (curr_start_sec / total_duration) * 100
        curr_end_p = (curr_end_sec / total_duration) * 100
        
        print(f"\n⏱️ [구간 {i+1}/{num_chunks}] 분석 처리 중: {curr_start_p:.2f}% ~ {curr_end_p:.2f}%")

        cached_script_path = os.path.join(
            specific_palette_dir,
            f"cached_raw_script_{int(curr_start_p)}_{int(curr_end_p)}.txt"
        )

        if os.path.exists(cached_script_path):
            print("✨ [보이스 파레트 적중] 로컬에 보관된 대본 캐시 파일로 초고속 우회합니다.")
            with open(cached_script_path, "r", encoding="utf-8") as f:
                full_script = f.read()
        else:
            print("🚀 [최초 분석] 청크 지정 다운로드 및 Whisper 대본 추출을 개시합니다...")
            audio_mp3_path = download_chzzk_vod_audio(
                chzzk_url=chzzk_url,
                vod_id=vod_id, 
                start_percent=curr_start_p,
                end_percent=curr_end_p,
                output_filename=f"audio_chunk_{int(curr_start_p)}_{int(curr_end_p)}"
            )
            
            if not audio_mp3_path or not os.path.exists(audio_mp3_path):
                print(f"❌ [구간 {i+1}] 오디오 확보에 실패하여 건너뜜 처리합니다.")
                continue

            full_script = transcribe_chzzk_audio(
                audio_mp3_path,
                chzzk_url=chzzk_url,
                start_percent=curr_start_p
            )

            with open(cached_script_path, "w", encoding="utf-8") as f:
                f.write(full_script)
            print("💾 원본 오프셋 대본 로컬 캐싱 완료.")

        if not full_script.strip():
            print("⚠️ 추출된 대사 데이터가 비어있어 타임라인 요약을 패스합니다.")
            continue

        result_timeline = generate_chzzk_timeline(
            full_script,
            actual_title=actual_title,
            chzzk_url=chzzk_url,
            api_key=GEMINI_API_KEY
        )

        lines = [line.strip() for line in result_timeline.split("\n") if line.strip()]
        
        if i > 0:
            lines = [line for line in lines if "방송 시작 인사" not in line and not line.startswith("[00:00:00]")]
            
        all_final_lines.extend(lines)

    ai_notice = "🤖 이 댓글은 방송 하이라이트를 AI가 분석하여 생성한 타임라인으로 다소 부정확 부분이 있을 수 있습니다."
    new_header = f"[00:00:00] {actual_title}"

    cleaned_final_lines = [
        line for line in all_final_lines 
        if "🤖 이 댓글은" not in line 
        and not line.startswith("[00:00:00]")
    ]
    
    cleaned_final_lines.insert(0, new_header)
    cleaned_final_lines.insert(0, ai_notice)

    final_timeline = "\n".join(cleaned_final_lines)
    output_path = f"TL_VOD_{vod_id}_{int(start_percent)}_{int(end_percent)}.txt"

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(final_timeline)

    repair_final_timeline_layout(output_path)

    with open(output_path, "r", encoding="utf-8") as f:
        final_printed_timeline = f.read()

    print("\n==================================================")
    print("🎯 [완성] 취합 및 가공이 모두 완료된 최종 타임라인 결과")
    print("==================================================")
    print(final_printed_timeline)
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