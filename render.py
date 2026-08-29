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
    "freeze_sec": 1.2,            # フリーズ全体の長さ（秒）
    "brush_anim_sec": 0.8,        # ブラシが伸びるアニメーションの長さ（秒）
    "brush_width": 0.12,          # ブラシ太さ（動画幅に対する比率）
    "brush_shape": "round",       # round | hake | marker | spray
    "mono_contrast": 1.0,         # mono背景のコントラスト倍率（1.0=従来どおり無加工）
    "film_offset": [0.0, 0.0],    # フィルム縁取りのズラし量（出力幅に対する比率、[x, y]）
    "film_color": "#FF6432",      # フィルム縁取りの色（film_offsetが[0,0]なら見えない）
    "film_alpha": 0.8,            # フィルム縁取りの不透明度
    "background": "mono",         # mono | dark
    "font": "assets/fonts/NotoSansJP-Bold.ttf",
    # title_font / title_font_jp は省略時 font にフォールバックする（use_style_font()参照）。
    # ここではキー自体を持たせず、未指定であることを判別できるようにする。
    "title_bounce": False,        # テロップ出現時に130%→100%のバウンスを付けるか
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
TELOP_FADE_SEC = 0.15         # テロップのフェードイン（＝バウンスも同じ時間で行う）
TELOP_BOUNCE_FROM = 1.3       # title_bounce=true のときの初期スケール（130%→100%）
TELOP_Y_RATIO = 0.78          # テロップ中心の縦位置（高さ比）
TELOP_SIZE_RATIO = 0.06       # 文字サイズ（高さ比）
BRUSH_PAINT_ALPHA = 0.85      # 描いている最中の白い絵の具の不透明度
DARK_GAIN = 0.30              # background="dark" のときの明るさ倍率

LOGO_BOUNCE_SEC = 0.3         # ロゴが200%→100%に縮むアニメーションの長さ
LOGO_BOUNCE_FROM = 2.0        # ロゴの初期スケール（200%→100%）
LOGO_WIDTH_RATIO = 0.4        # ロゴの基準表示幅（出力幅に対する比率、等倍=100%のとき）
DEFAULT_LOGO_AT = "end"       # logo.at の既定値・不明値のフォールバック先

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
        "logo": proj.get("logo"),   # 無指定ならNone（ロゴ演出なし）
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

def make_background(frame, mode, contrast=1.0):
    """フリーズ中の「カラーが抜けた背景」を作る（mono時はcontrastでコントラストを強調できる）"""
    if mode == "dark":
        return np.clip(frame.astype(np.float32) * DARK_GAIN, 0, 255).astype(np.uint8)
    if mode != "mono":
        warn(f"background='{mode}' は未知の値です。mono として扱います。")
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY).astype(np.float32)
    contrast = float(contrast) if contrast else 1.0
    if contrast != 1.0:
        gray = np.clip((gray - 128.0) * contrast + 128.0, 0, 255)
    gray = gray.astype(np.uint8)
    return cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)


def hex_to_bgr(hex_color, default=(50, 100, 255)):
    """"#RRGGBB" をOpenCVのBGRタプルに変換する。不正な値はdefaultにフォールバックする"""
    s = (hex_color or "").lstrip("#")
    if len(s) != 6:
        if hex_color:
            warn(f"film_color='{hex_color}' は不正な値です。既定色を使います。")
        return default
    try:
        r, g, b = int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)
        return (b, g, r)
    except ValueError:
        warn(f"film_color='{hex_color}' は不正な値です。既定色を使います。")
        return default


def translate_mask(mask, dx, dy):
    """マスク（H×W, uint8）を(dx, dy)だけ平行移動する（はみ出た部分は切り捨てる）"""
    if abs(dx) < 0.5 and abs(dy) < 0.5:
        return mask
    H, W = mask.shape
    M = np.float32([[1, 0, dx], [0, 1, dy]])
    return cv2.warpAffine(mask, M, (W, H), flags=cv2.INTER_LINEAR,
                           borderMode=cv2.BORDER_CONSTANT, borderValue=0)


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


def composite_brush(bg, color, geo, total_len, progress, W, shape,
                     film_offset=(0.0, 0.0), film_color_bgr=None, film_alpha=0.0):
    """
    進捗 progress(0〜1) の時点の合成フレームを作る。
      - 背景の上に、本番マスクを film_offset だけズラした「フィルム縁取り」を
        film_color/film_alpha で敷く（film_offsetが[0,0]なら人物カラーの下に
        完全に隠れるため、見た目には現れない＝既定で無効化される）
      - 描画済みの領域だけ元のカラーが見える（筆先スタンプによるマスクで復元）
      - 描いたばかりの先端付近には白い絵の具（alpha 0.85）が乗り、
        少し後ろでフェードして消える（＝最終的にはカラーだけが残る）
    """
    if total_len <= 0:
        return bg.copy()

    head = total_len * float(np.clip(progress, 0.0, 1.0))
    mask = draw_stroke_mask(geo, W, bg.shape[0], 0.0, head, shape)

    out = bg.astype(np.float32)

    dx, dy = float(film_offset[0]) * W, float(film_offset[1]) * W
    if film_alpha > 0 and (abs(dx) >= 0.5 or abs(dy) >= 0.5):
        film_mask = translate_mask(mask, dx, dy)
        fm = (film_mask.astype(np.float32) / 255.0)[:, :, None] * float(np.clip(film_alpha, 0.0, 1.0))
        film_bgr = np.array(film_color_bgr or (50, 100, 255), dtype=np.float32)
        out = out * (1.0 - fm) + film_bgr * fm

    m = (mask.astype(np.float32) / 255.0)[:, :, None]
    out = out * (1.0 - m) + color.astype(np.float32) * m

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


def contains_japanese(text):
    """ひらがな・カタカナ・CJK統合漢字が含まれるかどうか"""
    for ch in text or "":
        code = ord(ch)
        if (0x3040 <= code <= 0x30FF or      # ひらがな・カタカナ
                0x3400 <= code <= 0x4DBF or  # CJK統合漢字拡張A
                0x4E00 <= code <= 0x9FFF or  # CJK統合漢字
                0xFF66 <= code <= 0xFF9F):   # 半角カタカナ
            return True
    return False


def resolve_title_font_path(fz, json_dir):
    """
    title_font / title_font_jp（テキストが日本語を含むかで選ぶ）から実パスを解決する。
    どちらも未指定なら font にフォールバックする（既存JSONとの後方互換のため）。
    """
    key = "title_font_jp" if contains_japanese(fz.get("name", "")) else "title_font"
    path = fz.get(key) or fz.get("font")
    return resolve_path(path, [os.getcwd(), json_dir, SCRIPT_DIR])


def telop_bounce_scale(t):
    """t(0〜1、フェード進行割合)から、バウンス中のテロップのスケール値を返す（急停止イージング）"""
    t = float(np.clip(t, 0.0, 1.0))
    eased = 1.0 - (1.0 - t) ** 4   # ease-out-quart：最初速く、終盤で急停止
    return TELOP_BOUNCE_FROM + (1.0 - TELOP_BOUNCE_FROM) * eased


def scale_telop_layer(bgr, alpha, scale, cx, cy):
    """テロップ層（BGR・アルファ）を(cx, cy)中心にscale倍する（バウンス演出用）"""
    if abs(scale - 1.0) < 0.001:
        return bgr, alpha
    H, W = bgr.shape[:2]
    M = cv2.getRotationMatrix2D((float(cx), float(cy)), 0.0, scale)
    bgr_s = cv2.warpAffine(bgr, M, (W, H), flags=cv2.INTER_LINEAR,
                            borderMode=cv2.BORDER_CONSTANT, borderValue=(0, 0, 0))
    alpha2d = alpha[:, :, 0] if alpha.ndim == 3 else alpha
    alpha_s = cv2.warpAffine(alpha2d, M, (W, H), flags=cv2.INTER_LINEAR,
                              borderMode=cv2.BORDER_CONSTANT, borderValue=0.0)
    return bgr_s, alpha_s[:, :, None]


def render_telop_layer(text, W, H, font):
    """
    テロップを1回だけ描いて (BGR画像, アルファ0〜1, 中心x, 中心y) として返す。
    フェードインは毎フレームこのアルファに係数を掛けるだけで済ませる。
    """
    if not text:
        return None, None, 0, 0

    size = max(8, int(round(H * TELOP_SIZE_RATIO)))
    cx, cy = W // 2, int(round(H * TELOP_Y_RATIO))
    shadow_offset = max(1, size // 20)
    shadow_alpha = 130   # 薄い黒ドロップシャドウ（0〜255）

    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))

    if font is not None:
        draw = ImageDraw.Draw(layer)
        # 白太字＋薄い黒ドロップシャドウ（先に影を描き、後から白文字を重ねる）
        draw.text((cx + shadow_offset, cy + shadow_offset), text, font=font, anchor="mm",
                  fill=(0, 0, 0, shadow_alpha))
        draw.text((cx, cy), text, font=font, anchor="mm", fill=(255, 255, 255, 255))
        rgba = np.array(layer)
    else:
        # フォントが無い環境向けフォールバック（日本語は表示できない）
        warn("日本語フォントが無いため OpenCV の既定フォントで描画します。")
        tmp = np.zeros((H, W, 4), np.uint8)
        scale = size / 30.0
        thickness = max(2, size // 12)
        (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, scale, thickness)
        org = (cx - tw // 2, cy + th // 2)
        shadow_org = (org[0] + shadow_offset, org[1] + shadow_offset)
        shadow_layer = np.zeros((H, W, 4), np.uint8)
        cv2.putText(shadow_layer, text, shadow_org, cv2.FONT_HERSHEY_SIMPLEX, scale,
                    (0, 0, 0, 255), thickness, cv2.LINE_AA)
        shadow_layer[:, :, 3] = (shadow_layer[:, :, 3].astype(np.float32) *
                                  (shadow_alpha / 255.0)).astype(np.uint8)
        tmp = shadow_layer
        cv2.putText(tmp, text, org, cv2.FONT_HERSHEY_SIMPLEX, scale,
                    (255, 255, 255, 255), thickness, cv2.LINE_AA)
        rgba = tmp

    rgb = rgba[:, :, :3].astype(np.float32)
    bgr = rgb[:, :, ::-1].copy() if font is not None else rgb  # PILはRGB、cv2はBGR
    alpha = (rgba[:, :, 3].astype(np.float32) / 255.0)[:, :, None]
    return bgr, alpha, cx, cy


def blend_telop(frame, telop_bgr, telop_alpha, fade):
    """テロップを fade(0〜1) の濃さで重ねる"""
    if telop_bgr is None or fade <= 0:
        return frame
    a = telop_alpha * float(np.clip(fade, 0.0, 1.0))
    out = frame.astype(np.float32) * (1.0 - a) + telop_bgr * a
    return np.clip(out, 0, 255).astype(np.uint8)


# ---------------------------------------------------------------------------
# ロゴ（動画末尾 or 最後のフリーズ中に表示）
# ---------------------------------------------------------------------------

def resolve_logo_at(at, has_freezes):
    """logo.at を検証する。last_freeze を指定してもfreezesが無ければ end にフォールバックする"""
    at = at or DEFAULT_LOGO_AT
    if at not in ("end", "last_freeze"):
        warn(f"logo.at='{at}' は未知の値です。'{DEFAULT_LOGO_AT}' として扱います。")
        at = DEFAULT_LOGO_AT
    if at == "last_freeze" and not has_freezes:
        warn("logo.at='last_freeze' ですが freezes が空のため、'end' として扱います。")
        at = "end"
    return at


def load_logo_image(path):
    """ロゴPNGを読み込み、(BGR uint8, アルファ float32 Hlogo×Wlogo×1) を返す。無ければ (None, None)"""
    if not path or not os.path.exists(path):
        return None, None
    img = Image.open(path).convert("RGBA")
    arr = np.array(img)
    bgr = arr[:, :, :3][:, :, ::-1].copy()
    alpha = (arr[:, :, 3].astype(np.float32) / 255.0)[:, :, None]
    return bgr, alpha


def logo_bounce_scale(t):
    """t(0〜1、LOGO_BOUNCE_SEC内での経過割合)から、ロゴのスケール値を返す（急停止イージング）"""
    t = float(np.clip(t, 0.0, 1.0))
    eased = 1.0 - (1.0 - t) ** 4
    return LOGO_BOUNCE_FROM + (1.0 - LOGO_BOUNCE_FROM) * eased


def composite_logo(frame, logo_bgr, logo_alpha, W, H, scale):
    """
    frame の中央に、ロゴを「基準表示幅（LOGO_WIDTH_RATIO×W）× scale」のサイズで合成する。
    scale=1.0 が最終的な表示サイズ、LOGO_BOUNCE_FROM が縮み始めの大きさ。
    """
    if logo_bgr is None:
        return frame
    lh, lw = logo_bgr.shape[:2]
    if lw <= 0 or lh <= 0:
        return frame

    base_scale = (W * LOGO_WIDTH_RATIO) / lw
    final_scale = max(0.01, base_scale * scale)
    new_w = max(1, int(round(lw * final_scale)))
    new_h = max(1, int(round(lh * final_scale)))
    interp = cv2.INTER_AREA if final_scale < 1.0 else cv2.INTER_LINEAR
    resized_bgr = cv2.resize(logo_bgr, (new_w, new_h), interpolation=interp).astype(np.float32)
    resized_alpha = cv2.resize(logo_alpha[:, :, 0], (new_w, new_h), interpolation=interp)[:, :, None]

    cx, cy = W // 2, H // 2
    x0, y0 = cx - new_w // 2, cy - new_h // 2
    x1, y1 = x0 + new_w, y0 + new_h
    sx0, sy0 = max(0, -x0), max(0, -y0)
    sx1, sy1 = new_w - max(0, x1 - W), new_h - max(0, y1 - H)
    dx0, dy0 = max(0, x0), max(0, y0)
    dx1, dy1 = min(W, x1), min(H, y1)
    if dx1 <= dx0 or dy1 <= dy0 or sx1 <= sx0 or sy1 <= sy0:
        return frame

    out = frame.copy()
    patch_bgr = resized_bgr[sy0:sy1, sx0:sx1]
    patch_a = resized_alpha[sy0:sy1, sx0:sx1]
    region = out[dy0:dy1, dx0:dx1].astype(np.float32)
    blended = region * (1.0 - patch_a) + patch_bgr * patch_a
    out[dy0:dy1, dx0:dx1] = np.clip(blended, 0, 255).astype(np.uint8)
    return out


def render_logo_frame(frame, logo_bgr, logo_alpha, W, H, elapsed_sec):
    """ロゴ表示区間内の経過秒数 elapsed_sec に応じた1フレームを作る（縮小アニメ→静止表示）"""
    if logo_bgr is None:
        return frame
    if elapsed_sec < LOGO_BOUNCE_SEC:
        scale = logo_bounce_scale(elapsed_sec / LOGO_BOUNCE_SEC)
    else:
        scale = 1.0
    return composite_logo(frame, logo_bgr, logo_alpha, W, H, scale)


# ---------------------------------------------------------------------------
# フリーズ区間の設計（映像と音声で同じ数値を使うため一箇所で計算する）
# ---------------------------------------------------------------------------

def plan_freezes(freezes, fps, src_frames, json_dir, logo=None, logo_at=None):
    """
    各フリーズについて、挿入位置（フレーム番号）と各フェーズのフレーム数を決める。
    logo_at=='last_freeze' の場合、時刻が一番遅いフリーズの静止保持（rest）フェーズを
    ロゴの表示時間ぶん確保できるよう延長する。
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
            "show_logo": False,
        })
    plans.sort(key=lambda p: p["frame_index"])

    if logo and logo_at == "last_freeze" and plans:
        last = plans[-1]
        last["show_logo"] = True
        need_rest = max(1, int(round(float(logo.get("duration_sec", 1.5)) * fps)))
        if last["n_rest"] < need_rest:
            extra = need_rest - last["n_rest"]
            last["n_rest"] += extra
            last["n_total"] += extra

    return plans


def iter_freeze_frames(frame, plan, W, H, fps, font_cache, json_dir, logo_bgr=None, logo_alpha=None):
    """
    1回分のフリーズ区間のフレームを順に生成する（メモリに溜めない）。
      1. 静止（背景処理のみ）
      2. ブラシが伸びる（フィルム縁取り→人物カラーの順に復元）
      3. ブラシ完了 → テロップがフェードイン（バウンス可）、そのまま保持
         （plan["show_logo"]がTrueなら、同じタイミングでロゴも表示する）
    """
    fz = plan["fz"]
    bg = make_background(frame, fz.get("background", "mono"), float(fz.get("mono_contrast", 1.0)))
    shape = resolve_brush_shape(fz.get("brush_shape"))
    geo, total_len = build_stroke_geometry(
        fz.get("strokes") or [], W, H, float(fz.get("brush_width", 0.12)))
    film_offset = fz.get("film_offset") or (0.0, 0.0)
    film_color_bgr = hex_to_bgr(fz.get("film_color"))
    film_alpha = float(fz.get("film_alpha", 0.0))

    font_path = resolve_title_font_path(fz, json_dir)
    if font_path not in font_cache:
        font_cache[font_path] = load_font(font_path, max(8, int(round(H * TELOP_SIZE_RATIO))))
    telop_bgr, telop_alpha, tcx, tcy = render_telop_layer(fz.get("name", ""), W, H, font_cache[font_path])
    bounce = bool(fz.get("title_bounce"))

    # 1) ブラシ開始まで静止
    for _ in range(plan["n_hold"]):
        yield bg.copy()

    # 2) ブラシ描画
    for i in range(plan["n_brush"]):
        progress = (i + 1) / float(plan["n_brush"])
        yield composite_brush(bg, frame, geo, total_len, progress, W, shape,
                               film_offset, film_color_bgr, film_alpha)

    # 3) 完成状態＋テロップのフェードイン（バウンス可）→保持。ロゴがあれば同時に表示する
    done = composite_brush(bg, frame, geo, total_len, 1.0, W, shape,
                            film_offset, film_color_bgr, film_alpha)
    fade_frames = max(1, int(round(TELOP_FADE_SEC * fps)))
    for i in range(plan["n_rest"]):
        t = (i + 1) / float(fade_frames)
        fade = min(1.0, t)
        if bounce and t < 1.0:
            scale = telop_bounce_scale(t)
            f_bgr, f_alpha = scale_telop_layer(telop_bgr, telop_alpha, scale, tcx, tcy) \
                if telop_bgr is not None else (None, None)
        else:
            f_bgr, f_alpha = telop_bgr, telop_alpha
        out = blend_telop(done, f_bgr, f_alpha, fade)
        if plan["show_logo"]:
            out = render_logo_frame(out, logo_bgr, logo_alpha, W, H, i / float(fps))
        yield out


def render_preview(frame, plan, W, H, fps, font_cache, json_dir, out_png):
    """--preview 用：ブラシ完了＋テロップ全表示のフレームを1枚PNGで出す"""
    fz = plan["fz"]
    bg = make_background(frame, fz.get("background", "mono"), float(fz.get("mono_contrast", 1.0)))
    shape = resolve_brush_shape(fz.get("brush_shape"))
    geo, total_len = build_stroke_geometry(
        fz.get("strokes") or [], W, H, float(fz.get("brush_width", 0.12)))
    film_offset = fz.get("film_offset") or (0.0, 0.0)
    film_color_bgr = hex_to_bgr(fz.get("film_color"))
    film_alpha = float(fz.get("film_alpha", 0.0))
    img = composite_brush(bg, frame, geo, total_len, 1.0, W, shape,
                           film_offset, film_color_bgr, film_alpha)

    font_path = resolve_title_font_path(fz, json_dir)
    font = load_font(font_path, max(8, int(round(H * TELOP_SIZE_RATIO))))
    telop_bgr, telop_alpha, _tcx, _tcy = render_telop_layer(fz.get("name", ""), W, H, font)
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


def build_audio(src_path, plans, fps, src_frames, has_audio, sr=AUDIO_SR, ch=AUDIO_CH,
                 logo_sfx_path=None, logo_at=None, logo_extra_frames=0):
    """
    フリーズ区間の分だけ元音声を後ろへずらし、
    フリーズ中は無音 or 直前0.5秒のループを差し込み、効果音を重ねる。
    logo_at=='last_freeze' なら最後のフリーズのロゴ表示開始時、
    logo_at=='end' なら末尾に追加する logo_extra_frames 分の無音区間の先頭で、
    logo_sfx_path のSEを鳴らす。
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
        # ロゴがこのフリーズ中に表示される場合、表示開始（rest開始）と同時にSEを鳴らす
        if plan.get("show_logo") and logo_at == "last_freeze" and logo_sfx_path:
            logo_offset = int(round((plan["n_hold"] + plan["n_brush"]) / float(fps) * sr))
            sfx_jobs.append((written + logo_offset, logo_sfx_path))
        written += n_samples

    pieces.append(orig[cursor:])
    tail_start = written + (len(orig) - cursor)   # 元音声の残り分を挟んだ後の位置

    if logo_at == "end" and logo_extra_frames > 0:
        extra_samples = int(round(logo_extra_frames / float(fps) * sr))
        pieces.append(np.zeros((extra_samples, ch), np.float32))
        if logo_sfx_path:
            sfx_jobs.append((tail_start, logo_sfx_path))

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

    # --- ロゴ設定の解決（無指定ならlogo_cfg=None、以後ロゴ関連処理はすべて素通りになる） ---
    logo_cfg = project.get("logo")
    logo_at = None
    logo_bgr, logo_alpha = None, None
    logo_sfx_path = None
    logo_extra_frames = 0
    if logo_cfg:
        logo_at = resolve_logo_at(logo_cfg.get("at"), bool(project["freezes"]))
        logo_path = resolve_path(logo_cfg.get("image"), [os.getcwd(), json_dir, SCRIPT_DIR])
        logo_bgr, logo_alpha = load_logo_image(logo_path)
        if logo_bgr is None:
            warn(f"ロゴ画像が見つかりません（{logo_cfg.get('image')}）。ロゴ演出は無効化します。")
            logo_cfg, logo_at = None, None
        else:
            if logo_cfg.get("sfx"):
                cand = os.path.join("assets", "sfx", f"{logo_cfg['sfx']}.wav")
                logo_sfx_path = resolve_path(cand, [os.getcwd(), json_dir, SCRIPT_DIR])
                if not os.path.exists(logo_sfx_path):
                    warn(f"ロゴ用の効果音が見つかりません: {cand}")
                    logo_sfx_path = None
            if logo_at == "end":
                logo_extra_frames = max(1, int(round(float(logo_cfg.get("duration_sec", 1.5)) * fps)))

    plans = plan_freezes(project["freezes"], fps, src_frames, json_dir, logo_cfg, logo_at)

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
        audio = build_audio(video_path, plans, fps, src_frames, info["has_audio"],
                             logo_sfx_path=logo_sfx_path, logo_at=logo_at,
                             logo_extra_frames=logo_extra_frames)
        wav_path = write_wav(os.path.join(tmpdir, "audio.wav"), audio)
        log(f"音声トラック: {len(audio) / AUDIO_SR:.2f} 秒")

        total_out = src_frames + sum(p["n_total"] for p in plans) + logo_extra_frames
        reader = open_frame_reader(video_path, W, H, fps)
        writer = open_video_writer(out_path, W, H, fps, wav_path)

        font_cache = {}
        pending = list(plans)      # まだ挿入していないフリーズ
        written = 0
        last_out_frame = None

        try:
            for i, frame in enumerate(iter_frames(reader, W, H)):
                # このフレーム位置に来たフリーズを（複数あってもすべて）挿入する
                while pending and pending[0]["frame_index"] == i:
                    plan = pending.pop(0)
                    for f in iter_freeze_frames(frame, plan, W, H, fps, font_cache, json_dir,
                                                logo_bgr, logo_alpha):
                        writer.stdin.write(f.tobytes())
                        last_out_frame = f
                        written += 1
                        if written % 15 == 0:
                            print(f"\r  {written}/{total_out} フレーム",
                                  end="", flush=True)
                writer.stdin.write(np.ascontiguousarray(frame).tobytes())
                last_out_frame = frame
                written += 1
                if written % 15 == 0:
                    print(f"\r  {written}/{total_out} フレーム", end="", flush=True)

            # 動画末尾より後ろを指すフリーズが残っていたら最後のフレームで処理する
            for plan in pending:
                for f in iter_freeze_frames(frame, plan, W, H, fps, font_cache, json_dir,
                                            logo_bgr, logo_alpha):
                    writer.stdin.write(f.tobytes())
                    last_out_frame = f
                    written += 1

            # logo.at=='end'：最後に出力したフレームを背景に、ロゴだけを追加区間として書き足す
            if logo_at == "end" and logo_bgr is not None and last_out_frame is not None:
                backdrop = np.ascontiguousarray(last_out_frame)
                for i in range(logo_extra_frames):
                    out_frame = render_logo_frame(backdrop, logo_bgr, logo_alpha, W, H, i / float(fps))
                    writer.stdin.write(out_frame.tobytes())
                    written += 1
                    if written % 15 == 0:
                        print(f"\r  {written}/{total_out} フレーム", end="", flush=True)
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
