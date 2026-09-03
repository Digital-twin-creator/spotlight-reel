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
        30.0, int(round(info_for_plan["duration"] * 30.0)), ".")
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
    # 2.5) テロップの複数行対応（title.lines契約：resolve_title_lines/render_telop_layer）
    # ------------------------------------------------------------------
    def title_line(text, size=1.0, underline=False, color="#FFFFFF", align=None, anim=None,
                    anim_sec=None, delay_sec=0.0, sfx=None, backing="outline"):
        """resolve_title_lines()が返す1行分の辞書を、テストで期待値として組み立てるヘルパー"""
        return {"text": text, "size": size, "underline": underline, "color": color, "align": align,
                "anim": anim, "anim_sec": anim_sec, "delay_sec": delay_sec, "sfx": sfx,
                "backing": backing}

    print("")
    print("=== テロップの複数行対応：resolve_title_lines（JSON契約の正規化） ===")
    check(render.resolve_title_lines({"name": "山田 太郎"}) == [title_line("山田 太郎")],
          "文字列は1行（size=1.0・underline=False・色/アニメ等は未指定）として正規化される")
    check(render.resolve_title_lines({"name": ""}) == [title_line("")],
          "空文字・未指定は空文字1行に正規化される（配列自体は空にならない）")
    multi = render.resolve_title_lines({"name": {"lines": [
        {"text": "山田 太郎", "size": 1.0, "underline": True},
        {"text": "エースストライカー", "size": 0.55},
    ]}})
    check(multi == [
        title_line("山田 太郎", underline=True),
        title_line("エースストライカー", size=0.55),
    ], f"{{lines:[...]}}形式は行ごとのsize/underlineを保ったまま正規化される: {multi}")

    print("")
    print("=== テロップの複数行対応：render_telop_line_layersが複数行・サイズ違い・アンダーラインを描く ===")
    font_path_default = render.resolve_title_font_path(dict(render.DEFAULT_STYLE, name="山田 太郎"))
    W2, H2 = 540, 960
    size_px_base = max(8, int(round(H2 * render.DEFAULT_STYLE["title_size"])))

    def combined_alpha(layers):
        """行ごとのレイヤー群を、旧テストが前提にしていた「1枚に合成したアルファ」相当へまとめる
        （行同士は重ならないよう積み上げられているので、maxで合成しても等価）"""
        return np.maximum.reduce([l["alpha"] for l in layers])

    single_layers = render.render_telop_line_layers(
        render.resolve_title_lines({"name": "山田 太郎"}), W2, H2, {}, font_path_default, size_px_base)
    check(len(single_layers) == 1, "1行のテロップは1件のレイヤーとして返る")
    single_alpha = single_layers[0]["alpha"]
    check(single_layers[0]["bgr"] is not None and float(single_alpha.sum()) > 0,
          "1行のテロップが従来どおり描画される")

    multi_lines = [
        title_line("山田 太郎", underline=True),
        title_line("エースストライカー", size=0.55),
    ]
    multi_layers = render.render_telop_line_layers(multi_lines, W2, H2, {}, font_path_default, size_px_base)
    check(len(multi_layers) == 2, "複数行のテロップは行数ぶんのレイヤーとして返る（行ごとに独立した合成ができる）")
    multi_alpha = combined_alpha(multi_layers)
    check(all(l["bgr"] is not None and float(l["alpha"].sum()) > 0 for l in multi_layers),
          "複数行それぞれが描画される")

    # 複数行の方が縦方向に描画されている行数が多く、非透明画素の縦方向の広がり(行の高さの合計相当)が
    # 1行のときより大きくなるはず（複数行対応が実際に効いていることの確認）
    def alpha_row_has_content(alpha, y, threshold=10):
        return bool((alpha[y, :, 0] > threshold / 255.0).any())

    def vertical_extent(alpha):
        rows = np.where(alpha[:, :, 0].max(axis=1) > 10 / 255.0)[0]
        return (int(rows.min()), int(rows.max())) if len(rows) else (0, 0)

    single_extent = vertical_extent(single_alpha)
    multi_extent = vertical_extent(multi_alpha)
    single_h = single_extent[1] - single_extent[0]
    multi_h = multi_extent[1] - multi_extent[0]
    check(multi_h > single_h * 1.3,
          f"複数行のテロップは1行より縦方向に大きく広がる（複数行が積み上がっている）: "
          f"1行={single_h}px / 複数行={multi_h}px")

    # 1行目（underline=True）の下端付近に、アンダーライン（横棒）による非透明画素があるはず。
    # 2行目の直前（1行目と2行目の間）を軽くスキャンし、連続した横方向の非透明画素（下線）を探す。
    found_underline_row = False
    for y in range(multi_extent[0], multi_extent[0] + int(multi_h * 0.6)):
        row = multi_alpha[y, :, 0] > 10 / 255.0
        if row.sum() > 20:  # 文字の一部ではなく横に連続した塊（下線）らしき行
            # 連続run長を見る（下線は文字と違い、太い矩形として連続する）
            max_run = 0
            cur = 0
            for v in row:
                if v:
                    cur += 1
                    max_run = max(max_run, cur)
                else:
                    cur = 0
            if max_run > row.sum() * 0.8:  # ほぼ隙間なく連続＝下線らしい
                found_underline_row = True
                break
    check(found_underline_row, "underline=Trueの行の下にアンダーライン（連続した横棒）が描かれている")

    print("")
    print("=== テロップの複数行対応：フルレンダリング（title.linesを含むJSON）がエラーなく完走する ===")
    multiline_project = sample_project("dummy_input.mp4")
    multiline_project["freezes"][0]["name"] = {
        "lines": [
            {"text": "山田 太郎", "size": 1.0, "underline": True},
            {"text": "エースストライカー", "size": 0.55},
        ]
    }
    multiline_out = os.path.join(tmpdir, "multiline_telop.mp4")
    render_direct(multiline_project, video_path, multiline_out, tmpdir)
    check(os.path.exists(multiline_out) and os.path.getsize(multiline_out) > 0,
          "title.lines（複数行・サイズ違い・アンダーライン）を含むプロジェクトのフルレンダリングが成功する")

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
    plans = render.plan_freezes(freezes, fps, src_frames, ".")
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
        plan = render.plan_freezes([fz], fps, src_frames, ".")[0]
        frame = render.grab_frame_at(video_path, plan["frame_index"] / fps, W, H, fps)
        fz_no_name = dict(fz)
        fz_no_name["name"] = ""  # テロップの変化と混同しないよう名前を消して分離する
        plan_no_name = render.plan_freezes([fz_no_name], fps, src_frames, ".")[0]
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
        render.render_telop_line_layers(
            render.resolve_title_lines({"name": "テスト"}), 200, 100, {},
            "assets/fonts/存在しないフォント.ttf", 24)
    except RuntimeError:
        raised = True
    check(raised, "フォントが読み込めない場合、render_telop_line_layersがRuntimeErrorを送出する")

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
        plan = render.plan_freezes([fz], fps, src_frames, ".")[0]
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
    plan_sfx = render.plan_freezes([fz_sfx], fps, src_frames, ".")[0]
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
    before_path, after_path, lines_preview_path = render.render(
        loaded_preview, preview_json_path, video_path, "unused.mp4", preview_path=preview_png_arg)
    check(before_path == os.path.join(tmpdir, "shadow_preview_before.png"),
          f"スライド前PNGのパスが_before付きになる: {before_path}")
    check(after_path == os.path.join(tmpdir, "shadow_preview_after.png"),
          f"スライド後PNGのパスが_after付きになる: {after_path}")
    check(lines_preview_path is None, "可視行が2行未満（このプロジェクトはname=\"\"）なら行アニメ確認PNGは作られない")
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
        plan = render.plan_freezes([fz], fps, src_frames, ".")[0]
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
    print("=== ラストロゴ：既定値が「開始を大きく・タメ→縮小→セトル」になっている ===")
    default_logo_params = render.resolve_logo_params({})
    expected_scale_from = 1.6 / 0.62
    check(abs(default_logo_params["start_width_ratio"] - 1.6) < 1e-9,
          f"start_width_ratioの既定は1.6（画面外に見切れる大きさ。以前は105%固定）: "
          f"{default_logo_params['start_width_ratio']}")
    check(abs(default_logo_params["scale_from"] - expected_scale_from) < 1e-9,
          f"scale_fromはstart_width_ratio(1.6)÷width_ratio(0.62)から逆算される: "
          f"{default_logo_params['scale_from']}（期待値 {expected_scale_from}）")
    check(abs(default_logo_params["hold_big_sec"] - 0.15) < 1e-9,
          f"hold_big_secの既定は0.15秒（タメ）: {default_logo_params['hold_big_sec']}")
    check(abs(default_logo_params["shrink_sec"] - 0.5) < 1e-9,
          f"shrink_secの既定は0.5秒（縮小）: {default_logo_params['shrink_sec']}")
    check(abs(default_logo_params["settle_sec"] - 0.15) < 1e-9,
          f"settle_secの既定は0.15秒（セトル）: {default_logo_params['settle_sec']}")
    check(abs(default_logo_params["min_scale"] - 0.98) < 1e-9,
          f"min_scaleの既定は0.98（最小サイズへの沈み込み量2%）: {default_logo_params['min_scale']}")
    check(abs(default_logo_params["flash_strength"] - 0.35) < 1e-9,
          f"flash_strengthの既定は0.35（以前は0.6）: {default_logo_params['flash_strength']}")
    check(abs(default_logo_params["sweep_start_sec"] - 0.35) < 1e-9,
          f"sweep_start_secの既定は0.35秒（着地演出完了からスイープ開始まで）: {default_logo_params['sweep_start_sec']}")
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
    actual_start_width_ratio = default_logo_params["scale_from"] * default_logo_params["width_ratio"]
    check(actual_start_width_ratio > 1.0,
          f"既定設定では開始時のロゴ実効幅が出力幅を超え、画面外に見切れた状態から始まる: "
          f"scale_from×width_ratio={actual_start_width_ratio:.3f}（出力幅の{actual_start_width_ratio*100:.0f}%）")
    check(abs(actual_start_width_ratio - 1.6) < 1e-6,
          "その実効幅はstart_width_ratio(1.6)と一致する（width_ratioの値によらず開始サイズは常に揃う）")

    custom_logo_cfg = {
        "start_width_ratio": 2.0, "hold_big_sec": 0.1, "shrink_sec": 0.3, "settle_sec": 0.1,
        "sweep_start_sec": 0.1, "sweep_sec": 0.4,
        "flash_strength": 0.5, "shake_sec": 0.1, "shake_amplitude": 0.01,
        "duration_sec": 1.0, "fade_sec": 0.2, "sfx_tail": False, "width_ratio": 0.7,
    }
    custom_logo_params = render.resolve_logo_params(custom_logo_cfg)
    check(all(abs(custom_logo_params[k] - custom_logo_cfg[k]) < 1e-9
              for k in ("start_width_ratio", "hold_big_sec", "shrink_sec", "settle_sec",
                        "sweep_start_sec", "sweep_sec",
                        "flash_strength", "shake_sec", "shake_amplitude", "duration_sec",
                        "fade_sec", "width_ratio")),
          f"logo{{}}配下で全パラメータを上書きできる: {custom_logo_params}")
    check(custom_logo_params["sfx_tail"] is False, "sfx_tail: false も上書きできる")
    check(abs(custom_logo_params["scale_from"] - 2.0 / 0.7) < 1e-9,
          "scale_fromは上書きしたstart_width_ratio/width_ratioから再計算される")

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

    # 着地の瞬間は白フラッシュ・画面の揺れがまだ発火中で画面全体の色が
    # 動いてしまうため、それらが収まった少し後（着地演出完了+0.3秒。スイープ開始前）で計測する
    landed_frame = render.render_logo_frame(
        backdrop, cropped_bgr, cropped_alpha, logo_luma, LW, LH,
        render.logo_landing_total_sec(logo_params_default) + 0.3, logo_params_default, logo_bg_color)
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
    print("=== ラストロゴ：着地タイミング（タメ→縮小→セトル・揺れ・スイープ開始の遅延） ===")
    check(render.ease_in_cubic(0.0) == 0.0 and render.ease_in_cubic(1.0) == 1.0,
          "ease_in_cubic: t=0で0、t=1で1")
    check(abs(render.ease_in_cubic(0.5) - 0.5 ** 3) < 1e-9,
          "ease_in_cubic: t=0.5で 0.5^3 になる")

    hold_big = default_logo_params["hold_big_sec"]
    shrink = default_logo_params["shrink_sec"]
    settle = default_logo_params["settle_sec"]
    scale_from = default_logo_params["scale_from"]
    min_scale = default_logo_params["min_scale"]

    def landing_scale(t):
        return render.logo_landing_scale(t, hold_big, shrink, settle, scale_from, min_scale)

    check(abs(landing_scale(0.0) - scale_from) < 1e-6,
          "logo_landing_scale: t=0はscale_from（開始サイズ）のまま")
    check(abs(landing_scale(hold_big * 0.5) - scale_from) < 1e-6,
          "logo_landing_scale: タメ区間中はscale_fromのまま静止している（動かない）")
    check(abs(landing_scale(hold_big + shrink) - min_scale) < 1e-6,
          f"logo_landing_scale: タメ+縮小の終わり（着地の瞬間）で最小サイズ(min_scale={min_scale})に到達する")
    check(abs(landing_scale(hold_big + shrink + settle) - 1.0) < 1e-6,
          "logo_landing_scale: タメ+縮小+セトルの終わりで100%に完全着地する")

    mid_scale = landing_scale(hold_big + shrink * 0.5)
    check(scale_from > mid_scale > min_scale,
          "logo_landing_scale: 縮小区間はscale_from→中間→最小サイズと単調に縮んでいく")
    # 「ゆっくり入って終盤に加速して急停止する」：Ease-In Cubicの性質どおり、区間の
    # 後半のほうが前半より速く動く（前半の変化量 < 後半の変化量）
    early_drop = scale_from - mid_scale
    late_drop = mid_scale - min_scale
    check(late_drop > early_drop,
          f"logo_landing_scale: 縮小区間の前半(drop={early_drop:.4f})より後半(drop={late_drop:.4f})の"
          "ほうが速く動く（ゆっくり入って終盤に加速→急停止）")

    landing_instant = render.logo_landing_instant_sec(default_logo_params)
    landing_total = render.logo_landing_total_sec(default_logo_params)
    state_start = render.logo_animation_state(0.0, default_logo_params)
    check(abs(state_start["scale"] - scale_from) < 1e-6 and state_start["opacity"] == 1.0,
          f"t=0は開始サイズのまま（scale={scale_from}）、不透明度は最初から1.0（フェードインなし）: {state_start}")
    state_landing_instant = render.logo_animation_state(landing_instant, default_logo_params)
    check(abs(state_landing_instant["scale"] - min_scale) < 1e-6 and abs(state_landing_instant["opacity"] - 1.0) < 1e-6,
          f"着地の瞬間(t=hold_big_sec+shrink_sec)はscale=min_scale({min_scale}), opacity=1.0: {state_landing_instant}")
    state_landed = render.logo_animation_state(landing_total, default_logo_params)
    check(abs(state_landed["scale"] - 1.0) < 1e-6 and abs(state_landed["opacity"] - 1.0) < 1e-6,
          f"着地演出完了時点(t=hold_big_sec+shrink_sec+settle_sec)はscale=1.0, opacity=1.0: {state_landed}")

    state_mid_flash = render.logo_animation_state(landing_instant + 0.01, default_logo_params)
    check(state_mid_flash["flash_amt"] > 0.2,
          f"着地の瞬間の直後はフラッシュが発火している: {state_mid_flash['flash_amt']:.3f}")

    state_mid_shake = render.logo_animation_state(landing_instant + 0.05, default_logo_params)
    check(abs(state_mid_shake["shake_dx"]) > 0 or abs(state_mid_shake["shake_dy"]) > 0,
          f"着地の瞬間の直後は画面の揺れが発生している: dx={state_mid_shake['shake_dx']:.5f} dy={state_mid_shake['shake_dy']:.5f}")
    state_after_shake = render.logo_animation_state(
        landing_instant + default_logo_params["shake_sec"] + 0.01, default_logo_params)
    check(state_after_shake["shake_dx"] == 0.0 and state_after_shake["shake_dy"] == 0.0,
          "揺れはshake_sec経過後は収まる（振幅0に戻る）")

    just_before_sweep = render.logo_animation_state(
        landing_total + default_logo_params["sweep_start_sec"] - 0.02, default_logo_params)
    check(just_before_sweep["sweep_t"] is None,
          "スイープはsweep_start_sec経過前はまだ始まらない（着地演出完了直後にすぐ始まらない＝間を置く）")
    just_after_sweep = render.logo_animation_state(
        landing_total + default_logo_params["sweep_start_sec"] + 0.02, default_logo_params)
    check(just_after_sweep["sweep_t"] is not None and 0.0 <= just_after_sweep["sweep_t"] < 0.2,
          f"sweep_start_sec経過後にスイープが始まる: {just_after_sweep['sweep_t']}")

    seg_end = landing_total + default_logo_params["duration_sec"]
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
    print("=== ラストロゴ：直前のシーンから0.1秒の暗転を経てカットする（クロスフェードではない） ===")
    check(render.logo_blackout_frames_for(30) == 3,
          f"30fpsでは0.1秒=3フレームぶんの暗転を挟む: {render.logo_blackout_frames_for(30)}")
    check(render.logo_blackout_frames_for(60) == 6,
          f"60fpsでは0.1秒=6フレームぶんの暗転を挟む: {render.logo_blackout_frames_for(60)}")

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
        [logo_fz], fps, src_frames, ".", logo=logo_cfg_tail, logo_at="last_freeze",
        logo_total_frames=render.logo_total_frames_for(logo_params_tail, fps), logo_blackout_frames=0)
    plan0 = plans_logo[0]
    check(plan0["show_logo"], "テスト前提：このフリーズでラストロゴが表示される設定になっている")

    don_sfx_spec = {"path": don_path, "align": "start_at_landing", "is_custom": False}
    audio_tail = render.build_audio(video_path, plans_logo, fps, src_frames, True,
                                     logo_sfx_spec=don_sfx_spec, logo_at="last_freeze",
                                     logo_params=logo_params_tail)
    audio_no_tail = render.build_audio(video_path, plans_logo, fps, src_frames, True,
                                        logo_sfx_spec=don_sfx_spec, logo_at="last_freeze",
                                        logo_params=logo_params_no_tail)

    landed_frame_offset = plan0["n_pre"] + plan0["n_reveal"] + plan0.get("n_slide_in", 0)
    landing_frames = int(round(render.logo_landing_instant_sec(logo_params_tail) * fps))
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

    print("")
    print("=== merge_touching_component：RVMで欠落しがちな「手に持った物」の合成（接触物のみ取り込む） ===")
    # baseは「人物」（中央の大きな矩形）を模したRVMのアルファ。
    person_alpha = np.zeros((100, 100), np.uint8)
    person_alpha[20:80, 30:60] = 255

    # extraは「手に持った物」（人物のすぐ右に接する小さな矩形）と、
    # 「無関係な背景の物体」（人物から離れた左上の矩形、接していない）の2つの連結成分を持つ
    # isnet側の前景を模したもの。
    isnet_alpha = np.zeros((100, 100), np.uint8)
    isnet_alpha[40:50, 60:65] = 200   # 人物の右端(x=59)にすぐ接する「持ち物」（半透明寄りの値）
    isnet_alpha[0:10, 0:10] = 255     # 人物から離れた無関係の物体（非接触）

    merged = render.merge_touching_component(person_alpha, isnet_alpha)
    check(bool((merged[40:50, 60:65] > 0).all()),
          "merge_touching_component：人物に接する「持ち物」の連結成分は合成される")
    check(int(merged[45, 62]) == 200,
          f"merge_touching_component：合成された持ち物の領域はisnet側の連続値をそのまま使う: {int(merged[45, 62])}")
    check(bool((merged[0:10, 0:10] == 0).all()),
          "merge_touching_component：人物に接していない無関係の物体は取り込まれない")
    check(bool((merged[20:80, 30:60] == 255).all()),
          "merge_touching_component：base（人物）自身の領域はそのまま保たれる")

    # 合成後にpostprocess_auto_alphaを適用すると、持ち物は人物と同じ連結成分として
    # 一緒に残る（=握った物が身体から千切れて消えたりしない）ことも確認しておく
    merged_post = render.postprocess_auto_alpha(merged)
    check(int(merged_post[45, 62]) > 0,
          f"merge_touching_component後にpostprocess_auto_alphaを通しても持ち物は人物と一緒に残る: {int(merged_post[45, 62])}")
    check(bool((merged_post[0:10, 0:10] == 0).all()),
          "postprocess_auto_alpha後も無関係の物体は含まれないまま")

    # 接触物・無関係物体のどちらも無い場合は、base（RVM単体の結果）をそのまま返す
    no_extra = np.zeros((100, 100), np.uint8)
    check(np.array_equal(render.merge_touching_component(person_alpha, no_extra), person_alpha),
          "merge_touching_component：extra側に前景が無ければbaseをそのまま返す")
    no_base = np.zeros((100, 100), np.uint8)
    check(np.array_equal(render.merge_touching_component(no_base, isnet_alpha), no_base),
          "merge_touching_component：base側に前景が無ければ（人物を検出できていない）そのままbaseを返す")

    print("")
    print("=== get_or_extract_alpha：model=apple-visionはrender.py（ubuntu）からは抽出できずRuntimeErrorになる ===")
    # Apple Visionの実抽出はmacOSランナー上のtools/apple-vision/subject_lift.swiftが担い、
    # render.py（ubuntu）はcache/*.npzに事前配置された結果を読むだけ。キャッシュが無い状態で
    # get_or_extract_alphaに到達したら、原因（ワークフロー構成の不具合）がわかる形で
    # 明確に失敗するべきで、rembg/onnxruntime側の抽出を試みてはいけない。
    apple_cache_dir = os.path.join(tmpdir, "apple_vision_cache")
    dummy_frame = np.zeros((100, 100, 3), np.uint8)
    try:
        render.get_or_extract_alpha(dummy_frame, "/no/such/video.mp4", 1.0, 100, 100,
                                     apple_cache_dir, {"model": "apple-vision"})
        check(False, "get_or_extract_alpha：model=apple-visionでキャッシュが無ければRuntimeErrorになる")
    except RuntimeError as e:
        check("apple-vision" in str(e),
              f"get_or_extract_alpha：model=apple-visionでキャッシュが無いとRuntimeErrorになる: {e}")

    print("")
    print("=== freeze_needs_model_extraction：build_mask_context/build_shadow_maskと同じ条件で対象フリーズを判定する ===")
    import extract as extract_module
    check(render.freeze_needs_model_extraction(
        {"mask_options": {"model": "apple-vision"}, "color_source": "auto"},
        extract_module, "apple-vision") is True,
        "freeze_needs_model_extraction：model一致＋color_source=autoならTrue")
    check(render.freeze_needs_model_extraction(
        {"mask_options": {"model": "apple-vision"}, "color_source": "brush"},
        extract_module, "apple-vision") is False,
        "freeze_needs_model_extraction：color_source=brushかつshadow指定が無ければFalse（既定shadowはsource='same'）")
    check(render.freeze_needs_model_extraction(
        {"mask_options": {"model": "apple-vision"}, "color_source": "brush",
         "shadow": {"source": "auto"}},
        extract_module, "apple-vision") is True,
        "freeze_needs_model_extraction：color_source=brushでもshadow.source=autoならTrue")
    check(render.freeze_needs_model_extraction(
        {"mask_options": {"model": "isnet-general-use"}, "color_source": "auto"},
        extract_module, "apple-vision") is False,
        "freeze_needs_model_extraction：modelが違えばFalse")
    check(render.freeze_needs_model_extraction(
        {"color_source": "auto"}, extract_module, "isnet-general-use") is True,
        "freeze_needs_model_extraction：mask_options省略時はDEFAULT_MODEL(isnet-general-use)扱い")

    print("")
    print("=== extract_frames_for_model：対象フリーズが無ければ動画を一切デコードしない ===")
    import json as json_mod2
    no_target_project = {
        "version": 1, "video": "dummy_input.mp4",
        "output": {"width": W, "height": H},
        "style": {"freeze_sec": 1.0, "audio_during_freeze": "mute"},
        "freezes": [{"time": 2.5, "name": "", "color_source": "brush"}],
    }
    no_target_json_path = os.path.join(tmpdir, "no_target_project.json")
    with open(no_target_json_path, "w", encoding="utf-8") as f:
        json_mod2.dump(no_target_project, f, ensure_ascii=False)
    loaded_no_target = render.load_project(no_target_json_path)
    no_target_out_dir = os.path.join(tmpdir, "apple_frames_no_target")
    manifest_path_empty = render.render(loaded_no_target, no_target_json_path, video_path, "unused.mp4",
                                         extract_frames_for="apple-vision",
                                         extract_frames_out=no_target_out_dir)
    with open(manifest_path_empty, encoding="utf-8") as f:
        manifest_empty = json_mod2.load(f)
    check(manifest_empty["frames"] == [], f"対象フリーズが無ければmanifest.jsonのframesは空になる: {manifest_empty}")

    print("")
    print("=== extract_frames_for_model：apple-vision指定のフリーズだけ本番と同じ解像度のフレームPNGを書き出す ===")
    mixed_model_project = {
        "version": 1, "video": "dummy_input.mp4",
        "output": {"width": W, "height": H},
        "style": {"freeze_sec": 1.0, "audio_during_freeze": "mute"},
        "freezes": [
            {"time": 1.5, "name": "", "color_source": "auto",
             "mask_options": {"model": "apple-vision"}},
            {"time": 3.0, "name": "", "color_source": "auto",
             "mask_options": {"model": "isnet-general-use"}},
        ],
    }
    mixed_model_json_path = os.path.join(tmpdir, "mixed_model_project.json")
    with open(mixed_model_json_path, "w", encoding="utf-8") as f:
        json_mod2.dump(mixed_model_project, f, ensure_ascii=False)
    loaded_mixed_model = render.load_project(mixed_model_json_path)
    mixed_model_out_dir = os.path.join(tmpdir, "apple_frames_mixed")
    manifest_path_mixed = render.render(loaded_mixed_model, mixed_model_json_path, video_path, "unused.mp4",
                                         extract_frames_for="apple-vision",
                                         extract_frames_out=mixed_model_out_dir)
    with open(manifest_path_mixed, encoding="utf-8") as f:
        manifest_mixed = json_mod2.load(f)
    check(len(manifest_mixed["frames"]) == 1,
          f"apple-visionを指定した1件だけが対象になる（isnet-general-use指定の方は含まれない）: {manifest_mixed['frames']}")
    check(abs(manifest_mixed["frames"][0]["time"] - 1.5) < 1e-6,
          f"対象フレームの時刻はapple-visionを指定したフリーズの time と一致する: {manifest_mixed['frames'][0]}")
    check(manifest_mixed["output_width"] == W and manifest_mixed["output_height"] == H,
          f"manifest.jsonのoutput_width/heightは本番の出力解像度と一致する: {manifest_mixed}")
    frame_png_path = os.path.join(mixed_model_out_dir, manifest_mixed["frames"][0]["path"])
    frame_img = cv2.imread(frame_png_path)
    check(frame_img is not None and frame_img.shape[:2] == (H, W),
          f"書き出されたPNGは本番と同じ出力解像度(HxW)になっている: {None if frame_img is None else frame_img.shape}")
    reference_frame = render.grab_frame_at(video_path, 1.5, W, H, fps)
    diff_ref = float(np.abs(frame_img.astype(np.int16) - reference_frame.astype(np.int16)).mean())
    check(diff_ref < 5.0,
          f"書き出されたPNGは同時刻のフレーム内容とほぼ一致する（シーケンシャルデコード起源であることの目視代わり、平均差分={diff_ref:.2f}）")

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
        print("=== render_timing.json：所要時間の内訳（render.ymlのジョブサマリー用）が書き出される ===")
        timing_path = os.path.join(tmpdir, "render_timing.json")
        check(os.path.exists(timing_path), f"render_timing.jsonが作られる: {timing_path}")
        with open(timing_path, encoding="utf-8") as f:
            timing = json_mod.load(f)
        check(len(timing.get("freezes", [])) == 1,
              f"フリーズ1件分のエントリが記録される: {timing.get('freezes')}")
        fz_timing = timing["freezes"][0]
        check(fz_timing["cache_used"] is False and fz_timing["time"] == 1.0,
              f"初回実行はキャッシュ未使用として記録される（time=1.0）: {fz_timing}")
        check(isinstance(fz_timing["extract_seconds"], (int, float)) and fz_timing["extract_seconds"] > 0,
              f"自動切り抜きの所要時間が正の数で記録される: {fz_timing['extract_seconds']}")
        check(timing.get("extract_seconds_total", 0) > 0,
              f"切り抜き合計時間が記録される: {timing.get('extract_seconds_total')}")
        check(timing.get("render_seconds", 0) >= timing.get("extract_seconds_total", 0),
              "レンダリング全体の所要時間は切り抜き時間以上になる"
              f"（render_seconds={timing.get('render_seconds')}, "
              f"extract_seconds_total={timing.get('extract_seconds_total')}）")

        print("")
        print("=== render_timing.json：2回目（キャッシュヒット）はcache_used=Trueで記録される ===")
        out_auto2 = os.path.join(tmpdir, "auto_out2.mp4")
        old_cwd_auto2 = os.getcwd()
        try:
            os.chdir(tmpdir)
            render.render(loaded_auto, auto_json_path, auto_video_path, out_auto2)
        finally:
            os.chdir(old_cwd_auto2)
        with open(timing_path, encoding="utf-8") as f:
            timing2 = json_mod.load(f)
        fz_timing2 = timing2["freezes"][0]
        check(fz_timing2["cache_used"] is True and fz_timing2["extract_seconds"] is None,
              f"2回目はキャッシュヒットとして記録される: {fz_timing2}")

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

    # ------------------------------------------------------------------
    # 7) 効果音のカスタムファイル対応（オブジェクト形式のsfx・align位置合わせ）
    # ------------------------------------------------------------------
    print("")
    print("=== 効果音：resolve_sfx_spec（文字列プリセット/オブジェクト形式の解決） ===")
    sfx_json_dir = os.path.join(tmpdir, "sfx_json_dir")
    os.makedirs(os.path.join(sfx_json_dir, "sfx"), exist_ok=True)
    riser_samples, riser_content_len = md.synth_test_riser()
    riser_path = os.path.join(sfx_json_dir, "sfx", "riser.wav")
    md.write_wav(riser_path, riser_samples)

    preset_spec = render.resolve_sfx_spec("shakin", sfx_json_dir, render.SFX_ALIGNS_FREEZE, "test")
    check(preset_spec is not None and preset_spec["is_custom"] is False and
          preset_spec["align"] == "start_at_landing" and os.path.exists(preset_spec["path"]),
          f"resolve_sfx_spec：文字列プリセットはis_custom=False・align既定で解決される: {preset_spec}")

    custom_spec = render.resolve_sfx_spec(
        {"file": "sfx/riser.wav", "align": "end_at_landing"}, sfx_json_dir, render.SFX_ALIGNS_FREEZE, "test")
    check(custom_spec is not None and custom_spec["is_custom"] is True and
          custom_spec["align"] == "end_at_landing" and custom_spec["path"] == riser_path,
          f"resolve_sfx_spec：オブジェクト形式はjson_dir基準でfileを解決し、is_custom=True・指定alignを返す: {custom_spec}")

    bad_align_spec = render.resolve_sfx_spec(
        {"file": "sfx/riser.wav", "align": "no-such-align"}, sfx_json_dir, render.SFX_ALIGNS_FREEZE, "test")
    check(bad_align_spec is not None and bad_align_spec["align"] == "start_at_landing",
          f"resolve_sfx_spec：不明なalignは既定(start_at_landing)にフォールバックする: {bad_align_spec}")

    check(render.resolve_sfx_spec({"align": "end_at_landing"}, sfx_json_dir,
                                   render.SFX_ALIGNS_FREEZE, "test") is None,
          "resolve_sfx_spec：fileが無いオブジェクトはNoneを返す")
    check(render.resolve_sfx_spec("no-such-preset", sfx_json_dir, render.SFX_ALIGNS_FREEZE, "test") is None,
          "resolve_sfx_spec：存在しないプリセット名はNoneを返す")
    check(render.resolve_sfx_spec(None, sfx_json_dir, render.SFX_ALIGNS_FREEZE, "test") is None,
          "resolve_sfx_spec：sfx指定が無ければNoneを返す")

    print("")
    print("=== 効果音：位置合わせの計算（trailing_silence_trimmed_length/peak_sample_index/compute_sfx_start） ===")
    loud = np.full((1000, 2), 0.5, np.float32)
    silent = np.zeros((300, 2), np.float32)
    mixed = np.concatenate([loud, silent], axis=0)
    got_trim = render.trailing_silence_trimmed_length(mixed)
    check(got_trim == 1000,
          f"trailing_silence_trimmed_length：末尾の無音サンプル数を正確に除いた長さを返す: {got_trim}")
    all_silent = np.zeros((500, 2), np.float32)
    check(render.trailing_silence_trimmed_length(all_silent) == 500,
          "trailing_silence_trimmed_length：全体が無音なら安全側でlen(samples)をそのまま返す")

    peak_arr = np.zeros((1000, 2), np.float32)
    peak_arr[420, 0] = 0.9
    peak_arr[420, 1] = -0.3  # 絶対値としては0チャンネルの方が大きいので、argmaxは420のままのはず
    got_peak = render.peak_sample_index(peak_arr)
    check(got_peak == 420, f"peak_sample_index：全チャンネル中の絶対値最大のサンプル位置を返す: {got_peak}")

    check(render.compute_sfx_start(loud, 5000, "start_at_landing") == 5000,
          "compute_sfx_start：start_at_landingはlanding_sampleそのまま")
    check(render.compute_sfx_start(mixed, 5000, "end_at_landing") == 5000 - 1000,
          "compute_sfx_start：end_at_landingはlanding_sample-trailing_silence_trimmed_length")
    check(render.compute_sfx_start(peak_arr, 5000, "peak_at_landing") == 5000 - 420,
          "compute_sfx_start：peak_at_landingはlanding_sample-peak_sample_index")

    loud_flat = np.full((1000, 2), 0.4, np.float32)
    normalized = render.normalize_peak_dbfs(loud_flat, target_db=-1.0)
    got_peak_db = 20.0 * float(np.log10(np.max(np.abs(normalized))))
    check(abs(got_peak_db - (-1.0)) < 1e-4,
          f"normalize_peak_dbfs：正規化後のピークが指定dBFSになる: {got_peak_db:.4f}dBFS")

    print("")
    print("=== 効果音：ライザー音（align=end_at_landing）が実際のミックスで着地位置に音の終わりが来る ===")
    fz_riser = make_dot_freeze(shadow=None, extra={
        "sfx": {"file": "sfx/riser.wav", "align": "end_at_landing"}})
    plan_riser = render.plan_freezes([fz_riser], fps, src_frames, sfx_json_dir)[0]
    check(plan_riser["sfx"] is not None and plan_riser["sfx"]["align"] == "end_at_landing" and
          plan_riser["sfx"]["is_custom"] is True,
          f"plan_freezes：カスタムsfxオブジェクトがjson_dir基準で解決され、alignが伝わる: {plan_riser['sfx']}")

    audio_riser = render.build_audio(video_path, [plan_riser], fps, src_frames, True)
    landed_frame_offset_riser = plan_riser["n_pre"] + plan_riser["n_reveal"] + plan_riser.get("n_slide_in", 0)
    landing_sample_riser = render.frames_to_samples(
        plan_riser["frame_index"] + landed_frame_offset_riser, fps, render.AUDIO_SR)
    expected_start_riser = landing_sample_riser - riser_content_len
    check(expected_start_riser >= 0,
          f"align=end_at_landing：この検証用フリーズの着地位置なら開始位置が負にならない"
          f"（0クランプに頼らずずれを検証できる）: {expected_start_riser}")
    just_before = audio_riser[max(0, landing_sample_riser - 200):landing_sample_riser]
    just_after = audio_riser[landing_sample_riser + 50:landing_sample_riser + 2000]
    check(bool(np.any(np.abs(just_before) > 0.01)),
          "align=end_at_landing：着地の直前はまだライザー音が鳴っている（終わりが着地に一致）")
    check(bool(np.max(np.abs(just_after)) < 0.01),
          "align=end_at_landing：着地の直後はライザーの無音区間に入り静かになる")

    print("")
    print("=== 効果音：衝撃音（align=peak_at_landing、ロゴ用）がロゴ着地にピークを合わせる ===")
    impact_samples, impact_peak_idx = md.synth_test_impact_offset_peak()
    impact_path = os.path.join(sfx_json_dir, "sfx", "impact_offset.wav")
    md.write_wav(impact_path, impact_samples)

    logo_fz_peak = dict(base_style)
    logo_fz_peak.update({"time": 2.5, "name": "", "sfx": None, "strokes": [], "shadow": None})
    # sfx_tail=false：着地SEの減衰ディレイ（apply_reverb_tail）は、原音のピークより後ろに
    # 減衰コピーを重ねる別の演出であり、ミックス後の「実測ピーク位置」を数十サンプル動かし
    # うる（このテストが検証したいのは位置合わせの計算そのものなので、無効化して切り分ける）
    logo_cfg_peak = {"image": "store_logo.png", "at": "last_freeze", "sfx_tail": False,
                      "sfx": {"file": "sfx/impact_offset.wav", "align": "peak_at_landing"}}
    logo_params_peak = render.resolve_logo_params(logo_cfg_peak)
    logo_sfx_spec_peak = render.resolve_sfx_spec(
        logo_cfg_peak["sfx"], sfx_json_dir, render.SFX_ALIGNS_LOGO, "logo")
    check(logo_sfx_spec_peak is not None and logo_sfx_spec_peak["align"] == "peak_at_landing" and
          logo_sfx_spec_peak["is_custom"] is True,
          f"resolve_sfx_spec：ロゴ用オブジェクトsfxもjson_dir基準で解決され、peak_at_landingが伝わる: "
          f"{logo_sfx_spec_peak}")

    plans_logo_peak = render.plan_freezes(
        [logo_fz_peak], fps, src_frames, sfx_json_dir, logo=logo_cfg_peak, logo_at="last_freeze",
        logo_total_frames=render.logo_total_frames_for(logo_params_peak, fps), logo_blackout_frames=0)
    plan0_peak = plans_logo_peak[0]
    check(plan0_peak["show_logo"], "テスト前提：このフリーズでラストロゴが表示される設定になっている")

    audio_peak = render.build_audio(video_path, plans_logo_peak, fps, src_frames, True,
                                     logo_sfx_spec=logo_sfx_spec_peak, logo_at="last_freeze",
                                     logo_params=logo_params_peak)
    landed_frame_offset_peak = plan0_peak["n_pre"] + plan0_peak["n_reveal"] + plan0_peak.get("n_slide_in", 0)
    landing_frames_peak = int(round(render.logo_landing_instant_sec(logo_params_peak) * fps))
    logo_landing_sample = render.frames_to_samples(
        plan0_peak["frame_index"] + landed_frame_offset_peak + landing_frames_peak, fps, render.AUDIO_SR)
    search_window = audio_peak[max(0, logo_landing_sample - 200):logo_landing_sample + 200]
    measured_peak_offset = int(np.argmax(np.max(np.abs(search_window), axis=1)))
    measured_peak_sample = max(0, logo_landing_sample - 200) + measured_peak_offset
    check(abs(measured_peak_sample - logo_landing_sample) <= 2,
          f"align=peak_at_landing：ミックス後の衝撃音のピークがロゴ着地位置と一致する"
          f"（着地={logo_landing_sample}, 実測ピーク={measured_peak_sample}）")

    # ------------------------------------------------------------------
    # 9) reveal_sec=0：ブラシの塗りアニメを即時化できる
    # ------------------------------------------------------------------
    print("")
    print("=== reveal_sec=0：ブラシの塗りアニメを即時化できる（①塗りフレーム無し・白い絵の具の描画も無し） ===")
    fz_instant = make_dot_freeze(shadow=None, extra={"reveal_sec": 0.0, "brush_fade_sec": 0.5})
    plan_instant = render.plan_freezes([fz_instant], fps, src_frames, ".")[0]
    check(plan_instant["n_reveal"] == 0,
          f"reveal_sec=0はn_reveal=0になる（他フェーズと違い1フレームに切り上げない）: {plan_instant['n_reveal']}")

    frame_instant = render.grab_frame_at(video_path, plan_instant["frame_index"] / fps, W, H, fps)
    frames_instant = list(render.iter_freeze_frames(frame_instant, plan_instant, W, H, fps, {}))
    first_after_pre = frames_instant[plan_instant["n_pre"]]

    ctx_instant = render.build_mask_context(fz_instant, frame_instant, W, H,
                                             os.path.join(tmpdir, "cache_instant"), video_path)
    done_mask_instant, _ = render.mask_and_paint_at(ctx_instant, W, H, 1.0)
    bg_instant = render.make_background(frame_instant, fz_instant.get("background", "mono"),
                                         float(fz_instant.get("mono_contrast", 1.0)))
    expected_instant = render.composite_layers(bg_instant, frame_instant, done_mask_instant, W, H)
    diff_instant = int(np.abs(first_after_pre.astype(int) - expected_instant.astype(int)).sum())
    check(diff_instant == 0,
          f"reveal_sec=0：静止直後のフレームがいきなり完全カラー化された状態と一致する（塗りアニメ無し）: diff={diff_instant}")

    last_frame_instant = frames_instant[-1]
    diff_paint = int(np.abs(first_after_pre.astype(int) - last_frame_instant.astype(int)).sum())
    check(diff_paint == 0,
          f"reveal_sec=0：brush_fade_secを設定していても白い絵の具のフェードは発生しない"
          f"（hold開始フレーム=hold終端フレーム): diff={diff_paint}")

    # ------------------------------------------------------------------
    # 10) テロップ文字色：lines[].color / style.title_color と自動縁取り色
    # ------------------------------------------------------------------
    print("")
    print("=== テロップ文字色：lines[].color（#RRGGBB）と自動選択される縁取り色 ===")
    check(render.validate_hex_color("#e6c15c", "#FFFFFF", "test") == "#e6c15c",
          "validate_hex_color：妥当な#RRGGBBはそのまま使われる")
    check(render.validate_hex_color("not-a-color", "#FFFFFF", "test") == "#FFFFFF",
          "validate_hex_color：不正な#RRGGBB文字列は既定色にフォールバックする")
    check(render.auto_outline_rgb((0xE6, 0xC1, 0x5C)) == (0, 0, 0),
          "auto_outline_rgb：明るい金色(#E6C15C)には黒い縁取りが自動選択される")
    check(render.auto_outline_rgb((0xFF, 0x3B, 0x30)) == (255, 255, 255),
          "auto_outline_rgb：赤(#FF3B30)には白い縁取りが自動選択される")
    check(render.resolve_title_outline_color(freezes[0]) is None,
          "resolve_title_outline_color：title_outline_color未指定はNone（行ごとの自動選択に任せる）")
    override_fz = dict(freezes[0])
    override_fz["title_outline_color"] = "#00FF00"
    check(render.resolve_title_outline_color(override_fz) == (0, 255, 0),
          "resolve_title_outline_color：明示指定は自動選択より優先される")

    color_lines = render.resolve_title_lines(
        {"name": {"lines": [{"text": "ゴールド", "color": "#E6C15C"}, {"text": "レッド", "color": "#FF3B30"}]}})
    color_layers = render.render_telop_line_layers(
        color_lines, W2, H2, {}, font_path_default, size_px_base)

    def dominant_bgr(layer, alpha_thresh=0.8):
        mask = layer["alpha"][:, :, 0] > alpha_thresh
        return layer["bgr"][mask].mean(axis=0) if mask.any() else None

    gold_bgr = dominant_bgr(color_layers[0])
    red_bgr = dominant_bgr(color_layers[1])
    check(gold_bgr is not None and abs(gold_bgr[2] - 0xE6) < 10 and abs(gold_bgr[1] - 0xC1) < 10
          and abs(gold_bgr[0] - 0x5C) < 10,
          f"1行目（color=#E6C15C）の文字本体の色がgoldに一致する（BGR平均={gold_bgr}）")
    check(red_bgr is not None and abs(red_bgr[2] - 0xFF) < 10 and abs(red_bgr[1] - 0x3B) < 10
          and abs(red_bgr[0] - 0x30) < 10,
          f"2行目（color=#FF3B30）の文字本体の色がredに一致する（BGR平均={red_bgr}）")

    # ------------------------------------------------------------------
    # 10.5) 行ごとの寄せ（lines[].align）
    # ------------------------------------------------------------------
    print("")
    print("=== 行ごとの寄せ：lines[].alignがブロック幅の中で行ごとに適用される（省略時は全体title_alignを継承） ===")
    align_lines = render.resolve_title_lines({"name": {"lines": [
        {"text": "LEFT", "align": "left"},
        {"text": "CENTERLINE"},
        {"text": "RIGHT", "align": "right"},
        {"text": "BAD", "align": "top"},
    ]}})
    check(align_lines[0]["align"] == "left" and align_lines[1]["align"] is None
          and align_lines[2]["align"] == "right" and align_lines[3]["align"] is None,
          f"resolve_title_lines：align指定はそのまま保持、未指定/不明値はNone: "
          f"{[l['align'] for l in align_lines]}")

    align_layers = render.render_telop_line_layers(
        align_lines, W2, H2, {}, font_path_default, size_px_base, (0.5, 0.5), "center")
    block_center_x = W2 * 0.5
    left_cx, center_cx, right_cx, inherit_cx = (l["cx"] for l in align_layers)
    check(left_cx < block_center_x, f"align='left'の行はブロック中心より左に配置される: cx={left_cx}")
    check(right_cx > block_center_x, f"align='right'の行はブロック中心より右に配置される: cx={right_cx}")
    check(abs(center_cx - block_center_x) < 1.0,
          f"align未指定（このフリーズのtitle_align='center'を継承）の行はブロック中心に配置される: cx={center_cx}")
    check(abs(inherit_cx - block_center_x) < 1.0,
          f"align='top'（不明値）もtitle_align='center'継承と同じ扱いになる: cx={inherit_cx}")

    # 後方互換の確認：全行がブロック全体と同じalignを明示した場合、align省略（継承）の場合と
    # 完全に同じ見た目（bgr/alpha/cx/cy）になる＝行ごとのalign機構が既存の単一align指定の
    # 挙動を壊していないことを保証する。
    explicit_lines = [dict(l, align="center") for l in align_lines[:2]]  # LEFT/CENTERLINEをcenterに揃える
    implicit_lines = [dict(l, align=None) for l in align_lines[:2]]
    explicit_layers = render.render_telop_line_layers(
        explicit_lines, W2, H2, {}, font_path_default, size_px_base, (0.5, 0.5), "center")
    implicit_layers = render.render_telop_line_layers(
        implicit_lines, W2, H2, {}, font_path_default, size_px_base, (0.5, 0.5), "center")
    same = all(
        e["cx"] == i["cx"] and e["cy"] == i["cy"] and np.array_equal(e["bgr"], i["bgr"])
        and np.array_equal(e["alpha"], i["alpha"])
        for e, i in zip(explicit_layers, implicit_layers))
    check(same, "全行のalignをブロック全体のtitle_alignと明示的に同じ値にすると、"
          "align省略（継承）時と完全に同じ結果になる（後方互換）")

    # ------------------------------------------------------------------
    # 11) 行ごとのテロップ出現アクション：anim/anim_sec/delay_sec
    # ------------------------------------------------------------------
    print("")
    print("=== 行ごとのテロップ出現アクション：anim='none'は即時表示、delay_secぶん2行目の出現が遅れる ===")
    fz_lines = make_dot_freeze(shadow=None, extra={
        "reveal_sec": 0.0, "hold_sec": 1.0,
        "name": {"lines": [
            {"text": "ライン1", "anim": "none"},
            {"text": "ライン2", "anim": "fade", "anim_sec": 0.4, "delay_sec": 0.3},
        ]},
    })
    plan_lines = render.plan_freezes([fz_lines], fps, src_frames, ".")[0]
    frame_lines = render.grab_frame_at(video_path, plan_lines["frame_index"] / fps, W, H, fps)
    frames_lines = list(render.iter_freeze_frames(frame_lines, plan_lines, W, H, fps, {}))

    fz_no_telop = dict(fz_lines)
    fz_no_telop["name"] = ""
    plan_no_telop = render.plan_freezes([fz_no_telop], fps, src_frames, ".")[0]
    frames_no_telop = list(render.iter_freeze_frames(frame_lines, plan_no_telop, W, H, fps, {}))

    layers_lines = render.render_telop_line_layers(
        render.resolve_title_lines(fz_lines), W, H, {}, render.resolve_title_font_path(fz_lines),
        max(8, int(round(H * fz_lines.get("title_size", render.DEFAULT_STYLE["title_size"])))),
        fz_lines.get("title_pos") or render.DEFAULT_STYLE["title_pos"],
        fz_lines.get("title_align", render.DEFAULT_STYLE["title_align"]))
    check(len(layers_lines) == 2, "2行分のテロップレイヤーが得られる")

    def region_diff(frame_a, frame_b, layer, alpha_thresh=0.3):
        mask = layer["alpha"][:, :, 0] > alpha_thresh
        return int(np.abs(frame_a[mask].astype(int) - frame_b[mask].astype(int)).sum())

    n_pre_lines = plan_lines["n_pre"]
    diff_line1_at_start = region_diff(frames_lines[n_pre_lines], frames_no_telop[n_pre_lines], layers_lines[0])
    check(diff_line1_at_start > 0,
          f"anim='none'の1行目は着地直後（holdの最初のフレーム）から表示されている: diff={diff_line1_at_start}")

    diff_line2_at_start = region_diff(frames_lines[n_pre_lines], frames_no_telop[n_pre_lines], layers_lines[1])
    check(diff_line2_at_start == 0,
          f"delay_sec=0.3の2行目は着地直後はまだ表示されない: diff={diff_line2_at_start}")

    after_delay_idx = n_pre_lines + int(round(0.35 * fps))
    diff_line2_after_delay = region_diff(
        frames_lines[after_delay_idx], frames_no_telop[after_delay_idx], layers_lines[1])
    check(diff_line2_after_delay > 0,
          f"delay_sec=0.3秒経過後は2行目も表示され始める: diff={diff_line2_after_delay}")

    # ------------------------------------------------------------------
    # 12) 行ごとの効果音（lines[].sfx）：その行の出現開始（delay_sec経過後）に鳴る
    # ------------------------------------------------------------------
    print("")
    print("=== 効果音：行ごとのsfx（lines[].sfx）がその行の出現開始（delay_sec経過後）に鳴る ===")
    fz_sfx_lines = make_dot_freeze(shadow=None, extra={
        "reveal_sec": 0.0, "hold_sec": 1.0,
        "name": {"lines": [
            {"text": "1行目"},
            {"text": "2行目", "delay_sec": 0.3, "sfx": "shakin"},
        ]},
    })
    plans_sfx_lines = render.plan_freezes([fz_sfx_lines], fps, src_frames, ".")
    plan0_sfx_lines = plans_sfx_lines[0]
    check(len(plan0_sfx_lines["line_sfx"]) == 1 and abs(plan0_sfx_lines["line_sfx"][0][0] - 0.3) < 1e-9,
          f"plan_freezes：行ごとのsfxが(delay_sec, spec)としてline_sfxに集約される: {plan0_sfx_lines['line_sfx']}")

    audio_sfx_lines = render.build_audio(video_path, plans_sfx_lines, fps, src_frames, True)
    landed_frame_offset_lines = (plan0_sfx_lines["n_pre"] + plan0_sfx_lines["n_reveal"]
                                  + plan0_sfx_lines.get("n_slide_in", 0))
    line_sfx_frame = plan0_sfx_lines["frame_index"] + landed_frame_offset_lines + int(round(0.3 * fps))
    line_sfx_sample = render.frames_to_samples(line_sfx_frame, fps, render.AUDIO_SR)
    before_window = audio_sfx_lines[max(0, line_sfx_sample - 4000):line_sfx_sample]
    after_window = audio_sfx_lines[line_sfx_sample:line_sfx_sample + 4000]
    check(bool(np.max(np.abs(before_window)) < 0.02),
          f"行ごとのsfx：2行目の出現（delay_sec=0.3秒）より前は無音: peak={float(np.max(np.abs(before_window))):.4f}")
    check(bool(np.max(np.abs(after_window)) > 0.05),
          f"行ごとのsfx：2行目の出現の瞬間から効果音が鳴り始める: peak={float(np.max(np.abs(after_window))):.4f}")

    # ------------------------------------------------------------------
    # 13) 回帰確認：lines[]にanimを指定しない旧形式は、title_bounceに応じた
    #     従来どおりのbounce/fadeとTELOP_FADE_SEC(0.15秒)のタイミングをビット単位で再現する
    # ------------------------------------------------------------------
    print("")
    print("=== 回帰確認：旧形式（lines[]にanim無し／文字列name）は従来のbounce/fadeとビット単位で一致する ===")
    for bounce_flag, label in ((False, "title_bounce=False（フェードのみ）"), (True, "title_bounce=True（バウンス）")):
        fz_legacy = make_dot_freeze(shadow=None, extra={
            "reveal_sec": 0.0, "hold_sec": 1.0, "title_bounce": bounce_flag, "name": "旧形式のテロップ",
        })
        plan_legacy, frames_legacy = render_freeze_frames(fz_legacy)
        n_pre_legacy = plan_legacy["n_pre"]
        check_i = 2   # elapsed_hold_sec = 3/fps秒 ≈ TELOP_FADE_SEC(0.15秒)の途中
        elapsed = (check_i + 1) / fps
        expected_fade = min(1.0, elapsed / render.TELOP_FADE_SEC)

        frame0 = render.grab_frame_at(video_path, plan_legacy["frame_index"] / fps, W, H, fps)
        bg0 = render.make_background(frame0, fz_legacy.get("background", "mono"),
                                      float(fz_legacy.get("mono_contrast", 1.0)))
        ctx0 = render.build_mask_context(fz_legacy, frame0, W, H,
                                          os.path.join(tmpdir, "cache_legacy"), video_path)
        done_mask0, _ = render.mask_and_paint_at(ctx0, W, H, 1.0)
        landed0 = render.composite_layers(bg0, frame0, done_mask0, W, H)
        layers0 = render.render_telop_line_layers(
            render.resolve_title_lines(fz_legacy), W, H, {}, render.resolve_title_font_path(fz_legacy),
            max(8, int(round(H * fz_legacy.get("title_size", render.DEFAULT_STYLE["title_size"])))),
            fz_legacy.get("title_pos") or render.DEFAULT_STYLE["title_pos"],
            fz_legacy.get("title_align", render.DEFAULT_STYLE["title_align"]))
        check(len(layers0) == 1, "旧形式（文字列name）は1行のレイヤーになる")
        scale0 = render.telop_bounce_scale(expected_fade) if bounce_flag and expected_fade < 1.0 else 1.0
        bgr0, alpha0 = render.transform_telop_layer(
            layers0[0]["bgr"], layers0[0]["alpha"], scale0, 0.0, 0.0, layers0[0]["cx"], layers0[0]["cy"])
        expected_frame = render.blend_telop(landed0, bgr0, alpha0, expected_fade)
        actual_frame = frames_legacy[n_pre_legacy + check_i]
        diff = int(np.abs(expected_frame.astype(int) - actual_frame.astype(int)).sum())
        check(diff == 0,
              f"{label}：新しい行ごとの合成でも、旧式の単一ブロック計算"
              f"（fade={expected_fade:.4f}, scale={scale0:.4f}）とビット単位で一致する: diff={diff}")

    # ------------------------------------------------------------------
    # 14) --preview の3枚目：行ごとのアニメ確認PNG（1行目が到達済み・2行目が移動中）
    # ------------------------------------------------------------------
    print("")
    print("=== --preview：行ごとのアニメが異なる2行以上のテロップでは3枚目（_lines）のPNGも書き出す ===")
    import json as json_mod2
    lines_preview_project = {
        "version": 1, "video": "dummy_input.mp4",
        "output": {"width": W, "height": H},
        "style": {"hold_sec": 2.0, "audio_during_freeze": "mute"},
        "freezes": [{
            "time": 2.5, "reveal_sec": 0.0,
            "name": {"lines": [
                {"text": "1行目", "anim": "slide_right", "anim_sec": 0.5},
                {"text": "2行目", "anim": "slide_left", "anim_sec": 0.6, "delay_sec": 0.3},
            ]},
            "strokes": [{"width": 0.1, "points": [[0.5, 0.5], [0.5, 0.55]]}],
        }],
    }
    lines_preview_json_path = os.path.join(tmpdir, "lines_preview_project.json")
    with open(lines_preview_json_path, "w", encoding="utf-8") as f:
        json_mod2.dump(lines_preview_project, f, ensure_ascii=False)
    loaded_lines_preview = render.load_project(lines_preview_json_path)
    lines_preview_png_arg = os.path.join(tmpdir, "lines_anim_preview.png")
    _bp, _ap, lines_anim_path = render.render(
        loaded_lines_preview, lines_preview_json_path, video_path, "unused.mp4",
        preview_path=lines_preview_png_arg)
    check(lines_anim_path == os.path.join(tmpdir, "lines_anim_preview_lines.png"),
          f"行アニメ確認PNGのパスが_lines付きになる: {lines_anim_path}")
    check(lines_anim_path is not None and os.path.exists(lines_anim_path)
          and os.path.getsize(lines_anim_path) > 0,
          "行アニメ確認PNGが実際に書き出される")

    # ------------------------------------------------------------------
    # 15) 背景（人物以外）の塗りの種類：resolve_background_mode/resolve_background_options
    #     のフォールバックと、8種のbackgroundモードの出力を検証する
    # ------------------------------------------------------------------
    print("")
    print("=== 背景の塗りの種類：resolve_background_mode/resolve_background_optionsのフォールバック ===")
    check(render.resolve_background_mode("halftone") == "halftone", "既知の値はそのまま通す")
    check(render.resolve_background_mode("unknown-mode") == "mono", "未知の値はmonoにフォールバックする")
    check(render.resolve_background_mode(None) == "mono", "Noneはmonoにフォールバックする")

    default_opts = render.resolve_background_options({})
    check(default_opts == dict(render.BACKGROUND_OPTIONS_DEFAULTS),
          "background_options省略時はBACKGROUND_OPTIONS_DEFAULTSがそのまま使われる")
    bad_opts = render.resolve_background_options({"background_options": {
        "base": "not-a-color", "accent": "also-bad", "scale": 0, "angle": None, "opacity": 5}})
    check(bad_opts["base"] == render.BACKGROUND_OPTIONS_DEFAULTS["base"], "不正なbaseは既定色にフォールバックする")
    check(bad_opts["accent"] == render.BACKGROUND_OPTIONS_DEFAULTS["accent"], "不正なaccentは既定色にフォールバックする")
    check(bad_opts["scale"] > 0, "scale=0は下限でクランプされ0除算にならない")
    check(bad_opts["opacity"] == 1.0, "opacity=5は1.0にクランプされる")

    print("")
    print("=== 背景の塗りの種類：flat/gradient/halftone/stripes/grid/grainの出力を検証 ===")
    bg_h, bg_w = 120, 160
    synth_frame = np.full((bg_h, bg_w, 3), 230, dtype=np.uint8)   # 明るい下地
    synth_frame[40:80, 20:60] = (10, 10, 10)                       # 暗い正方形（halftoneのドット径判定に使う）
    opts_bw = render.resolve_background_options({"background_options": {
        "base": "#FFFFFF", "accent": "#000000", "scale": 0.05, "angle": 30.0, "opacity": 1.0}})

    flat_bg = render.make_background(synth_frame, "flat", options=opts_bw)
    check(bool(np.all(flat_bg == (255, 255, 255))),
          "flat：background_options.baseの単色でベタ塗りされる（BGR全画素が白）")

    grad_bg = render.make_background(synth_frame, "gradient", options=opts_bw)
    check(bool(np.all(grad_bg[0] == (255, 255, 255))), "gradient：最上段はbase色になる")
    check(bool(np.all(grad_bg[-1] == (0, 0, 0))), "gradient：最下段はaccent色になる")
    mid_val = int(grad_bg[bg_h // 2, 0, 0])
    check(0 < mid_val < 255, f"gradient：中間の行はbase/accentの間の値になる: {mid_val}")

    halftone_bg = render.make_background(synth_frame, "halftone", options=opts_bw)
    dark_area = halftone_bg[40:80, 20:60]
    light_area = halftone_bg[0:20, 0:20]
    dark_accent_ratio = float(np.mean(np.all(dark_area == (0, 0, 0), axis=-1)))
    light_accent_ratio = float(np.mean(np.all(light_area == (0, 0, 0), axis=-1)))
    check(dark_accent_ratio > light_accent_ratio,
          "halftone：元フレームが暗い領域ほどaccent色のドットが大きく（面積比が高く）なる: "
          f"dark={dark_accent_ratio:.3f} light={light_accent_ratio:.3f}")
    has_base = bool(np.any(np.all(halftone_bg == (255, 255, 255), axis=-1)))
    has_accent = bool(np.any(np.all(halftone_bg == (0, 0, 0), axis=-1)))
    check(has_base and has_accent, "halftone：base地とaccent色のドットの両方が出力に含まれる")

    stripes_bg = render.make_background(synth_frame, "stripes", options=opts_bw)
    grid_bg = render.make_background(synth_frame, "grid", options=opts_bw)
    stripes_accent_ratio = float(np.mean(np.all(stripes_bg == (0, 0, 0), axis=-1)))
    grid_accent_ratio = float(np.mean(np.all(grid_bg == (0, 0, 0), axis=-1)))
    check(0.3 < stripes_accent_ratio < 0.7,
          f"stripes：base/accentがおおよそ半々の面積比になる: {stripes_accent_ratio:.3f}")
    check(grid_accent_ratio > stripes_accent_ratio,
          "grid：直交する縞を重ねるため、stripesよりaccent面積比が大きくなる: "
          f"grid={grid_accent_ratio:.3f} stripes={stripes_accent_ratio:.3f}")

    mono_bg = render.make_background(synth_frame, "mono", 1.0)
    grain_bg_low = render.make_background(synth_frame, "grain", options=render.resolve_background_options(
        {"background_options": {"opacity": 0.0}}))
    grain_bg_high = render.make_background(synth_frame, "grain", options=render.resolve_background_options(
        {"background_options": {"opacity": 1.0}}))
    diff_low = int(np.abs(grain_bg_low.astype(int) - mono_bg.astype(int)).sum())
    diff_high = int(np.abs(grain_bg_high.astype(int) - mono_bg.astype(int)).sum())
    check(diff_low == 0, f"grain：opacity=0はノイズ無しでmonoと一致する: diff={diff_low}")
    check(diff_high > diff_low,
          f"grain：opacityが大きいほどノイズ（monoとの差）が大きくなる: diff_high={diff_high}")

    unknown_bg = render.make_background(synth_frame, "not-a-real-mode", 1.0)
    check(bool(np.array_equal(unknown_bg, mono_bg)),
          "make_background：未知のbackground値はmonoとして扱われる")

    print("")
    print("=== 背景の塗りの種類：旧JSON（background='mono'/'dark'のみ・background_options無し）は従来どおり ===")
    legacy_mono = render.make_background(synth_frame, "mono", 1.0)
    legacy_mono_default_opts = render.make_background(synth_frame, "mono", 1.0,
                                                        options=render.resolve_background_options({}))
    check(bool(np.array_equal(legacy_mono, legacy_mono_default_opts)),
          "mono：background_options省略時と既定値明示時で出力が完全一致する（後方互換）")
    legacy_dark = render.make_background(synth_frame, "dark", 1.0)
    legacy_dark_default_opts = render.make_background(synth_frame, "dark", 1.0,
                                                        options=render.resolve_background_options({}))
    check(bool(np.array_equal(legacy_dark, legacy_dark_default_opts)),
          "dark：background_options省略時と既定値明示時で出力が完全一致する（後方互換）")

    # ------------------------------------------------------------------
    # 16) 人物マスクの縁の種類（mask_style）：resolve_mask_style/resolve_mask_style_options
    #     のフォールバックと、composite_layersでの5種の見た目・影への適用を検証する
    # ------------------------------------------------------------------
    print("")
    print("=== 人物マスクの縁の種類：resolve_mask_style/resolve_mask_style_optionsのフォールバック ===")
    check(render.resolve_mask_style("halftone") == "halftone", "既知の値はそのまま通す")
    check(render.resolve_mask_style("unknown-style") == "solid", "未知の値はsolidにフォールバックする")
    check(render.resolve_mask_style(None) == "solid", "Noneはsolidにフォールバックする")

    default_mask_opts = render.resolve_mask_style_options({})
    check(default_mask_opts == dict(render.MASK_STYLE_OPTIONS_DEFAULTS),
          "mask_style_options省略時はMASK_STYLE_OPTIONS_DEFAULTSがそのまま使われる")
    bad_mask_opts = render.resolve_mask_style_options({"mask_style_options": {
        "scale": 0, "color": "not-a-color", "width": -1}})
    check(bad_mask_opts["scale"] > 0, "scale=0は下限でクランプされ0除算にならない")
    check(bad_mask_opts["color"] == render.MASK_STYLE_OPTIONS_DEFAULTS["color"],
          "不正なcolorは既定色にフォールバックする")
    check(bad_mask_opts["width"] > 0, "widthが負の値でも下限でクランプされる")

    print("")
    print("=== 人物マスクの縁の種類：composite_layersでsolid/halftone/pixel/outline/roughの見た目を検証 ===")
    ms_h, ms_w = 200, 200
    ms_bg = np.full((ms_h, ms_w, 3), 200, dtype=np.uint8)
    ms_color = np.full((ms_h, ms_w, 3), (255, 120, 40), dtype=np.uint8)
    ms_mask = np.zeros((ms_h, ms_w), dtype=np.uint8)
    cv2.circle(ms_mask, (ms_w // 2, ms_h // 2), 60, 255, -1, lineType=cv2.LINE_AA)
    ms_opts = render.resolve_mask_style_options({"mask_style_options": {
        "scale": 0.02, "color": "#00FF00", "width": 0.02}})

    solid_out = render.composite_layers(ms_bg, ms_color, ms_mask, ms_w, ms_h, mask_style="solid")
    unknown_out = render.composite_layers(ms_bg, ms_color, ms_mask, ms_w, ms_h, mask_style="not-a-style")
    check(bool(np.array_equal(solid_out, unknown_out)),
          "composite_layers：未知のmask_styleはsolidとして扱われる（フォールバック）")
    default_out = render.composite_layers(ms_bg, ms_color, ms_mask, ms_w, ms_h)
    check(bool(np.array_equal(solid_out, default_out)),
          "composite_layers：mask_style省略時はsolid（既存の丸い縁のまま）と完全一致する（後方互換）")

    halftone_out = render.composite_layers(ms_bg, ms_color, ms_mask, ms_w, ms_h,
                                            mask_style="halftone", mask_style_options=ms_opts)
    check(bool(np.any(halftone_out != solid_out)), "halftone：solidと異なる見た目になる（縁がドットに溶ける）")
    center_solid = solid_out[ms_h // 2, ms_w // 2]
    # ドットの中心/隙間どちらに当たるかでピクセル単位ではブレるため、中心付近の
    # 小さなパッチ平均で比較する（内部は依然ほぼ人物色を保っているはず）
    patch_solid_mean = solid_out[ms_h // 2 - 5:ms_h // 2 + 5, ms_w // 2 - 5:ms_w // 2 + 5].mean(axis=(0, 1))
    patch_halftone_mean = halftone_out[ms_h // 2 - 5:ms_h // 2 + 5, ms_w // 2 - 5:ms_w // 2 + 5].mean(axis=(0, 1))
    check(float(np.abs(patch_solid_mean - patch_halftone_mean).sum()) < 90,
          "halftone：マスク中心（完全に不透明な内部）は引き続きほぼ人物色のまま（周辺パッチ平均で比較）")

    pixel_out = render.composite_layers(ms_bg, ms_color, ms_mask, ms_w, ms_h,
                                         mask_style="pixel", mask_style_options=ms_opts)
    check(bool(np.any(pixel_out != solid_out)), "pixel：solidと異なる見た目になる（縁がブロック状になる）")

    rough_out = render.composite_layers(ms_bg, ms_color, ms_mask, ms_w, ms_h,
                                         mask_style="rough", mask_style_options=ms_opts)
    check(bool(np.any(rough_out != solid_out)), "rough：solidと異なる見た目になる（縁がギザギザになる）")
    center_rough = rough_out[ms_h // 2, ms_w // 2]
    check(int(np.abs(center_solid.astype(int) - center_rough.astype(int)).sum()) < 30,
          "rough：マスク中心（縁から十分離れた内部）はほぼ変化しない（境界だけが歪む）")

    outline_out = render.composite_layers(ms_bg, ms_color, ms_mask, ms_w, ms_h,
                                           mask_style="outline", mask_style_options=ms_opts)
    edge_point = outline_out[ms_h // 2, ms_w // 2 + 60]
    check(bool(np.array_equal(edge_point, np.array([0, 255, 0]))),
          f"outline：mask_style_options.colorで指定した縁の線（#00FF00→BGR(0,255,0)）が輪郭上に描かれる: {edge_point}")
    outline_center = outline_out[ms_h // 2, ms_w // 2]
    check(bool(np.array_equal(outline_center, center_solid)),
          "outline：縁に線を追加するだけでマスクの形自体（中心の色）は変えない")

    print("")
    print("=== 人物マスクの縁の種類：shadowにも同じmask_styleが適用される ===")
    ms_shadow_cfg = {"color": "#000000", "alpha": 0.9, "distance": 0.15, "direction": "right",
                      "offset_y": 0.0, "blur": 0.0}
    shadow_solid = render.composite_layers(ms_bg, ms_color, ms_mask, ms_w, ms_h, shadow_cfg=ms_shadow_cfg,
                                            slide_dx=ms_w * 0.15, slide_dy=0.0, mask_style="solid")
    shadow_outline = render.composite_layers(ms_bg, ms_color, ms_mask, ms_w, ms_h, shadow_cfg=ms_shadow_cfg,
                                              slide_dx=ms_w * 0.15, slide_dy=0.0,
                                              mask_style="outline", mask_style_options=ms_opts)
    # 影は人物の「元の位置」（スライドしていない場所）に残る。その輪郭付近にも
    # mask_style_options.color の線が乗っているはず（人物側の輪郭とは別の位置）。
    shadow_edge_point = shadow_outline[ms_h // 2, ms_w // 2 - 60].astype(int)
    # shadow_alphaが1.0未満のため背景がわずかに透けるが、指定した緑(#00FF00)に近い
    # 色になっているはず（厳密な完全一致ではなく、緑成分が支配的であることを見る）
    diff_from_outline_color = int(np.abs(shadow_edge_point - np.array([0, 255, 0])).sum())
    check(diff_from_outline_color < 40,
          f"shadow：影の輪郭（人物の元の位置）にも同じ縁の線が（影のalphaぶん薄れつつ）描かれる: "
          f"{shadow_edge_point} diff={diff_from_outline_color}")
    check(bool(np.any(shadow_outline != shadow_solid)),
          "shadow：mask_style='outline'はsolidと異なる見た目になる（影の縁にも線が乗る）")

    # ------------------------------------------------------------------
    # 17) テロップの可読性（text_backing）：none/outline/shadow/box/band と auto_contrast
    # ------------------------------------------------------------------
    print("")
    print("=== text_backing：resolve_text_backing/resolve_text_backing_optionsのフォールバック ===")
    check(render.resolve_text_backing("box") == "box", "既知の値はそのまま通す")
    check(render.resolve_text_backing("unknown-backing") == "outline", "未知の値はoutlineにフォールバックする")
    check(render.resolve_text_backing(None) == "outline", "Noneはoutlineにフォールバックする")

    default_backing_opts = render.resolve_text_backing_options({})
    check(default_backing_opts == dict(render.TEXT_BACKING_OPTIONS_DEFAULTS),
          "text_backing_options省略時はTEXT_BACKING_OPTIONS_DEFAULTSがそのまま使われる")
    bad_backing_opts = render.resolve_text_backing_options({"text_backing_options": {
        "color": "not-a-color", "opacity": 5, "radius": -1, "padding": -1}})
    check(bad_backing_opts["color"] == render.TEXT_BACKING_OPTIONS_DEFAULTS["color"],
          "不正なcolorは既定色にフォールバックする")
    check(bad_backing_opts["opacity"] == 1.0, "opacity=5は1.0にクランプされる")
    check(bad_backing_opts["radius"] >= 0 and bad_backing_opts["padding"] >= 0,
          "radius/paddingが負の値でも0以上にクランプされる")

    print("")
    print("=== text_backing：resolve_title_linesが行ごとのbackingを解決する（省略時はstyle既定を継承） ===")
    backing_lines = render.resolve_title_lines({
        "text_backing": "shadow",
        "name": {"lines": [{"text": "行1"}, {"text": "行2", "text_backing": "box"},
                            {"text": "行3", "text_backing": "invalid"}]},
    })
    check([l["backing"] for l in backing_lines] == ["shadow", "box", "shadow"],
          f"style既定(shadow)を継承・行2は明示box・行3は不正値でstyle既定にフォールバック: "
          f"{[l['backing'] for l in backing_lines]}")

    print("")
    print("=== text_backing：render_telop_line_layersでnone/outline/shadow/box/bandの見た目を検証 ===")
    tb_w, tb_h = 400, 200
    tb_lines_for = lambda backing: [{"text": "サンプル", "size": 1.0, "underline": False,
                                      "color": "#FFFFFF", "align": None, "anim": None, "anim_sec": None,
                                      "delay_sec": 0.0, "sfx": None, "backing": backing}]
    tb_opts = render.resolve_text_backing_options({"text_backing_options": {
        "color": "#000000", "opacity": 0.6, "radius": 0.2, "padding": 0.4}})

    def render_single(backing, bg_bgr=None, auto_contrast=True):
        layers = render.render_telop_line_layers(
            tb_lines_for(backing), tb_w, tb_h, {}, font_path_default,
            size_px_base, pos_ratio=(0.5, 0.5), backing_options=tb_opts,
            auto_contrast=auto_contrast, bg_bgr=bg_bgr)
        return layers[0]

    none_layer = render_single("none")
    outline_layer = render_single("outline")
    shadow_layer = render_single("shadow")
    box_layer = render_single("box")
    band_layer = render_single("band")

    def opaque_pixel_count(layer, thresh=0.5):
        return int((layer["alpha"][:, :, 0] > thresh).sum())

    check(opaque_pixel_count(outline_layer) > opaque_pixel_count(none_layer),
          "outline：strokeぶんnoneより不透明画素が多い（縁取りが実際に描かれている）")
    check(opaque_pixel_count(box_layer) > opaque_pixel_count(outline_layer),
          "box：文字の周りに座布団が敷かれるため、outlineよりさらに不透明画素が多い")
    band_alpha_col0 = band_layer["alpha"][:, 0, 0]
    check(bool(np.any(band_alpha_col0 > 0.3)),
          "band：画面左端（x=0、テキストから離れた位置）にも帯が不透明で描かれている")
    box_alpha_col0 = box_layer["alpha"][:, 0, 0]
    check(bool(np.all(box_alpha_col0 < 0.3)),
          "box：画面左端にはbandと違って座布団が伸びていない（行ごとの矩形のみ）")
    check(opaque_pixel_count(shadow_layer) != opaque_pixel_count(none_layer),
          "shadow：noneと異なる見た目になる（ドロップシャドウが乗る）")

    print("")
    print("=== text_backing：auto_contrastが低コントラスト背景でoutlineをboxへ自動格上げする ===")
    dark_bg = np.full((tb_h, tb_w, 3), (20, 20, 20), dtype=np.uint8)   # 黒背景＋白文字＝高コントラスト
    light_bg = np.full((tb_h, tb_w, 3), (235, 235, 235), dtype=np.uint8)  # 白背景＋白文字＝低コントラスト
    high_contrast_layer = render_single("outline", bg_bgr=dark_bg)
    low_contrast_layer = render_single("outline", bg_bgr=light_bg)
    check(opaque_pixel_count(high_contrast_layer) == opaque_pixel_count(outline_layer),
          "高コントラスト背景ではoutlineのまま（bg_bgr省略時と同じ不透明画素数）")
    check(opaque_pixel_count(low_contrast_layer) > opaque_pixel_count(high_contrast_layer),
          "低コントラスト背景ではboxに格上げされ、不透明画素数が明らかに増える")
    no_auto_layer = render_single("outline", bg_bgr=light_bg, auto_contrast=False)
    check(opaque_pixel_count(no_auto_layer) == opaque_pixel_count(outline_layer),
          "auto_contrast=Falseなら低コントラスト背景でも格上げされずoutlineのまま")

    print("")
    print("=== text_backing：contrast_ratio（WCAG 2.0）の基本値を確認 ===")
    check(abs(render.contrast_ratio((255, 255, 255), (0, 0, 0)) - 21.0) < 0.1,
          "白と黒のコントラスト比は21（最大）")
    check(abs(render.contrast_ratio((128, 128, 128), (128, 128, 128)) - 1.0) < 0.01,
          "同じ色同士のコントラスト比は1（最小）")

finally:
    shutil.rmtree(tmpdir, ignore_errors=True)

print("")
print(f"{passed} 件成功 / {failed} 件失敗")
if failed > 0:
    sys.exit(1)
