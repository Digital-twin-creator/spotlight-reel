#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
複数クリップ（clips[]）結合の回帰テスト。

検証内容:
  1. 解像度・fpsが混在する3クリップ（各1フリーズ・watermark+hashtags.always）を
     クロスフェード/黒フェードで結合し、
     (a) 総尺 ≈ 各クリップのトリム後尺の合計 − 遷移の重なり秒数
     (b) 音声トラックが存在し、総尺と一致する
     (c) 各クリップのレンダリングモードがrender_timing.jsonのclips[]に記録される
     (d) watermarkの周期位相が、クリップ境界（遷移のブレンドが終わった後）でも
         連続している（該当時刻の絶対時刻から計算した期待値と実際の出力が一致する）
  2. ラストロゴ（logo.at="end"）は全体の末尾に1回だけ追加される（総尺がロゴ分だけ
     伸びる。各クリップに重複して付かない）
  3. clips未指定の旧JSONは、load_project()が単一videoを1クリップとして扱う
     （完全後方互換）
  4. in!=0のクリップでも、フリーズ時刻がエディタ記録どおり（クリップの元動画上の
     絶対時刻）に変換される（load_project()のin基準変換 + plan_freezes()の実測）
  5. iPhone風の縦動画（3840x2160・rotation=-90のdisplay matrix付き）が複数クリップ
     経路でも正しい向き・アスペクト比で正規化され、映像/音声の尺も一致する

実行方法:
    python make_dummy.py   # 初回のみ
    python tests/multi_clip.test.py
"""

import json
import os
import subprocess
import sys
import tempfile

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
    import make_dummy as md

    os.makedirs(md.SFX_DIR, exist_ok=True)
    os.makedirs(md.BRUSH_DIR, exist_ok=True)
    os.makedirs(md.FONT_DIR, exist_ok=True)
    md.ensure_sfx(os.path.join(md.SFX_DIR, "shakin.wav"), md.synth_shakin, "shakin.wav")
    if not os.path.exists(os.path.join(md.BRUSH_DIR, "round.png")):
        md.generate_brush_tips()
    md.ensure_font()
    md.ensure_title_font()

    portrait_path = os.path.join(md.EXAMPLES_DIR, "dummy_input.mp4")
    if not os.path.exists(portrait_path):
        md.render_dummy_video(portrait_path)
        md.mux_audio_into_video(portrait_path, md.make_tone_track())

    landscape_path = os.path.join(md.EXAMPLES_DIR, "dummy_input_landscape.mp4")
    if not os.path.exists(landscape_path):
        md.render_dummy_video(landscape_path, w=1920, h=1080)
        md.mux_audio_into_video(landscape_path, md.make_tone_track())

    return portrait_path, landscape_path


def ensure_24fps_variant(src_path, out_path):
    """3本目のクリップ用に、fpsだけ異なる（24fps）バリアントを作る"""
    if os.path.exists(out_path):
        return out_path
    ffmpeg = render.find_exe("ffmpeg")
    cmd = [ffmpeg, "-y", "-v", "error", "-i", src_path,
           "-r", "24", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
           "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k", out_path]
    res = subprocess.run(cmd)
    if res.returncode != 0:
        raise RuntimeError("24fpsバリアントの生成に失敗しました")
    return out_path


def decode_frame(path, idx, W, H):
    ffmpeg = render.find_exe("ffmpeg")
    cmd = [ffmpeg, "-v", "error", "-i", path, "-vf", f"select=eq(n\\,{idx})", "-vsync", "0",
           "-f", "rawvideo", "-pix_fmt", "bgr24", "-frames:v", "1", "pipe:1"]
    out = subprocess.run(cmd, capture_output=True, check=True).stdout
    return np.frombuffer(out, dtype=np.uint8).reshape(H, W, 3)


CLIP_FREEZE_LOCAL_TIME = 2.5  # クリップ自身の（トリム後・0起点の）タイムライン上でのフリーズ時刻
WATERMARK_CHECK_LOCAL_TIME = 1.0  # 各クリップの遷移ブレンド終了後・フリーズ開始前の検証用局所時刻


def base_clip(video_path, in_t, out_t, text, transition_out=None):
    """
    project.json（raw JSON）上のfreezes[].timeは、エディタの<video>.currentTime
    そのまま＝そのクリップの「元動画（トリム前）」上の絶対時刻として記録される
    （トリムin/outは再生範囲を絞らないメタデータのため）。render.pyのload_project()が
    各クリップのinを引いてトリム後・0起点の時刻へ変換するので、ここでは
    「トリム後の狙った時刻(CLIP_FREEZE_LOCAL_TIME)」にin_tを足した値をJSONへ書く
    （in_t=0のクリップでは従来どおりCLIP_FREEZE_LOCAL_TIMEと一致する）。
    """
    clip = {
        "video": video_path,
        "in": in_t,
        "out": out_t,
        "freezes": [{
            "time": in_t + CLIP_FREEZE_LOCAL_TIME,
            "name": {"lines": [{"text": text}]},
            "strokes": [{"width": 0.1, "points": [[0.3, 0.3], [0.5, 0.5], [0.6, 0.4]]}],
        }],
    }
    if transition_out is not None:
        clip["transition_out"] = transition_out
    return clip


def build_project(clips, with_logo=False):
    proj = {
        "version": 1,
        "clips": clips,
        "output": {"width": 1080, "height": 1920, "fps": 30},
        "style": {
            "font": "assets/fonts/NotoSansJP-Bold.ttf",
            "title_font": "assets/fonts/Anton-Regular.ttf",
            "title_font_jp": "assets/fonts/NotoSansJP-Bold.ttf",
        },
        "watermark": {
            "image": "examples/store_logo.png",
            "shine": {"enabled": True, "interval_sec": 1.0, "sec": 0.3},
            "spin": {"enabled": True, "interval_sec": 1.5, "sec": 0.3, "degrees": 30},
            "width_ratio": 0.12, "position": "bottom_right",
        },
        "hashtags": {"text": "#マルチクリップ検証", "position": "bottom", "always": True},
    }
    if with_logo:
        proj["logo"] = {"image": "examples/store_logo.png", "at": "end",
                         "background": "#111111"}
    return proj


def render_project(proj, tmpdir, name, mode="auto"):
    json_path = os.path.join(tmpdir, f"{name}.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(proj, f, ensure_ascii=False)
    loaded = render.load_project(json_path)
    out_path = os.path.join(tmpdir, f"{name}.mp4")
    render.render_multi_clip(loaded, json_path, out_path, mode=mode)
    timing = {}
    if os.path.exists(render.TIMING_JSON_NAME):
        with open(render.TIMING_JSON_NAME, encoding="utf-8") as f:
            timing = json.load(f)
        os.remove(render.TIMING_JSON_NAME)
    return out_path, timing


def main():
    portrait_path, landscape_path = ensure_fixtures()

    print("=== シナリオ1: 解像度/fps混在3クリップをトランジションで結合 ===")
    with tempfile.TemporaryDirectory(prefix="multi_clip_test_") as tmpdir:
        fps24_path = ensure_24fps_variant(portrait_path,
                                           os.path.join(tmpdir, "clip3_24fps.mp4"))

        t1 = {"type": "crossfade", "sec": 0.5}
        t2 = {"type": "fade_black", "sec": 0.3}
        clips = [
            base_clip(portrait_path, 1.0, 6.0, "クリップ1", transition_out=t1),   # 5.0s, 1080x1920@30
            base_clip(landscape_path, 0.0, 4.0, "クリップ2", transition_out=t2),  # 4.0s, 1920x1080@30
            base_clip(fps24_path, 0.0, 3.0, "クリップ3"),                          # 3.0s, 1080x1920@24
        ]
        proj = build_project(clips, with_logo=False)
        out_path, timing = render_project(proj, tmpdir, "scenario1")

        check(len(timing.get("clips", [])) == 3,
              f"render_timing.jsonにクリップ3件分の内訳が記録されている: {len(timing.get('clips', []))}件")
        for i, c in enumerate(timing.get("clips", [])):
            check(c.get("render_mode") in ("smart", "hybrid", "full"),
                  f"クリップ{i}のrender_modeが記録されている: {c.get('render_mode')}")

        # 各クリップの実際にレンダリングされた尺（フリーズ演出込み。トリムしただけの尺
        # （5.0/4.0/3.0秒）より長くなる）はrender_timing.jsonのclips[].duration_secondsに
        # 記録されている値を使う（テスト側で独自に再計算しない。render_multi_clip自身の
        # 計算と同じ実測値に基づいて期待値を組み立てる）
        d0 = timing["clips"][0]["duration_seconds"]
        d1 = timing["clips"][1]["duration_seconds"]
        d2 = timing["clips"][2]["duration_seconds"]
        blend1 = render.transition_blend_sec(t1, 30.0)
        blend2 = render.transition_blend_sec(t2, 30.0)
        expected_total = d0 + d1 + d2 - blend1 - blend2

        actual_total = render.probe_duration(out_path)
        check(abs(actual_total - expected_total) < 0.15,
              f"総尺が期待どおり: 期待={expected_total:.2f}s（クリップ実測尺{d0:.2f}+{d1:.2f}+{d2:.2f}"
              f"－遷移{blend1:.2f}+{blend2:.2f}） 実際={actual_total:.2f}s")

        info = render.probe_video(out_path)
        check(info["has_audio"], "音声トラックが存在する")
        check(abs(info["duration"] - actual_total) < 0.15,
              f"音声を含む総尺が映像と一致する: {info['duration']:.2f}s")

        # --- コンテナのduration値（ffprobe format=duration）だけに頼らず、実際に
        #     デコードした映像フレーム数と音声ストリームの尺（フレーム換算）が一致する
        #     ことを保証する（±1フレーム）。実機（iPhone縦動画・複数クリップ）で
        #     「音声は最後まで残るのに映像だけ途中で打ち切られ、後続クリップの映像が
        #     丸ごと欠落する」不具合が発生していた。原因はmerge_clip_pairのxfade
        #     offsetをffprobeのcontainer duration（実デコード可能な映像フレーム数より
        #     わずかに長く報告されることがある）から計算していたため。 ---
        output_fps = float(proj["output"]["fps"])
        video_frames = render.probe_segment_frame_count(out_path)
        expected_frames = round(expected_total * output_fps)
        check(abs(video_frames - expected_frames) <= 1,
              f"実デコードした映像フレーム数も期待どおり（±1フレーム）: "
              f"期待={expected_frames}枚 実際={video_frames}枚")

        def audio_stream_duration(path):
            ffprobe = render.find_exe("ffprobe")
            cmd = [ffprobe, "-v", "error", "-select_streams", "a:0",
                   "-show_entries", "stream=duration", "-of", "csv=p=0", path]
            out = subprocess.run(cmd, capture_output=True).stdout.decode().strip()
            return float(out) if out else 0.0

        audio_frames = round(audio_stream_duration(out_path) * output_fps)
        check(abs(video_frames - audio_frames) <= 1,
              f"映像の実フレーム数と音声の尺（フレーム換算）が一致する（±1フレーム。"
              f"音声だけ長く残り映像が早期に打ち切られる不具合の回帰確認）: "
              f"映像={video_frames}枚 音声換算={audio_frames}枚")

        # --- watermarkの位相連続性: 各クリップの遷移ブレンド終了後・フリーズ開始前
        #     （CLIP_FREEZE_LOCAL_TIME=2.5sより前）の安全な検証点で、実際の出力フレームの
        #     watermark領域が、その絶対時刻での期待値と一致するか（この範囲ではフリーズによる
        #     ソース時刻とタイムライン時刻のズレが無いため、クリップ自身の局所時刻＝
        #     このクリップの絶対開始時刻からの経過秒数、として単純に検証できる） ---
        W, H, fps = 1080, 1920, 30.0
        wm_cfg = render.resolve_watermark_config(proj["watermark"])
        wm_path = render.resolve_path(wm_cfg["image"], [REPO_ROOT])
        wm_bgr, wm_alpha = render.load_logo_image(wm_path)
        wm_bgr, wm_alpha = render.auto_transparent_bg(wm_bgr, wm_alpha)
        wm_luma = render.build_logo_luminance_mask(wm_bgr, wm_alpha)
        rect = render.watermark_bounding_rect(wm_bgr, wm_alpha, W, H, wm_cfg)
        rx0, ry0, rx1, ry1 = rect

        offset0 = 0.0
        offset1 = offset0 + d0 - blend1
        offset2 = offset1 + d1 - blend2

        check_abs_t2 = offset1 + WATERMARK_CHECK_LOCAL_TIME
        check_abs_t3 = offset2 + WATERMARK_CHECK_LOCAL_TIME

        clip2_norm = os.path.join(tmpdir, "clip2_norm_check.mp4")
        render.prepare_clip_video(landscape_path, 0.0, 4.0, W, H, fps, clip2_norm)
        clip3_norm = os.path.join(tmpdir, "clip3_norm_check.mp4")
        render.prepare_clip_video(fps24_path, 0.0, 3.0, W, H, fps, clip3_norm)

        worst_mean = 0.0
        all_close = True
        for norm_path, local_t, abs_t in ((clip2_norm, WATERMARK_CHECK_LOCAL_TIME, check_abs_t2),
                                           (clip3_norm, WATERMARK_CHECK_LOCAL_TIME, check_abs_t3)):
            raw_frame = render.grab_frame_at(norm_path, local_t, W, H, fps)
            expected_frame = render.render_watermark_frame(
                raw_frame, wm_bgr, wm_alpha, wm_luma, W, H, abs_t, wm_cfg)
            out_idx = int(round(abs_t * fps))
            actual_frame = decode_frame(out_path, out_idx, W, H)
            d = np.abs(expected_frame[ry0:ry1, rx0:rx1].astype(np.int16) -
                       actual_frame[ry0:ry1, rx0:rx1].astype(np.int16))
            worst_mean = max(worst_mean, float(d.mean()))
            if d.mean() > 4.0:
                all_close = False
        check(all_close, f"watermarkの周期位相がクリップ境界をまたいでも連続している"
                          f"（絶対時刻での期待値との領域平均絶対誤差の最大値={worst_mean:.3f}/255、"
                          f"独立エンコードによるCRFノイズの範囲内。位相がズレていれば大きく外れるはず）")

    print("")
    print("=== シナリオ2: logo.at='end'は全体の末尾に1回だけ追加される ===")
    with tempfile.TemporaryDirectory(prefix="multi_clip_test_") as tmpdir:
        fps24_path = ensure_24fps_variant(portrait_path,
                                           os.path.join(tmpdir, "clip3_24fps.mp4"))
        t1 = {"type": "crossfade", "sec": 0.5}
        t2 = {"type": "fade_black", "sec": 0.3}
        clips = [
            base_clip(portrait_path, 1.0, 6.0, "クリップ1", transition_out=t1),
            base_clip(landscape_path, 0.0, 4.0, "クリップ2", transition_out=t2),
            base_clip(fps24_path, 0.0, 3.0, "クリップ3"),
        ]
        proj_no_logo = build_project(clips, with_logo=False)
        proj_with_logo = build_project(clips, with_logo=True)

        out_no_logo, _ = render_project(proj_no_logo, tmpdir, "scenario2_no_logo")
        out_with_logo, timing_logo = render_project(proj_with_logo, tmpdir, "scenario2_with_logo")

        dur_no_logo = render.probe_duration(out_no_logo)
        dur_with_logo = render.probe_duration(out_with_logo)

        logo_params = render.resolve_logo_params(proj_with_logo["logo"])
        fps = 30.0
        expected_logo_extra = (render.logo_blackout_frames_for(fps)
                                + render.logo_total_frames_for(logo_params, fps)) / fps

        check(dur_with_logo > dur_no_logo,
              f"logo指定時は総尺が伸びる: なし={dur_no_logo:.2f}s あり={dur_with_logo:.2f}s")
        actual_extra = dur_with_logo - dur_no_logo
        check(abs(actual_extra - expected_logo_extra) < 0.3,
              f"伸びた分がロゴ演出1回ぶんとほぼ一致する（末尾に1回だけ追加されている）: "
              f"期待={expected_logo_extra:.2f}s 実際={actual_extra:.2f}s"
              "（各クリップに重複して付いていれば約3倍の差になるはず）")

    print("")
    print("=== シナリオ3: clips未指定の旧JSONは単一クリップとして扱われる（後方互換） ===")
    with tempfile.TemporaryDirectory(prefix="multi_clip_test_") as tmpdir:
        legacy_proj = {
            "version": 1,
            "video": "dummy_input.mp4",
            "style": {"font": "assets/fonts/NotoSansJP-Bold.ttf",
                      "title_font": "assets/fonts/Anton-Regular.ttf",
                      "title_font_jp": "assets/fonts/NotoSansJP-Bold.ttf"},
            "freezes": [{"time": 2.0, "name": {"lines": [{"text": "旧形式"}]},
                         "strokes": [{"width": 0.1, "points": [[0.3, 0.3], [0.5, 0.5], [0.6, 0.4]]}]}],
        }
        json_path = os.path.join(tmpdir, "legacy.json")
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(legacy_proj, f, ensure_ascii=False)
        loaded = render.load_project(json_path)

        check("clips" in loaded and len(loaded["clips"]) == 1,
              "load_project()がclips未指定時も1件のclipsリストを合成する")
        check(loaded["clips"][0]["video"] == "dummy_input.mp4",
              "合成されたclipのvideoが従来のvideoと一致する")
        check(len(loaded["clips"][0]["freezes"]) == 1
              and loaded["clips"][0]["freezes"][0]["name"]["lines"][0]["text"] == "旧形式",
              "合成されたclipのfreezesが従来のfreezesと一致する")
        check(loaded["video"] == "dummy_input.mp4" and len(loaded["freezes"]) == 1,
              "project['video']/['freezes']はclips有無によらず従来どおり保たれる（完全後方互換）")
        check("raw" in loaded and not loaded["raw"].get("clips"),
              "raw JSONにclipsキーが無いことをmain()側のルーティング判定に使える")

    print("")
    print("=== シナリオ4: in!=0のクリップでも、フリーズ時刻がエディタ記録どおり"
          "（クリップの元動画上の絶対時刻）に変換される ===")
    with tempfile.TemporaryDirectory(prefix="multi_clip_test_") as tmpdir:
        # portrait_pathは8秒。2.0〜7.0（5秒）にトリムし、トリム後0起点で1.5秒の位置に
        # フリーズしたいとする。エディタは<video>.currentTimeをそのまま記録するため、
        # project.json上のfreezes[].timeには「元動画上の絶対時刻」= in + 1.5 = 3.5を書く
        # （実際にindex.htmlのloadVideoFile/selectClip後、els.video.currentTimeで
        # フリーズを記録する処理と同じ値の作り方）。
        in_t, out_t = 2.0, 7.0
        target_local_t = 1.5
        absolute_t = in_t + target_local_t
        fps = 30.0

        clip = {
            "video": portrait_path, "in": in_t, "out": out_t,
            "freezes": [{
                "time": absolute_t, "name": "in_offset_check",
                "strokes": [{"width": 0.1, "points": [[0.3, 0.3], [0.5, 0.5]]}],
            }],
        }
        proj = {
            "version": 1, "clips": [clip],
            "style": {"font": "assets/fonts/NotoSansJP-Bold.ttf",
                      "title_font": "assets/fonts/Anton-Regular.ttf",
                      "title_font_jp": "assets/fonts/NotoSansJP-Bold.ttf"},
        }
        json_path = os.path.join(tmpdir, "in_offset.json")
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(proj, f, ensure_ascii=False)
        loaded = render.load_project(json_path)

        converted = loaded["clips"][0]["freezes"][0]["time"]
        check(abs(converted - target_local_t) < 1e-6,
              f"load_project()がclips[].freezes[].timeを「元動画上の絶対時刻」から"
              f"「トリム後0起点の時刻」へ変換する: 絶対時刻{absolute_t}s(JSON記載値) "
              f"- in{in_t}s = {converted:.3f}s（狙い{target_local_t}s）")

        # plan_freezes（render()が実際にフリーズ位置を決めるのに使う関数そのもの）に、
        # render_multi_clipが渡すのと同じ「トリム後0起点」の値を渡し、
        # 計算されるframe_indexが正確にtarget_local_t*fpsと一致することを直接確認する
        # （元動画上の絶対時刻をそのまま使っていた修正前は、この値がズレていた）。
        src_frames = int(round((out_t - in_t) * fps))
        plans = render.plan_freezes(loaded["clips"][0]["freezes"], fps, src_frames, tmpdir)
        expected_frame_index = round(target_local_t * fps)
        check(plans[0]["frame_index"] == expected_frame_index,
              f"plan_freezes()が計算するフリーズのフレーム位置も指定どおり: "
              f"期待={expected_frame_index} 実際={plans[0]['frame_index']}"
              f"（クリップ先頭付近にズレる不具合の回帰確認）")

        # 統合確認として実際にrender_multi_clipを1本通し、クラッシュしないこと・
        # in分だけ短くなったトリム後の尺（5秒）を土台に正しく完走することも見ておく
        out_path = os.path.join(tmpdir, "in_offset_out.mp4")
        render.render_multi_clip(loaded, json_path, out_path, mode="full")
        actual_duration = render.probe_duration(out_path)
        check(actual_duration > (out_t - in_t),
              f"in!=0でも通しでレンダリングが完走し、トリム後尺（{out_t - in_t:.1f}s）に"
              f"フリーズ演出の分だけ尺が伸びた動画が生成される: {actual_duration:.2f}s")

    print("")
    print("=== シナリオ5: iPhone風の縦動画（rotationメタデータ付き）が複数クリップ経路でも"
          "正しい向きへ正規化される ===")
    with tempfile.TemporaryDirectory(prefix="multi_clip_test_") as tmpdir:
        import make_dummy as md

        # 実際に失敗したproject.jsonと同じ入力（3840x2160、rotation=-90の縦持ち撮影）を
        # 2本用意する。生ピクセルは横長のまま、tkhdのdisplay matrixで回転を付与する
        # （render_dummy_videoと同じ被写体描画ロジックなので通常のダミー動画と同様に
        # フリーズも使える）。
        rot_dir = os.path.join(tmpdir, "rotated_src")
        os.makedirs(rot_dir, exist_ok=True)
        rot_path_1 = os.path.join(rot_dir, "clip1.mp4")
        rot_path_2 = os.path.join(rot_dir, "clip2.mp4")
        md.gen_dummy_video_rotated(rot_path_1, w=3840, h=2160, rotation=-90)
        md.gen_dummy_video_rotated(rot_path_2, w=3840, h=2160, rotation=-90)

        src_info = render.probe_video(rot_path_1)
        check(src_info["width"] == 2160 and src_info["height"] == 3840,
              f"probe_video()が生ピクセル(3840x2160)ではなく表示上の向き(縦2160x3840)を"
              f"返す: {src_info['width']}x{src_info['height']} rotation={src_info['rotation']}")

        clips = [
            base_clip(rot_path_1, 1.0, 5.0, "縦動画1", transition_out={"type": "crossfade", "sec": 0.4}),
            base_clip(rot_path_2, 0.5, 4.5, "縦動画2"),
        ]
        # 出力解像度は指定せず、1本目のクリップ（表示上の向き=縦2160x3840）に
        # 自動で合わせさせる（実際のエディタも通常は出力サイズを明示しない）
        proj = {
            "version": 1, "clips": clips,
            "style": {"font": "assets/fonts/NotoSansJP-Bold.ttf",
                      "title_font": "assets/fonts/Anton-Regular.ttf",
                      "title_font_jp": "assets/fonts/NotoSansJP-Bold.ttf"},
        }
        json_path = os.path.join(tmpdir, "rotated.json")
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(proj, f, ensure_ascii=False)
        loaded = render.load_project(json_path)

        out_path = os.path.join(tmpdir, "rotated_out.mp4")
        render.render_multi_clip(loaded, json_path, out_path, mode="full")

        out_info = render.probe_video(out_path)
        check(out_info["height"] > out_info["width"],
              f"出力も縦動画のまま正規化される（横倒しにならない）: "
              f"{out_info['width']}x{out_info['height']}")
        check(abs(out_info["width"] / out_info["height"] - 2160 / 3840) < 0.02,
              f"出力のアスペクト比が入力の表示上の向き（2160:3840）と一致する: "
              f"{out_info['width']}/{out_info['height']}")

        # ここでも実デコードした映像フレーム数と音声の尺（フレーム換算）が一致することを
        # 確認する（回転付き入力×複数クリップという、実際に映像欠落が発生した組み合わせでの
        # item1の回帰確認）
        fps = 30.0
        video_frames = render.probe_segment_frame_count(out_path)

        def audio_stream_duration(path):
            ffprobe = render.find_exe("ffprobe")
            cmd = [ffprobe, "-v", "error", "-select_streams", "a:0",
                   "-show_entries", "stream=duration", "-of", "csv=p=0", path]
            out = subprocess.run(cmd, capture_output=True).stdout.decode().strip()
            return float(out) if out else 0.0

        audio_frames = round(audio_stream_duration(out_path) * fps)
        check(abs(video_frames - audio_frames) <= 1,
              f"縦動画×複数クリップでも映像の実フレーム数と音声の尺が一致する（±1フレーム）: "
              f"映像={video_frames}枚 音声換算={audio_frames}枚")

    print("")
    print("=== シナリオ6: cut遷移（既定の遷移種別）でも映像が欠落しない ===")
    with tempfile.TemporaryDirectory(prefix="multi_clip_test_") as tmpdir:
        # cut は transition_out省略時の既定値（DEFAULT_TRANSITION_TYPE）であり、
        # 実際のジョブでも最も使われる頻度が高い。ffmpegのxfadeフィルタは、
        # duration（ブレンド秒数）が1フレームの時間（1/fps）未満だと2本目の入力の
        # 映像を一切出力しないまま1本目の長さで打ち切る既知の挙動があり、
        # CUT_TRANSITION_SEC=0.01は一般的なfps（24〜60）で常にこれに該当していた
        # （実機ジョブで「3クリップ中、2本目以降の映像が丸ごと欠落する」形で再現・
        # transition_blend_secのfps対応フロアで修正）。crossfade/fade_blackのみを
        # 使う他シナリオではこの経路が一切検証されないため、cutを明示的に検証する。
        clips = [
            base_clip(portrait_path, 1.0, 6.0, "クリップ1", transition_out={"type": "cut"}),
            base_clip(landscape_path, 0.0, 4.0, "クリップ2", transition_out={"type": "cut"}),
            base_clip(portrait_path, 0.5, 4.5, "クリップ3"),
        ]
        proj = build_project(clips, with_logo=False)
        out_path, timing = render_project(proj, tmpdir, "scenario6")

        output_fps = float(proj["output"]["fps"])
        d0 = timing["clips"][0]["duration_seconds"]
        d1 = timing["clips"][1]["duration_seconds"]
        d2 = timing["clips"][2]["duration_seconds"]
        blend = render.transition_blend_sec({"type": "cut"}, output_fps)
        expected_total = d0 + d1 + d2 - 2 * blend

        video_frames = render.probe_segment_frame_count(out_path)
        expected_frames = round(expected_total * output_fps)
        check(abs(video_frames - expected_frames) <= 1,
              f"cut遷移×3クリップで想定どおりのフレーム数（±1フレーム。2本目以降の"
              f"映像欠落の回帰確認）: 期待={expected_frames}枚（クリップ実測尺"
              f"{d0:.2f}+{d1:.2f}+{d2:.2f}－遷移{blend:.3f}×2） 実際={video_frames}枚")

        def audio_stream_duration(path):
            ffprobe = render.find_exe("ffprobe")
            cmd = [ffprobe, "-v", "error", "-select_streams", "a:0",
                   "-show_entries", "stream=duration", "-of", "csv=p=0", path]
            out = subprocess.run(cmd, capture_output=True).stdout.decode().strip()
            return float(out) if out else 0.0

        audio_frames = round(audio_stream_duration(out_path) * output_fps)
        check(abs(video_frames - audio_frames) <= 1,
              f"cut遷移でも映像の実フレーム数と音声の尺（フレーム換算）が一致する"
              f"（±1フレーム）: 映像={video_frames}枚 音声換算={audio_frames}枚")

    print("")
    print(f"{passed} 件成功 / {failed} 件失敗")
    if failed > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
