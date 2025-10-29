import streamlit as st
import base64
import json

# --- Placeholders (실제 구현은 아래 함수 대체) ---
def extract_melody(audio_bytes):
    # basic-pitch/CREPE로 멜로디 추출 → [(pitch_hz, duration_sec)] 리스트
    return [{"pitch": 261.63, "dur": 0.5}, {"pitch": 293.66, "dur": 0.5}]  # 도-레

def simplify_and_transpose(notes, target_key="C"):
    # 양자화/음역 제한/이조
    return notes

def notes_to_abc(notes, key="C"):
    # 간단 변환 예시(실제는 음고→음명, 길이→ABC 길이로 매핑)
    return f"X:1\nT:Simple\nM:4/4\nK:{key}\nC D E F|G A B c|"

def solfege_syllables(notes, key="C", movable_do=True):
    return ["도","레","미","파","솔","라","시","도"]

def estimate_chords(notes, key="C"):
    # I/IV/V 규칙 기반 예시
    return ["C","F","G7","C"]

def add_piano_accompaniment(notes, chords, pattern="block"):
    # 패턴 적용 → ABC 또는 MIDI로 합성
    return "X:1\nT:Melody+Accomp\nM:4/4\nK:C\n%%score (T1 B1)\nV:T1\nC D E F|G A B c|\nV:B1\n[C,E,G,C] z z z | [F,A,C] z z z |"

def abc_download_link(abc_str, filename="score.abc"):
    b = abc_str.encode("utf-8")
    href = f'<a download="{filename}" href="data:text/plain;base64,{base64.b64encode(b).decode()}">ABC 다운로드</a>'
    return href

st.title("초등 음악 도우미 🎵")

tab1, tab2 = st.tabs(["감상곡 해석 & 간단 악보", "계이름 읽기 & 화음 반주"])

with tab1:
    audio = st.file_uploader("오디오 파일 업로드 (mp3/wav)", type=["mp3","wav"])
    if audio:
        raw = audio.read()
        notes = extract_melody(raw)
        notes = simplify_and_transpose(notes, target_key="C")
        abc  = notes_to_abc(notes, key="C")
        st.subheader("간단 멜로디 악보")

        # JS에 안전하게 전달하기 위해 JSON 인코딩 사용 (백틱/따옴표/개행 이스케이프)
        abc_json = json.dumps(abc)
        st.components.v1.html(f"""
        <div id="paper"></div>
        <script src="https://cdn.jsdelivr.net/npm/abcjs@6.4.0/bin/abcjs_basic.min.js"></script>
        <script>
          var abc = {abc_json};
          ABCJS.renderAbc("paper", abc);
        </script>
        """, height=220)
        st.markdown(abc_download_link(abc), unsafe_allow_html=True)

        # 초등 눈높이 해설(LLM 연동 부분은 서버 사이드에서)
        st.info("이 곡은 경쾌하고 밝은 느낌이에요. 높은 음으로 올라갈 때 호흡을 길게 가져가 보세요!")

with tab2:
    score = st.file_uploader("악보 파일 업로드 (MIDI/MusicXML/ABC)", type=["mid","midi","musicxml","xml","abc"])
    if score:
        # 실제 구현: music21으로 파싱 → notes 추출
        notes = [{"pitch": 261.63, "dur": 1.0},{"pitch": 329.63, "dur": 1.0}]
        syll = solfege_syllables(notes, key="C", movable_do=True)
        st.write("계이름(이동도):", " ".join(syll))

        chords = estimate_chords(notes, key="C")
        abc_with_acc = add_piano_accompaniment(notes, chords, pattern="block")
        st.subheader("멜로디 + 피아노 반주")

        # 동일하게 안전하게 전달
        abc_acc_json = json.dumps(abc_with_acc)
        st.components.v1.html(f"""
        <div id="paper2"></div>
        <script src="https://cdn.jsdelivr.net/npm/abcjs@6.4.0/bin/abcjs_basic.min.js"></script>
        <script>
          var abc = {abc_acc_json};
          ABCJS.renderAbc("paper2", abc);
        </script>
        """, height=260)
