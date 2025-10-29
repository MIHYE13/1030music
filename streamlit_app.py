
import streamlit as st
import io
import base64
import tempfile
from typing import List, Tuple, Optional

# ---- Optional heavy deps guarded by try/except ----
try:
    # basic_pitch needs tensorflow; keep optional for audio -> MIDI
    from basic_pitch.inference import predict
    from basic_pitch import ICASSP_2022_MODEL_PATH
    _BASIC_PITCH_AVAILABLE = True
except Exception:
    _BASIC_PITCH_AVAILABLE = False

from music21 import converter, stream, note, chord, meter, key as m21key, instrument, clef, tempo, interval, pitch

# ------------------ Utility: music21 <-> MusicXML string ------------------
def stream_to_musicxml_string(s: stream.Stream) -> str:
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".musicxml")
    fp = tmp.name
    tmp.close()
    s.write('musicxml', fp=fp)
    with open(fp, 'r', encoding='utf-8') as f:
        xml = f.read()
    return xml

# ------------------ Audio -> Melody (music21 Stream) via basic-pitch ------------------
def audio_bytes_to_m21_melody(audio_bytes: bytes, sr_hint: Optional[int]=None) -> Optional[stream.Part]:
    """
    Uses basic-pitch to estimate melody as MIDI-like notes and returns a music21 Part (monophonic).
    Returns None if basic-pitch is not available or inference fails.
    """
    if not _BASIC_PITCH_AVAILABLE:
        return None
    try:
        import numpy as np
        import soundfile as sf  # basic-pitch expects float32 wav-like arrays
        # Read bytes -> numpy audio (handle mp3/wav via soundfile)
        with io.BytesIO(audio_bytes) as bio:
            audio, sr = sf.read(bio, dtype='float32', always_2d=False)
        # Run basic-pitch prediction
        # predict returns (est_note_events, est_note_list, est_activation)
        _, note_list, _ = predict(
            audio,
            sr=sr,
            model_or_path=ICASSP_2022_MODEL_PATH,
            save_midi=False,
            onset_threshold=0.5,
            frame_threshold=0.2,
            min_note_len=11  # frames
        )
        # note_list: [start_time, end_time, midi_pitch, amplitude]
        # Build monophonic melody by taking highest amplitude at any overlapping time
        events = []
        for start, end, midi, amp in note_list:
            dur_quarter = (end - start) * 2.0  # rough: 120bpm -> 2 quarter notes per second (heuristic)
            if dur_quarter <= 0.125:
                continue
            n = note.Note(int(midi))
            n.duration.quarterLength = max(0.25, round(dur_quarter*2)/2)  # snap to 1/2 subdivision
            events.append((start, n))
        # Sort by onset and build Part
        events.sort(key=lambda x: x[0])
        p = stream.Part(id="Melody")
        p.insert(0, instrument.Piano())
        p.insert(0, clef.TrebleClef())
        p.append(meter.TimeSignature('4/4'))
        for _, n in events:
            p.append(n)
        return p
    except Exception as e:
        st.warning(f"오디오에서 멜로디 추출에 실패했습니다: {e}")
        return None

# ------------------ Simplification & Transposition to C major ------------------
def analyze_key_simple(s: stream.Stream) -> m21key.Key:
    try:
        k = s.analyze('key')
        return k
    except Exception:
        # default C major
        return m21key.Key('C')

def transpose_to_C_major(s: stream.Stream) -> stream.Stream:
    k = analyze_key_simple(s)
    try:
        i = interval.Interval(k.tonic, pitch.Pitch('C'))
        s2 = s.transpose(i)
        # Force Key signature explicitly to C major
        for ks in s2.recurse().getElementsByClass(meter.TimeSignature):
            # leave meter as is
            pass
        s2.insert(0, m21key.Key('C'))
        return s2
    except Exception:
        # If transposition fails, return original
        return s

def clamp_octave_C4_C5(p: note.Note) -> note.Note:
    # Bring pitch into C4..C5 range by transposing octaves
    target_low = pitch.Pitch('C4').midi
    target_high = pitch.Pitch('C5').midi
    if not isinstance(p, note.Note):
        return p
    while p.pitch.midi < target_low:
        p.transpose(12, inPlace=True)
    while p.pitch.midi > target_high:
        p.transpose(-12, inPlace=True)
    return p

def simplify_melody_to_elementary(part: stream.Part) -> stream.Part:
    """
    - Quantize durations to {0.5, 1.0, 2.0} (eighth, quarter, half) with preference for longer notes.
    - Remove ornaments (grace not handled here).
    - Clamp pitch to C4..C5.
    - Set tempo to 90 BPM for elementary practice.
    """
    qset = [0.5, 1.0, 2.0, 4.0]
    simp = stream.Part(id="MelodySimplified")
    simp.insert(0, instrument.Piano())
    simp.insert(0, clef.TrebleClef())
    # Meter
    ts = part.recurse().getElementsByClass(meter.TimeSignature)
    simp.append(ts[0] if ts else meter.TimeSignature('4/4'))
    # Tempo
    simp.insert(0, tempo.MetronomeMark(number=90))
    for el in part.flat.notesAndRests:
        if isinstance(el, note.Note):
            n = note.Note(el.pitch)
            # quantize duration
            dur = float(el.quarterLength)
            target = min(qset, key=lambda x: (abs(x - dur), x))  # tie-breaker to longer
            n.duration.quarterLength = target
            clamp_octave_C4_C5(n)
            simp.append(n)
        elif isinstance(el, note.Rest):
            r = note.Rest()
            dur = float(el.quarterLength)
            target = min(qset, key=lambda x: (abs(x - dur), x))
            r.duration.quarterLength = target
            simp.append(r)
    # Transpose to C major (다장조)
    simpC = transpose_to_C_major(simp)
    # Force Key Signature visible
    simpC.insert(0, m21key.Key('C'))
    return simpC

# ------------------ Solfege (movable-do in C major) ------------------
SOLFEGE_C_MAJOR = { 'C':'도','D':'레','E':'미','F':'파','G':'솔','A':'라','B':'시' }

def attach_solfege_lyrics(melody: stream.Part, movable_do: bool=True) -> None:
    """
    Attach Korean solfege syllables as lyrics to each note in the melody.
    For MVP we assume C major after transposition (movable-do == fixed-do in C here).
    """
    for n in melody.recurse().notes:
        try:
            letter = n.pitch.name[0]  # 'C','D',...
            syl = SOLFEGE_C_MAJOR.get(letter, '')
            n.lyric = syl
        except Exception:
            pass

# ------------------ Chord estimation and accompaniment ------------------
TRIADS_C = {
    "C": ["C4","E4","G4"],
    "F": ["F3","A3","C4"],
    "G": ["G3","B3","D4"],
    "Am": ["A3","C4","E4"]
}

def pick_chord_for_measure(measure: stream.Measure) -> str:
    letters = []
    for n in measure.notes:
        if isinstance(n, note.Note):
            letters.append(n.pitch.name[0])
    # Very naive heuristic: prioritize G if G/B present near cadence, else F if lots of F/A, else Am if A/C present, else C
    if any(l in ('G','B') for l in letters):
        return "G"
    if letters.count('F') + letters.count('A') >= 2:
        return "F"
    if letters.count('A') + letters.count('C') >= 2:
        return "Am"
    return "C"

def build_piano_accompaniment(melodyC: stream.Part) -> stream.Part:
    """
    Left-hand accompaniment: half-note block chords per bar (beats 1 and 3).
    """
    acc = stream.Part(id="PianoLH")
    acc.insert(0, instrument.Piano())
    acc.insert(0, clef.BassClef())
    ts = melodyC.recurse().getElementsByClass(meter.TimeSignature)
    acc.append(ts[0] if ts else meter.TimeSignature('4/4'))
    mm = melodyC.makeMeasures(inPlace=False)
    measures = mm.getElementsByClass(stream.Measure)
    for m in measures:
        chord_name = pick_chord_for_measure(m)
        pitches = TRIADS_C.get(chord_name, TRIADS_C["C"])
        # Half note chord on beat 1 and 3
        ch1 = chord.Chord(pitches)
        ch1.duration.quarterLength = 2.0
        ch2 = chord.Chord(pitches)
        ch2.duration.quarterLength = 2.0
        meas = stream.Measure(number=m.measureNumber)
        meas.append(ch1)
        meas.append(ch2)
        acc.append(meas)
    return acc

# ------------------ Score assembly ------------------
def assemble_score(melody_part: stream.Part, with_acc: bool=True, with_solfege: bool=False) -> stream.Score:
    sc = stream.Score(id="Score")
    p1 = simplify_melody_to_elementary(melody_part)
    if with_solfege:
        attach_solfege_lyrics(p1, movable_do=True)
    sc.insert(0, p1)
    if with_acc:
        acc = build_piano_accompaniment(p1)
        sc.insert(0, acc)
    return sc

# ------------------ Rendering via OSMD inside Streamlit ------------------
OSMD_HTML_TMPL = """
<div id="osmd-container" style="border:1px solid #333; border-radius:10px; padding:8px;"></div>
<div style="margin-top:8px;">
  <button id="btnPlay">▶ Play</button>
  <button id="btnPause">⏸ Pause</button>
  <button id="btnStop">⏹ Stop</button>
</div>
<script src="https://cdn.jsdelivr.net/npm/opensheetmusicdisplay@1.8.7/build/opensheetmusicdisplay.min.js"></script>
<script src="https://unpkg.com/osmd-audio-player/dist/OsmdAudioPlayer.js"></script>
<script>
(async () => {
  const xmlBase64 = "%s";
  const xml = atob(xmlBase64);
  const container = document.getElementById("osmd-container");
  const osmd = new opensheetmusicdisplay.OpenSheetMusicDisplay(container, {
    drawFromMeasureNumber: 1,
    drawUpToMeasureNumber: Number.MAX_SAFE_INTEGER,
    followCursor: true,
    drawPartNames: true,
    drawMeasureNumbers: false,
    backend: "svg",
    autoResize: true,
  });
  await osmd.load(xml);
  await osmd.render();

  const player = new osmdAudioPlayer.OsmdAudioPlayer();
  await player.loadScore(osmd);

  document.getElementById("btnPlay").onclick = async () => { await player.play(); };
  document.getElementById("btnPause").onclick = async () => { player.pause(); };
  document.getElementById("btnStop").onclick = async () => { player.stop(); };
})();
</script>
"""

def render_musicxml_with_osmd(xml_str: str, height: int = 420):
    xml_b64 = base64.b64encode(xml_str.encode('utf-8')).decode('ascii')
    html = OSMD_HTML_TMPL % xml_b64
    st.components.v1.html(html, height=height, scrolling=True)

# ------------------ File parsing ------------------
def parse_score_file(upload) -> Optional[stream.Part]:
    name = upload.name.lower()
    data = upload.read()
    try:
        if name.endswith(('.mid', '.midi')):
            s = converter.parse(io.BytesIO(data))
        elif name.endswith(('.musicxml', '.xml')):
            s = converter.parse(io.BytesIO(data))
        elif name.endswith('.abc'):
            s = converter.parse(io.BytesIO(data))
        elif name.endswith('.pdf'):
            # PDF requires OMR (e.g., Audiveris) -> not handled in-app
            st.warning("PDF 악보는 자동 인식(OMR)이 필요합니다. Audiveris 등으로 MusicXML로 변환 후 업로드하세요.")
            return None
        else:
            st.error("지원하지 않는 악보 형식입니다. MIDI/MusicXML/ABC를 사용해주세요.")
            return None
        # Take top melody line: choose highest notes per offset
        part = stream.Part(id="ParsedMelody")
        part.insert(0, instrument.Piano())
        part.insert(0, clef.TrebleClef())
        part.append(meter.TimeSignature('4/4'))
        flat = s.flat.notesAndRests
        last_offset = None
        highest_at_offset = None
        for el in flat:
            off = float(el.offset)
            if last_offset is None:
                last_offset = off
                highest_at_offset = el
                continue
            if abs(off - last_offset) < 1e-6:
                # same onset: choose higher pitch
                if isinstance(el, note.Note) and isinstance(highest_at_offset, note.Note):
                    if el.pitch.midi > highest_at_offset.pitch.midi:
                        highest_at_offset = el
            else:
                # emit previous
                if isinstance(highest_at_offset, note.Note):
                    n = note.Note(highest_at_offset.pitch)
                    n.duration.quarterLength = float(highest_at_offset.quarterLength)
                    part.append(n)
                elif isinstance(highest_at_offset, note.Rest):
                    r = note.Rest()
                    r.duration.quarterLength = float(highest_at_offset.quarterLength)
                    part.append(r)
                # reset
                last_offset = off
                highest_at_offset = el
        # flush last
        if highest_at_offset is not None:
            if isinstance(highest_at_offset, note.Note):
                n = note.Note(highest_at_offset.pitch)
                n.duration.quarterLength = float(highest_at_offset.quarterLength)
                part.append(n)
            elif isinstance(highest_at_offset, note.Rest):
                r = note.Rest()
                r.duration.quarterLength = float(highest_at_offset.quarterLength)
                part.append(r)
        return part
    except Exception as e:
        st.error(f"악보 파싱 실패: {e}")
        return None

# ------------------ Streamlit UI ------------------
st.set_page_config(page_title="초등 음악 도우미", page_icon="🎼", layout="centered")
st.title("🎼 초등 음악 도우미 (멜로디 단순화 · 계이름 · 반주 · 자동플레이)")

st.caption("입력: 오디오(mp3/wav) 또는 악보(MIDI/MusicXML/ABC). PDF는 OMR 변환 필요.")

with st.expander("설정 / 옵션"):
    add_solfege = st.checkbox("계이름(도·레·미) 표시", value=True)
    add_accomp  = st.checkbox("기본 화음 반주 추가 (왼손 하프노트 블록 코드)", value=True)

col1, col2 = st.columns(2, vertical_alignment="top")
with col1:
    st.subheader("① 오디오 → 악보")
    audio_file = st.file_uploader("오디오 업로드 (mp3/wav)", type=["mp3", "wav"], key="audio")
    if audio_file is not None:
        audio_bytes = audio_file.read()
        if not _BASIC_PITCH_AVAILABLE:
            st.warning("basic-pitch 미설치/미동작: `pip install basic-pitch tensorflow soundfile` 후 다시 시도하세요.")
        else:
            mel = audio_bytes_to_m21_melody(audio_bytes)
            if mel:
                sc = assemble_score(mel, with_acc=add_accomp, with_solfege=add_solfege)
                xml = stream_to_musicxml_string(sc)
                st.success("멜로디 추출 및 악보 생성 완료 (다장조, 단순화). 아래에서 악보를 보고 재생하세요.")
                render_musicxml_with_osmd(xml, height=520)
                st.download_button("MusicXML 다운로드", data=xml.encode('utf-8'), file_name="melody_c_major.musicxml", mime="application/vnd.recordare.musicxml+xml")

with col2:
    st.subheader("② 악보 파일 → 계이름/반주/플레이")
    score_file = st.file_uploader("악보 업로드 (MIDI/MusicXML/ABC/PDF)", type=["mid","midi","musicxml","xml","abc","pdf"], key="score")
    if score_file is not None:
        part = parse_score_file(score_file)
        if part:
            sc = assemble_score(part, with_acc=add_accomp, with_solfege=add_solfege)
            xml = stream_to_musicxml_string(sc)
            st.success("악보 변환 완료 (다장조, 멜로디 단순화 포함). 아래에서 악보를 보고 재생하세요.")
            render_musicxml_with_osmd(xml, height=520)
            st.download_button("MusicXML 다운로드", data=xml.encode('utf-8'), file_name="score_c_major_with_acc.musicxml", mime="application/vnd.recordare.musicxml+xml")

st.markdown("""
---
**메모**  
- PDF에서 **계이름 자동 기재**는 광학악보인식(OMR)이 필요합니다. 무료 OMR 도구 **Audiveris**로 PDF→MusicXML 변환 후 업로드하면 본 앱이 계이름을 가사로 붙여줍니다.  
- 자동 반주는 **C/F/G/Am** 4화음 규칙 기반의 초등용 단순 패턴(왼손 하프노트)입니다.
""")
 
