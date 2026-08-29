#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
人物スポットライト動画レンダラー

プロジェクトJSON + 元動画 から、
「指定時刻でフリーズ → 背景をモノクロ/減光 → ブラシでなぞった所だけカラー復活
  → 名前テロップ + 効果音」
という演出を挿入した MP4 を生成する。

使い方:
    python render.py project.json --video input.mp4 --out output.mp4
    python render.py project.json --preview            # 確認用PNGを1枚だけ出力

依存: opencv-python-headless / numpy / pillow / ffmpeg(コマンド)
"""

import argparse
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import wave

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

# ---------------------------------------------------------------------------
# 定数・既定値
# ---------------------------------------------------------------------------

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# style の既定値（プロジェクトJSONの style / 各freeze で上書きされる）
DEFAULT_STYLE = {
    "freeze_sec": 2.5,            # フリーズ全体の長さ（秒）
    "brush_anim_sec": 0.8,        # ブラシが伸びるアニメーションの長さ（秒）
    "brush_width": 0.12,          # ブラシ太さ（動画幅に対する比率）
    "brush_shape": "round",       # round | hake | marker | spray
    "background": "mono",         # mono | dark
    "font": "assets/fonts/NotoSansJP-Bold.ttf",
    "audio_during_freeze": "mute",  # mute | keep
}

BRUSH_SHAPES = ("round", "hake", "marker", "spray")
BRUSH_ASSET_DIR = os.path.join("assets", "brushes")
# 筆先PNG（assets/brushes/<shape>.png、正方形キャンバス）の中で、実際の絵柄が
# 占める一辺の比率の逆数。ブラシ太さ(px)からキャンバスサイズを逆算するのに使う。
# 値は make_dummy.py の gen_*_tip() が生成する図形の実寸と対応しているので、
# 片方を変えたらもう片方も見た目を確認しながら合わせること。
BRUSH_TIP_SCALE = {"round": 1.19, "hake": 1.56, "marker": 1.61, "spray": 1.09}
# 筆先スタンプの間隔 ÷ ブラシ太さ。形状ごとに変える：
# round/marker は隙間なく滑らかにつながるよう密に、hake/spray は重ねすぎると
# 質感（毛筋・粒感）が塗りつぶされて消えてしまうので粗めにスタンプする。
BRUSH_STAMP_SPACING = {"round": 0.32, "hake": 0.62, "marker": 0.42, "spray": 0.68}

HOLD_BEFORE_BRUSH_SEC = 0.3   # フリーズ開始からブラシ描き始めまでの静止時間
TELOP_FADE_SEC = 0.15         # テロップのフェードイン時間
TELOP_Y_RATIO = 0.78          # テロップ中心の縦位置（高さ比）
TELOP_SIZE_RATIO = 0.06       # 文字サイズ（高さ比）
BRUSH_PAINT_ALPHA = 0.85      # 描いている最中の白い絵の具の不透明度
DARK_GAIN = 0.30              # background="dark" のときの明るさ倍率

AUDIO_SR = 48000              # 音声処理のサンプリングレート
AUDIO_CH = 2                  # 音声処理のチャンネル数（ステレオ固定）
KEEP_LOOP_SEC = 0.5           # audio_during_freeze="keep" のときにループする長さ


# ---------------------------------------------------------------------------
# 小さなユーティリティ
# ---------------------------------------------------------------------------

def log(msg):
    """標準出力へのログ（進捗表示と混ざらないよう改行を明示）"""
    print(msg, flush=True)


def warn(msg):
    """警告は標準エラーへ"""
    print("[warn] " + msg, file=sys.stderr, flush=True)


def find_exe(name):
    """ffmpeg / ffprobe の場所を探す。無ければ分かりやすく落とす。"""
    path = shutil.which(name)
    if not path:
        raise RuntimeError(f"{name} が見つかりません。ffmpeg をインストールしてください。")
    return path


def resolve_path(path, bases):
    """
    相対パスを、候補ディレクトリ（カレント→JSONのある場所→スクリプトの場所）の
    順に探して解決する。見つからなければ元の文字列をそのまま返す。
    """
    if not path:
        return path
    if os.path.isabs(path):
        return path
    for base in bases:
        cand = os.path.join(base, path)
        if os.path.exists(cand):
            return os.path.abspath(cand)
    return path


def even(n):
    """yuv420p は偶数サイズが必要なので偶数に丸める"""
    n = int(n)
    return n - (n % 2)


# ---------------------------------------------------------------------------
# プロジェクトJSONの読み込み
# ---------------------------------------------------------------------------

def load_project(json_path):
    """
    プロジェクトJSONを読み、style の既定値を各 freeze にマージした状態で返す。
    未知のキーは無視する（将来の拡張に備えて壊れないようにする）。
    """
    with open(json_path, "r", encoding="utf-8") as f:
        proj = json.load(f)

    style = dict(DEFAULT_STYLE)
    style.update(proj.get("style") or {})

    freezes = []
    for fz in (proj.get("freezes") or []):
        merged = dict(style)          # style を既定値として
        merged.update(fz)             # freeze 側のキーで上書き
        merged["strokes"] = fz.get("strokes") or []
        merged["time"] = float(fz.get("time", 0.0))
        merged["name"] = fz.get("name", "")
        merged["sfx"] = fz.get("sfx")
        freezes.append(merged)

    # 必ず time 順に処理する
    freezes.sort(key=lambda f: f["time"])

    return {
        "raw": proj,
        "video": proj.get("video"),
        "output": proj.get("output") or {},
        "style": style,
        "freezes": freezes,
    }


# ---------------------------------------------------------------------------
# 入力動画の情報取得（ffprobe）
# ---------------------------------------------------------------------------

def probe_video(path):
    """
    ffprobe で解像度・fps・長さ・回転メタデータ・音声有無を調べる。
    回転が ±90 度のときは「表示上の向き」に合わせて幅と高さを入れ替えて返す。
    """
    ffprobe = find_exe("ffprobe")
    cmd = [ffprobe, "-v", "error", "-print_format", "json",
           "-show_format", "-show_streams", path]
    out = subprocess.run(cmd, check=True, capture_output=True).stdout
    info = json.loads(out.decode("utf-8", "replace"))

    vs = None
    has_audio = False
    for st in info.get("streams", []):
        if st.get("codec_type") == "video" and vs is None:
            vs = st
        elif st.get("codec_type") == "audio":
            has_audio = True
    if vs is None:
        raise RuntimeError(f"映像ストリームが見つかりません: {path}")

    width, height = int(vs["width"]), int(vs["height"])

    # 回転メタデータ（コンテナのtagsか、display matrixのside_data）
    rotation = 0
    tags = vs.get("tags") or {}
    if "rotate" in tags:
        try:
            rotation = int(float(tags["rotate"]))
        except ValueError:
            rotation = 0
    for sd in (vs.get("side_data_list") or []):
        if "rotation" in sd:
            rotation = int(round(float(sd["rotation"])))
    rotation = rotation % 360

    # fps（avg_frame_rate優先、無ければr_frame_rate）
    fps = 30.0
    for key in ("avg_frame_rate", "r_frame_rate"):
        val = vs.get(key)
        if val and val != "0/0":
            num, _, den = val.partition("/")
            den = den or "1"
            if float(den) != 0 and float(num) != 0:
                fps = float(num) / float(den)
                break

    duration = 0.0
    for src in (vs.get("duration"), (info.get("format") or {}).get("duration")):
        if src:
            duration = float(src)
            break

    # ffmpeg はデコード時に自動で回転を適用するので、表示サイズを返す
    if rotation in (90, 270):
        width, height = height, width

    return {
        "path": path,
        "width": width,
        "height": height,
        "fps": fps,
        "duration": duration,
        "rotation": rotation,
        "has_audio": has_audio,
    }


# ---------------------------------------------------------------------------
# 映像の入出力（ffmpeg パイプ）
# ---------------------------------------------------------------------------

def build_scale_filter(W, H, fps):
    """
    出力解像度・fpsに合わせるフィルタ。
    アスペクト比は保ったまま縮小し、余白は黒でパディング（レターボックス）する。
    """
    return (f"fps={fps:.6f},"
            f"scale={W}:{H}:force_original_aspect_ratio=decrease,"
            f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color=black,"
            f"setsar=1")


def open_frame_reader(path, W, H, fps):
    """
    ffmpeg を rawvideo(bgr24) で吐かせるプロセスを開く。
    （回転はffmpeg側が自動適用するので、出てくるフレームは表示上の向き）
    """
    ffmpeg = find_exe("ffmpeg")
    cmd = [ffmpeg, "-v", "error", "-i", path,
           "-vf", build_scale_filter(W, H, fps),
           "-f", "rawvideo", "-pix_fmt", "bgr24", "pipe:1"]
    return subprocess.Popen(cmd, stdout=subprocess.PIPE, bufsize=10 ** 8)


def iter_frames(proc, W, H):
    """rawvideo パイプから1フレームずつ読み出すジェネレータ（全部は溜めない）"""
    nbytes = W * H * 3
    while True:
        buf = proc.stdout.read(nbytes)
        if not buf or len(buf) < nbytes:
            break
        yield np.frombuffer(buf, np.uint8).reshape(H, W, 3)
    proc.stdout.close()
    proc.wait()


def grab_frame_at(path, t, W, H, fps):
    """指定時刻のフレームを1枚だけ取り出す（--preview 用）"""
    ffmpeg = find_exe("ffmpeg")
    cmd = [ffmpeg, "-v", "error", "-ss", f"{max(t, 0.0):.6f}", "-i", path,
           "-frames:v", "1",
           "-vf", build_scale_filter(W, H, fps),
           "-f", "rawvideo", "-pix_fmt", "bgr24", "pipe:1"]
    out = subprocess.run(cmd, check=True, capture_output=True).stdout
    need = W * H * 3
    if len(out) < need:
        raise RuntimeError(f"時刻 {t}s のフレームを取得できませんでした。")
    return np.frombuffer(out[:need], np.uint8).reshape(H, W, 3).copy()


def open_video_writer(out_path, W, H, fps, audio_wav):
    """
    rawvideo を stdin で受け取り、H.264(yuv420p, crf20) の MP4 を書く ffmpeg を開く。
    音声wavがあれば2つ目の入力として一緒にmuxする。
    """
    ffmpeg = find_exe("ffmpeg")
    cmd = [ffmpeg, "-y", "-v", "error",
           "-f", "rawvideo", "-pix_fmt", "bgr24",
           "-s", f"{W}x{H}", "-r", f"{fps:.6f}", "-i", "pipe:0"]
    if audio_wav:
        cmd += ["-i", audio_wav]
    cmd += ["-c:v", "libx264", "-preset", "medium", "-crf", "20",
            "-pix_fmt", "yuv420p", "-movflags", "+faststart"]
    if audio_wav:
        cmd += ["-c:a", "aac", "-b:a", "192k", "-shortest"]
    cmd += [out_path]
    return subprocess.Popen(cmd, stdin=subprocess.PIPE)


# ---------------------------------------------------------------------------
# 背景処理（モノクロ / 減光）
# ---------------------------------------------------------------------------

def make_background(frame, mode):
    """フリーズ中の「カラーが抜けた背景」を作る"""
    if mode == "dark":
        return np.clip(frame.astype(np.float32) * DARK_GAIN, 0, 255).astype(np.uint8)
    if mode != "mono":
        warn(f"background='{mode}' は未知の値です。mono として扱います。")
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    return cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)


# ---------------------------------------------------------------------------
# ブラシストローク
# ---------------------------------------------------------------------------

def build_stroke_geometry(strokes, W, H, default_width):
    """
    比率で書かれたストロークをピクセル座標に直し、
    線分長・累積長（＝アニメーションの進み具合の基準）を計算する。
    """
    geo = []
    for st in strokes:
        pts_ratio = st.get("points") or []
        pts = [(float(p[0]) * W, float(p[1]) * H) for p in pts_ratio if len(p) >= 2]
        if not pts:
            continue
        width_ratio = float(st.get("width", default_width))
        thick = max(1, int(round(width_ratio * W)))   # 太さは「幅」基準
        seglens = [math.hypot(b[0] - a[0], b[1] - a[1])
                   for a, b in zip(pts[:-1], pts[1:])]
        length = float(sum(seglens))
        geo.append({
            "pts": pts,
            "seglens": seglens,
            "thick": thick,
            "length": length,
            # 点だけのストロークでも時間を消費するように下駄を履かせる
            "cost": max(length, float(thick)),
        })
    total = sum(g["cost"] for g in geo)
    return geo, total


def resolve_brush_shape(shape):
    """未知の値は round にフォールバックする（background の扱いと同じ方針）"""
    shape = shape or "round"
    if shape not in BRUSH_SHAPES:
        warn(f"brush_shape='{shape}' は未知の値です。round として扱います。")
        return "round"
    return shape


_TIP_CACHE = {}


def get_brush_tip_alpha(shape, size_px):
    """
    assets/brushes/<shape>.png を読み込み、size_px四方にリサイズした
    アルファ値（float32, 0.0〜1.0）の配列を返す。(shape, size_px) 単位でキャッシュする。
    """
    size_px = max(2, int(round(size_px)))
    key = (shape, size_px)
    cached = _TIP_CACHE.get(key)
    if cached is not None:
        return cached

    path = resolve_path(os.path.join(BRUSH_ASSET_DIR, f"{shape}.png"), [SCRIPT_DIR])
    if not os.path.exists(path):
        if shape != "round":
            warn(f"ブラシ画像が見つかりません（{path}）。round にフォールバックします。")
            return get_brush_tip_alpha("round", size_px)
        raise RuntimeError(
            f"ブラシ画像が見つかりません: {path}\n"
            "python make_dummy.py を実行して assets/brushes/*.png を生成してください。")

    img = Image.open(path).convert("RGBA").resize((size_px, size_px), Image.LANCZOS)
    alpha = np.asarray(img, dtype=np.float32)[:, :, 3] / 255.0
    _TIP_CACHE[key] = alpha
    return alpha


def point_and_angle_at_length(pts, seglens, length):
    """ポリライン pts 上の累積長 length の位置の座標と、その位置での進行方向（ラジアン）を返す"""
    acc = 0.0
    for i, seg in enumerate(seglens):
        nxt = acc + seg
        if seg > 0 and (length <= nxt or i == len(seglens) - 1):
            t = float(np.clip((length - acc) / seg, 0.0, 1.0))
            p0, p1 = pts[i], pts[i + 1]
            x = p0[0] + (p1[0] - p0[0]) * t
            y = p0[1] + (p1[1] - p0[1]) * t
            angle = math.atan2(p1[1] - p0[1], p1[0] - p0[0])
            return (x, y), angle
        acc = nxt
    return pts[-1], 0.0


def stamp_tip(accum, tip_alpha, cx, cy, angle_rad, size_px):
    """
    accum（H×W, float32, 0〜1の被覆率）に、tip_alphaを angle_rad だけ回転させて
    (cx, cy) を中心に「over」合成でスタンプする（重なった部分は自然と濃くなる）。
    """
    H, W = accum.shape
    half = size_px / 2.0
    # cv2.getRotationMatrix2Dは数学的な反時計回りを正とするため、
    # 画像のY軸が下向き（画面座標）でも進行方向どおりに向くよう符号を反転する。
    M = cv2.getRotationMatrix2D((half, half), -math.degrees(angle_rad), 1.0)
    rotated = cv2.warpAffine(tip_alpha, M, (size_px, size_px),
                              flags=cv2.INTER_LINEAR,
                              borderMode=cv2.BORDER_CONSTANT, borderValue=0.0)

    x0, y0 = int(round(cx - half)), int(round(cy - half))
    x1, y1 = x0 + size_px, y0 + size_px
    sx0, sy0 = max(0, -x0), max(0, -y0)
    sx1, sy1 = size_px - max(0, x1 - W), size_px - max(0, y1 - H)
    dx0, dy0 = max(0, x0), max(0, y0)
    dx1, dy1 = min(W, x1), min(H, y1)
    if dx1 <= dx0 or dy1 <= dy0 or sx1 <= sx0 or sy1 <= sy0:
        return

    patch = rotated[sy0:sy1, sx0:sx1]
    region = accum[dy0:dy1, dx0:dx1]
    region += (1.0 - region) * patch


def draw_stroke_mask(geo, W, H, start_len, end_len, shape):
    """
    累積長 start_len〜end_len の範囲を、shapeの筆先画像を軌跡に沿って
    一定間隔でスタンプすることで描いたマスク（0〜255, uint8）を作る。
    筆先は進行方向に合わせて回転させる。
    """
    accum = np.zeros((H, W), np.float32)
    if end_len <= start_len:
        return np.zeros((H, W), np.uint8)

    offset = 0.0
    for g in geo:
        s = max(start_len - offset, 0.0)
        e = min(end_len - offset, g["cost"])
        offset += g["cost"]
        if e <= 0 or e <= s:
            continue

        thick = g["thick"]
        size_px = max(4, int(round(thick * BRUSH_TIP_SCALE.get(shape, 1.2))))
        tip_alpha = get_brush_tip_alpha(shape, size_px)
        pts = g["pts"]

        if g["length"] <= 0.0:
            # 1点だけ（またはゼロ長）のストロークは、その場に1つだけスタンプする
            stamp_tip(accum, tip_alpha, pts[0][0], pts[0][1], 0.0, size_px)
            continue

        spacing = max(1.0, thick * BRUSH_STAMP_SPACING.get(shape, 0.4))
        pos = s
        while pos < e:
            (x, y), ang = point_and_angle_at_length(pts, g["seglens"], pos)
            stamp_tip(accum, tip_alpha, x, y, ang, size_px)
            pos += spacing
        # 区間の終端も必ずスタンプする（spacing間隔の端数で端が欠けないように）
        (x, y), ang = point_and_angle_at_length(pts, g["seglens"], e)
        stamp_tip(accum, tip_alpha, x, y, ang, size_px)

    mask = np.clip(accum * 255.0, 0, 255).astype(np.uint8)
    # ごく軽くぼかす：筆先画像そのものの縁は既に滑らかだが、点数の少ないストローク
    # （角ばったポリラインで曲線を近似している場合など）の継ぎ目を目立たなくする。
    # カーネルを小さく保ち、ハケの毛筋やスプレーの粒状感を潰さない程度にする。
    k = max(3, int(round(W / 500.0)))
    if k % 2 == 0:
        k += 1
    return cv2.GaussianBlur(mask, (k, k), 0)


def composite_brush(bg, color, geo, total_len, progress, W, shape):
    """
    進捗 progress(0〜1) の時点の合成フレームを作る。
      - 描画済みの領域だけ元のカラーが見える（筆先スタンプによるマスクで復元）
      - 描いたばかりの先端付近には白い絵の具（alpha 0.85）が乗り、
        少し後ろでフェードして消える（＝最終的にはカラーだけが残る）
    """
    if total_len <= 0:
        return bg.copy()

    head = total_len * float(np.clip(progress, 0.0, 1.0))
    mask = draw_stroke_mask(geo, W, bg.shape[0], 0.0, head, shape)
    m = (mask.astype(np.float32) / 255.0)[:, :, None]

    out = bg.astype(np.float32) * (1.0 - m) + color.astype(np.float32) * m

    if progress < 1.0:
        # 先端付近の「まだ乾いていない絵の具」の長さ
        max_thick = max(g["thick"] for g in geo)
        tail = max(2.0 * max_thick, total_len * 0.12)
        paint = draw_stroke_mask(geo, W, bg.shape[0], max(head - tail, 0.0), head, shape)
        pm = (paint.astype(np.float32) / 255.0)[:, :, None] * BRUSH_PAINT_ALPHA
        out = out * (1.0 - pm) + 255.0 * pm

    return np.clip(out, 0, 255).astype(np.uint8)


# ---------------------------------------------------------------------------
# 名前テロップ
# ---------------------------------------------------------------------------

def load_font(font_path, size):
    """TTF/OTFを読み込む。可変フォントならBoldウェイトを選ぶ。失敗したらNone。"""
    if not font_path or not os.path.exists(font_path):
        return None
    try:
        font = ImageFont.truetype(font_path, size)
    except Exception as exc:      # noqa: BLE001 - 環境依存の失敗を握って警告に落とす
        warn(f"フォントを読み込めませんでした（{font_path}）: {exc}")
        return None
    try:
        font.set_variation_by_name("Bold")   # 可変フォント対応環境のみ
    except Exception:                        # noqa: BLE001
        pass
    return font


def render_telop_layer(text, W, H, font):
    """
    テロップを1回だけ描いて (BGR画像, アルファ0〜1) として返す。
    フェードインは毎フレームこのアルファに係数を掛けるだけで済ませる。
    """
    if not text:
        return None, None

    size = max(8, int(round(H * TELOP_SIZE_RATIO)))
    cx, cy = W // 2, int(round(H * TELOP_Y_RATIO))
    outline = max(2, size // 12)

    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))

    if font is not None:
        draw = ImageDraw.Draw(layer)
        draw.text((cx, cy), text, font=font, anchor="mm",
                  fill=(255, 255, 255, 255),
                  stroke_width=outline, stroke_fill=(0, 0, 0, 255))
        rgba = np.array(layer)
    else:
        # フォントが無い環境向けフォールバック（日本語は表示できない）
        warn("日本語フォントが無いため OpenCV の既定フォントで描画します。")
        tmp = np.zeros((H, W, 4), np.uint8)
        scale = size / 30.0
        (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, scale, outline)
        org = (cx - tw // 2, cy + th // 2)
        cv2.putText(tmp, text, org, cv2.FONT_HERSHEY_SIMPLEX, scale,
                    (0, 0, 0, 255), outline * 2, cv2.LINE_AA)
        cv2.putText(tmp, text, org, cv2.FONT_HERSHEY_SIMPLEX, scale,
                    (255, 255, 255, 255), outline, cv2.LINE_AA)
        rgba = tmp

    rgb = rgba[:, :, :3].astype(np.float32)
    bgr = rgb[:, :, ::-1].copy() if font is not None else rgb  # PILはRGB、cv2はBGR
    alpha = (rgba[:, :, 3].astype(np.float32) / 255.0)[:, :, None]
    return bgr, alpha


def blend_telop(frame, telop_bgr, telop_alpha, fade):
    """テロップを fade(0〜1) の濃さで重ねる"""
    if telop_bgr is None or fade <= 0:
        return frame
    a = telop_alpha * float(np.clip(fade, 0.0, 1.0))
    out = frame.astype(np.float32) * (1.0 - a) + telop_bgr * a
    return np.clip(out, 0, 255).astype(np.uint8)


# ---------------------------------------------------------------------------
# フリーズ区間の設計（映像と音声で同じ数値を使うため一箇所で計算する）
# ---------------------------------------------------------------------------

def plan_freezes(freezes, fps, src_frames, json_dir):
    """
    各フリーズについて、挿入位置（フレーム番号）と
    各フェーズのフレーム数を決める。
    """
    plans = []
    for fz in freezes:
        idx = int(round(float(fz["time"]) * fps))
        if src_frames > 0:
            idx = min(idx, max(src_frames - 1, 0))
        idx = max(idx, 0)

        n_total = max(1, int(round(float(fz["freeze_sec"]) * fps)))
        n_hold = int(round(HOLD_BEFORE_BRUSH_SEC * fps))
        n_brush = max(1, int(round(float(fz["brush_anim_sec"]) * fps)))

        # freeze_sec が短すぎる場合は、前半2フェーズを比例縮小して収める
        if n_hold + n_brush > n_total:
            scale = n_total / float(n_hold + n_brush)
            n_hold = int(n_hold * scale)
            n_brush = max(1, n_total - n_hold)
        n_rest = max(0, n_total - n_hold - n_brush)

        sfx_path = None
        if fz.get("sfx"):
            cand = os.path.join("assets", "sfx", f"{fz['sfx']}.wav")
            sfx_path = resolve_path(cand, [os.getcwd(), json_dir, SCRIPT_DIR])
            if not os.path.exists(sfx_path):
                warn(f"効果音が見つかりません: {cand}")
                sfx_path = None

        plans.append({
            "fz": fz,
            "frame_index": idx,
            "n_hold": n_hold,
            "n_brush": n_brush,
            "n_rest": n_rest,
            "n_total": n_hold + n_brush + n_rest,
            "sfx_path": sfx_path,
        })
    plans.sort(key=lambda p: p["frame_index"])
    return plans


def iter_freeze_frames(frame, plan, W, H, fps, font_cache, json_dir):
    """
    1回分のフリーズ区間のフレームを順に生成する（メモリに溜めない）。
      1. 静止（背景処理のみ）
      2. ブラシが伸びる
      3. ブラシ完了 → テロップがフェードイン、そのまま保持
    """
    fz = plan["fz"]
    bg = make_background(frame, fz.get("background", "mono"))
    shape = resolve_brush_shape(fz.get("brush_shape"))
    geo, total_len = build_stroke_geometry(
        fz.get("strokes") or [], W, H, float(fz.get("brush_width", 0.12)))

    font_path = resolve_path(fz.get("font"), [os.getcwd(), json_dir, SCRIPT_DIR])
    if font_path not in font_cache:
        font_cache[font_path] = load_font(font_path, max(8, int(round(H * TELOP_SIZE_RATIO))))
    telop_bgr, telop_alpha = render_telop_layer(fz.get("name", ""), W, H, font_cache[font_path])

    # 1) ブラシ開始まで静止
    for _ in range(plan["n_hold"]):
        yield bg.copy()

    # 2) ブラシ描画
    for i in range(plan["n_brush"]):
        progress = (i + 1) / float(plan["n_brush"])
        yield composite_brush(bg, frame, geo, total_len, progress, W, shape)

    # 3) 完成状態＋テロップのフェードイン→保持
    done = composite_brush(bg, frame, geo, total_len, 1.0, W, shape)
    fade_frames = max(1, int(round(TELOP_FADE_SEC * fps)))
    for i in range(plan["n_rest"]):
        fade = min(1.0, (i + 1) / float(fade_frames))
        yield blend_telop(done, telop_bgr, telop_alpha, fade)


def render_preview(frame, plan, W, H, fps, font_cache, json_dir, out_png):
    """--preview 用：ブラシ完了＋テロップ全表示のフレームを1枚PNGで出す"""
    fz = plan["fz"]
    bg = make_background(frame, fz.get("background", "mono"))
    shape = resolve_brush_shape(fz.get("brush_shape"))
    geo, total_len = build_stroke_geometry(
        fz.get("strokes") or [], W, H, float(fz.get("brush_width", 0.12)))
    img = composite_brush(bg, frame, geo, total_len, 1.0, W, shape)

    font_path = resolve_path(fz.get("font"), [os.getcwd(), json_dir, SCRIPT_DIR])
    font = load_font(font_path, max(8, int(round(H * TELOP_SIZE_RATIO))))
    telop_bgr, telop_alpha = render_telop_layer(fz.get("name", ""), W, H, font)
    img = blend_telop(img, telop_bgr, telop_alpha, 1.0)

    os.makedirs(os.path.dirname(os.path.abspath(out_png)) or ".", exist_ok=True)
    cv2.imwrite(out_png, img)
    return out_png


# ---------------------------------------------------------------------------
# 音声処理（numpyで切り貼り・ミックス）
# ---------------------------------------------------------------------------

def decode_audio(path, sr=AUDIO_SR, ch=AUDIO_CH):
    """
    ffmpeg で任意の音声を float32 の (N, ch) 配列にデコードする。
    音声が無い / 読めない場合は長さ0の配列を返す。
    """
    ffmpeg = find_exe("ffmpeg")
    cmd = [ffmpeg, "-v", "error", "-i", path, "-vn",
           "-f", "f32le", "-acodec", "pcm_f32le",
           "-ac", str(ch), "-ar", str(sr), "pipe:1"]
    res = subprocess.run(cmd, capture_output=True)
    if res.returncode != 0 or not res.stdout:
        return np.zeros((0, ch), np.float32)
    data = np.frombuffer(res.stdout, np.float32)
    usable = (len(data) // ch) * ch
    return data[:usable].reshape(-1, ch).copy()


def mix_into(buf, snippet, start):
    """buf の start サンプル目に snippet を加算ミックスする（はみ出しは切る）"""
    if snippet.size == 0 or start >= len(buf):
        return
    start = max(0, int(start))
    n = min(len(snippet), len(buf) - start)
    if n > 0:
        buf[start:start + n] += snippet[:n]


def build_audio(src_path, plans, fps, src_frames, has_audio, sr=AUDIO_SR, ch=AUDIO_CH):
    """
    フリーズ区間の分だけ元音声を後ろへずらし、
    フリーズ中は無音 or 直前0.5秒のループを差し込み、効果音を重ねる。
    """
    orig = decode_audio(src_path, sr, ch) if has_audio else np.zeros((0, ch), np.float32)

    # 音声が無い動画でも音声トラックは作る（映像長ぶんの無音）
    need = int(round(src_frames / float(fps) * sr)) if src_frames > 0 else 0
    if len(orig) < need:
        orig = np.concatenate([orig, np.zeros((need - len(orig), ch), np.float32)])

    pieces = []
    sfx_jobs = []      # (新タイムライン上の開始サンプル, wavパス)
    cursor = 0         # 元音声の読み出し位置
    written = 0        # 出力済みサンプル数（＝新タイムラインの位置）

    for plan in plans:
        # 映像と同じ時刻基準（フレーム番号）で切り貼りしてズレを防ぐ
        cut = int(round(plan["frame_index"] / float(fps) * sr))
        cut = min(max(cut, cursor), len(orig))
        pieces.append(orig[cursor:cut])
        written += cut - cursor
        cursor = cut

        n_samples = int(round(plan["n_total"] / float(fps) * sr))
        mode = plan["fz"].get("audio_during_freeze", "mute")
        if mode == "keep":
            loop_src = orig[max(0, cut - int(KEEP_LOOP_SEC * sr)):cut]
            if len(loop_src) > 0:
                reps = int(math.ceil(n_samples / float(len(loop_src))))
                seg = np.tile(loop_src, (reps, 1))[:n_samples].astype(np.float32)
            else:
                seg = np.zeros((n_samples, ch), np.float32)
        else:
            if mode != "mute":
                warn(f"audio_during_freeze='{mode}' は未知の値です。mute として扱います。")
            seg = np.zeros((n_samples, ch), np.float32)
        pieces.append(seg)

        # 効果音はブラシ完了の瞬間
        if plan["sfx_path"]:
            offset = int(round((plan["n_hold"] + plan["n_brush"]) / float(fps) * sr))
            sfx_jobs.append((written + offset, plan["sfx_path"]))
        written += n_samples

    pieces.append(orig[cursor:])
    out = np.concatenate(pieces) if pieces else np.zeros((0, ch), np.float32)

    for start, path in sfx_jobs:
        mix_into(out, decode_audio(path, sr, ch), start)

    return np.clip(out, -1.0, 1.0)


def write_wav(path, samples, sr=AUDIO_SR):
    """float32 (N, ch) の音声を 16bit PCM の wav に書き出す"""
    pcm = np.clip(samples, -1.0, 1.0)
    pcm = (pcm * 32767.0).astype(np.int16)
    with wave.open(path, "wb") as wf:
        wf.setnchannels(pcm.shape[1] if pcm.ndim > 1 else 1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(pcm.tobytes())
    return path


# ---------------------------------------------------------------------------
# メイン処理
# ---------------------------------------------------------------------------

def render(project, json_path, video_path, out_path, preview_path=None):
    """プロジェクト定義に従って1本のMP4（または確認用PNG）を作る"""
    json_dir = os.path.dirname(os.path.abspath(json_path))
    info = probe_video(video_path)

    out_cfg = project["output"] or {}
    W = even(out_cfg.get("width") or info["width"])
    H = even(out_cfg.get("height") or info["height"])
    fps = float(out_cfg.get("fps") or info["fps"])
    if W <= 0 or H <= 0 or fps <= 0:
        raise RuntimeError("出力サイズ/fpsが不正です。")

    log(f"入力: {video_path}  {info['width']}x{info['height']} "
        f"{info['fps']:.3f}fps rotation={info['rotation']} "
        f"audio={'あり' if info['has_audio'] else 'なし'}")
    log(f"出力: {W}x{H} {fps:.3f}fps  フリーズ {len(project['freezes'])} 箇所")

    src_frames = int(round(info["duration"] * fps)) if info["duration"] else 0
    plans = plan_freezes(project["freezes"], fps, src_frames, json_dir)

    # --- 確認用PNGだけ出して終了 ---
    if preview_path:
        if not plans:
            raise RuntimeError("freezes が空なので preview を作れません。")
        plan = plans[0]
        frame = grab_frame_at(video_path, plan["frame_index"] / fps, W, H, fps)
        path = render_preview(frame, plan, W, H, fps, {}, json_dir, preview_path)
        log(f"プレビューを書き出しました: {path}")
        return path

    # --- 音声を先に作る（ffmpegのmux入力として渡すため） ---
    tmpdir = tempfile.mkdtemp(prefix="spotlight_")
    try:
        audio = build_audio(video_path, plans, fps, src_frames, info["has_audio"])
        wav_path = write_wav(os.path.join(tmpdir, "audio.wav"), audio)
        log(f"音声トラック: {len(audio) / AUDIO_SR:.2f} 秒")

        total_out = src_frames + sum(p["n_total"] for p in plans)
        reader = open_frame_reader(video_path, W, H, fps)
        writer = open_video_writer(out_path, W, H, fps, wav_path)

        font_cache = {}
        pending = list(plans)      # まだ挿入していないフリーズ
        written = 0

        try:
            for i, frame in enumerate(iter_frames(reader, W, H)):
                # このフレーム位置に来たフリーズを（複数あってもすべて）挿入する
                while pending and pending[0]["frame_index"] == i:
                    plan = pending.pop(0)
                    for f in iter_freeze_frames(frame, plan, W, H, fps,
                                                font_cache, json_dir):
                        writer.stdin.write(f.tobytes())
                        written += 1
                        if written % 15 == 0:
                            print(f"\r  {written}/{total_out} フレーム",
                                  end="", flush=True)
                writer.stdin.write(np.ascontiguousarray(frame).tobytes())
                written += 1
                if written % 15 == 0:
                    print(f"\r  {written}/{total_out} フレーム", end="", flush=True)

            # 動画末尾より後ろを指すフリーズが残っていたら最後のフレームで処理する
            for plan in pending:
                for f in iter_freeze_frames(frame, plan, W, H, fps,
                                            font_cache, json_dir):
                    writer.stdin.write(f.tobytes())
                    written += 1
        finally:
            writer.stdin.close()
            writer.wait()
        print(f"\r  {written}/{total_out} フレーム", flush=True)

        if writer.returncode != 0:
            raise RuntimeError("ffmpeg のエンコードに失敗しました。")
        log(f"完成: {out_path}  ({written} フレーム / {written / fps:.2f} 秒)")
        return out_path
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="プロジェクトJSONと元動画から、スポットライト演出付きMP4を書き出す")
    parser.add_argument("project", help="プロジェクトJSONのパス")
    parser.add_argument("--video", help="入力動画（省略時はJSONの video を使う）")
    parser.add_argument("--out", default="output.mp4", help="出力MP4のパス")
    parser.add_argument("--preview", nargs="?", const="", default=None,
                        metavar="PNG",
                        help="最初のフリーズのブラシ完了フレームをPNGで1枚出力して終了")
    args = parser.parse_args(argv)

    project = load_project(args.project)
    json_dir = os.path.dirname(os.path.abspath(args.project))

    video = args.video or project["video"]
    if not video:
        parser.error("入力動画が指定されていません（--video か JSON の video）")
    video = resolve_path(video, [os.getcwd(), json_dir, SCRIPT_DIR])
    if not os.path.exists(video):
        parser.error(f"入力動画が見つかりません: {video}")

    preview_path = None
    if args.preview is not None:
        preview_path = args.preview or (os.path.splitext(args.out)[0] + "_preview.png")

    if not preview_path:
        os.makedirs(os.path.dirname(os.path.abspath(args.out)) or ".", exist_ok=True)

    render(project, args.project, video, args.out, preview_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
