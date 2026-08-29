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
from PIL import Image, ImageDraw, ImageFilter

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

    log("=== 効果音を生成 ===")
    write_wav(os.path.join(SFX_DIR, "shakin.wav"), synth_shakin())
    write_wav(os.path.join(SFX_DIR, "don.wav"), synth_don())
    log("  assets/sfx/shakin.wav, assets/sfx/don.wav を生成しました")

    log("=== ブラシ筆先画像を生成 ===")
    generate_brush_tips()

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
