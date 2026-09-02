#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
render.py の品質回帰テスト。

実機で書き出した output.mp4 の解析で見つかった以下の不具合の回帰を検証する:
  1. 音声が10msごとの無音の隙間でブツ切れになる（numpyでの切り貼り境界のズレ）
  2. 可変フレームレート(VFR)の入力で映像と音声がずれる
  3. name を入れてもテロップが描かれない（フォント読み込み失敗の黙殺）
  4. ブラシ完了後も白い絵の具が不透明のまま残る（フェードアウトが無い）
  5. assets/sfx/ の効果音を外部ファイルに差し替えられる

実行方法:
    python make_dummy.py   # 初回のみ（assets/examples のテスト用アセットを生成）
    python tests/render_quality.test.py
"""

import os
import shutil
import subprocess
import sys
import tempfile
import time

import cv2
import numpy as np

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

import render  # noqa: E402

passed = 0
failed = 0


def check(cond, label):
    global passed, failed
    if cond:
        passed += 1
        print("  ok - " + label)
    else:
        failed += 1
        print("  NG - " + label)


def ensure_fixtures():
    """必要なテスト用アセットが無ければ make_dummy.py の生成関数で作る"""
    import make_dummy as md

    os.makedirs(md.SFX_DIR, exist_ok=True)
    os.makedirs(md.BRUSH_DIR, exist_ok=True)
    os.makedirs(md.FONT_DIR, exist_ok=True)
    md.ensure_sfx(os.path.join(md.SFX_DIR, "shakin.wav"), md.synth_shakin, "shakin.wav")
    md.ensure_sfx(os.path.join(md.SFX_DIR, "don.wav"), md.synth_don, "don.wav")
    if not os.path.exists(os.path.join(md.BRUSH_DIR, "round.png")):
        md.generate_brush_tips()
    md.ensure_font()
    md.ensure_title_font()

    video_path = os.path.join(md.EXAMPLES_DIR, "dummy_input.mp4")
    if not os.path.exists(video_path):
        md.render_dummy_video(video_path)
        md.mux_audio_into_video(video_path, md.make_tone_track())

    vfr_path = os.path.join(md.EXAMPLES_DIR, "dummy_input_vfr.mp4")
    if not os.path.exists(vfr_path):
        md.render_dummy_video_vfr(vfr_path)
        md.mux_audio_into_video(vfr_path, md.make_tone_track())

    opaque_logo_png = os.path.join(md.EXAMPLES_DIR, "store_logo_opaque_black.png")
    opaque_logo_jpg = os.path.join(md.EXAMPLES_DIR, "store_logo_opaque_black.jpg")
    if not os.path.exists(opaque_logo_png) or not os.path.exists(opaque_logo_jpg):
        md.gen_dummy_logo_opaque_black(opaque_logo_png, jpeg_path=opaque_logo_jpg)

    return video_path, vfr_path


def decode_wav(path, sr=48000):
    cmd = ["ffmpeg", "-v", "error", "-i", path, "-vn",
           "-f", "f32le", "-acodec", "pcm_f32le", "-ac", "2", "-ar", str(sr), "pipe:1"]
    out = subprocess.run(cmd, capture_output=True).stdout
    data = np.frombuffer(out, np.float32)
    usable = (len(data) // 2) * 2
    return data[:usable].reshape(-1, 2)


def find_silent_runs(data, sr=48000, win_sec=0.01, db_threshold=-60.0):
    """10ms窓ごとのRMS音量が db_threshold 未満の区間を(開始秒, 継続秒)のリストで返す"""
    win = int(sr * win_sec)
    if win <= 0 or len(data) < win:
        return []
    n_win = len(data) // win
    mono = data[:, 0].astype(np.float64) * 0.5 + data[:, 1].astype(np.float64) * 0.5
    rms = np.sqrt(np.mean((mono[:n_win * win].reshape(n_win, win)) ** 2, axis=1))
    db = 20 * np.log10(np.maximum(rms, 1e-9))
    silent = db < db_threshold
    runs = []
    i = 0
    while i < len(silent):
        if silent[i]:
            j = i
            while j < len(silent) and silent[j]:
                j += 1
            runs.append((i * win_sec, (j - i) * win_sec))
            i = j
        else:
            i += 1
    return runs


def sample_project(video_filename, extra_freeze_kwargs=None):
    """render.pyへ直接渡せる、フリーズ2件の最小プロジェクト定義を作る"""
    fz1 = {"time": 2.5, "name": "赤い人", "sfx": "shakin",
           "strokes": [{"width": 0.1, "points": [[0.3, 0.35], [0.33, 0.3], [0.36, 0.35]]}]}
    fz2 = {"time": 5.5, "name": "BLUE GUY", "sfx": "don",
           "strokes": [{"width": 0.09, "points": [[0.68, 0.55], [0.7, 0.5], [0.72, 0.55]]}]}
    if extra_freeze_kwargs:
        fz1.update(extra_freeze_kwargs)
        fz2.update(extra_freeze_kwargs)
    return {
        "version": 1,
        "video": video_filename,
        "output": {"width": 540, "height": 960, "fps": 30},
        "style": {
            "freeze_sec": 1.5,
            "brush_anim_sec": 0.5,
            "font": "assets/fonts/NotoSansJP-Bold.ttf",
            "title_font": "assets/fonts/Anton-Regular.ttf",
            "title_font_jp": "assets/fonts/NotoSansJP-Bold.ttf",
        },
        "freezes": [fz1, fz2],
    }


def render_direct(project_dict, video_path, out_path, tmpdir):
    """
    project_dict（style/freezesが未マージの生のプロジェクト定義）を一時JSONに書き出し、
    render.load_project() で正規のマージ済み構造にしてから render.render() を呼ぶ
    （CLIのmain()と同じ経路を通すことで、実際の使われ方と処理内容を一致させる）。
    """
    import json as json_mod
    json_path = os.path.join(tmpdir, f"project_{os.path.basename(out_path)}.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json_mod.dump(project_dict, f, ensure_ascii=False)
    loaded = render.load_project(json_path)
    return render.render(loaded, json_path, video_path, out_path)


print("=== フィクスチャの確認・準備 ===")
video_path, vfr_video_path = ensure_fixtures()
check(os.path.exists(video_path), "examples/dummy_input.mp4 が存在する")
check(os.path.exists(vfr_video_path), "examples/dummy_input_vfr.mp4 が存在する")

tmpdir = tempfile.mkdtemp(prefix="render_quality_test_")
try:
    # ------------------------------------------------------------------
    # 1) 音声の連続性：固定フレームレート(CFR)入力
    # ------------------------------------------------------------------
    print("")
    print("=== 音声の連続性（CFR入力）：フリーズ区間以外に細切れの無音が無いこと ===")
    project = sample_project("dummy_input.mp4")
    out_cfr = os.path.join(tmpdir, "audio_cfr.mp4")
    render_direct(project, video_path, out_cfr, tmpdir)

    data = decode_wav(out_cfr)
    check(len(data) > 0, "音声トラックをデコードできる")
    runs = find_silent_runs(data)
    n_freezes = len(project["freezes"])
    print(f"  検出した無音区間（10ms窓、-60dB未満）: {len(runs)}件 {runs}")
    check(len(runs) <= 2 * n_freezes,
          f"無音区間の件数がフリーズ数から想定される範囲内（<= {2 * n_freezes}件）: {len(runs)}件"
          "（大量の細切れ無音＝ブツ切れバグが無いことの確認）")
    # フリーズ1回分の実際の長さ（秒）は、①塗り②ズレ③静止＋固定の pre/slide_back から
    # plan_freezes が計算する（旧freeze_secは③静止(hold_sec)の秒数として読み替えられるため、
    # 単純に「freeze_sec = フリーズ全体の長さ」という以前の前提はもう成り立たない）。
    info_for_plan = render.probe_video(video_path)
    plans_for_silence = render.plan_freezes(
        [dict(render.DEFAULT_STYLE, **project["style"], **fz) for fz in project["freezes"]],
        30.0, int(round(info_for_plan["duration"] * 30.0)))
    total_freeze_sec = sum(p["n_total"] for p in plans_for_silence) / 30.0
    max_freeze_sec = max(p["n_total"] for p in plans_for_silence) / 30.0
    check(all(dur <= max_freeze_sec + 0.5 for _, dur in runs),
          "各無音区間の長さがフリーズ1回分の長さを大きく超えない（フリーズ以外の場所が無音化していない）")

    total_silence = sum(dur for _, dur in runs)
    # フリーズ区間の大半は無音だが、効果音（今回はリバーブ尾を含め1秒前後と長め）が
    # 鳴っている間は意図的に無音ではない。厳密な一致ではなく、
    # 「フリーズ総時間より明らかに短い（＝効果音が実際に鳴って無音を削っている）」
    # 「フリーズ総時間の半分は下回らない（＝大半はまだ無音のまま）」の範囲で検証する。
    check(0 < total_silence < total_freeze_sec,
          f"効果音が鳴る分、無音合計はフリーズ総時間より短い: "
          f"無音={total_silence:.2f}s / フリーズ総時間={total_freeze_sec:.2f}s")
    check(total_silence > total_freeze_sec * 0.3,
          f"それでも大半はまだ無音のまま（効果音だけで大部分が埋まってはいない）: "
          f"無音={total_silence:.2f}s / フリーズ総時間={total_freeze_sec:.2f}s")

    # ------------------------------------------------------------------
    # 2) 可変フレームレート(VFR)入力の正規化
    # ------------------------------------------------------------------
    print("")
    print("=== 可変フレームレート(VFR)入力：is_vfr検出・正規化・音声連続性・尺 ===")
    vfr_info = render.probe_video(vfr_video_path)
    check(vfr_info["is_vfr"], f"VFRダミー動画がis_vfr=Trueと判定される（平均{vfr_info['fps']:.2f}fps "
          f"/ 基準{vfr_info['r_fps']:.2f}fps）")

    normalize_tmpdir = tempfile.mkdtemp(dir=tmpdir)
    normalized_path = render.normalize_frame_rate(vfr_video_path, 30.0, vfr_info["has_audio"], normalize_tmpdir)
    normalized_info = render.probe_video(normalized_path)
    check(not normalized_info["is_vfr"], "normalize_frame_rateの出力自体は固定フレームレートになっている")
    check(abs(normalized_info["duration"] - vfr_info["duration"]) < 0.5,
          "正規化してもVFR入力自体の尺（duration）は大きく変わらない: "
          f"入力{vfr_info['duration']:.2f}s -> 正規化後{normalized_info['duration']:.2f}s")

    project_vfr = sample_project("dummy_input_vfr.mp4")
    out_vfr = os.path.join(tmpdir, "audio_vfr.mp4")
    render_direct(project_vfr, vfr_video_path, out_vfr, tmpdir)
    vfr_out_info = render.probe_video(out_vfr)
    check(not vfr_out_info["is_vfr"], "VFR入力からのフルレンダリング結果も固定フレームレートになっている")

    data_vfr = decode_wav(out_vfr)
    runs_vfr = find_silent_runs(data_vfr)
    print(f"  検出した無音区間（VFR入力・正規化後）: {len(runs_vfr)}件 {runs_vfr}")
    check(len(runs_vfr) <= 2 * n_freezes,
          f"VFR入力でも無音区間の件数が想定範囲内（<= {2 * n_freezes}件、ブツ切れが起きていない）: "
          f"{len(runs_vfr)}件")

    out_cfr_info = render.probe_video(out_cfr)
    check(abs(out_cfr_info["duration"] - vfr_out_info["duration"]) < 1.0,
          "同じプロジェクト定義なら、CFR入力とVFR入力（正規化後）で出力の尺がほぼ一致する: "
          f"CFR={out_cfr_info['duration']:.2f}s / VFR={vfr_out_info['duration']:.2f}s")

    # ------------------------------------------------------------------
    # 3) テロップ描画：name を入れた全フリーズで画素が変化している
    # ------------------------------------------------------------------
    print("")
    print("=== テロップ描画：nameが空でない全フリーズでテロップ領域の画素が変化する ===")
    info = render.probe_video(video_path)
    W, H, fps = 540, 960, 30.0
    src_frames = int(round(info["duration"] * fps))
    style = dict(render.DEFAULT_STYLE)
    style.update(project.get("style") or {})
    freezes = []
    for fz in project["freezes"]:
        merged = dict(style)
        merged.update(fz)
        merged["strokes"] = fz.get("strokes") or []
        merged["time"] = float(fz.get("time", 0.0))
        merged["name"] = fz.get("name", "")
        merged["sfx"] = fz.get("sfx")
        freezes.append(merged)
    plans = render.plan_freezes(freezes, fps, src_frames)
    for plan in plans:
        fz = plan["fz"]
        frame = render.grab_frame_at(video_path, plan["frame_index"] / fps, W, H, fps)
        frames = list(render.iter_freeze_frames(frame, plan, W, H, fps, {}))
        done_idx = plan["n_pre"] + plan["n_reveal"]
        no_telop_frame = frames[done_idx]        # ブラシ完了直後、テロップフェードイン前
        telop_frame = frames[-1]                  # フリーズ末尾、テロップ表示済み
        diff = int(np.abs(no_telop_frame.astype(int) - telop_frame.astype(int)).sum())
        check(diff > 0, f"name={fz['name']!r} のフリーズで、テロップ表示前後に画素の変化がある"
              f"（diff={diff}）")

    # ------------------------------------------------------------------
    # 4) ブラシの白フェード（brush_fade_sec）
    # ------------------------------------------------------------------
    print("")
    print("=== ブラシ完了後の白フェード（brush_fade_sec） ===")
    for label, fade_sec in (("既定(0.3)", None), ("無効化(0)", 0.0)):
        fz = dict(freezes[0])
        # このテストは brush_fade_sec 単体の挙動を見るためのものなので、
        # 既定で有効になった影のスライドイン演出（無関係な画素変化の原因になる）は
        # 明示的に無効化しておく。
        fz["shadow"] = None
        if fade_sec is not None:
            fz["brush_fade_sec"] = fade_sec
        plan = render.plan_freezes([fz], fps, src_frames)[0]
        frame = render.grab_frame_at(video_path, plan["frame_index"] / fps, W, H, fps)
        fz_no_name = dict(fz)
        fz_no_name["name"] = ""  # テロップの変化と混同しないよう名前を消して分離する
        plan_no_name = render.plan_freezes([fz_no_name], fps, src_frames)[0]
        frames = list(render.iter_freeze_frames(frame, plan_no_name, W, H, fps, {}))
        done_idx = plan_no_name["n_pre"] + plan_no_name["n_reveal"]
        first_rest = frames[done_idx]
        last_rest = frames[-1]
        diff = int(np.abs(first_rest.astype(int) - last_rest.astype(int)).sum())
        if fade_sec == 0.0:
            check(diff == 0, f"brush_fade_sec={label}: フェード無効時は完了直後と末尾のフレームが同一"
                  f"（白フェードのアニメーションが発生しない）: diff={diff}")
        else:
            check(diff > 0, f"brush_fade_sec={label}: 完了直後は白い絵の具が残り、末尾では消えている"
                  f"（diff={diff}）")

    # ------------------------------------------------------------------
    # 5) フォント読み込み失敗時はエラー終了する（黙って握りつぶさない）
    # ------------------------------------------------------------------
    print("")
    print("=== フォント読み込み失敗時はエラー終了する ===")
    raised = False
    try:
        render.render_telop_layer("テスト", 200, 100, None, "assets/fonts/存在しないフォント.ttf", 24)
    except RuntimeError:
        raised = True
    check(raised, "font=None かつ text非空でrender_telop_layerがRuntimeErrorを送出する")

    raised_pipeline = False
    err_msg = ""
    try:
        broken_project = sample_project("dummy_input.mp4")
        broken_project["style"]["font"] = "assets/fonts/存在しないフォント.ttf"
        broken_project["style"]["title_font"] = "assets/fonts/存在しないフォント.ttf"
        broken_project["style"]["title_font_jp"] = "assets/fonts/存在しないフォント.ttf"
        render_direct(broken_project, video_path, os.path.join(tmpdir, "should_not_exist.mp4"), tmpdir)
    except RuntimeError as exc:
        raised_pipeline = True
        err_msg = str(exc)
    check(raised_pipeline, "存在しないフォントを指定したプロジェクト全体のrender()もRuntimeErrorで停止する: "
          + err_msg[:60])

    # ------------------------------------------------------------------
    # 5.5) GitHub Actions相当のディレクトリ構成でもアセットが解決できる
    #      （render.yml は project.json を含むjobブランチをcwdにcheckoutし、
    #       spotlight-reel は別のサブディレクトリにcloneする。この構成でcwd基準の
    #       パス解決に頼っていると、assets/fonts/等が見つからずテロップが描けない）
    # ------------------------------------------------------------------
    print("")
    print("=== CI相当のレイアウト：cwdがrender.pyの置き場所と無関係でもアセットが解決できる ===")
    unrelated_cwd = tempfile.mkdtemp(prefix="unrelated_cwd_")  # assetsもrender.pyも存在しない場所
    old_cwd = os.getcwd()
    try:
        os.chdir(unrelated_cwd)
        out_ci_layout = os.path.join(tmpdir, "ci_layout.mp4")
        render_direct(sample_project("dummy_input.mp4"), video_path, out_ci_layout, tmpdir)
    finally:
        os.chdir(old_cwd)
        shutil.rmtree(unrelated_cwd, ignore_errors=True)
    check(os.path.exists(out_ci_layout) and os.path.getsize(out_ci_layout) > 0,
          "無関係なcwdから実行してもレンダリングが成功する"
          "（フォント/効果音のパス解決がcwdに依存せず、render.py自身の置き場所を基準にする）")
    ci_layout_info = render.probe_video(out_ci_layout)
    check(ci_layout_info["duration"] > 0, f"CI相当レイアウトでの出力の尺が正常: {ci_layout_info['duration']:.2f}s")

    # ------------------------------------------------------------------
    # 6.7) 「影」演出（フィルム色統合・人物スライドで影を見せる新方式）
    #      shadowは「差分ベース」で検証する：shadow付き/無しで着地後フレームを作り、
    #      スライド距離ぶんだけ離れた位置（本来は背景のまま）で画素が変化していることを見る。
    # ------------------------------------------------------------------
    print("")
    print("=== shadow演出：人物をスライドさせ、元の位置に残る影が指定方向に見える ===")
    shadow_cfg = {"color": "#000000", "alpha": 0.9, "distance": 0.08, "direction": "right",
                  "offset_y": 0.0, "blur": 0.0, "slide_sec": 0.3}
    base_style = dict(render.DEFAULT_STYLE)
    # brush_fade_sec=0：ブラシ完了直後の「乾いていない白い絵の具」の余韻を無効化する。
    # 影のテストは人物の元の位置（＝ストロークの位置そのもの）を画素比較するため、
    # 有効なままだとその余韻（白フェード）が同じ位置に重なってしまい判定を邪魔する。
    base_style.update({"freeze_sec": 1.0, "brush_anim_sec": 0.2, "background": "mono", "brush_fade_sec": 0})

    def make_dot_freeze(shadow=None, extra=None):
        fz = dict(base_style)
        fz.update({
            "time": 2.5, "name": "", "sfx": None,
            "strokes": [{"width": 0.1, "points": [[0.5, 0.5], [0.5, 0.5]]}],
            "shadow": shadow,
        })
        if extra:
            fz.update(extra)
        return fz

    def render_freeze_frames(fz):
        plan = render.plan_freezes([fz], fps, src_frames)[0]
        frame = render.grab_frame_at(video_path, plan["frame_index"] / fps, W, H, fps)
        frames = list(render.iter_freeze_frames(frame, plan, W, H, fps, {}))
        return plan, frames

    plan_shadow, frames_shadow = render_freeze_frames(make_dot_freeze(shadow=shadow_cfg))
    _plan_noshadow, frames_noshadow = render_freeze_frames(make_dot_freeze(shadow=None))
    landed_idx = plan_shadow["n_pre"] + plan_shadow["n_reveal"] + plan_shadow["n_slide_in"]
    frame_with_shadow = frames_shadow[landed_idx]
    frame_no_shadow = frames_noshadow[plan_shadow["n_pre"] + plan_shadow["n_reveal"]]

    check(plan_shadow["n_slide_in"] > 0, "shadowを指定するとn_slide_in（スライドインのフレーム数）が0より大きい")
    check(plan_shadow["n_slide_back"] > 0, "shadowを指定するとn_slide_back（スライドバックのフレーム数）が0より大きい")

    # 影は人物の「元の位置」（ドットの中心 0.5,0.5）に静止したまま残る。人物自身は
    # distance(0.08)*Wだけ右にスライドするため、元の位置では着地後、人物ではなく
    # 影（指定色をalphaで重ねたもの）が見えているはず。素の背景（影も人物も無い状態）
    # と比較することで、「影の色が乗って暗くなっている」ことを正しく検証する。
    bg_probe_frame = render.grab_frame_at(video_path, plan_shadow["frame_index"] / fps, W, H, fps)
    plain_bg = render.make_background(bg_probe_frame, "mono", 1.0)
    dx = int(shadow_cfg["distance"] * W)
    origin_probe = (int(0.5 * H), int(0.5 * W))          # 人物の元の位置＝影が見えるはず
    slid_probe = (int(0.5 * H), int(0.5 * W) + dx)        # 人物のスライド先＝人物の色が見えるはず
    far_probe = (10, 10)                                   # 影・人物どちらの影響も受けないはずの隅

    origin_bg = plain_bg[origin_probe].astype(int)
    origin_shadow = frame_with_shadow[origin_probe].astype(int)
    diff_origin_from_bg = int(np.abs(origin_bg - origin_shadow).sum())
    check(diff_origin_from_bg > 20,
          f"人物の元の位置は、影が無い場合の素の背景と異なる画素になっている（diff={diff_origin_from_bg}）")
    check(bool(np.all(origin_shadow <= origin_bg + 1)),
          "元の位置に残った影の画素は、素の背景より暗い（指定色（黒）をalphaで重ねているため）")

    diff_slid_area = int(np.abs(
        frame_no_shadow[slid_probe].astype(int) - frame_with_shadow[slid_probe].astype(int)).sum())
    check(diff_slid_area > 20,
          f"人物はdistance方向のスライド先に実際に現れている（shadow無効時はそこに何も無い, diff={diff_slid_area}）")

    diff_far_area = int(np.abs(
        frame_no_shadow[far_probe].astype(int) - frame_with_shadow[far_probe].astype(int)).sum())
    check(diff_far_area == 0,
          f"影・人物から離れた隅の画素はshadow有無で変化しない（diff={diff_far_area}）")

    print("")
    print("=== shadow演出：スライドインは瞬時ではなく、複数フレームにわたる連続アニメーション ===")
    early_i = 0
    late_i = plan_shadow["n_slide_in"] - 1
    check(early_i < late_i, "テスト前提：スライドインが複数フレームある（n_slide_inが小さすぎない）")
    frame_early = frames_shadow[plan_shadow["n_pre"] + plan_shadow["n_reveal"] + early_i]
    frame_late = frames_shadow[plan_shadow["n_pre"] + plan_shadow["n_reveal"] + late_i]
    diff_early = int(np.abs(
        frame_no_shadow[origin_probe].astype(int) - frame_early[origin_probe].astype(int)).sum())
    diff_late = int(np.abs(
        frame_no_shadow[origin_probe].astype(int) - frame_late[origin_probe].astype(int)).sum())
    check(diff_late >= diff_early,
          f"スライドイン序盤(diff={diff_early})より終盤(diff={diff_late})のほうが影がしっかり見えている"
          "（Ease-Out Expoで段階的にスライドしている）")

    print("")
    print("=== shadow演出：効果音は「着地の瞬間」（hold+brush+slide_in後）に鳴る ===")
    fz_sfx = make_dot_freeze(shadow=shadow_cfg, extra={"sfx": "shakin"})
    plan_sfx = render.plan_freezes([fz_sfx], fps, src_frames)[0]
    check(plan_sfx["n_slide_in"] == plan_shadow["n_slide_in"],
          "sfx指定してもn_slide_inの計算は変わらない")
    audio = render.build_audio(video_path, [plan_sfx], fps, src_frames, True)
    landed_frame_offset = plan_sfx["n_pre"] + plan_sfx["n_reveal"] + plan_sfx["n_slide_in"]
    expected_sample = render.frames_to_samples(plan_sfx["frame_index"] + landed_frame_offset, fps, render.AUDIO_SR)
    # 効果音(shakin.wav)そのものの立ち上がり位置を探し、期待位置に近いことを確認する
    # （無音区間の判定と同じ考え方：しきい値を超える最初のサンプルを「発火位置」とみなす）
    window = audio[max(0, expected_sample - render.AUDIO_SR // 2):expected_sample + render.AUDIO_SR]
    check(bool(np.any(np.abs(window) > 0.02)),
          f"着地予定位置({expected_sample}サンプル)付近に効果音の音量が実際に存在する")

    print("")
    print("=== shadow演出：旧film_offset/film_color/film_alphaは後方互換で読み替えられる ===")
    # make_dot_freeze(shadow=None) は "shadow" キーを明示的にNoneにする（＝新仕様での明示的な
    # 無効化）ため、ここでは使えない。後方互換の読み替えが効くのは「"shadow" キー自体が
    # 無い」場合だけなので、base_style（"shadow"キーを含まない）から直接組み立てる。
    legacy_fz = dict(base_style)
    legacy_fz.update({
        "time": 2.5, "name": "", "sfx": None,
        "strokes": [{"width": 0.1, "points": [[0.5, 0.5], [0.5, 0.5]]}],
        "film_offset": [0.08, 0.0], "film_color": "#000000", "film_alpha": 0.9,
    })
    check("shadow" not in legacy_fz, "テスト前提：legacy_fzは'shadow'キー自体を持たない")
    legacy_cfg = render.resolve_shadow_config(legacy_fz)
    check(legacy_cfg is not None, "film_offsetが非ゼロならshadow未指定でも影設定が解決される（後方互換）")
    check(legacy_cfg is not None and legacy_cfg["direction"] == "right" and abs(legacy_cfg["distance"] - 0.08) < 1e-6,
          f"film_offset=[0.08, 0.0] は distance=0.08 / direction='right' に読み替えられる: {legacy_cfg}")
    _plan_legacy, frames_legacy = render_freeze_frames(legacy_fz)
    legacy_landed_idx = _plan_legacy["n_pre"] + _plan_legacy["n_reveal"] + _plan_legacy["n_slide_in"]
    frame_legacy = frames_legacy[legacy_landed_idx]
    diff_legacy = int(np.abs(
        frame_no_shadow[origin_probe].astype(int) - frame_legacy[origin_probe].astype(int)).sum())
    check(diff_legacy > 20, f"film_offsetのみを指定した旧JSONでも実際に影が描画される（diff={diff_legacy}）")

    zero_legacy_cfg = render.resolve_shadow_config(make_dot_freeze(shadow=None))
    check(zero_legacy_cfg is None, "film_offsetが[0,0]（既定）のままなら、shadowもNone（影演出なし。完全後方互換）")

    print("")
    print("=== shadow演出：'shadow'キー省略時は既定で有効になる（実機で影が出ない不具合の回帰防止） ===")
    no_shadow_key_fz = dict(base_style)
    no_shadow_key_fz.update({
        "time": 2.5, "name": "", "sfx": None,
        "strokes": [{"width": 0.1, "points": [[0.5, 0.5], [0.5, 0.5]]}],
    })
    check("shadow" not in no_shadow_key_fz, "テスト前提：'shadow'キー自体を持たない")

    default_cfg = render.resolve_shadow_config(no_shadow_key_fz)
    check(default_cfg is not None, "'shadow'キー省略時、resolve_shadow_configはNoneではなく既定設定を返す")
    check(default_cfg is not None
          and default_cfg["color"] == render.SHADOW_COLOR_DEFAULT
          and abs(default_cfg["alpha"] - render.SHADOW_ALPHA_DEFAULT) < 1e-9
          and abs(default_cfg["distance"] - render.SHADOW_DISTANCE_DEFAULT) < 1e-9
          and default_cfg["direction"] == "auto"
          and abs(default_cfg["offset_y"] - render.SHADOW_OFFSET_Y_DEFAULT) < 1e-9
          and default_cfg["blur"] == 0.0,
          f"既定設定の内容がSHADOW_*_DEFAULTと一致する: {default_cfg}")

    _plan_default, frames_default = render_freeze_frames(no_shadow_key_fz)
    check(_plan_default["n_slide_in"] > 0,
          "'shadow'キー省略時もn_slide_inが0より大きい（実際にスライド演出が動く）")
    default_landed = frames_default[_plan_default["n_pre"] + _plan_default["n_reveal"] + _plan_default["n_slide_in"]]
    diff_default_origin = int(np.abs(
        plain_bg[origin_probe].astype(int) - default_landed[origin_probe].astype(int)).sum())
    check(diff_default_origin > 20,
          f"'shadow'キー省略時も、実際にレンダリングすると影が描画される（diff={diff_default_origin}）")

    check(render.resolve_shadow_config(dict(no_shadow_key_fz, shadow=None)) is None,
          '"shadow": null は明示的に無効化される')
    check(render.resolve_shadow_config(dict(no_shadow_key_fz, shadow={"enabled": False})) is None,
          '"shadow": {"enabled": false} は明示的に無効化される')
    check(render.resolve_shadow_config(dict(no_shadow_key_fz, shadow={})) is not None,
          '"shadow": {} （enabledキー無し）は有効のまま既定値で埋められる')
    enabled_true_cfg = render.resolve_shadow_config(
        dict(no_shadow_key_fz, shadow={"enabled": True, "color": "#ABCDEF"}))
    check(enabled_true_cfg is not None and enabled_true_cfg["color"] == "#ABCDEF",
          '"shadow": {"enabled": true, "color": "#ABCDEF"} は指定値どおり有効になる: '
          f"{enabled_true_cfg}")
    check(abs(default_cfg["slide_sec"] - render.SHADOW_SLIDE_IN_SEC_DEFAULT) < 1e-9
          and abs(render.SHADOW_SLIDE_IN_SEC_DEFAULT - 0.5) < 1e-9,
          f"slide_secの既定値は0.5秒（ゆっくりめのスライド）: {default_cfg['slide_sec']}")

    print("")
    print("=== shadow演出：スライドはEase-Out Cubic（急停止ではなく滑らかに減速）で行われる ===")
    check(render.SHADOW_SLIDE_BACK_SEC == 0.25, "スライドバックの時間は0.25秒（既定0.1秒から変更）")
    check(render.ease_out_cubic(0) == 0.0 and render.ease_out_cubic(1) == 1.0,
          "ease_out_cubic: t=0で0、t=1で1")
    check(abs(render.ease_out_cubic(0.5) - (1 - 0.5 ** 3)) < 1e-9,
          "ease_out_cubic: t=0.5で 1-(1-0.5)^3 になる")
    early_gain = render.ease_out_cubic(0.3) - render.ease_out_cubic(0.0)
    late_gain = render.ease_out_cubic(1.0) - render.ease_out_cubic(0.7)
    check(early_gain > late_gain > 0.02,
          f"ease-outの形（序盤>終盤）は保ちつつ、終盤(0.7→1.0)にも十分な伸びが残る"
          f"（急停止しない）: early={early_gain:.4f} late={late_gain:.4f}")
    # 旧Ease-Out Expoと比べ、終盤の伸びが明らかに大きい（急停止ではなく滑らかに減速している証拠）
    def _ease_out_expo_reference(t):
        return 1.0 if t >= 1.0 else (0.0 if t <= 0.0 else 1.0 - 2.0 ** (-10.0 * t))
    expo_late_gain = _ease_out_expo_reference(1.0) - _ease_out_expo_reference(0.7)
    check(late_gain > expo_late_gain * 2,
          f"旧Ease-Out Expo（終盤の伸びexpo_late={expo_late_gain:.4f}）より、"
          f"新Ease-Out Cubic（late={late_gain:.4f}）のほうが終盤も滑らかに動き続ける")

    print("")
    print("=== shadow演出：direction='auto'は人物マスクの位置に応じて左右が反転する ===")

    def make_side_freeze(cx_ratio, direction="auto"):
        fz = dict(base_style)
        fz.update({
            "time": 2.5, "name": "", "sfx": None,
            "strokes": [{"width": 0.12, "points": [[cx_ratio, 0.5], [cx_ratio, 0.5]]}],
            "shadow": {"color": "#000000", "alpha": 0.9, "distance": 0.08, "direction": direction,
                       "offset_y": 0.0, "blur": 0.0, "slide_sec": 0.1},
        })
        return fz

    def landed_frame_for(fz):
        plan, frames = render_freeze_frames(fz)
        idx = plan["n_pre"] + plan["n_reveal"] + plan["n_slide_in"]
        return frames[idx]

    right_dummy_landed = landed_frame_for(make_side_freeze(0.8))    # 右寄りの人物
    left_dummy_landed = landed_frame_for(make_side_freeze(0.2))     # 左寄りの人物
    # 右寄りダミー：中心(cx=0.8*W)より右側に影が漏れているはず（右へスライドしたと分かる）
    right_probe = (int(0.5 * H), min(W - 1, int(0.8 * W) + dx))
    # 比較対象として、影を完全に無効化した同じ位置の人物フレームを作る
    _plan_right_ns, frames_right_ns = render_freeze_frames(make_dot_freeze(shadow=None, extra={
        "strokes": [{"width": 0.12, "points": [[0.8, 0.5], [0.8, 0.5]]}],
    }))
    right_no_shadow = frames_right_ns[_plan_right_ns["n_pre"] + _plan_right_ns["n_reveal"]]
    diff_right_at_right_probe = int(np.abs(
        right_no_shadow[right_probe].astype(int) - right_dummy_landed[right_probe].astype(int)).sum())
    check(diff_right_at_right_probe > 20,
          f"人物が右寄りのダミーでは、direction='auto'が'right'を選び、右側で影が見える（diff={diff_right_at_right_probe}）")

    _plan_left_ns, frames_left_ns = render_freeze_frames(make_dot_freeze(shadow=None, extra={
        "strokes": [{"width": 0.12, "points": [[0.2, 0.5], [0.2, 0.5]]}],
    }))
    left_no_shadow = frames_left_ns[_plan_left_ns["n_pre"] + _plan_left_ns["n_reveal"]]
    left_probe = (int(0.5 * H), max(0, int(0.2 * W) - dx))
    diff_left_at_left_probe = int(np.abs(
        left_no_shadow[left_probe].astype(int) - left_dummy_landed[left_probe].astype(int)).sum())
    check(diff_left_at_left_probe > 20,
          f"人物が左寄りのダミーでは、direction='auto'が'left'を選び、左側で影が見える（diff={diff_left_at_left_probe}）")

    # 単体関数レベルでも同じことを確認（マスク重心のみからの判定）
    right_mask = np.zeros((H, W), np.uint8)
    right_mask[:, int(0.75 * W):int(0.85 * W)] = 255
    left_mask = np.zeros((H, W), np.uint8)
    left_mask[:, int(0.15 * W):int(0.25 * W)] = 255
    center_mask = np.zeros((H, W), np.uint8)
    center_mask[:, int(0.48 * W):int(0.52 * W)] = 255
    check(render.resolve_shadow_auto_direction(right_mask, W) == "right",
          "resolve_shadow_auto_direction: 画面右寄りのマスクは'right'と判定される")
    check(render.resolve_shadow_auto_direction(left_mask, W) == "left",
          "resolve_shadow_auto_direction: 画面左寄りのマスクは'left'と判定される")
    check(render.resolve_shadow_auto_direction(center_mask, W) == "right",
          "resolve_shadow_auto_direction: 画面中心±5%以内のマスクは既定の'right'になる")

    # 単体関数だけでなく、実際のレンダリングパイプライン（plan_freezes→iter_freeze_frames）を
    # 通しても同じ結果になることを改めて確認する（中心ぴったりのダミー人物）
    center_dummy_landed = landed_frame_for(make_side_freeze(0.5))
    center_right_probe = (int(0.5 * H), min(W - 1, int(0.5 * W) + dx))
    _plan_center_ns, frames_center_ns = render_freeze_frames(make_dot_freeze(shadow=None, extra={
        "strokes": [{"width": 0.12, "points": [[0.5, 0.5], [0.5, 0.5]]}],
    }))
    center_no_shadow = frames_center_ns[_plan_center_ns["n_pre"] + _plan_center_ns["n_reveal"]]
    diff_center_at_right_probe = int(np.abs(
        center_no_shadow[center_right_probe].astype(int) - center_dummy_landed[center_right_probe].astype(int)).sum())
    check(diff_center_at_right_probe > 20,
          "実レンダリングでも、画面中心ぴったりのダミー人物ではdirection='auto'が既定の'right'を選び、"
          f"右側で影が見える（diff={diff_center_at_right_probe}）")

    print("")
    print("=== --preview：影のスライド前後で2枚のPNGを出力する ===")
    import json as json_mod
    preview_project = {
        "version": 1, "video": "dummy_input.mp4",
        "output": {"width": W, "height": H},
        "style": {"freeze_sec": 1.0, "brush_anim_sec": 0.2, "audio_during_freeze": "mute"},
        "freezes": [{
            "time": 2.5, "name": "",
            "strokes": [{"width": 0.1, "points": [[0.5, 0.5], [0.5, 0.5]]}],
            "shadow": shadow_cfg,
        }],
    }
    preview_json_path = os.path.join(tmpdir, "preview_project.json")
    with open(preview_json_path, "w", encoding="utf-8") as f:
        json_mod.dump(preview_project, f, ensure_ascii=False)
    loaded_preview = render.load_project(preview_json_path)
    preview_png_arg = os.path.join(tmpdir, "shadow_preview.png")
    before_path, after_path = render.render(loaded_preview, preview_json_path, video_path, "unused.mp4",
                                             preview_path=preview_png_arg)
    check(before_path == os.path.join(tmpdir, "shadow_preview_before.png"),
          f"スライド前PNGのパスが_before付きになる: {before_path}")
    check(after_path == os.path.join(tmpdir, "shadow_preview_after.png"),
          f"スライド後PNGのパスが_after付きになる: {after_path}")
    check(os.path.exists(before_path) and os.path.getsize(before_path) > 0, "スライド前PNGが実際に書き出される")
    check(os.path.exists(after_path) and os.path.getsize(after_path) > 0, "スライド後PNGが実際に書き出される")
    before_img = cv2.imread(before_path)
    after_img = cv2.imread(after_path)
    diff_preview = int(np.abs(before_img.astype(int) - after_img.astype(int)).sum())
    check(diff_preview > 0, f"スライド前後のPNGは見た目が異なる（影の有無, diff={diff_preview}）")

    # ------------------------------------------------------------------
    # 6.75) 演出の3段構成（①塗りreveal_sec→②ズレslide_sec→③静止hold_sec）
    # ------------------------------------------------------------------
    print("")
    print("=== 3段時間：reveal_sec/slide_sec/hold_secを変えた3パターンでフレーム数が指定どおりになる ===")
    timing_patterns = [
        {"reveal_sec": 0.3, "slide_sec": 0.2, "hold_sec": 0.8},
        {"reveal_sec": 0.6, "slide_sec": 0.5, "hold_sec": 2.0},
        {"reveal_sec": 1.0, "slide_sec": 0.9, "hold_sec": 3.5},
    ]
    for pat in timing_patterns:
        fz = dict(base_style)
        fz.update({
            "time": 2.5, "name": "", "sfx": None,
            "strokes": [{"width": 0.1, "points": [[0.5, 0.5], [0.5, 0.5]]}],
            "shadow": {"color": "#000000", "alpha": 0.9, "distance": 0.05, "direction": "right",
                       "offset_y": 0.0, "blur": 0.0},
        })
        fz.update(pat)
        plan = render.plan_freezes([fz], fps, src_frames)[0]
        expect_reveal = max(1, round(pat["reveal_sec"] * fps))
        expect_slide = round(pat["slide_sec"] * fps)
        expect_hold = round(pat["hold_sec"] * fps)
        check(plan["n_reveal"] == expect_reveal,
              f"reveal_sec={pat['reveal_sec']}: n_reveal={plan['n_reveal']}（期待{expect_reveal}）")
        check(plan["n_slide_in"] == expect_slide,
              f"slide_sec={pat['slide_sec']}: n_slide_in={plan['n_slide_in']}（期待{expect_slide}）")
        check(plan["n_hold"] == expect_hold,
              f"hold_sec={pat['hold_sec']}: n_hold={plan['n_hold']}（期待{expect_hold}）")
        frames_actual = list(render.iter_freeze_frames(
            render.grab_frame_at(video_path, plan["frame_index"] / fps, W, H, fps), plan, W, H, fps, {}))
        check(len(frames_actual) == plan["n_total"],
              f"実際に生成されるフレーム数もplan['n_total']({plan['n_total']})と一致する: {len(frames_actual)}")

    print("")
    print("=== 色付けと影で別々のマスクを使える（color_source=brush・shadow.source=auto の混在） ===")
    try:
        import rembg  # noqa: F401
        has_rembg_mixed = True
    except ImportError:
        has_rembg_mixed = False
    if has_rembg_mixed:
        mixed_fz = dict(base_style)
        mixed_fz.update({
            "time": 2.5, "name": "", "sfx": None, "color_source": "brush",
            "strokes": [{"width": 0.12, "points": [[0.5, 0.5], [0.5, 0.5]]}],
            "shadow": {"color": "#000000", "alpha": 0.9, "distance": 0.05, "direction": "right",
                       "offset_y": 0.0, "blur": 0.0, "source": "auto"},
        })
        mixed_cache_dir = os.path.join(tmpdir, "mixed_cache")
        # render.validate_auto_alpha（マスク面積が画面の5%未満/85%超なら失敗とみなす）を
        # 通常のダミー動画の小さな円（画面の約2.5%）で満たせないため、ここだけ実写の
        # 人物のように十分な大きさを持つ合成フレームを使う（video_path/tはキャッシュキー
        # 用にそのまま流用するが、専用のmixed_cache_dirを使うため他のテストとは衝突しない）
        mixed_frame = np.full((H, W, 3), (90, 70, 60), np.uint8)
        cv2.circle(mixed_frame, (int(W * 0.5), int(H * 0.42)), int(min(W, H) * 0.28),
                   (60, 90, 220), -1, cv2.LINE_AA)
        color_ctx = render.build_mask_context(mixed_fz, mixed_frame, W, H, mixed_cache_dir, video_path)
        check(color_ctx["mask_mode"] == "brush", "color_source='brush'のとき、カラー化はブラシのマスクを使う")
        color_done_mask, _ = render.mask_and_paint_at(color_ctx, W, H, 1.0)
        shadow_cfg_mixed = render.resolve_shadow_config(mixed_fz)
        shadow_mask_mixed = render.build_shadow_mask(
            mixed_fz, mixed_frame, W, H, mixed_cache_dir, video_path, shadow_cfg_mixed, color_done_mask)
        check(not np.array_equal(color_done_mask, shadow_mask_mixed),
              "shadow.source='auto'のとき、影のマスクはブラシのマスクと異なる（自動切り抜きを使っている）")
        check(os.path.isdir(mixed_cache_dir) and len(os.listdir(mixed_cache_dir)) >= 1,
              "shadow.source='auto'のためにこのフリーズだけ自動切り抜きが実行され、キャッシュが作られる")
    else:
        print("  [skip] rembg が未インストールのため color_source/shadow.source の混在検証は省略します")

    print("")
    print("=== テロップは②ズレの着地で出現し、③静止(hold)の終わりまで表示され続ける ===")
    telop_fz = dict(base_style)
    telop_fz.update({
        "time": 2.5, "name": "テロップ確認", "sfx": None,
        "reveal_sec": 0.3, "slide_sec": 0.2, "hold_sec": 1.5,
        "strokes": [{"width": 0.1, "points": [[0.5, 0.5], [0.5, 0.5]]}],
        "shadow": {"color": "#000000", "alpha": 0.9, "distance": 0.05, "direction": "right",
                   "offset_y": 0.0, "blur": 0.0},
    })
    telop_plan, telop_frames = render_freeze_frames(telop_fz)
    landing_frame_idx = telop_plan["n_pre"] + telop_plan["n_reveal"] + telop_plan["n_slide_in"]
    hold_end_idx = landing_frame_idx + telop_plan["n_hold"] - 1
    # まだテロップが出ていないはずのフレーム（スライド着地の直前）と比較する
    just_before_landing = telop_frames[max(0, landing_frame_idx - 1)]
    at_landing = telop_frames[landing_frame_idx]
    near_hold_end = telop_frames[hold_end_idx]
    # テロップ想定領域（title_pos既定 [0.5, 0.78] 付近）の画素だけを見る
    tcy0, tcy1 = int(0.65 * H), int(0.95 * H)
    diff_at_landing = int(np.abs(
        just_before_landing[tcy0:tcy1].astype(int) - at_landing[tcy0:tcy1].astype(int)).sum())
    check(diff_at_landing > 0, f"②ズレの着地の瞬間にテロップ領域の画素が変化し始める（diff={diff_at_landing}）")
    diff_still_visible = int(np.abs(
        just_before_landing[tcy0:tcy1].astype(int) - near_hold_end[tcy0:tcy1].astype(int)).sum())
    check(diff_still_visible > 0,
          f"③静止(hold)の終わり近くでもテロップはまだ表示されたまま（diff={diff_still_visible}）")

    # ------------------------------------------------------------------
    # 6.9) 旧キー（mask/freeze_sec/shadow.slide_sec/film_*）のみの既存JSONが従来どおり動く
    # ------------------------------------------------------------------
    print("")
    print("=== 後方互換：旧キー（mask/freeze_sec/brush_anim_sec/shadow.slide_sec/film_*）のみのJSONも動く ===")
    legacy_project = {
        "version": 1, "video": "dummy_input.mp4",
        "output": {"width": W, "height": H, "fps": fps},
        "style": {
            "freeze_sec": 1.2, "brush_anim_sec": 0.4,
            "mask": "brush",
            "font": "assets/fonts/NotoSansJP-Bold.ttf",
            "title_font": "assets/fonts/Anton-Regular.ttf",
            "title_font_jp": "assets/fonts/NotoSansJP-Bold.ttf",
            "shadow": {"color": "#112233", "alpha": 0.7, "distance": 0.04,
                       "direction": "left", "offset_y": 0.0, "blur": 0.0, "slide_sec": 0.3},
        },
        "freezes": [{
            "time": 2.5, "name": "旧キー", "mask": "brush",
            "strokes": [{"width": 0.1, "points": [[0.5, 0.5], [0.55, 0.5]]}],
        }],
    }
    legacy_json_path = os.path.join(tmpdir, "legacy_project.json")
    with open(legacy_json_path, "w", encoding="utf-8") as f:
        json_mod.dump(legacy_project, f, ensure_ascii=False)
    loaded_legacy = render.load_project(legacy_json_path)
    legacy_fz_merged = loaded_legacy["freezes"][0]
    check(render.resolve_color_source(legacy_fz_merged) == "brush",
          "旧mask='brush'キーはcolor_source='brush'として読み替えられる")
    legacy_shadow_cfg = render.resolve_shadow_config(legacy_fz_merged)
    check(legacy_shadow_cfg is not None and abs(legacy_shadow_cfg["slide_sec"] - 0.3) < 1e-9,
          f"旧shadow.slide_secがそのまま使われる: {legacy_shadow_cfg['slide_sec'] if legacy_shadow_cfg else None}")
    check(abs(render.resolve_hold_sec(legacy_fz_merged) - 1.2) < 1e-9,
          "旧freeze_secはhold_secとして読み替えられる")
    out_legacy = os.path.join(tmpdir, "legacy_out.mp4")
    render.render(loaded_legacy, legacy_json_path, video_path, out_legacy)
    check(os.path.exists(out_legacy) and os.path.getsize(out_legacy) > 0,
          "旧キーのみのプロジェクトJSONでも実際にレンダリングが成功する")

    # ------------------------------------------------------------------
    # 6.8) ラストロゴ演出「ゆっくり・重厚」への変更
    # ------------------------------------------------------------------
    print("")
    print("=== ラストロゴ：既定値がよりゆっくり・重厚になっている ===")
    default_logo_params = render.resolve_logo_params({})
    expected_scale_from = 1.05 / 0.62
    check(abs(default_logo_params["scale_from"] - expected_scale_from) < 1e-9,
          f"scale_fromの既定は「画面幅の105%」÷width_ratio(0.62)から逆算される（以前は1.6固定）: "
          f"{default_logo_params['scale_from']}（期待値 {expected_scale_from}）")
    check(abs(default_logo_params["landing_sec"] - 0.6) < 1e-9,
          f"landing_secの既定は0.6秒（以前は0.45秒、その前は0.15秒）: {default_logo_params['landing_sec']}")
    check(abs(default_logo_params["flash_strength"] - 0.35) < 1e-9,
          f"flash_strengthの既定は0.35（以前は0.6）: {default_logo_params['flash_strength']}")
    check(abs(default_logo_params["sweep_start_sec"] - 0.35) < 1e-9,
          f"sweep_start_secの既定は0.35秒（着地完了からスイープ開始まで）: {default_logo_params['sweep_start_sec']}")
    check(abs(default_logo_params["sweep_sec"] - 0.70) < 1e-9,
          f"sweep_secの既定は0.70秒（以前は0.30秒）: {default_logo_params['sweep_sec']}")
    check(abs(default_logo_params["shake_sec"] - 0.25) < 1e-9,
          f"shake_secの既定は0.25秒: {default_logo_params['shake_sec']}")
    check(abs(default_logo_params["shake_amplitude"] - 0.004) < 1e-9,
          f"shake_amplitudeの既定は0.004（出力幅比0.4%）: {default_logo_params['shake_amplitude']}")
    check(abs(default_logo_params["duration_sec"] - 2.2) < 1e-9,
          f"duration_secの既定は2.2秒（以前は1.2秒）: {default_logo_params['duration_sec']}")
    check(abs(default_logo_params["fade_sec"] - 0.4) < 1e-9,
          f"fade_secの既定は0.4秒（以前は0.3秒→0.6秒→0.4秒）: {default_logo_params['fade_sec']}")
    check(default_logo_params["sfx_tail"] is True, "sfx_tailの既定はTrue")
    check(abs(default_logo_params["width_ratio"] - 0.62) < 1e-9,
          f"width_ratioの既定は0.62（以前は0.55、その前は0.4固定）: {default_logo_params['width_ratio']}")

    custom_logo_cfg = {
        "scale_from": 1.3, "landing_sec": 0.2, "sweep_start_sec": 0.1, "sweep_sec": 0.4,
        "flash_strength": 0.5, "shake_sec": 0.1, "shake_amplitude": 0.01,
        "duration_sec": 1.0, "fade_sec": 0.2, "sfx_tail": False, "width_ratio": 0.7,
    }
    custom_logo_params = render.resolve_logo_params(custom_logo_cfg)
    check(all(abs(custom_logo_params[k] - custom_logo_cfg[k]) < 1e-9
              for k in ("scale_from", "landing_sec", "sweep_start_sec", "sweep_sec",
                        "flash_strength", "shake_sec", "shake_amplitude", "duration_sec",
                        "fade_sec", "width_ratio")),
          f"logo{{}}配下で全パラメータを上書きできる: {custom_logo_params}")
    check(custom_logo_params["sfx_tail"] is False, "sfx_tail: false も上書きできる")

    # ------------------------------------------------------------------
    # 6.9) ラストロゴの自動クロップ：黒背景・アルファ無しの実ロゴ画像で効いているか
    # ------------------------------------------------------------------
    print("")
    print("=== ラストロゴ：自動クロップ（黒背景・アルファ無しロゴ）の効き目とJPEGノイズ耐性 ===")
    opaque_png_path = os.path.join("examples", "store_logo_opaque_black.png")
    opaque_jpg_path = os.path.join("examples", "store_logo_opaque_black.jpg")

    png_bgr, png_alpha = render.load_logo_image(opaque_png_path)
    orig_h, orig_w = png_bgr.shape[:2]
    check(png_alpha.min() >= 1.0 - 1e-6,
          "テスト用ロゴ(PNG)はアルファ無し相当（全面不透明）になっている（色距離での判定経路を通す）")

    cropped_png_bgr, _ = render.crop_logo_content(png_bgr, png_alpha)
    crop_h, crop_w = cropped_png_bgr.shape[:2]
    check(crop_w < orig_w * 0.7 and crop_h < orig_h * 0.7,
          f"余白多め（画像幅の30%）のロゴが自動クロップで実際に大きく縮む: "
          f"{orig_w}x{orig_h} → {crop_w}x{crop_h}")
    check(crop_w > orig_w * 0.3 and crop_h > orig_h * 0.3,
          "クロップし過ぎて内容自体を失ってはいない（極端に小さくなりすぎない）")

    jpg_bgr, jpg_alpha = render.load_logo_image(opaque_jpg_path)
    cropped_jpg_bgr, _ = render.crop_logo_content(jpg_bgr, jpg_alpha)
    crop_jh, crop_jw = cropped_jpg_bgr.shape[:2]
    check(abs(crop_jw - crop_w) <= max(4, crop_w * 0.05) and abs(crop_jh - crop_h) <= max(4, crop_h * 0.05),
          f"JPEG（quality=82）に再圧縮した同じロゴでも、PNG版とほぼ同じ範囲にクロップされる"
          f"（PNG:{crop_w}x{crop_h} JPEG:{crop_jw}x{crop_jh}）")

    # 四隅の1画素だけがノイズで大きく外れ値になっても、内容の外接矩形が
    # そこに引きずられて肥大化しない（モルフォロジー・オープニングで孤立画素を除去）ことを確認
    noisy_bgr = png_bgr.copy()
    noisy_bgr[0, 0] = [90, 40, 200]
    x0n, y0n, x1n, y1n = render.detect_logo_content_bbox(noisy_bgr, png_alpha)
    x0c, y0c, x1c, y1c = render.detect_logo_content_bbox(png_bgr, png_alpha)
    check((x0n, y0n, x1n, y1n) == (x0c, y0c, x1c, y1c),
          f"背景の1画素だけが外れ値でも、外接矩形は孤立ノイズに引きずられず同じ結果になる: "
          f"ノイズ無し={(x0c, y0c, x1c, y1c)} ノイズ有り={(x0n, y0n, x1n, y1n)}")

    # 四隅を1点だけで見ると、この外れ値1点で背景色の推定自体が大きくズレることも確認しておく
    # （＝パッチ平均化で背景色推定を安定させたことの意味）
    old_style_bg = render.logo_corner_avg_color(noisy_bgr, patch_px=1)
    new_style_bg = render.logo_corner_avg_color(noisy_bgr, patch_px=render.LOGO_CROP_CORNER_PATCH_PX)
    true_bg = render.logo_corner_avg_color(png_bgr, patch_px=render.LOGO_CROP_CORNER_PATCH_PX)
    old_err = sum(abs(a - b) for a, b in zip(old_style_bg, true_bg))
    new_err = sum(abs(a - b) for a, b in zip(new_style_bg, true_bg))
    check(new_err < old_err,
          f"背景色推定は、四隅1画素だけより周囲をパッチ平均した方が外れ値の影響を受けにくい: "
          f"1画素推定の誤差={old_err} パッチ平均推定の誤差={new_err}")

    # ------------------------------------------------------------------
    # 6.10) ラストロゴのアニメーション：「画面いっぱい→縮小して定位置」になっているか
    # ------------------------------------------------------------------
    print("")
    print("=== ラストロゴ：着地アニメが「画面いっぱい→縮小」になっている ===")
    LW, LH = 540, 960
    logo_params_default = render.resolve_logo_params({})
    backdrop = np.full((LH, LW, 3), 40, np.uint8)  # ロゴ本体・背景色のどちらとも異なる中間グレー
    cropped_bgr, cropped_alpha = render.crop_logo_content(png_bgr, png_alpha)
    logo_luma = render.build_logo_luminance_mask(cropped_bgr, cropped_alpha)
    logo_bg_color = render.resolve_logo_background_color("auto", cropped_bgr)

    def logo_content_width_px(frame_bgr, bg_color_bgr, tol=20):
        """backdropの色(bg_color_bgr)と有意に異なる画素の左右端の幅(px)を返す"""
        diff = np.abs(frame_bgr.astype(np.int32) - np.array(bg_color_bgr, dtype=np.int32)).sum(axis=2)
        cols_with_content = np.where(diff.max(axis=0) > tol)[0]
        if cols_with_content.size == 0:
            return 0
        return int(cols_with_content.max() - cols_with_content.min()) + 1

    early_frame = render.render_logo_frame(
        backdrop, cropped_bgr, cropped_alpha, logo_luma, LW, LH, 0.12, logo_params_default, logo_bg_color)
    early_width_px = logo_content_width_px(early_frame, (40, 40, 40))
    check(early_width_px > LW * 0.90,
          f"着地アニメ開始直後（t=0.12s）は、ロゴがほぼ画面幅いっぱいに見える: "
          f"content_width={early_width_px}px / frame_width={LW}px "
          f"({early_width_px / LW * 100:.1f}%)")

    # 着地直後(t=landing_sec)は白フラッシュ・画面の揺れがまだ発火中で画面全体の色が
    # 動いてしまうため、それらが収まった少し後（+0.3秒。スイープ開始前）で計測する
    landed_frame = render.render_logo_frame(
        backdrop, cropped_bgr, cropped_alpha, logo_luma, LW, LH,
        logo_params_default["landing_sec"] + 0.3, logo_params_default, logo_bg_color)
    landed_width_px = logo_content_width_px(landed_frame, (40, 40, 40))
    expected_landed_ratio = logo_params_default["width_ratio"]
    check(abs(landed_width_px / LW - expected_landed_ratio) < 0.08,
          f"着地完了後は、ロゴの表示幅がwidth_ratio（既定{expected_landed_ratio:.2f}）付近に縮小している: "
          f"content_width={landed_width_px}px / frame_width={LW}px "
          f"({landed_width_px / LW * 100:.1f}%)")
    check(early_width_px > landed_width_px,
          "着地アニメ中に、ロゴの表示幅は「画面いっぱい」から「着地後の定位置サイズ」へ縮小していく: "
          f"開始直後={early_width_px}px → 着地完了={landed_width_px}px")

    print("")
    print("=== ラストロゴ：着地タイミング（アンティシペーション＋セトル・揺れ・スイープ開始の遅延） ===")
    check(render.ease_in_cubic(0.0) == 0.0 and render.ease_in_cubic(1.0) == 1.0,
          "ease_in_cubic: t=0で0、t=1で1")
    check(abs(render.ease_in_cubic(0.5) - 0.5 ** 3) < 1e-9,
          "ease_in_cubic: t=0.5で 0.5^3 になる")
    # 着地カーブ（アンティシペーション＋セトル）：--preview/ダミーレンダリングで単純な
    # Ease-Out Quart（旧実装）と見比べた結果、「ゆっくり入って一気に落ち、100%を
    # わずかに沈み込んでから戻る」動きの方が重厚感が出て良かったため、こちらを採用した
    # （render.logo_landing_curve、詳細はコード内コメント参照）。
    scale_from = render.LOGO_SCALE_FROM_DEFAULT
    check(abs(render.logo_landing_curve(0.0, scale_from) - scale_from) < 1e-6,
          "logo_landing_curve: t=0はscale_from（スタンバイ時の初期スケール）のまま")
    check(abs(render.logo_landing_curve(1.0, scale_from) - 1.0) < 1e-6,
          "logo_landing_curve: t=1で100%に完全着地する")
    dip_t = render.LOGO_LANDING_ANTICIPATION_RATIO
    dip_scale = render.logo_landing_curve(dip_t, scale_from)
    check(dip_scale < 1.0,
          f"logo_landing_curve: 沈み込み区間の終わり(t={dip_t})では100%をわずかに下回っている"
          f"（アンティシペーション＋セトル）: scale={dip_scale:.4f}")
    check(1.0 - dip_scale < 0.05,
          f"logo_landing_curve: 沈み込み量はわずか（数%程度）に留まる: dip={1.0 - dip_scale:.4f}")
    mid_scale = render.logo_landing_curve(dip_t * 0.5, scale_from)
    check(scale_from > mid_scale > dip_scale,
          "logo_landing_curve: 沈み込み区間はscale_from→中間→沈み込み位置と単調に縮んでいく")
    # 「ゆっくり入って一気に落ちる」：Ease-In Cubicの性質どおり、区間の後半のほうが
    # 前半より速く動く（前半の変化量 < 後半の変化量）
    early_drop = scale_from - mid_scale
    late_drop = mid_scale - dip_scale
    check(late_drop > early_drop,
          f"logo_landing_curve: 沈み込み区間の前半(drop={early_drop:.4f})より後半(drop={late_drop:.4f})の"
          "ほうが速く動く（ゆっくり入って一気に落ちる）")

    landing = default_logo_params["landing_sec"]
    state_start = render.logo_animation_state(0.0, default_logo_params)
    check(abs(state_start["scale"] - default_logo_params["scale_from"]) < 1e-6 and state_start["opacity"] == 0.0,
          f"t=0はスタンバイ状態（scale={default_logo_params['scale_from']}, opacity=0）: {state_start}")
    state_landed = render.logo_animation_state(landing, default_logo_params)
    check(abs(state_landed["scale"] - 1.0) < 1e-6 and abs(state_landed["opacity"] - 1.0) < 1e-6,
          f"着地完了時点(t=landing_sec)はscale=1.0, opacity=1.0: {state_landed}")

    state_mid_flash = render.logo_animation_state(landing + 0.01, default_logo_params)
    check(state_mid_flash["flash_amt"] > 0.2,
          f"着地直後はフラッシュが発火している: {state_mid_flash['flash_amt']:.3f}")

    state_mid_shake = render.logo_animation_state(landing + 0.05, default_logo_params)
    check(abs(state_mid_shake["shake_dx"]) > 0 or abs(state_mid_shake["shake_dy"]) > 0,
          f"着地直後は画面の揺れが発生している: dx={state_mid_shake['shake_dx']:.5f} dy={state_mid_shake['shake_dy']:.5f}")
    state_after_shake = render.logo_animation_state(
        landing + default_logo_params["shake_sec"] + 0.01, default_logo_params)
    check(state_after_shake["shake_dx"] == 0.0 and state_after_shake["shake_dy"] == 0.0,
          "揺れはshake_sec経過後は収まる（振幅0に戻る）")

    just_before_sweep = render.logo_animation_state(
        landing + default_logo_params["sweep_start_sec"] - 0.02, default_logo_params)
    check(just_before_sweep["sweep_t"] is None,
          "スイープはsweep_start_sec経過前はまだ始まらない（着地直後にすぐ始まらない＝間を置く）")
    just_after_sweep = render.logo_animation_state(
        landing + default_logo_params["sweep_start_sec"] + 0.02, default_logo_params)
    check(just_after_sweep["sweep_t"] is not None and 0.0 <= just_after_sweep["sweep_t"] < 0.2,
          f"sweep_start_sec経過後にスイープが始まる: {just_after_sweep['sweep_t']}")

    seg_end = landing + default_logo_params["duration_sec"]
    state_near_end = render.logo_animation_state(seg_end - 0.01, default_logo_params)
    check(state_near_end["fade_amt"] > 0.9,
          f"終了直前はほぼ完全に背景色へ暗転している: {state_near_end['fade_amt']:.3f}")

    print("")
    print("=== ラストロゴ：着地の瞬間の画面の揺れ（shake_translate）は実際に画素をずらす ===")
    shake_test_img = np.zeros((100, 100, 3), np.uint8)
    shake_test_img[40:60, 40:60] = 255
    shaken = render.shake_translate(shake_test_img, 5.0, 2.0)
    check(not np.array_equal(shake_test_img, shaken), "shake_translateは実際に画素をずらす")
    check(np.array_equal(render.shake_translate(shake_test_img, 0.0, 0.0), shake_test_img),
          "揺れ量が0のときは元画像のまま変化しない")
    check(shaken.shape == shake_test_img.shape, "揺らしても画像サイズは変わらない")

    print("")
    print("=== ラストロゴ：sfx_tailで着地SEに減衰ディレイ（簡易リバーブ風テール）を付けられる ===")
    sr = render.AUDIO_SR
    rng = np.random.RandomState(0)
    dsp_test_samples = rng.uniform(-0.3, 0.3, size=(int(0.3 * sr), 2)).astype(np.float32)
    tailed = render.apply_reverb_tail(dsp_test_samples, sr, tail_sec=0.6)
    check(len(tailed) == len(dsp_test_samples) + int(round(0.6 * sr)),
          "apply_reverb_tailは元の長さ+tail_secぶん長くなる: "
          f"{len(dsp_test_samples)} -> {len(tailed)}")
    check(bool(np.any(np.abs(tailed[len(dsp_test_samples):]) > 1e-4)),
          "延長された部分にもエコー由来の音量が残っている（テールがある）")
    check(np.array_equal(render.apply_reverb_tail(dsp_test_samples, sr, tail_sec=0), dsp_test_samples),
          "tail_sec=0なら音声は変化しない（長さも同じ）")

    don_path = render.resolve_path(os.path.join("assets", "sfx", "don.wav"), [render.SCRIPT_DIR])
    raw_don = render.decode_audio(don_path, render.AUDIO_SR, render.AUDIO_CH)
    check(len(raw_don) > 0, "assets/sfx/don.wavが読み込める（着地SEの実ファイルでテールを確認する）")

    logo_fz = dict(base_style)
    logo_fz.update({"time": 2.5, "name": "", "sfx": None, "strokes": [], "shadow": None})
    logo_cfg_tail = {"image": "store_logo.png", "at": "last_freeze", "sfx": "don"}
    logo_params_tail = render.resolve_logo_params(logo_cfg_tail)
    logo_params_no_tail = render.resolve_logo_params(dict(logo_cfg_tail, sfx_tail=False))

    plans_logo = render.plan_freezes(
        [logo_fz], fps, src_frames, logo=logo_cfg_tail, logo_at="last_freeze",
        logo_total_frames=render.logo_total_frames_for(logo_params_tail, fps), logo_crossfade_frames=0)
    plan0 = plans_logo[0]
    check(plan0["show_logo"], "テスト前提：このフリーズでラストロゴが表示される設定になっている")

    audio_tail = render.build_audio(video_path, plans_logo, fps, src_frames, True,
                                     logo_sfx_path=don_path, logo_at="last_freeze",
                                     logo_params=logo_params_tail)
    audio_no_tail = render.build_audio(video_path, plans_logo, fps, src_frames, True,
                                        logo_sfx_path=don_path, logo_at="last_freeze",
                                        logo_params=logo_params_no_tail)

    landed_frame_offset = plan0["n_pre"] + plan0["n_reveal"] + plan0.get("n_slide_in", 0)
    landing_frames = int(round(logo_params_tail["landing_sec"] * fps))
    sfx_start_sample = render.frames_to_samples(
        plan0["frame_index"] + landed_frame_offset + landing_frames, fps, render.AUDIO_SR)
    probe_start = sfx_start_sample + len(raw_don) + int(0.05 * render.AUDIO_SR)
    probe_end = probe_start + int(0.05 * render.AUDIO_SR)
    tail_energy = float(np.abs(audio_tail[probe_start:probe_end]).mean())
    no_tail_energy = float(np.abs(audio_no_tail[probe_start:probe_end]).mean())
    check(tail_energy > 1e-4 and tail_energy > no_tail_energy * 3,
          f"sfx_tail=Trueだと、生のSE終了直後にもエコーの音量が残る"
          f"（tail={tail_energy:.5f} / no_tail={no_tail_energy:.5f}）")
    check(no_tail_energy < 1e-3,
          f"sfx_tail=Falseなら生のSE終了直後はほぼ無音のまま: {no_tail_energy:.5f}")

    print("")
    print("=== 自動切り抜きのアルファ後処理・破綻検知（keep_largest_component/fill_holes/postprocess_auto_alpha/validate_auto_alpha） ===")

    # keep_largest_component: 小さなノイズの破片が除去され、大きな塊だけが残る
    noise_mask = np.zeros((100, 100), np.uint8)
    noise_mask[10:60, 10:60] = 255       # 大きな塊（50x50=2500px）
    noise_mask[80:85, 80:85] = 255       # 小さなノイズの破片（5x5=25px、非連結）
    kept = render.keep_largest_component(noise_mask)
    check(kept[30, 30] == 255 and kept[82, 82] == 0,
          "keep_largest_component：大きな塊は残り、非連結の小さなノイズの破片は除去される")
    check(int((kept > 0).sum()) == 2500,
          f"keep_largest_component：残った面積が最大成分の面積と一致する: {int((kept > 0).sum())}")

    # fill_holes: 被写体内部の穴（外周とはつながっていない背景領域）だけが埋まり、外周の背景は変わらない
    hole_mask = np.zeros((100, 100), np.uint8)
    hole_mask[10:90, 10:90] = 255
    hole_mask[40:50, 40:50] = 0          # 内部の穴（外周とは非連結）
    filled = render.fill_holes(hole_mask)
    check(filled[45, 45] == 255, "fill_holes：被写体内部の穴が埋まる")
    check(filled[0, 0] == 0, "fill_holes：外周の背景（外部）はそのまま背景のまま")

    # postprocess_auto_alpha: 連続値（半透明の境界）を保ちつつ、ノイズ除去・穴埋め・軽いフェザーを適用する
    raw_alpha = np.zeros((100, 100), np.uint8)
    raw_alpha[10:90, 10:90] = 200        # 被写体（完全な255ではなく半透明寄りの連続値のまま）
    raw_alpha[40:50, 40:50] = 0          # 内部の穴
    raw_alpha[95:100, 95:100] = 255      # 非連結のノイズの破片
    post = render.postprocess_auto_alpha(raw_alpha)
    check(int(post[45, 45]) > 200,
          f"postprocess_auto_alpha：穴埋め後の内部領域は周囲より不透明寄りになる（フェザーで多少滲む）: {int(post[45, 45])}")
    check(80 < int(post[50, 20]) <= 200,
          f"postprocess_auto_alpha：元々前景だった領域は連続値が概ね保たれる（フェザーで多少変化しうる）: {int(post[50, 20])}")
    check(int(post[97, 97]) < 50,
          f"postprocess_auto_alpha：非連結のノイズの破片は除去される: {int(post[97, 97])}")

    # auto_mask_area_ratio: 単純な閾値128以上の面積比
    half_alpha = np.zeros((10, 10), np.uint8)
    half_alpha[:, :5] = 255
    check(abs(render.auto_mask_area_ratio(half_alpha) - 0.5) < 1e-9,
          f"auto_mask_area_ratio：半分が前景なら0.5になる: {render.auto_mask_area_ratio(half_alpha)}")

    # validate_auto_alpha: 85%超・5%未満のいずれでもRuntimeErrorになり、日本語の理由が含まれる
    oversized = np.full((100, 100), 255, np.uint8)
    oversized[:10, :10] = 0  # 前景 99% > 85%
    try:
        render.validate_auto_alpha(oversized, "t=1.00s")
        check(False, "validate_auto_alpha：面積が85%を超えるとRuntimeErrorになる")
    except RuntimeError as e:
        check("失敗しました" in str(e) and "t=1.00s" in str(e),
              f"validate_auto_alpha：面積が85%を超えるとRuntimeErrorになり、日本語の理由とラベルを含む: {e}")

    undersized = np.zeros((100, 100), np.uint8)
    undersized[:3, :3] = 255  # 前景 0.09% < 5%
    try:
        render.validate_auto_alpha(undersized, "t=2.00s")
        check(False, "validate_auto_alpha：面積が5%未満だとRuntimeErrorになる")
    except RuntimeError as e:
        check("失敗しました" in str(e) and "t=2.00s" in str(e),
              f"validate_auto_alpha：面積が5%未満だとRuntimeErrorになり、日本語の理由とラベルを含む: {e}")

    normal_mask = np.zeros((100, 100), np.uint8)
    normal_mask[10:60, 10:60] = 255  # 前景 25%（5%〜85%の範囲内）
    check(abs(render.validate_auto_alpha(normal_mask, "t=3.00s") - 0.25) < 1e-9,
          "validate_auto_alpha：5%〜85%の範囲内であればエラーにならず面積比を返す")

    has_rembg = False
    try:
        import rembg  # noqa: F401
        has_rembg = True
    except ImportError:
        pass

    if not has_rembg:
        print("  [skip] rembg が未インストールのため mask='auto'/'auto+brush' の検証は省略します"
              "（requirements-extract.txt を pip install すれば実行されます）")
    else:
        print("")
        print("=== mask='auto'：自動切り抜き（rembg）を使ったフルレンダリングが成功し、キャッシュが効く ===")
        auto_cache_dir = os.path.join(tmpdir, "auto_cache")
        # render.validate_auto_alpha（マスク面積が画面の5%未満/85%超なら失敗とみなす）を、
        # 通常のダミー動画の小さな円（画面の約2.5%）では満たせないため、実写の人物のように
        # 十分な大きさを持つ被写体を描いた専用のダミー動画を使う
        import make_dummy as md_large
        auto_video_path = os.path.join(tmpdir, "large_subject.mp4")
        md_large.gen_dummy_video_large_subject(auto_video_path, w=W, h=int(H), duration_sec=2)
        auto_project = {
            "version": 1, "video": "large_subject.mp4",
            "output": {"width": W, "height": int(H)},
            "style": {
                "freeze_sec": 1.0, "audio_during_freeze": "mute",
                "mask": "auto", "mask_options": {"model": "isnet-general-use"},
                "reveal": "wipe", "shadow": shadow_cfg,
            },
            "freezes": [{"time": 1.0, "name": "自動くん"}],
        }
        auto_json_path = os.path.join(tmpdir, "auto_project.json")
        import json as json_mod
        with open(auto_json_path, "w", encoding="utf-8") as f:
            json_mod.dump(auto_project, f, ensure_ascii=False)
        loaded_auto = render.load_project(auto_json_path)
        out_auto = os.path.join(tmpdir, "auto_out.mp4")
        old_cwd_auto = os.getcwd()
        try:
            os.chdir(tmpdir)  # render()のcache_dirはcwd基準のcache/なので、tmpdir配下に閉じ込める
            t0 = time.time()
            render.render(loaded_auto, auto_json_path, auto_video_path, out_auto)
            elapsed_auto = time.time() - t0
        finally:
            os.chdir(old_cwd_auto)
        check(os.path.exists(out_auto) and os.path.getsize(out_auto) > 0,
              f"mask='auto' のフルレンダリングが成功する（{elapsed_auto:.1f}秒）")
        cache_dir_used = os.path.join(tmpdir, "cache")
        cache_files = [f for f in os.listdir(cache_dir_used)] if os.path.isdir(cache_dir_used) else []
        check(len(cache_files) >= 1, f"自動切り抜きのキャッシュ(.npz)が作られる: {cache_files}")

        print("")
        print("=== mask='auto+brush'：addストロークで自動アルファに領域を足し足せる ===")
        rgb_frame = render.grab_frame_at(video_path, 2.5, W, H, fps)[:, :, ::-1]
        from PIL import Image
        import extract
        base_alpha, _elapsed, _session = extract.extract_alpha(
            Image.fromarray(rgb_frame), model_name="isnet-general-use")
        base_alpha = np.array(base_alpha)[:, :, 3]
        # 左上の隅（自動切り抜きでは前景と判定されないはずの領域）にaddストロークを描き足す
        corrected = render.apply_brush_correction(
            base_alpha, [{"width": 0.08, "mode": "add", "points": [[0.05, 0.05], [0.05, 0.05]]}],
            W, H, 0.08, "round")
        check(int(corrected[int(0.05 * H), int(0.05 * W)]) > int(base_alpha[int(0.05 * H), int(0.05 * W)]),
              "addストロークを描いた場所は、自動アルファ単体より値が大きくなる（塗り足された）")

    # ------------------------------------------------------------------
    # 6) 効果音の外部ファイル優先（assets/sfx/を差し替えれば使われる）
    # ------------------------------------------------------------------
    print("")
    print("=== 効果音：assets/sfx/ に置いたファイルがそのまま使われる ===")
    sfx_path = render.resolve_path(os.path.join("assets", "sfx", "shakin.wav"),
                                    [os.getcwd(), REPO_ROOT])
    check(os.path.exists(sfx_path), "assets/sfx/shakin.wav が解決できる（差し替えれば同じ仕組みで使われる）")
    import make_dummy as md
    check(os.path.exists(os.path.join(md.SFX_DIR, "shakin.wav")) and
          os.path.exists(os.path.join(md.SFX_DIR, "don.wav")),
          "make_dummy.py 再実行後もassets/sfx/の各ファイルが存在する（上書きされていない）")

finally:
    shutil.rmtree(tmpdir, ignore_errors=True)

print("")
print(f"{passed} 件成功 / {failed} 件失敗")
if failed > 0:
    sys.exit(1)
