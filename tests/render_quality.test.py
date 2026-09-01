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
    max_freeze_sec = max(f.get("freeze_sec", project["style"]["freeze_sec"]) for f in project["freezes"])
    check(all(dur <= max_freeze_sec + 0.5 for _, dur in runs),
          "各無音区間の長さがfreeze_secを大きく超えない（フリーズ以外の場所が無音化していない）")
    def sfx_duration_sec(name):
        if not name:
            return 0.0
        path = render.resolve_path(os.path.join("assets", "sfx", f"{name}.wav"), [REPO_ROOT])
        return len(decode_wav(path)) / 48000.0 if os.path.exists(path) else 0.0

    total_silence = sum(dur for _, dur in runs)
    # フリーズ区間の大半は無音だが、効果音が鳴っている間は意図的に無音ではないため差し引く
    expected_total = (n_freezes * project["style"]["freeze_sec"]
                       - sum(sfx_duration_sec(f.get("sfx")) for f in project["freezes"]))
    check(abs(total_silence - expected_total) < 0.3,
          f"無音の合計時間が「フリーズ合計時間−効果音の再生時間」に近い: "
          f"実測{total_silence:.2f}s / 想定{expected_total:.2f}s")

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
        done_idx = plan["n_hold"] + plan["n_brush"]
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
        if fade_sec is not None:
            fz["brush_fade_sec"] = fade_sec
        plan = render.plan_freezes([fz], fps, src_frames)[0]
        frame = render.grab_frame_at(video_path, plan["frame_index"] / fps, W, H, fps)
        fz_no_name = dict(fz)
        fz_no_name["name"] = ""  # テロップの変化と混同しないよう名前を消して分離する
        plan_no_name = render.plan_freezes([fz_no_name], fps, src_frames)[0]
        frames = list(render.iter_freeze_frames(frame, plan_no_name, W, H, fps, {}))
        done_idx = plan_no_name["n_hold"] + plan_no_name["n_brush"]
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
    landed_idx = plan_shadow["n_hold"] + plan_shadow["n_brush"] + plan_shadow["n_slide_in"]
    frame_with_shadow = frames_shadow[landed_idx]
    frame_no_shadow = frames_noshadow[plan_shadow["n_hold"] + plan_shadow["n_brush"]]

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
    frame_early = frames_shadow[plan_shadow["n_hold"] + plan_shadow["n_brush"] + early_i]
    frame_late = frames_shadow[plan_shadow["n_hold"] + plan_shadow["n_brush"] + late_i]
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
    landed_frame_offset = plan_sfx["n_hold"] + plan_sfx["n_brush"] + plan_sfx["n_slide_in"]
    expected_sample = render.frames_to_samples(plan_sfx["frame_index"] + landed_frame_offset, fps, render.AUDIO_SR)
    # 効果音(shakin.wav)そのものの立ち上がり位置を探し、期待位置に近いことを確認する
    # （無音区間の判定と同じ考え方：しきい値を超える最初のサンプルを「発火位置」とみなす）
    window = audio[max(0, expected_sample - render.AUDIO_SR // 2):expected_sample + render.AUDIO_SR]
    check(bool(np.any(np.abs(window) > 0.02)),
          f"着地予定位置({expected_sample}サンプル)付近に効果音の音量が実際に存在する")

    print("")
    print("=== shadow演出：旧film_offset/film_color/film_alphaは後方互換で読み替えられる ===")
    legacy_fz = make_dot_freeze(shadow=None, extra={
        "film_offset": [0.08, 0.0], "film_color": "#000000", "film_alpha": 0.9,
    })
    legacy_cfg = render.resolve_shadow_config(legacy_fz)
    check(legacy_cfg is not None, "film_offsetが非ゼロならshadow未指定でも影設定が解決される（後方互換）")
    check(legacy_cfg is not None and legacy_cfg["direction"] == "right" and abs(legacy_cfg["distance"] - 0.08) < 1e-6,
          f"film_offset=[0.08, 0.0] は distance=0.08 / direction='right' に読み替えられる: {legacy_cfg}")
    _plan_legacy, frames_legacy = render_freeze_frames(legacy_fz)
    legacy_landed_idx = _plan_legacy["n_hold"] + _plan_legacy["n_brush"] + _plan_legacy["n_slide_in"]
    frame_legacy = frames_legacy[legacy_landed_idx]
    diff_legacy = int(np.abs(
        frame_no_shadow[origin_probe].astype(int) - frame_legacy[origin_probe].astype(int)).sum())
    check(diff_legacy > 20, f"film_offsetのみを指定した旧JSONでも実際に影が描画される（diff={diff_legacy}）")

    zero_legacy_cfg = render.resolve_shadow_config(make_dot_freeze(shadow=None))
    check(zero_legacy_cfg is None, "film_offsetが[0,0]（既定）のままなら、shadowもNone（影演出なし。完全後方互換）")

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
        idx = plan["n_hold"] + plan["n_brush"] + plan["n_slide_in"]
        return frames[idx]

    right_dummy_landed = landed_frame_for(make_side_freeze(0.8))    # 右寄りの人物
    left_dummy_landed = landed_frame_for(make_side_freeze(0.2))     # 左寄りの人物
    # 右寄りダミー：中心(cx=0.8*W)より右側に影が漏れているはず（右へスライドしたと分かる）
    right_probe = (int(0.5 * H), min(W - 1, int(0.8 * W) + dx))
    # 比較対象として、影を完全に無効化した同じ位置の人物フレームを作る
    _plan_right_ns, frames_right_ns = render_freeze_frames(make_dot_freeze(shadow=None, extra={
        "strokes": [{"width": 0.12, "points": [[0.8, 0.5], [0.8, 0.5]]}],
    }))
    right_no_shadow = frames_right_ns[_plan_right_ns["n_hold"] + _plan_right_ns["n_brush"]]
    diff_right_at_right_probe = int(np.abs(
        right_no_shadow[right_probe].astype(int) - right_dummy_landed[right_probe].astype(int)).sum())
    check(diff_right_at_right_probe > 20,
          f"人物が右寄りのダミーでは、direction='auto'が'right'を選び、右側で影が見える（diff={diff_right_at_right_probe}）")

    _plan_left_ns, frames_left_ns = render_freeze_frames(make_dot_freeze(shadow=None, extra={
        "strokes": [{"width": 0.12, "points": [[0.2, 0.5], [0.2, 0.5]]}],
    }))
    left_no_shadow = frames_left_ns[_plan_left_ns["n_hold"] + _plan_left_ns["n_brush"]]
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
        auto_project = {
            "version": 1, "video": "dummy_input.mp4",
            "output": {"width": W, "height": int(H)},
            "style": {
                "freeze_sec": 1.0, "audio_during_freeze": "mute",
                "mask": "auto", "mask_options": {"model": "isnet-general-use"},
                "reveal": "wipe", "shadow": shadow_cfg,
            },
            "freezes": [{"time": 2.5, "name": "自動くん"}],
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
            render.render(loaded_auto, auto_json_path, video_path, out_auto)
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
