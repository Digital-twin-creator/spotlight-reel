#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
テスト用のダミー動画・ダミーJSON・効果音・フォントを生成するスクリプト。

生成物:
    examples/dummy_input.mp4   1080x1920 30fps 8秒のテスト動画
    examples/sample.json       render.py にそのまま渡せるプロジェクトJSON
    assets/sfx/shakin.wav      効果音（金属的な高域シマー）
    assets/sfx/don.wav         効果音（サブベースのインパクト）
    assets/sfx/impact.wav      効果音（donより低く重い、ラストロゴ着地の既定SE）
    assets/fonts/NotoSansJP-Bold.ttf  日本語フォント（取得できれば）

実行:
    python make_dummy.py
"""

import json
import math
import os
import subprocess
import sys
import urllib.request
import wave

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
EXAMPLES_DIR = os.path.join(SCRIPT_DIR, "examples")
ASSETS_DIR = os.path.join(SCRIPT_DIR, "assets")
FONT_DIR = os.path.join(ASSETS_DIR, "fonts")
SFX_DIR = os.path.join(ASSETS_DIR, "sfx")
BRUSH_DIR = os.path.join(ASSETS_DIR, "brushes")

W, H, FPS, DURATION_SEC = 1080, 1920, 30, 8
SR = 48000

FONT_URLS = [
    # 可変フォント（Boldウェイトを指定して使う。render.py側で対応）
    "https://raw.githubusercontent.com/google/fonts/main/ofl/notosansjp/NotoSansJP%5Bwght%5D.ttf",
    # 予備: Google Fonts CDN（静的Regularだが、無いよりはまし）
    "https://fonts.gstatic.com/s/notosansjp/v53/-F6jfjtqLzI2JPCgQBnw7HFyzSD-AsregP8VFBEj75s.ttf",
]
FONT_PATH = os.path.join(FONT_DIR, "NotoSansJP-Bold.ttf")

ANTON_FONT_URLS = [
    # タイトル用の欧文ディスプレイ体（Google Fonts OFL）
    "https://raw.githubusercontent.com/google/fonts/main/ofl/anton/Anton-Regular.ttf",
]
ANTON_FONT_PATH = os.path.join(FONT_DIR, "Anton-Regular.ttf")

# エディタでテロップの行ごとに選べる追加フォント一式（すべてGoogle Fonts OFL）。
# 日本語対応を優先して選定：明朝体・ゴシック体・見出し向けの太いディスプレイ体・丸ゴシックなど、
# 雰囲気の異なる選択肢を揃える。
EXTRA_TITLE_FONTS = [
    {
        "key": "notoserifjp",
        "label": "Noto Serif JP",
        "urls": ["https://raw.githubusercontent.com/google/fonts/main/ofl/notoserifjp/NotoSerifJP%5Bwght%5D.ttf"],
        "path": os.path.join(FONT_DIR, "NotoSerifJP-Bold.ttf"),
        "variable": True,   # 可変フォント。render.py側でBoldウェイトを選ぶ
    },
    {
        "key": "zenkakugothicnew",
        "label": "Zen Kaku Gothic New",
        "urls": ["https://raw.githubusercontent.com/google/fonts/main/ofl/zenkakugothicnew/ZenKakuGothicNew-Bold.ttf"],
        "path": os.path.join(FONT_DIR, "ZenKakuGothicNew-Bold.ttf"),
        "variable": False,
    },
    {
        "key": "delagothicone",
        "label": "Dela Gothic One",
        "urls": ["https://raw.githubusercontent.com/google/fonts/main/ofl/delagothicone/DelaGothicOne-Regular.ttf"],
        "path": os.path.join(FONT_DIR, "DelaGothicOne-Regular.ttf"),
        "variable": False,  # このファミリーはRegularウェイトのみ（元から極太のディスプレイ体）
    },
    {
        "key": "shipporimincho",
        "label": "Shippori Mincho",
        "urls": ["https://raw.githubusercontent.com/google/fonts/main/ofl/shipporimincho/ShipporiMincho-Bold.ttf"],
        "path": os.path.join(FONT_DIR, "ShipporiMincho-Bold.ttf"),
        "variable": False,
    },
    {
        "key": "mplusrounded1c",
        "label": "M PLUS Rounded 1c",
        "urls": ["https://raw.githubusercontent.com/google/fonts/main/ofl/mplusrounded1c/MPLUSRounded1c-Bold.ttf"],
        "path": os.path.join(FONT_DIR, "MPLUSRounded1c-Bold.ttf"),
        "variable": False,
    },
]


def log(msg):
    print(msg, flush=True)


# ---------------------------------------------------------------------------
# ダミー動画
# ---------------------------------------------------------------------------

def circle_positions(t, w=W, h=H):
    """「人物」役の円2つの中心座標(t秒時点)を返す。左右にゆっくり往復する。"""
    cx1 = w * 0.30 + math.sin(t * 0.6) * w * 0.10
    cy1 = h * 0.35 + math.cos(t * 0.4) * h * 0.04
    cx2 = w * 0.70 + math.sin(t * 0.5 + 1.5) * w * 0.10
    cy2 = h * 0.55 + math.cos(t * 0.35 + 1.0) * h * 0.04
    return (cx1, cy1), (cx2, cy2)


def make_gradient_bg(t, w=W, h=H):
    """時間とともにゆっくり変化する背景グラデーションを作る"""
    yy = np.linspace(0, 1, h, dtype=np.float32).reshape(h, 1)
    xx = np.linspace(0, 1, w, dtype=np.float32).reshape(1, w)
    shift = (math.sin(t * 0.3) + 1) / 2
    r = (80 + 100 * yy * (0.5 + 0.5 * shift)).astype(np.float32)
    g = (60 + 80 * xx).astype(np.float32)
    b = (120 + 100 * (1 - yy) * (0.5 + 0.5 * (1 - shift))).astype(np.float32)
    img = np.zeros((h, w, 3), np.float32)
    img[:, :, 0] = b   # B
    img[:, :, 1] = g   # G
    img[:, :, 2] = r   # R
    return np.clip(img, 0, 255).astype(np.uint8)


def render_dummy_video(out_path, w=W, h=H):
    """OpenCVで毎フレーム描画し、ffmpegにパイプしてH.264 MP4を作る（既定は縦動画1080x1920）"""
    ffmpeg = "ffmpeg"
    n_frames = DURATION_SEC * FPS
    cmd = [ffmpeg, "-y", "-v", "error",
           "-f", "rawvideo", "-pix_fmt", "bgr24",
           "-s", f"{w}x{h}", "-r", str(FPS), "-i", "pipe:0",
           "-c:v", "libx264", "-preset", "medium", "-crf", "18",
           "-pix_fmt", "yuv420p", out_path]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)

    for i in range(n_frames):
        t = i / FPS
        frame = make_gradient_bg(t, w, h)
        (x1, y1), (x2, y2) = circle_positions(t, w, h)
        cv2.circle(frame, (int(x1), int(y1)), int(w * 0.12), (60, 90, 220), -1, cv2.LINE_AA)
        cv2.circle(frame, (int(x2), int(y2)), int(w * 0.10), (200, 140, 60), -1, cv2.LINE_AA)
        proc.stdin.write(frame.tobytes())
        if i % 30 == 0:
            print(f"\r  動画生成 {i}/{n_frames}", end="", flush=True)
    print(f"\r  動画生成 {n_frames}/{n_frames}", flush=True)
    proc.stdin.close()
    proc.wait()
    if proc.returncode != 0:
        raise RuntimeError("ダミー動画の生成に失敗しました（ffmpeg）")
    return out_path


def gen_dummy_video_large_subject(out_path, w=W, h=H, duration_sec=2, fps=FPS):
    """
    自動切り抜き(rembg)のマスク面積チェック（render.pyのvalidate_auto_alpha。
    画面の5%未満/85%超なら「切り抜き失敗」として止める）のテスト用に、実写の人物の
    ように画面に対して十分な大きさ（画面の10%強）を持つ「被写体」（大きな円）を
    描いた短いダミー動画を作る。通常のダミー動画（circle_positions由来の円）は
    小さく、rembgの検出結果がこのチェックの下限(5%)を割り込みテストが不安定に
    なるため、専用に用意する。
    """
    ffmpeg = "ffmpeg"
    n_frames = int(duration_sec * fps)
    cmd = [ffmpeg, "-y", "-v", "error",
           "-f", "rawvideo", "-pix_fmt", "bgr24",
           "-s", f"{w}x{h}", "-r", str(fps), "-i", "pipe:0",
           "-c:v", "libx264", "-preset", "medium", "-crf", "18",
           "-pix_fmt", "yuv420p", out_path]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    cx, cy = int(w * 0.5), int(h * 0.42)
    r = int(min(w, h) * 0.28)
    for i in range(n_frames):
        t = i / fps
        frame = make_gradient_bg(t, w, h)
        cv2.circle(frame, (cx, cy), r, (60, 90, 220), -1, cv2.LINE_AA)
        proc.stdin.write(frame.tobytes())
    proc.stdin.close()
    proc.wait()
    if proc.returncode != 0:
        raise RuntimeError("大型被写体ダミー動画の生成に失敗しました（ffmpeg）")
    return out_path


def render_dummy_video_vfr(out_path, w=W, h=H, base_fps=60, sub=2, duration_sec=DURATION_SEC,
                            stall_every=15, stall_extra_subframes=3):
    """
    iPhoneのスクリーン録画（負荷でフレーム間隔がばらつく、実効fpsが基準よりやや低くなる）を
    模した、可変フレームレート(VFR)のテスト動画を作る。

    base_fps（見た目上のフレームレート、既定60）の各フレームを、内部的には
    nominal_fps = base_fps*sub の細かい時間刻みで sub 回ずつ「画素まで完全に同一な」
    フレームとして複製して書き出す。stall_everyフレームに1回だけ、複製回数を
    stall_extra_subframes 分だけ余計に増やす（＝負荷で次の絵が描けず、直前のフレームが
    長く据え置かれた瞬間を模す）。エンコード時に mpdecimate で完全一致フレームの連続を
    検出・間引きし、-vsync vfr で間引いた分だけ提示間隔を延ばして出力する。

    人物役の円の動きを実時間の何倍も速く進める（大きく・はっきり動かす）ことで、
    フレームごとの絵の変化を意図的に大きくし、mpdecimateの画素差分ベースの判定が
    「本当に同一なstallフレーム」と「動きのある通常フレーム」を確実に区別できるようにする
    （変化がなだらかすぎると、動きのある本来のフレームまで誤って間引かれてしまうため）。

    結果として、
      - r_frame_rate（コンテナが宣言する基準fps。フレーム間隔の最小公倍数に近い値）は
        nominal_fps相当になり
      - avg_frame_rate（配信フレーム数からの実効平均fps）は、stall分だけbase_fpsより
        わずかに低くなる
    という、実機VFR録画に典型的な「両者の乖離」を持つファイルになる。
    """
    ffmpeg = "ffmpeg"
    nominal_fps = base_fps * sub
    n_logical = int(duration_sec * base_fps)
    motion_speed = 6.0   # 円の動きを速める倍率（フレーム間の絵の変化を大きくするため）
    cmd = [ffmpeg, "-y", "-v", "error",
           "-f", "rawvideo", "-pix_fmt", "bgr24",
           "-s", f"{w}x{h}", "-r", str(nominal_fps), "-i", "pipe:0",
           "-vf", "mpdecimate",
           "-vsync", "vfr",
           "-c:v", "libx264", "-preset", "medium", "-crf", "18",
           "-pix_fmt", "yuv420p", out_path]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)

    written = 0
    for i in range(n_logical):
        t = i / base_fps
        frame = make_gradient_bg(t, w, h)
        (x1, y1), (x2, y2) = circle_positions(t * motion_speed, w, h)
        cv2.circle(frame, (int(x1), int(y1)), int(w * 0.12), (60, 90, 220), -1, cv2.LINE_AA)
        cv2.circle(frame, (int(x2), int(y2)), int(w * 0.10), (200, 140, 60), -1, cv2.LINE_AA)
        hold = sub
        if stall_every > 0 and i > 0 and i % stall_every == 0:
            hold += stall_extra_subframes   # 負荷で据え置かれた瞬間を模す（同一フレームを長く複製）
        payload = frame.tobytes()
        for _ in range(hold):
            proc.stdin.write(payload)
            written += 1
        if i % 60 == 0:
            print(f"\r  VFR動画生成 {i}/{n_logical}", end="", flush=True)
    print(f"\r  VFR動画生成 {n_logical}/{n_logical}（実フレーム書き込み数 {written}）", flush=True)
    proc.stdin.close()
    proc.wait()
    if proc.returncode != 0:
        raise RuntimeError("VFRダミー動画の生成に失敗しました（ffmpeg）")
    return out_path


# ---------------------------------------------------------------------------
# ダミー音声（動画に合成する交互トーン）
# ---------------------------------------------------------------------------

def make_tone_track():
    """440Hzと660Hzが1秒ごとに交互に鳴るステレオトーンを作る"""
    n = DURATION_SEC * SR
    t = np.arange(n, dtype=np.float32) / SR
    freq = np.where((t.astype(np.int64) % 2) == 0, 440.0, 660.0)
    # 秒の切り替わりでプチノイズが出ないよう位相を積算して連続にする
    phase = np.cumsum(2 * np.pi * freq / SR).astype(np.float32)
    tone = 0.25 * np.sin(phase)
    stereo = np.stack([tone, tone], axis=1)
    return stereo


def mux_audio_into_video(video_path, audio_samples, sr=SR):
    """既存の動画に音声トラックを追加する（動画は再エンコードしない）"""
    tmp_wav = video_path + ".tmp.wav"
    pcm = np.clip(audio_samples, -1.0, 1.0)
    pcm = (pcm * 32767.0).astype(np.int16)
    with wave.open(tmp_wav, "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(pcm.tobytes())

    tmp_out = video_path + ".withaudio.mp4"
    cmd = ["ffmpeg", "-y", "-v", "error", "-i", video_path, "-i", tmp_wav,
           "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest", tmp_out]
    subprocess.run(cmd, check=True)
    os.replace(tmp_out, video_path)
    os.remove(tmp_wav)


# ---------------------------------------------------------------------------
# 効果音（shakin.wav / don.wav）
# ---------------------------------------------------------------------------

def _delay_tail(sig, sr, taps):
    """(delay_sec, amp)のリストぶん、sigを遅らせて減衰コピーを重ねる簡易リバーブ/ディレイ"""
    out = sig.copy()
    n = len(sig)
    for delay_sec, amp in taps:
        d = int(round(delay_sec * sr))
        if d >= n:
            continue
        out[d:] += sig[:n - d] * amp
    return out


def _limiter_normalize(sig, peak_target=0.95):
    """ピークがpeak_targetになるよう正規化する（リミッター的な仕上げ。クリッピング防止）"""
    peak = float(np.max(np.abs(sig))) if sig.size else 0.0
    if peak <= 1e-9:
        return sig
    return sig * (peak_target / peak)


def synth_shakin(sr=SR, dur=0.9):
    """
    「シャキーン」風（映画予告編ふうに刷新）：金属的な高域シマー
    （複数の非整数倍音を重ねたベル/シンバル風合成）＋アタック直後の上昇スイープ＋短い残響。
    """
    n = int(sr * dur)
    t = np.linspace(0, dur, n, endpoint=False, dtype=np.float32)

    base = 1400.0
    # (倍音比, 振幅, 減衰の速さ)。非整数比にすることで金属的な「シャキーン」感を出す
    partials = [(1.00, 1.00, 6.0), (2.03, 0.60, 9.0), (3.29, 0.42, 13.0),
                (4.77, 0.28, 18.0), (5.88, 0.18, 24.0), (7.16, 0.12, 30.0)]
    sig = np.zeros(n, np.float32)
    for ratio, amp, decay in partials:
        env = np.exp(-decay * t)
        sig += (amp * np.sin(2 * np.pi * base * ratio * t) * env).astype(np.float32)

    # アタック直後の「キラッ」とした上昇スイープ
    sweep_freq = 2200.0 + 2600.0 * np.exp(-t / 0.05)
    sweep_env = np.exp(-14.0 * t).astype(np.float32)
    sweep = np.sin(2 * np.pi * np.cumsum(sweep_freq) / sr).astype(np.float32) * sweep_env * 0.35
    sig = sig + sweep

    sig = _delay_tail(sig, sr, [(0.03, 0.30), (0.07, 0.18), (0.13, 0.10), (0.22, 0.05)])
    sig = _limiter_normalize(sig)
    return np.stack([sig, sig], axis=1).astype(np.float32)


def synth_don(sr=SR, dur=1.6):
    """
    「ドン」風（映画予告編ふうに刷新）：40〜60Hzのサブベース（アタックでピッチが
    半音ほど下がる）＋アタックの打撃ノイズ＋1.2秒以上の減衰リバーブ尾。
    ピークで歪まないようリミッター的に正規化して仕上げる。
    """
    n = int(sr * dur)
    t = np.linspace(0, dur, n, endpoint=False, dtype=np.float32)

    base_freq = 50.0  # 40〜60Hzのサブベース帯域
    semitone = 2.0 ** (1.0 / 12.0) - 1.0
    freq = base_freq + base_freq * semitone * np.exp(-t / 0.06)  # 半音ほど高い所から素早く落ち着く
    phase = 2 * np.pi * np.cumsum(freq) / sr
    sub = np.sin(phase).astype(np.float32) * np.exp(-t / 0.55).astype(np.float32)

    noise = np.random.default_rng(0).standard_normal(n).astype(np.float32)
    transient = noise * np.exp(-t / 0.02).astype(np.float32) * 0.5   # アタックの打撃ノイズ

    sig = sub * 0.85 + transient
    sig = _delay_tail(sig, sr, [(0.05, 0.50), (0.12, 0.32), (0.22, 0.20),
                                 (0.38, 0.12), (0.60, 0.07), (0.95, 0.04)])
    sig = _limiter_normalize(sig)
    return np.stack([sig, sig], axis=1).astype(np.float32)


def synth_impact(sr=SR, dur=2.2):
    """
    「impact」：ラストロゴの着地用に、donよりも低く・重く・尾を長くしたインパクト音。
    logo.sfxの既定値として使う（donはフリーズのSE用途に残す）。
    """
    n = int(sr * dur)
    t = np.linspace(0, dur, n, endpoint=False, dtype=np.float32)

    base_freq = 42.0
    semitone = 2.0 ** (1.0 / 12.0) - 1.0
    freq = base_freq + base_freq * semitone * np.exp(-t / 0.08)
    phase = 2 * np.pi * np.cumsum(freq) / sr
    sub = np.sin(phase).astype(np.float32) * np.exp(-t / 0.8).astype(np.float32)

    noise = np.random.default_rng(1).standard_normal(n).astype(np.float32)
    transient = noise * np.exp(-t / 0.025).astype(np.float32) * 0.6

    sig = sub * 0.9 + transient
    sig = _delay_tail(sig, sr, [(0.06, 0.55), (0.14, 0.38), (0.26, 0.26), (0.44, 0.18),
                                 (0.70, 0.12), (1.05, 0.08), (1.45, 0.05)])
    sig = _limiter_normalize(sig)
    return np.stack([sig, sig], axis=1).astype(np.float32)


def synth_test_riser(sr=SR, content_dur=1.6, silence_dur=0.4):
    """
    render.pyのalign="end_at_landing"（音の終わりを着地に合わせる）の検証用テスト音声
    （既定で計2.0秒）。前半content_dur秒は音量が徐々に高まる上昇スイープ、後半silence_dur秒は
    完全な無音（ゼロサンプル）にする。減衰カーブに頼らず厳密にゼロを敷き詰めることで、
    trailing_silence_trimmed_length()が「後半の無音を除いた長さ」を1サンプルの誤差もなく
    検出できることを、まぎれのない形で確認できるようにしている。
    戻り値: (stereo音声(N,2)float32, 無音区間を除いた実効サンプル数)
    """
    n_content = int(round(sr * content_dur))
    n_silence = int(round(sr * silence_dur))
    t = np.linspace(0, content_dur, n_content, endpoint=False, dtype=np.float32)
    freq = 200.0 + 3000.0 * (t / content_dur) ** 2  # 徐々に高くなる周波数（ライザーらしい上昇感）
    env = (t / content_dur).astype(np.float32)       # 0→1のリニア音量上昇
    phase = 2 * np.pi * np.cumsum(freq) / sr
    sig = np.sin(phase).astype(np.float32) * env
    sig = np.concatenate([sig, np.zeros(n_silence, np.float32)]).astype(np.float32)
    return np.stack([sig, sig], axis=1).astype(np.float32), n_content


def synth_test_impact_offset_peak(sr=SR, dur=1.0, peak_t=0.4):
    """
    render.pyのalign="peak_at_landing"（音声のピーク位置を着地に合わせる）の検証用テスト音声。
    ピークが先頭ではなく中程（既定でpeak_t=0.4秒）にあることを保証するため、ピーク前の
    弱い前振れ（プリロール）とピーク後の減衰を、いずれもピークよりはっきり小さい振幅に
    抑えたうえで、ピーク位置のサンプルだけ明示的に振幅1.0にする（丸め誤差での位置ズレ防止）。
    戻り値: (stereo音声(N,2)float32, ピークのサンプル位置)
    """
    n = int(round(sr * dur))
    peak_idx = int(round(sr * peak_t))
    t = np.linspace(0, dur, n, endpoint=False, dtype=np.float32)
    pre_env = 0.15 * np.exp(-((t - peak_t * 0.4) ** 2) / (2 * (peak_t * 0.15) ** 2))
    post_env = np.where(t < peak_t, 0.0, np.exp(-(t - peak_t) / 0.15)).astype(np.float32)
    env = np.maximum(pre_env, post_env).astype(np.float32)
    sig = (np.sin(2 * np.pi * 220.0 * t).astype(np.float32) * env).astype(np.float32)
    sig[peak_idx] = 1.0
    return np.stack([sig, sig], axis=1).astype(np.float32), peak_idx


def write_wav(path, samples, sr=SR):
    pcm = np.clip(samples, -1.0, 1.0)
    pcm = (pcm * 32767.0).astype(np.int16)
    with wave.open(path, "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(pcm.tobytes())


def ensure_sfx(path, synth_fn, label):
    """
    assets/sfx/ にあるファイルは render.py がそのまま読み込んで使う（合成音は
    あくまで仮の音）。フリー効果音サイトの音源に差し替えたユーザーのファイルを
    再実行のたびに上書きしないよう、既に存在する場合はスキップする
    （ensure_font/ensure_title_fontと同じ方針）。
    """
    if os.path.exists(path):
        log(f"{label}は既に存在します（そのまま使います）: {path}")
        return
    write_wav(path, synth_fn())
    log(f"  仮の効果音（合成音）を生成しました: {path}")


# ---------------------------------------------------------------------------
# フォント取得
# ---------------------------------------------------------------------------

def ensure_font_file(urls, dest_path, label):
    """dest_path が無ければ urls を順に試してダウンロードする"""
    if os.path.exists(dest_path):
        log(f"フォントは既に存在します: {dest_path}")
        return
    for url in urls:
        log(f"{label}をダウンロード中: {url}")
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "Mozilla/5.0 (make_dummy.py)"})
            with urllib.request.urlopen(req, timeout=20) as resp:
                data = resp.read()
            with open(dest_path, "wb") as f:
                f.write(data)
            log(f"{label}を保存しました: {dest_path}")
            return
        except Exception as exc:  # noqa: BLE001 - ネットワーク不通環境向けフォールバック
            print(f"[warn] このURLからは取得できませんでした（{exc}）", file=sys.stderr)
    print(f"[warn] {label}を取得できませんでした。"
          "render.py は代替フォント（未指定時はOpenCVの既定フォント）にフォールバックします。",
          file=sys.stderr)


def ensure_font():
    """NotoSansJP-Bold.ttf が無ければ Google Fonts からダウンロードする"""
    ensure_font_file(FONT_URLS, FONT_PATH, "日本語フォント")


def ensure_title_font():
    """Anton-Regular.ttf（欧文タイトル用）が無ければ Google Fonts からダウンロードする"""
    ensure_font_file(ANTON_FONT_URLS, ANTON_FONT_PATH, "タイトル用欧文フォント(Anton)")


def ensure_extra_title_fonts():
    """テロップの行ごとのフォント選択肢として使う追加フォント一式（EXTRA_TITLE_FONTS）を取得する"""
    for spec in EXTRA_TITLE_FONTS:
        ensure_font_file(spec["urls"], spec["path"], f"テロップ選択用フォント({spec['label']})")


# ---------------------------------------------------------------------------
# サンプルJSON
# ---------------------------------------------------------------------------

def make_sample_json(video_filename):
    """circle_positions と同じ軌道をなぞる2回分のフリーズを含むJSONを作る"""

    def stroke_over_circle(t, cx_func_index):
        (p1, p2) = circle_positions(t)
        cx, cy = (p1 if cx_func_index == 0 else p2)
        r = W * (0.12 if cx_func_index == 0 else 0.10)
        pts = []
        for k in range(9):
            ang = -math.pi + (2 * math.pi) * k / 8
            x = cx + r * 0.8 * math.cos(ang)
            y = cy + r * 0.8 * math.sin(ang)
            pts.append([round(x / W, 4), round(y / H, 4)])
        return pts

    freezes = [
        {
            "time": 2.5,
            # 日本語 → title_font_jp（NotoSansJP）が使われる。text_backing="box"を明示し、
            # 座布団（半透明の角丸矩形）を敷いたテロップの見本にする
            "name": {"lines": [{"text": "赤い人", "text_backing": "box"}]},
            "sfx": "shakin",
            "brush_shape": "round",
            # circle_positions(2.5)の1人目は画面中心より左寄りに描かれるため、
            # style.shadow.direction="auto" により影は左へスライドして現れる見本になる
            "strokes": [{"width": 0.10, "points": stroke_over_circle(2.5, 0)}],
        },
        {
            "time": 5.5,
            "name": "BLUE GUY",            # 欧文 → title_font（Anton）が使われる
            "sfx": "don",
            "brush_shape": "hake",
            # circle_positions(5.5)の2人目は画面中心より右寄りに描かれるため、
            # style.shadow.direction="auto" により影は右へスライドして現れる見本になる。
            # このフリーズだけ shadow.source="auto" にして、カラー化はブラシ（color_source既定）、
            # 影の形だけ自動切り抜きのアルファを使う「混在」パターンの見本にする
            # （自動切り抜きはこのフリーズだけ実行される）
            "shadow": {"source": "auto"},
            "strokes": [{"width": 0.09, "points": stroke_over_circle(5.5, 1)}],
        },
    ]

    project = {
        "version": 1,
        "video": video_filename,
        "output": {"width": W, "height": H, "fps": FPS},
        "style": {
            # 1フリーズの流れ「①塗り(reveal_sec)→②ズレ(slide_sec)→③静止(hold_sec)」。
            # hold_secは既定値(2.0)より少し長めに取り、バウンス/ロゴが映える余裕を持たせる
            "reveal_sec": 0.6,
            "slide_sec": 0.4,
            "hold_sec": 2.2,
            "brush_width": 0.12,
            "brush_shape": "round",
            "mono_contrast": 1.3,
            "background": "mono",
            "font": "assets/fonts/NotoSansJP-Bold.ttf",
            "title_font": "assets/fonts/Anton-Regular.ttf",
            "title_font_jp": "assets/fonts/NotoSansJP-Bold.ttf",
            "title_bounce": True,
            "audio_during_freeze": "mute",
            # 影（フィルム色）演出：人物出現完了後、人物だけをdistanceぶんスライドさせ、
            # 元の位置に残る影(color/alpha)を覗かせる。direction="auto"は人物マスクの
            # X重心が画面中心より右か左かで自動的に左右を切り替える（後述のfreezesの
            # コメント参照。2つのフリーズでそれぞれ左・右にスライドする見本になっている）。
            # sourceは既定"same"（color_sourceと同じマスク）。2つめのフリーズだけ
            # freeze側で"auto"に上書きしている（上のfreezesのコメント参照）
            "shadow": {
                "color": "#1A1A2E", "alpha": 0.8, "distance": 0.03,
                "direction": "auto", "offset_y": 0.02, "blur": 0.0,
            },
            # 人物の輪郭線（subject_outline）。mask_style=outlineとは独立の演出で、
            # 人物マスクの外周に細い縁取りを重ねる（座布団と併用できることの見本にもなる）
            "subject_outline": {"enabled": True, "width": 0.006},
        },
        "freezes": freezes,
        "logo": {
            "image": "store_logo.png",
            "at": "last_freeze",
            "background": "auto",          # ロゴの四隅平均色（このダミーロゴは黒背景なので画面が黒くなる）
            "sfx": "impact",               # donより低く重い、ラストロゴ着地の既定SE
            # duration_sec / start_width_ratio / hold_big_sec / shrink_sec / settle_sec /
            # sweep_sec / flash_strength / width_ratio は省略時の既定値のまま使う
        },
        # 常時表示の透かしロゴ（watermark）。ラストロゴと同じダミー画像を流用し、
        # 動画全体を通して右下に小さく表示され続ける（shine/spinは既定で有効）
        "watermark": {
            "image": "store_logo.png",
            "position": "bottom_right",
            "width_ratio": 0.14,
        },
        # ハッシュタグ表示（hashtags）。telopの既定位置(下寄り)と被らないよう上端に配置し、
        # 座布団（box）でも安全域からはみ出さないことの見本にする
        "hashtags": {
            "text": "#京都 #祇園 #ClubIRIS",
            "position": "top",
            "backing": "box",
            "always": True,
        },
    }
    return project


# ---------------------------------------------------------------------------
# ダミーロゴ（examples/store_logo.png）
# ---------------------------------------------------------------------------

def gen_dummy_logo(out_path):
    """
    テスト用のダミーロゴPNG（透過、丸角バッジに白文字の「STORE」）を生成する。
    render.pyのロゴ自動クロップ（crop_logo_content）の効果を確認しやすいよう、
    意図的に周囲に大きめの透明な余白（画像幅の16%）を持たせている。
    """
    w, h, ss = 640, 360, 2
    img = Image.new("RGBA", (w * ss, h * ss), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    pad = int(w * ss * 0.16)
    draw.rounded_rectangle(
        [pad, pad, w * ss - pad, h * ss - pad],
        radius=40 * ss, fill=(30, 30, 40, 235), outline=(255, 255, 255, 255), width=6 * ss)

    text = "STORE"
    font = None
    font_candidates = [ANTON_FONT_PATH, FONT_PATH]
    for cand in font_candidates:
        if os.path.exists(cand):
            try:
                font = ImageFont.truetype(cand, 100 * ss)
                break
            except Exception:  # noqa: BLE001
                font = None
    if font is not None:
        draw.text((w * ss / 2, h * ss / 2), text, font=font, anchor="mm", fill=(255, 255, 255, 255))
    else:
        arr = np.array(img)
        (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, 3.2 * ss, 6 * ss)
        org = (int(w * ss / 2 - tw / 2), int(h * ss / 2 + th / 2))
        cv2.putText(arr, text, org, cv2.FONT_HERSHEY_SIMPLEX, 3.2 * ss, (255, 255, 255, 255), 6 * ss, cv2.LINE_AA)
        img = Image.fromarray(arr)

    img = img.resize((w, h), Image.LANCZOS)
    img.save(out_path)
    return out_path


def gen_dummy_logo_opaque_black(out_path, jpeg_path=None):
    """
    テスト用のダミーロゴ（不透明・黒背景・アルファ無し、余白多め）を生成する。
    実際の店舗ロゴでよくある「黒背景に白抜き文字、アルファチャンネル無しで書き出された
    PNG/JPG」を想定し、render.pyの自動クロップ（crop_logo_content。アルファが無い画像では
    四隅の背景色との距離で内容を判定する経路を通る）が実際に効くかを確認するための
    テスト画像。gen_dummy_logo()（アルファ有り、余白16%）よりさらに余白を大きく
    （画像幅の30%）取っている。
    jpeg_pathを指定すると、同じ絵柄をJPEG（quality=82）でも書き出す。JPEG圧縮ノイズが
    あっても自動クロップが正しく働くか（四隅1画素だけでなくパッチ平均で背景色を
    推定しているか）の確認に使う。
    """
    w, h, ss = 640, 640, 2
    img = Image.new("RGB", (w * ss, h * ss), (6, 6, 8))
    draw = ImageDraw.Draw(img)
    pad = int(w * ss * 0.30)
    draw.ellipse([pad, pad, w * ss - pad, h * ss - pad],
                 fill=(235, 235, 240), outline=(255, 255, 255), width=4 * ss)

    text = "SHOP"
    font = None
    for cand in (ANTON_FONT_PATH, FONT_PATH):
        if os.path.exists(cand):
            try:
                font = ImageFont.truetype(cand, 70 * ss)
                break
            except Exception:  # noqa: BLE001
                font = None
    if font is not None:
        draw.text((w * ss / 2, h * ss / 2), text, font=font, anchor="mm", fill=(10, 10, 20))
    else:
        arr = np.array(img)
        (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, 2.4 * ss, 5 * ss)
        org = (int(w * ss / 2 - tw / 2), int(h * ss / 2 + th / 2))
        cv2.putText(arr, text, org, cv2.FONT_HERSHEY_SIMPLEX, 2.4 * ss, (10, 10, 20), 5 * ss, cv2.LINE_AA)
        img = Image.fromarray(arr)

    img = img.resize((w, h), Image.LANCZOS)
    img.save(out_path)  # PNGだがアルファ無し(RGB)のまま保存する
    if jpeg_path:
        img.convert("RGB").save(jpeg_path, "JPEG", quality=82)
    return out_path


# ---------------------------------------------------------------------------
# extract.py 検証用のシルエット画像（examples/extract_test_silhouette.png）
#
# 髪風の細線・指風の隙間など「細かい構造」を含む人物シルエットを合成し、
# extract.py（rembgベースの自動切り抜き）が、この細部の境界で0/255の二値
# ではなく連続値のアルファを出せているかを確認するためのテスト画像。
# 実写に近い写真の代わりに使う（examples/には人物写真が無いため）。
# ---------------------------------------------------------------------------

def gen_extract_test_silhouette(out_path, w=W, h=H):
    """extract.py の検証用：髪風の細線と指風の隙間を持つシルエット画像を作る"""
    ss = 4  # スーパーサンプリングで縁を滑らかにする
    W2, H2 = w * ss, h * ss
    img = Image.new("RGB", (W2, H2), (60, 110, 190))
    draw = ImageDraw.Draw(img)
    band_h = H2 // 20
    for yband in range(0, H2, band_h):
        shade = 190 - int(40 * yband / H2)
        draw.rectangle([0, yband, W2, yband + band_h], fill=(60, 100, shade))

    skin = (230, 200, 170)
    shirt = (200, 60, 60)
    hair = (70, 45, 30)

    cx, cy = W2 * 0.5, H2 * 0.26
    head_r = W2 * 0.12
    draw.ellipse([cx - head_r, cy - head_r, cx + head_r, cy + head_r], fill=skin)

    rng = np.random.default_rng(42)
    n_strands = 50
    for i in range(n_strands):
        ang = math.pi * 1.15 + math.pi * 0.7 * (i / (n_strands - 1))
        length = head_r * (1.05 + 0.5 * rng.random())
        x0 = cx + math.cos(ang) * head_r * 0.95
        y0 = cy + math.sin(ang) * head_r * 0.95
        x1 = cx + math.cos(ang) * length
        y1 = cy + math.sin(ang) * length
        midx = (x0 + x1) / 2 + rng.normal(0, W2 * 0.008)
        midy = (y0 + y1) / 2 + rng.normal(0, W2 * 0.004)
        draw.line([(x0, y0), (midx, midy), (x1, y1)], fill=hair, width=max(1, int(W2 * 0.0012)))

    body_top = cy + head_r * 0.85
    body_w = W2 * 0.24
    body_bottom = H2 * 0.60
    draw.rounded_rectangle([cx - body_w / 2, body_top, cx + body_w / 2, body_bottom],
                            radius=W2 * 0.03, fill=shirt)

    hand_y0, hand_y1 = H2 * 0.58, H2 * 0.70
    hand_x0 = cx - body_w * 0.85
    n_fingers = 4
    finger_w = W2 * 0.028
    gap_w = W2 * 0.007
    for f in range(n_fingers):
        fx0 = hand_x0 + f * (finger_w + gap_w)
        draw.rounded_rectangle([fx0, hand_y0, fx0 + finger_w, hand_y1],
                                radius=finger_w * 0.35, fill=skin)
    palm_x1 = hand_x0 + n_fingers * (finger_w + gap_w)
    draw.rounded_rectangle([hand_x0 - W2 * 0.01, hand_y1 - W2 * 0.015, palm_x1, hand_y1 + W2 * 0.05],
                            radius=W2 * 0.02, fill=skin)

    img = img.resize((w, h), Image.LANCZOS)
    img.save(out_path)
    return out_path


# ---------------------------------------------------------------------------
# extract.py モデル比較用の「実写フレームに近い、破綻しやすい構図」の画像
# （examples/extract_test_white_on_white.png）
#
# 実機で自動切り抜きが全面マスク（画面のほぼ全体が前景と誤判定）になった失敗の
# 再現用。被写体（白い服）と背景（白い壁）の明度差がほとんど無く、さらに
# 画面中央付近に強い照明のハイライト（白飛び気味の光）を重ねることで、
# 輪郭のコントラストをさらに削っている。標準モデル(isnet-general-use)と
# 高精度モデル(birefnet-portrait)のアルファを比較する材料として使う。
# ---------------------------------------------------------------------------

def gen_extract_test_white_on_white(out_path, w=W, h=H):
    """extract.py のモデル比較用：白い服・白い壁・強い照明の低コントラスト画像を作る"""
    ss = 4  # スーパーサンプリングで縁を滑らかにする
    W2, H2 = w * ss, h * ss
    img = Image.new("RGB", (W2, H2), (238, 236, 232))
    draw = ImageDraw.Draw(img)

    # 白い壁：わずかな明度ムラ（完全な単色だと逆にrembgが検出しやすくなってしまうため）
    band_h = H2 // 24
    for yband in range(0, H2, band_h):
        shade = 242 - int(14 * yband / H2)
        draw.rectangle([0, yband, W2, yband + band_h], fill=(shade, shade - 1, shade - 4))

    skin = (232, 200, 176)
    shirt = (231, 228, 222)   # 壁とほぼ同じ明度の白い服（意図的に低コントラスト）
    hair = (90, 70, 55)

    cx, cy = W2 * 0.5, H2 * 0.28
    head_r = W2 * 0.11
    draw.ellipse([cx - head_r, cy - head_r, cx + head_r, cy + head_r], fill=skin)

    rng = np.random.default_rng(7)
    n_strands = 40
    for i in range(n_strands):
        ang = math.pi * 1.15 + math.pi * 0.7 * (i / (n_strands - 1))
        length = head_r * (1.0 + 0.4 * rng.random())
        x0 = cx + math.cos(ang) * head_r * 0.95
        y0 = cy + math.sin(ang) * head_r * 0.95
        x1 = cx + math.cos(ang) * length
        y1 = cy + math.sin(ang) * length
        draw.line([(x0, y0), (x1, y1)], fill=hair, width=max(1, int(W2 * 0.001)))

    body_top = cy + head_r * 0.85
    body_w = W2 * 0.30
    body_bottom = H2 * 0.62
    draw.rounded_rectangle([cx - body_w / 2, body_top, cx + body_w / 2, body_bottom],
                            radius=W2 * 0.03, fill=shirt)
    # 服のわずかな陰影（皺）。壁との明度差をわずかに残しつつ、境界の一部をさらに曖昧にする
    for i in range(6):
        fx = cx - body_w * 0.35 + body_w * 0.7 * (i / 5.0)
        draw.line([(fx, body_top + W2 * 0.02), (fx + W2 * 0.01, body_bottom - W2 * 0.02)],
                   fill=(shirt[0] - 10, shirt[1] - 10, shirt[2] - 10), width=max(1, int(W2 * 0.0015)))

    hand_y0, hand_y1 = H2 * 0.60, H2 * 0.71
    hand_x0 = cx - body_w * 0.80
    n_fingers = 4
    finger_w = W2 * 0.026
    gap_w = W2 * 0.006
    for f in range(n_fingers):
        fx0 = hand_x0 + f * (finger_w + gap_w)
        draw.rounded_rectangle([fx0, hand_y0, fx0 + finger_w, hand_y1],
                                radius=finger_w * 0.35, fill=skin)
    palm_x1 = hand_x0 + n_fingers * (finger_w + gap_w)
    draw.rounded_rectangle([hand_x0 - W2 * 0.01, hand_y1 - W2 * 0.015, palm_x1, hand_y1 + W2 * 0.05],
                            radius=W2 * 0.02, fill=skin)

    # 強い照明のハイライト：被写体と壁の両方にまたがる白飛び気味の円形グラデーション。
    # これが被写体輪郭のコントラストをさらに削り、実写の「強い照明」条件に近づける。
    glare = Image.new("L", (W2, H2), 0)
    gdraw = ImageDraw.Draw(glare)
    glare_cx, glare_cy = cx + W2 * 0.12, cy + H2 * 0.05
    for r_ratio, alpha in [(0.42, 60), (0.30, 110), (0.18, 170), (0.08, 220)]:
        r = W2 * r_ratio
        gdraw.ellipse([glare_cx - r, glare_cy - r, glare_cx + r, glare_cy + r], fill=alpha)
    glare = glare.filter(ImageFilter.GaussianBlur(W2 * 0.03))
    white_layer = Image.new("RGB", (W2, H2), (255, 255, 255))
    img = Image.composite(white_layer, img, glare)

    img = img.resize((w, h), Image.LANCZOS)
    img.save(out_path)
    return out_path


# ---------------------------------------------------------------------------
# ブラシ筆先画像（assets/brushes/<shape>.png）
#
# render.py / index.html の両方が同じPNG（白RGB＋アルファ）を「筆先スタンプ」として使う。
# 筆先は「進行方向が+X（右向き）」を基準に描いており、render.py側は
# ストロークの向きに合わせて回転させてから貼り付ける。
# TIP_SIZE（正方形キャンバスの一辺）に対する実際の絵柄のサイズ比は、
# render.py の BRUSH_TIP_SCALE と対応関係にあるので、両者を変更するときは
# 見た目のバランスを見ながら一緒に調整すること。
# ---------------------------------------------------------------------------

TIP_SIZE = 256      # 生成する筆先PNGの一辺（px）
TIP_SS = 4          # 縁を滑らかにするためのスーパーサンプリング倍率


def _new_alpha_canvas(size):
    return Image.new("L", (size, size), 0)


def _finish_tip(alpha_img):
    """Lモード（アルファ）画像を TIP_SIZE にリサイズし、白RGB+そのアルファのRGBAにする"""
    if alpha_img.size[0] != TIP_SIZE:
        alpha_img = alpha_img.resize((TIP_SIZE, TIP_SIZE), Image.LANCZOS)
    rgba = Image.new("RGBA", (TIP_SIZE, TIP_SIZE), (255, 255, 255, 0))
    rgba.putalpha(alpha_img)
    return rgba


def gen_round_tip():
    """round：柔らかい縁を持つ円（従来の丸キャップの見た目に近い）"""
    size = TIP_SIZE * TIP_SS
    img = _new_alpha_canvas(size)
    draw = ImageDraw.Draw(img)
    cx = cy = size / 2.0
    r = size * 0.42
    draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=255)
    img = img.filter(ImageFilter.GaussianBlur(size * 0.012))
    return _finish_tip(img)


def gen_marker_tip():
    """marker：角の丸い長方形。縁はくっきり、不透明度は0.82（重なりで自然に濃くなる）"""
    size = TIP_SIZE * TIP_SS
    img = _new_alpha_canvas(size)
    draw = ImageDraw.Draw(img)
    w, h = size * 0.90, size * 0.62
    x0, y0 = (size - w) / 2.0, (size - h) / 2.0
    x1, y1 = x0 + w, y0 + h
    draw.rounded_rectangle([x0, y0, x1, y1], radius=h * 0.28, fill=255)
    img = img.filter(ImageFilter.GaussianBlur(size * 0.004))
    rgba = _finish_tip(img)
    a = np.asarray(rgba.getchannel("A"), dtype=np.float32) * 0.82
    rgba.putalpha(Image.fromarray(a.astype(np.uint8)))
    return rgba


def gen_hake_tip():
    """hake：横長の楕円ベース＋縦方向の毛筋（濃淡バンド）＋縁のギザつき＋わずかな不透明度ムラ"""
    rng = np.random.default_rng(20260830)
    size = TIP_SIZE * TIP_SS
    cx = cy = size / 2.0
    rx, ry = size * 0.46, size * 0.32

    base = _new_alpha_canvas(size)
    ImageDraw.Draw(base).ellipse([cx - rx, cy - ry, cx + rx, cy + ry], fill=255)
    base_arr = np.asarray(base, dtype=np.float32) / 255.0

    yy, xx = np.mgrid[0:size, 0:size].astype(np.float32)

    # 縁のギザつき：角度に応じて縁の半径を波打たせ、はみ出た部分を欠けさせる
    ang = np.arctan2((yy - cy) / ry, (xx - cx) / rx)
    n_teeth = 40
    jag = 0.06 * np.sin(ang * n_teeth + rng.uniform(0, 2 * math.pi))
    rad_norm = np.sqrt(((xx - cx) / rx) ** 2 + ((yy - cy) / ry) ** 2)
    edge_mask = (rad_norm <= (1.0 + jag)).astype(np.float32)
    base_arr = base_arr * edge_mask

    # 毛筋：y位置ごとに濃淡が変わる縦バンド（本数はランダムだが再現性のため固定シード）
    n_bristles = 16
    band_mul = rng.uniform(0.45, 1.0, size=n_bristles).astype(np.float32)
    band_idx = np.clip(((yy - (cy - ry)) / (2 * ry) * n_bristles).astype(np.int32),
                        0, n_bristles - 1)
    bristle_mul = band_mul[band_idx]

    # わずかな不透明度ムラ
    noise = 1.0 + 0.08 * rng.standard_normal((size, size)).astype(np.float32)

    alpha = np.clip(base_arr * bristle_mul * noise, 0.0, 1.0)
    img = Image.fromarray((alpha * 255).astype(np.uint8), mode="L")
    img = img.filter(ImageFilter.GaussianBlur(size * 0.006))
    return _finish_tip(img)


def gen_spray_tip():
    """spray：円形範囲にランダムな点をまき散らす（中心寄りにやや密度が高い）"""
    rng = np.random.default_rng(20260831)
    size = TIP_SIZE * TIP_SS
    img = Image.new("L", (size, size), 0)
    draw = ImageDraw.Draw(img)
    cx = cy = size / 2.0
    radius = size * 0.46
    n_dots = 260
    for _ in range(n_dots):
        r = radius * math.sqrt(rng.uniform(0.0, 1.0)) * rng.uniform(0.85, 1.0)
        theta = rng.uniform(0, 2 * math.pi)
        x = cx + r * math.cos(theta)
        y = cy + r * math.sin(theta)
        dot_r = rng.uniform(size * 0.006, size * 0.022)
        alpha = int(rng.uniform(90, 235))
        draw.ellipse([x - dot_r, y - dot_r, x + dot_r, y + dot_r], fill=alpha)
    return _finish_tip(img)


def generate_brush_tips():
    os.makedirs(BRUSH_DIR, exist_ok=True)
    generators = {
        "round": gen_round_tip,
        "hake": gen_hake_tip,
        "marker": gen_marker_tip,
        "spray": gen_spray_tip,
    }
    for name, fn in generators.items():
        path = os.path.join(BRUSH_DIR, f"{name}.png")
        fn().save(path)
        log(f"  {path} を生成しました")


# ---------------------------------------------------------------------------
# エントリポイント
# ---------------------------------------------------------------------------

def main():
    os.makedirs(EXAMPLES_DIR, exist_ok=True)
    os.makedirs(FONT_DIR, exist_ok=True)
    os.makedirs(SFX_DIR, exist_ok=True)
    os.makedirs(BRUSH_DIR, exist_ok=True)

    log("=== 効果音を確認（無ければ仮の合成音を生成） ===")
    ensure_sfx(os.path.join(SFX_DIR, "shakin.wav"), synth_shakin, "shakin.wav")
    ensure_sfx(os.path.join(SFX_DIR, "don.wav"), synth_don, "don.wav")
    ensure_sfx(os.path.join(SFX_DIR, "impact.wav"), synth_impact, "impact.wav")

    log("=== ブラシ筆先画像を生成 ===")
    generate_brush_tips()

    log("=== フォントを確認 ===")
    ensure_font()
    ensure_title_font()
    ensure_extra_title_fonts()

    log("=== ダミー動画を生成（縦） ===")
    video_path = os.path.join(EXAMPLES_DIR, "dummy_input.mp4")
    render_dummy_video(video_path)
    mux_audio_into_video(video_path, make_tone_track())
    log(f"  {video_path} を生成しました")

    log("=== ダミー動画を生成（横。iPhone実機の縦動画バグの回帰テスト用） ===")
    video_path_landscape = os.path.join(EXAMPLES_DIR, "dummy_input_landscape.mp4")
    render_dummy_video(video_path_landscape, w=H, h=W)  # 縦動画の幅高を入れ替えただけの横動画
    mux_audio_into_video(video_path_landscape, make_tone_track())
    log(f"  {video_path_landscape} を生成しました")

    log("=== ダミー動画を生成（縦・可変フレームレート。iPhoneスクリーン録画のVFR回帰テスト用） ===")
    video_path_vfr = os.path.join(EXAMPLES_DIR, "dummy_input_vfr.mp4")
    render_dummy_video_vfr(video_path_vfr)
    mux_audio_into_video(video_path_vfr, make_tone_track())
    log(f"  {video_path_vfr} を生成しました")

    log("=== ダミーロゴを生成 ===")
    logo_path = gen_dummy_logo(os.path.join(EXAMPLES_DIR, "store_logo.png"))
    log(f"  {logo_path} を生成しました")

    log("=== ダミーロゴ（不透明・黒背景・余白多め）を生成 ===")
    opaque_logo_path = gen_dummy_logo_opaque_black(
        os.path.join(EXAMPLES_DIR, "store_logo_opaque_black.png"),
        jpeg_path=os.path.join(EXAMPLES_DIR, "store_logo_opaque_black.jpg"))
    log(f"  {opaque_logo_path} / {os.path.splitext(opaque_logo_path)[0]}.jpg を生成しました"
        "（自動クロップ＋JPEGノイズ耐性の検証用）")

    log("=== extract.py 検証用シルエット画像を生成 ===")
    silhouette_path = gen_extract_test_silhouette(os.path.join(EXAMPLES_DIR, "extract_test_silhouette.png"))
    log(f"  {silhouette_path} を生成しました")

    log("=== extract.py モデル比較用：白い服・白い壁・強い照明の低コントラスト画像を生成 ===")
    white_on_white_path = gen_extract_test_white_on_white(
        os.path.join(EXAMPLES_DIR, "extract_test_white_on_white.png"))
    log(f"  {white_on_white_path} を生成しました")

    log("=== サンプルJSONを生成 ===")
    project = make_sample_json("dummy_input.mp4")
    json_path = os.path.join(EXAMPLES_DIR, "sample.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(project, f, ensure_ascii=False, indent=2)
    log(f"  {json_path} を生成しました")

    log("完了しました。次のコマンドで確認できます:")
    log("  python render.py examples/sample.json --out examples/out.mp4")


if __name__ == "__main__":
    main()
