import os
import sys
import json
import warnings
import subprocess
import re
from yt_dlp import YoutubeDL
from google import genai
from google.genai import types

warnings.filterwarnings("ignore", category=UserWarning)

CONFIG_FILE = "config.json"
GEMINI_MODEL = "gemini-3.1-flash-lite"      
TEMPERATURE = 0.1  
MAX_OUTPUT_TOKENS = 4000             
TOP_P = 0.95                          

def load_config():
    if not os.path.exists(CONFIG_FILE):
        default_config = {
            "TARGET_CHANNEL_ID": "채널_ID_입력",
            "CHZZK_CLIENT_ID": "YOUR_CHZZK_CLIENT_ID",
            "CHZZK_CLIENT_SECRET": "YOUR_CHZZK_CLIENT_SECRET",
            "GEMINI_API_KEY": "YOUR_GEMINI_API_KEY"
        }
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(default_config, f, indent=4, ensure_ascii=False)
        print(f"\n⚙️  [안내] 프로젝트 폴더에 '{CONFIG_FILE}' 파일이 생성되었습니다.")
        sys.exit(0)

    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            config = json.load(f)
        return config.get("TARGET_CHANNEL_ID", "").strip(), config.get("GEMINI_API_KEY", "").strip()
    except Exception as e:
        print(f"❌ [JSON 파싱 실패] config.json 파일을 읽는 중 오류 발생: {e}")
        sys.exit(1)

def get_video_duration(chzzk_url):
    ydl_opts = {'quiet': True, 'nocheckcertificate': True}
    with YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(chzzk_url, download=False)
        return info.get('duration', 0)

def download_chzzk_vod_audio(chzzk_url, vod_id, start_percent=0.0, end_percent=100.0, output_filename="chzzk_vod_audio"):
    specific_palette_dir = os.path.join(os.getcwd(), "voicepalette", f"VOD_{vod_id}")
    os.makedirs(specific_palette_dir, exist_ok=True)
    
    master_audio_mp3 = os.path.join(specific_palette_dir, "full_vod_audio.mp3")
    raw_master_tmpl = os.path.join(specific_palette_dir, "raw_master_stream")
    final_chunk_mp3 = os.path.join(specific_palette_dir, f"{output_filename}.mp3")
    
    if os.path.exists(final_chunk_mp3) and os.path.getsize(final_chunk_mp3) > 1024:
        print(f"✨ [청크 캐시 적중] 이미 가공된 로컬 오디오 조각을 불러옵니다: {final_chunk_mp3}")
        return final_chunk_mp3

    total_duration = get_video_duration(chzzk_url)
    if total_duration == 0:
        print("❌ VOD 메타데이터 파싱 실패.")
        return ""

    try:
        import imageio_ffmpeg
        ffmpeg_bin = imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        ffmpeg_bin = "ffmpeg"

    if not os.path.exists(master_audio_mp3) or os.path.getsize(master_audio_mp3) < 10240:
        print(f"\n📡 [최초 1회 실행] 16개 스레드 비동기 가속 엔진으로 전체 오디오 수집을 시작합니다...")
        
        ydl_opts = {
            'format': 'worstaudio/worst',
            'outtmpl': f'{raw_master_tmpl}.%(ext)s',
            'keepvideo': False,
            'quiet': True,
            'nocheckcertificate': True,
            'noplaylist': True,
            'concurrent_fragment_downloads': 16,
            'socket_timeout': 30,
            'retries': 15,
            'fragment_retries': 20,
            'skip_unavailable_fragments': False,
            'http_chunk_size': 10485760,
        }

        try:
            with YoutubeDL(ydl_opts) as ydl:
                info_dict = ydl.extract_info(chzzk_url, download=True)
                downloaded_ext = info_dict.get('ext', 'ts')
                downloaded_raw_path = f"{raw_master_tmpl}.{downloaded_ext}"
        except Exception as e:
            print(f"❌ 멀티스레드 스트림 다운로드 중 오류 발생: {e}")
            return ""

        if not os.path.exists(downloaded_raw_path):
            print("❌ 원본 오디오 마스터 스트림 파일 생성에 실패했습니다.")
            return ""

        print("⚡ [로컬 가속] 비동기로 수집 완료된 스트림을 전체 마스터 MP3 캐시 파일로 빌드 중...")
        cmd_master = [
            ffmpeg_bin, '-y',
            '-i', downloaded_raw_path,
            '-acodec', 'libmp3lame',
            '-b:a', '96k',
            master_audio_mp3
        ]
        subprocess.run(cmd_master, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        if os.path.exists(downloaded_raw_path):
            try: os.remove(downloaded_raw_path)
            except: pass
        print("✅ [마스터 캐시 완료] 전체 영상 오디오가 로컬 보이스 파레트에 영구 보관되었습니다.")

    print(f"⚡ [로컬 초고속 컷팅] 마스터 오디오에서 구간 ({start_percent:.2f}% ~ {end_percent:.2f}%) 즉각 추출 중...")
    
    start_secs = int(total_duration * (start_percent / 100.0))
    end_secs = int(total_duration * (end_percent / 100.0))
    duration_secs = end_secs - start_secs

    cmd_chunk = [
        ffmpeg_bin, '-y',
        '-ss', str(start_secs),
        '-i', master_audio_mp3,
        '-t', str(duration_secs),
        '-acodec', 'copy',
        final_chunk_mp3
    ]
    subprocess.run(cmd_chunk, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    return final_chunk_mp3

def transcribe_chzzk_audio(audio_path, chzzk_url, start_percent, model_size="base"):
    print(f"\n🎙️ 2단계: Faster-Whisper AI 엔진 구동 ({model_size}) - 대본 추출 및 시간 복원 중...")
    
    if not os.path.exists(audio_path):
        print("❌ 분석할 오디오 파일이 존재하지 않습니다.")
        return ""

    total_duration = get_video_duration(chzzk_url)
    start_secs = int(total_duration * (start_percent / 100.0))
    
    try:
        from faster_whisper import WhisperModel
        model = WhisperModel(model_size, device="cpu", compute_type="int8")
    except ImportError:
        print("❌ faster-whisper 라이브러리가 설치되어 있지 않습니다.")
        return ""

    segments, info = model.transcribe(
        audio_path,
        language="ko",
        beam_size=5,
        word_timestamps=False,
        repetition_penalty=1.4,
        compression_ratio_threshold=1.8,
        condition_on_previous_text=False
    )
    
    script_lines = []
    for segment in segments:
        absolute_secs = int(segment.start + start_secs)
        h = absolute_secs // 3600
        m = (absolute_secs % 3600) // 60
        s = absolute_secs % 60
        
        timestamp_str = f"[{h:02d}:{m:02d}:{s:02d}]"
        text_content = segment.text.strip()
        
        if text_content:
            script_lines.append(f"{timestamp_str} {text_content}")
            print(f"  {timestamp_str} {text_content}")

    raw_script = "\n".join(script_lines)
    script_cache_path = audio_path.replace(".mp3", "_raw_script.txt")
    with open(script_cache_path, "w", encoding="utf-8") as f:
        f.write(raw_script)
        
    print(f"✅ 원본 오프셋이 복원된 생대본 추출 완료! (보존 경로: {script_cache_path})")
    return raw_script

def filter_timeline_by_score(timeline_result):
    final_lines = []
    current_group_tag = None
    group_contents = []
    
    raw_lines = timeline_result.split('\n')
    
    for line in raw_lines:
        line = line.strip()
        if not line:
            continue
            
        if line.startswith("[") and ";" in line and line.endswith("]"):
            if current_group_tag and group_contents:
                if final_lines: 
                    final_lines.append("") 
                final_lines.append(current_group_tag)
                final_lines.extend(group_contents)
            
            current_group_tag = line  
            group_contents = []
            continue
            
        match = re.search(r"재미:(\d+),\s*중요:(\d+)", line)
        if match:
            wf, wi = int(match.group(1)), int(match.group(2))
            wt = (wf + wi) / 100
            step = max(1, min(10, round(wt * 10)))
            
            if step >= 6 or wi >= 40:
                clean_line = re.sub(r"\(재미:\d+,\s*중요:\d+\)", "", line).strip()
                if current_group_tag:
                    group_contents.append(clean_line)
                else:
                    final_lines.append(clean_line)
                    
    if current_group_tag and group_contents:
        if final_lines: 
            final_lines.append("")
        final_lines.append(current_group_tag)
        final_lines.extend(group_contents)
        
    return "\n".join(final_lines)

def generate_chzzk_timeline(raw_script, actual_title="VOD제목", chzzk_url="", api_key=""):
    try:
        with open("prompt.txt", "r", encoding="utf-8") as f:
            system_instruction = f.read()
    except:
        system_instruction = "당신은 VOD 편집자입니다. 대본을 분석하여 사건과 상황을 타임라인 형식으로 요약하세요."

    streamer_info_content = ""
    if os.path.exists("streamer_info.txt"):
        try:
            with open("streamer_info.txt", "r", encoding="utf-8") as f:
                streamer_info_content = f.read().strip()
                if streamer_info_content:
                    print("ℹ️  [설정 블록] 'streamer_info.txt'를 성공적으로 감지하여 문맥 분석 컨텍스트에 포함합니다.")
        except Exception as e:
            print(f"⚠️ 'streamer_info.txt' 파일을 읽는 중 오류 발생: {e}")

    formatting_constraint = (
        "\n\n🚨 [추가 필독 엄격 제약사양]\n"
        "출력 시 '[대분류;소주제]' 규격의 그룹 헤더 태그와 타임스탬프 기반 요약 행을 제외하고, "
        "마크다운 강조(**)나 기타 불필요한 공백 텍스트는 생성하지 마세요."
    )

    if streamer_info_content:
        combined_instruction = f"{system_instruction}\n\n{streamer_info_content}\n\n{formatting_constraint}"
    else:
        combined_instruction = f"{system_instruction}\n\n{formatting_constraint}"

    print(f"\n✨ 3단계: Gemini AI 기반 타임라인 가공 중 (방송인 컨텍스트 및 그룹 태깅 적용)...")
    
    try:
        client = genai.Client(api_key=api_key)
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=f"분석 대상 스크립트:\n{raw_script}",
            config=types.GenerateContentConfig(
                system_instruction=combined_instruction,
                temperature=TEMPERATURE,
                max_output_tokens=MAX_OUTPUT_TOKENS,
                top_p=TOP_P
            )
        )
        timeline_result = response.text.strip()
    except Exception as e:
        print(f"\n❌ Gemini API 실행 오류: {e}")
        return ""
            
    print("🛠️ 4단계: 타임라인 점수 필터링 및 그룹 빌드 중...")
    filtered_timeline = filter_timeline_by_score(timeline_result)
    
    print("🛠️ 5단계: 타임라인 최종 안전 필터링 및 찌꺼기 후처리 정제 중...")
    timeline_lines = [line.strip() for line in filtered_timeline.split('\n') if line.strip()]
    cleaned_lines = []
    
    timestamp_pattern = re.compile(r"^\[?\d{1,2}:\d{2}(?::\d{2})?\]?")

    for line in timeline_lines:
        line = re.sub(r"\s*\|\|\s*step:\d+", "", line).strip()
        if not line:
            continue
        
        line = re.sub(r"^[-*+•\s]+", "", line).strip()
        
        if line.startswith("[") and ";" in line and line.endswith("]"):
            cleaned_lines.append(line)
            continue
            
        if "🤖 이 댓글은" in line or line.startswith("[00:00:00]"):
            continue
        
        if timestamp_pattern.match(line):
            cleaned_lines.append(line)
            
    return "\n".join(cleaned_lines)