#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
extract.py — 画像から被写体を自動で切り抜き、アルファマット
（0/1ではない連続値の半透明境界を含むアルファチャンネル）を得る、
独立実行可能なモジュール。

他プロジェクトからもそのまま使えるよう、依存は requirements-extract.txt に
分離し、出力はファイル名・形式を固定した「契約」として扱う。
render.py はこのファイルの extract_alpha() を直接importして使う
（rembg自体のimportは実際に呼ばれるまで遅延するため、mask:"brush"のみの
プロジェクトではrembg/onnxruntimeが未インストールでもrender.pyは問題なく動く）。

使い方:
    python extract.py input.png --out outdir/ \
        [--model birefnet-portrait|birefnet-general-lite|isnet-general-use] \
        [--refine vitmatte] [--decontaminate]

出力（outdir/ 配下、固定のファイル名。他プロジェクトと共有する契約）:
    subject-rgba.png   前景RGB＋アルファ（透過PNG）
    alpha.png          アルファチャンネルのみ（8bitグレースケール、0〜255の連続値）
    mask.png           アルファ>=128 の2値マスク（0 または 255）
    preview.png        チェッカー背景に合成した確認用プレビュー
    metadata.json       {"extractor": "...", "refine": "..."|null,
                          "decontaminate": bool, "input": "...",
                          "size": [w, h], "elapsedSec": float, "createdAt": "..."}

依存: rembg（MIT）, onnxruntime, numpy, pillow（requirements-extract.txt参照）
"""

import argparse
import datetime
import json
import os
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
# 既定にしている（README参照。実測値の詳細もそちらに記載）。
DEFAULT_MODEL = "isnet-general-use"
VALID_MODELS = ("birefnet-portrait", "birefnet-general-lite", "isnet-general-use")
VALID_REFINES = ("vitmatte",)


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


def extract_to_files(input_path, out_dir, model_name=DEFAULT_MODEL, refine=None, decontaminate=False):
    """CLI本体。入力画像1枚を処理し、固定形式のファイル一式をout_dirに書き出す。"""
    os.makedirs(out_dir, exist_ok=True)
    img = Image.open(input_path)
    if img.mode != "RGB":
        img = img.convert("RGB")

    rgba_img, elapsed, _session = extract_alpha(img, model_name, refine, decontaminate)
    rgba = np.array(rgba_img)

    rgba_img.save(os.path.join(out_dir, "subject-rgba.png"))
    Image.fromarray(rgba[:, :, 3], mode="L").save(os.path.join(out_dir, "alpha.png"))
    mask = np.where(rgba[:, :, 3] >= 128, 255, 0).astype(np.uint8)
    Image.fromarray(mask, mode="L").save(os.path.join(out_dir, "mask.png"))
    Image.fromarray(build_preview(rgba)).save(os.path.join(out_dir, "preview.png"))

    metadata = {
        "extractor": f"rembg-{model_name}",
        "refine": refine,
        "decontaminate": bool(decontaminate),
        "input": os.path.abspath(input_path),
        "size": [img.width, img.height],
        "elapsedSec": round(elapsed, 3),
        "createdAt": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    with open(os.path.join(out_dir, "metadata.json"), "w", encoding="utf-8") as f:
        json.dump(metadata, f, ensure_ascii=False, indent=2)

    log(f"処理時間: {elapsed:.2f}秒（モデル={model_name}, refine={refine}, decontaminate={decontaminate}）")
    log(f"出力: {out_dir}")
    return metadata


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="画像から被写体を自動切り抜きし、アルファマット（連続値のアルファチャンネル）を得る")
    parser.add_argument("input", help="入力画像（PNG/JPEG等）")
    parser.add_argument("--out", required=True, help="出力ディレクトリ")
    parser.add_argument("--model", default=DEFAULT_MODEL, choices=VALID_MODELS,
                         help=f"切り抜きモデル（既定: {DEFAULT_MODEL}）")
    parser.add_argument("--refine", default=None, choices=VALID_REFINES,
                         help="境界精密化（vitmatte: rembg組み込みのViTMatte）")
    parser.add_argument("--decontaminate", action="store_true",
                         help="半透明境界の背景色にじみを除去する")
    args = parser.parse_args(argv)

    if not os.path.exists(args.input):
        parser.error(f"入力画像が見つかりません: {args.input}")

    extract_to_files(args.input, args.out, args.model, args.refine, args.decontaminate)
    return 0


if __name__ == "__main__":
    sys.exit(main())
