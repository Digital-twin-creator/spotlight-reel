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
    python render.py project.json --preview            # 確認用PNGを2枚（スライド前後）だけ出力

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
import time
import wave

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

# ---------------------------------------------------------------------------
# 定数・既定値
# ---------------------------------------------------------------------------

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

# style の既定値（プロジェクトJSONの style / 各freeze で上書きされる）
#
# reveal_sec / slide_sec / hold_sec / brush_anim_sec / freeze_sec は、意図的にここへ
# 既定値を持たせていない（shadowキーと同じ理由）。1フリーズの流れを「①塗り(reveal_sec)→
# ②ズレ(slide_sec)→③静止(hold_sec)」の3段に分解した際、「JSON側で省略された」ことと
# 「旧キー（brush_anim_sec/freeze_sec）が明示的に指定された」ことを区別できないと、
# 後方互換の読み替え（resolve_reveal_sec/resolve_hold_sec）が正しく行えないため。
# 新しいJSONは reveal_sec/slide_sec/hold_sec だけを使うこと（各既定値は
# REVEAL_SEC_DEFAULT/SHADOW_SLIDE_IN_SEC_DEFAULT/HOLD_SEC_DEFAULT）。
DEFAULT_STYLE = {
    "brush_width": 0.12,          # ブラシ太さ（動画幅に対する比率）
    "brush_shape": "round",       # round | hake | marker | spray
    "mono_contrast": 1.0,         # mono背景のコントラスト倍率（1.0=従来どおり無加工）
    # film_offset/film_color/film_alpha は廃止（shadowに統合）。旧JSONとの後方互換のためだけに
    # ここに残しており、resolve_shadow_config() がshadow未指定時にこれらを読み替える。
    # 新しいJSONはshadowだけを使うこと（DEFAULT_STYLEのshadowキー参照）。
    "film_offset": [0.0, 0.0],
    "film_color": "#FF6432",
    "film_alpha": 0.8,
    "background": "mono",         # mono | dark
    "font": "assets/fonts/NotoSansJP-Bold.ttf",
    # title_font / title_font_jp は省略時 font にフォールバックする（use_style_font()参照）。
    # ここではキー自体を持たせず、未指定であることを判別できるようにする。
    "title_bounce": False,        # テロップ出現時に130%→100%のバウンスを付けるか
    "title_pos": [0.5, 0.78],     # テロップ中心の位置（出力サイズに対する比率、[x, y]）
    "title_size": 0.06,           # 文字サイズ（高さに対する比率）
    "title_align": "center",      # テロップの水平寄せ（left | center | right、title_posのxを基準）
    "brush_fade_sec": 0.3,        # ブラシ完了後、先端に残る白い絵の具をフェードアウトさせる時間（秒）。0でフェードなし
    "audio_during_freeze": "mute",  # mute | keep
    # mask は廃止（color_source/shadow.sourceに分離）。旧JSONとの後方互換のためだけに
    # ここに残しており、resolve_color_source()がcolor_source未指定時にこれを読み替える。
    "mask": "brush",              # brush | auto | auto+brush（後方互換専用。新JSONはcolor_sourceを使う）
    "mask_options": None,         # {"model": "...", "refine": "vitmatte"|None, "decontaminate": bool,
                                   #  "include_held_objects": bool}（自動切り抜きを使う場合のみ使用。
                                   #  include_held_objectsはmodel="rvm-mobilenetv3"の時だけ意味を持ち、
                                   #  省略時はTrue＝isnet-general-useの前景のうち人物マスクに接する
                                   #  部分を合成して手に持った物の欠落を補う。merge_held_objects_with_isnet参照）
    "reveal": "wipe",             # wipe | fade | brush | none（人物の出現アニメ）
    # color_source（カラー化に使うマスク）もここには持たせない。省略時は上のmaskキーを
    # 読み替える（resolve_color_source参照）ことで、新旧どちらのJSONも同じロジックで扱える。
    # "color_source": "brush" | "auto"
    # 影（フィルム色）演出はキーをここに含めない（意図的）。
    # style/freezeのJSONに"shadow"キーが無い場合、resolve_shadow_config()は既定で
    # 影を有効にする（フルデフォルト値）。DEFAULT_STYLEにここで既定値を持たせてしまうと
    # 「JSON側で省略された」ことと「明示的に"shadow": nullが指定された」ことを
    # 区別できなくなってしまうため、あえて持たせていない。
    # {"color": "#RRGGBB", "alpha": 0〜1, "distance": 出力幅比, "direction": "auto"|"left"|"right",
    #  "offset_y": 出力幅比, "blur": 出力幅比（既定0＝従来のフィルム色ベタ塗り）,
    #  "source": "same"|"brush"|"auto"（影の形に使うマスク。既定"same"=color_sourceと同じ）}
    # 無効にしたい場合は "shadow": null または "shadow": {"enabled": false} を指定する。
}

TITLE_ALIGNS = ("left", "center", "right")

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
BRUSH_PAINT_ALPHA = 0.85      # 描いている最中の白い絵の具の不透明度
DARK_GAIN = 0.30              # background="dark" のときの明るさ倍率

LOGO_WIDTH_RATIO_DEFAULT = 0.62   # ロゴの着地後の基準表示幅（出力幅に対する比率。以前は0.55、その前は0.4固定）
DEFAULT_LOGO_AT = "end"       # logo.at の既定値・不明値のフォールバック先
DEFAULT_LOGO_BACKGROUND = "auto"  # logo.background の既定値・不明値のフォールバック先
LOGO_CROP_MARGIN_RATIO = 0.06     # ロゴ自動クロップ時に残す余白（検出した外接矩形の辺の長さに対する比率）
LOGO_CROP_ALPHA_THRESH = 0.02     # クロップ判定：これを超えるアルファを「内容」とみなす
LOGO_CROP_COLOR_THRESH = 30.0     # クロップ判定（不透明PNG用）：四隅平均色とのBGR差分合計がこれを超えれば「内容」
                                  # （以前は18.0。JPEG保存されたロゴ画像の圧縮ノイズで四隅付近が
                                  #   誤って「内容」と判定される事例があったため、
                                  #   下記のLOGO_CROP_CORNER_PATCH_PXでのパッチ平均化と合わせて調整）
LOGO_CROP_CORNER_PATCH_PX = 6     # 四隅の背景色を推定する際、1点だけでなくこの半径のパッチ（正方形）を
                                  # 平均する。JPEGノイズで四隅の1画素だけがたまたま外れ値になり、
                                  # 背景色の推定自体がズレる事態を避けるため。
LOGO_CROP_DENOISE_KERNEL_PX = 5   # 「内容」判定マスクにかけるモルフォロジー・オープニングのカーネルサイズ。
                                  # 背景側にノイズで紛れ込んだ孤立画素を、外接矩形を求める前に除去する。

# ロゴ演出「インパクト着地＋光彩スイープ」のパラメータ。
# start_width_ratio/hold_big_sec/shrink_sec/settle_sec/sweep_start_sec/sweep_sec/
# flash_strength/shake_sec/shake_amplitude/duration_sec/fade_sec/sfx_tail は
# logo{} 配下でJSON上書き可能（resolve_logo_paramsで読み取る）。それ以外
# （フラッシュの長さ・最小サイズへの沈み込み量・スイープ帯の幅比率・
# 保持中に拡大する先・暗転経由のカットつなぎの長さ・SFXテールの長さ）は固定値。
#
# 着地演出は3段階（タメ→縮小→セトル）で構成する:
#   タメ(hold_big_sec)  : start_width_ratio のまま静止（開始インパクトを強調する「間」）
#   縮小(shrink_sec)    : start_width_ratio から最小サイズ（width_ratio×(1-LOGO_LANDING_DIP_DEFAULT)）
#                         までEase-In（ゆっくり入って終盤に加速→急停止）で縮む
#   セトル(settle_sec)  : 最小サイズから最終サイズ(width_ratio)までEase-Outで戻る
# 「着地の瞬間」＝最小サイズに到達する瞬間（タメ+縮小の終わり）で、SE・白フラッシュ・
# 画面の揺れ・logo.sfxのpeak_at_landingはすべてこの瞬間に揃える。
LOGO_START_WIDTH_RATIO_DEFAULT = 1.6  # logo.start_width_ratio の既定値（出力幅に対する比率）。
                                       # 160%＝両端が画面外に見切れた状態から表示を始める
                                       # （以前は105%固定だった。実機での「インパクトが弱い」
                                       # という指摘への対応）。
LOGO_HOLD_BIG_SEC_DEFAULT = 0.15  # タメ：start_width_ratioのまま見せる時間
LOGO_SHRINK_SEC_DEFAULT = 0.5     # 縮小：start_width_ratioから最小サイズまで縮む時間
LOGO_SETTLE_SEC_DEFAULT = 0.15    # セトル：最小サイズから最終サイズ(width_ratio)へ戻る時間
LOGO_LANDING_DIP_DEFAULT = 0.02   # 着地の沈み込み量（スケール比率。最小サイズ＝width_ratio×(1-これ)）
LOGO_FLASH_SEC = 0.05             # 着地の瞬間の白フラッシュの長さ（固定）
LOGO_FLASH_STRENGTH_DEFAULT = 0.35  # 白フラッシュの強さ（screen合成、0〜1。以前は0.6）
LOGO_SHAKE_SEC_DEFAULT = 0.25         # 着地の瞬間に画面全体をわずかに揺らす時間
LOGO_SHAKE_AMPLITUDE_RATIO_DEFAULT = 0.004  # 揺れの振幅（出力幅に対する比率）
LOGO_SWEEP_START_SEC_DEFAULT = 0.35   # 着地演出完了（タメ+縮小+セトル終わり）から光彩スイープ開始までの時間
LOGO_SWEEP_SEC_DEFAULT = 0.70     # 光彩スイープにかかる時間（以前は0.3秒）
LOGO_SWEEP_WIDTH_RATIO = 0.25     # 光彩帯の幅（ロゴ幅に対する比率、固定）
LOGO_GROW_TO = 1.03               # 保持中にゆっくり拡大する先（103%、固定）
LOGO_FADE_TO_BG_SEC_DEFAULT = 0.4  # 終了直前、背景色へ暗転する時間（以前は0.3秒→0.6秒→0.4秒）
LOGO_CUT_BLACKOUT_SEC = 0.1       # 直前のシーンから0.1秒かけて暗転し、そこでカットしてロゴ演出へ
                                   # 切り替わる（クロスフェードではなくカットつなぎ。logo.at="end"/
                                   # "last_freeze"のどちらも、また logo.background の指定によらず
                                   # 常にこの暗転を挟む。固定値）。
LOGO_DURATION_SEC_DEFAULT = 2.2   # 着地からの表示時間の既定値（以前は1.2秒）
LOGO_SFX_TAIL_SEC = 0.6           # logo.sfx_tail=true（既定）のときに付ける減衰ディレイの長さ（固定）

AUDIO_SR = 48000              # 音声処理のサンプリングレート
AUDIO_CH = 2                  # 音声処理のチャンネル数（ステレオ固定）
KEEP_LOOP_SEC = 0.5           # audio_during_freeze="keep" のときにループする長さ

COLOR_SOURCES = ("brush", "auto")
SHADOW_SOURCES = ("same", "brush", "auto")
MASK_MODES = ("brush", "auto", "auto+brush")   # 後方互換専用（旧maskキー）。新JSONはcolor_sourceを使う
REVEALS = ("wipe", "fade", "brush", "none")
CACHE_DIR_NAME = "cache"      # 自動切り抜きのアルファをキャッシュするディレクトリ（cwd基準）
TIMING_JSON_NAME = "render_timing.json"  # 所要時間の内訳（--summary。render()呼び出しごとにリセット）

# フリーズごとの自動切り抜き所要時間・キャッシュ使用有無を記録する側路（get_or_extract_alpha
# 参照）。render()の呼び出しごとにクリアされ、成功時にTIMING_JSON_NAMEへ書き出される
# （ジョブサマリーでの内訳表示用。render()の戻り値やシグネチャは変えずに済むよう、
# あえてモジュールレベルの一覧として持たせている）。
_TIMING_LOG = []

# 自動切り抜き（rembg）のアルファ品質チェック。実写フレームでは、照明や被写体との
# コントラスト次第でモデルの出力が不安定になり、画面のほぼ全体（または逆にほぼ皆無）が
# 前景と判定される「破綻」が起こることがある。そのまま使うと不定形の失敗動画が
# できあがってしまうため、明らかにおかしい場合はレンダリング自体を止める。
AUTO_MASK_MIN_AREA_RATIO = 0.05   # マスク面積がこれ未満（画面の5%未満）なら失敗とみなす
AUTO_MASK_MAX_AREA_RATIO = 0.85   # マスク面積がこれを超える（画面の85%超）なら失敗とみなす
AUTO_MASK_FEATHER_SIGMA_PX = 1.5  # 後処理で最後にかける軽いフェザー（ガウスぼかし）の強さ

# RVM（人物専用モデル）は手に持った物（スマホ・マイク・カバン等）を人物から
# 切り離して除外してしまうことがある。isnet-general-use（汎用の顕著物体検出）の前景を
# 補助的に使い、RVMの人物マスクにこの距離（px）以内で接している連結成分だけを合成する
# （merge_touching_component参照）。無関係な背景の物体まで拾わないよう、あまり大きく
# しすぎない。
HELD_OBJECT_TOUCH_DILATE_PX = 6

# 1フリーズの流れ「①塗り(reveal)→②ズレ(slide)→③静止(hold)」の既定の秒数。
# reveal_sec/slide_sec/hold_sec がJSON側で省略された場合に使う（resolve_reveal_sec/
# resolve_hold_sec/_shadow_cfg_from_dict参照）。
REVEAL_SEC_DEFAULT = 0.5
HOLD_SEC_DEFAULT = 2.0

# 影（フィルム色）演出。人物の出現が完了した後、人物レイヤーだけを0→distanceまで
# スライドさせ、元の位置に静止したままの影レイヤーを覗かせる。
SHADOW_DIRECTIONS = ("auto", "left", "right")
SHADOW_COLOR_DEFAULT = "#FF6432"
SHADOW_ALPHA_DEFAULT = 0.8
SHADOW_DISTANCE_DEFAULT = 0.03    # 出力幅に対する比率
SHADOW_OFFSET_Y_DEFAULT = 0.02    # 出力幅に対する比率（下方向）
SHADOW_DIRECTION_AMBIGUOUS_BAND = 0.05  # マスクX重心が画面中心からこの比率以内なら「あいまい」とみなす
SHADOW_SLIDE_IN_SEC_DEFAULT = 0.5  # スライドインの時間（既定。slide_sec/shadow.slide_secで上書き可）
SHADOW_SLIDE_BACK_SEC = 0.25       # 保持終了→通常再生に戻る直前、人物を元位置へ戻す時間（固定）


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

    def parse_rate(val):
        if not val or val == "0/0":
            return None
        num, _, den = val.partition("/")
        den = den or "1"
        if float(den) != 0 and float(num) != 0:
            return float(num) / float(den)
        return None

    # avg_frame_rate: 実際に配信されたフレーム数から求めた「平均」fps。
    # r_frame_rate: コンテナが宣言する「基準」fps（可変フレームレートの場合、
    #   各フレーム間隔の最小公倍数に近い、実際の平均とかけ離れた値になることが多い）。
    # 両者が大きく食い違う場合は可変フレームレート(VFR)と判断する
    # （iPhoneのスクリーン録画など、実機の録画でよく見られる）。
    avg_fps = parse_rate(vs.get("avg_frame_rate"))
    r_fps = parse_rate(vs.get("r_frame_rate"))
    fps = avg_fps or r_fps or 30.0
    is_vfr = bool(avg_fps and r_fps and
                  abs(r_fps - avg_fps) > max(avg_fps, r_fps) * 0.01)

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
        "r_fps": r_fps,
        "duration": duration,
        "rotation": rotation,
        "has_audio": has_audio,
        "is_vfr": is_vfr,
    }


# ---------------------------------------------------------------------------
# 映像の入出力（ffmpeg パイプ）
# ---------------------------------------------------------------------------

def normalize_frame_rate(video_path, target_fps, has_audio, tmpdir):
    """
    可変フレームレート(VFR)の入力（iPhoneのスクリーン録画など、負荷でフレーム間隔が
    ばらつく実機の録画で典型的）を、固定フレームレート(CFR)の一時ファイルへ
    正規化してから返す。

    以降の全処理（フレーム抽出・音声の尺計算）は「フレーム番号 = 時刻 × fps」という
    単純な前提で動いているため、実際の再生間隔が不揃いなVFR入力のままだと、
    フリーズ挿入位置や音声の切り貼り位置が実際の見た目の時刻とズレていき、
    映像と音声が徐々に食い違う原因になる。CFRへ正規化することでこの前提を
    満たす状態にしてから、以降の処理を行う。
    """
    ffmpeg = find_exe("ffmpeg")
    out_path = os.path.join(tmpdir, "normalized_input.mp4")
    cmd = [ffmpeg, "-y", "-v", "error", "-i", video_path,
           "-vf", f"fps={target_fps:.6f}",
           "-vsync", "cfr",
           "-c:v", "libx264", "-preset", "veryfast", "-crf", "16",
           "-pix_fmt", "yuv420p"]
    cmd += ["-c:a", "aac", "-b:a", "256k"] if has_audio else ["-an"]
    cmd += [out_path]
    res = subprocess.run(cmd)
    if res.returncode != 0 or not os.path.exists(out_path):
        raise RuntimeError("可変フレームレート入力の固定フレームレートへの正規化に失敗しました。")
    return out_path


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


def translate_mask(img, dx, dy):
    """画像（H×W、またはH×W×Cのカラー画像）を(dx, dy)だけ平行移動する（はみ出た部分は切り捨てる）"""
    if abs(dx) < 0.5 and abs(dy) < 0.5:
        return img
    H, W = img.shape[:2]
    M = np.float32([[1, 0, dx], [0, 1, dy]])
    return cv2.warpAffine(img, M, (W, H), flags=cv2.INTER_LINEAR,
                           borderMode=cv2.BORDER_CONSTANT, borderValue=0)


def ease_out_cubic(t):
    """
    Ease-Out Cubic：1 - (1-t)^3（t=0→0, t=1→1）。
    以前使っていたEase-Out Expoは終盤ほぼ完全に停止する「急停止」の動きだったが、
    影のスライドをよりゆっくり・滑らかに見せるため、速度が最後まで滑らかに
    減衰していくCubicに変更した（急停止ではない）。
    """
    t = float(np.clip(t, 0.0, 1.0))
    return 1.0 - (1.0 - t) ** 3


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


def resolve_mask_mode(mode):
    """未知の値は brush（従来どおり完全後方互換）にフォールバックする（後方互換専用。新JSONはresolve_color_sourceを使う）"""
    mode = mode or "brush"
    if mode not in MASK_MODES:
        warn(f"mask='{mode}' は未知の値です。brush として扱います。")
        return "brush"
    return mode


def resolve_color_source(fz):
    """
    カラー化に使うマスクの種類を解決する。戻り値は "brush" | "auto" | "auto+brush"
    （auto+brushは旧mask値の後方互換専用。新JSONのcolor_sourceには存在しない）。

    "color_source" キーがあればそれを優先する（"brush"|"auto"のみ有効）。
    無ければ旧 "mask" キーを読み替える（resolve_mask_mode。省略時はbrush）。
    """
    if "color_source" in fz:
        source = fz.get("color_source") or "brush"
        if source not in COLOR_SOURCES:
            warn(f"color_source='{source}' は未知の値です。brush として扱います。")
            return "brush"
        return source
    return resolve_mask_mode(fz.get("mask"))


def resolve_reveal(reveal, mask_mode):
    """
    未知の値は wipe にフォールバックする。
    reveal="brush" は mask_mode="auto+brush" の場合のみ有効（それ以外はwipeにフォールバック）。
    reveal="none" は mask_mode="brush" 以外（auto/auto+brush）でのみ意味を持つ
    （mask_mode="brush"は常にブラシの progressive な描画を行うため、reveal自体を無視する）。
    """
    reveal = reveal or "wipe"
    if reveal not in REVEALS:
        warn(f"reveal='{reveal}' は未知の値です。wipe として扱います。")
        reveal = "wipe"
    if reveal == "brush" and mask_mode != "auto+brush":
        warn("reveal='brush' は mask='auto+brush' の場合のみ有効です。wipe として扱います。")
        reveal = "wipe"
    return reveal


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


def resolve_slide_sec(fz, shadow):
    """
    影スライドインの秒数を解決する（②ズレ）。優先順位:
      1. shadow.slide_sec（旧キー。明示されていれば後方互換で最優先）
      2. fz の slide_sec（新キー。style/freezeのどちらでも指定可）
      3. SHADOW_SLIDE_IN_SEC_DEFAULT（既定0.5秒）
    """
    if isinstance(shadow, dict) and "slide_sec" in shadow:
        return max(1e-3, float(shadow["slide_sec"]))
    if "slide_sec" in fz:
        return max(1e-3, float(fz["slide_sec"]))
    return SHADOW_SLIDE_IN_SEC_DEFAULT


def resolve_shadow_source(shadow):
    """shadow.source（影の形に使うマスク）を解決する。未知の値・未指定は既定'same'にフォールバックする"""
    source = shadow.get("source") if isinstance(shadow, dict) else None
    source = source or "same"
    if source not in SHADOW_SOURCES:
        warn(f"shadow.source='{source}' は未知の値です。'same' として扱います。")
        source = "same"
    return source


def _shadow_cfg_from_dict(shadow, fz):
    """shadow辞書（キー省略可）から、既定値で埋めた実効設定を組み立てる"""
    shadow = shadow if isinstance(shadow, dict) else {}
    direction = shadow.get("direction") or "auto"
    if direction not in SHADOW_DIRECTIONS:
        warn(f"shadow.direction='{direction}' は未知の値です。auto として扱います。")
        direction = "auto"
    return {
        "color": shadow.get("color") or SHADOW_COLOR_DEFAULT,
        "alpha": float(np.clip(shadow.get("alpha", SHADOW_ALPHA_DEFAULT), 0.0, 1.0)),
        "distance": float(shadow.get("distance", SHADOW_DISTANCE_DEFAULT)),
        "direction": direction,
        "offset_y": float(shadow.get("offset_y", SHADOW_OFFSET_Y_DEFAULT)),
        "blur": max(0.0, float(shadow.get("blur", 0.0))),
        "slide_sec": resolve_slide_sec(fz, shadow),
        "source": resolve_shadow_source(shadow),
    }


def resolve_shadow_config(fz):
    """
    fz（style×freezeマージ済み辞書）から実効的な影(shadow)設定を解決する。

    - "shadow" キーが無ければ、既定で影を**有効**にする（フルデフォルト値）。
      ただし旧 film_offset/film_color/film_alpha が明示されていれば
      （film_offsetのx・yのどちらかが非ゼロの場合のみ）、そちらを後方互換で優先する。
    - "shadow": null、または "shadow": {"enabled": false} は明示的な無効化。
    - それ以外の "shadow": {...} は指定値（省略項目は既定値）で有効化する。

    DEFAULT_STYLEには"shadow"キーを含めていないため、"shadow" in fz は
    「JSON側（style/freezeのどちらか）で実際にこのキーが書かれていたか」を正しく表す
    （書かれていなければ既定で有効、明示的にnull/enabled:falseなら無効、という区別に使う）。
    """
    if "shadow" in fz:
        shadow = fz.get("shadow")
        if shadow is None:
            return None
        if isinstance(shadow, dict) and shadow.get("enabled") is False:
            return None
        return _shadow_cfg_from_dict(shadow, fz)

    film_offset = fz.get("film_offset") or (0.0, 0.0)
    fx, fy = float(film_offset[0]), float(film_offset[1])
    if fx != 0.0 or fy != 0.0:
        return {
            "color": fz.get("film_color") or SHADOW_COLOR_DEFAULT,
            "alpha": float(np.clip(fz.get("film_alpha", SHADOW_ALPHA_DEFAULT), 0.0, 1.0)),
            "distance": abs(fx),
            "direction": "right" if fx >= 0 else "left",
            "offset_y": fy,
            "blur": 0.0,
            "slide_sec": resolve_slide_sec(fz, None),
            "source": "same",
        }

    # "shadow"キーも旧film_offsetも無い → 既定で影を有効にする
    return _shadow_cfg_from_dict(None, fz)


def mask_x_center_ratio(mask_u8, W):
    """人物マスクのX重心を、画面幅に対する比率(0〜1)で返す。マスクが空なら0.5（中心）を返す"""
    total = float(mask_u8.astype(np.float64).sum())
    if total <= 0:
        return 0.5
    xs = np.arange(W, dtype=np.float64)
    col_sums = mask_u8.astype(np.float64).sum(axis=0)
    weighted_x = float((col_sums * xs).sum() / total)
    return weighted_x / W


def resolve_shadow_auto_direction(mask_u8, W):
    """人物マスクのX重心を計算し、画面中心より右／左／あいまい（既定right）を返す"""
    ratio = mask_x_center_ratio(mask_u8, W)
    band = SHADOW_DIRECTION_AMBIGUOUS_BAND
    if ratio > 0.5 + band:
        return "right"
    if ratio < 0.5 - band:
        return "left"
    return "right"


def compute_shadow_slide_vector(mask_u8, W, shadow_cfg):
    """完成した人物マスクから、影演出のスライド着地先ベクトル(dx, dy)（px）を返す"""
    direction = shadow_cfg.get("direction", "auto")
    if direction not in ("left", "right"):
        direction = resolve_shadow_auto_direction(mask_u8, W)
    sign = 1.0 if direction == "right" else -1.0
    dx = sign * float(shadow_cfg.get("distance", SHADOW_DISTANCE_DEFAULT)) * W
    dy = float(shadow_cfg.get("offset_y", SHADOW_OFFSET_Y_DEFAULT)) * W
    return dx, dy


def render_shadow_layer(mask_u8, shadow_cfg):
    """
    人物の「元の位置」のマスク(mask_u8、平行移動しない)から影レイヤーの見た目を作る。
    blur=0（既定）ならマスクをそのまま使う＝従来のフィルム色ベタ塗りと同じ見た目になる。
    戻り値: (影の形 uint8, alpha, BGR色)
    """
    blur_ratio = float(shadow_cfg.get("blur", 0.0))
    if blur_ratio > 0:
        W = mask_u8.shape[1]
        k = int(round(blur_ratio * W))
        k = max(3, k + (1 - k % 2))   # 奇数に丸める（GaussianBlurの制約）
        shape_mask = cv2.GaussianBlur(mask_u8, (k, k), 0)
    else:
        shape_mask = mask_u8
    alpha = float(np.clip(shadow_cfg.get("alpha", SHADOW_ALPHA_DEFAULT), 0.0, 1.0))
    color_bgr = hex_to_bgr(shadow_cfg.get("color"), default=(50, 100, 255))
    return shape_mask, alpha, color_bgr


def composite_layers(bg, color, mask_u8, W, H, shadow_cfg=None,
                      slide_dx=0.0, slide_dy=0.0, paint_mask_u8=None, shadow_mask_u8=None):
    """
    1フレーム分の合成処理（マスクの作り方＝ブラシ／自動／自動＋ブラシ には依存しない、
    共通の「マスクさえあれば合成できる」部分）。
    レイヤー順: 背景 → 影（人物の元の位置。動かない） → 人物（(slide_dx, slide_dy)だけ
    ずらした位置） → （描いている最中の白い絵の具、あれば）。
    影は常に shadow_mask_u8（省略時はmask_u8と同じ）の位置（＝人物の「元の位置」）に
    固定して描き、人物レイヤー（mask_u8）だけをずらすことで、そのズレ量ぶん影が
    「現れる」ように見せる（影自体は動かさない）。shadow.source（影に使うマスクの種類）が
    color_sourceと異なる場合、shadow_mask_u8にその別マスクを渡す。
    """
    out = bg.astype(np.float32)

    if shadow_cfg:
        sm_mask = shadow_mask_u8 if shadow_mask_u8 is not None else mask_u8
        shadow_mask, shadow_alpha, shadow_color = render_shadow_layer(sm_mask, shadow_cfg)
        sm = (shadow_mask.astype(np.float32) / 255.0)[:, :, None] * shadow_alpha
        out = out * (1.0 - sm) + np.array(shadow_color, dtype=np.float32) * sm

    if abs(slide_dx) >= 0.5 or abs(slide_dy) >= 0.5:
        person_mask = translate_mask(mask_u8, slide_dx, slide_dy)
        person_color = translate_mask(color, slide_dx, slide_dy)
    else:
        person_mask = mask_u8
        person_color = color
    m = (person_mask.astype(np.float32) / 255.0)[:, :, None]
    out = out * (1.0 - m) + person_color.astype(np.float32) * m

    if paint_mask_u8 is not None:
        pm = (paint_mask_u8.astype(np.float32) / 255.0)[:, :, None] * BRUSH_PAINT_ALPHA
        out = out * (1.0 - pm) + 255.0 * pm

    return np.clip(out, 0, 255).astype(np.uint8)


def apply_reveal_wipe(base_mask_u8, H, progress):
    """下から上へ progress(0〜1) の割合だけアルファを拭き取るように表示する（境界は1px程度のアンチエイリアス）"""
    progress = float(np.clip(progress, 0.0, 1.0))
    if progress <= 0.0:
        return np.zeros_like(base_mask_u8)
    if progress >= 1.0:
        return base_mask_u8
    yy = np.arange(H, dtype=np.float32).reshape(H, 1)
    thresh_y = H * (1.0 - progress)
    gate = np.clip((yy - thresh_y) + 1.0, 0.0, 1.0)
    return np.clip(base_mask_u8.astype(np.float32) * gate, 0, 255).astype(np.uint8)


def apply_reveal_fade(base_mask_u8, progress):
    """base_mask_u8 全体を progress(0〜1) の濃さでフェードインする"""
    progress = float(np.clip(progress, 0.0, 1.0))
    return np.clip(base_mask_u8.astype(np.float32) * progress, 0, 255).astype(np.uint8)


def apply_brush_correction(alpha_u8, strokes, W, H, default_width, shape):
    """
    mask="auto+brush"：自動切り抜きのアルファ(alpha_u8)を、ブラシストロークで補正する。
    mode="add"（省略時の既定）は該当領域をα=1相当に塗り足し、mode="erase"はα=0相当に削る。
    フリーズごとに1回だけ計算する静的な補正（時間変化はreveal側が担当する）。
    """
    strokes = strokes or []
    add_strokes = [s for s in strokes if (s.get("mode") or "add") == "add"]
    erase_strokes = [s for s in strokes if s.get("mode") == "erase"]
    out = alpha_u8.astype(np.float32)

    if add_strokes:
        geo, total = build_stroke_geometry(add_strokes, W, H, default_width)
        if total > 0:
            add_mask = draw_stroke_mask(geo, W, H, 0.0, total, shape).astype(np.float32)
            out = np.maximum(out, add_mask)

    if erase_strokes:
        geo, total = build_stroke_geometry(erase_strokes, W, H, default_width)
        if total > 0:
            erase_mask = draw_stroke_mask(geo, W, H, 0.0, total, shape).astype(np.float32)
            out = out * (1.0 - erase_mask / 255.0)

    return np.clip(out, 0, 255).astype(np.uint8)


def cache_path_for_alpha(video_path, t, cache_dir):
    base = os.path.splitext(os.path.basename(video_path))[0]
    return os.path.join(cache_dir, f"{base}_{t:.3f}.npz")


def keep_largest_component(mask_u8):
    """2値マスク(0/255, H×W)から最大連結成分だけを残した2値マスクを返す（ノイズ・分裂した破片の除去）"""
    num_labels, labels, stats, _ = cv2.connectedComponentsWithStats(mask_u8, connectivity=8)
    if num_labels <= 1:
        return mask_u8  # 前景（背景以外のラベル）が無い
    areas = stats[1:, cv2.CC_STAT_AREA]  # ラベル0は背景なので除く
    largest_label = 1 + int(np.argmax(areas))
    return np.where(labels == largest_label, 255, 0).astype(np.uint8)


def fill_holes(mask_u8):
    """2値マスク(0/255, H×W)の内部にある穴（外周とつながっていない背景領域）を埋める"""
    h, w = mask_u8.shape
    padded = cv2.copyMakeBorder(mask_u8, 1, 1, 1, 1, cv2.BORDER_CONSTANT, value=0)
    flood = padded.copy()
    ff_mask = np.zeros((h + 4, w + 4), np.uint8)
    cv2.floodFill(flood, ff_mask, (0, 0), 255)
    exterior_bg = cv2.bitwise_not(flood)
    filled = cv2.bitwise_or(padded, exterior_bg)
    return filled[1:-1, 1:-1]


def merge_touching_component(alpha_base_u8, alpha_extra_u8, dilate_px=HELD_OBJECT_TOUCH_DILATE_PX):
    """
    alpha_base_u8（例：RVMの人物アルファ）に、alpha_extra_u8（例：isnet-general-useの
    前景アルファ）のうち、alpha_baseの前景（>=128）にdilate_pxピクセル以内で接している
    連結成分だけを合成する。人物と接していない孤立した物体（無関係な背景など）は
    取り込まれない。合成領域では、base側に元々値がある画素はbase優先、base側が背景
    だった画素（新たに取り込んだ部分）はextraの値を使う。
    """
    base_bin = np.where(alpha_base_u8 >= 128, 255, 0).astype(np.uint8)
    extra_bin = np.where(alpha_extra_u8 >= 128, 255, 0).astype(np.uint8)
    if not base_bin.any() or not extra_bin.any():
        return alpha_base_u8

    kernel = np.ones((3, 3), np.uint8)
    base_dilated = cv2.dilate(base_bin, kernel, iterations=max(1, int(dilate_px)))

    num_labels, labels, _, _ = cv2.connectedComponentsWithStats(extra_bin, connectivity=8)
    touching = np.zeros_like(extra_bin)
    for label in range(1, num_labels):
        component_mask = (labels == label)
        if np.any(base_dilated[component_mask] > 0):
            touching[component_mask] = 255

    if not touching.any():
        return alpha_base_u8

    return np.where(base_bin > 0, alpha_base_u8,
                     np.where(touching > 0, alpha_extra_u8, 0)).astype(np.uint8)


def merge_held_objects_with_isnet(alpha_rvm_u8, frame_bgr, extract_module):
    """
    RVM（人物専用モデル）は手に持った物（スマホ・マイク・カバン等）を人物から切り離して
    除外してしまうことがある。同じフレームをisnet-general-use（汎用の顕著物体検出）にも
    かけ、その前景のうちRVMの人物マスクに接している連結成分だけをmerge_touching_componentで
    合成する。isnet側の実行が何らかの理由で失敗しても、RVM単体の結果へ安全にフォールバック
    する（この補完はあくまで品質向上のための補助であり、必須の処理ではないため）。
    """
    try:
        rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        img = Image.fromarray(rgb)
        isnet_rgba_img, isnet_elapsed, _session = extract_module.extract_alpha(img, "isnet-general-use", None, False)
        h, w = alpha_rvm_u8.shape[:2]
        if isnet_rgba_img.size != (w, h):
            isnet_rgba_img = isnet_rgba_img.resize((w, h), Image.LANCZOS)
        alpha_isnet_u8 = np.array(isnet_rgba_img)[:, :, 3]
    except Exception as exc:  # noqa: BLE001 - 補助的な処理なので失敗してもRVM単体の結果にフォールバックする
        warn(f"手に持った物の補完に失敗しました（RVM単体の結果を使います）: {exc}")
        return alpha_rvm_u8

    merged = merge_touching_component(alpha_rvm_u8, alpha_isnet_u8)
    log(f"  手に持った物の補完: isnet-general-use（{isnet_elapsed:.2f}秒）の前景のうち人物に接する部分を合成")
    return merged


def postprocess_auto_alpha(alpha_u8):
    """
    自動切り抜き（rembg）のアルファに、破綻を防ぐための後処理を既定で適用する。
      1. 最大連結成分のみ残す（ノイズ・被写体から分裂した破片を除去）
      2. 穴埋め（被写体内部に残った小さな背景の穴を塞ぐ。白い服が背景と誤認されて
         内部に穴があく、といった典型的な失敗を軽減する）
      3. 軽いフェザー（ガウスぼかし）：1・2の処理でできた硬い輪郭を和らげる
    最大連結成分の範囲内は、元のモデル出力の連続値（半透明の境界）をそのまま保つ
    （境界の滑らかさを損なわないため、フェザー以外は範囲内の値自体を書き換えない）。
    """
    binary = np.where(alpha_u8 >= 128, 255, 0).astype(np.uint8)
    kept = keep_largest_component(binary)
    filled = fill_holes(kept)

    out = np.where(filled > 0, alpha_u8, 0).astype(np.float32)
    # 穴埋めで新たに追加された領域（穴埋め前は非前景だった箇所）は不透明として扱う
    hole_added = (filled > 0) & (kept == 0)
    out[hole_added] = 255.0

    out = cv2.GaussianBlur(out, (0, 0), sigmaX=AUTO_MASK_FEATHER_SIGMA_PX)
    return np.clip(out, 0, 255).astype(np.uint8)


def auto_mask_area_ratio(alpha_u8):
    """アルファ(0-255)のうち、閾値128以上（＝前景）の画素が占める面積比を返す"""
    return float((alpha_u8 >= 128).sum()) / float(alpha_u8.size)


def validate_auto_alpha(alpha_u8, context_label):
    """
    自動切り抜きのマスク面積が明らかに異常（画面のAUTO_MASK_MAX_AREA_RATIO超、
    またはAUTO_MASK_MIN_AREA_RATIO未満）であれば、「切り抜き失敗」とみなして
    RuntimeErrorを送出する。実写フレームでは、照明や被写体とのコントラスト次第で
    このような破綻が起こりうるため、不定形の失敗動画を最後まで作らないための最終防波堤。
    """
    ratio = auto_mask_area_ratio(alpha_u8)
    if ratio > AUTO_MASK_MAX_AREA_RATIO:
        raise RuntimeError(
            f"自動切り抜きに失敗しました（{context_label}）：マスクが画面の{ratio * 100:.1f}%を占めており、"
            "被写体だけを切り抜けていない可能性があります。動画のコントラストや照明を見直すか、"
            "手動ブラシでの切り抜き（color_source を \"brush\"）に切り替えてください。"
        )
    if ratio < AUTO_MASK_MIN_AREA_RATIO:
        raise RuntimeError(
            f"自動切り抜きに失敗しました（{context_label}）：マスクが画面の{ratio * 100:.1f}%しかなく、"
            "被写体を検出できていない可能性があります。動画のコントラストや照明を見直すか、"
            "手動ブラシでの切り抜き（color_source を \"brush\"）に切り替えてください。"
        )
    return ratio


def get_or_extract_alpha(frame_bgr, video_path, t, W, H, cache_dir, mask_options):
    """
    extract.py（自動切り抜き）でアルファ（0〜255連続値、H×W）を得る。
    cache_dir/<動画名>_<時刻>.npz にキャッシュし、同じ動画・同じフリーズ時刻の
    再レンダリング時は再計算しない。
    extract のimportはこの関数の中でのみ行うため、mask="brush"のみのプロジェクトは
    rembg/onnxruntimeが未インストールでも問題なく動く。
    """
    os.makedirs(cache_dir, exist_ok=True)
    cache_file = cache_path_for_alpha(video_path, t, cache_dir)

    if os.path.exists(cache_file):
        try:
            data = np.load(cache_file)
            alpha = data["alpha"]
            if alpha.shape == (H, W):
                log(f"  切り抜きキャッシュを使用します: {cache_file}")
                _TIMING_LOG.append({"time": round(t, 3), "cache_used": True, "extract_seconds": None})
                return alpha
            warn(f"切り抜きキャッシュのサイズが現在の出力解像度と一致しないため再計算します: {cache_file}")
        except Exception as exc:  # noqa: BLE001 - 壊れたキャッシュ等は再計算にフォールバック
            warn(f"切り抜きキャッシュを読み込めませんでした（再計算します）: {exc}")

    if SCRIPT_DIR not in sys.path:
        sys.path.insert(0, SCRIPT_DIR)
    import extract  # 遅延import（mask="brush"のみのプロジェクトではここに到達しない）

    opts = mask_options or {}
    model = opts.get("model") or extract.DEFAULT_MODEL
    refine = opts.get("refine")
    decontaminate = bool(opts.get("decontaminate"))

    if model == extract.APPLE_VISION_MODEL_NAME:
        # Apple Vision（VNGenerateForegroundInstanceMaskRequest）はmacOS専用で、
        # このrender.py自体（ubuntuランナー上で実行）は呼び出せない。事前に
        # spotlight-jobsのextract-apple段（macos-14）がtools/apple-vision/subject_lift.swiftを
        # 実行し、その結果をこの関数の先頭のキャッシュ判定が拾えるよう
        # cache_path_for_alpha() と同じ命名でcache_dir配下に .npz として置いておく必要がある。
        # ここに到達した時点でキャッシュが無いのは、そのプリ配置が行われなかった
        # （＝ワークフロー構成の不具合）ことを意味するため、明確に失敗させる。
        raise RuntimeError(
            f"model={extract.APPLE_VISION_MODEL_NAME} のアルファキャッシュが見つかりません"
            f"（{cache_file}）。Apple Visionモデルはrender.py（ubuntu）からは抽出できないため、"
            "事前にmacOSランナー上のextract-apple段でtools/apple-vision/subject_lift.swiftを実行し、"
            "結果をcache/に配置しておく必要があります。"
        )

    if model == extract.RVM_MODEL_NAME:
        # 動画モード（RVM）：静止フレーム1枚ではなく、video_path・tから前後クリップを
        # 自前で切り出して時系列で推論する。ここでの引数のframe_bgrは、RVM自体の推論には
        # 使わないが、include_held_objects（既定true）が有効な場合の手に持った物の補完
        # （isnet-general-useをこのフレームに対して追加で実行する）にそのまま使う。
        rgba, elapsed = extract.extract_alpha_rvm(video_path, t, W, H)
        alpha = rgba[:, :, 3]
        log(f"  自動切り抜き完了（動画モード）: {elapsed:.2f}秒（モデル={model}）")
        if opts.get("include_held_objects", True):
            alpha = merge_held_objects_with_isnet(alpha, frame_bgr, extract)
    else:
        rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        img = Image.fromarray(rgb)
        rgba_img, elapsed, _session = extract.extract_alpha(img, model, refine, decontaminate)
        if rgba_img.size != (W, H):
            rgba_img = rgba_img.resize((W, H), Image.LANCZOS)
        alpha = np.array(rgba_img)[:, :, 3]
        log(f"  自動切り抜き完了: {elapsed:.2f}秒（モデル={model}, refine={refine}, decontaminate={decontaminate}）")
    _TIMING_LOG.append({"time": round(t, 3), "cache_used": False, "extract_seconds": round(elapsed, 2)})

    alpha = postprocess_auto_alpha(alpha)
    ratio = validate_auto_alpha(alpha, f"t={t:.2f}s")
    log(f"  マスク後処理: 最大連結成分＋穴埋め＋フェザーを適用（面積比={ratio * 100:.1f}%）")

    np.savez_compressed(cache_file, alpha=alpha)
    return alpha


def freeze_needs_model_extraction(fz, extract_module, model_name):
    """
    このフリーズが、実際に自動切り抜き（color_source/shadow.sourceのいずれかが
    "auto"系）を使い、かつそのモデルがmodel_nameであれば True を返す。
    build_mask_context（mask_mode in auto/auto+brush）・build_shadow_mask
    （shadow.source=="auto"）がget_or_extract_alphaを呼ぶ条件と厳密に一致させる
    （spotlight-jobsのprepare段が、本番と食い違わずに対象フリーズを判定するため）。
    """
    opts = fz.get("mask_options") or {}
    model = opts.get("model") or extract_module.DEFAULT_MODEL
    if model != model_name:
        return False
    if resolve_color_source(fz) in ("auto", "auto+brush"):
        return True
    shadow_cfg = resolve_shadow_config(fz)
    return bool(shadow_cfg and shadow_cfg.get("source") == "auto")


def extract_frames_for_model(project, video_path, W, H, fps, plans, model_name, out_dir):
    """
    model_nameでの自動切り抜きを必要とするフリーズだけ、本番レンダリングの主ループ
    （iter_frames）と全く同じデコード経路でframe_bgr（出力解像度、回転補正済み）を
    取り出しPNGとして書き出す（<time:.3f>.png）。

    grab_frame_at（--preview用。-ss高速シークによる近似取得）ではなくシーケンシャル
    デコードを使うのは、本番レンダリング時にget_or_extract_alphaへ渡される
    frame_bgrとビット単位で一致させるため（Apple Visionのマスクと実際に合成される
    フレームが後からずれないようにする）。

    対象フリーズが無ければ動画を一切デコードせずに空リストを返す。対象がある場合も、
    最後に必要なフリーズのフレームまで読んだ時点でデコードを打ち切る
    （Apple VisionはmacOSランナー分のコストが高いため、無駄なデコード時間を避ける）。
    戻り値: [{"time": float, "path": "<basename>.png"}, ...]（time昇順）
    """
    if SCRIPT_DIR not in sys.path:
        sys.path.insert(0, SCRIPT_DIR)
    import extract as extract_module  # 遅延import（extract.pyのDEFAULT_MODEL参照のため）

    targets = {}  # frame_index -> time
    for fz, plan in zip(project["freezes"], plans):
        if freeze_needs_model_extraction(fz, extract_module, model_name):
            targets[plan["frame_index"]] = float(fz["time"])
    os.makedirs(out_dir, exist_ok=True)
    if not targets:
        return []

    max_index = max(targets.keys())
    written = []
    reader = open_frame_reader(video_path, W, H, fps)
    try:
        for i, frame in enumerate(iter_frames(reader, W, H)):
            if i in targets:
                t = targets[i]
                name = f"{t:.3f}.png"
                cv2.imwrite(os.path.join(out_dir, name), frame)
                written.append({"time": t, "path": name})
            if i >= max_index:
                break
    finally:
        try:
            reader.terminate()
        except Exception:  # noqa: BLE001 - 後片付けなので失敗しても無視する
            pass
        reader.stdout.close()
        reader.wait()
    written.sort(key=lambda w: w["time"])
    return written


def build_mask_context(fz, frame, W, H, cache_dir, video_path):
    """
    フリーズ1回分の「マスクの作り方」に関する下ごしらえを1回だけ計算する
    （iter_freeze_frames と render_preview の両方から使う共通処理）。
    ここで作るのはカラー化（reveal）に使うマスクの文脈で、color_source（新）/
    旧maskキーの読み替えで決まる mask_mode に基づく。影(shadow)に使うマスクは
    shadow.source が"same"以外の場合、build_shadow_mask()で別途作る。
    """
    shape = resolve_brush_shape(fz.get("brush_shape"))
    strokes = fz.get("strokes") or []
    default_width = float(fz.get("brush_width", 0.12))
    all_geo, all_total = build_stroke_geometry(strokes, W, H, default_width)

    mask_mode = resolve_color_source(fz)
    reveal = resolve_reveal(fz.get("reveal"), mask_mode)

    base_mask = None
    if mask_mode in ("auto", "auto+brush"):
        base_mask = get_or_extract_alpha(frame, video_path, float(fz.get("time", 0.0)),
                                          W, H, cache_dir, fz.get("mask_options"))
        if mask_mode == "auto+brush":
            base_mask = apply_brush_correction(base_mask, strokes, W, H, default_width, shape)

    return {
        "mask_mode": mask_mode, "reveal": reveal, "shape": shape,
        "all_geo": all_geo, "all_total": all_total, "base_mask": base_mask,
    }


def build_shadow_mask(fz, frame, W, H, cache_dir, video_path, shadow_cfg, color_done_mask):
    """
    影(shadow)の形として使うマスクを、shadow_cfg["source"]に従って作る。
      - "same"（既定）：カラー化で完成させたマスク(color_done_mask)をそのまま使う（従来どおり）
      - "brush"：色付けの方式に関係なく、このフリーズの全ストロークを完全に描いた静的マスクを使う
      - "auto"：色付けの方式に関係なく、自動切り抜き（rembg）のアルファを使う
        （キャッシュ済みでなければこのフリーズだけ切り抜きを実行する。他のフリーズが
        color_source="brush"のままなら、そちらでは切り抜きは走らない）
    """
    source = shadow_cfg.get("source", "same")
    if source == "same":
        return color_done_mask
    if source == "brush":
        strokes = fz.get("strokes") or []
        shape = resolve_brush_shape(fz.get("brush_shape"))
        default_width = float(fz.get("brush_width", 0.12))
        geo, total = build_stroke_geometry(strokes, W, H, default_width)
        if total <= 0:
            return np.zeros((H, W), np.uint8)
        return draw_stroke_mask(geo, W, H, 0.0, total, shape)
    # source == "auto"
    return get_or_extract_alpha(frame, video_path, float(fz.get("time", 0.0)),
                                 W, H, cache_dir, fz.get("mask_options"))


def mask_and_paint_at(ctx, W, H, progress):
    """
    ctx（build_mask_context の戻り値）と進捗 progress(0〜1) から、
    (現在の人物マスク uint8, 描いている最中の白い絵の具マスク uint8 or None) を返す。
    """
    mask_mode, reveal, shape = ctx["mask_mode"], ctx["reveal"], ctx["shape"]
    all_geo, all_total, base_mask = ctx["all_geo"], ctx["all_total"], ctx["base_mask"]

    if mask_mode == "brush" or reveal == "brush":
        head = all_total * float(np.clip(progress, 0.0, 1.0))
        mask = draw_stroke_mask(all_geo, W, H, 0.0, head, shape)
        if mask_mode == "auto+brush":
            # ストロークが「なぞった/なぞっていない」領域として、自動アルファの出現をゲートする
            gate = mask.astype(np.float32) / 255.0
            mask = np.clip(base_mask.astype(np.float32) * gate, 0, 255).astype(np.uint8)
        paint = None
        if progress < 1.0 and all_geo:
            max_thick = max(g["thick"] for g in all_geo)
            tail = max(2.0 * max_thick, all_total * 0.12)
            paint = draw_stroke_mask(all_geo, W, H, max(head - tail, 0.0), head, shape)
        return mask, paint

    if reveal == "none":
        # 一瞬で色が付いた状態にして、reveal_sec秒のあいだ動かず静止して見せる
        # （auto/auto+brushでreveal="none"のときのみ意味を持つ。progressは使わない）
        return base_mask, None
    if reveal == "fade":
        return apply_reveal_fade(base_mask, progress), None
    return apply_reveal_wipe(base_mask, H, progress), None


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


def resolve_title_lines(fz):
    """
    fz["name"] を、テロップ描画用の行リスト [{"text","size","underline"}, ...] に正規化する。

    - 文字列（従来形式）は1行として扱う（size=1.0倍・underline=false）。
    - {"lines": [{"text","size","underline"}, ...]} 形式にも対応する。各行のtextは
      文字列化し、sizeは指定が無ければ1.0倍、underlineは指定が無ければfalse。
    - 空でも必ず1件以上返す（textが空文字の1行）ことで、呼び出し側は常に
      「行のリスト」として扱える（分岐を減らすため）。
    """
    name = fz.get("name")
    if isinstance(name, dict):
        lines_in = name.get("lines") or []
        lines = []
        for line in lines_in:
            if isinstance(line, dict):
                text = str(line.get("text", ""))
                size = float(line.get("size", 1.0))
                underline = bool(line.get("underline", False))
            else:
                text = str(line)
                size = 1.0
                underline = False
            lines.append({"text": text, "size": max(0.05, size), "underline": underline})
        return lines or [{"text": "", "size": 1.0, "underline": False}]
    return [{"text": str(name or ""), "size": 1.0, "underline": False}]


def resolve_title_font_path(fz):
    """
    title_font / title_font_jp（全行を結合したテキストが日本語を含むかで選ぶ）から
    実パスを解決する。どちらも未指定なら font にフォールバックする（既存JSONとの後方互換）。

    フォントは render.py に同梱されたアセット（assets/fonts/配下）である前提のため、
    cwd や プロジェクトJSONの置き場所（json_dir）には依存せず、常に render.py 自身の
    ディレクトリ（SCRIPT_DIR）基準で解決する（絶対パスが指定された場合はそのまま使う）。
    GitHub Actions のジョブでは、project.json を含むジョブブランチのチェックアウトと
    spotlight-reelのクローンが別ディレクトリに存在し、render.pyの実行時cwdは前者になる
    ため、cwd基準で探すとフォントが見つからずテロップが描けない不具合があった。
    """
    combined_text = "".join(line["text"] for line in resolve_title_lines(fz))
    key = "title_font_jp" if contains_japanese(combined_text) else "title_font"
    path = fz.get(key) or fz.get("font")
    return resolve_path(path, [SCRIPT_DIR])


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


def resolve_title_align(align):
    """未知の値は center にフォールバックする（background の扱いと同じ方針）"""
    align = align or "center"
    if align not in TITLE_ALIGNS:
        warn(f"title_align='{align}' は未知の値です。center として扱います。")
        return "center"
    return align


def clamp_box_to_canvas(bbox, W, H):
    """外接矩形bbox=(x0,y0,x1,y1)が(0,0)-(W,H)に収まるよう、必要な平行移動量(dx,dy)を返す"""
    x0, y0, x1, y1 = bbox
    dx = 0.0
    if x0 < 0:
        dx = -x0
    elif x1 > W:
        dx = W - x1
    dy = 0.0
    if y0 < 0:
        dy = -y0
    elif y1 > H:
        dy = H - y1
    return dx, dy


def render_telop_layer(lines, W, H, font_cache, font_path, base_size_px, pos_ratio=(0.5, 0.78), align="center"):
    """
    テロップ（複数行対応）を1回だけ描いて (BGR画像, アルファ0〜1, 中心x, 中心y) として返す。
    フェードインは毎フレームこのアルファに係数を掛けるだけで済ませる。

    lines は resolve_title_lines() が返す形式（[{"text","size","underline"}, ...]）。
    行ごとに文字サイズ（base_size_pxに対する倍率）・アンダーラインの有無を変えられる。
    複数行はブロック全体を1つのまとまりとして扱い、pos_ratio=[x, y]（0〜1、出力サイズに
    対する比率）をブロック中心のアンカー位置とする。alignに応じてブロック全体を
    左寄せ/中央/右寄せする（各行はブロックの基準位置に揃えて配置される）。
    画面端にはみ出す場合はブロックごと自動で内側に寄せる。
    戻り値の中心x,yは実際に描画されたブロックの外接矩形の中心（バウンス演出の基準点）。

    フォントが読み込めていない（file_pathが存在しない／破損しているなど）場合は、
    OpenCVの既定フォント（日本語グリフを持たず、実質「何も描かれない」に等しい）へ
    黙ってフォールバックせず、ここで即座にエラー終了させる。過去に「テロップの
    フォントが見つからないまま全フレームで無言のうちに文字が描かれない」という
    不具合があったため、失敗は必ず気づける形にする。
    """
    visible_lines = [line for line in (lines or []) if line.get("text")]
    if not visible_lines:
        return None, None, 0, 0

    line_infos = []
    for line in visible_lines:
        size_px = max(8, int(round(base_size_px * float(line.get("size", 1.0)))))
        font_key = (font_path, size_px)
        if font_key not in font_cache:
            font_cache[font_key] = load_font(font_path, size_px)
        font = font_cache[font_key]
        if font is None:
            raise RuntimeError(
                f"テロップ用フォントを読み込めませんでした: {font_path}\n"
                f"（name={line['text']!r}）。title_font / title_font_jp / font で指定した"
                "フォントファイルが存在し、正しく読み込めるか確認してください"
                "（例: python make_dummy.py でダウンロードに失敗していないか）。"
            )
        ascent, descent = font.getmetrics()
        line_infos.append({
            "text": line["text"], "font": font, "size_px": size_px,
            "underline": bool(line.get("underline")), "ascent": ascent, "descent": descent,
        })

    align = resolve_title_align(align)
    anchor_h = {"left": "l", "center": "m", "right": "r"}[align]

    layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)

    # 行間の詰め方はシンプルに隙間なし（各行のascent+descentをそのまま積み上げる）とする。
    # ブロック幅は最も広い行の幅（alignの基準になる）。
    block_h = 0.0
    max_w = 0.0
    for li in line_infos:
        li["width"] = draw.textlength(li["text"], font=li["font"])
        max_w = max(max_w, li["width"])
        block_h += li["ascent"] + li["descent"]

    ref_x = float(pos_ratio[0]) * W if pos_ratio and len(pos_ratio) > 0 else W * 0.5
    py = float(pos_ratio[1]) * H if pos_ratio and len(pos_ratio) > 1 else H * 0.78

    if align == "left":
        block_left, block_right = ref_x, ref_x + max_w
    elif align == "right":
        block_left, block_right = ref_x - max_w, ref_x
    else:
        block_left, block_right = ref_x - max_w / 2.0, ref_x + max_w / 2.0
    block_top = py - block_h / 2.0
    block_bottom = block_top + block_h

    dx, dy = clamp_box_to_canvas((block_left, block_top, block_right, block_bottom), W, H)
    ref_x += dx
    block_top += dy
    cx = (block_left + dx + block_right + dx) / 2.0
    cy = (block_top + block_top + block_h) / 2.0

    shadow_alpha = 130   # 薄い黒ドロップシャドウ（0〜255）
    anchor = anchor_h + "a"  # 例："la"（左・アセンダー基準）。行を上から積み上げるための基準。
    cursor_y = block_top
    for li in line_infos:
        size_px = li["size_px"]
        shadow_offset = max(1, size_px // 20)
        # 白太字＋薄い黒ドロップシャドウ（先に影を描き、後から白文字を重ねる）
        draw.text((ref_x + shadow_offset, cursor_y + shadow_offset), li["text"], font=li["font"],
                  anchor=anchor, fill=(0, 0, 0, shadow_alpha))
        draw.text((ref_x, cursor_y), li["text"], font=li["font"], anchor=anchor, fill=(255, 255, 255, 255))
        if li["underline"]:
            bbox = draw.textbbox((ref_x, cursor_y), li["text"], font=li["font"], anchor=anchor)
            underline_h = max(1, round(size_px * 0.06))
            underline_gap = max(1, round(size_px * 0.04))
            uy0 = bbox[3] + underline_gap
            draw.rectangle((bbox[0], uy0, bbox[2], uy0 + underline_h), fill=(255, 255, 255, 255))
        cursor_y += li["ascent"] + li["descent"]

    rgba = np.array(layer)
    rgb = rgba[:, :, :3].astype(np.float32)
    bgr = rgb[:, :, ::-1].copy()  # PILはRGB、cv2はBGR
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


def ease_in_cubic(t):
    """Ease-In Cubic：t^3（t=0→0, t=1→1）。ゆっくり入って終盤に一気に加速する"""
    t = float(np.clip(t, 0.0, 1.0))
    return t ** 3


def logo_landing_scale(t, hold_big_sec, shrink_sec, settle_sec, scale_from, min_scale):
    """
    ロゴ着地演出（タメ→縮小→セトル）のスケールを、着地開始(t=0)からの経過秒数tで返す。
      タメ  (0〜hold_big_sec)                         : scale_fromのまま静止
      縮小  (hold_big_sec〜+shrink_sec)                : Ease-Inでscale_fromからmin_scaleへ
                                                          （ゆっくり入って終盤に加速し、
                                                          減速せずそのまま急停止する）
      セトル(hold_big_sec+shrink_sec〜+settle_sec)     : Ease-Outでmin_scaleから1.0へ戻る
    区間外（t<0 または 着地演出終了後）はクランプする。
    """
    if t < hold_big_sec:
        return scale_from
    t2 = t - hold_big_sec
    if t2 < shrink_sec:
        local = t2 / shrink_sec if shrink_sec > 1e-9 else 1.0
        return scale_from + (min_scale - scale_from) * ease_in_cubic(local)
    t3 = t2 - shrink_sec
    if settle_sec > 1e-9 and t3 < settle_sec:
        local = t3 / settle_sec
        return min_scale + (1.0 - min_scale) * ease_out_cubic(local)
    return 1.0


def logo_corner_avg_color(logo_bgr, patch_px=LOGO_CROP_CORNER_PATCH_PX):
    """
    ロゴ画像の四隅の背景色を推定してBGR色で返す（logo.background="auto"・
    自動クロップの背景色判定の両方で使う）。
    1画素だけを見ると、JPEG保存されたロゴ画像では圧縮ノイズでその1画素だけが
    たまたま外れ値になり、背景色の推定自体がズレることがあるため、各隅
    patch_px四方の小さなパッチを平均する（patch_px<=1なら従来どおり1画素）。
    """
    h, w = logo_bgr.shape[:2]
    p = max(1, min(int(patch_px), h, w))
    patches = [
        logo_bgr[0:p, 0:p],
        logo_bgr[0:p, w - p:w],
        logo_bgr[h - p:h, 0:p],
        logo_bgr[h - p:h, w - p:w],
    ]
    avg = np.concatenate([patch.reshape(-1, 3) for patch in patches], axis=0).astype(np.float32).mean(axis=0)
    return tuple(int(round(c)) for c in avg)


def detect_logo_content_bbox(bgr, alpha, margin_ratio=LOGO_CROP_MARGIN_RATIO,
                              alpha_thresh=LOGO_CROP_ALPHA_THRESH, color_thresh=LOGO_CROP_COLOR_THRESH):
    """
    ロゴ画像の「内容部分」の外接矩形(x0, y0, x1, y1)を自動検出する。
      - アルファに実際の透明部分があれば、そのアルファのしきい値で判定する
      - 無ければ（背景まで完全に不透明なPNG）、四隅の平均背景色との画素差分の
        しきい値で判定する
    外接矩形はnp.where()のmin/maxで求めるため、背景側に「内容」と誤判定された
    画素が1つでも紛れ込むと、そこまで矩形が引きずられてクロップが効かなくなる
    （JPEG圧縮ノイズ等で背景の1画素だけが色距離のしきい値を超える、という
    形で起こりうる）。そのためbool判定後にモルフォロジー・オープニング
    （収縮→膨張）をかけ、孤立したノイズ画素を先に除去してから外接矩形を求める。
    検出した外接矩形の辺の長さに対してmargin_ratio分の余白を残して広げ、
    画像範囲内に収める。内容が検出できない・画像全体を占める場合は
    クロップ不要として (0, 0, w, h) を返す。
    """
    h, w = bgr.shape[:2]
    a = alpha[:, :, 0] if alpha.ndim == 3 else alpha
    has_real_alpha = float(a.min()) < (1.0 - alpha_thresh)
    if has_real_alpha:
        content = a > alpha_thresh
    else:
        bg_color = np.array(logo_corner_avg_color(bgr), dtype=np.float32)
        diff = np.abs(bgr.astype(np.float32) - bg_color).sum(axis=2)
        content = diff > color_thresh

    kernel = np.ones((LOGO_CROP_DENOISE_KERNEL_PX, LOGO_CROP_DENOISE_KERNEL_PX), np.uint8)
    content = cv2.morphologyEx(content.astype(np.uint8), cv2.MORPH_OPEN, kernel).astype(bool)

    ys, xs = np.where(content)
    if ys.size == 0 or xs.size == 0:
        return 0, 0, w, h
    x0, x1 = int(xs.min()), int(xs.max()) + 1
    y0, y1 = int(ys.min()), int(ys.max()) + 1
    if x0 <= 0 and y0 <= 0 and x1 >= w and y1 >= h:
        return 0, 0, w, h   # 既に全面が内容 → クロップ不要

    mw = int(round((x1 - x0) * margin_ratio))
    mh = int(round((y1 - y0) * margin_ratio))
    x0 = max(0, x0 - mw)
    y0 = max(0, y0 - mh)
    x1 = min(w, x1 + mw)
    y1 = min(h, y1 + mh)
    return x0, y0, x1, y1


def crop_logo_content(logo_bgr, logo_alpha):
    """
    detect_logo_content_bbox()の結果でロゴ画像をクロップする（不要ならそのまま返す）。
    実際に効いているか（元サイズ→クロップ後サイズ）を実行ログに残す。
    """
    h, w = logo_bgr.shape[:2]
    x0, y0, x1, y1 = detect_logo_content_bbox(logo_bgr, logo_alpha)
    if (x0, y0, x1, y1) == (0, 0, w, h):
        log(f"ロゴ自動クロップ: 余白なし（{w}x{h}のまま）")
        return logo_bgr, logo_alpha
    cw, ch = x1 - x0, y1 - y0
    log(f"ロゴ自動クロップ: {w}x{h} → {cw}x{ch}（余白を検出して切り抜き）")
    return logo_bgr[y0:y1, x0:x1].copy(), logo_alpha[y0:y1, x0:x1].copy()


def save_logo_crop_preview_png(logo_bgr, logo_alpha, out_path):
    """--preview 用：自動クロップ後のロゴ単体をアルファ付きPNG(BGRA)として書き出す"""
    a = logo_alpha[:, :, 0] if logo_alpha.ndim == 3 else logo_alpha
    alpha_u8 = np.clip(a * 255.0, 0, 255).astype(np.uint8)
    bgra = np.dstack([logo_bgr, alpha_u8])
    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
    cv2.imwrite(out_path, bgra)


def resolve_logo_background_color(bg_spec, logo_bgr):
    """
    logo.background を解決する。
      - "video"                → None（背景色を敷かない。映像/静止フレームの上にそのまま重ねる）
      - "auto"（既定・未指定）  → logo_corner_avg_color() の色
      - "#RRGGBB"               → その色
      - 不明な値                → 警告のうえ "auto" として扱う
    戻り値: BGRのタプル、または背景色を敷かない場合は None
    """
    spec = bg_spec or DEFAULT_LOGO_BACKGROUND
    if spec == "video":
        return None
    if spec == "auto":
        return logo_corner_avg_color(logo_bgr)
    if isinstance(spec, str) and spec.startswith("#") and len(spec) == 7:
        s = spec.lstrip("#")
        try:
            r, g, b = int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)
            return (b, g, r)
        except ValueError:
            pass
    warn(f"logo.background='{spec}' は未知の値です。'{DEFAULT_LOGO_BACKGROUND}' として扱います。")
    return logo_corner_avg_color(logo_bgr)


def resolve_logo_params(logo_cfg):
    """
    logo{} 配下のアニメーション上書きパラメータ（未指定なら既定値）をまとめて返す。
    scale_from（着地演出「タメ」区間のスケール）は、start_width_ratio（画面幅比、
    logo.start_width_ratio。既定LOGO_START_WIDTH_RATIO_DEFAULT）をwidth_ratio
    （着地後の画面幅比）で割って求める。こうすることで、width_ratioをJSON側で
    変えても、開始時サイズは常に「画面幅のstart_width_ratio比率」から揃う。
    """
    width_ratio = max(0.01, float(logo_cfg.get("width_ratio", LOGO_WIDTH_RATIO_DEFAULT)))
    start_width_ratio = max(1.0, float(logo_cfg.get("start_width_ratio", LOGO_START_WIDTH_RATIO_DEFAULT)))
    scale_from = start_width_ratio / width_ratio if width_ratio > 1e-6 else start_width_ratio
    return {
        "scale_from": scale_from,
        "min_scale": 1.0 - LOGO_LANDING_DIP_DEFAULT,
        "hold_big_sec": max(0.0, float(logo_cfg.get("hold_big_sec", LOGO_HOLD_BIG_SEC_DEFAULT))),
        "shrink_sec": max(1e-3, float(logo_cfg.get("shrink_sec", LOGO_SHRINK_SEC_DEFAULT))),
        "settle_sec": max(0.0, float(logo_cfg.get("settle_sec", LOGO_SETTLE_SEC_DEFAULT))),
        "sweep_start_sec": max(0.0, float(logo_cfg.get("sweep_start_sec", LOGO_SWEEP_START_SEC_DEFAULT))),
        "sweep_sec": max(1e-3, float(logo_cfg.get("sweep_sec", LOGO_SWEEP_SEC_DEFAULT))),
        "flash_strength": float(np.clip(logo_cfg.get("flash_strength", LOGO_FLASH_STRENGTH_DEFAULT), 0.0, 1.0)),
        "shake_sec": max(0.0, float(logo_cfg.get("shake_sec", LOGO_SHAKE_SEC_DEFAULT))),
        "shake_amplitude": max(0.0, float(logo_cfg.get("shake_amplitude", LOGO_SHAKE_AMPLITUDE_RATIO_DEFAULT))),
        "duration_sec": max(0.0, float(logo_cfg.get("duration_sec", LOGO_DURATION_SEC_DEFAULT))),
        "fade_sec": max(0.0, float(logo_cfg.get("fade_sec", LOGO_FADE_TO_BG_SEC_DEFAULT))),
        "sfx_tail": bool(logo_cfg.get("sfx_tail", True)),
        "width_ratio": width_ratio,
        "start_width_ratio": start_width_ratio,
    }


def logo_landing_total_sec(params):
    """着地演出全体（タメ+縮小+セトル）の長さ（秒）"""
    return params["hold_big_sec"] + params["shrink_sec"] + params["settle_sec"]


def logo_landing_instant_sec(params):
    """
    「着地の瞬間」＝ロゴが最小サイズに到達する時刻（タメ+縮小の終わり、着地開始からの秒数）。
    SE・白フラッシュ・画面の揺れ・logo.sfxのpeak_at_landingは、すべてこの瞬間に揃える。
    """
    return params["hold_big_sec"] + params["shrink_sec"]


def logo_blackout_frames_for(fps):
    """直前のシーンから暗転してカットするまでのフレーム数（LOGO_CUT_BLACKOUT_SEC・固定）"""
    return max(1, int(round(LOGO_CUT_BLACKOUT_SEC * fps)))


def logo_total_frames_for(logo_params, fps):
    """着地開始から終了までの合計フレーム数（＝着地演出(タメ+縮小+セトル) + duration_sec 分）"""
    return max(1, int(round((logo_landing_total_sec(logo_params) + logo_params["duration_sec"]) * fps)))


def build_logo_luminance_mask(logo_bgr, logo_alpha):
    """
    ロゴ画像の輝度(0〜1)×アルファをマスクとして返す（(H,W,1)）。
    光彩スイープを「ロゴの明るい部分だけ」に乗せるために使う。黒背景の不透明PNGでは
    アルファが全面ほぼ1で役に立たないため、主な判定材料は輝度にする。
    """
    gray = cv2.cvtColor(logo_bgr, cv2.COLOR_BGR2GRAY).astype(np.float32) / 255.0
    a = logo_alpha[:, :, 0] if logo_alpha.ndim == 3 else logo_alpha
    return (gray * a)[:, :, None]


def sweep_highlight_layer(luminance_mask, sweep_t, width_ratio=LOGO_SWEEP_WIDTH_RATIO):
    """
    sweep_t(0〜1)における、ロゴの左上→右下に走る45度の白い帯の強度マップ(0〜1、(H,W,1))を返す。
    透明→白→透明の線形グラデーションで、帯の幅はロゴ幅の width_ratio。
    luminance_mask を掛けることで、ロゴの明るい部分だけに乗るようにする。
    """
    h, w = luminance_mask.shape[:2]
    ys, xs = np.mgrid[0:h, 0:w].astype(np.float32)
    diag = (xs + ys) / math.sqrt(2.0)                       # 左上=0の対角線上の距離(px)
    diag_max = ((w - 1) + (h - 1)) / math.sqrt(2.0)
    band = max(1.0, width_ratio * w)
    center = -band / 2.0 + float(np.clip(sweep_t, 0.0, 1.0)) * (diag_max + band)
    dist = np.abs(diag - center)
    intensity = np.clip(1.0 - dist / (band / 2.0), 0.0, 1.0)
    return (intensity[:, :, None] * luminance_mask)


def screen_blend_white(img_f32, amount):
    """
    img_f32(float32, 0〜255)に、白色を amount の強さでscreen合成した結果を返す。
    amount はスカラー（フラッシュ用）・(H,W,1)配列（スイープの強度マップ用）のどちらでもよい。
    """
    return img_f32 + amount * (255.0 - img_f32)


def composite_logo(frame, logo_bgr, logo_alpha, W, H, scale, opacity=1.0, width_ratio=LOGO_WIDTH_RATIO_DEFAULT):
    """
    frame の中央に、ロゴを「基準表示幅（width_ratio×W）× scale」のサイズで合成する。
    scale=1.0 が最終的な表示サイズ。opacity(0〜1)はロゴ全体の不透明度（着地時のフェードイン用）。
    width_ratio は logo.width_ratio（既定LOGO_WIDTH_RATIO_DEFAULT）で上書きできる。
    """
    if logo_bgr is None or opacity <= 0:
        return frame
    lh, lw = logo_bgr.shape[:2]
    if lw <= 0 or lh <= 0:
        return frame

    base_scale = (W * width_ratio) / lw
    final_scale = max(0.01, base_scale * scale)
    new_w = max(1, int(round(lw * final_scale)))
    new_h = max(1, int(round(lh * final_scale)))
    interp = cv2.INTER_AREA if final_scale < 1.0 else cv2.INTER_LINEAR
    resized_bgr = cv2.resize(logo_bgr, (new_w, new_h), interpolation=interp).astype(np.float32)
    resized_alpha = cv2.resize(logo_alpha[:, :, 0], (new_w, new_h), interpolation=interp)[:, :, None]
    resized_alpha = resized_alpha * float(np.clip(opacity, 0.0, 1.0))

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


def logo_animation_state(elapsed_sec, params):
    """
    着地開始(t=0、スタンバイ状態)からの経過秒数 elapsed_sec における、ロゴ演出の状態を返す。
      scale       : ロゴの表示スケール
      opacity     : ロゴ自体の不透明度（常に1.0。フェードインはしない）
      flash_amt   : 画面全体に乗せる白フラッシュの強さ(0〜1)
      shake_dx/dy : 画面全体のわずかな揺れ（出力幅に対する比率）
      sweep_t     : 光彩スイープの進行度(0〜1)。スイープ区間外は None
      fade_amt    : 画面全体を背景色へ暗転させる強さ(0〜1)
    タイムライン（既定値の場合）:
      0.00-0.15  タメ：start_width_ratioのまま静止（不透明度は最初から1.0）
      0.15-0.65  縮小：Ease-Inで最小サイズ（width_ratio×98%）まで縮む。ゆっくり入って
                 終盤に加速し、減速せずそのまま急停止する
      0.65       着地の瞬間（最小サイズに到達）：SE・白フラッシュ・画面の揺れが発火
      0.65-0.80  セトル：Ease-Outで最小サイズから最終サイズ(width_ratio)へ戻る
      1.15-1.85  光彩スイープ（着地演出完了から0.35秒後に開始、0.70秒かける）
      1.85-終了  ゆっくり103%まで拡大、最後の0.4秒で背景色へ暗転
    """
    hold_big_sec = params["hold_big_sec"]
    shrink_sec = params["shrink_sec"]
    settle_sec = params["settle_sec"]
    landing = logo_landing_total_sec(params)          # 着地演出全体（タメ+縮小+セトル）の長さ
    landing_instant = logo_landing_instant_sec(params)  # 最小サイズに到達する瞬間（SE等の起点）
    flash_sec = LOGO_FLASH_SEC
    shake_sec = params["shake_sec"]
    shake_amplitude = params["shake_amplitude"]
    sweep_sec = params["sweep_sec"]
    duration_sec = params["duration_sec"]
    scale_from = params["scale_from"]
    min_scale = params["min_scale"]
    fade_sec = params["fade_sec"]

    seg_end = landing + duration_sec
    sweep_start = landing + params["sweep_start_sec"]
    sweep_end = sweep_start + sweep_sec
    hold_start = sweep_end
    fade_start = max(hold_start, seg_end - fade_sec)

    t = float(np.clip(elapsed_sec, 0.0, seg_end))

    if t < landing:
        scale = logo_landing_scale(t, hold_big_sec, shrink_sec, settle_sec, scale_from, min_scale)
    elif t < hold_start:
        scale = 1.0
    else:
        grow_span = max(seg_end - hold_start, 1e-6)
        gp = float(np.clip((t - hold_start) / grow_span, 0.0, 1.0))
        scale = 1.0 + (LOGO_GROW_TO - 1.0) * gp
    opacity = 1.0  # 開始時からフェードインなし（要件どおり常に完全不透明）

    if landing_instant <= t < landing_instant + flash_sec:
        flash_amt = params["flash_strength"] * (1.0 - (t - landing_instant) / flash_sec)
    else:
        flash_amt = 0.0

    if shake_sec > 0 and landing_instant <= t < landing_instant + shake_sec:
        shake_t = (t - landing_instant) / shake_sec
        decay = 1.0 - shake_t  # 線形減衰（急停止ではなく徐々に収まる）
        shake_dx = shake_amplitude * math.sin(shake_t * 2.0 * math.pi * 3.0) * decay
        shake_dy = shake_amplitude * math.sin(shake_t * 2.0 * math.pi * 3.0 + math.pi / 2.0) * decay * 0.6
    else:
        shake_dx, shake_dy = 0.0, 0.0

    if sweep_start <= t < sweep_end:
        sweep_t = (t - sweep_start) / sweep_sec
    else:
        sweep_t = None

    if t >= fade_start:
        fade_amt = float(np.clip((t - fade_start) / max(seg_end - fade_start, 1e-6), 0.0, 1.0))
    else:
        fade_amt = 0.0

    return {
        "scale": scale, "opacity": opacity, "flash_amt": flash_amt,
        "shake_dx": shake_dx, "shake_dy": shake_dy,
        "sweep_t": sweep_t, "fade_amt": fade_amt,
    }


def shake_translate(img, dx, dy):
    """
    画面シェイク用のごくわずかな平行移動。translate_mask（影演出用）とは異なり、
    はみ出た縁を黒で埋めると視認できてしまうため、縁を伸ばして埋める
    （cv2.BORDER_REPLICATE）。
    """
    if abs(dx) < 0.05 and abs(dy) < 0.05:
        return img
    H, W = img.shape[:2]
    M = np.float32([[1, 0, dx], [0, 1, dy]])
    return cv2.warpAffine(img, M, (W, H), flags=cv2.INTER_LINEAR,
                           borderMode=cv2.BORDER_REPLICATE)


def render_logo_frame(backdrop, logo_bgr, logo_alpha, logo_luma, W, H, elapsed_sec, params, bg_color):
    """
    着地開始からの経過秒数 elapsed_sec における1フレームを作る
    （スタンバイ→着地＋画面の揺れ→白フラッシュ→光彩スイープ→ゆっくり拡大→背景色へ暗転）。
    backdrop: ロゴの後ろに敷く画面（背景色の単色フレーム、または映像/静止フレーム）。
    """
    if logo_bgr is None:
        return backdrop

    state = logo_animation_state(elapsed_sec, params)

    logo_layer = logo_bgr.astype(np.float32)
    if state["sweep_t"] is not None:
        strength = sweep_highlight_layer(logo_luma, state["sweep_t"])
        logo_layer = screen_blend_white(logo_layer, strength)
    logo_layer = np.clip(logo_layer, 0, 255).astype(np.uint8)

    frame = composite_logo(backdrop, logo_layer, logo_alpha, W, H, state["scale"], opacity=state["opacity"],
                            width_ratio=params.get("width_ratio", LOGO_WIDTH_RATIO_DEFAULT))

    if state["shake_dx"] != 0.0 or state["shake_dy"] != 0.0:
        frame = shake_translate(frame, state["shake_dx"] * W, state["shake_dy"] * W)

    if state["flash_amt"] > 0:
        f = screen_blend_white(frame.astype(np.float32), state["flash_amt"])
        frame = np.clip(f, 0, 255).astype(np.uint8)

    if state["fade_amt"] > 0:
        target = np.array(bg_color if bg_color is not None else (0, 0, 0), dtype=np.float32)
        f = frame.astype(np.float32)
        f = f * (1.0 - state["fade_amt"]) + target * state["fade_amt"]
        frame = np.clip(f, 0, 255).astype(np.uint8)

    return frame


# ---------------------------------------------------------------------------
# フリーズ区間の設計（映像と音声で同じ数値を使うため一箇所で計算する）
# ---------------------------------------------------------------------------

def resolve_reveal_sec(fz, brush_driven):
    """
    ①塗り（reveal）にかける秒数を解決する。優先順位:
      1. reveal_sec（新キー）が明示されていればそれを使う
      2. brush駆動（mask_mode="brush"またはreveal="brush"）の場合のみ、
         旧 brush_anim_sec が明示されていればそれを使う（後方互換）
      3. REVEAL_SEC_DEFAULT（既定0.5秒）
    """
    if "reveal_sec" in fz:
        return max(0.0, float(fz["reveal_sec"]))
    if brush_driven and "brush_anim_sec" in fz:
        return max(0.0, float(fz["brush_anim_sec"]))
    return REVEAL_SEC_DEFAULT


def resolve_hold_sec(fz):
    """
    ③静止（hold）にかける秒数を解決する。優先順位:
      1. hold_sec（新キー）が明示されていればそれを使う
      2. 旧 freeze_sec が明示されていればそれを読み替える（freeze_secを改名したもの。
         旧JSONでは「フリーズ全体の長さ」だったが、新JSONでは「③静止の長さ」を表す）
      3. HOLD_SEC_DEFAULT（既定2.0秒）
    """
    if "hold_sec" in fz:
        return max(0.0, float(fz["hold_sec"]))
    if "freeze_sec" in fz:
        return max(0.0, float(fz["freeze_sec"]))
    return HOLD_SEC_DEFAULT


def resolve_sfx_spec(sfx_value, json_dir, valid_aligns, context_label):
    """
    freeze.sfx / logo.sfx を解決する。戻り値は {"path", "align", "is_custom"} の辞書、
    または指定が無い/解決できない場合は None。
      - 文字列: 従来どおりのプリセット名（render.py同梱の assets/sfx/<name>.wav）。
        フォント（resolve_title_font_path）と同じ理由でSCRIPT_DIR基準のみで解決する。
        align は常に既定（start_at_landing）。
      - {"file": "...", "align": "..."} オブジェクト: ユーザー提供の音声ファイル
        （mp3/wav、ffmpeg decode_audioが対応する形式ならどちらでも可）。
        file はロゴ画像（logo.image）と同じ方針で cwd→JSONの場所→SCRIPT_DIR の順に解決する
        （job実行時はproject.jsonと同じ場所にコミットされている前提）。
        align は valid_aligns のいずれか。未指定・不明な値は SFX_ALIGN_DEFAULT にフォールバックする。
    """
    if not sfx_value:
        return None
    if isinstance(sfx_value, str):
        cand = os.path.join("assets", "sfx", f"{sfx_value}.wav")
        path = resolve_path(cand, [SCRIPT_DIR])
        if not os.path.exists(path):
            warn(f"効果音が見つかりません（{context_label}）: {cand}")
            return None
        return {"path": path, "align": SFX_ALIGN_DEFAULT, "is_custom": False}
    if isinstance(sfx_value, dict):
        file_rel = sfx_value.get("file")
        if not file_rel:
            warn(f"sfx オブジェクトに file が指定されていません（{context_label}）。無視します。")
            return None
        path = resolve_path(file_rel, [os.getcwd(), json_dir, SCRIPT_DIR])
        if not os.path.exists(path):
            warn(f"効果音ファイルが見つかりません（{context_label}）: {file_rel}")
            return None
        align = sfx_value.get("align") or SFX_ALIGN_DEFAULT
        if align not in valid_aligns:
            warn(f"sfx.align='{align}' は未知の値です（{context_label}）。"
                 f"{SFX_ALIGN_DEFAULT} として扱います。")
            align = SFX_ALIGN_DEFAULT
        log(f"  効果音ファイルを解決しました（{context_label}）: {file_rel} (align={align})")
        return {"path": path, "align": align, "is_custom": True}
    warn(f"sfx の値が不正です（{context_label}）。文字列またはオブジェクトを指定してください: {sfx_value!r}")
    return None


def plan_freezes(freezes, fps, src_frames, json_dir, logo=None, logo_at=None,
                  logo_total_frames=0, logo_blackout_frames=0):
    """
    各フリーズについて、挿入位置（フレーム番号）と各フェーズのフレーム数を決める。
    フェーズ順: pre(出現前の静止・固定0.3秒) → reveal(①塗り) →
      slide_in(②ズレ。影スライドイン、shadow有効時のみ) → hold(③静止。テロップ・ロゴ) →
      slide_back(影を隠して元位置へ。shadow有効時のみ、ただしこのフリーズでロゴを
      表示する場合は戻す意味が無いため行わない)。
    reveal_sec/slide_sec/hold_secはそれぞれ独立に指定できる秒数で、
    （旧freeze_secのような）全体予算による比例縮小は行わない。
    logo_at=='last_freeze' の場合、時刻が一番遅いフリーズの③静止（hold）フェーズを、
    静止フレームからの暗転経由のカットつなぎ分(logo_blackout_frames) ＋
    ロゴの着地〜表示終了分(logo_total_frames) を確保できるよう延長する。
    """
    plans = []
    for fz in freezes:
        idx = int(round(float(fz["time"]) * fps))
        if src_frames > 0:
            idx = min(idx, max(src_frames - 1, 0))
        idx = max(idx, 0)

        mask_mode = resolve_color_source(fz)
        reveal = resolve_reveal(fz.get("reveal"), mask_mode)
        brush_driven = mask_mode == "brush" or reveal == "brush"
        reveal_anim_sec = resolve_reveal_sec(fz, brush_driven)

        shadow_cfg = resolve_shadow_config(fz)
        hold_sec = resolve_hold_sec(fz)

        n_pre = int(round(HOLD_BEFORE_BRUSH_SEC * fps))
        n_reveal = max(1, int(round(reveal_anim_sec * fps)))
        n_slide_in = int(round(shadow_cfg["slide_sec"] * fps)) if shadow_cfg else 0
        n_hold = max(0, int(round(hold_sec * fps)))
        n_slide_back = int(round(SHADOW_SLIDE_BACK_SEC * fps)) if shadow_cfg else 0

        sfx_spec = resolve_sfx_spec(fz.get("sfx"), json_dir, SFX_ALIGNS_FREEZE,
                                     f"t={fz['time']:.2f}s")

        plans.append({
            "fz": fz,
            "frame_index": idx,
            "n_pre": n_pre,
            "n_reveal": n_reveal,
            "n_slide_in": n_slide_in,
            "n_hold": n_hold,
            "n_slide_back": n_slide_back,
            "n_total": n_pre + n_reveal + n_slide_in + n_hold + n_slide_back,
            "sfx": sfx_spec,
            "show_logo": False,
            "logo_blackout_frames": 0,
            "logo_total_frames": 0,
            "mask_mode": mask_mode,
            "reveal": reveal,
            "shadow_cfg": shadow_cfg,
        })
    plans.sort(key=lambda p: p["frame_index"])

    if logo and logo_at == "last_freeze" and plans:
        last = plans[-1]
        last["show_logo"] = True
        last["logo_blackout_frames"] = logo_blackout_frames
        last["logo_total_frames"] = logo_total_frames
        # このフリーズはロゴへ移るため、影を隠す元位置へのスライドバックは不要
        # （n_totalは変えず、reclaimしたぶんはholdへ回す）
        if last["n_slide_back"] > 0:
            last["n_hold"] += last["n_slide_back"]
            last["n_slide_back"] = 0
        need_hold = max(1, logo_blackout_frames + logo_total_frames)
        if last["n_hold"] < need_hold:
            extra = need_hold - last["n_hold"]
            last["n_hold"] += extra
            last["n_total"] += extra

    return plans


def iter_freeze_frames(frame, plan, W, H, fps, font_cache, cache_dir=None, video_path=None,
                        logo_bgr=None, logo_alpha=None, logo_luma=None,
                        logo_params=None, logo_bg_color=None):
    """
    1回分のフリーズ区間のフレームを順に生成する（メモリに溜めない）。
      1. 静止（背景処理のみ、固定0.3秒）
      2. ①塗り(reveal)：人物が出現する（マスクの作り方はcolor_source="brush"/"auto"/
         旧mask="auto+brush"で異なるが、出現アニメ自体はreveal="wipe"/"fade"/"brush"/"none"で
         統一的に扱う。人物は「元の位置」に出現するため、この間は影（あれば）が
         人物の真下に完全に隠れて見えない）
      3. ②ズレ(slide)：影演出のスライドイン（shadowがあれば）：人物レイヤーだけを
         0→distanceまでEase-Out Cubicでずらし、元の位置に静止したままの影
         （shadow.sourceに応じたマスク）を覗かせる。着地の瞬間にテロップ（バウンス可）と
         効果音が発火する
      4. ③静止(hold)（plan["show_logo"]がTrueなら、終盤で「静止フレーム→背景色の
         クロスフェード（logo.backgroundが背景色を敷くモードの場合のみ）→ロゴの着地〜表示」
         を行う）。テロップは着地の瞬間に出現し、この③静止の終わりまで表示し続ける
      5. 影演出のスライドバック（shadowがあり、かつこのフリーズでロゴを表示しない場合）：
         通常再生に戻る直前に人物を元の位置へ戻し、影を隠す（戻さないと再生再開時に
         人物が飛んで見えるため）
    """
    fz = plan["fz"]
    bg = make_background(frame, fz.get("background", "mono"), float(fz.get("mono_contrast", 1.0)))
    shadow_cfg = plan.get("shadow_cfg")

    cache_dir = cache_dir or os.path.join(os.getcwd(), CACHE_DIR_NAME)
    video_path = video_path or "input"
    ctx = build_mask_context(fz, frame, W, H, cache_dir, video_path)

    title_size = float(fz.get("title_size", DEFAULT_STYLE["title_size"]))
    size_px = max(8, int(round(H * title_size)))
    font_path = resolve_title_font_path(fz)
    title_pos = fz.get("title_pos") or DEFAULT_STYLE["title_pos"]
    title_align = fz.get("title_align", DEFAULT_STYLE["title_align"])
    telop_bgr, telop_alpha, tcx, tcy = render_telop_layer(
        resolve_title_lines(fz), W, H, font_cache, font_path, size_px, title_pos, title_align)
    bounce = bool(fz.get("title_bounce"))

    # 1) 出現アニメ開始まで静止
    for _ in range(plan["n_pre"]):
        yield bg.copy()

    # 2) ①塗り(reveal)：人物の出現（ブラシが伸びる／自動マスクをワイプ・フェード／
    #    reveal="none"なら即座に全体表示）。元の位置のまま＝影は隠れている
    for i in range(plan["n_reveal"]):
        progress = (i + 1) / float(plan["n_reveal"])
        mask, paint = mask_and_paint_at(ctx, W, H, progress)
        yield composite_layers(bg, frame, mask, W, H, shadow_cfg=shadow_cfg, paint_mask_u8=paint)

    done_mask, _done_paint = mask_and_paint_at(ctx, W, H, 1.0)
    shadow_done_mask = None
    if shadow_cfg:
        shadow_done_mask = build_shadow_mask(fz, frame, W, H, cache_dir, video_path, shadow_cfg, done_mask)

    # Actionsのログで各フリーズの影の状態を確認できるようにする（実機で影が出ない場合の
    # 切り分け用：有効/無効・実際に使われる方向・影マスクのX重心を毎回出力する）
    freeze_label_text = " / ".join(line["text"] for line in resolve_title_lines(fz) if line["text"])
    freeze_label = f"t={float(fz.get('time', 0.0)):.2f}s 「{freeze_label_text}」"
    if shadow_cfg:
        mask_x_ratio = mask_x_center_ratio(shadow_done_mask, W)
        shadow_direction = shadow_cfg.get("direction", "auto")
        if shadow_direction not in ("left", "right"):
            shadow_direction = resolve_shadow_auto_direction(shadow_done_mask, W)
        log(f"  フリーズ {freeze_label}: 影=有効 方向={shadow_direction} "
            f"マスクX中心={mask_x_ratio:.2f} source={shadow_cfg.get('source', 'same')}")
    else:
        mask_x_ratio = mask_x_center_ratio(done_mask, W)
        log(f"  フリーズ {freeze_label}: 影=無効 マスクX中心={mask_x_ratio:.2f}")

    # 3) ②ズレ(slide)：影演出のスライドイン：Ease-Out Cubicで着地先ベクトルまでずらし、影を覗かせる
    slide_dx, slide_dy = 0.0, 0.0
    if shadow_cfg and plan["n_slide_in"] > 0:
        slide_dx, slide_dy = compute_shadow_slide_vector(shadow_done_mask, W, shadow_cfg)
        for i in range(plan["n_slide_in"]):
            t = (i + 1) / float(plan["n_slide_in"])
            eased = ease_out_cubic(t)
            yield composite_layers(bg, frame, done_mask, W, H, shadow_cfg=shadow_cfg,
                                    slide_dx=slide_dx * eased, slide_dy=slide_dy * eased,
                                    shadow_mask_u8=shadow_done_mask)

    # 「着地」した状態（スライド済み。影が無ければ元の位置のまま）を1回だけ作って使い回す
    landed = composite_layers(bg, frame, done_mask, W, H, shadow_cfg=shadow_cfg,
                               slide_dx=slide_dx, slide_dy=slide_dy, shadow_mask_u8=shadow_done_mask)
    fade_frames = max(1, int(round(TELOP_FADE_SEC * fps)))

    # ブラシ（またはauto+brushのreveal="brush"）で描いた場合のみ、完了時点で先端に残っている
    # 「乾いていない白い絵の具」を brush_fade_sec 秒かけてフェードアウトさせる。
    # auto/auto+brush(wipe/fade)にはこの「絵の具」の概念自体が無いため対象外。
    # 0（既定は0.3）を指定すると、この後続フェードを行わず従来どおり即座に消える。
    brush_driven = ctx["mask_mode"] == "brush" or ctx["reveal"] == "brush"
    all_geo, all_total = ctx["all_geo"], ctx["all_total"]
    shape = ctx["shape"]
    brush_fade_sec = float(fz.get("brush_fade_sec", DEFAULT_STYLE["brush_fade_sec"]))
    n_brush_fade = max(0, int(round(brush_fade_sec * fps))) if brush_fade_sec > 0 else 0
    tail_paint_alpha = None
    if brush_driven and n_brush_fade > 0 and all_geo and all_total > 0:
        max_thick = max(g["thick"] for g in all_geo)
        tail_len = max(2.0 * max_thick, all_total * 0.12)
        tail_mask = draw_stroke_mask(all_geo, W, H, max(all_total - tail_len, 0.0), all_total, shape)
        tail_paint_alpha = (tail_mask.astype(np.float32) / 255.0)[:, :, None]

    blackout_frames = plan.get("logo_blackout_frames", 0)
    logo_seg_frames = plan.get("logo_total_frames", 0)
    logo_start_in_hold = plan["n_hold"] - blackout_frames - logo_seg_frames
    solid_bg_frame = None
    if plan["show_logo"] and logo_bg_color is not None:
        solid_bg_frame = np.full((H, W, 3), logo_bg_color, dtype=np.uint8)

    # 4) ③静止(hold)。着地の瞬間（=このhold区間の最初のフレーム）にテロップのフェードイン開始し、
    #    hold区間の終わりまで表示し続ける（hold_secに完全に連動）
    for i in range(plan["n_hold"]):
        if tail_paint_alpha is not None and i < n_brush_fade:
            fade_out_t = (i + 1) / float(n_brush_fade)
            pm = tail_paint_alpha * (BRUSH_PAINT_ALPHA * (1.0 - fade_out_t))
            base = np.clip(landed.astype(np.float32) * (1.0 - pm) + 255.0 * pm, 0, 255).astype(np.uint8)
        else:
            base = landed

        t = (i + 1) / float(fade_frames)
        fade = min(1.0, t)
        if bounce and t < 1.0:
            scale = telop_bounce_scale(t)
            f_bgr, f_alpha = scale_telop_layer(telop_bgr, telop_alpha, scale, tcx, tcy) \
                if telop_bgr is not None else (None, None)
        else:
            f_bgr, f_alpha = telop_bgr, telop_alpha
        out = blend_telop(base, f_bgr, f_alpha, fade)

        if plan["show_logo"]:
            rel = i - logo_start_in_hold
            if rel >= 0:
                if blackout_frames > 0 and rel < blackout_frames:
                    # 直前のシーン（テロップ込みの静止フレーム）から0.1秒（LOGO_CUT_BLACKOUT_SEC）
                    # かけて暗転し、そこでロゴ演出へカットする（クロスフェードではない）
                    bt = (rel + 1) / float(blackout_frames)
                    outf = out.astype(np.float32) * (1.0 - bt)
                    out = np.clip(outf, 0, 255).astype(np.uint8)
                else:
                    logo_elapsed = (rel - blackout_frames) / float(fps)
                    backdrop = solid_bg_frame if solid_bg_frame is not None else out
                    out = render_logo_frame(backdrop, logo_bgr, logo_alpha, logo_luma,
                                             W, H, logo_elapsed, logo_params, logo_bg_color)
        yield out

    # 5) 影演出のスライドバック：通常再生に戻る直前に人物を元位置へ戻し、影を隠す
    if shadow_cfg and plan["n_slide_back"] > 0:
        for i in range(plan["n_slide_back"]):
            t = (i + 1) / float(plan["n_slide_back"])
            remaining = 1.0 - ease_out_cubic(t)
            out = composite_layers(bg, frame, done_mask, W, H, shadow_cfg=shadow_cfg,
                                    slide_dx=slide_dx * remaining, slide_dy=slide_dy * remaining,
                                    shadow_mask_u8=shadow_done_mask)
            out = blend_telop(out, telop_bgr, telop_alpha, 1.0)
            yield out


def render_preview(frame, plan, W, H, fps, font_cache, out_png, cache_dir=None, video_path=None):
    """
    --preview 用：影演出の「スライド前（影が隠れている）」「スライド後（影が見えている）」の
    2枚をPNGで出す（out_pngのパスから _before / _after のファイル名を導く）。
    shadowが無効な場合は、2枚とも同じ内容になる。
    戻り値: (スライド前のパス, スライド後のパス)
    """
    fz = plan["fz"]
    bg = make_background(frame, fz.get("background", "mono"), float(fz.get("mono_contrast", 1.0)))
    shadow_cfg = resolve_shadow_config(fz)

    cache_dir = cache_dir or os.path.join(os.getcwd(), CACHE_DIR_NAME)
    video_path = video_path or "input"
    ctx = build_mask_context(fz, frame, W, H, cache_dir, video_path)
    mask, _paint = mask_and_paint_at(ctx, W, H, 1.0)

    slide_dx, slide_dy = 0.0, 0.0
    shadow_mask = None
    if shadow_cfg:
        shadow_mask = build_shadow_mask(fz, frame, W, H, cache_dir, video_path, shadow_cfg, mask)
        slide_dx, slide_dy = compute_shadow_slide_vector(shadow_mask, W, shadow_cfg)

    before_img = composite_layers(bg, frame, mask, W, H, shadow_cfg=shadow_cfg, shadow_mask_u8=shadow_mask)
    after_img = composite_layers(bg, frame, mask, W, H, shadow_cfg=shadow_cfg,
                                  slide_dx=slide_dx, slide_dy=slide_dy, shadow_mask_u8=shadow_mask)

    title_size = float(fz.get("title_size", DEFAULT_STYLE["title_size"]))
    size_px = max(8, int(round(H * title_size)))
    font_path = resolve_title_font_path(fz)
    title_pos = fz.get("title_pos") or DEFAULT_STYLE["title_pos"]
    title_align = fz.get("title_align", DEFAULT_STYLE["title_align"])
    telop_bgr, telop_alpha, _tcx, _tcy = render_telop_layer(
        resolve_title_lines(fz), W, H, font_cache, font_path, size_px, title_pos, title_align)
    before_img = blend_telop(before_img, telop_bgr, telop_alpha, 1.0)
    after_img = blend_telop(after_img, telop_bgr, telop_alpha, 1.0)

    base, ext = os.path.splitext(out_png)
    ext = ext or ".png"
    before_path = f"{base}_before{ext}"
    after_path = f"{base}_after{ext}"
    os.makedirs(os.path.dirname(os.path.abspath(out_png)) or ".", exist_ok=True)
    cv2.imwrite(before_path, before_img)
    cv2.imwrite(after_path, after_img)
    return before_path, after_path


# ---------------------------------------------------------------------------
# 音声処理（numpyで切り貼り・ミックス）
# ---------------------------------------------------------------------------

def decode_audio(path, sr=AUDIO_SR, ch=AUDIO_CH):
    """
    ffmpeg で任意の音声を float32 の (N, ch) 配列にデコードする。
    音声が無い / 読めない場合は長さ0の配列を返す。

    aresample=async=1 を指定し、コンテナ内の音声パケットのタイムスタンプに
    小さな不連続（実機のスクリーン録画などで音声バッファが一瞬詰まった場合に
    起きうる）があっても、ffmpeg側でサンプルを補間・伸縮して滑らかに追従させる。
    指定しない場合、パケットをそのまま連結するだけになり、そうした不連続が
    ブツ切れ（無音の隙間）としてそのまま出力に残ってしまうことがある。
    """
    ffmpeg = find_exe("ffmpeg")
    cmd = [ffmpeg, "-v", "error", "-i", path, "-vn",
           "-af", "aresample=async=1:min_hard_comp=0.100000:first_pts=0",
           "-f", "f32le", "-acodec", "pcm_f32le",
           "-ac", str(ch), "-ar", str(sr), "pipe:1"]
    res = subprocess.run(cmd, capture_output=True)
    if res.returncode != 0 or not res.stdout:
        return np.zeros((0, ch), np.float32)
    data = np.frombuffer(res.stdout, np.float32)
    usable = (len(data) // ch) * ch
    return data[:usable].reshape(-1, ch).copy()


def frames_to_samples(n_frames, fps, sr):
    """
    映像のフレーム数を、fps・サンプルレートに応じた音声サンプル数（整数）に変換する。
    build_audio内の全てのフレーム→サンプル変換はこの関数だけを通すことで、
    呼び出し箇所ごとに計算式や丸め方がバラバラになって境界がズレる（＝結果的に
    サンプルが重複／欠落して隙間やクリックノイズになる）事態を防ぐ。
    """
    return int(round(float(n_frames) * sr / float(fps)))


def mix_into(buf, snippet, start):
    """buf の start サンプル目に snippet を加算ミックスする（はみ出しは切る）"""
    if snippet.size == 0 or start >= len(buf):
        return
    start = max(0, int(start))
    n = min(len(snippet), len(buf) - start)
    if n > 0:
        buf[start:start + n] += snippet[:n]


SFX_GAIN_OVER_SOURCE_DB = 3.0     # SEの音量は、元動画の音声（RMS基準）より約この分(dB)大きくする
SFX_DEFAULT_RMS_WHEN_SILENT = 0.18  # 元動画に音声が無い/ほぼ無音の場合に使うSEの既定RMS目標値
SOFT_LIMIT_THRESHOLD = 0.9        # このレベルを超えた分だけtanhで滑らかに丸める（ハードクリップ回避）

# ユーザー提供の効果音ファイル（freeze.sfx / logo.sfx のオブジェクト形式）に関する既定値。
# プリセット（assets/sfx/配下の同梱wav）は既にバランス調整済みという前提でこの正規化を
# 適用しない。ユーザーファイルは録音・書き出し環境によって音量がまちまちなため、
# まずピークを揃えてから、既存のRMS基準の音量バランス調整（SFX_GAIN_OVER_SOURCE_DB）に乗せる。
SFX_CUSTOM_PEAK_TARGET_DB = -1.0
# 「音の終わり」を判定するしきい値。末尾からこの値以下（無音）が続く区間を、
# align="end_at_landing" の位置合わせでは無視する。
SFX_TRAILING_SILENCE_DB = -50.0
SFX_ALIGNS_FREEZE = ("start_at_landing", "end_at_landing")
SFX_ALIGNS_LOGO = ("start_at_landing", "peak_at_landing")
SFX_ALIGN_DEFAULT = "start_at_landing"


def measure_rms(samples):
    """音声(N, ch)のRMS（二乗平均平方根）を返す。空なら0.0"""
    if samples.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(np.square(samples))))


def normalize_sfx_gain(snippet, source_rms):
    """
    効果音snippetの音量を、元動画の音声(source_rms)よりSFX_GAIN_OVER_SOURCE_DB dBだけ
    大きくなるよう調整する。元動画がほぼ無音（source_rmsが小さい）場合は、測定不能な
    source_rmsに引きずられて過大なゲインをかけないよう、SFX_DEFAULT_RMS_WHEN_SILENTを
    目標値として使う。ピークでの歪みは、最終ミックス後のsoft_limit()側で防ぐ
    （ここでは音量バランスの調整のみを行う）。
    """
    if snippet.size == 0:
        return snippet
    snippet_rms = measure_rms(snippet)
    if snippet_rms <= 1e-6:
        return snippet
    target_rms = (source_rms * (10.0 ** (SFX_GAIN_OVER_SOURCE_DB / 20.0))
                  if source_rms > 1e-4 else SFX_DEFAULT_RMS_WHEN_SILENT)
    gain = target_rms / snippet_rms
    return snippet * gain


def normalize_peak_dbfs(snippet, target_db=SFX_CUSTOM_PEAK_TARGET_DB):
    """
    snippet(N, ch)の振幅ピークが target_db(dBFS) になるよう正規化する。
    ユーザー提供の効果音ファイルは、録音・書き出し環境によって音量がまちまちなため、
    RMS基準の音量バランス調整（normalize_sfx_gain）にかける前に、まずここでピークを
    揃えておく。無音（ピークがほぼ0）の場合は何もしない。
    """
    if snippet.size == 0:
        return snippet
    peak = float(np.max(np.abs(snippet)))
    if peak <= 1e-9:
        return snippet
    target_linear = 10.0 ** (target_db / 20.0)
    return snippet * (target_linear / peak)


def trailing_silence_trimmed_length(samples, threshold_db=SFX_TRAILING_SILENCE_DB):
    """
    samples(N, ch)を末尾から見て、threshold_db(dBFS)以下が続く無音区間を除いた
    実効的な長さ（サンプル数）を返す。全体が無音（しきい値を一度も超えない）の場合は
    安全側としてlen(samples)をそのまま返す（無音全体を「音」として扱い、極端に
    早い位置へずれてしまうのを防ぐ）。
    """
    if samples.size == 0:
        return 0
    threshold = 10.0 ** (threshold_db / 20.0)
    peak_per_sample = np.max(np.abs(samples), axis=1)
    above = np.nonzero(peak_per_sample > threshold)[0]
    if above.size == 0:
        return len(samples)
    return int(above[-1]) + 1


def peak_sample_index(samples):
    """samples(N, ch)のうち、振幅（全チャンネル中の絶対値最大）がピークとなるサンプル位置を返す"""
    if samples.size == 0:
        return 0
    peak_per_sample = np.max(np.abs(samples), axis=1)
    return int(np.argmax(peak_per_sample))


def compute_sfx_start(snippet, landing_sample, align):
    """
    効果音snippetの再生開始位置（新タイムライン上のサンプル位置）を、align指定に応じて
    landing_sample（着地の瞬間）から逆算する。
      - start_at_landing（既定）: 着地の瞬間に再生開始（従来どおり、そのまま返す）
      - end_at_landing: 音の終わり（末尾の無音を除いた位置）が着地に一致するよう
        開始位置を前にずらす
      - peak_at_landing: 音声のピーク位置が着地に一致するよう開始位置を前にずらす
    計算結果が負（＝映像の先頭より前）になる場合は、呼び出し側のmix_into()が
    自動的に0にクランプして先頭から鳴らす。
    """
    if align == "end_at_landing":
        return landing_sample - trailing_silence_trimmed_length(snippet)
    if align == "peak_at_landing":
        return landing_sample - peak_sample_index(snippet)
    return landing_sample


def soft_limit(samples, threshold=SOFT_LIMIT_THRESHOLD):
    """
    最終ミックス後に使う簡易リミッター。thresholdを超えた部分だけtanhで滑らかに
    丸め込み（それ以下はそのまま）、単純な np.clip によるハードクリップ（歪みっぽい
    音割れ）を避ける。効果音を元動画より音量アップ（SFX_GAIN_OVER_SOURCE_DB）しても
    ピークで歪まないようにするための仕上げ処理。
    """
    out = np.asarray(samples, dtype=np.float32).copy()
    over = np.abs(out) > threshold
    if np.any(over):
        sign = np.sign(out[over])
        span = max(1e-6, 1.0 - threshold)
        excess = (np.abs(out[over]) - threshold) / span
        out[over] = sign * (threshold + span * np.tanh(excess))
    return np.clip(out, -1.0, 1.0)


def apply_reverb_tail(samples, sr, tail_sec=LOGO_SFX_TAIL_SEC, num_taps=6, decay=0.55):
    """
    logo.sfx_tail=true（既定）のとき、ロゴ着地SEに簡易的な減衰ディレイ（コムフィルタ風の
    エコーを tail_sec 秒かけて重ねる、簡易的なリバーブ近似）を付け、低音の余韻が残る
    ようにする。本格的な畳み込みリバーブではなく、num_taps回、一定間隔で少しずつ
    遅れて重なる減衰エコーを足すだけの簡易版。戻り値は元よりtail_sec秒分長くなる。
    """
    if samples.size == 0 or tail_sec <= 0:
        return samples
    tail_samples = int(round(tail_sec * sr))
    if tail_samples <= 0:
        return samples
    out = np.zeros((len(samples) + tail_samples, samples.shape[1]), np.float32)
    out[:len(samples)] += samples
    tap_interval = max(1, tail_samples // num_taps)
    amp = 1.0
    for i in range(1, num_taps + 1):
        amp *= decay
        offset = tap_interval * i
        n = min(len(samples), len(out) - offset)
        if n <= 0:
            break
        out[offset:offset + n] += samples[:n] * amp
    return out


def build_audio(src_path, plans, fps, src_frames, has_audio, sr=AUDIO_SR, ch=AUDIO_CH,
                 logo_sfx_spec=None, logo_at=None, logo_extra_frames=0, logo_params=None):
    """
    フリーズ区間の分だけ元音声を後ろへずらし、
    フリーズ中は無音 or 直前0.5秒のループを差し込み、効果音を重ねる。
    logo_sfx_spec のSE（インパクトSE）は、ロゴが「着地」する瞬間（align="start_at_landing"の場合。
    align="peak_at_landing"なら音声のピーク位置がその瞬間に一致するよう前にずらす）に鳴らす
    （logo_at=='last_freeze' なら最後のフリーズのクロスフェード後の着地時、
      'end' なら末尾に追加する logo_extra_frames 区間内の着地時）。
    """
    orig = decode_audio(src_path, sr, ch) if has_audio else np.zeros((0, ch), np.float32)

    # 音声が無い動画でも音声トラックは作る（映像長ぶんの無音）
    need = frames_to_samples(src_frames, fps, sr) if src_frames > 0 else 0
    if len(orig) < need:
        orig = np.concatenate([orig, np.zeros((need - len(orig), ch), np.float32)])

    pieces = []
    sfx_jobs = []      # (着地の瞬間の新タイムライン上のサンプル位置, sfx_spec, 減衰ディレイを付けるか)
    cursor = 0         # 元音声の読み出し位置
    written = 0        # 出力済みサンプル数（＝新タイムラインの位置）

    for plan in plans:
        # 映像と同じ時刻基準（フレーム番号）で切り貼りしてズレを防ぐ
        cut = frames_to_samples(plan["frame_index"], fps, sr)
        cut = min(max(cut, cursor), len(orig))
        pieces.append(orig[cursor:cut])
        written += cut - cursor
        cursor = cut

        n_samples = frames_to_samples(plan["n_total"], fps, sr)
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

        # 効果音の基準は「人物の出現が完了した瞬間」（shadowがあれば、スライドインが着地した瞬間）。
        # align="start_at_landing"（既定）ならこの瞬間に再生開始、"end_at_landing"なら
        # 音の終わりがこの瞬間に一致するよう開始位置を前にずらす（compute_sfx_start参照）。
        landed_frame_offset = plan["n_pre"] + plan["n_reveal"] + plan.get("n_slide_in", 0)
        if plan["sfx"]:
            offset = frames_to_samples(landed_frame_offset, fps, sr)
            sfx_jobs.append((written + offset, plan["sfx"], False))
        # ロゴがこのフリーズ中に表示される場合、着地の瞬間（暗転カットの後、ロゴが最小サイズに
        # 到達する瞬間）を基準にSEを鳴らす
        if plan.get("show_logo") and logo_at == "last_freeze" and logo_sfx_spec and logo_params:
            landing_instant_frames = int(round(logo_landing_instant_sec(logo_params) * fps))
            logo_offset = frames_to_samples(
                landed_frame_offset + plan.get("logo_blackout_frames", 0) + landing_instant_frames,
                fps, sr)
            sfx_jobs.append((written + logo_offset, logo_sfx_spec, logo_params["sfx_tail"]))
        written += n_samples

    pieces.append(orig[cursor:])
    tail_start = written + (len(orig) - cursor)   # 元音声の残り分を挟んだ後の位置

    if logo_at == "end" and logo_extra_frames > 0:
        extra_samples = frames_to_samples(logo_extra_frames, fps, sr)
        pieces.append(np.zeros((extra_samples, ch), np.float32))
        if logo_sfx_spec and logo_params:
            landing_instant_frames = logo_blackout_frames_for(fps) + int(round(logo_landing_instant_sec(logo_params) * fps))
            landing_samples = frames_to_samples(landing_instant_frames, fps, sr)
            sfx_jobs.append((tail_start + landing_samples, logo_sfx_spec, logo_params["sfx_tail"]))

    out = np.concatenate(pieces) if pieces else np.zeros((0, ch), np.float32)

    # SEの音量バランス：元動画の音声(orig)のRMSを基準に、SFX_GAIN_OVER_SOURCE_DB分だけ
    # 大きく聞こえるよう各SFXを正規化する（ピークの歪みはsoft_limit()側で防ぐ）
    source_rms = measure_rms(orig)
    for landing_sample, spec, apply_tail in sfx_jobs:
        snippet = decode_audio(spec["path"], sr, ch)
        if spec["is_custom"]:
            # ユーザー提供ファイルはまずピークを揃えてから、通常どおりのRMSバランス調整に乗せる
            snippet = normalize_peak_dbfs(snippet)
        # 位置合わせ（end_at_landing/peak_at_landing）は、RMS調整やテールで音量・長さが
        # 変わる前の、元の波形のまま計算する（開始位置の計算に影響させないため）
        start = compute_sfx_start(snippet, landing_sample, spec["align"])
        snippet = normalize_sfx_gain(snippet, source_rms)
        if apply_tail:
            snippet = apply_reverb_tail(snippet, sr)
        mix_into(out, snippet, start)

    return soft_limit(out)


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

def _write_timing_summary(render_seconds):
    """
    フリーズごとの自動切り抜き所要時間・キャッシュ使用有無（_TIMING_LOG）と、
    render()全体の所要時間をTIMING_JSON_NAME（cwd基準）へ書き出す（ジョブサマリー表示用。
    render.ymlがこのJSONを読んで内訳をまとめる）。
    color_source/shadow.sourceが両方"auto"の同じフリーズでは、get_or_extract_alphaが
    2回（色用・影用）呼ばれることがあるため、同じ時刻のエントリは実際に抽出が
    走った方（cache_used=False）を優先して1件にまとめる。
    書き出しに失敗してもレンダリング自体の成功を妨げないよう、例外は握りつぶしてwarnする。
    """
    try:
        merged = {}
        for entry in _TIMING_LOG:
            key = entry["time"]
            if key not in merged or not entry["cache_used"]:
                merged[key] = entry
        freezes = sorted(merged.values(), key=lambda e: e["time"])
        extract_total = sum(e["extract_seconds"] or 0 for e in freezes)
        summary = {
            "freezes": freezes,
            "extract_seconds_total": round(extract_total, 2),
            "render_seconds": round(render_seconds, 2),
        }
        with open(TIMING_JSON_NAME, "w", encoding="utf-8") as f:
            json.dump(summary, f, ensure_ascii=False, indent=2)
    except Exception as exc:  # noqa: BLE001 - 内訳の書き出し失敗はレンダリング成功を妨げない
        warn(f"所要時間の内訳（{TIMING_JSON_NAME}）の書き出しに失敗しました: {exc}")


def render(project, json_path, video_path, out_path, preview_path=None,
           extract_frames_for=None, extract_frames_out=None):
    """プロジェクト定義に従って1本のMP4（または確認用PNG）を作る"""
    _TIMING_LOG.clear()
    render_start = time.time()
    json_dir = os.path.dirname(os.path.abspath(json_path))
    info = probe_video(video_path)

    out_cfg = project["output"] or {}
    fps = float(out_cfg.get("fps") or info["fps"])
    if fps <= 0:
        raise RuntimeError("出力サイズ/fpsが不正です。")

    # --- 一時ディレクトリは、確認用PNGだけの場合も含めて関数を抜ける時に必ず片付ける ---
    tmpdir = tempfile.mkdtemp(prefix="spotlight_")
    try:
        if info["is_vfr"]:
            warn(f"入力が可変フレームレート(VFR)のようです"
                 f"（平均{info['fps']:.2f}fps・基準{info['r_fps']:.2f}fps）。"
                 f"固定{fps:.2f}fpsへ正規化してから処理します。")
            video_path = normalize_frame_rate(video_path, fps, info["has_audio"], tmpdir)
            info = probe_video(video_path)

        W = even(out_cfg.get("width") or info["width"])
        H = even(out_cfg.get("height") or info["height"])
        if W <= 0 or H <= 0:
            raise RuntimeError("出力サイズ/fpsが不正です。")

        log(f"入力: {video_path}  {info['width']}x{info['height']} "
            f"{info['fps']:.3f}fps rotation={info['rotation']} "
            f"audio={'あり' if info['has_audio'] else 'なし'}")
        log(f"出力: {W}x{H} {fps:.3f}fps  フリーズ {len(project['freezes'])} 箇所")

        src_frames = int(round(info["duration"] * fps)) if info["duration"] else 0

        # --- ロゴ設定の解決（無指定ならlogo_cfg=None、以後ロゴ関連処理はすべて素通りになる） ---
        logo_cfg = project.get("logo")
        logo_at = None
        logo_bgr, logo_alpha, logo_luma = None, None, None
        logo_sfx_spec = None
        logo_extra_frames = 0
        logo_params = None
        logo_bg_color = None
        logo_blackout_frames = 0
        logo_total_frames = 0
        if logo_cfg:
            logo_at = resolve_logo_at(logo_cfg.get("at"), bool(project["freezes"]))
            logo_path = resolve_path(logo_cfg.get("image"), [os.getcwd(), json_dir, SCRIPT_DIR])
            logo_bgr, logo_alpha = load_logo_image(logo_path)
            if logo_bgr is None:
                warn(f"ロゴ画像が見つかりません（{logo_cfg.get('image')}）。ロゴ演出は無効化します。")
                logo_cfg, logo_at = None, None
            else:
                # ロゴ画像の余白（透明部分、または不透明PNGの背景色部分）を自動検出してクロップし、
                # 内容部分だけをwidth_ratio基準で配置する（見た目の「迫力」を上げるため）
                logo_bgr, logo_alpha = crop_logo_content(logo_bgr, logo_alpha)
                logo_luma = build_logo_luminance_mask(logo_bgr, logo_alpha)
                logo_params = resolve_logo_params(logo_cfg)
                logo_bg_color = resolve_logo_background_color(logo_cfg.get("background"), logo_bgr)
                logo_sfx_spec = resolve_sfx_spec(logo_cfg.get("sfx"), json_dir, SFX_ALIGNS_LOGO,
                                                  "logo")
                logo_total_frames = logo_total_frames_for(logo_params, fps)
                # 直前のシーンから暗転経由でカットしてロゴ演出へ切り替える（logo.at・
                # logo.backgroundの指定によらず常に挟む。LOGO_CUT_BLACKOUT_SEC・固定）
                logo_blackout_frames = logo_blackout_frames_for(fps)
                if logo_at == "end":
                    logo_extra_frames = logo_blackout_frames + logo_total_frames

        plans = plan_freezes(project["freezes"], fps, src_frames, json_dir, logo_cfg, logo_at,
                              logo_total_frames=(logo_total_frames if logo_params else 0),
                              logo_blackout_frames=(logo_blackout_frames if logo_at == "last_freeze" else 0))

        # 自動切り抜き（mask="auto"/"auto+brush"）のアルファは、動画名＋フリーズ時刻をキーに
        # cwd基準の cache/ ディレクトリへキャッシュし、同じフレームの再レンダリング時は
        # 再計算しない。
        cache_dir = os.path.join(os.getcwd(), CACHE_DIR_NAME)

        # --- spotlight-jobsのprepare段用：指定モデルが必要なフリーズの静止フレームだけ
        #     書き出して終了する（Apple Visionのように、このrender.py自身が実行できない
        #     モデルの抽出をmacOSランナーへ委ねるための下ごしらえ）。 ---
        if extract_frames_for:
            written = extract_frames_for_model(project, video_path, W, H, fps, plans,
                                                extract_frames_for, extract_frames_out)
            manifest_path = os.path.join(extract_frames_out, "manifest.json")
            with open(manifest_path, "w", encoding="utf-8") as f:
                json.dump({"model": extract_frames_for, "output_width": W, "output_height": H,
                           "frames": written}, f, ensure_ascii=False, indent=2)
            log(f"model={extract_frames_for} が必要なフリーズのフレームを{len(written)}枚書き出しました: {extract_frames_out}")
            return manifest_path

        # --- 確認用PNG（スライド前・スライド後の2枚）だけ出して終了 ---
        if preview_path:
            if not plans:
                raise RuntimeError("freezes が空なので preview を作れません。")
            plan = plans[0]
            frame = grab_frame_at(video_path, plan["frame_index"] / fps, W, H, fps)
            before_path, after_path = render_preview(frame, plan, W, H, fps, {}, preview_path,
                                                       cache_dir=cache_dir, video_path=video_path)
            preview_msg = f"プレビューを書き出しました: {before_path}（スライド前） / {after_path}（スライド後）"
            if logo_cfg and logo_bgr is not None:
                # 自動クロップが実際に効いているかを目視確認できるよう、
                # クロップ後のロゴ単体もアルファ付きPNGで書き出す
                base, ext = os.path.splitext(preview_path)
                logo_preview_path = f"{base}_logo{ext or '.png'}"
                save_logo_crop_preview_png(logo_bgr, logo_alpha, logo_preview_path)
                preview_msg += f" / {logo_preview_path}（クロップ後ロゴ単体）"
            log(preview_msg)
            return before_path, after_path

        # --- 音声を先に作る（ffmpegのmux入力として渡すため） ---
        audio = build_audio(video_path, plans, fps, src_frames, info["has_audio"],
                             logo_sfx_spec=logo_sfx_spec, logo_at=logo_at,
                             logo_extra_frames=logo_extra_frames, logo_params=logo_params)
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
                    for f in iter_freeze_frames(frame, plan, W, H, fps, font_cache,
                                                cache_dir=cache_dir, video_path=video_path,
                                                logo_bgr=logo_bgr, logo_alpha=logo_alpha, logo_luma=logo_luma,
                                                logo_params=logo_params, logo_bg_color=logo_bg_color):
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
                for f in iter_freeze_frames(frame, plan, W, H, fps, font_cache,
                                            cache_dir=cache_dir, video_path=video_path,
                                            logo_bgr=logo_bgr, logo_alpha=logo_alpha, logo_luma=logo_luma,
                                            logo_params=logo_params, logo_bg_color=logo_bg_color):
                    writer.stdin.write(f.tobytes())
                    last_out_frame = f
                    written += 1

            # logo.at=='end'：末尾に新しい区間としてロゴを書き足す。背景色を敷くモードなら
            # 全面その色のフレーム、"video"モードなら最後に出力したフレームを背景にする。
            # 最後に出力したフレームから0.1秒（LOGO_CUT_BLACKOUT_SEC）かけて暗転し、
            # そこでロゴ演出へカットする（クロスフェードではない）。
            if logo_at == "end" and logo_bgr is not None and last_out_frame is not None:
                if logo_bg_color is not None:
                    backdrop = np.full((H, W, 3), logo_bg_color, dtype=np.uint8)
                else:
                    backdrop = np.ascontiguousarray(last_out_frame)
                blackout_frames = logo_blackout_frames_for(fps)
                last_frame_f32 = np.ascontiguousarray(last_out_frame).astype(np.float32)
                for i in range(blackout_frames):
                    bt = (i + 1) / float(blackout_frames)
                    out_frame = np.clip(last_frame_f32 * (1.0 - bt), 0, 255).astype(np.uint8)
                    writer.stdin.write(out_frame.tobytes())
                    written += 1
                    if written % 15 == 0:
                        print(f"\r  {written}/{total_out} フレーム", end="", flush=True)
                for i in range(logo_total_frames):
                    out_frame = render_logo_frame(backdrop, logo_bgr, logo_alpha, logo_luma,
                                                   W, H, i / float(fps), logo_params, logo_bg_color)
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
        _write_timing_summary(time.time() - render_start)
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
                        help="最初のフリーズの「影スライド前」「スライド後」をPNG2枚（_before/_after）出力して終了")
    parser.add_argument("--extract-apple-frames", metavar="MODEL",
                         help="指定モデル（例: apple-vision）を使うフリーズの静止フレームだけを"
                              "--extract-apple-frames-out へ書き出して終了する"
                              "（spotlight-jobsのprepare段が使う。動画の合成・エンコードは行わない）")
    parser.add_argument("--extract-apple-frames-out", default="apple_frames",
                         help="--extract-apple-frames の出力先ディレクトリ（既定: apple_frames）")
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

    if not preview_path and not args.extract_apple_frames:
        os.makedirs(os.path.dirname(os.path.abspath(args.out)) or ".", exist_ok=True)

    render(project, args.project, video, args.out, preview_path,
           extract_frames_for=args.extract_apple_frames,
           extract_frames_out=args.extract_apple_frames_out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
