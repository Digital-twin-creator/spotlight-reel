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
    # 6.7) 自動切り抜き（mask="auto"/"auto+brush"）＋「影」演出
    #      shadowは「差分ベース」で検証する：shadow付き/無しでdoneフレームを作り、
    #      影のずれ量ぶんだけ離れた位置（本来は背景のまま）で画素が変化していることを見る。
    # ------------------------------------------------------------------
    print("")
    print("=== shadow演出：mask='brush'でも影が指定オフセット方向にずれて乗る ===")
    shadow_cfg = {"offset": [0.08, 0.08], "blur": 0.0, "color": "#000000", "alpha": 0.9}
    base_style = dict(render.DEFAULT_STYLE)
    base_style.update({"freeze_sec": 1.0, "brush_anim_sec": 0.2, "background": "mono"})

    def make_dot_freeze(shadow=None):
        fz = dict(base_style)
        fz.update({
            "time": 2.5, "name": "", "sfx": None,
            "strokes": [{"width": 0.1, "points": [[0.5, 0.5], [0.5, 0.5]]}],
            "shadow": shadow,
        })
        return fz

    def render_done_frame(fz):
        plan = render.plan_freezes([fz], fps, src_frames)[0]
        frame = render.grab_frame_at(video_path, plan["frame_index"] / fps, W, H, fps)
        frames = list(render.iter_freeze_frames(frame, plan, W, H, fps, {}))
        return frames[plan["n_hold"] + plan["n_brush"]]

    frame_no_shadow = render_done_frame(make_dot_freeze(shadow=None))
    frame_with_shadow = render_done_frame(make_dot_freeze(shadow=shadow_cfg))

    # ドットの中心(0.5,0.5)からoffset(0.05,0.05)*Wだけ右下にずれた位置は、
    # ドット自体の外（マスク範囲外）かつ影の範囲内になる想定
    dx = int(shadow_cfg["offset"][0] * W)
    shadow_probe = (int(0.5 * H) + dx, int(0.5 * W) + dx)
    far_probe = (10, 10)  # 影・ドットどちらの影響も受けないはずの隅
    diff_shadow_area = int(np.abs(
        frame_no_shadow[shadow_probe].astype(int) - frame_with_shadow[shadow_probe].astype(int)).sum())
    diff_far_area = int(np.abs(
        frame_no_shadow[far_probe].astype(int) - frame_with_shadow[far_probe].astype(int)).sum())
    check(diff_shadow_area > 20,
          f"影のオフセット方向の画素がshadow有無で変化している（画素は暗くなる想定, diff={diff_shadow_area}）")
    check(diff_far_area == 0,
          f"影・人物から離れた隅の画素はshadow有無で変化しない（diff={diff_far_area}）")
    check(bool(np.all(frame_with_shadow[shadow_probe] <= frame_no_shadow[shadow_probe] + 1)),
          "影が乗った画素は、影が無い場合より暗い（黒を alpha で重ねているため）")

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
