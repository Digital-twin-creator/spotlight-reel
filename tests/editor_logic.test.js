#!/usr/bin/env node
// -*- coding: utf-8 -*-
//
// index.html に埋め込まれた <script id="core-logic"> だけを取り出して
// Node.js 上で直接テストする（ブラウザなしで実行できる）。
// これにより「エディタが作るJSONは render.py の契約どおりか」を検証する。

"use strict";

const fs = require("fs");
const path = require("path");
const assert = require("assert");

function loadCoreLogic() {
  // index.html を書き換えたら常に最新のロジックがテストされるよう、
  // ここではコピーを持たず本体からその場で抜き出して実行する。
  // vm.createContext は別レルムになり deepStrictEqual 等が誤爆するため、
  // Function コンストラクタで現在のレルムのまま評価する
  // （core-logic は document/window に触れない前提のため安全）。
  const htmlPath = path.join(__dirname, "..", "index.html");
  const html = fs.readFileSync(htmlPath, "utf8");
  const m = html.match(/<script id="core-logic">([\s\S]*?)<\/script>/);
  if (!m) {
    throw new Error("index.html から <script id=\"core-logic\"> が見つかりませんでした");
  }
  const code = m[1];
  const moduleObj = { exports: {} };
  const runner = new Function("module", "exports", code);
  runner(moduleObj, moduleObj.exports);
  return moduleObj.exports;
}

const core = loadCoreLogic();
let failures = 0;
let passed = 0;

function test(name, fn) {
  try {
    fn();
    passed++;
    console.log("  ok - " + name);
  } catch (err) {
    failures++;
    console.log("  NG - " + name);
    console.log("        " + err.message);
  }
}

console.log("=== core-logic ユニットテスト ===");

/* ---- round / clamp01 ---- */
test("round は指定桁数に丸める", () => {
  assert.strictEqual(core.round(0.123456, 4), 0.1235);
  assert.strictEqual(core.round(1, 2), 1);
});
test("clamp01 は0〜1にクランプする", () => {
  assert.strictEqual(core.clamp01(-0.5), 0);
  assert.strictEqual(core.clamp01(1.5), 1);
  assert.strictEqual(core.clamp01(0.3), 0.3);
});

/* ---- computeContentRect（レターボックス計算） ---- */
test("computeContentRect: 縦動画をより横長のboxに収めると左右にレターボックス", () => {
  // box: 400x400 (正方形), video: 1080x1920 (縦長) → 幅方向がレターボックス
  const r = core.computeContentRect(400, 400, 1080, 1920);
  assert.ok(r.width < 400);
  assert.strictEqual(r.height, 400);
  assert.ok(r.left > 0);
});
test("computeContentRect: 横動画を縦長boxに収めると上下にレターボックス", () => {
  const r = core.computeContentRect(400, 800, 1920, 1080);
  assert.strictEqual(r.width, 400);
  assert.ok(r.height < 800);
  assert.ok(r.top > 0);
});
test("computeContentRect: box比率と動画比率が同じならレターボックスなし", () => {
  const r = core.computeContentRect(1080, 1920, 1080, 1920);
  assert.strictEqual(Math.round(r.width), 1080);
  assert.strictEqual(Math.round(r.height), 1920);
  assert.strictEqual(r.left, 0);
  assert.strictEqual(r.top, 0);
});

/* ---- ratioFromRect ---- */
test("ratioFromRect: 矩形中央のタッチは(0.5, 0.5)になる", () => {
  const rect = { left: 10, top: 20, width: 100, height: 200 };
  const p = core.ratioFromRect(60, 120, rect);
  assert.strictEqual(p.x, 0.5);
  assert.strictEqual(p.y, 0.5);
});
test("ratioFromRect: 矩形外のタッチは0〜1にクランプされる", () => {
  const rect = { left: 0, top: 0, width: 100, height: 100 };
  const p1 = core.ratioFromRect(-50, -50, rect);
  assert.deepStrictEqual(p1, { x: 0, y: 0 });
  const p2 = core.ratioFromRect(500, 500, rect);
  assert.deepStrictEqual(p2, { x: 1, y: 1 });
});

/* ---- shouldAddPoint（点の間引き） ---- */
test("shouldAddPoint: 最初の点は常に追加する", () => {
  assert.strictEqual(core.shouldAddPoint(null, { x: 0.1, y: 0.1 }, 1000, 1000), true);
});
test("shouldAddPoint: 表示幅の0.5%未満の移動は追加しない", () => {
  const last = { x: 0.5, y: 0.5 };
  const near = { x: 0.5001, y: 0.5 }; // 1000px幅なら0.1px相当
  assert.strictEqual(core.shouldAddPoint(last, near, 1000, 1000), false);
});
test("shouldAddPoint: 表示幅の0.5%以上の移動は追加する", () => {
  const last = { x: 0.5, y: 0.5 };
  const far = { x: 0.51, y: 0.5 }; // 1000px幅なら10px相当
  assert.strictEqual(core.shouldAddPoint(last, far, 1000, 1000), true);
});

/* ---- sortFreezesByTime ---- */
test("sortFreezesByTime: time昇順に並び替える（元配列は変更しない）", () => {
  const input = [{ time: 5 }, { time: 1 }, { time: 3 }];
  const sorted = core.sortFreezesByTime(input);
  assert.deepStrictEqual(sorted.map(f => f.time), [1, 3, 5]);
  assert.deepStrictEqual(input.map(f => f.time), [5, 1, 3]);
});

/* ---- buildProjectJSON: 契約どおりのJSONを作れるか ---- */
function sampleState() {
  return {
    videoFileName: "dummy_input.mp4",
    outputMode: "1080x1920",
    freezeSec: 2.5,
    brushAnimSec: 0.8,
    audioDuringFreeze: "mute",
    freezes: [
      {
        id: "b", time: 5.5, name: "青い人", sfx: "don", background: "mono",
        strokes: [{ width: 0.09, points: [[0.53, 0.51], [0.61, 0.47]] }]
      },
      {
        id: "a", time: 2.5, name: "赤い人", sfx: "shakin", background: "dark",
        strokes: [{ width: 0.1, points: [[0.30, 0.37], [0.33, 0.33]] }]
      }
    ]
  };
}

test("buildProjectJSON: version, video, output, style, freezes を契約どおりに出力する", () => {
  const project = core.buildProjectJSON(sampleState());
  assert.strictEqual(project.version, 1);
  assert.strictEqual(project.video, "dummy_input.mp4");
  assert.deepStrictEqual(project.output, { width: 1080, height: 1920, fps: 30 });
  assert.strictEqual(project.style.freeze_sec, 2.5);
  assert.strictEqual(project.style.brush_anim_sec, 0.8);
  assert.strictEqual(project.style.audio_during_freeze, "mute");
  assert.strictEqual(project.style.font, "assets/fonts/NotoSansJP-Bold.ttf");
  assert.strictEqual(project.freezes.length, 2);
});

test("buildProjectJSON: freezes は time 昇順で出力される", () => {
  const project = core.buildProjectJSON(sampleState());
  assert.deepStrictEqual(project.freezes.map(f => f.time), [2.5, 5.5]);
  assert.strictEqual(project.freezes[0].name, "赤い人");
  assert.strictEqual(project.freezes[0].background, "dark");
});

test("buildProjectJSON: strokes の points/width が比率のまま出力される", () => {
  const project = core.buildProjectJSON(sampleState());
  const st = project.freezes[0].strokes[0];
  assert.strictEqual(st.width, 0.1);
  assert.ok(st.points[0][0] >= 0 && st.points[0][0] <= 1);
  assert.deepStrictEqual(st.points[0], [0.3, 0.37]);
});

test("buildProjectJSON: outputMode='original' なら output キーを省略する", () => {
  const state = sampleState();
  state.outputMode = "original";
  const project = core.buildProjectJSON(state);
  assert.strictEqual(project.output, undefined);
});

test("buildProjectJSON: sfx未設定のフリーズは sfx キーを出力しない", () => {
  const state = sampleState();
  state.freezes[0].sfx = "";
  const project = core.buildProjectJSON(state);
  const fz = project.freezes.filter(f => f.time === 5.5)[0];
  assert.strictEqual("sfx" in fz, false);
});

/* ---- parseProjectJSON: buildと逆変換して一致するか ---- */
test("parseProjectJSON: buildProjectJSON の出力を読み込んで往復できる", () => {
  const state = sampleState();
  const project = core.buildProjectJSON(state);
  const loaded = core.parseProjectJSON(project);
  assert.strictEqual(loaded.videoFileName, state.videoFileName);
  assert.strictEqual(loaded.outputMode, "1080x1920");
  assert.strictEqual(loaded.freezeSec, 2.5);
  assert.strictEqual(loaded.freezes.length, 2);
  assert.strictEqual(loaded.freezes[0].time, 2.5);
  assert.strictEqual(loaded.freezes[0].name, "赤い人");
});

test("parseProjectJSON: 未知のキーがあっても無視して読み込める", () => {
  const project = core.buildProjectJSON(sampleState());
  project.futureFeature = { whatever: true };
  project.freezes[0].futureKey = 123;
  const loaded = core.parseProjectJSON(project);
  assert.strictEqual(loaded.freezes.length, 2);
});

test("parseProjectJSON: style が省略されていても既定値を補う", () => {
  const loaded = core.parseProjectJSON({ version: 1, video: "x.mp4", freezes: [] });
  assert.strictEqual(loaded.freezeSec, core.DEFAULT_STYLE.freeze_sec);
  assert.strictEqual(loaded.brushAnimSec, core.DEFAULT_STYLE.brush_anim_sec);
  assert.strictEqual(loaded.audioDuringFreeze, "mute");
});

/* ---- buildStrokeGeometry / trimGeometryToLength（ブラシのプレビュー計算） ---- */
test("buildStrokeGeometry: 比率座標をピクセル座標に変換し長さを計算する", () => {
  const strokes = [{ width: 0.1, points: [[0, 0], [1, 0]] }]; // 幅いっぱいの水平線
  const { geo, total } = core.buildStrokeGeometry(strokes, 100, 100, 0.12);
  assert.strictEqual(geo.length, 1);
  assert.strictEqual(geo[0].length, 100);
  assert.strictEqual(geo[0].thick, 10);
  assert.ok(total > 0);
});

test("trimGeometryToLength: progress=0 では何も描かれない", () => {
  const strokes = [{ width: 0.1, points: [[0, 0], [1, 0]] }];
  const { geo } = core.buildStrokeGeometry(strokes, 100, 100, 0.12);
  const trimmed = core.trimGeometryToLength(geo, 0, 0);
  assert.strictEqual(trimmed.length, 0);
});

test("trimGeometryToLength: 全長を指定すると始点から終点までの線が返る", () => {
  const strokes = [{ width: 0.1, points: [[0, 0], [1, 0]] }];
  const { geo, total } = core.buildStrokeGeometry(strokes, 100, 100, 0.12);
  const trimmed = core.trimGeometryToLength(geo, 0, total);
  assert.strictEqual(trimmed.length, 1);
  assert.deepStrictEqual(trimmed[0].pts[0], [0, 0]);
  assert.deepStrictEqual(trimmed[0].pts[trimmed[0].pts.length - 1], [100, 0]);
});

test("trimGeometryToLength: 半分の長さでは中間点までしか描かれない", () => {
  const strokes = [{ width: 0.1, points: [[0, 0], [1, 0]] }];
  const { geo, total } = core.buildStrokeGeometry(strokes, 100, 100, 0.12);
  const trimmed = core.trimGeometryToLength(geo, 0, total / 2);
  const last = trimmed[0].pts[trimmed[0].pts.length - 1];
  assert.ok(Math.abs(last[0] - 50) < 1e-6);
});

test("trimGeometryToLength: 1点だけのストロークは同じ点2つの線分として返る", () => {
  const strokes = [{ width: 0.1, points: [[0.5, 0.5]] }];
  const { geo, total } = core.buildStrokeGeometry(strokes, 100, 100, 0.12);
  const trimmed = core.trimGeometryToLength(geo, 0, total);
  assert.strictEqual(trimmed.length, 1);
  assert.deepStrictEqual(trimmed[0].pts[0], trimmed[0].pts[1]);
});

/* ---- computeBgPixel（render.py の make_background と同じ計算） ---- */
test("computeBgPixel: mono はグレースケール(BT.601)になる", () => {
  const [r, g, b] = core.computeBgPixel(100, 150, 200, "mono");
  assert.strictEqual(r, g);
  assert.strictEqual(g, b);
  const expect = Math.round(0.299 * 100 + 0.587 * 150 + 0.114 * 200);
  assert.strictEqual(r, expect);
});
test("computeBgPixel: dark は30%の明るさになる", () => {
  const [r, g, b] = core.computeBgPixel(100, 150, 200, "dark");
  assert.strictEqual(r, Math.round(100 * 0.3));
  assert.strictEqual(g, Math.round(150 * 0.3));
  assert.strictEqual(b, Math.round(200 * 0.3));
});

/* ---- isFrameInvalid（静止フレーム取得失敗＝黒フレームの検知） ---- */
test("isFrameInvalid: 中央が黒に近ければ無効", () => {
  const samples = [[2, 3, 1], [100, 100, 100], [50, 50, 50], [10, 10, 10], [200, 0, 0]];
  assert.strictEqual(core.isFrameInvalid(samples), true);
});
test("isFrameInvalid: 5点すべて同一色（ベタ塗り）なら無効", () => {
  const samples = [[120, 120, 120], [121, 120, 121], [120, 121, 120], [120, 120, 122], [119, 120, 120]];
  assert.strictEqual(core.isFrameInvalid(samples), true);
});
test("isFrameInvalid: 中央が明るく、色にばらつきがあれば有効", () => {
  const samples = [[120, 100, 150], [30, 40, 200], [220, 60, 10], [10, 200, 90], [80, 80, 240]];
  assert.strictEqual(core.isFrameInvalid(samples), false);
});
test("isFrameInvalid: サンプルが1点も取れなければ無効", () => {
  assert.strictEqual(core.isFrameInvalid([null, null, null, null, null]), true);
  assert.strictEqual(core.isFrameInvalid([]), true);
});

/* ---- まとめ ---- */
console.log("");
console.log(passed + " 件成功 / " + failures + " 件失敗");
if (failures > 0) {
  process.exit(1);
}
