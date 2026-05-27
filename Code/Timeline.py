# Timeline.py
import os
import sys
import json
import warnings
import subprocess
from yt_dlp import YoutubeDL
from google import genai
from google.genai import types
import ollama
import webbrowser
import platform

warnings.filterwarnings("ignore", category=UserWarning)

CONFIG_FILE = "config.json"

def load_config():
    """설정 파일 로드 (API 키 대신 모델 선택값 확인)"""
    if not os.path.exists(CONFIG_FILE):
        default_config = {
            "TARGET_CHANNEL_ID": "채널_ID_입력",
            "SELECTED_MODEL": "teddylee777/llama-3-korean-8b-instruct"
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


def get_gemini_client(api_key):
    return genai.Client(api_key=api_key)


def get_video_duration(chzzk_url):
    ydl_opts = {'quiet': True, 'nocheckcertificate': True}
    with YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(chzzk_url, download=False)
        return info.get('duration', 0)


def download_chzzk_vod_audio(chzzk_url, vod_id, start_percent=0.0, end_percent=100.0, output_filename="chzzk_vod_audio"):
    """
    [개선된 오디오 수집 엔진]
    1. 보존형: voicepalette/VOD_{vod_id} 폴더에 데이터를 남겨 다음 실행 시 즉시 재사용
    2. 무손실: 원본 조각 파일을 삭제하지 않음
    """
    # 1. 고유 폴더 경로 생성 (voicepalette/VOD_ID)
    specific_palette_dir = os.path.join(os.getcwd(), "voicepalette", f"VOD_{vod_id}")
    os.makedirs(specific_palette_dir, exist_ok=True)
    
    # 최종 결과물 경로
    final_output_mp3 = os.path.join(specific_palette_dir, f"{output_filename}.mp3")
    raw_audio_tmpl = os.path.join(specific_palette_dir, "raw_full_audio")
    
    # 2. 캐시 확인: 이미 컷팅된 파일이 존재하면 즉시 반환 (네트워크 통신 차단)
    if os.path.exists(final_output_mp3):
        print(f"\n✨ [캐시 적중] 이미 저장된 오디오를 사용합니다: {final_output_mp3}")
        return final_output_mp3

    print(f"\n🎵 1단계: 치지직 오디오 데이터 수집 엔진 가동 (멀티스레드 가속)...")
    
    total_duration = get_video_duration(chzzk_url)
    if total_duration == 0:
        print("❌ VOD 메타데이터 파싱 실패.")
        return ""
        
    start_secs = int(total_duration * (start_percent / 100.0))
    end_secs = int(total_duration * (end_percent / 100.0))
    duration_secs = end_secs - start_secs
    
    # 3. yt-dlp 옵션: 조각 파일 보존을 위한 keepvideo 설정
    ydl_opts = {
        'format': 'worstaudio/worst',
        'outtmpl': f'{raw_audio_tmpl}.%(ext)s',
        'keepvideo': True,             # 👈 [핵심] 다운로드 후 원본 조각 파일 유지
        'quiet': True,
        'nocheckcertificate': True,
        'concurrent_fragment_downloads': 16,  
        'socket_timeout': 3,
        'retries': 3,
        'fragment_retries': 3,
        'skip_unavailable_fragments': True,
        'noplaylist': True,
    }

    print("📥 [네트워크 가속] 16스레드 병렬 통신으로 보이스 파레트 낚아채는 중...")
    with YoutubeDL(ydl_opts) as ydl:
        info_dict = ydl.extract_info(chzzk_url, download=True)
        downloaded_ext = info_dict.get('ext', 'ts')
        downloaded_raw_path = f"{raw_audio_tmpl}.{downloaded_ext}"

    if not os.path.exists(downloaded_raw_path):
        print("❌ 원본 오디오 스트림 다운로드 실패.")
        return ""

    print("⚡ [로컬 가속] 타겟 구간 오디오 칼정밀 단순 컷팅 진행 중...")
    
    try:
        import imageio_ffmpeg
        ffmpeg_bin = imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        ffmpeg_bin = "ffmpeg"

    cmd = [
        ffmpeg_bin, '-y',
        '-ss', str(start_secs),
        '-i', downloaded_raw_path,
        '-t', str(duration_secs),
        '-acodec', 'libmp3lame',
        '-b:a', '96k',
        final_output_mp3
    ]
    subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # 💡 [핵심 교정]: downloaded_raw_path 삭제 코드 제거 (원본 보존)
    print("✅ 타겟 구간 지정 오디오 고속 워프 추출 및 원본 파일 보존 완료!")
    return final_output_mp3


def transcribe_chzzk_audio(audio_path, chzzk_url, start_percent, model_size="turbo"):
    """
    2단계: [절대 시간 오프셋 복원 버전]
    잘려진 오디오 조각의 상대 시간을 원본 VOD의 절대 시간대(HH:MM:SS)로 자동 역산하여 스크립트를 생성합니다.
    """
    print(f"\n🎙️ 2단계: Faster-Whisper AI 엔진 구동 ({model_size}) - 대본 추출 및 시간 복원 중...")
    
    if not os.path.exists(audio_path):
        print("❌ 분석할 오디오 파일이 존재하지 않습니다.")
        return ""

    # 1. 원본 VOD의 전체 길이 정보를 가져와 9% 지점이 실제 몇 초였는지 절대 시작 시각 역산
    total_duration = get_video_duration(chzzk_url)
    start_secs = int(total_duration * (start_percent / 100.0))
    
    try:
        from faster_whisper import WhisperModel
        # CPU 환경에서 안정적이고 빠르게 돌아가도록 int8 경량화 모델 로드
        model = WhisperModel(model_size, device="cpu", compute_type="int8")
    except ImportError:
        print("❌ faster-whisper 라이브러리가 설치되어 있지 않습니다.")
        return ""

    # 오디오 분석 가동
    segments, info = model.transcribe(
        audio_path, 
        beam_size=5,
        word_timestamps=False,
        repetition_penalty=1.4,          # 뇌절/도배 방지용 패널티
        compression_ratio_threshold=1.8  # 환각 현상 제어 역치
    )
    
    script_lines = []
    
    for segment in segments:
        # 💡 [핵심 교정]: 조각 파일의 시간(segment.start)에 원본 시작 초(start_secs)를 더함
        absolute_secs = int(segment.start + start_secs)
        
        # 보정된 절대 초 데이터를 기반으로 정확한 시, 분, 초 역산
        h = absolute_secs // 3600
        m = (absolute_secs % 3600) // 60
        s = absolute_secs % 60
        
        # 깨끗하게 포맷팅된 문장 생성
        timestamp_str = f"[{h:02d}:{m:02d}:{s:02d}]"
        text_content = segment.text.strip()
        
        if text_content:
            script_lines.append(f"{timestamp_str} {text_content}")
            # 터미널 실시간 모니터링 출력
            print(f"  {timestamp_str} {text_content}")

    raw_script = "\n".join(script_lines)
    
    # 디버깅 및 무조건 보존을 위해 temp_audio 폴더 안에 텍스트 파일로 영구 저장
    script_cache_path = audio_path.replace(".mp3", "_raw_script.txt")
    with open(script_cache_path, "w", encoding="utf-8") as f:
        f.write(raw_script)
        
    print(f"✅ 원본 오프셋이 복원된 생대본 추출 완료! (보존 경로: {script_cache_path})")
    return raw_script

def ensure_ollama_installed():
    """Ollama가 설치되어 있는지 확인하고, 없으면 다운로드 페이지로 안내"""
    try:
        subprocess.run(["ollama", "--version"], capture_output=True, check=True)
        return True
    except (FileNotFoundError, subprocess.CalledProcessError):
        print("\n❌ [중요] 시스템에 'Ollama'가 설치되어 있지 않습니다.")
        print("📥 자동 설치를 위해 다운로드 페이지를 엽니다...")
        
        # OS별 다운로드 페이지 연결
        if platform.system() == "Windows":
            webbrowser.open("https://ollama.com/download/windows")
        else:
            webbrowser.open("https://ollama.com/download")
            
        print("💡 페이지에서 설치 프로그램을 다운로드하여 설치를 완료한 후,")
        print("   터미널을 완전히 껐다가 다시 켜서 프로그램을 재실행하세요.")
        sys.exit(1)

def ensure_model_exists(model_name):
    """모델이 로컬에 없으면 자동으로 pull 수행"""
    # 먼저 Ollama 설치 여부 확인
    ensure_ollama_installed()
    
    try:
        result = subprocess.run(["ollama", "list"], capture_output=True, text=True)
        if model_name not in result.stdout:
            print(f"\n📥 모델 '{model_name}'이 없습니다. 자동 설치를 시작합니다...")
            # Ollama 서버가 켜져 있는지 확인 (연결 시도)
            subprocess.run(["ollama", "pull", model_name], check=True)
            print(f"✅ 모델 '{model_name}' 설치 완료!")
    except Exception as e:
        print(f"\n⚠️ Ollama 서비스가 실행 중인지 확인하세요! (작업표시줄 아이콘 확인)")
        print(f"상세 에러: {e}")
        sys.exit(1)

def get_user_model_choice(default_model):
    """사용자에게 모델 선택권을 주는 함수"""
    models = ["teddylee777/llama-3-korean-8b-instruct", "anpigon/eeve-korean-10.8b"]
    print(f"\n📦 사용할 모델을 선택하세요 (기본값: {default_model}):")
    for i, model in enumerate(models):
        prefix = "★ " if model == default_model else "  "
        print(f"{prefix}[{i}] {model}")
    
    try:
        choice = input("\n👉 번호 입력 (엔터키를 누르면 기본값 사용): ").strip()
        return models[int(choice)] if choice.isdigit() and 0 <= int(choice) < len(models) else default_model
    except:
        return default_model

def generate_chzzk_timeline(raw_script, actual_title="VOD제목", chzzk_url=""):
    """
    Ollama 공식 모델 전용 타임라인 생성 함수 (100% 다운로드 완료 대기 버전)
    """
    # 1. 프롬프트 로드 (prompt.txt)
    try:
        with open("prompt.txt", "r", encoding="utf-8") as f:
            system_instruction = f.read()
    except:
        system_instruction = "당신은 VOD 편집자입니다. 대본을 분석하여 사건과 상황을 타임라인 형식으로 요약하세요."

    # 2. 설정 및 모델 로드
    CONFIG_FILE = "config.json"
    config = {}
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            config = json.load(f)
    
    # ⚠️ 중요: 허깅페이스 개인 저장소명이 아닌, Ollama 공식 등록 한국어 모델 사용
    model_name = config.get("SELECTED_MODEL", "koesn/llama3-8b-instruct")

    print(f"\n✨ 3단계: 로컬 AI ({model_name}) 기반 타임라인 가공 중...")
    
    try:
        # 로컬 저장소 모델 체크
        try:
            local_models_data = ollama.list()
            local_models = [m.get('model', m.get('name', '')) for m in local_models_data.get('models', [])]
        except:
            local_models = []

        # 모델이 로컬에 없으면, 100% 완료될 때까지 붙잡고 대기(Stream)
        if model_name not in local_models and f"{model_name}:latest" not in local_models:
            print(f"📥 로컬 저장소에 '{model_name}' 모델이 없습니다.")
            print(f"📥 최초 1회 실시간 자동 다운로드를 시작합니다 (약 4.7GB)...")
            
            # ★ 핵심: stream=True로 설정하여 다운로드 과정을 한 땀 한 땀 추적하며 코드를 대기시킵니다.
            current_status = ""
            for progress in ollama.pull(model_name, stream=True):
                status = progress.get('status', '')
                if status != current_status:
                    print(f"🔄 다운로드 상태: {status}")
                    current_status = status
            
            print("✅ [완료] 모델 파일 다운로드가 100% 완료되었습니다! 분석을 시작합니다.")

        # 3. 로컬 AI 모델 호출 (이제 다운로드가 무조건 끝났으므로 안심하고 호출 가능)
        response = ollama.chat(
            model=model_name,
            messages=[
                {'role': 'system', 'content': system_instruction},
                {'role': 'user', 'content': f"분석 대상 스크립트:\n{raw_script}"}
            ],
            options={'temperature': 0.2}
        )
        timeline_result = response['message']['content'].strip()
        
    except Exception as e:
        print(f"\n❌ Ollama 실행 오류: {e}")
        print("💡 팁: config.json의 SELECTED_MODEL이 올바른 Ollama 공식 모델명인지 확인하세요.")
        return ""
            
    # 4. 타임라인 최종 후처리
    print("🛠️ 4단계: 타임라인 최종 후처리 중...")
    
    timeline_lines = [line.strip() for line in timeline_result.split('\n') if line.strip()]
    timeline_lines = [line for line in timeline_lines if not line.startswith("[00:00:00]")]
    
    title_header = f"[00:00:00] {actual_title if actual_title else 'VOD제목'}"
    timeline_lines.insert(0, title_header)
    
    return "\n".join(timeline_lines)