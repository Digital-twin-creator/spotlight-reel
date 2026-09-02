#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
extract.py — 画像/動画から被写体を自動で切り抜き、アルファマット
（0/1ではない連続値の半透明境界を含むアルファチャンネル）を得る、
独立実行可能なモジュール。

他プロジェクトからもそのまま使えるよう、依存は requirements-extract.txt に
分離し、出力はファイル名・形式を固定した「契約」として扱う。
render.py はこのファイルの extract_alpha() / extract_alpha_rvm() を直接importして
使う（rembg・torchのimportは実際に呼ばれるまで遅延するため、mask:"brush"のみの
プロジェクトではrembg/onnxruntime/torchが未インストールでもrender.pyは問題なく動く）。

静止画モデル（rembg・isnet-general-use/birefnet-portrait等）は、フレーム単体だけを
見て切り抜くため、背景と紛らわしい色の巻き込みや、体の一部（手・腕など）が
シルエットから欠落する、といった構造的な失敗が起きうる。これに対し「動画モード」
（rvm-mobilenetv3、RobustVideoMatting）は、フリーズ時刻の前後クリップを時系列順に
モデルへ流し、再帰状態（ConvGRUの隠れ状態）を温めながら進めることで、直前フレームの
文脈を使ってこの種の失敗を防ぐ（詳細はextract_alpha_rvm()参照）。

使い方（静止画モード）:
    python extract.py input.png --out outdir/ \
        [--model birefnet-portrait|birefnet-general-lite|isnet-general-use] \
        [--refine vitmatte] [--decontaminate]

使い方（動画モード、RVM）:
    python extract.py input.mp4 --out outdir/ --model rvm-mobilenetv3 \
        --video-time 2.5 --output-width 1080 --output-height 1920

出力（outdir/ 配下、固定のファイル名。他プロジェクトと共有する契約。
どちらのモードでも同じ）:
    subject-rgba.png   前景RGB＋アルファ（透過PNG）
    alpha.png          アルファチャンネルのみ（8bitグレースケール、0〜255の連続値）
    mask.png           アルファ>=128 の2値マスク（0 または 255）
    preview.png        チェッカー背景に合成した確認用プレビュー
    metadata.json       {"extractor": "...", "refine": "..."|null,
                          "decontaminate": bool, "input": "...",
                          "size": [w, h], "elapsedSec": float, "createdAt": "..."}
                        （動画モードでは追加で "freezeTimeSec"/"preSec"/"postSec" も記録）

依存:
  静止画モード: rembg（MIT）, onnxruntime, numpy, pillow
  動画モード:   torch（CPU版）, RobustVideoMatting（MIT、PeterL1n/RobustVideoMatting。
                torch.hub経由でモデルコード・重みを取得）, numpy, pillow, ffmpeg
  （requirements-extract.txt参照）
"""

import argparse
import datetime
import json
import os
import shutil
import subprocess
import sys
import time

import numpy as np
from PIL import Image

# 既定は本来 birefnet-portrait だが、実際のGitHub Actionsランナー(CPU)で1080x1920 1枚を
# 計測したところ、モデル読み込み＋推論で約64秒（要件の30秒を大きく超過）だった。
# 要件どおりのフォールバック先である birefnet-general-lite も試したが、こちらは
# 実機Actionsランナー上でモデルキャッシュ有無に関わらず約81.6秒かかり
# （sandboxでの実測より実機のCPUが大幅に遅く、ダウンロード時間ではなく推論自体が
# 遅いことをキャッシュ有無での比較で確認済み）、依然として30秒を大きく超えてしまう。
# そのため、候補3モデルのうち実機Actionsランナーで唯一30秒を大きく下回った
# isnet-general-use（実測約8.7秒、精度は他の2モデルより劣る古めの汎用モデル）を
# extract.py単体（画像1枚を渡すCLI・render.pyがmask_options.model省略時に使う
# フォールバック）の既定にしている（README参照。実測値の詳細もそちらに記載）。
# エディタ側の既定選択は別（"動画（安定・推奨）"＝RVM_MODEL_NAME）で、
# index.htmlのDEFAULT_MASK_MODEL_SELECTIONを参照。両者が別なのは意図的：
# 「mask_options.model省略時に何を使うか」（＝これ。旧JSONとの後方互換を保つため
# 変更しない）と「エディタで新規に選ぶ際の初期値」（＝RVM）は別の関心事のため。
DEFAULT_MODEL = "isnet-general-use"
RVM_MODEL_NAME = "rvm-mobilenetv3"
VALID_MODELS = ("birefnet-portrait", "birefnet-general-lite", "isnet-general-use", RVM_MODEL_NAME)
VALID_REFINES = ("vitmatte",)

# --- 動画モード（RVM: Robust Video Matting）のパラメータ ---
RVM_TORCH_HUB_REPO = "PeterL1n/RobustVideoMatting"   # MIT license
RVM_TORCH_HUB_MODEL = "mobilenetv3"                  # 軽量版（resnet50版より高速）
RVM_PRE_SEC_DEFAULT = 1.5   # フリーズ時刻より前、再帰状態を温めるために遡る秒数
RVM_POST_SEC_DEFAULT = 0.5  # クリップの末尾に残す余白（シーク誤差対策。推論には使わない。後述）
RVM_DOWNSAMPLE_MAX_SIDE = 512  # downsample_ratio自動計算の基準（RVM公式サンプルと同じ考え方）

_rvm_model_singleton = {}


def log(msg):
    print(msg, flush=True)


def run_rembg(img, model_name=DEFAULT_MODEL, refine=None, decontaminate=False, session=None):
    """
    PIL RGB画像を rembg で切り抜き、(RGBA画像, 使用したsession) を返す。
    rembgのimportはこの関数の中でだけ行う（呼ばれない限りrembg/onnxruntimeが
    未インストールでもエラーにならないようにするため）。

    refine="vitmatte" は rembg 組み込みの ViTMatte 精密化（ONNXモデル、
    髪の毛など細い境界のアルファを推定し直す）を有効にする。
    decontaminate=True は、半透明の境界画素に残る背景色のにじみを、
    前景色を推定し直すことで除去する（rembg組み込み、pymattingベース）。
    vitmatte有効時は常に decontaminate 相当の処理が行われる（rembgの仕様）。
    """
    from rembg import new_session, remove

    if session is None:
        session = new_session(model_name)
    kwargs = {}
    if refine == "vitmatte":
        kwargs["vitmatte"] = True
    if decontaminate:
        kwargs["decontaminate"] = True
    out = remove(img, session=session, **kwargs)
    return out.convert("RGBA"), session


def extract_alpha(img, model_name=DEFAULT_MODEL, refine=None, decontaminate=False, session=None):
    """
    PIL画像から (rgba: PIL.Image(RGBA), elapsed_sec: float, session) を返す。
    render.py はこの戻り値のうちアルファチャンネルだけをキャッシュ・使用する。
    session を渡すと再利用し、モデル読み込み（数秒〜）を1回で済ませられる
    （1本の動画に複数のauto/auto+brushフリーズがある場合に効く）。
    """
    if img.mode != "RGB":
        img = img.convert("RGB")
    t0 = time.time()
    rgba, session = run_rembg(img, model_name, refine, decontaminate, session=session)
    elapsed = time.time() - t0
    return rgba, elapsed, session


def make_checker(w, h, size=16, light=235, dark=205):
    """プレビュー用のチェッカー柄背景（透明部分の確認用）を作る"""
    tile = np.full((size * 2, size * 2), dark, np.uint8)
    tile[:size, :size] = light
    tile[size:, size:] = light
    reps_y = h // (size * 2) + 2
    reps_x = w // (size * 2) + 2
    big = np.tile(tile, (reps_y, reps_x))[:h, :w]
    return np.stack([big, big, big], axis=2)


def build_preview(rgba: np.ndarray) -> np.ndarray:
    """RGBA配列(H,W,4)を、チェッカー背景に合成したBGR風確認用プレビュー(H,W,3)にする"""
    h, w = rgba.shape[:2]
    checker = make_checker(w, h).astype(np.float32)
    rgb = rgba[:, :, :3].astype(np.float32)
    a = (rgba[:, :, 3].astype(np.float32) / 255.0)[:, :, None]
    out = checker * (1 - a) + rgb * a
    return np.clip(out, 0, 255).astype(np.uint8)


def write_extract_files(rgba, out_dir, metadata):
    """
    RGBA配列(H,W,4) uint8 と metadata dict を、固定形式のファイル一式として out_dir に書き出す
    （extract_to_files / extract_to_files_video の共通部分）。metadataはそのままJSON化して保存する。
    """
    os.makedirs(out_dir, exist_ok=True)
    Image.fromarray(rgba, mode="RGBA").save(os.path.join(out_dir, "subject-rgba.png"))
    Image.fromarray(rgba[:, :, 3], mode="L").save(os.path.join(out_dir, "alpha.png"))
    mask = np.where(rgba[:, :, 3] >= 128, 255, 0).astype(np.uint8)
    Image.fromarray(mask, mode="L").save(os.path.join(out_dir, "mask.png"))
    Image.fromarray(build_preview(rgba)).save(os.path.join(out_dir, "preview.png"))
    with open(os.path.join(out_dir, "metadata.json"), "w", encoding="utf-8") as f:
        json.dump(metadata, f, ensure_ascii=False, indent=2)


def extract_to_files(input_path, out_dir, model_name=DEFAULT_MODEL, refine=None, decontaminate=False):
    """CLI本体（静止画モード）。入力画像1枚を処理し、固定形式のファイル一式をout_dirに書き出す。"""
    img = Image.open(input_path)
    if img.mode != "RGB":
        img = img.convert("RGB")

    rgba_img, elapsed, _session = extract_alpha(img, model_name, refine, decontaminate)
    rgba = np.array(rgba_img)

    metadata = {
        "extractor": f"rembg-{model_name}",
        "refine": refine,
        "decontaminate": bool(decontaminate),
        "input": os.path.abspath(input_path),
        "size": [img.width, img.height],
        "elapsedSec": round(elapsed, 3),
        "createdAt": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    write_extract_files(rgba, out_dir, metadata)

    log(f"処理時間: {elapsed:.2f}秒（モデル={model_name}, refine={refine}, decontaminate={decontaminate}）")
    log(f"出力: {out_dir}")
    return metadata


# ---------------------------------------------------------------------------
# 動画モード（RVM: Robust Video Matting）
# ---------------------------------------------------------------------------
#
# 静止画モデルは1フレームだけを見て切り抜くため、次のような構造的な失敗が起きうる：
#   - 背景の巻き込み（服・壁の色が背景と紛らわしく、境界を誤検出する）
#   - 手・腕など身体の一部がシルエットから欠落する
# RVMはこれを、時間方向の情報（直前フレームの推定結果を再帰的に持ち越すConvGRU）で
# 防ぐ。フリーズ時刻の前後クリップを切り出し、先頭（＝一番過去）から時系列順に
# 1フレームずつモデルへ通し、隠れ状態(r1〜r4)を温めながらフリーズ時刻のフレームまで
# 進めることで、静止画単体では得られない文脈を使った推定になる。
#
# RVMは純粋な因果的（過去→未来の一方向）再帰モデルであり、あるフレームの出力は
# それより後のフレームの影響を一切受けない。そのため実際の推論はクリップ先頭から
# フリーズ時刻フレームまでで打ち切ればよく、クリップ末尾側（RVM_POST_SEC_DEFAULT分）は
# 主に「ffmpegのシーク精度の丸め誤差でフリーズ時刻がクリップ末尾ぎりぎりにならない
# ための安全マージン」として切り出すだけで、推論そのものには使わない。


def _find_exe(name):
    """ffmpeg / ffprobe の場所を探す（render.pyのfind_exeと同じ考え方。extract.py単体で
    動かせるよう、ここでも自前で持つ）。"""
    path = shutil.which(name)
    if not path:
        raise RuntimeError(f"{name} が見つかりません。ffmpeg をインストールしてください。")
    return path


def probe_video_fps(video_path):
    """ffprobeで動画のfpsを取得する（可変フレームレート考慮の平均→基準の順でフォールバック）"""
    ffprobe = _find_exe("ffprobe")
    cmd = [ffprobe, "-v", "error", "-select_streams", "v:0",
           "-show_entries", "stream=r_frame_rate,avg_frame_rate",
           "-of", "json", video_path]
    out = subprocess.run(cmd, check=True, capture_output=True).stdout
    info = json.loads(out.decode("utf-8", "replace"))
    streams = info.get("streams") or [{}]
    st = streams[0]

    def parse_rate(val):
        if not val or val == "0/0":
            return None
        num, _, den = val.partition("/")
        den = den or "1"
        try:
            n, d = float(num), float(den)
        except ValueError:
            return None
        return n / d if d != 0 and n != 0 else None

    return parse_rate(st.get("avg_frame_rate")) or parse_rate(st.get("r_frame_rate")) or 30.0


def extract_clip_frames_bgr(video_path, start_sec, end_sec, W, H, fps):
    """
    video_pathの[start_sec, end_sec)を、出力解像度W×Hへレターボックス整形しつつ
    切り出し、BGR uint8フレーム（H,W,3）のlistとして返す（render.pyのbuild_scale_filterと
    同じフィルタ。数秒程度の短いクリップ前提でメモリに載せる）。
    """
    ffmpeg = _find_exe("ffmpeg")
    duration = max(0.0, end_sec - start_sec)
    vf = (f"fps={fps:.6f},"
          f"scale={W}:{H}:force_original_aspect_ratio=decrease,"
          f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color=black,"
          f"setsar=1")
    cmd = [ffmpeg, "-v", "error",
           "-ss", f"{max(start_sec, 0.0):.6f}", "-i", video_path,
           "-t", f"{duration:.6f}",
           "-vf", vf,
           "-f", "rawvideo", "-pix_fmt", "bgr24", "pipe:1"]
    out = subprocess.run(cmd, check=True, capture_output=True).stdout
    nbytes = W * H * 3
    n = len(out) // nbytes
    return [np.frombuffer(out[i * nbytes:(i + 1) * nbytes], np.uint8).reshape(H, W, 3).copy()
            for i in range(n)]


def rvm_auto_downsample_ratio(h, w):
    """
    RVM公式サンプル（inference_utils.pyのauto_downsample_ratio）と同じ計算。
    長辺がRVM_DOWNSAMPLE_MAX_SIDE(512)pxに収まる比率まで内部でダウンサンプルしてから
    推論し、アルファだけ元解像度へ戻す（精度を保ちつつ実行時間を抑えるための
    公式推奨設定）。既に512px以下ならダウンサンプルしない（比率1.0）。
    """
    return min(RVM_DOWNSAMPLE_MAX_SIDE / max(h, w), 1.0)


def load_rvm_model():
    """
    RVM(mobilenetv3)モデルをtorch.hubから読み込み、プロセス内で使い回す
    （torch.hubは初回のみモデルコード＋学習済み重みをダウンロードし、以後は
    ~/.cache/torch/hub にキャッシュする。CI側はwarm-model-cache.ymlでこの
    ディレクトリごとキャッシュする）。torchのimportはこの関数内でのみ行うため、
    RVMを使わないプロジェクト・ジョブではtorch未インストールでも問題ない。

    trust_repo=Trueを指定しないと、torch.hub側の仕様で「このリポジトリを信頼するか」を
    標準入力から尋ねようとし、GitHub Actions等の非対話環境では標準入力が無いため
    `EOFError: EOF when reading a line` で落ちる（実Actionsジョブでの検証で発見）。
    RVM_TORCH_HUB_REPOは固定のMITライセンスリポジトリであることが分かっているため、
    常に信頼して問題ない。
    """
    import torch

    if "model" not in _rvm_model_singleton:
        model = torch.hub.load(RVM_TORCH_HUB_REPO, RVM_TORCH_HUB_MODEL, trust_repo=True)
        model.eval()
        _rvm_model_singleton["model"] = model
    return _rvm_model_singleton["model"]


def rvm_infer_alpha(frames_rgb, target_index, downsample_ratio=None):
    """
    frames_rgb（時系列順、同一解像度のRGB uint8配列(H,W,3)のリスト）をRVMへ先頭から
    順に流し、再帰状態を温めながら target_index フレームまで進めて、その時点の
    (fgr: 前景RGB, pha: アルファ) を合成した RGBA(H,W,4) uint8 を返す。
    target_indexより後のフレームは（因果モデルのため）結果に影響しないので処理しない。
    戻り値: (rgba: np.ndarray HxWx4 uint8, elapsed_sec)
    """
    import torch

    if not frames_rgb:
        raise ValueError("frames_rgbが空です（クリップを取得できなかった可能性があります）")
    target_index = max(0, min(int(target_index), len(frames_rgb) - 1))
    h, w = frames_rgb[0].shape[:2]
    model = load_rvm_model()
    dr = downsample_ratio if downsample_ratio is not None else rvm_auto_downsample_ratio(h, w)

    rec = [None, None, None, None]
    result_fgr = result_pha = None
    t0 = time.time()
    with torch.no_grad():
        for i in range(target_index + 1):
            frame = np.ascontiguousarray(frames_rgb[i])
            src = torch.from_numpy(frame).permute(2, 0, 1).float().div(255.0).unsqueeze(0)
            fgr, pha, *rec = model(src, *rec, downsample_ratio=dr)
            if i == target_index:
                result_fgr, result_pha = fgr, pha
    elapsed = time.time() - t0

    fgr_np = (result_fgr[0].clamp(0.0, 1.0).permute(1, 2, 0).numpy() * 255.0).round().astype(np.uint8)
    pha_np = (result_pha[0, 0].clamp(0.0, 1.0).numpy() * 255.0).round().astype(np.uint8)
    rgba = np.dstack([fgr_np, pha_np])
    return rgba, elapsed


def extract_alpha_rvm(video_path, freeze_time_sec, W, H,
                       pre_sec=RVM_PRE_SEC_DEFAULT, post_sec=RVM_POST_SEC_DEFAULT,
                       downsample_ratio=None, fps=None):
    """
    動画ファイルから前後クリップを切り出し、RVMでフリーズ時刻のアルファマットを得る
    （render.py用。render.pyは既に動画ファイルを持っているのでこちらを直接呼ぶ）。
    戻り値: (rgba: np.ndarray HxWx4 uint8, elapsed_sec)  ※elapsedsecはクリップ切り出し込み
    """
    if fps is None:
        fps = probe_video_fps(video_path)
    clip_start = max(0.0, freeze_time_sec - pre_sec)
    target_index = int(round((freeze_time_sec - clip_start) * fps))

    t0 = time.time()
    frames_bgr = extract_clip_frames_bgr(video_path, clip_start, freeze_time_sec + post_sec, W, H, fps)
    if not frames_bgr:
        raise RuntimeError(f"動画からクリップを取得できませんでした（t={freeze_time_sec:.2f}s）")
    frames_rgb = [f[:, :, ::-1] for f in frames_bgr]
    rgba, infer_elapsed = rvm_infer_alpha(frames_rgb, target_index, downsample_ratio)
    elapsed = time.time() - t0
    log(f"  （クリップ切り出し: {elapsed - infer_elapsed:.2f}秒 / RVM推論: {infer_elapsed:.2f}秒 / "
        f"クリップ{len(frames_rgb)}フレーム中{target_index + 1}フレーム目まで処理）")
    return rgba, elapsed


def extract_to_files_video(video_path, freeze_time_sec, out_dir, W, H,
                            pre_sec=RVM_PRE_SEC_DEFAULT, post_sec=RVM_POST_SEC_DEFAULT,
                            downsample_ratio=None):
    """CLI本体（動画モード）。動画ファイル1本＋フリーズ時刻を処理し、固定形式のファイル
    一式をout_dirに書き出す（extract_to_filesの動画版。出力ファイルの契約は同一）。"""
    rgba, elapsed = extract_alpha_rvm(video_path, freeze_time_sec, W, H, pre_sec, post_sec, downsample_ratio)

    metadata = {
        "extractor": RVM_MODEL_NAME,
        "refine": None,
        "decontaminate": False,
        "input": os.path.abspath(video_path),
        "size": [W, H],
        "elapsedSec": round(elapsed, 3),
        "createdAt": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "freezeTimeSec": round(freeze_time_sec, 3),
        "preSec": pre_sec,
        "postSec": post_sec,
    }
    write_extract_files(rgba, out_dir, metadata)

    log(f"処理時間: {elapsed:.2f}秒（モデル={RVM_MODEL_NAME}, t={freeze_time_sec:.2f}s）")
    log(f"出力: {out_dir}")
    return metadata


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="画像/動画から被写体を自動切り抜きし、アルファマット（連続値のアルファチャンネル）を得る")
    parser.add_argument("input", help="入力（静止画モード: 画像ファイル。動画モード: 動画ファイル＋--video-time）")
    parser.add_argument("--out", required=True, help="出力ディレクトリ")
    parser.add_argument("--model", default=DEFAULT_MODEL, choices=VALID_MODELS,
                         help=f"切り抜きモデル（既定: {DEFAULT_MODEL}。{RVM_MODEL_NAME}を指定すると動画モードになる）")
    parser.add_argument("--refine", default=None, choices=VALID_REFINES,
                         help="境界精密化（vitmatte: rembg組み込みのViTMatte。静止画モードのみ）")
    parser.add_argument("--decontaminate", action="store_true",
                         help="半透明境界の背景色にじみを除去する（静止画モードのみ。動画モードはRVMのfgr出力で常に相当の処理が行われる）")
    parser.add_argument("--video-time", type=float, default=None,
                         help="動画モード：切り抜き対象のフリーズ時刻（秒）。--model rvm-mobilenetv3 と併用する")
    parser.add_argument("--output-width", type=int, default=None, help="動画モード：出力解像度の幅")
    parser.add_argument("--output-height", type=int, default=None, help="動画モード：出力解像度の高さ")
    parser.add_argument("--pre-sec", type=float, default=RVM_PRE_SEC_DEFAULT,
                         help=f"動画モード：フリーズ時刻より前に遡る秒数（既定{RVM_PRE_SEC_DEFAULT}）")
    parser.add_argument("--post-sec", type=float, default=RVM_POST_SEC_DEFAULT,
                         help=f"動画モード：フリーズ時刻より後に残す余白秒数（既定{RVM_POST_SEC_DEFAULT}）")
    args = parser.parse_args(argv)

    if not os.path.exists(args.input):
        parser.error(f"入力ファイルが見つかりません: {args.input}")

    if args.model == RVM_MODEL_NAME:
        if args.video_time is None:
            parser.error(f"--model {RVM_MODEL_NAME}（動画モード）には --video-time が必要です")
        if not args.output_width or not args.output_height:
            parser.error(f"--model {RVM_MODEL_NAME}（動画モード）には --output-width/--output-height が必要です")
        extract_to_files_video(args.input, args.video_time, args.out,
                                args.output_width, args.output_height,
                                args.pre_sec, args.post_sec)
    else:
        extract_to_files(args.input, args.out, args.model, args.refine, args.decontaminate)
    return 0


if __name__ == "__main__":
    sys.exit(main())
