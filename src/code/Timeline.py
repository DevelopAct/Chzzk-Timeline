import os
import sys
import json
import warnings
import subprocess
import re
import time
from yt_dlp import YoutubeDL
from google import genai
from google.genai import types
from google.genai.errors import APIError
from pydantic import BaseModel, Field
from typing import List, Optional

warnings.filterwarnings("ignore", category=UserWarning)

CONFIG_FILE = "config.json"
GEMINI_MODEL = "gemini-3.1-flash-lite"      
TEMPERATURE = 0.1  
MAX_OUTPUT_TOKENS = 4000             
TOP_P = 0.95                          

class TimelineItem(BaseModel):
    group_large: str = Field(description="상황이 속한 큰 사건 대분류 (예: 저스트 채팅, 게임 방송, 공지사항 등)")
    group_small: str = Field(description="상황이 속한 세부 주제 (예: 시청자 티키타카, 룰 세팅, 오버워치 합방 등)")
    timestamp: str = Field(description="[HH:MM:SS] 또는 [MM:SS] 형태의 타임스탬프 시간")
    wf: int = Field(description="재미 점수 (0 ~ 50점)")
    wi: int = Field(description="중요 점수 (0 ~ 50점)")
    content: str = Field(description="방송 중 일어난 상황에 대한 명사형 요약 내용")

class TimelineResponse(BaseModel):
    items: List[TimelineItem] = Field(description="추출된 타임라인 아이템 목록")

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

def transcribe_chzzk_audio(audio_path, chzzk_url, start_percent, model_size="turbo"):
    print(f"\n🎙️ 2단계: Faster-Whisper AI 엔진 구동 ({model_size}) - 대본 추출 및 시간 복원 중...")
    
    if not os.path.exists(audio_path):
        print("❌ 분석할 오디오 파일이 존재하지 않습니다.")
        return ""

    total_duration = get_video_duration(chzzk_url)
    start_secs = int(total_duration * (start_percent / 100.0))
    
    try:
        from faster_whisper import WhisperModel
        try:
            print("⚡ 외장 GPU 가속(CUDA) 가동을 시도합니다...")
            model = WhisperModel(model_size, device="cuda", compute_type="float16")
            print("🚀 [GPU 가속 성공] NVIDIA CUDA 백엔드로 초고속 STT 연산을 시작합니다.")
        except Exception as gpu_error:
            print(f"⚠️ GPU 로드 실패 ({gpu_error}). 안전하게 CPU 모드로 전환합니다.")
            model = WhisperModel(model_size, device="cpu", compute_type="int8")
            print("🐌 [CPU 전환 완료] CPU 환경에서 대본 추출을 진행합니다.")
            
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

def generate_chzzk_timeline(raw_script, actual_title="VOD제목", chzzk_url="", api_key=""):
    try:
        with open("prompt.txt", "r", encoding="utf-8") as f:
            system_instruction = f.read()
    except:
        system_instruction = "당신은 VOD 편집자입니다. 대본을 분석하여 사건하고 상황을 타임라인 형식으로 요약하세요."

    streamer_info_content = ""
    if os.path.exists("streamer_info.txt"):
        try:
            with open("streamer_info.txt", "r", encoding="utf-8") as f:
                streamer_info_content = f.read().strip()
                if streamer_info_content:
                    print("ℹ️  [설정 블록] 'streamer_info.txt'를 성공적으로 감지하여 문맥 분석 컨텍스트에 포함합니다.")
        except Exception as e:
            print(f"⚠️ 'streamer_info.txt' 파일을 읽는 중 오류 발생: {e}")

    clean_script_text = raw_script.replace(" ", "").replace("\n", "")
    script_lines_count = len([l for l in raw_script.split("\n") if l.strip()])
    
    dynamic_density_hint = ""
    if script_lines_count > 0:
        avg_chars_per_line = len(clean_script_text) / script_lines_count
        if avg_chars_per_line >= 16.0 or script_lines_count >= 150:
            dynamic_density_hint = (
                "\n\n[💡 AI 편집기 통계 신호 안내]\n"
                "- 현재 구간 특징: 단위 시간당 대사 전환 빈도가 높고 발화 밀도가 매우 촘촘합니다.\n"
                "- 분석 가이드: 여러 사람이 대화를 나누는 '합방/디코 소통'이거나, 오디오가 쉬지 않고 채워지는 '외부 영상 시청' 상태일 확률이 극도로 높습니다. "
                "주인공 스트리머가 대화에 실시간으로 끼어들어 양방향 상호작용을 하는지, 아니면 배경 음성을 가만히 들으며 혼자 리액션하는지 문맥을 엄격히 구별해 타임라인을 도출하십시오."
            )
        else:
            dynamic_density_hint = (
                "\n\n[💡 AI 편집기 통계 신호 안내]\n"
                "- 현재 구간 특징: 발화 간격이 여유롭고 단독 오디오 위주로 구성되어 있습니다.\n"
                "- 분석 가이드: 주인공 스트리머 혼자 방송을 이끌어가는 '단독 저스트 채팅/소통'이거나, 오디오가 빈번하게 비는 잔잔한 게임 구간일 확률이 높습니다. "
                "독백 및 시청자 피드백을 중심으로 깔끔하게 상황을 요약하십시오."
            )

    if streamer_info_content:
        combined_instruction = f"{system_instruction}\n\n{streamer_info_content}"
    else:
        combined_instruction = f"{system_instruction}"

    input_content = f"{dynamic_density_hint}\n\n분석 대상 스크립트:\n{raw_script}"

    print(f"\n✨ 3단계: Gemini AI 기반 타임라인 가공 중 (Response Schema 연동 구조)...")
    
    max_retries = 5
    retry_delay = 10
    response_json_text = ""

    for attempt in range(max_retries):
        try:
            time.sleep(2.0)
            client = genai.Client(api_key=api_key)
            response = client.models.generate_content(
                model=GEMINI_MODEL,
                contents=input_content,
                config=types.GenerateContentConfig(
                    system_instruction=combined_instruction,
                    temperature=TEMPERATURE,
                    max_output_tokens=MAX_OUTPUT_TOKENS,
                    top_p=TOP_P,
                    response_mime_type="application/json",
                    response_schema=TimelineResponse,
                )
            )
            response_json_text = response.text.strip()
            if response_json_text:
                time.sleep(2.0)
                break
        except Exception as e:
            err_msg = str(e)
            is_quota_error = any(x in err_msg for x in ["429", "RESOURCE_EXHAUSTED", "quota", "limit", "exceeded"])
            is_server_error = any(x in err_msg for x in ["503", "UNAVAILABLE", "internal", "server"])
            
            if is_quota_error:
                print(f"⚠️ [Gemini 할당량 한도 도달] 429 RESOURCE_EXHAUSTED 예외 방어 기동. 안전 잠금을 해제하기 위해 {retry_delay}초간 제어 대기합니다... (시도: {attempt + 1}/{max_retries})")
                time.sleep(retry_delay)
                retry_delay = min(120, retry_delay * 2)
                continue
            elif is_server_error:
                print(f"⚠️ [Gemini 서버 과부하 상태] 일시적인 503 과부하 신호 감지. {retry_delay}초 후 처리를 재시도합니다... (시도: {attempt + 1}/{max_retries})")
                time.sleep(retry_delay)
                retry_delay = min(120, retry_delay * 2)
                continue
            else:
                print(f"❌ Gemini API 연산 중 일반 예외 필터링 차단: {e}")
                return ""

    if not response_json_text:
        print("❌ [구간 스킵 안내] 설정된 자동 최대 재시도 임계값 내에 할당량 잠금이 해제되지 않아 본 청크 분석을 안전하게 건너뜁니다.")
        return ""

    print("🛠️ 4단계: 타임라인 구조적 점수 필터링 및 그룹 빌드 중...")
    print("🛠️ 5단계: 타임라인 최종 안전 필터링 및 찌꺼기 후처리 정제 중...")
    
    try:
        data = json.loads(response_json_text)
        items = data.get("items", [])
        
        final_output_lines = []
        current_header = None
        current_group_lines = []
        
        for item in items:
            gl = item.get("group_large", "").strip()
            gs = item.get("group_small", "").strip()
            ts = item.get("timestamp", "").strip()
            wf = item.get("wf", 0)
            wi = item.get("wi", 0)
            content = item.get("content", "").strip()
            
            wt = wf + wi
            step = max(1, min(10, round((wt / 100) * 10)))
            
            if step >= 4 or wi >= 40:
                header_tag = f"[{gl};{gs}]"
                
                if current_header != header_tag:
                    if current_header and current_group_lines:
                        final_output_lines.append(current_header)
                        final_output_lines.extend(current_group_lines)
                        final_output_lines.append("")
                    current_header = header_tag
                    current_group_lines = []
                
                cleaned_content = re.sub(r"\s*\(\s*\d+\s*단계\s*\)\s*", " ", content).strip()
                cleaned_content = re.sub(r"^\s*\(\s*\d+\s*단계\s*\)\s*", "", cleaned_content).strip()
                
                current_group_lines.append(f"{ts} {cleaned_content}")
                
        if current_header and current_group_lines:
            final_output_lines.append(current_header)
            final_output_lines.extend(current_group_lines)
            
        return "\n".join(final_output_lines)
    except Exception as parse_error:
        print(f"❌ JSON 스키마 파싱 실패: {parse_error}")
        return ""