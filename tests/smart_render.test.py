#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
スマートレンダリング（--mode smart。フリーズ区間だけ再エンコードし、素通し区間は
元動画から無劣化コピーする）の回帰テスト。

検証内容:
  1. 60秒・3フリーズのダミー動画で smart と full をそれぞれレンダリングし、
     (a) 総フレーム数が一致する
     (b) フリーズ演出区間の画素が視覚的に一致する（同一のiter_freeze_frames・同一の
         crf20エンコード設定を使うため。ただし独立したエンコードセッションになる分、
         CRFエンコーダのレート制御差による極小のノイズは許容する）
     (c) smartの素通し区間（無劣化コピー）が元動画の対応フレームとビット単位で一致する
     (d) 音声トラックの長さが一致する
  2. 透かしロゴ(watermark)・常時表示ハッシュタグ(hashtags.always)を設定したプロジェクトは
     自動的にfullへフォールバックする（render_timing.jsonのrender_modeで確認）
  3. キーフレームが実質1つしか無い動画（dummy_input.mp4）でもsmartが正しく完走し、
     fullと総フレーム数が一致する（キーフレーム間隔が粗い場合の退化ケースの回帰）

実行方法:
    python make_dummy.py   # 初回のみ
    python tests/smart_render.test.py
"""

import json
import os
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
    import make_dummy as md

    os.makedirs(md.SFX_DIR, exist_ok=True)
    os.makedirs(md.BRUSH_DIR, exist_ok=True)
    os.makedirs(md.FONT_DIR, exist_ok=True)
    md.ensure_sfx(os.path.join(md.SFX_DIR, "shakin.wav"), md.synth_shakin, "shakin.wav")
    if not os.path.exists(os.path.join(md.BRUSH_DIR, "round.png")):
        md.generate_brush_tips()
    md.ensure_font()
    md.ensure_title_font()

    video_path = os.path.join(md.EXAMPLES_DIR, "dummy_input.mp4")
    if not os.path.exists(video_path):
        md.render_dummy_video(video_path)
        md.mux_audio_into_video(video_path, md.make_tone_track())
    return video_path


def ensure_gop_video(src_video_path, out_path, duration_loops=8, gop=30):
    """
    smartレンダリングのコピー経路を実際に試せるよう、キーフレーム間隔(gop)を明示的に
    短く設定した長尺（元動画をstream_loopで繰り返して約60秒）のテスト動画を作る。
    dummy_input.mp4自体はGOP設定をしておらず（他のテストが依存するため変更しない）、
    x264の既定GOP長（250フレーム＝8秒の動画では実質キーフレーム1つだけ）になるため、
    このテスト専用に別ファイルとして生成する。
    """
    if os.path.exists(out_path):
        return out_path
    cmd = ["ffmpeg", "-y", "-v", "error", "-stream_loop", str(duration_loops - 1),
           "-i", src_video_path,
           "-c:v", "libx264", "-preset", "medium", "-crf", "18",
           "-g", str(gop), "-keyint_min", str(gop), "-sc_threshold", "0",
           "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "256k", out_path]
    res = subprocess.run(cmd)
    if res.returncode != 0:
        raise RuntimeError("GOPテスト動画の生成に失敗しました")
    return out_path


def decode_frames(path, W, H):
    cmd = ["ffmpeg", "-v", "error", "-i", path, "-f", "rawvideo", "-pix_fmt", "bgr24", "pipe:1"]
    out = subprocess.run(cmd, capture_output=True).stdout
    n = W * H * 3
    return [np.frombuffer(out[i:i + n], np.uint8).reshape(H, W, 3)
            for i in range(0, len(out), n) if len(out[i:i + n]) == n]


def decode_frame_range(path, start, count, W, H):
    """
    frame番号[start, start+count)だけを生のBGR24で取り出す（`select`フィルタで対象外の
    フレームはデコード後すぐ捨てるため、長尺の動画でも出力サイズ・メモリを小さく保てる）。
    """
    select = f"select='between(n\\,{start}\\,{start + count - 1})'"
    cmd = ["ffmpeg", "-v", "error", "-i", path, "-vf", select, "-vsync", "0",
           "-f", "rawvideo", "-pix_fmt", "bgr24", "pipe:1"]
    out = subprocess.run(cmd, capture_output=True).stdout
    n = W * H * 3
    return [np.frombuffer(out[i:i + n], np.uint8).reshape(H, W, 3)
            for i in range(0, len(out), n) if len(out[i:i + n]) == n]


def probe_duration(path):
    cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path]
    out = subprocess.run(cmd, capture_output=True).stdout.decode().strip()
    return float(out) if out else 0.0


def base_project(video_rel_path, freezes):
    return {
        "version": 1,
        "video": video_rel_path,
        "style": {
            "font": "assets/fonts/NotoSansJP-Bold.ttf",
            "title_font": "assets/fonts/Anton-Regular.ttf",
            "title_font_jp": "assets/fonts/NotoSansJP-Bold.ttf",
        },
        "freezes": freezes,
    }


def make_freeze(t, text):
    return {
        "time": t,
        "name": {"lines": [{"text": text}]},
        "strokes": [{"width": 0.1, "points": [[0.3, 0.3], [0.5, 0.5], [0.6, 0.4]]}],
    }


def render_with_mode(project_dict, video_path, out_path, tmpdir, mode):
    json_path = os.path.join(tmpdir, f"project_{mode}_{os.path.basename(out_path)}.json")
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(project_dict, f, ensure_ascii=False)
    loaded = render.load_project(json_path)
    render.render(loaded, json_path, video_path, out_path, mode=mode)
    timing = {}
    if os.path.exists(render.TIMING_JSON_NAME):
        with open(render.TIMING_JSON_NAME, encoding="utf-8") as f:
            timing = json.load(f)
        os.remove(render.TIMING_JSON_NAME)
    return timing


def main():
    video_path = ensure_fixtures()
    W, H = 1080, 1920

    print("=== シナリオ1: 60秒・3フリーズでsmart/fullを比較 ===")
    with tempfile.TemporaryDirectory(prefix="smart_render_test_") as tmpdir:
        gop_video = ensure_gop_video(
            video_path, os.path.join(REPO_ROOT, "examples", "smart_render_gop_test.mp4"))

        freezes = [make_freeze(5.0, "テスト1"), make_freeze(25.0, "テスト2"), make_freeze(45.0, "テスト3")]
        project = base_project(gop_video, freezes)

        smart_out = os.path.join(tmpdir, "smart.mp4")
        full_out = os.path.join(tmpdir, "full.mp4")
        smart_timing = render_with_mode(project, gop_video, smart_out, tmpdir, "smart")
        full_timing = render_with_mode(project, gop_video, full_out, tmpdir, "full")

        check(smart_timing.get("render_mode") == "smart", "smart指定でrender_mode=smartになる")
        check(full_timing.get("render_mode") == "full", "full指定でrender_mode=fullになる")
        check(smart_timing.get("copied_seconds", 0) > 0,
              f"無劣化コピーが実際に発生している: {smart_timing.get('copied_seconds')}秒")

        # 総フレーム数は、長尺のraw BGR24を丸ごと吐かせるとメモリ・時間を大量に食うため、
        # 2x2グレースケールへ縮小して数えるrender.probe_segment_frame_count（軽量）を使う。
        smart_count = render.probe_segment_frame_count(smart_out)
        full_count = render.probe_segment_frame_count(full_out)
        check(smart_count == full_count,
              f"総フレーム数が一致する: smart={smart_count} full={full_count}")

        # plan_freezesを実際に呼び、フリーズ演出のフレーム範囲を計算する
        info = render.probe_video(gop_video)
        fps = info["fps"]
        src_frames = int(round(info["duration"] * fps))
        plans = render.plan_freezes(freezes, fps, src_frames, tmpdir, None, None)
        offset = 0
        freeze_ranges = []
        for p in plans:
            start = p["frame_index"] + offset
            freeze_ranges.append((start, start + p["n_total"]))
            offset += p["n_total"]

        if smart_count == full_count:
            # 各フリーズ区間だけを select フィルタでピンポイントにデコードして比較する
            # （60秒全体をraw dumpしない。フリーズ以外のフレームはffmpeg側で即座に捨てられる）
            all_close = True
            worst_mean = 0.0
            for (a, b) in freeze_ranges:
                full_seg = decode_frame_range(full_out, a, b - a, W, H)
                smart_seg = decode_frame_range(smart_out, a, b - a, W, H)
                for fa, sa in zip(full_seg, smart_seg):
                    d = np.abs(fa.astype(np.int16) - sa.astype(np.int16))
                    worst_mean = max(worst_mean, float(d.mean()))
                    if d.mean() > 2.0:
                        all_close = False
            check(all_close, f"フリーズ演出区間の画素がsmart/fullで視覚的に一致する"
                              f"（区間平均絶対誤差の最大値={worst_mean:.3f}/255、独立エンコードによる"
                              f"CRFノイズの範囲内）")

            # 素通し区間（コピー区間）は元動画とビット単位で一致するはず。
            # 最初のフリーズより前（[0, plans[0].frame_index)）だけを対象にする
            # （これもselectフィルタでピンポイント抽出し、動画全体はdumpしない）。
            lead_end = plans[0]["frame_index"]
            src_lead = decode_frame_range(gop_video, 0, lead_end, W, H)
            smart_lead = decode_frame_range(smart_out, 0, lead_end, W, H)
            mism = sum(
                1 for sa, sb in zip(src_lead, smart_lead) if not np.array_equal(sa, sb)
            )
            check(mism == 0 and len(src_lead) == lead_end and len(smart_lead) == lead_end,
                  f"smartの先頭の無劣化コピー区間({lead_end}フレーム)が"
                  f"元動画とビット単位で一致する（不一致={mism}）")

        smart_dur = probe_duration(smart_out)
        full_dur = probe_duration(full_out)
        check(abs(smart_dur - full_dur) < 0.05,
              f"音声を含む総尺がsmart/fullでほぼ一致する: smart={smart_dur:.3f}s full={full_dur:.3f}s")

    print("")
    print("=== シナリオ2: watermark設定時は自動的にhybridへフォールバックする ===")
    with tempfile.TemporaryDirectory(prefix="smart_render_test_") as tmpdir:
        gop_video = os.path.join(REPO_ROOT, "examples", "smart_render_gop_test.mp4")
        project = base_project(gop_video, [make_freeze(2.0, "テスト")])
        project["watermark"] = {"image": "examples/store_logo.png"}
        out = os.path.join(tmpdir, "wm.mp4")
        timing = render_with_mode(project, gop_video, out, tmpdir, "auto")
        check(timing.get("render_mode") == "hybrid", "watermark設定時はauto指定でhybridになる")

        # 明示的にsmartを指定した場合は、watermarkがあるためfullへフォールバックする
        # （--mode hybridを使うよう理由に案内が出る）
        smart_forced_out = os.path.join(tmpdir, "wm_smart.mp4")
        smart_timing = render_with_mode(project, gop_video, smart_forced_out, tmpdir, "smart")
        check(smart_timing.get("render_mode") == "full",
              "watermark設定時はmode=smart指定だとfullにフォールバックする")
        check("watermark" in (smart_timing.get("render_mode_reason") or ""),
              f"フォールバック理由にwatermarkが含まれる: {smart_timing.get('render_mode_reason')}")

    print("")
    print("=== シナリオ3: hashtags.always=true設定時は自動的にhybridへフォールバックする ===")
    with tempfile.TemporaryDirectory(prefix="smart_render_test_") as tmpdir:
        gop_video = os.path.join(REPO_ROOT, "examples", "smart_render_gop_test.mp4")
        project = base_project(gop_video, [make_freeze(2.0, "テスト")])
        project["hashtags"] = {"text": "#テスト", "always": True}
        out = os.path.join(tmpdir, "ht.mp4")
        timing = render_with_mode(project, gop_video, out, tmpdir, "auto")
        check(timing.get("render_mode") == "hybrid", "hashtags.always=true設定時はauto指定でhybridになる")

        smart_forced_out = os.path.join(tmpdir, "ht_smart.mp4")
        smart_timing = render_with_mode(project, gop_video, smart_forced_out, tmpdir, "smart")
        check(smart_timing.get("render_mode") == "full",
              "hashtags.always=true設定時はmode=smart指定だとfullにフォールバックする")
        check("hashtags" in (smart_timing.get("render_mode_reason") or ""),
              f"フォールバック理由にhashtagsが含まれる: {smart_timing.get('render_mode_reason')}")

    print("")
    print("=== シナリオ4: キーフレームが実質1つしか無い動画でもsmartが正しく完走する ===")
    with tempfile.TemporaryDirectory(prefix="smart_render_test_") as tmpdir:
        freezes = [make_freeze(2.0, "テスト1"), make_freeze(5.0, "テスト2")]
        project = base_project(video_path, freezes)
        smart_out = os.path.join(tmpdir, "smart_sparse.mp4")
        full_out = os.path.join(tmpdir, "full_sparse.mp4")
        smart_timing = render_with_mode(project, video_path, smart_out, tmpdir, "smart")
        full_timing = render_with_mode(project, video_path, full_out, tmpdir, "full")
        check(smart_timing.get("render_mode") == "smart",
              "キーフレームが粗い動画でもsmartモード自体は選ばれる（コピーが0秒でも成立する）")
        smart_frames = decode_frames(smart_out, W, H)
        full_frames = decode_frames(full_out, W, H)
        check(len(smart_frames) == len(full_frames),
              f"総フレーム数が一致する: smart={len(smart_frames)} full={len(full_frames)}")

    print("")
    print("=== シナリオ5: hybrid（常時表示のwatermark+hashtags）でfullと結果が一致し高速化する ===")
    with tempfile.TemporaryDirectory(prefix="smart_render_test_") as tmpdir:
        gop_video = ensure_gop_video(
            video_path, os.path.join(REPO_ROOT, "examples", "smart_render_gop_test.mp4"))
        freezes = [make_freeze(5.0, "テスト1"), make_freeze(25.0, "テスト2"), make_freeze(45.0, "テスト3")]
        project = base_project(gop_video, freezes)
        project["watermark"] = {
            "image": "examples/store_logo.png",
            "shine": {"enabled": True, "interval_sec": 1.0, "sec": 0.3},
            "spin": {"enabled": True, "interval_sec": 1.5, "sec": 0.3, "degrees": 30},
            "width_ratio": 0.12, "position": "bottom_right",
        }
        project["hashtags"] = {"text": "#テスト #spotlight", "position": "bottom", "always": True}

        hybrid_out = os.path.join(tmpdir, "hybrid.mp4")
        full_out = os.path.join(tmpdir, "full_hybrid_cmp.mp4")

        t0 = time.time()
        hybrid_timing = render_with_mode(project, gop_video, hybrid_out, tmpdir, "hybrid")
        t_hybrid = time.time() - t0
        t0 = time.time()
        full_timing = render_with_mode(project, gop_video, full_out, tmpdir, "full")
        t_full = time.time() - t0

        check(hybrid_timing.get("render_mode") == "hybrid", "mode=hybrid指定でrender_mode=hybridになる")
        check(hybrid_timing.get("copied_seconds", 0) > 0,
              f"ffmpegフィルタでの素通し合成が実際に発生している: {hybrid_timing.get('copied_seconds')}秒")
        check(hybrid_timing.get("reencoded_seconds", 0) > 0,
              f"フリーズ区間の再エンコードが実際に発生している: {hybrid_timing.get('reencoded_seconds')}秒")
        check(t_hybrid < t_full,
              f"hybridがfullより高速: hybrid={t_hybrid:.1f}s full={t_full:.1f}s")

        hybrid_count = render.probe_segment_frame_count(hybrid_out)
        full_count = render.probe_segment_frame_count(full_out)
        check(hybrid_count == full_count,
              f"総フレーム数が一致する: hybrid={hybrid_count} full={full_count}")

        hybrid_dur = probe_duration(hybrid_out)
        full_dur = probe_duration(full_out)
        check(abs(hybrid_dur - full_dur) < 0.05,
              f"音声を含む総尺がhybrid/fullでほぼ一致する: hybrid={hybrid_dur:.3f}s full={full_dur:.3f}s")

        # watermarkの位相（shine/spin）がhybridの区間境界（フリーズ直後の「own frame」→
        # 次の素通しセグメント冒頭）をまたいでも不連続にジャンプしないことを、
        # fullレンダリング（区間分割の無い基準）の対応フレームと直接比較して確認する
        # （fullは常に動画全体の絶対時刻で連続的に合成しているため、区間境界の有無に
        # よらずhybridと同じ絶対時刻なら同じ見た目になるはず）。
        info = render.probe_video(gop_video)
        fps = info["fps"]
        src_frames = int(round(info["duration"] * fps))
        plans = render.plan_freezes(freezes, fps, src_frames, tmpdir, None, None)
        all_close = True
        worst_mean = 0.0
        for p in plans:
            boundary_idx = p["frame_index"] + p["n_total"]  # 「own frame」の直後（次の素通し冒頭）
            for idx in (boundary_idx, boundary_idx + 1, boundary_idx + 2):
                if idx >= src_frames + sum(pp["n_total"] for pp in plans):
                    continue
                hf = decode_frame_range(hybrid_out, idx, 1, W, H)
                ff = decode_frame_range(full_out, idx, 1, W, H)
                if not hf or not ff:
                    continue
                d = np.abs(hf[0].astype(np.int16) - ff[0].astype(np.int16))
                worst_mean = max(worst_mean, float(d.mean()))
                if d.mean() > 3.0:
                    all_close = False
        check(all_close, f"区間境界付近のwatermark/hashtags合成がfullと視覚的に一致する"
                          f"（区間平均絶対誤差の最大値={worst_mean:.3f}/255、独立エンコードによる"
                          f"CRFノイズの範囲内。位相が不連続にジャンプしていれば大きくずれるはず）")

    print("")
    print(f"{passed} 件成功 / {failed} 件失敗")
    if failed > 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
