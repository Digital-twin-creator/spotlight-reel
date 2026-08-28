#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
テスト用のダミー動画・ダミーJSON・効果音・フォントを生成するスクリプト。

生成物:
    examples/dummy_input.mp4   1080x1920 30fps 8秒のテスト動画
    examples/sample.json       render.py にそのまま渡せるプロジェクトJSON
    assets/sfx/shakin.wav      効果音（短い上昇音）
    assets/sfx/don.wav         効果音（短い低音）
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

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
EXAMPLES_DIR = os.path.join(SCRIPT_DIR, "examples")
ASSETS_DIR = os.path.join(SCRIPT_DIR, "assets")
FONT_DIR = os.path.join(ASSETS_DIR, "fonts")
SFX_DIR = os.path.join(ASSETS_DIR, "sfx")

W, H, FPS, DURATION_SEC = 1080, 1920, 30, 8
SR = 48000

FONT_URLS = [
    # 可変フォント（Boldウェイトを指定して使う。render.py側で対応）
    "https://raw.githubusercontent.com/google/fonts/main/ofl/notosansjp/NotoSansJP%5Bwght%5D.ttf",
    # 予備: Google Fonts CDN（静的Regularだが、無いよりはまし）
    "https://fonts.gstatic.com/s/notosansjp/v53/-F6jfjtqLzI2JPCgQBnw7HFyzSD-AsregP8VFBEj75s.ttf",
]
FONT_PATH = os.path.join(FONT_DIR, "NotoSansJP-Bold.ttf")


def log(msg):
    print(msg, flush=True)


# ---------------------------------------------------------------------------
# ダミー動画
# ---------------------------------------------------------------------------

def circle_positions(t):
    """「人物」役の円2つの中心座標(t秒時点)を返す。左右にゆっくり往復する。"""
    cx1 = W * 0.30 + math.sin(t * 0.6) * W * 0.10
    cy1 = H * 0.35 + math.cos(t * 0.4) * H * 0.04
    cx2 = W * 0.70 + math.sin(t * 0.5 + 1.5) * W * 0.10
    cy2 = H * 0.55 + math.cos(t * 0.35 + 1.0) * H * 0.04
    return (cx1, cy1), (cx2, cy2)


def make_gradient_bg(t):
    """時間とともにゆっくり変化する背景グラデーションを作る"""
    yy = np.linspace(0, 1, H, dtype=np.float32).reshape(H, 1)
    xx = np.linspace(0, 1, W, dtype=np.float32).reshape(1, W)
    shift = (math.sin(t * 0.3) + 1) / 2
    r = (80 + 100 * yy * (0.5 + 0.5 * shift)).astype(np.float32)
    g = (60 + 80 * xx).astype(np.float32)
    b = (120 + 100 * (1 - yy) * (0.5 + 0.5 * (1 - shift))).astype(np.float32)
    img = np.zeros((H, W, 3), np.float32)
    img[:, :, 0] = b   # B
    img[:, :, 1] = g   # G
    img[:, :, 2] = r   # R
    return np.clip(img, 0, 255).astype(np.uint8)


def render_dummy_video(out_path):
    """OpenCVで毎フレーム描画し、ffmpegにパイプしてH.264 MP4を作る"""
    ffmpeg = "ffmpeg"
    n_frames = DURATION_SEC * FPS
    cmd = [ffmpeg, "-y", "-v", "error",
           "-f", "rawvideo", "-pix_fmt", "bgr24",
           "-s", f"{W}x{H}", "-r", str(FPS), "-i", "pipe:0",
           "-c:v", "libx264", "-preset", "medium", "-crf", "18",
           "-pix_fmt", "yuv420p", out_path]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)

    for i in range(n_frames):
        t = i / FPS
        frame = make_gradient_bg(t)
        (x1, y1), (x2, y2) = circle_positions(t)
        cv2.circle(frame, (int(x1), int(y1)), int(W * 0.12), (60, 90, 220), -1, cv2.LINE_AA)
        cv2.circle(frame, (int(x2), int(y2)), int(W * 0.10), (200, 140, 60), -1, cv2.LINE_AA)
        proc.stdin.write(frame.tobytes())
        if i % 30 == 0:
            print(f"\r  動画生成 {i}/{n_frames}", end="", flush=True)
    print(f"\r  動画生成 {n_frames}/{n_frames}", flush=True)
    proc.stdin.close()
    proc.wait()
    if proc.returncode != 0:
        raise RuntimeError("ダミー動画の生成に失敗しました（ffmpeg）")
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

def synth_shakin(sr=SR, dur=0.5):
    """「シャキーン」風：素早く上昇するサイン波＋簡易キラキラ"""
    t = np.linspace(0, dur, int(sr * dur), endpoint=False, dtype=np.float32)
    freq = 600 + 3000 * (t / dur)
    env = np.exp(-3.0 * t) * (1 - np.exp(-40 * t))
    tone = np.sin(2 * np.pi * np.cumsum(freq) / sr) * env
    sparkle = 0.0
    for h in (2, 3, 4):
        sparkle = sparkle + 0.15 * np.sin(2 * np.pi * freq * h * t) * env
    sig = 0.6 * tone + 0.2 * sparkle
    return np.stack([sig, sig], axis=1).astype(np.float32)


def synth_don(sr=SR, dur=0.4):
    """「ドン」風：低音のサイン波バースト＋軽いノイズ"""
    t = np.linspace(0, dur, int(sr * dur), endpoint=False, dtype=np.float32)
    freq = 120 * np.exp(-4.0 * t) + 55
    env = np.exp(-9.0 * t)
    tone = np.sin(2 * np.pi * np.cumsum(freq) / sr) * env
    noise = (np.random.default_rng(0).standard_normal(len(t)).astype(np.float32)
             * env * 0.08)
    sig = 0.8 * tone + noise
    return np.stack([sig, sig], axis=1).astype(np.float32)


def write_wav(path, samples, sr=SR):
    pcm = np.clip(samples, -1.0, 1.0)
    pcm = (pcm * 32767.0).astype(np.int16)
    with wave.open(path, "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(pcm.tobytes())


# ---------------------------------------------------------------------------
# フォント取得
# ---------------------------------------------------------------------------

def ensure_font():
    """NotoSansJP-Bold.ttf が無ければ Google Fonts からダウンロードする"""
    if os.path.exists(FONT_PATH):
        log(f"フォントは既に存在します: {FONT_PATH}")
        return
    for url in FONT_URLS:
        log(f"フォントをダウンロード中: {url}")
        try:
            req = urllib.request.Request(
                url, headers={"User-Agent": "Mozilla/5.0 (make_dummy.py)"})
            with urllib.request.urlopen(req, timeout=20) as resp:
                data = resp.read()
            with open(FONT_PATH, "wb") as f:
                f.write(data)
            log(f"フォントを保存しました: {FONT_PATH}")
            return
        except Exception as exc:  # noqa: BLE001 - ネットワーク不通環境向けフォールバック
            print(f"[warn] このURLからは取得できませんでした（{exc}）", file=sys.stderr)
    print("[warn] フォントを取得できませんでした。"
          "render.py は OpenCV の既定フォントにフォールバックします。",
          file=sys.stderr)


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
            "name": "赤い人",
            "sfx": "shakin",
            "strokes": [{"width": 0.10, "points": stroke_over_circle(2.5, 0)}],
        },
        {
            "time": 5.5,
            "name": "青い人",
            "sfx": "don",
            "strokes": [{"width": 0.09, "points": stroke_over_circle(5.5, 1)}],
        },
    ]

    project = {
        "version": 1,
        "video": video_filename,
        "output": {"width": W, "height": H, "fps": FPS},
        "style": {
            "freeze_sec": 2.5,
            "brush_anim_sec": 0.8,
            "brush_width": 0.12,
            "background": "mono",
            "font": "assets/fonts/NotoSansJP-Bold.ttf",
            "audio_during_freeze": "mute",
        },
        "freezes": freezes,
    }
    return project


# ---------------------------------------------------------------------------
# エントリポイント
# ---------------------------------------------------------------------------

def main():
    os.makedirs(EXAMPLES_DIR, exist_ok=True)
    os.makedirs(FONT_DIR, exist_ok=True)
    os.makedirs(SFX_DIR, exist_ok=True)

    log("=== 効果音を生成 ===")
    write_wav(os.path.join(SFX_DIR, "shakin.wav"), synth_shakin())
    write_wav(os.path.join(SFX_DIR, "don.wav"), synth_don())
    log("  assets/sfx/shakin.wav, assets/sfx/don.wav を生成しました")

    log("=== フォントを確認 ===")
    ensure_font()

    log("=== ダミー動画を生成 ===")
    video_path = os.path.join(EXAMPLES_DIR, "dummy_input.mp4")
    render_dummy_video(video_path)
    mux_audio_into_video(video_path, make_tone_track())
    log(f"  {video_path} を生成しました")

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
