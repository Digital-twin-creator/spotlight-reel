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

/* ---- parseMp4DisplayRotation / mp4RotationNeedsSwap / resolveEffectiveVideoDims ----
 * iPhone実機で縦動画が正しく扱えない不具合（videoWidth/videoHeightが回転メタデータを
 * 反映しない既知のWebKit系の不具合）への対策。ffmpegの-display_rotationで実際に
 * 書き込まれるtkhdの変換行列と同じバイト構造を手組みして検証する。 */

/** trak(tkhd+mdia>minf>vmhd)だけを持つ最小限のmoovボックスを組み立てる（version 0 tkhd固定） */
function buildMinimalMp4Moov(a, b) {
  function box(type, body) {
    const header = Buffer.alloc(8);
    header.writeUInt32BE(8 + body.length, 0);
    header.write(type, 4, 4, "ascii");
    return Buffer.concat([header, body]);
  }
  function makeTkhd(a, b) {
    const buf = Buffer.alloc(40 + 36 + 8); // version0固定部(40) + matrix(36) + width/height(8)
    const m = 40;
    buf.writeInt32BE(Math.round(a * 65536), m);       // a
    buf.writeInt32BE(Math.round(b * 65536), m + 4);   // b
    buf.writeInt32BE(0, m + 8);                       // u
    buf.writeInt32BE(Math.round(-b * 65536), m + 12); // c
    buf.writeInt32BE(Math.round(a * 65536), m + 16);  // d
    buf.writeInt32BE(0, m + 20);                      // v
    buf.writeInt32BE(0, m + 24);                      // x
    buf.writeInt32BE(0, m + 28);                      // y
    buf.writeInt32BE(0x40000000, m + 32);              // w = 1.0 (2.30 fixed)
    return buf;
  }
  const tkhd = box("tkhd", makeTkhd(a, b));
  const vmhd = box("vmhd", Buffer.alloc(12));
  const minf = box("minf", vmhd);
  const mdia = box("mdia", minf);
  const trak = box("trak", Buffer.concat([tkhd, mdia]));
  return box("moov", trak);
}

function toArrayBuffer(buf) {
  return buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength);
}

test("parseMp4DisplayRotation: 単位行列(無回転)は0を返す", () => {
  const buf = buildMinimalMp4Moov(1, 0);
  assert.strictEqual(core.parseMp4DisplayRotation(toArrayBuffer(buf)), 0);
});

test("parseMp4DisplayRotation: 90度系の行列は90または270を返す(mp4RotationNeedsSwapがtrue)", () => {
  const buf = buildMinimalMp4Moov(0, 1);
  const rotation = core.parseMp4DisplayRotation(toArrayBuffer(buf));
  assert.ok(rotation === 90 || rotation === 270, "実際の値: " + rotation);
  assert.strictEqual(core.mp4RotationNeedsSwap(rotation), true);
});

test("parseMp4DisplayRotation: 180度の行列は180を返す(mp4RotationNeedsSwapがfalse)", () => {
  const buf = buildMinimalMp4Moov(-1, 0);
  const rotation = core.parseMp4DisplayRotation(toArrayBuffer(buf));
  assert.strictEqual(rotation, 180);
  assert.strictEqual(core.mp4RotationNeedsSwap(rotation), false);
});

test("parseMp4DisplayRotation: moovの無いバイト列はnullを返す", () => {
  assert.strictEqual(core.parseMp4DisplayRotation(toArrayBuffer(Buffer.from("not an mp4 file"))), null);
});

test("resolveEffectiveVideoDims: 90度系回転かつ横向き報告のときだけ幅高を入れ替える", () => {
  // iOSの不具合を模したケース：回転メタデータは90度系なのに、videoWidth/videoHeightが
  // 回転前（横向き）のまま報告されている → 補正して縦向きにする
  assert.deepStrictEqual(core.resolveEffectiveVideoDims(1920, 1080, 90), { width: 1080, height: 1920 });
  assert.deepStrictEqual(core.resolveEffectiveVideoDims(1920, 1080, 270), { width: 1080, height: 1920 });
  // ブラウザが既に正しく縦向きを報告している場合は、二重に入れ替えない
  assert.deepStrictEqual(core.resolveEffectiveVideoDims(1080, 1920, 90), { width: 1080, height: 1920 });
  // 回転なし(0/180)は常にそのまま
  assert.deepStrictEqual(core.resolveEffectiveVideoDims(1920, 1080, 0), { width: 1920, height: 1080 });
  assert.deepStrictEqual(core.resolveEffectiveVideoDims(1920, 1080, 180), { width: 1920, height: 1080 });
  // 回転情報が不明(null)なら常にそのまま報告値を使う
  assert.deepStrictEqual(core.resolveEffectiveVideoDims(1920, 1080, null), { width: 1920, height: 1080 });
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
    outputPreset: "custom",
    outputMode: "1080x1920",
    revealSec: 0.8,
    slideSec: 0.4,
    holdSec: 2.5,
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
  assert.strictEqual(project.style.reveal_sec, 0.8);
  assert.strictEqual(project.style.slide_sec, 0.4);
  assert.strictEqual(project.style.hold_sec, 2.5);
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

test("buildProjectJSON: フリーズ一覧(state.freezes)の件数と書き出しJSONのfreezes件数が一致する（3件以上でも欠落しない）", () => {
  const state = sampleState();
  // sampleStateの2件に加え、3件目以降を追加して合計5件で検証する
  for (let i = 0; i < 3; i++) {
    state.freezes.push({
      id: "extra-" + i, time: 10 + i, name: "追加" + i, sfx: "", background: "mono",
      strokes: [{ width: 0.1, points: [[0.1, 0.1], [0.2, 0.2]] }]
    });
  }
  assert.strictEqual(state.freezes.length, 5);
  const project = core.buildProjectJSON(state);
  assert.strictEqual(project.freezes.length, state.freezes.length,
    "一覧の件数(" + state.freezes.length + ")と書き出しJSONのfreezes件数(" + project.freezes.length + ")が一致しない");
  // 追加した3件の名前がすべてJSONに含まれている（途中で欠落していない）ことも確認する
  const names = project.freezes.map(f => f.name);
  assert.ok(["追加0", "追加1", "追加2"].every(n => names.includes(n)),
    "追加したフリーズの一部がJSONから欠落している: " + JSON.stringify(names));
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

/* ---- color_source（新）/ mask（旧・後方互換） ---- */

test("buildProjectJSON: maskModeが brush/auto ならcolor_sourceキーで出力し、maskキーは出力しない", () => {
  const state = sampleState();
  state.freezes[0].maskMode = "auto";
  const project = core.buildProjectJSON(state);
  const fz = project.freezes.filter(f => f.time === 5.5)[0];
  assert.strictEqual(fz.color_source, "auto");
  assert.strictEqual("mask" in fz, false);
});

test("buildProjectJSON: maskModeが auto+brush（後方互換のハイブリッド）なら旧maskキーで出力し、color_sourceは出力しない", () => {
  const state = sampleState();
  state.freezes[0].maskMode = "auto+brush";
  const project = core.buildProjectJSON(state);
  const fz = project.freezes.filter(f => f.time === 5.5)[0];
  assert.strictEqual(fz.mask, "auto+brush");
  assert.strictEqual("color_source" in fz, false);
});

test("resolveColorSourceForFreeze: color_sourceキーがあれば優先し、無ければ旧maskキーを読み替える", () => {
  assert.strictEqual(core.resolveColorSourceForFreeze({ color_source: "auto" }), "auto");
  assert.strictEqual(core.resolveColorSourceForFreeze({ mask: "auto+brush" }), "auto+brush");
  assert.strictEqual(core.resolveColorSourceForFreeze({}), "brush");
  assert.strictEqual(core.resolveColorSourceForFreeze({ color_source: "nonsense" }), "brush");
});

test("parseProjectJSON: color_source（新）とmask（旧）のどちらもmaskModeとして読み込める", () => {
  const withNew = core.parseProjectJSON({
    version: 1, video: "x.mp4",
    freezes: [{ time: 1, name: "a", color_source: "auto", strokes: [] }]
  });
  assert.strictEqual(withNew.freezes[0].maskMode, "auto");
  const withLegacy = core.parseProjectJSON({
    version: 1, video: "x.mp4",
    freezes: [{ time: 1, name: "a", mask: "auto+brush", strokes: [] }]
  });
  assert.strictEqual(withLegacy.freezes[0].maskMode, "auto+brush");
});

/* ---- shadow.source（新）：影に使うマスクの種類 ---- */

test("parseProjectJSON: shadow.sourceを読み込める（未指定はsame）", () => {
  const withSource = core.parseProjectJSON({
    version: 1, video: "x.mp4", freezes: [],
    style: { shadow: { source: "brush" } }
  });
  assert.strictEqual(withSource.shadowSource, "brush");
  const withoutSource = core.parseProjectJSON({
    version: 1, video: "x.mp4", freezes: [],
    style: { shadow: {} }
  });
  assert.strictEqual(withoutSource.shadowSource, "same");
});

test("buildProjectJSON/parseProjectJSON: shadow.sourceを含めて往復できる", () => {
  const state = sampleState();
  state.shadowEnabled = true;
  state.shadowSource = "brush";
  const loaded = core.parseProjectJSON(core.buildProjectJSON(state));
  assert.strictEqual(loaded.shadowSource, "brush");
});

/* ---- reveal="none"（自動マスクを一瞬で全体表示し、reveal_sec秒だけ静止して待つ） ---- */

test("resolveReveal: 'none'はREVEALSに含まれ、そのまま返る", () => {
  assert.ok(core.REVEALS.indexOf("none") >= 0);
  assert.strictEqual(core.resolveReveal("none"), "none");
});

/* ---- 自動切り抜きモデル選択（mask_options.model） ---- */

test("resolveMaskModel: 未知の値・未指定は既定モデル(isnet-general-use)にフォールバックする", () => {
  assert.strictEqual(core.resolveMaskModel(undefined), core.DEFAULT_MASK_MODEL);
  assert.strictEqual(core.resolveMaskModel(null), core.DEFAULT_MASK_MODEL);
  assert.strictEqual(core.resolveMaskModel("no-such-model"), core.DEFAULT_MASK_MODEL);
  assert.strictEqual(core.DEFAULT_MASK_MODEL, "isnet-general-use");
});

test("resolveMaskModel: 選択可能な4種類（rvm-mobilenetv3/isnet-general-use/birefnet-portrait/apple-vision）はそのまま返る", () => {
  assert.strictEqual(core.resolveMaskModel("rvm-mobilenetv3"), "rvm-mobilenetv3");
  assert.strictEqual(core.resolveMaskModel("isnet-general-use"), "isnet-general-use");
  assert.strictEqual(core.resolveMaskModel("birefnet-portrait"), "birefnet-portrait");
  assert.strictEqual(core.resolveMaskModel("apple-vision"), "apple-vision");
  assert.deepStrictEqual(core.MASK_MODELS, ["rvm-mobilenetv3", "isnet-general-use", "birefnet-portrait", "apple-vision"]);
  assert.strictEqual(core.APPLE_VISION_MODEL_NAME, "apple-vision");
});

test("DEFAULT_MASK_MODEL_SELECTION: 新規プロジェクトの初期選択は動画（RVM）", () => {
  assert.strictEqual(core.DEFAULT_MASK_MODEL_SELECTION, "rvm-mobilenetv3");
});

test("buildProjectJSON: maskModelが既定(isnet-general-use)ならstyle.mask_optionsキー自体を省略する（後方互換）", () => {
  const state = sampleState();
  state.maskModel = "isnet-general-use";
  const project = core.buildProjectJSON(state);
  assert.strictEqual("mask_options" in project.style, false);
});

test("buildProjectJSON: maskModelが未指定でもstyle.mask_optionsキーを省略する（既定モデル扱い）", () => {
  const project = core.buildProjectJSON(sampleState());
  assert.strictEqual("mask_options" in project.style, false);
});

test("buildProjectJSON: maskModelが高精度(birefnet-portrait)ならstyle.mask_options.modelを明示的に出力する", () => {
  const state = sampleState();
  state.maskModel = "birefnet-portrait";
  const project = core.buildProjectJSON(state);
  assert.deepStrictEqual(project.style.mask_options, { model: "birefnet-portrait" });
});

test("buildProjectJSON: maskModelがApple Visionならstyle.mask_options.include_held_objectsを既定でfalseにする", () => {
  const state = sampleState();
  state.maskModel = "apple-vision";
  const project = core.buildProjectJSON(state);
  assert.deepStrictEqual(project.style.mask_options, { model: "apple-vision", include_held_objects: false });
});

test("parseProjectJSON: style.mask_options.modelを読み込んでmaskModelとして復元する", () => {
  const loaded = core.parseProjectJSON({
    version: 1, video: "x.mp4", freezes: [],
    style: { mask_options: { model: "birefnet-portrait" } }
  });
  assert.strictEqual(loaded.maskModel, "birefnet-portrait");
});

test("parseProjectJSON: style.mask_optionsが無ければmaskModelは既定モデルになる", () => {
  const loaded = core.parseProjectJSON({ version: 1, video: "x.mp4", freezes: [], style: {} });
  assert.strictEqual(loaded.maskModel, core.DEFAULT_MASK_MODEL);
});

test("parseProjectJSON: buildProjectJSONで高精度モデルを書き出し→読み込むと同じモデルに往復できる", () => {
  const state = sampleState();
  state.maskModel = "birefnet-portrait";
  const loaded = core.parseProjectJSON(core.buildProjectJSON(state));
  assert.strictEqual(loaded.maskModel, "birefnet-portrait");
});

/* ---- 画質段階(quality) ---- */
test("buildProjectJSON: qualityが既定(high)ならoutput.qualityキー自体を省略する（後方互換）", () => {
  const state = sampleState();
  state.quality = "high";
  state.outputMode = "original";
  const project = core.buildProjectJSON(state);
  assert.strictEqual(project.output, undefined);
});

test("buildProjectJSON: qualityが未指定でもoutput.qualityキーを省略する（既定扱い）", () => {
  const state = sampleState();
  state.outputMode = "original";
  const project = core.buildProjectJSON(state);
  assert.strictEqual(project.output, undefined);
});

test("buildProjectJSON: qualityがstandardならoutput.qualityを明示的に出力する", () => {
  const state = sampleState();
  state.quality = "standard";
  state.outputMode = "original";
  const project = core.buildProjectJSON(state);
  assert.deepStrictEqual(project.output, { quality: "standard" });
});

test("buildProjectJSON: qualityがbestならoutput.qualityを明示的に出力する（output.width/heightと共存できる）", () => {
  const state = sampleState();
  state.quality = "best";
  const project = core.buildProjectJSON(state);
  assert.deepStrictEqual(project.output, { width: 1080, height: 1920, fps: 30, quality: "best" });
});

test("resolveQuality: 不明な値はDEFAULT_QUALITYにフォールバックする", () => {
  assert.strictEqual(core.resolveQuality("ultra"), core.DEFAULT_QUALITY);
  assert.strictEqual(core.resolveQuality(undefined), core.DEFAULT_QUALITY);
});

test("parseProjectJSON: output.qualityを読み込んでqualityとして復元する", () => {
  const loaded = core.parseProjectJSON({
    version: 1, video: "x.mp4", freezes: [], style: {},
    output: { quality: "best" }
  });
  assert.strictEqual(loaded.quality, "best");
});

test("parseProjectJSON: output.qualityが無ければqualityは既定(high)になる", () => {
  const loaded = core.parseProjectJSON({ version: 1, video: "x.mp4", freezes: [], style: {} });
  assert.strictEqual(loaded.quality, core.DEFAULT_QUALITY);
});

test("parseProjectJSON: buildProjectJSONでstandardを書き出し→読み込むと同じ画質段階に往復できる", () => {
  const state = sampleState();
  state.quality = "standard";
  const loaded = core.parseProjectJSON(core.buildProjectJSON(state));
  assert.strictEqual(loaded.quality, "standard");
});

/* ---- 出力先プリセット(instagram_tiktok/youtube_shorts/custom) ---- */
test("buildProjectJSON: outputPresetが既定(instagram_tiktok)ならoutputキー自体を省略する（後方互換）", () => {
  const state = sampleState();
  state.outputPreset = "instagram_tiktok";
  const project = core.buildProjectJSON(state);
  assert.strictEqual(project.output, undefined);
});

test("buildProjectJSON: outputPresetが未指定でもoutputキーを省略する（既定扱い。outputMode/qualityは無視される）", () => {
  const state = sampleState();
  delete state.outputPreset;
  const project = core.buildProjectJSON(state);
  assert.strictEqual(project.output, undefined);
});

test("buildProjectJSON: outputPreset=youtube_shortsならoutput.presetだけを出力し、width/height/qualityは出さない", () => {
  const state = sampleState();
  state.outputPreset = "youtube_shorts";
  state.quality = "best";
  const project = core.buildProjectJSON(state);
  assert.deepStrictEqual(project.output, { preset: "youtube_shorts" });
});

test("buildProjectJSON: outputPreset=customならoutputMode/qualityの値をそのまま出力する", () => {
  const state = sampleState();
  state.outputPreset = "custom";
  state.outputMode = "720x1280";
  state.quality = "standard";
  const project = core.buildProjectJSON(state);
  assert.deepStrictEqual(project.output, { width: 720, height: 1280, fps: 30, quality: "standard" });
});

test("resolveOutputPresetKind: 不明な値はDEFAULT_OUTPUT_PRESET(instagram_tiktok)にフォールバックする", () => {
  assert.strictEqual(core.resolveOutputPresetKind("4k"), core.DEFAULT_OUTPUT_PRESET);
  assert.strictEqual(core.resolveOutputPresetKind(undefined), "instagram_tiktok");
});

test("parseProjectJSON: output.preset='youtube_shorts'を読み込んでoutputPresetとして復元する", () => {
  const loaded = core.parseProjectJSON({
    version: 1, video: "x.mp4", freezes: [], style: {},
    output: { preset: "youtube_shorts" }
  });
  assert.strictEqual(loaded.outputPreset, "youtube_shorts");
});

test("parseProjectJSON: output.width/heightが明示された旧JSON（presetキー無し）はcustomとして復元する", () => {
  const loaded = core.parseProjectJSON({
    version: 1, video: "x.mp4", freezes: [], style: {},
    output: { width: 1080, height: 1920, fps: 30 }
  });
  assert.strictEqual(loaded.outputPreset, "custom");
  assert.strictEqual(loaded.outputMode, "1080x1920");
});

test("parseProjectJSON: outputが省略/空ならoutputPresetは既定(instagram_tiktok)になる", () => {
  const loaded = core.parseProjectJSON({ version: 1, video: "x.mp4", freezes: [], style: {} });
  assert.strictEqual(loaded.outputPreset, core.DEFAULT_OUTPUT_PRESET);
});

test("parseProjectJSON: buildProjectJSONでyoutube_shortsを書き出し→読み込むと同じプリセットに往復できる", () => {
  const state = sampleState();
  state.outputPreset = "youtube_shorts";
  const loaded = core.parseProjectJSON(core.buildProjectJSON(state));
  assert.strictEqual(loaded.outputPreset, "youtube_shorts");
});

/* ---- 効果音ライブラリ（自分のmp3/wav、align位置合わせ） ---- */

test("resolveSfxAlign: 未知の値・未指定は既定(start_at_landing)にフォールバックする", () => {
  assert.strictEqual(core.resolveSfxAlign(undefined, core.SFX_ALIGNS_FREEZE), "start_at_landing");
  assert.strictEqual(core.resolveSfxAlign("no-such-align", core.SFX_ALIGNS_FREEZE), "start_at_landing");
  assert.strictEqual(core.resolveSfxAlign("peak_at_landing", core.SFX_ALIGNS_FREEZE), "start_at_landing",
    "フリーズ用のalignセットにpeak_at_landingは含まれないのでフォールバックする");
});

test("resolveSfxAlign: 対応するalignセット内の値はそのまま返る", () => {
  assert.strictEqual(core.resolveSfxAlign("end_at_landing", core.SFX_ALIGNS_FREEZE), "end_at_landing");
  assert.strictEqual(core.resolveSfxAlign("peak_at_landing", core.SFX_ALIGNS_LOGO), "peak_at_landing");
});

test("sfxLibraryAssetPath: idと拡張子から一意なパスを作る（ファイル名本体には依存しない）", () => {
  assert.strictEqual(core.sfxLibraryAssetPath("sfx-abc123", "my rise.mp3"), "sfx/sfx-abc123.mp3");
  assert.strictEqual(core.sfxLibraryAssetPath("sfx-xyz", "drum.WAV"), "sfx/sfx-xyz.wav");
  assert.strictEqual(core.sfxLibraryAssetPath("sfx-1", "noext"), "sfx/sfx-1.mp3",
    "拡張子が無ければmp3にフォールバックする");
});

test("collectUsedSfxLibraryIds: フリーズ・ロゴの両方から重複無く集める", () => {
  const freezes = [
    { sfxLibraryId: "a" }, { sfxLibraryId: "b" }, { sfxLibraryId: "a" }, { sfx: "shakin" }
  ];
  assert.deepStrictEqual(core.collectUsedSfxLibraryIds(freezes, { sfxLibraryId: "b" }), ["a", "b"]);
  assert.deepStrictEqual(core.collectUsedSfxLibraryIds(freezes, { sfxLibraryId: "c" }), ["a", "b", "c"]);
  assert.deepStrictEqual(core.collectUsedSfxLibraryIds([], null), []);
});

test("sfxSelectValue/applySfxSelectValue: <select>値とsfx/sfxLibraryIdペアを相互変換できる", () => {
  assert.strictEqual(core.sfxSelectValue(null, "shakin"), "shakin");
  assert.strictEqual(core.sfxSelectValue("sfx-1", "shakin"), "lib:sfx-1",
    "sfxLibraryIdがあればプリセット値より優先される");
  assert.deepStrictEqual(core.applySfxSelectValue("shakin"), { sfx: "shakin", sfxLibraryId: null });
  assert.deepStrictEqual(core.applySfxSelectValue("lib:sfx-1"), { sfx: "", sfxLibraryId: "sfx-1" });
  assert.deepStrictEqual(core.applySfxSelectValue(""), { sfx: "", sfxLibraryId: null });
});

test("resolveSfxFieldsFromJSON: 文字列プリセットはsfxLibraryId=nullでそのまま解決される", () => {
  const fields = core.resolveSfxFieldsFromJSON("shakin", [], core.SFX_ALIGNS_FREEZE);
  assert.strictEqual(fields.sfx, "shakin");
  assert.strictEqual(fields.sfxLibraryId, null);
});

test("resolveSfxFieldsFromJSON: {file,align}オブジェクトは、fileのパスが一致するライブラリを見つけて解決する", () => {
  const lib = [{ id: "sfx-1", name: "rise.mp3" }, { id: "sfx-2", name: "impact.wav" }];
  const fields = core.resolveSfxFieldsFromJSON(
    { file: "sfx/sfx-1.mp3", align: "end_at_landing" }, lib, core.SFX_ALIGNS_FREEZE);
  assert.strictEqual(fields.sfx, "");
  assert.strictEqual(fields.sfxLibraryId, "sfx-1");
  assert.strictEqual(fields.sfxAlign, "end_at_landing");
});

test("resolveSfxFieldsFromJSON: ファイル名一致（別端末で作ったJSON等、パスが一致しない場合）でも解決できる", () => {
  const lib = [{ id: "sfx-9", name: "rise.mp3" }];
  const fields = core.resolveSfxFieldsFromJSON(
    { file: "sfx/rise.mp3", align: "start_at_landing" }, lib, core.SFX_ALIGNS_FREEZE);
  assert.strictEqual(fields.sfxLibraryId, "sfx-9");
});

test("resolveSfxFieldsFromJSON: ライブラリに無いファイル参照は未選択のまま、ヒントだけ残す", () => {
  const fields = core.resolveSfxFieldsFromJSON(
    { file: "sfx/missing.mp3", align: "end_at_landing" }, [], core.SFX_ALIGNS_FREEZE);
  assert.strictEqual(fields.sfxLibraryId, null);
  assert.strictEqual(fields.sfxMissingFile, "sfx/missing.mp3");
});

test("buildProjectJSON: フリーズがsfxLibraryIdを持つ場合、state.sfxLibraryから{file,align}オブジェクトを出力する", () => {
  const state = sampleState();
  state.sfxLibrary = [{ id: "sfx-1", name: "rise.mp3" }];
  state.freezes[0].sfxLibraryId = "sfx-1";
  state.freezes[0].sfxAlign = "end_at_landing";
  state.freezes[0].sfx = "shakin";  // sfxLibraryIdがある場合はこちらは無視される
  const project = core.buildProjectJSON(state);
  const fz = project.freezes.filter(f => f.time === state.freezes[0].time)[0];
  assert.deepStrictEqual(fz.sfx, { file: "sfx/sfx-1.mp3", align: "end_at_landing" });
});

test("buildProjectJSON: logoがsfxLibraryIdを持つ場合、state.sfxLibraryから{file,align}オブジェクトを出力する", () => {
  const state = Object.assign(sampleState(), {
    logo: { imageName: "logo.png", at: "end", background: "auto", durationSec: 2.2,
            sfxLibraryId: "sfx-2", sfxAlign: "peak_at_landing" },
    sfxLibrary: [{ id: "sfx-2", name: "impact.wav" }]
  });
  const project = core.buildProjectJSON(state);
  assert.deepStrictEqual(project.logo.sfx, { file: "sfx/sfx-2.wav", align: "peak_at_landing" });
});

test("buildProjectJSON/parseProjectJSON: ライブラリ効果音のsfxLibraryId/sfxAlignを往復できる", () => {
  const lib = [{ id: "sfx-1", name: "rise.mp3" }];
  const state = sampleState();
  state.sfxLibrary = lib;
  state.freezes[0].sfxLibraryId = "sfx-1";
  state.freezes[0].sfxAlign = "end_at_landing";
  const project = core.buildProjectJSON(state);
  const loaded = core.parseProjectJSON(project, lib);
  const fz = loaded.freezes.filter(f => f.time === state.freezes[0].time)[0];
  assert.strictEqual(fz.sfxLibraryId, "sfx-1");
  assert.strictEqual(fz.sfxAlign, "end_at_landing");
});

/* ---- reveal_sec/slide_sec/hold_secの旧キー読み替え（freeze_sec/brush_anim_sec/shadow.slide_sec） ---- */

test("parseProjectJSON: 旧freeze_sec/brush_anim_secは新しいhold_sec/reveal_secとして読み替えられる", () => {
  const loaded = core.parseProjectJSON({
    version: 1, video: "x.mp4", freezes: [],
    style: { freeze_sec: 1.8, brush_anim_sec: 0.9 }
  });
  assert.strictEqual(loaded.holdSec, 1.8);
  assert.strictEqual(loaded.revealSec, 0.9);
});

test("parseProjectJSON: shadow.slide_sec（旧）はstyle.slide_sec（新）より優先される", () => {
  const loaded = core.parseProjectJSON({
    version: 1, video: "x.mp4", freezes: [],
    style: { slide_sec: 0.3, shadow: { slide_sec: 0.9 } }
  });
  assert.strictEqual(loaded.slideSec, 0.9);
});

test("parseProjectJSON: 新しいstyle.slide_secはshadow.slide_secが無ければそのまま使われる", () => {
  const loaded = core.parseProjectJSON({
    version: 1, video: "x.mp4", freezes: [],
    style: { slide_sec: 0.3, shadow: {} }
  });
  assert.strictEqual(loaded.slideSec, 0.3);
});

/* ---- parseProjectJSON: buildと逆変換して一致するか ---- */
test("parseProjectJSON: buildProjectJSON の出力を読み込んで往復できる", () => {
  const state = sampleState();
  const project = core.buildProjectJSON(state);
  const loaded = core.parseProjectJSON(project);
  assert.strictEqual(loaded.videoFileName, state.videoFileName);
  assert.strictEqual(loaded.outputMode, "1080x1920");
  assert.strictEqual(loaded.revealSec, 0.8);
  assert.strictEqual(loaded.slideSec, 0.4);
  assert.strictEqual(loaded.holdSec, 2.5);
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
  assert.strictEqual(loaded.revealSec, core.REVEAL_SEC_DEFAULT);
  assert.strictEqual(loaded.slideSec, core.SLIDE_SEC_DEFAULT);
  assert.strictEqual(loaded.holdSec, core.HOLD_SEC_DEFAULT);
  assert.strictEqual(loaded.audioDuringFreeze, "mute");
});

/* ---- 演出追加：影（フィルム色）／テロップバウンス／ラストロゴ ---- */

test("DEFAULT_STYLE: reveal_sec/slide_sec/hold_secの既定値は0.5/0.5/2.0、film_offset等は後方互換パース専用の無効化デフォルト", () => {
  // reveal_sec/slide_sec/hold_sec（そしてfreeze_sec/brush_anim_sec、mask）はDEFAULT_STYLEに
  // 意図的に含めていない（shadowキーと同じ理由。render.pyのDEFAULT_STYLEと同じ設計）
  assert.strictEqual(core.DEFAULT_STYLE.freeze_sec, undefined);
  assert.strictEqual(core.DEFAULT_STYLE.brush_anim_sec, undefined);
  assert.strictEqual(core.REVEAL_SEC_DEFAULT, 0.5);
  assert.strictEqual(core.SLIDE_SEC_DEFAULT, 0.5);
  assert.strictEqual(core.HOLD_SEC_DEFAULT, 2.0);
  assert.strictEqual(core.DEFAULT_STYLE.mono_contrast, 1.0);
  assert.deepStrictEqual(core.DEFAULT_STYLE.film_offset, [0, 0]);
  assert.strictEqual(core.DEFAULT_STYLE.title_bounce, false);
});

test("DEFAULT_SHADOW: render.pyのSHADOW_*_DEFAULTと同じ値", () => {
  assert.strictEqual(core.DEFAULT_SHADOW.color, "#FF6432");
  assert.strictEqual(core.DEFAULT_SHADOW.alpha, 0.8);
  assert.strictEqual(core.DEFAULT_SHADOW.distance, 0.03);
  assert.strictEqual(core.DEFAULT_SHADOW.direction, "auto");
  assert.strictEqual(core.DEFAULT_SHADOW.offsetY, 0.02);
  assert.strictEqual(core.DEFAULT_SHADOW.blur, 0.0);
  assert.strictEqual(core.DEFAULT_SHADOW.slideSec, 0.5);
  assert.strictEqual(core.DEFAULT_SHADOW.source, "same");
  assert.strictEqual(core.SHADOW_SLIDE_BACK_SEC, 0.25);
});

test("resolveShadowDirection: auto/left/right以外はautoにフォールバックする", () => {
  assert.strictEqual(core.resolveShadowDirection("left"), "left");
  assert.strictEqual(core.resolveShadowDirection("right"), "right");
  assert.strictEqual(core.resolveShadowDirection("auto"), "auto");
  assert.strictEqual(core.resolveShadowDirection("nonsense"), "auto");
  assert.strictEqual(core.resolveShadowDirection(undefined), "auto");
});

test("resolveShadowAutoDirection: 人物マスクのX重心が画面中心より右／左寄りのダミーで方向が反転する", () => {
  const W = 100, H = 10;
  const rightMask = new Uint8Array(W * H); // X=70〜89（中心50より右寄り）に人物がいるダミー
  const leftMask = new Uint8Array(W * H);  // X=10〜29（中心50より左寄り）に人物がいるダミー
  for (let y = 0; y < H; y++) {
    for (let x = 70; x < 90; x++) rightMask[y * W + x] = 255;
    for (let x = 10; x < 30; x++) leftMask[y * W + x] = 255;
  }
  assert.strictEqual(core.resolveShadowAutoDirection(rightMask, W, H), "right");
  assert.strictEqual(core.resolveShadowAutoDirection(leftMask, W, H), "left");
});

test("resolveShadowAutoDirection: 中心±5%以内のあいまいなマスク、または空マスクはrightにフォールバックする", () => {
  const W = 100, H = 10;
  const centerMask = new Uint8Array(W * H); // X=48〜51（中心50のすぐ近く）
  for (let y = 0; y < H; y++) {
    for (let x = 48; x < 52; x++) centerMask[y * W + x] = 255;
  }
  assert.strictEqual(core.resolveShadowAutoDirection(centerMask, W, H), "right");
  assert.strictEqual(core.resolveShadowAutoDirection(new Uint8Array(W * H), W, H), "right");
});

test("easeOutCubic: t=0で0、t=1で1、Ease-Out Expoのような急停止ではなく最後まで滑らかに減速するイージング（render.pyのease_out_cubicと同じ）", () => {
  assert.strictEqual(core.easeOutCubic(0), 0);
  assert.strictEqual(core.easeOutCubic(1), 1);
  assert.strictEqual(core.easeOutCubic(0.5), 1 - Math.pow(0.5, 3)); // 1 - (1-0.5)^3
  // 序盤(0→0.3)の伸びのほうが終盤(0.7→1.0)より大きい（ease-outの形は保つ）
  const earlyGain = core.easeOutCubic(0.3) - core.easeOutCubic(0);
  const lateGain = core.easeOutCubic(1.0) - core.easeOutCubic(0.7);
  assert.ok(earlyGain > lateGain);
  // ただしEase-Out Expo（旧実装）ほど極端に序盤へ偏らない：終盤にも十分な伸びが残る
  // （「急停止」しない滑らかな減速であることの確認）
  assert.ok(lateGain > 0.02);
});

test("telopBounceScale: t=0で130%、t=1で100%になる急停止イージング", () => {
  assert.strictEqual(core.telopBounceScale(0), 1.3);
  assert.strictEqual(core.telopBounceScale(1), 1.0);
  assert.ok(core.telopBounceScale(0.5) > 1.0 && core.telopBounceScale(0.5) < 1.3);
});

test("logoAssetName: 拡張子を保ったまま logo.<ext> にする（大文字は小文字化）", () => {
  assert.strictEqual(core.logoAssetName("my store LOGO.PNG"), "logo.png");
  assert.strictEqual(core.logoAssetName("noext"), "logo.png");
});

test("buildProjectJSON: shadowEnabledがtrueならstyle.shadowを契約どおりに出力する", () => {
  const state = sampleState();
  state.monoContrast = 1.3;
  state.titleBounce = true;
  state.shadowEnabled = true;
  state.shadowColor = "#00C8FF";
  state.shadowAlpha = 0.9;
  state.shadowDistanceRatio = 0.05;
  state.shadowDirection = "left";
  state.shadowBlurRatio = 0.02;
  const project = core.buildProjectJSON(state);
  assert.strictEqual(project.style.mono_contrast, 1.3);
  assert.strictEqual(project.style.title_bounce, true);
  assert.deepStrictEqual(project.style.shadow, {
    color: "#00C8FF", alpha: 0.9, distance: 0.05, direction: "left",
    offset_y: core.DEFAULT_SHADOW.offsetY, blur: 0.02, source: "same"
  });
});

test("buildProjectJSON: shadowSourceを設定するとstyle.shadow.sourceに反映される", () => {
  const state = sampleState();
  state.shadowEnabled = true;
  state.shadowSource = "auto";
  const project = core.buildProjectJSON(state);
  assert.strictEqual(project.style.shadow.source, "auto");
});

test("buildProjectJSON: shadowEnabledがfalseならstyle.shadow={enabled:false}を明示的に出力する（キー省略はしない）", () => {
  // render.pyのresolve_shadow_configは"shadow"キー省略時を「既定で有効」と解釈するため、
  // オフにした意図を確実に伝えるには{"enabled": false}を明示する必要がある
  // （キーを省略すると実機で意図せず影が有効になってしまう不具合の再発防止）。
  const state = sampleState();
  const project = core.buildProjectJSON(state);
  assert.strictEqual(project.style.mono_contrast, 1.0);
  assert.strictEqual(project.style.title_bounce, false);
  assert.deepStrictEqual(project.style.shadow, { enabled: false });
  assert.strictEqual(project.logo, undefined);
});

test("buildProjectJSON: フリーズごとの film_color 上書きは廃止され出力されない（影は全体設定に一本化）", () => {
  const state = sampleState();
  const project = core.buildProjectJSON(state);
  project.freezes.forEach(fz => assert.strictEqual("film_color" in fz, false));
});

/* ---- background（背景の塗り種類・8種） ---- */

test("resolveBackgroundMode: 8種の既知の値はそのまま、未知の値はmonoにフォールバックする", () => {
  ["mono", "dark", "flat", "halftone", "stripes", "grid", "grain", "gradient"].forEach(m => {
    assert.strictEqual(core.resolveBackgroundMode(m), m);
  });
  assert.strictEqual(core.resolveBackgroundMode("rainbow"), "mono");
  assert.strictEqual(core.resolveBackgroundMode(undefined), "mono");
  assert.strictEqual(core.resolveBackgroundMode(null), "mono");
});

test("resolveBackgroundOptions: 未指定/不正値はDEFAULT_BACKGROUND_OPTIONSで補う", () => {
  const opts = core.resolveBackgroundOptions({});
  assert.deepStrictEqual(opts, core.DEFAULT_BACKGROUND_OPTIONS);
  const invalid = core.resolveBackgroundOptions({ base: "red", accent: "#zzzzzz", scale: 0, angle: "abc", opacity: -1 });
  assert.strictEqual(invalid.base, core.DEFAULT_BACKGROUND_OPTIONS.base);
  assert.strictEqual(invalid.accent, core.DEFAULT_BACKGROUND_OPTIONS.accent);
  assert.ok(invalid.scale > 0);
  assert.strictEqual(invalid.angle, core.DEFAULT_BACKGROUND_OPTIONS.angle);
  assert.strictEqual(invalid.opacity, 0);
});

test("resolveBackgroundOptions: 正しい値はそのまま(hexは大文字化)採用し、opacityは0〜1にクランプする", () => {
  const opts = core.resolveBackgroundOptions({ base: "#ff0000", accent: "#00ff00", scale: 0.05, angle: 30, opacity: 1.5 });
  assert.strictEqual(opts.base, "#FF0000");
  assert.strictEqual(opts.accent, "#00FF00");
  assert.strictEqual(opts.scale, 0.05);
  assert.strictEqual(opts.angle, 30);
  assert.strictEqual(opts.opacity, 1);
});

test("buildProjectJSON: state.backgroundが既定(mono)ならstyle.backgroundは'mono'、background_optionsキーは省略する", () => {
  const project = core.buildProjectJSON(sampleState());
  assert.strictEqual(project.style.background, "mono");
  assert.strictEqual("background_options" in project.style, false);
});

test("buildProjectJSON: state.backgroundが指定されていればstyle.backgroundに反映される（未知値はmonoにフォールバック）", () => {
  const state = Object.assign(sampleState(), { background: "halftone" });
  assert.strictEqual(core.buildProjectJSON(state).style.background, "halftone");
  const invalidState = Object.assign(sampleState(), { background: "invalid" });
  assert.strictEqual(core.buildProjectJSON(invalidState).style.background, "mono");
});

test("buildProjectJSON: state.backgroundOptionsが既定と異なればstyle.background_optionsを明示的に出力する", () => {
  const state = Object.assign(sampleState(), {
    background: "stripes",
    backgroundOptions: { base: "#111111", accent: "#EEEEEE", scale: 0.04, angle: 30, opacity: 0.8 }
  });
  const project = core.buildProjectJSON(state);
  assert.deepStrictEqual(project.style.background_options,
    { base: "#111111", accent: "#EEEEEE", scale: 0.04, angle: 30, opacity: 0.8 });
});

test("buildProjectJSON: フリーズのbackgroundは8種いずれも正しく出力され、未知値はmonoにフォールバックする", () => {
  const state = sampleState();
  state.freezes[0].background = "grid";
  state.freezes[1].background = "not-a-mode";
  const project = core.buildProjectJSON(state);
  const byTime = t => project.freezes.find(f => f.time === t);
  assert.strictEqual(byTime(5.5).background, "grid");
  assert.strictEqual(byTime(2.5).background, "mono");
});

test("parseProjectJSON: style.background/style.background_optionsを読み込める", () => {
  const loaded = core.parseProjectJSON({
    style: { background: "gradient", background_options: { base: "#010203", accent: "#040506", scale: 0.03, angle: 10, opacity: 0.5 } },
    freezes: []
  });
  assert.strictEqual(loaded.background, "gradient");
  assert.deepStrictEqual(loaded.backgroundOptions,
    { base: "#010203", accent: "#040506", scale: 0.03, angle: 10, opacity: 0.5 });
});

test("parseProjectJSON: style.backgroundが省略されていれば既定値'mono'/DEFAULT_BACKGROUND_OPTIONSを補う", () => {
  const loaded = core.parseProjectJSON({ freezes: [] });
  assert.strictEqual(loaded.background, "mono");
  assert.deepStrictEqual(loaded.backgroundOptions, core.DEFAULT_BACKGROUND_OPTIONS);
});

test("parseProjectJSON: フリーズにbackgroundが無ければstyle.backgroundを継承する", () => {
  const loaded = core.parseProjectJSON({
    style: { background: "grain" },
    freezes: [{ time: 0, name: "" }, { time: 1, name: "", background: "flat" }]
  });
  assert.strictEqual(loaded.freezes[0].background, "grain");
  assert.strictEqual(loaded.freezes[1].background, "flat");
});

test("buildProjectJSON/parseProjectJSON: 全体設定のbackground/backgroundOptionsを往復できる", () => {
  const state = Object.assign(sampleState(), {
    background: "halftone",
    backgroundOptions: { base: "#123456", accent: "#654321", scale: 0.015, angle: 60, opacity: 0.3 }
  });
  const project = core.buildProjectJSON(state);
  const loaded = core.parseProjectJSON(project);
  assert.strictEqual(loaded.background, "halftone");
  assert.deepStrictEqual(loaded.backgroundOptions, state.backgroundOptions);
});

test("buildProjectJSON: 旧JSON相当（backgroundOptions未設定）はbackground_optionsキーを出力せず、mono/darkのみの旧仕様と完全後方互換", () => {
  const project = core.buildProjectJSON(sampleState());
  assert.strictEqual("background_options" in project.style, false);
  project.freezes.forEach(fz => assert.ok(["mono", "dark"].indexOf(fz.background) >= 0));
});

test("buildProjectJSON: フリーズ単位でbackgroundOptionsを実体化していれば、その行だけbackground_optionsが出力される"
  + "（実機で「単色背景flatの色がフリーズ単位で選べない」不具合の再発防止）", () => {
  const state = sampleState();
  state.freezes[0].background = "flat";
  state.freezes[0].backgroundOptions = { base: "#ABCDEF", accent: "#000000", scale: 0.02, angle: 45, opacity: 1.0 };
  const project = core.buildProjectJSON(state);
  const byTime = t => project.freezes.find(f => f.time === t);
  assert.deepStrictEqual(byTime(5.5).background_options,
    { base: "#ABCDEF", accent: "#000000", scale: 0.02, angle: 45, opacity: 1.0 });
  assert.strictEqual("background_options" in byTime(2.5), false,
    "backgroundOptionsを実体化していない行はbackground_optionsキー自体を出力しない（全体設定を継承）");
});

test("parseProjectJSON: freezes[].background_optionsを読み込める。無ければnull（全体設定を継承）", () => {
  const loaded = core.parseProjectJSON({
    style: { background: "mono" },
    freezes: [
      { time: 0, name: "上書きあり", background: "flat", background_options: { base: "#112233" } },
      { time: 1, name: "上書きなし", background: "flat" }
    ]
  });
  assert.deepStrictEqual(loaded.freezes[0].backgroundOptions,
    core.resolveBackgroundOptions({ base: "#112233" }));
  assert.strictEqual(loaded.freezes[1].backgroundOptions, null);
});

test("buildProjectJSON/parseProjectJSON: フリーズ単位のbackgroundOptionsを往復できる", () => {
  const state = sampleState();
  state.freezes[0].background = "gradient";
  state.freezes[0].backgroundOptions = { base: "#010101", accent: "#020202", scale: 0.03, angle: 90, opacity: 0.6 };
  const project = core.buildProjectJSON(state);
  const loaded = core.parseProjectJSON(project);
  const byTime = t => loaded.freezes.find(f => f.time === t);
  assert.deepStrictEqual(byTime(5.5).backgroundOptions, state.freezes[0].backgroundOptions);
  assert.strictEqual(byTime(2.5).backgroundOptions, null);
});

/* ---- mask_style（人物マスク・影の縁の種類） ---- */

test("resolveMaskStyle: 5種の既知の値はそのまま、未知の値はsolidにフォールバックする", () => {
  ["solid", "halftone", "pixel", "outline", "rough"].forEach(m => {
    assert.strictEqual(core.resolveMaskStyle(m), m);
  });
  assert.strictEqual(core.resolveMaskStyle("rainbow"), "solid");
  assert.strictEqual(core.resolveMaskStyle(undefined), "solid");
  assert.strictEqual(core.resolveMaskStyle(null), "solid");
});

test("resolveMaskStyleOptions: 未指定/不正値はDEFAULT_MASK_STYLE_OPTIONSで補う", () => {
  const opts = core.resolveMaskStyleOptions({});
  assert.deepStrictEqual(opts, core.DEFAULT_MASK_STYLE_OPTIONS);
  const invalid = core.resolveMaskStyleOptions({ scale: 0, color: "not-a-color", width: -1 });
  assert.ok(invalid.scale > 0);
  assert.strictEqual(invalid.color, core.DEFAULT_MASK_STYLE_OPTIONS.color);
  assert.ok(invalid.width > 0);
});

test("resolveMaskStyleOptions: 正しい値はそのまま(hexは大文字化)採用する", () => {
  const opts = core.resolveMaskStyleOptions({ scale: 0.02, color: "#ff0000", width: 0.01 });
  assert.strictEqual(opts.scale, 0.02);
  assert.strictEqual(opts.color, "#FF0000");
  assert.strictEqual(opts.width, 0.01);
});

test("buildProjectJSON: state.maskStyleが既定(solid)ならstyle.mask_styleは'solid'、mask_style_optionsキーは省略する", () => {
  const project = core.buildProjectJSON(sampleState());
  assert.strictEqual(project.style.mask_style, "solid");
  assert.strictEqual("mask_style_options" in project.style, false);
});

test("buildProjectJSON: state.maskStyleが指定されていればstyle.mask_styleに反映される（未知値はsolidにフォールバック）", () => {
  const state = Object.assign(sampleState(), { maskStyle: "outline" });
  assert.strictEqual(core.buildProjectJSON(state).style.mask_style, "outline");
  const invalidState = Object.assign(sampleState(), { maskStyle: "invalid" });
  assert.strictEqual(core.buildProjectJSON(invalidState).style.mask_style, "solid");
});

test("buildProjectJSON: state.maskStyleOptionsが既定と異なればstyle.mask_style_optionsを明示的に出力する", () => {
  const state = Object.assign(sampleState(), {
    maskStyle: "halftone",
    maskStyleOptions: { scale: 0.02, color: "#123456", width: 0.01 }
  });
  const project = core.buildProjectJSON(state);
  assert.deepStrictEqual(project.style.mask_style_options, { scale: 0.02, color: "#123456", width: 0.01 });
});

test("buildProjectJSON: フリーズのmask_styleは常に出力され、未知値はsolidにフォールバックする", () => {
  const state = sampleState();
  state.freezes[0].maskStyle = "rough";
  state.freezes[1].maskStyle = "not-a-style";
  const project = core.buildProjectJSON(state);
  const byTime = t => project.freezes.find(f => f.time === t);
  assert.strictEqual(byTime(5.5).mask_style, "rough");
  assert.strictEqual(byTime(2.5).mask_style, "solid");
});

test("parseProjectJSON: style.mask_style/style.mask_style_optionsを読み込める", () => {
  const loaded = core.parseProjectJSON({
    style: { mask_style: "pixel", mask_style_options: { scale: 0.03, color: "#010203", width: 0.006 } },
    freezes: []
  });
  assert.strictEqual(loaded.maskStyle, "pixel");
  assert.deepStrictEqual(loaded.maskStyleOptions, { scale: 0.03, color: "#010203", width: 0.006 });
});

test("parseProjectJSON: style.mask_styleが省略されていれば既定値'solid'/DEFAULT_MASK_STYLE_OPTIONSを補う", () => {
  const loaded = core.parseProjectJSON({ freezes: [] });
  assert.strictEqual(loaded.maskStyle, "solid");
  assert.deepStrictEqual(loaded.maskStyleOptions, core.DEFAULT_MASK_STYLE_OPTIONS);
});

test("parseProjectJSON: フリーズにmask_styleが無ければstyle.mask_styleを継承する", () => {
  const loaded = core.parseProjectJSON({
    style: { mask_style: "outline" },
    freezes: [{ time: 0, name: "" }, { time: 1, name: "", mask_style: "pixel" }]
  });
  assert.strictEqual(loaded.freezes[0].maskStyle, "outline");
  assert.strictEqual(loaded.freezes[1].maskStyle, "pixel");
});

test("buildProjectJSON/parseProjectJSON: 全体設定のmaskStyle/maskStyleOptionsを往復できる", () => {
  const state = Object.assign(sampleState(), {
    maskStyle: "rough",
    maskStyleOptions: { scale: 0.008, color: "#ABCDEF", width: 0.002 }
  });
  const project = core.buildProjectJSON(state);
  const loaded = core.parseProjectJSON(project);
  assert.strictEqual(loaded.maskStyle, "rough");
  assert.deepStrictEqual(loaded.maskStyleOptions, state.maskStyleOptions);
});

test("buildProjectJSON: 旧JSON相当（maskStyleOptions未設定）はmask_style_optionsキーを出力せず、solidのみの旧仕様と完全後方互換", () => {
  const project = core.buildProjectJSON(sampleState());
  assert.strictEqual("mask_style_options" in project.style, false);
  project.freezes.forEach(fz => assert.strictEqual(fz.mask_style, "solid"));
});

/* ---- text_backing（テロップの可読性：座布団等） ---- */

test("resolveTextBacking: 5種の既知の値はそのまま、未知の値はoutlineにフォールバックする", () => {
  ["none", "outline", "shadow", "box", "band"].forEach(m => {
    assert.strictEqual(core.resolveTextBacking(m), m);
  });
  assert.strictEqual(core.resolveTextBacking("rainbow"), "outline");
  assert.strictEqual(core.resolveTextBacking(undefined), "outline");
  assert.strictEqual(core.resolveTextBacking(null), "outline");
});

test("resolveTextBackingOptions: 未指定/不正値はDEFAULT_TEXT_BACKING_OPTIONSで補う", () => {
  const opts = core.resolveTextBackingOptions({});
  assert.deepStrictEqual(opts, core.DEFAULT_TEXT_BACKING_OPTIONS);
  const invalid = core.resolveTextBackingOptions({ color: "not-a-color", opacity: -1, radius: -1, padding: -1 });
  assert.strictEqual(invalid.color, core.DEFAULT_TEXT_BACKING_OPTIONS.color);
  assert.strictEqual(invalid.opacity, 0);
  assert.ok(invalid.radius >= 0 && invalid.padding >= 0);
});

test("buildProjectJSON: state.textBackingが既定(outline)ならstyle.text_backingは'outline'、text_backing_optionsキーは省略する", () => {
  const project = core.buildProjectJSON(sampleState());
  assert.strictEqual(project.style.text_backing, "outline");
  assert.strictEqual("text_backing_options" in project.style, false);
  assert.strictEqual(project.style.auto_contrast, false,
    "実機で座布団が意図せず表示される不具合の再発防止のためauto_contrastの既定はfalse");
});

test("buildProjectJSON: state.textBacking/autoContrastが指定されていればstyleに反映される", () => {
  const state = Object.assign(sampleState(), { textBacking: "box", autoContrast: false });
  const project = core.buildProjectJSON(state);
  assert.strictEqual(project.style.text_backing, "box");
  assert.strictEqual(project.style.auto_contrast, false);
});

test("buildProjectJSON: state.textBackingOptionsが既定と異なればstyle.text_backing_optionsを明示的に出力する", () => {
  const state = Object.assign(sampleState(), {
    textBacking: "box",
    textBackingOptions: { color: "#123456", opacity: 0.8, radius: 0.1, padding: 0.2 }
  });
  const project = core.buildProjectJSON(state);
  assert.deepStrictEqual(project.style.text_backing_options,
    { color: "#123456", opacity: 0.8, radius: 0.1, padding: 0.2 });
});

test("parseProjectJSON: style.text_backing/auto_contrast/text_backing_optionsを読み込める", () => {
  const loaded = core.parseProjectJSON({
    style: { text_backing: "shadow", auto_contrast: false,
             text_backing_options: { color: "#010203", opacity: 0.4, radius: 0.15, padding: 0.25 } },
    freezes: [],
  });
  assert.strictEqual(loaded.textBacking, "shadow");
  assert.strictEqual(loaded.autoContrast, false);
  assert.deepStrictEqual(loaded.textBackingOptions, { color: "#010203", opacity: 0.4, radius: 0.15, padding: 0.25 });
});

test("parseProjectJSON: style.text_backingが省略されていれば既定値'outline'/false/DEFAULT_TEXT_BACKING_OPTIONSを補う", () => {
  const loaded = core.parseProjectJSON({ freezes: [] });
  assert.strictEqual(loaded.textBacking, "outline");
  assert.strictEqual(loaded.autoContrast, false);
  assert.deepStrictEqual(loaded.textBackingOptions, core.DEFAULT_TEXT_BACKING_OPTIONS);
});

test("buildProjectJSON/parseProjectJSON: 全体設定のtextBacking/autoContrast/textBackingOptionsを往復できる", () => {
  const state = Object.assign(sampleState(), {
    textBacking: "band",
    autoContrast: false,
    textBackingOptions: { color: "#ABCDEF", opacity: 0.3, radius: 0.5, padding: 0.6 }
  });
  const project = core.buildProjectJSON(state);
  const loaded = core.parseProjectJSON(project);
  assert.strictEqual(loaded.textBacking, "band");
  assert.strictEqual(loaded.autoContrast, false);
  assert.deepStrictEqual(loaded.textBackingOptions, state.textBackingOptions);
});

test("normalizeTitleLines/titleLineToJSON: 行ごとのtext_backingは値がある時だけ出力され、往復できる", () => {
  const lines = core.normalizeTitleLines({ lines: [
    { text: "行1" }, { text: "行2", text_backing: "box" }, { text: "行3", text_backing: "invalid" }
  ] });
  assert.strictEqual(lines[0].backing, "");
  assert.strictEqual(lines[1].backing, "box");
  assert.strictEqual(lines[2].backing, "", "不明な値は既定（空文字＝全体継承）にフォールバックする");
  const json1 = core.titleLineToJSON(lines[0], [], true);
  const json2 = core.titleLineToJSON(lines[1], [], false);
  assert.strictEqual("text_backing" in json1, false);
  assert.strictEqual(json2.text_backing, "box");
});

/* ---- subject_outline（人物輪郭アウトライン） ---- */

test("resolveSubjectOutlineConfig: 未指定/不正値はDEFAULT_SUBJECT_OUTLINEで補う", () => {
  const cfg = core.resolveSubjectOutlineConfig({});
  assert.deepStrictEqual(cfg, core.DEFAULT_SUBJECT_OUTLINE);
  const negWidth = core.resolveSubjectOutlineConfig({ enabled: true, width: -1, color: "auto" });
  assert.ok(negWidth.width > 0, "widthが負の値でも下限でクランプされる");
  assert.strictEqual(negWidth.enabled, true);
  const customColor = core.resolveSubjectOutlineConfig({ color: "#FF0000" });
  assert.strictEqual(customColor.color, "#FF0000");
});

test("buildProjectJSON: state.subjectOutlineが既定(enabled=false)ならstyle.subject_outlineキーを省略する（完全後方互換）", () => {
  const project = core.buildProjectJSON(sampleState());
  assert.strictEqual("subject_outline" in project.style, false);
});

test("buildProjectJSON: state.subjectOutline.enabled=trueならstyle.subject_outlineを明示的に出力する", () => {
  const state = Object.assign(sampleState(), {
    subjectOutline: { enabled: true, width: 0.005, color: "#00FF00" }
  });
  const project = core.buildProjectJSON(state);
  assert.deepStrictEqual(project.style.subject_outline, { enabled: true, width: 0.005, color: "#00FF00" });
});

test("parseProjectJSON: style.subject_outlineを読み込める。省略時はDEFAULT_SUBJECT_OUTLINEを補う", () => {
  const loaded = core.parseProjectJSON({
    style: { subject_outline: { enabled: true, width: 0.003, color: "auto" } },
    freezes: [],
  });
  assert.deepStrictEqual(loaded.subjectOutline, { enabled: true, width: 0.003, color: "auto" });
  const defaultLoaded = core.parseProjectJSON({ freezes: [] });
  assert.deepStrictEqual(defaultLoaded.subjectOutline, core.DEFAULT_SUBJECT_OUTLINE);
});

test("buildProjectJSON/parseProjectJSON: 全体設定のsubjectOutlineを往復できる", () => {
  const state = Object.assign(sampleState(), {
    subjectOutline: { enabled: true, width: 0.004, color: "#123ABC" }
  });
  const project = core.buildProjectJSON(state);
  const loaded = core.parseProjectJSON(project);
  assert.deepStrictEqual(loaded.subjectOutline, state.subjectOutline);
});

/* ---- safe_zone（セーフゾーン自動クランプ） ---- */

test("resolveSafeZone: 未指定/不正値はDEFAULT_SAFE_ZONEで補う", () => {
  const cfg = core.resolveSafeZone({});
  assert.deepStrictEqual(cfg, core.DEFAULT_SAFE_ZONE);
  const negLeft = core.resolveSafeZone({ left: -10, enabled: false });
  assert.strictEqual(negLeft.left, 0, "負の値は0でクランプされる");
  assert.strictEqual(negLeft.enabled, false);
  const custom = core.resolveSafeZone({ left: 10, top: 20, right: 30, bottom: 40 });
  assert.deepStrictEqual(custom, { enabled: true, left: 10, top: 20, right: 30, bottom: 40 });
});

test("safeZoneRectPx: 1080x1920（基準解像度）ではpx指定がそのまま矩形になる。無効/矩形が潰れる場合は画面全体", () => {
  const rect = core.safeZoneRectPx(core.DEFAULT_SAFE_ZONE, 1080, 1920);
  assert.deepStrictEqual(rect, { left: 40, top: 120, right: 1080 - 150, bottom: 1920 - 400 });

  const halfRect = core.safeZoneRectPx(core.DEFAULT_SAFE_ZONE, 540, 960);
  assert.ok(Math.abs(halfRect.left - 20) < 0.01 && Math.abs(halfRect.right - (540 - 75)) < 0.01,
    "半分の解像度ではマージンも比率換算され半分になる");

  const disabledRect = core.safeZoneRectPx(core.resolveSafeZone({ enabled: false }), 1080, 1920);
  assert.deepStrictEqual(disabledRect, { left: 0, top: 0, right: 1080, bottom: 1920 });

  const collapsedRect = core.safeZoneRectPx(
    core.resolveSafeZone({ left: 2000, right: 2000 }), 1080, 1920);
  assert.deepStrictEqual(collapsedRect, { left: 0, top: 0, right: 1080, bottom: 1920 },
    "マージンが大きすぎて矩形が潰れる場合は画面全体にフォールバックする");
});

test("clampBoxToRect: 矩形の外にはみ出た分だけ内側へ押し戻す量を返す。内側なら移動量は0", () => {
  const d = core.clampBoxToRect(
    { left: 10, top: 10, right: 100, bottom: 100 },
    { left: 40, top: 40, right: 500, bottom: 500 });
  assert.strictEqual(d.dx, 30);
  assert.strictEqual(d.dy, 30);
  const d2 = core.clampBoxToRect(
    { left: 50, top: 50, right: 100, bottom: 100 },
    { left: 0, top: 0, right: 200, bottom: 200 });
  assert.strictEqual(d2.dx, 0);
  assert.strictEqual(d2.dy, 0);
});

test("buildProjectJSON: state.safeZoneが既定のときはstyle.safe_zoneキーを省略する（完全後方互換）", () => {
  const project = core.buildProjectJSON(sampleState());
  assert.strictEqual("safe_zone" in project.style, false);
});

test("buildProjectJSON: state.safeZoneが既定と異なればstyle.safe_zoneを明示的に出力する（無効化も含む）", () => {
  const project = core.buildProjectJSON(Object.assign(sampleState(), {
    safeZone: { enabled: false, left: 40, top: 120, right: 150, bottom: 400 }
  }));
  assert.deepStrictEqual(project.style.safe_zone, { enabled: false, left: 40, top: 120, right: 150, bottom: 400 });
});

test("parseProjectJSON: style.safe_zoneを読み込める。省略時はDEFAULT_SAFE_ZONEを補う", () => {
  const loaded = core.parseProjectJSON({
    style: { safe_zone: { enabled: true, left: 10, top: 20, right: 30, bottom: 40 } },
    freezes: [],
  });
  assert.deepStrictEqual(loaded.safeZone, { enabled: true, left: 10, top: 20, right: 30, bottom: 40 });
  const defaultLoaded = core.parseProjectJSON({ freezes: [] });
  assert.deepStrictEqual(defaultLoaded.safeZone, core.DEFAULT_SAFE_ZONE);
});

test("buildProjectJSON/parseProjectJSON: 全体設定のsafeZoneを往復できる", () => {
  const state = Object.assign(sampleState(), {
    safeZone: { enabled: true, left: 60, top: 100, right: 80, bottom: 300 }
  });
  const project = core.buildProjectJSON(state);
  const loaded = core.parseProjectJSON(project);
  assert.deepStrictEqual(loaded.safeZone, state.safeZone);
});

test("buildProjectJSON: logoにimageNameがあればlogoブロックを出力し、無ければ省略する", () => {
  const withLogo = core.buildProjectJSON(Object.assign(sampleState(), {
    logo: { imageName: "logo.png", at: "last_freeze", background: "auto", durationSec: 1.2, sfx: "don" }
  }));
  assert.deepStrictEqual(withLogo.logo,
    { image: "logo.png", at: "last_freeze", background: "auto",
      start_width_ratio: core.DEFAULT_LOGO_START_WIDTH_RATIO,
      hold_big_sec: core.DEFAULT_LOGO_HOLD_BIG_SEC,
      shrink_sec: core.DEFAULT_LOGO_SHRINK_SEC,
      settle_sec: core.DEFAULT_LOGO_SETTLE_SEC,
      duration_sec: 1.2, sfx: "don", auto_transparent_bg: true });

  const withoutLogo = core.buildProjectJSON(Object.assign(sampleState(), {
    logo: { imageFile: null, imageName: "", at: "end", background: "auto", durationSec: 1.2, sfx: "don" }
  }));
  assert.strictEqual(withoutLogo.logo, undefined);
});

test("buildProjectJSON/parseProjectJSON: start_width_ratio/hold_big_sec/shrink_sec/settle_secが往復する", () => {
  const project = core.buildProjectJSON(Object.assign(sampleState(), {
    logo: { imageName: "logo.png", at: "end", background: "auto",
            startWidthRatio: 2.1, holdBigSec: 0.3, shrinkSec: 0.8, settleSec: 0.25, durationSec: 1.2, sfx: "don" }
  }));
  assert.strictEqual(project.logo.start_width_ratio, 2.1);
  assert.strictEqual(project.logo.hold_big_sec, 0.3);
  assert.strictEqual(project.logo.shrink_sec, 0.8);
  assert.strictEqual(project.logo.settle_sec, 0.25);

  const loaded = core.parseProjectJSON(project);
  assert.strictEqual(loaded.logo.startWidthRatio, 2.1);
  assert.strictEqual(loaded.logo.holdBigSec, 0.3);
  assert.strictEqual(loaded.logo.shrinkSec, 0.8);
  assert.strictEqual(loaded.logo.settleSec, 0.25);
});

test("buildProjectJSON: logo.atが'last_freeze'以外なら'end'に、sfx未設定ならsfxキー省略、backgroundは既定でauto", () => {
  const project = core.buildProjectJSON(Object.assign(sampleState(), {
    logo: { imageName: "logo.png", at: "something-unknown", durationSec: 2, sfx: "" }
  }));
  assert.strictEqual(project.logo.at, "end");
  assert.strictEqual(project.logo.background, "auto");
  assert.strictEqual("sfx" in project.logo, false);
});

test("buildProjectJSON: logo.backgroundに色指定(#RRGGBB)や'video'をそのまま出力する", () => {
  const withColor = core.buildProjectJSON(Object.assign(sampleState(), {
    logo: { imageName: "logo.png", at: "end", background: "#112233", durationSec: 1.2 }
  }));
  assert.strictEqual(withColor.logo.background, "#112233");

  const withVideo = core.buildProjectJSON(Object.assign(sampleState(), {
    logo: { imageName: "logo.png", at: "end", background: "video", durationSec: 1.2 }
  }));
  assert.strictEqual(withVideo.logo.background, "video");
});

test("parseProjectJSON: 新しいstyleキー・style.shadow・logo(background込み)ブロックを読み込める", () => {
  const project = {
    version: 1, video: "v.mp4",
    style: {
      freeze_sec: 1.8, mono_contrast: 1.3, title_bounce: true,
      shadow: { color: "#00C8FF", alpha: 0.9, distance: 0.05, direction: "left", offset_y: 0.01, blur: 0.02, slide_sec: 0.3 }
    },
    freezes: [{ time: 2.5, name: "赤い人", brush_shape: "round", strokes: [] }],
    logo: { image: "store_logo.png", at: "last_freeze", background: "#00C8FF", duration_sec: 1.2, sfx: "don" }
  };
  const loaded = core.parseProjectJSON(project);
  assert.strictEqual(loaded.monoContrast, 1.3);
  assert.strictEqual(loaded.titleBounce, true);
  assert.strictEqual(loaded.shadowEnabled, true);
  assert.strictEqual(loaded.shadowColor, "#00C8FF");
  assert.strictEqual(loaded.shadowAlpha, 0.9);
  assert.strictEqual(loaded.shadowDistanceRatio, 0.05);
  assert.strictEqual(loaded.shadowDirection, "left");
  assert.strictEqual(loaded.shadowBlurRatio, 0.02);
  assert.deepStrictEqual(loaded.logo, {
    imageFile: null, imageName: "store_logo.png", at: "last_freeze", background: "#00C8FF",
    startWidthRatio: core.DEFAULT_LOGO_START_WIDTH_RATIO,
    holdBigSec: core.DEFAULT_LOGO_HOLD_BIG_SEC,
    shrinkSec: core.DEFAULT_LOGO_SHRINK_SEC,
    settleSec: core.DEFAULT_LOGO_SETTLE_SEC,
    durationSec: 1.2, sfx: "don", sfxLibraryId: null, sfxAlign: "start_at_landing", sfxMissingFile: "",
    autoColorHex: "", autoTransparentBg: true,
    spinEnabled: core.DEFAULT_LOGO_SPIN.enabled, spinSec: core.DEFAULT_LOGO_SPIN.sec,
    spinDegrees: core.DEFAULT_LOGO_SPIN.degrees, spinEase: core.DEFAULT_LOGO_SPIN.ease,
    spinPerspective: core.DEFAULT_LOGO_SPIN.perspective
  });
});

test("parseProjectJSON: logo.backgroundが省略されていれば既定値'auto'を補う", () => {
  const loaded = core.parseProjectJSON({
    version: 1, video: "v.mp4", freezes: [],
    logo: { image: "logo.png", at: "end", duration_sec: 1.2 }
  });
  assert.strictEqual(loaded.logo.background, "auto");
});

/* ---- watermark（常時表示の透かしロゴ） ---- */

test("watermarkAssetName: 拡張子を保ったまま watermark.<ext> にする。無ければpngにフォールバック", () => {
  assert.strictEqual(core.watermarkAssetName("mylogo.PNG"), "watermark.png");
  assert.strictEqual(core.watermarkAssetName("mylogo.jpg"), "watermark.jpg");
  assert.strictEqual(core.watermarkAssetName("noext"), "watermark.png");
});

test("defaultWatermarkState: 無効・画像未選択・render.pyのWATERMARK_DEFAULTS等と同じ既定値を返す", () => {
  const wm = core.defaultWatermarkState();
  assert.strictEqual(wm.enabled, false);
  assert.strictEqual(wm.imageFile, null);
  assert.strictEqual(wm.imageName, "");
  assert.strictEqual(wm.position, core.DEFAULT_WATERMARK.position);
  assert.strictEqual(wm.widthRatio, core.DEFAULT_WATERMARK.widthRatio);
  assert.strictEqual(wm.opacity, core.DEFAULT_WATERMARK.opacity);
  assert.strictEqual(wm.margin, core.DEFAULT_WATERMARK.margin);
  assert.strictEqual(wm.shineEnabled, core.DEFAULT_WATERMARK_SHINE.enabled);
  assert.strictEqual(wm.spinEnabled, core.DEFAULT_WATERMARK_SPIN.enabled);
});

test("buildProjectJSON: watermarkが有効かつimageNameがあればwatermarkブロックを出力し、無効/未選択なら省略する", () => {
  const withWatermark = core.buildProjectJSON(Object.assign(sampleState(), {
    watermark: Object.assign(core.defaultWatermarkState(), {
      enabled: true, imageName: "watermark.png", position: "top_left",
      widthRatio: 0.2, opacity: 0.9, margin: 0.05
    })
  }));
  assert.deepStrictEqual(withWatermark.watermark, {
    image: "watermark.png", position: "top_left", width_ratio: 0.2, opacity: 0.9, margin: 0.05,
    auto_transparent_bg: true,
    shine: { enabled: core.DEFAULT_WATERMARK_SHINE.enabled,
             interval_sec: core.DEFAULT_WATERMARK_SHINE.intervalSec, sec: core.DEFAULT_WATERMARK_SHINE.sec },
    spin: { enabled: core.DEFAULT_WATERMARK_SPIN.enabled,
            interval_sec: core.DEFAULT_WATERMARK_SPIN.intervalSec, sec: core.DEFAULT_WATERMARK_SPIN.sec,
            degrees: core.DEFAULT_WATERMARK_SPIN.degrees, ease: core.DEFAULT_WATERMARK_SPIN.ease,
            perspective: core.DEFAULT_WATERMARK_SPIN.perspective }
  });

  const disabled = core.buildProjectJSON(Object.assign(sampleState(), {
    watermark: Object.assign(core.defaultWatermarkState(), { enabled: false, imageName: "watermark.png" })
  }));
  assert.strictEqual(disabled.watermark, undefined);

  const noImage = core.buildProjectJSON(Object.assign(sampleState(), {
    watermark: Object.assign(core.defaultWatermarkState(), { enabled: true, imageName: "" })
  }));
  assert.strictEqual(noImage.watermark, undefined);

  const noWatermarkAtAll = core.buildProjectJSON(sampleState());
  assert.strictEqual(noWatermarkAtAll.watermark, undefined);
});

test("buildProjectJSON: watermark.shine/spinを個別にenabled=falseにでき、interval_sec/secも反映される", () => {
  const project = core.buildProjectJSON(Object.assign(sampleState(), {
    watermark: Object.assign(core.defaultWatermarkState(), {
      enabled: true, imageName: "watermark.png",
      shineEnabled: false, shineIntervalSec: 2, shineSec: 0.3,
      spinEnabled: false, spinIntervalSec: 5, spinSec: 1.0,
      spinDegrees: 180, spinEase: "linear", spinPerspective: 0.7
    })
  }));
  assert.deepStrictEqual(project.watermark.shine, { enabled: false, interval_sec: 2, sec: 0.3 });
  assert.deepStrictEqual(project.watermark.spin,
    { enabled: false, interval_sec: 5, sec: 1.0, degrees: 180, ease: "linear", perspective: 0.7 });
});

test("parseProjectJSON: watermarkブロックを読み込める。省略時はdefaultWatermarkState()相当になる", () => {
  const loaded = core.parseProjectJSON({
    version: 1, video: "v.mp4", freezes: [],
    watermark: {
      image: "watermark.jpg", position: "top_right", width_ratio: 0.25, opacity: 0.7, margin: 0.04,
      shine: { enabled: false, interval_sec: 3, sec: 0.4 },
      spin: { enabled: true, interval_sec: 10, sec: 1.2, degrees: 180, ease: "linear", perspective: 0.9 }
    }
  });
  assert.deepStrictEqual(loaded.watermark, {
    enabled: true, imageFile: null, imageName: "watermark.jpg", position: "top_right",
    widthRatio: 0.25, opacity: 0.7, margin: 0.04,
    shineEnabled: false, shineIntervalSec: 3, shineSec: 0.4,
    spinEnabled: true, spinIntervalSec: 10, spinSec: 1.2,
    spinDegrees: 180, spinEase: "linear", spinPerspective: 0.9,
    autoTransparentBg: true
  });

  const loadedNone = core.parseProjectJSON({ version: 1, video: "v.mp4", freezes: [] });
  assert.deepStrictEqual(loadedNone.watermark, core.defaultWatermarkState());
});

test("parseProjectJSON: watermark.positionが未知の値なら既定(bottom_right)にフォールバックする", () => {
  const loaded = core.parseProjectJSON({
    version: 1, video: "v.mp4", freezes: [],
    watermark: { image: "watermark.png", position: "center" }
  });
  assert.strictEqual(loaded.watermark.position, core.DEFAULT_WATERMARK_POSITION);
});

test("buildProjectJSON/parseProjectJSON: watermarkの全フィールドが往復する", () => {
  const state = Object.assign(sampleState(), {
    watermark: {
      enabled: true, imageFile: null, imageName: "watermark.png", position: "bottom_left",
      widthRatio: 0.12, opacity: 0.6, margin: 0.02,
      shineEnabled: true, shineIntervalSec: 6, shineSec: 0.8,
      spinEnabled: false, spinIntervalSec: 12, spinSec: 1.5,
      spinDegrees: 180, spinEase: "linear", spinPerspective: 0.6
    }
  });
  const project = core.buildProjectJSON(state);
  const loaded = core.parseProjectJSON(project);
  assert.deepStrictEqual(loaded.watermark, {
    enabled: true, imageFile: null, imageName: "watermark.png", position: "bottom_left",
    widthRatio: 0.12, opacity: 0.6, margin: 0.02, autoTransparentBg: true,
    shineEnabled: true, shineIntervalSec: 6, shineSec: 0.8,
    spinEnabled: false, spinIntervalSec: 12, spinSec: 1.5,
    spinDegrees: 180, spinEase: "linear", spinPerspective: 0.6
  });
});

/* ---- logo.spin（ラストロゴの3D回転演出） ---- */

test("defaultLogoState: spinはrender.pyのLOGO_SPIN_DEFAULTSと同じ既定値（無効）を返す", () => {
  const logo = core.defaultLogoState();
  assert.strictEqual(logo.spinEnabled, core.DEFAULT_LOGO_SPIN.enabled);
  assert.strictEqual(logo.spinEnabled, false);
  assert.strictEqual(logo.spinSec, core.DEFAULT_LOGO_SPIN.sec);
  assert.strictEqual(logo.spinDegrees, core.DEFAULT_LOGO_SPIN.degrees);
  assert.strictEqual(logo.spinEase, core.DEFAULT_LOGO_SPIN.ease);
  assert.strictEqual(logo.spinPerspective, core.DEFAULT_LOGO_SPIN.perspective);
});

test("buildProjectJSON: logo.spinEnabled=falseなら logo.spin キー自体を省略する（後方互換）", () => {
  const project = core.buildProjectJSON(Object.assign(sampleState(), {
    logo: Object.assign(core.defaultLogoState(), { imageName: "logo.png", spinEnabled: false })
  }));
  assert.strictEqual(project.logo.spin, undefined);
});

test("buildProjectJSON: logo.spinEnabled=trueならlogo.spinブロックを出力する", () => {
  const project = core.buildProjectJSON(Object.assign(sampleState(), {
    logo: Object.assign(core.defaultLogoState(), {
      imageName: "logo.png", spinEnabled: true, spinSec: 1.0, spinDegrees: 180,
      spinEase: "linear", spinPerspective: 0.7
    })
  }));
  assert.deepStrictEqual(project.logo.spin,
    { enabled: true, sec: 1.0, degrees: 180, ease: "linear", perspective: 0.7 });
});

test("parseProjectJSON: logo.spinを読み込める。省略時はDEFAULT_LOGO_SPIN相当になる", () => {
  const loaded = core.parseProjectJSON({
    version: 1, video: "v.mp4", freezes: [],
    logo: { image: "logo.png", at: "end", duration_sec: 1.2,
            spin: { enabled: true, degrees: 180, sec: 1.0, ease: "linear", perspective: 0.7 } }
  });
  assert.strictEqual(loaded.logo.spinEnabled, true);
  assert.strictEqual(loaded.logo.spinSec, 1.0);
  assert.strictEqual(loaded.logo.spinDegrees, 180);
  assert.strictEqual(loaded.logo.spinEase, "linear");
  assert.strictEqual(loaded.logo.spinPerspective, 0.7);

  const loadedNoSpin = core.parseProjectJSON({
    version: 1, video: "v.mp4", freezes: [],
    logo: { image: "logo.png", at: "end", duration_sec: 1.2 }
  });
  assert.strictEqual(loadedNoSpin.logo.spinEnabled, core.DEFAULT_LOGO_SPIN.enabled);
  assert.strictEqual(loadedNoSpin.logo.spinSec, core.DEFAULT_LOGO_SPIN.sec);
  assert.strictEqual(loadedNoSpin.logo.spinDegrees, core.DEFAULT_LOGO_SPIN.degrees);
  assert.strictEqual(loadedNoSpin.logo.spinEase, core.DEFAULT_LOGO_SPIN.ease);
  assert.strictEqual(loadedNoSpin.logo.spinPerspective, core.DEFAULT_LOGO_SPIN.perspective);
});

test("buildProjectJSON/parseProjectJSON: logo.spinの全フィールドが往復する", () => {
  const state = Object.assign(sampleState(), {
    logo: Object.assign(core.defaultLogoState(), {
      imageName: "logo.png", spinEnabled: true, spinSec: 0.6, spinDegrees: 360,
      spinEase: "in_out", spinPerspective: 0.25
    })
  });
  const project = core.buildProjectJSON(state);
  const loaded = core.parseProjectJSON(project);
  assert.strictEqual(loaded.logo.spinEnabled, true);
  assert.strictEqual(loaded.logo.spinSec, 0.6);
  assert.strictEqual(loaded.logo.spinDegrees, 360);
  assert.strictEqual(loaded.logo.spinEase, "in_out");
  assert.strictEqual(loaded.logo.spinPerspective, 0.25);
});

test("resolveSpinEase: 未知の値・未指定は既定(in_out)にフォールバックする", () => {
  assert.strictEqual(core.resolveSpinEase("linear"), "linear");
  assert.strictEqual(core.resolveSpinEase("in_out"), "in_out");
  assert.strictEqual(core.resolveSpinEase("bogus"), core.DEFAULT_SPIN_EASE);
  assert.strictEqual(core.resolveSpinEase(undefined), core.DEFAULT_SPIN_EASE);
});

/* ---- hashtags（ハッシュタグ表示） ---- */

test("defaultHashtagsState: 無効・render.pyのHASHTAGS_DEFAULTSと同じ既定値を返す", () => {
  const ht = core.defaultHashtagsState();
  assert.strictEqual(ht.enabled, false);
  assert.strictEqual(ht.text, "");
  assert.strictEqual(ht.position, core.DEFAULT_HASHTAGS_POSITION);
  assert.deepStrictEqual(ht.pos, [0.5, 0.5]);
  assert.strictEqual(ht.size, core.DEFAULT_HASHTAGS.size);
  assert.strictEqual(ht.color, core.DEFAULT_HASHTAGS.color);
  assert.strictEqual(ht.backing, core.DEFAULT_HASHTAGS.backing);
  assert.strictEqual(ht.always, core.DEFAULT_HASHTAGS.always);
});

test("buildProjectJSON: hashtagsが有効かつtextがあればhashtagsブロックを出力し、無効/未入力なら省略する", () => {
  const withHashtags = core.buildProjectJSON(Object.assign(sampleState(), {
    hashtags: Object.assign(core.defaultHashtagsState(), {
      enabled: true, text: "#京都 #祇園", position: "bottom", size: 0.03, color: "#ff0000", backing: "box"
    })
  }));
  assert.deepStrictEqual(withHashtags.hashtags, {
    text: "#京都 #祇園", position: "bottom", size: 0.03, color: "#ff0000", backing: "box", always: true
  });

  const disabled = core.buildProjectJSON(Object.assign(sampleState(), {
    hashtags: Object.assign(core.defaultHashtagsState(), { enabled: false, text: "#x" })
  }));
  assert.strictEqual(disabled.hashtags, undefined);

  const noText = core.buildProjectJSON(Object.assign(sampleState(), {
    hashtags: Object.assign(core.defaultHashtagsState(), { enabled: true, text: "" })
  }));
  assert.strictEqual(noText.hashtags, undefined);

  const noHashtagsAtAll = core.buildProjectJSON(sampleState());
  assert.strictEqual(noHashtagsAtAll.hashtags, undefined);
});

test("buildProjectJSON: hashtags.position=customのときだけposを出力し、フォントも既定以外のときだけ出力する", () => {
  const custom = core.buildProjectJSON(Object.assign(sampleState(), {
    hashtags: Object.assign(core.defaultHashtagsState(), {
      enabled: true, text: "#custom", position: "custom", pos: [0.3, 0.6]
    })
  }));
  assert.deepStrictEqual(custom.hashtags.pos, [0.3, 0.6]);

  const bottom = core.buildProjectJSON(Object.assign(sampleState(), {
    hashtags: Object.assign(core.defaultHashtagsState(), { enabled: true, text: "#bottom", position: "bottom" })
  }));
  assert.strictEqual(bottom.hashtags.pos, undefined);

  const defaultFont = core.buildProjectJSON(Object.assign(sampleState(), {
    hashtags: Object.assign(core.defaultHashtagsState(), { enabled: true, text: "#f" })
  }));
  assert.strictEqual(defaultFont.hashtags.font, undefined);

  const customFont = core.buildProjectJSON(Object.assign(sampleState(), {
    hashtags: Object.assign(core.defaultHashtagsState(), { enabled: true, text: "#f", fontKey: "notoserifjp" })
  }));
  assert.strictEqual(customFont.hashtags.font, "assets/fonts/NotoSerifJP-Bold.ttf");
});

test("parseProjectJSON: hashtagsブロックを読み込める。省略時はdefaultHashtagsState()相当になる", () => {
  const loaded = core.parseProjectJSON({
    version: 1, video: "v.mp4", freezes: [],
    hashtags: {
      text: "#京都 #祇園", position: "top", size: 0.04, color: "#00FF00", backing: "box", always: false
    }
  });
  assert.deepStrictEqual(loaded.hashtags, {
    enabled: true, text: "#京都 #祇園", position: "top", pos: [0.5, 0.5],
    size: 0.04, fontKey: core.DEFAULT_TITLE_FONT_KEY, color: "#00FF00", backing: "box", always: false
  });

  const loadedNone = core.parseProjectJSON({ version: 1, video: "v.mp4", freezes: [] });
  assert.deepStrictEqual(loadedNone.hashtags, core.defaultHashtagsState());
});

test("parseProjectJSON: hashtags.position/backingが未知の値なら既定にフォールバックする", () => {
  const loaded = core.parseProjectJSON({
    version: 1, video: "v.mp4", freezes: [],
    hashtags: { text: "#x", position: "center", backing: "shadow" }
  });
  assert.strictEqual(loaded.hashtags.position, core.DEFAULT_HASHTAGS_POSITION);
  assert.strictEqual(loaded.hashtags.backing, core.DEFAULT_HASHTAGS_BACKING);
});

test("buildProjectJSON/parseProjectJSON: hashtagsの全フィールドが往復する", () => {
  const state = Object.assign(sampleState(), {
    hashtags: {
      enabled: true, text: "#京都 #祇園 #ClubIRIS", position: "custom", pos: [0.2, 0.7],
      size: 0.033, fontKey: "delagothicone", color: "#123456", backing: "box", always: false
    }
  });
  const project = core.buildProjectJSON(state);
  const loaded = core.parseProjectJSON(project);
  assert.deepStrictEqual(loaded.hashtags, {
    enabled: true, text: "#京都 #祇園 #ClubIRIS", position: "custom", pos: [0.2, 0.7],
    size: 0.033, fontKey: "delagothicone", color: "#123456", backing: "box", always: false
  });
});

/* ---- auto_transparent_bg（透過の無いロゴ/透かし画像の背景色を自動で透明化） ---- */

function makeSolidImageData(w, h, color) {
  const data = new Uint8ClampedArray(w * h * 4);
  for (let i = 0; i < w * h; i++) {
    data[i * 4] = color[0]; data[i * 4 + 1] = color[1]; data[i * 4 + 2] = color[2]; data[i * 4 + 3] = 255;
  }
  return { data, width: w, height: h };
}

test("autoTransparentBgImageData: 透過が無い単色画像は全面透明化される（中身が無いので四隅も中心も同じ背景色）", () => {
  const img = makeSolidImageData(20, 20, [10, 10, 10]);
  const result = core.autoTransparentBgImageData(img);
  const centerIdx = (10 * 20 + 10) * 4;
  assert.ok(result.data[centerIdx + 3] < 10, "背景色しか無い画像は中心も透明になる");
});

test("autoTransparentBgImageData: 中央に背景と異なる色の内容があれば、そこだけ不透明のまま残る", () => {
  const img = makeSolidImageData(30, 30, [10, 10, 10]);
  // 中央8x8を背景と大きく異なる色に塗る（連結していない「内容」領域）
  for (let y = 11; y < 19; y++) {
    for (let x = 11; x < 19; x++) {
      const idx = (y * 30 + x) * 4;
      img.data[idx] = 250; img.data[idx + 1] = 250; img.data[idx + 2] = 250;
    }
  }
  const result = core.autoTransparentBgImageData(img);
  const cornerIdx = (1 * 30 + 1) * 4;
  const centerIdx = (15 * 30 + 15) * 4;
  assert.ok(result.data[cornerIdx + 3] < 10, "四隅（背景色）は透明化される");
  assert.ok(result.data[centerIdx + 3] > 200, "中央の内容（背景と異なる色）は不透明のまま残る");
});

test("autoTransparentBgImageData: 既に透過（アルファ<250の画素）を持つ画像はそのまま返す", () => {
  const img = makeSolidImageData(10, 10, [10, 10, 10]);
  img.data[3] = 0; // 1画素だけ透明にしておく
  const result = core.autoTransparentBgImageData(img);
  assert.strictEqual(result, img, "既に透過を持つ画像は同じ参照のまま返る（変更しない）");
});

test("autoTransparentBgImageData: RGB画素そのものは変更しない（アルファのみ変更）", () => {
  const img = makeSolidImageData(20, 20, [10, 10, 10]);
  const result = core.autoTransparentBgImageData(img);
  assert.strictEqual(result.data[0], 10);
  assert.strictEqual(result.data[1], 10);
  assert.strictEqual(result.data[2], 10);
});

test("buildProjectJSON: logo.auto_transparent_bg/watermark.auto_transparent_bgが既定でtrue、falseも明示できる", () => {
  const withDefaults = core.buildProjectJSON(Object.assign(sampleState(), {
    logo: { imageName: "logo.png", at: "end", background: "auto" },
    watermark: Object.assign(core.defaultWatermarkState(), { enabled: true, imageName: "watermark.png" })
  }));
  assert.strictEqual(withDefaults.logo.auto_transparent_bg, true);
  assert.strictEqual(withDefaults.watermark.auto_transparent_bg, true);

  const withFalse = core.buildProjectJSON(Object.assign(sampleState(), {
    logo: { imageName: "logo.png", at: "end", background: "auto", autoTransparentBg: false },
    watermark: Object.assign(core.defaultWatermarkState(),
      { enabled: true, imageName: "watermark.png", autoTransparentBg: false })
  }));
  assert.strictEqual(withFalse.logo.auto_transparent_bg, false);
  assert.strictEqual(withFalse.watermark.auto_transparent_bg, false);
});

test("parseProjectJSON: logo.auto_transparent_bg/watermark.auto_transparent_bgを読み込める。省略時は既定でtrue", () => {
  const loaded = core.parseProjectJSON({
    version: 1, video: "v.mp4", freezes: [],
    logo: { image: "logo.png", auto_transparent_bg: false },
    watermark: { image: "watermark.png", auto_transparent_bg: false }
  });
  assert.strictEqual(loaded.logo.autoTransparentBg, false);
  assert.strictEqual(loaded.watermark.autoTransparentBg, false);

  const loadedDefault = core.parseProjectJSON({
    version: 1, video: "v.mp4", freezes: [],
    logo: { image: "logo.png" },
    watermark: { image: "watermark.png" }
  });
  assert.strictEqual(loadedDefault.logo.autoTransparentBg, true);
  assert.strictEqual(loadedDefault.watermark.autoTransparentBg, true);
});

test("parseProjectJSON: 'shadow'キーも旧film_offsetも含まない旧JSONは、既定で影が有効に解決される（実機で影が出ない不具合の再発防止）", () => {
  const loaded = core.parseProjectJSON({
    version: 1, video: "x.mp4",
    style: { freeze_sec: 2.5, font: "assets/fonts/CustomFont.ttf" },
    freezes: [{ time: 1, name: "旧フリーズ", strokes: [] }]
  });
  assert.strictEqual(loaded.monoContrast, 1.0);
  assert.strictEqual(loaded.titleBounce, false);
  assert.strictEqual(loaded.shadowEnabled, true);
  assert.strictEqual(loaded.shadowColor, core.DEFAULT_SHADOW.color);
  assert.strictEqual(loaded.shadowAlpha, core.DEFAULT_SHADOW.alpha);
  assert.strictEqual(loaded.shadowDistanceRatio, core.DEFAULT_SHADOW.distance);
  assert.strictEqual(loaded.shadowDirection, core.DEFAULT_SHADOW.direction);
  assert.strictEqual(loaded.logo, null);
});

test("parseProjectJSON: \"shadow\": null は明示的な無効化として解決される", () => {
  const loaded = core.parseProjectJSON({
    version: 1, video: "x.mp4",
    style: { shadow: null },
    freezes: []
  });
  assert.strictEqual(loaded.shadowEnabled, false);
});

test("parseProjectJSON: \"shadow\": {\"enabled\": false} は明示的な無効化として解決される", () => {
  const loaded = core.parseProjectJSON({
    version: 1, video: "x.mp4",
    style: { shadow: { enabled: false } },
    freezes: []
  });
  assert.strictEqual(loaded.shadowEnabled, false);
});

test("parseProjectJSON: \"shadow\": {} （enabledキー無し）は有効のまま既定値で解決される", () => {
  const loaded = core.parseProjectJSON({
    version: 1, video: "x.mp4",
    style: { shadow: {} },
    freezes: []
  });
  assert.strictEqual(loaded.shadowEnabled, true);
  assert.strictEqual(loaded.shadowColor, core.DEFAULT_SHADOW.color);
});

test("parseProjectJSON: 旧film_offset/film_color/film_alpha（film_offsetが非ゼロ）はshadowへ後方互換で読み替える", () => {
  const loaded = core.parseProjectJSON({
    version: 1, video: "x.mp4",
    style: { film_offset: [0.02, 0.0], film_color: "#112233", film_alpha: 0.7 },
    freezes: []
  });
  assert.strictEqual(loaded.shadowEnabled, true);
  assert.strictEqual(loaded.shadowColor, "#112233");
  assert.strictEqual(loaded.shadowAlpha, 0.7);
  assert.strictEqual(loaded.shadowDistanceRatio, 0.02);
  assert.strictEqual(loaded.shadowDirection, "right");
  assert.strictEqual(loaded.shadowBlurRatio, 0);
});

test("parseProjectJSON: 旧film_offsetが負の値なら方向leftに読み替える", () => {
  const loaded = core.parseProjectJSON({
    version: 1, video: "x.mp4",
    style: { film_offset: [-0.015, 0.0], film_color: "#112233", film_alpha: 0.7 },
    freezes: []
  });
  assert.strictEqual(loaded.shadowEnabled, true);
  assert.strictEqual(loaded.shadowDistanceRatio, 0.015);
  assert.strictEqual(loaded.shadowDirection, "left");
});

test("parseProjectJSON: 旧film_offsetが[0,0]（既定のまま）で'shadow'キーも無ければ、既定で影が有効になる", () => {
  // film_offset=[0,0]は「後方互換の読み替えを起動しない」だけであり、"shadow"キー自体は
  // やはり無いので、新仕様の「省略時は既定で有効」が適用される。
  const loaded = core.parseProjectJSON({
    version: 1, video: "x.mp4",
    style: { film_offset: [0, 0], film_color: "#112233", film_alpha: 0.7 },
    freezes: []
  });
  assert.strictEqual(loaded.shadowEnabled, true);
  assert.strictEqual(loaded.shadowColor, core.DEFAULT_SHADOW.color);
});

test("DEFAULT_LOGO_DURATION_SEC は着地からの表示時間として2.2秒（以前は1.2秒。より重厚な表示に変更）", () => {
  assert.strictEqual(core.DEFAULT_LOGO_DURATION_SEC, 2.2);
});

test("logoLandingScale: タメ→縮小→セトルの3段階（render.pyのlogo_landing_scaleと同じ挙動）", () => {
  const holdBig = 0.15, shrink = 0.5, settle = 0.15, scaleFrom = 2.0;
  const minScale = core.LOGO_PREVIEW_MIN_SCALE;
  // タメ区間：scaleFromのまま
  assert.strictEqual(core.logoLandingScale(0, holdBig, shrink, settle, scaleFrom), scaleFrom);
  assert.strictEqual(core.logoLandingScale(holdBig - 0.001, holdBig, shrink, settle, scaleFrom), scaleFrom);
  // 縮小終わり（＝着地の瞬間）：最小サイズに到達
  assert.ok(Math.abs(core.logoLandingScale(holdBig + shrink, holdBig, shrink, settle, scaleFrom) - minScale) < 1e-9);
  // セトル終わり：100%
  assert.strictEqual(core.logoLandingScale(holdBig + shrink + settle, holdBig, shrink, settle, scaleFrom), 1);
});

test("DEFAULT_LOGO_START_WIDTH_RATIO/HOLD_BIG_SEC/SHRINK_SEC/SETTLE_SEC: render.pyのLOGO_*_DEFAULTと同じ既定値", () => {
  assert.strictEqual(core.DEFAULT_LOGO_START_WIDTH_RATIO, 1.6);
  assert.strictEqual(core.DEFAULT_LOGO_HOLD_BIG_SEC, 0.15);
  assert.strictEqual(core.DEFAULT_LOGO_SHRINK_SEC, 0.5);
  assert.strictEqual(core.DEFAULT_LOGO_SETTLE_SEC, 0.15);
  assert.strictEqual(core.LOGO_PREVIEW_WIDTH_RATIO, 0.62);
  assert.strictEqual(core.LOGO_PREVIEW_MIN_SCALE, 0.98);
});

test("buildProjectJSON/parseProjectJSON: shadowを含めて往復できる", () => {
  const state = sampleState();
  state.monoContrast = 1.4;
  state.titleBounce = true;
  state.shadowEnabled = true;
  state.shadowColor = "#FFC832";
  state.shadowAlpha = 0.6;
  state.shadowDistanceRatio = 0.04;
  state.shadowDirection = "right";
  state.shadowBlurRatio = 0.01;
  state.logo = { imageName: "logo.png", at: "end", background: "video", durationSec: 2.0, sfx: "shakin" };
  const project = core.buildProjectJSON(state);
  const loaded = core.parseProjectJSON(project);
  assert.strictEqual(loaded.monoContrast, 1.4);
  assert.strictEqual(loaded.titleBounce, true);
  assert.strictEqual(loaded.shadowEnabled, true);
  assert.strictEqual(loaded.shadowColor, "#FFC832");
  assert.strictEqual(loaded.shadowAlpha, 0.6);
  assert.strictEqual(loaded.shadowDistanceRatio, 0.04);
  assert.strictEqual(loaded.shadowDirection, "right");
  assert.strictEqual(loaded.shadowBlurRatio, 0.01);
  assert.strictEqual(loaded.logo.imageName, "logo.png");
  assert.strictEqual(loaded.logo.at, "end");
  assert.strictEqual(loaded.logo.background, "video");
  assert.strictEqual(loaded.logo.durationSec, 2.0);
});

/* ---- title_pos / title_size / title_align（フリーズごとのテロップ位置） ---- */

test("DEFAULT_STYLE: title_pos/title_size/title_alignの既定値は従来の固定位置と一致する", () => {
  assert.deepStrictEqual(core.DEFAULT_STYLE.title_pos, [0.5, 0.78]);
  assert.strictEqual(core.DEFAULT_STYLE.title_size, 0.06);
  assert.strictEqual(core.DEFAULT_STYLE.title_align, "center");
});

test("resolveTitleAlign: 未知の値は center にフォールバックする", () => {
  assert.strictEqual(core.resolveTitleAlign("left"), "left");
  assert.strictEqual(core.resolveTitleAlign("right"), "right");
  assert.strictEqual(core.resolveTitleAlign("center"), "center");
  assert.strictEqual(core.resolveTitleAlign("nonsense"), "center");
  assert.strictEqual(core.resolveTitleAlign(undefined), "center");
});

test("resolveTitleField: フリーズ側がnull/undefinedならstyle側の値にフォールバックする", () => {
  assert.strictEqual(core.resolveTitleField(null, 0.06), 0.06);
  assert.strictEqual(core.resolveTitleField(undefined, 0.06), 0.06);
  assert.strictEqual(core.resolveTitleField(0.1, 0.06), 0.1);
  assert.strictEqual(core.resolveTitleField(0, 0.06), 0); // 0はフォールバックしない
});

test("buildProjectJSON: 何も設定しなければstyle.title_pos/size/alignは後方互換の既定値を出力する", () => {
  const project = core.buildProjectJSON(sampleState());
  assert.deepStrictEqual(project.style.title_pos, [0.5, 0.78]);
  assert.strictEqual(project.style.title_size, 0.06);
  assert.strictEqual(project.style.title_align, "center");
});

test("buildProjectJSON: state.titlePos/titleSize/titleAlignをstyleに反映する", () => {
  const state = sampleState();
  state.titlePos = [0.2, 0.3];
  state.titleSize = 0.09;
  state.titleAlign = "left";
  const project = core.buildProjectJSON(state);
  assert.deepStrictEqual(project.style.title_pos, [0.2, 0.3]);
  assert.strictEqual(project.style.title_size, 0.09);
  assert.strictEqual(project.style.title_align, "left");
});

test("freezeToJSON: フリーズにtitlePos等が無ければキーを省略する（全体設定を継承）", () => {
  const project = core.buildProjectJSON(sampleState());
  const fz = project.freezes[0];
  assert.strictEqual("title_pos" in fz, false);
  assert.strictEqual("title_size" in fz, false);
  assert.strictEqual("title_align" in fz, false);
});

test("freezeToJSON: フリーズごとのtitlePos/titleSize/titleAlignの上書きを出力する", () => {
  const state = sampleState();
  state.freezes[0].titlePos = [0.1, 0.9];
  state.freezes[0].titleSize = 0.08;
  state.freezes[0].titleAlign = "right";
  const project = core.buildProjectJSON(state);
  const fz = project.freezes.filter(f => f.time === state.freezes[0].time)[0];
  assert.deepStrictEqual(fz.title_pos, [0.1, 0.9]);
  assert.strictEqual(fz.title_size, 0.08);
  assert.strictEqual(fz.title_align, "right");
});

test("parseProjectJSON: 旧JSON（title_pos等を含まない）は既定値[0.5,0.78]/0.06/centerに解決される（後方互換）", () => {
  const loaded = core.parseProjectJSON({
    version: 1, video: "x.mp4",
    freezes: [{ time: 0, name: "テスト" }]
  });
  assert.deepStrictEqual(loaded.titlePos, [0.5, 0.78]);
  assert.strictEqual(loaded.titleSize, 0.06);
  assert.strictEqual(loaded.titleAlign, "center");
  assert.strictEqual(loaded.freezes[0].titlePos, null);
  assert.strictEqual(loaded.freezes[0].titleSize, null);
  assert.strictEqual(loaded.freezes[0].titleAlign, null);
});

test("buildProjectJSON/parseProjectJSON: title_pos/title_size/title_alignを全体・フリーズ単位ともに往復できる", () => {
  const state = sampleState();
  state.titlePos = [0.25, 0.4];
  state.titleSize = 0.07;
  state.titleAlign = "right";
  state.freezes[0].titlePos = [0.05, 0.95];
  state.freezes[0].titleSize = 0.1;
  state.freezes[0].titleAlign = "left";
  const project = core.buildProjectJSON(state);
  const loaded = core.parseProjectJSON(project);
  assert.deepStrictEqual(loaded.titlePos, [0.25, 0.4]);
  assert.strictEqual(loaded.titleSize, 0.07);
  assert.strictEqual(loaded.titleAlign, "right");
  const loadedFz = loaded.freezes.filter(f => f.time === state.freezes[0].time)[0];
  assert.deepStrictEqual(loadedFz.titlePos, [0.05, 0.95]);
  assert.strictEqual(loadedFz.titleSize, 0.1);
  assert.strictEqual(loadedFz.titleAlign, "left");
  // 上書きの無い方のフリーズは null のまま（全体設定を継承）
  const otherFz = loaded.freezes.filter(f => f.time !== state.freezes[0].time)[0];
  assert.strictEqual(otherFz.titlePos, null);
});

/* ---- テロップの複数行対応（title.lines契約：normalizeTitleLines/titleLinesForJSON） ---- */

// normalizeTitleLines/DEFAULT_TITLE_LINEが返す行は、size/underlineに加えて
// color/anim/animSec/delaySec/sfx/sfxLibraryId/sfxAlignも持つ（いずれも未指定を表す既定値）。
// テストではこのヘルパーで「他はすべて既定値」の行を簡潔に組み立てる。
function defaultLine(overrides) {
  return Object.assign({
    text: "", size: 1.0, underline: false, color: "", align: "", anim: "", animSec: null, delaySec: 0,
    sfx: "", sfxLibraryId: null, sfxAlign: "start_at_landing", backing: ""
  }, overrides || {});
}

test("normalizeTitleLines: 文字列は1行（size=1.0・underline=false・他は未指定）として扱う", () => {
  assert.deepStrictEqual(core.normalizeTitleLines("山田 太郎"), [defaultLine({ text: "山田 太郎" })]);
});
test("normalizeTitleLines: 空・未指定は空文字1行になる（配列自体は空にならない）", () => {
  assert.deepStrictEqual(core.normalizeTitleLines(""), [defaultLine()]);
  assert.deepStrictEqual(core.normalizeTitleLines(undefined), [defaultLine()]);
  assert.deepStrictEqual(core.normalizeTitleLines(null), [defaultLine()]);
});
test("normalizeTitleLines: {lines:[...]}形式を行ごとのsize/underline込みで読み取る", () => {
  const lines = core.normalizeTitleLines({
    lines: [{ text: "山田 太郎", size: 1.0, underline: true }, { text: "エースストライカー", size: 0.55 }]
  });
  assert.deepStrictEqual(lines, [
    defaultLine({ text: "山田 太郎", underline: true }),
    defaultLine({ text: "エースストライカー", size: 0.55 })
  ]);
});
test("normalizeTitleLines: 行に文字列だけが混ざっていてもtext扱いで正規化される", () => {
  const lines = core.normalizeTitleLines({ lines: ["ただの文字列", { text: "オブジェクト行" }] });
  assert.deepStrictEqual(lines, [
    defaultLine({ text: "ただの文字列" }),
    defaultLine({ text: "オブジェクト行" })
  ]);
});
test("normalizeTitleLines: lines配列が空のオブジェクトは空文字1行にフォールバックする", () => {
  assert.deepStrictEqual(core.normalizeTitleLines({ lines: [] }), [defaultLine()]);
});
test("normalizeTitleLines: 行ごとのcolor/anim/anim_sec/delay_sec/sfxを読み取る（1行目のdelay_secは常に0に強制）", () => {
  const lines = core.normalizeTitleLines({
    lines: [
      { text: "1行目", color: "#e6c15c", anim: "slide_right", anim_sec: 0.4, delay_sec: 99 },
      { text: "2行目", color: "not-a-color", anim: "unknown-anim", delay_sec: 0.3, sfx: "shakin" }
    ]
  });
  assert.deepStrictEqual(lines, [
    defaultLine({ text: "1行目", color: "#E6C15C", anim: "slide_right", animSec: 0.4, delaySec: 0 }),
    defaultLine({ text: "2行目", delaySec: 0.3, sfx: "shakin" })
  ]);
});
test("normalizeTitleLines: 行ごとのalign（left/center/right）を読み取り、不明な値は既定（空文字＝全体継承）にフォールバックする", () => {
  const lines = core.normalizeTitleLines({
    lines: [
      { text: "左", align: "left" },
      { text: "右", align: "right" },
      { text: "不明", align: "top" }
    ]
  });
  assert.deepStrictEqual(lines, [
    defaultLine({ text: "左", align: "left" }),
    defaultLine({ text: "右", align: "right" }),
    defaultLine({ text: "不明" })
  ]);
});

test("titleLineToJSON: 行ごとのalignは値がある時だけ出力される", () => {
  const out = core.titleLinesForJSON([
    defaultLine({ text: "左寄せ行", align: "left" }),
    defaultLine({ text: "既定行" })
  ]);
  assert.deepStrictEqual(out, {
    lines: [
      { text: "左寄せ行", size: 1, underline: false, align: "left" },
      { text: "既定行", size: 1, underline: false }
    ]
  });
});

test("titleLinesToPlainText: 空でない行のtextをスペース区切りで1行にまとめる", () => {
  assert.strictEqual(core.titleLinesToPlainText([
    { text: "山田 太郎", size: 1.0, underline: true }, { text: "エースストライカー", size: 0.55, underline: false }
  ]), "山田 太郎 エースストライカー");
});
test("titleLinesToPlainText: 空文字の行は無視される", () => {
  assert.strictEqual(core.titleLinesToPlainText([
    { text: "", size: 1.0, underline: false }, { text: "本文", size: 1.0, underline: false }
  ]), "本文");
});

test("titleLinesAllDefault: 1行・サイズ1.0・アンダーラインなし・色/アニメ/効果音とも未指定ならtrue", () => {
  assert.strictEqual(core.titleLinesAllDefault([defaultLine({ text: "山田 太郎" })]), true);
});
test("titleLinesAllDefault: 複数行・サイズ違い・アンダーラインあり・色/アニメ指定のいずれでもfalse", () => {
  assert.strictEqual(core.titleLinesAllDefault([defaultLine({ text: "A" }), defaultLine({ text: "B" })]), false);
  assert.strictEqual(core.titleLinesAllDefault([defaultLine({ text: "山田", size: 0.8 })]), false);
  assert.strictEqual(core.titleLinesAllDefault([defaultLine({ text: "山田", underline: true })]), false);
  assert.strictEqual(core.titleLinesAllDefault([defaultLine({ text: "山田", color: "#FF0000" })]), false);
  assert.strictEqual(core.titleLinesAllDefault([defaultLine({ text: "山田", anim: "fade" })]), false);
});

test("titleLinesForJSON: 既定の1行はプレーンな文字列として出力する（JSON後方互換）", () => {
  assert.strictEqual(core.titleLinesForJSON([defaultLine({ text: "山田 太郎" })]), "山田 太郎");
});
test("titleLinesForJSON: 複数行は{lines:[...]}のオブジェクト形式で出力する", () => {
  const out = core.titleLinesForJSON([
    defaultLine({ text: "山田 太郎", underline: true }),
    defaultLine({ text: "エースストライカー", size: 0.55 })
  ]);
  assert.deepStrictEqual(out, {
    lines: [
      { text: "山田 太郎", size: 1, underline: true },
      { text: "エースストライカー", size: 0.55, underline: false }
    ]
  });
});
test("titleLinesForJSON: 1行でもサイズやアンダーラインを既定から変えていればオブジェクト形式になる", () => {
  const out = core.titleLinesForJSON([defaultLine({ text: "山田 太郎", size: 1.3 })]);
  assert.deepStrictEqual(out, { lines: [{ text: "山田 太郎", size: 1.3, underline: false }] });
});
test("titleLinesForJSON: 行ごとのcolor/anim/anim_sec/delay_secは値がある時だけ出力される（1行目はdelay_secを出力しない）", () => {
  const out = core.titleLinesForJSON([
    defaultLine({ text: "1行目", color: "#E6C15C", anim: "slide_right", animSec: 0.4, delaySec: 5 }),
    defaultLine({ text: "2行目", anim: "slide_left", delaySec: 0.3 })
  ]);
  assert.deepStrictEqual(out, {
    lines: [
      { text: "1行目", size: 1, underline: false, color: "#E6C15C", anim: "slide_right", anim_sec: 0.4 },
      { text: "2行目", size: 1, underline: false, anim: "slide_left", delay_sec: 0.3 }
    ]
  });
});
test("titleLinesForJSON: 行ごとのsfxプリセット/ライブラリファイルが出力される", () => {
  const sfxLibrary = [{ id: "lib1", name: "custom.mp3" }];
  const out = core.titleLinesForJSON([
    defaultLine({ text: "1行目", sfx: "shakin" }),
    defaultLine({ text: "2行目", sfxLibraryId: "lib1", sfxAlign: "end_at_landing" })
  ], sfxLibrary);
  assert.deepStrictEqual(out.lines[0].sfx, "shakin");
  assert.deepStrictEqual(out.lines[1].sfx, {
    file: core.sfxLibraryAssetPath("lib1", "custom.mp3"), align: "end_at_landing"
  });
});

/* ---- 行ごとのテロップ出現アクション（isValidHexColor/autoOutlineColor/resolveLineAnimParams/computeLineFrameState） ---- */

test("isValidHexColor: #RRGGBB形式のみtrue", () => {
  assert.strictEqual(core.isValidHexColor("#E6C15C"), true);
  assert.strictEqual(core.isValidHexColor("#fff"), false);
  assert.strictEqual(core.isValidHexColor("E6C15C"), false);
  assert.strictEqual(core.isValidHexColor(""), false);
  assert.strictEqual(core.isValidHexColor(null), false);
});

test("normalizeDecimalInput: 半角の整数/小数はそのまま数値になる", () => {
  assert.strictEqual(core.normalizeDecimalInput("0.3"), 0.3);
  assert.strictEqual(core.normalizeDecimalInput("2"), 2);
  assert.strictEqual(core.normalizeDecimalInput("0"), 0);
});
test("normalizeDecimalInput: 空欄・空白のみはnull（既定値を使う指示として扱う）", () => {
  assert.strictEqual(core.normalizeDecimalInput(""), null);
  assert.strictEqual(core.normalizeDecimalInput("   "), null);
  assert.strictEqual(core.normalizeDecimalInput(null), null);
  assert.strictEqual(core.normalizeDecimalInput(undefined), null);
});
test("normalizeDecimalInput: 全角数字・全角ピリオド/句点・全角マイナスを半角に正規化してから解釈する", () => {
  assert.strictEqual(core.normalizeDecimalInput("０.３"), 0.3);
  assert.strictEqual(core.normalizeDecimalInput("０．３"), 0.3);
  assert.strictEqual(core.normalizeDecimalInput("０。３"), 0.3);
  assert.strictEqual(core.normalizeDecimalInput("１２"), 12);
  assert.strictEqual(core.normalizeDecimalInput("－０.５"), -0.5);
});
test("normalizeDecimalInput: 数値として解釈できない文字列はnull（0に化けて意図しない値を書き込まない）", () => {
  assert.strictEqual(core.normalizeDecimalInput("abc"), null);
  assert.strictEqual(core.normalizeDecimalInput("0.3.5"), null);
});

test("autoOutlineColor: 明るい色には黒、暗い色には白（render.pyのauto_outline_rgbと同じ閾値140）", () => {
  assert.strictEqual(core.autoOutlineColor("#FFFFFF"), "#000000");
  assert.strictEqual(core.autoOutlineColor("#E6C15C"), "#000000");
  assert.strictEqual(core.autoOutlineColor("#000000"), "#FFFFFF");
  assert.strictEqual(core.autoOutlineColor("#FF3B30"), "#FFFFFF");
});

test("resolveLineAnimParams: anim未指定はtitleBounceに従いbounce/fade、時間は常にTELOP_FADE_SEC(0.15秒)", () => {
  assert.deepStrictEqual(core.resolveLineAnimParams({ anim: "" }, true), { anim: "bounce", animSec: core.TELOP_FADE_SEC });
  assert.deepStrictEqual(core.resolveLineAnimParams({ anim: "" }, false), { anim: "fade", animSec: core.TELOP_FADE_SEC });
});
test("resolveLineAnimParams: anim明示時はanim_sec省略ならbounce=0.15秒・それ以外=0.25秒が既定", () => {
  assert.deepStrictEqual(core.resolveLineAnimParams({ anim: "bounce" }, false), { anim: "bounce", animSec: core.TELOP_FADE_SEC });
  assert.deepStrictEqual(core.resolveLineAnimParams({ anim: "slide_right" }, false), { anim: "slide_right", animSec: core.TITLE_LINE_ANIM_SEC_DEFAULT });
  assert.deepStrictEqual(core.resolveLineAnimParams({ anim: "fade", animSec: 0.4 }, false), { anim: "fade", animSec: 0.4 });
});

test("computeLineFrameState: delay_sec経過前はfade=0（非表示）", () => {
  const state = core.computeLineFrameState("fade", 0.25, 0.3, 0.1, 1080, 1920);
  assert.strictEqual(state.fade, 0);
});
test("computeLineFrameState: slide_rightは画面右外(+W)から0へ、slide_leftは-Wから0へ、Ease-Out Cubicで近づく", () => {
  const right = core.computeLineFrameState("slide_right", 0.5, 0, 0.25, 1000, 2000);
  assert.strictEqual(right.fade, 1);
  assert.ok(right.tx > 0 && right.tx < 1000, "0<tx<W の範囲で中間地点にいる: " + right.tx);
  assert.strictEqual(right.ty, 0);
  const left = core.computeLineFrameState("slide_left", 0.5, 0, 0.25, 1000, 2000);
  assert.ok(left.tx < 0 && left.tx > -1000);
  const done = core.computeLineFrameState("slide_right", 0.5, 0, 0.5, 1000, 2000);
  assert.strictEqual(done.tx, 0, "アニメ完了後は最終位置(tx=0)に到達する");
});
test("computeLineFrameState: noneはdelay経過後ただちに完全表示・変形なし", () => {
  const state = core.computeLineFrameState("none", 0.25, 0, 0.001, 1000, 2000);
  assert.deepStrictEqual(state, { fade: 1, scale: 1, tx: 0, ty: 0 });
});
test("computeLineFrameState: bounceはfade同様に進行し、完了前はtelopBounceScaleでスケールが1より大きい", () => {
  const state = core.computeLineFrameState("bounce", 0.15, 0, 0.05, 1000, 2000);
  assert.ok(state.fade > 0 && state.fade < 1);
  assert.ok(state.scale > 1, "バウンス中は1.3→1.0のスケールなので1より大きい: " + state.scale);
});

test("freezeToJSON経由：titleLinesが無いフリーズは従来どおりnameが文字列になる（完全後方互換）", () => {
  const state = sampleState();
  state.freezes[0].name = "従来どおりのフリーズ名";
  delete state.freezes[0].titleLines;
  const project = core.buildProjectJSON(state);
  const fz = project.freezes.filter(f => f.name === "従来どおりのフリーズ名")[0];
  assert.strictEqual(fz.name, "従来どおりのフリーズ名");
});
test("freezeToJSON経由：titleLinesがある複数行フリーズはnameが{lines:[...]}になる", () => {
  const state = sampleState();
  state.freezes[0].name = "無視される（titleLinesが優先）";
  state.freezes[0].titleLines = [
    { text: "山田 太郎", size: 1.0, underline: true },
    { text: "エースストライカー", size: 0.55, underline: false }
  ];
  const project = core.buildProjectJSON(state);
  const fz = project.freezes.filter(f => f.time === state.freezes[0].time)[0];
  assert.deepStrictEqual(fz.name, {
    lines: [
      { text: "山田 太郎", size: 1, underline: true },
      { text: "エースストライカー", size: 0.55, underline: false }
    ]
  });
});
test("buildProjectJSON/parseProjectJSON: 複数行タイトルを往復できる（titleLines・nameとも復元される）", () => {
  const state = sampleState();
  state.freezes[0].titleLines = [
    { text: "山田 太郎", size: 1.0, underline: true },
    { text: "エースストライカー", size: 0.55, underline: false }
  ];
  const project = core.buildProjectJSON(state);
  const loaded = core.parseProjectJSON(project);
  const loadedFz = loaded.freezes.filter(f => f.time === state.freezes[0].time)[0];
  assert.deepStrictEqual(loadedFz.titleLines.map(l => ({ text: l.text, size: l.size, underline: l.underline })), [
    { text: "山田 太郎", size: 1.0, underline: true },
    { text: "エースストライカー", size: 0.55, underline: false }
  ]);
  assert.strictEqual(loadedFz.name, "山田 太郎 エースストライカー");
});

/* ---- テロップのフォント選択（TITLE_FONT_CHOICES／resolveTitleFontChoice／titleFontKeyFromPath） ---- */

test("TITLE_FONT_CHOICES: 先頭は既定（pathがnull）、それ以外は5種すべてassets/fonts/配下のパスを持つ", () => {
  assert.strictEqual(core.TITLE_FONT_CHOICES[0].key, "default");
  assert.strictEqual(core.TITLE_FONT_CHOICES[0].path, null);
  assert.strictEqual(core.TITLE_FONT_CHOICES.length, 6);
  core.TITLE_FONT_CHOICES.slice(1).forEach(choice => {
    assert.ok(choice.path && choice.path.indexOf("assets/fonts/") === 0, choice.key + "のpathがassets/fonts/配下でない");
    assert.ok(choice.cssFamily, choice.key + "にcssFamilyが無い");
  });
});
test("resolveTitleFontChoice: 未知のキーは既定にフォールバックする", () => {
  assert.strictEqual(core.resolveTitleFontChoice("nonexistent-key").key, "default");
  assert.strictEqual(core.resolveTitleFontChoice(undefined).key, "default");
});
test("resolveTitleFontChoice: 既知のキーはそのまま解決する", () => {
  assert.strictEqual(core.resolveTitleFontChoice("notoserifjp").cssFamily, "SpotlightTitleNotoSerifJP");
});
test("titleFontKeyFromPath: 未指定(null/undefined)は既定キーになる", () => {
  assert.strictEqual(core.titleFontKeyFromPath(null), core.DEFAULT_TITLE_FONT_KEY);
  assert.strictEqual(core.titleFontKeyFromPath(undefined), core.DEFAULT_TITLE_FONT_KEY);
});
test("titleFontKeyFromPath: 既知のpathは対応するキーへ逆引きできる", () => {
  assert.strictEqual(core.titleFontKeyFromPath("assets/fonts/DelaGothicOne-Regular.ttf"), "delagothicone");
});
test("titleFontKeyFromPath: 未知のpathは既定キーにフォールバックする", () => {
  assert.strictEqual(core.titleFontKeyFromPath("assets/fonts/存在しないフォント.ttf"), core.DEFAULT_TITLE_FONT_KEY);
});

/* ---- measureTitleLines（複数行ブロックの実測。ctxはNode用のフェイクを使う） ---- */

function makeFakeMeasureCtx() {
  var fontStr = "";
  return {
    save: function () {},
    restore: function () {},
    get font() { return fontStr; },
    set font(v) { fontStr = v; },
    set textBaseline(v) {},
    measureText: function (text) {
      var m = /([\d.]+)px/.exec(fontStr);
      var sizePx = m ? Number(m[1]) : 10;
      return {
        width: (text || "").length * sizePx * 0.6,
        fontBoundingBoxAscent: sizePx * 0.9,
        fontBoundingBoxDescent: sizePx * 0.2
      };
    }
  };
}

test("measureTitleLines: 1行なら高さはascent+descent、幅は文字数に比例する", () => {
  const ctx = makeFakeMeasureCtx();
  const m = core.measureTitleLines(ctx, [{ text: "ABCD", size: 1.0, underline: false }], 40, "default");
  assert.strictEqual(m.lines.length, 1);
  assert.strictEqual(m.lines[0].sizePx, 40);
  assert.ok(Math.abs(m.height - 40 * 1.1) < 1e-6);
  assert.ok(Math.abs(m.width - 4 * 40 * 0.6) < 1e-6);
});
test("measureTitleLines: 複数行は高さが行の合計になり、幅は最も広い行に揃う", () => {
  const ctx = makeFakeMeasureCtx();
  const m = core.measureTitleLines(ctx, [
    { text: "山田太郎", size: 1.0, underline: true },
    { text: "エース", size: 0.55, underline: false }
  ], 40, "default");
  assert.strictEqual(m.lines.length, 2);
  assert.strictEqual(m.lines[0].sizePx, 40);
  assert.strictEqual(m.lines[1].sizePx, 22); // round(40*0.55)
  const expectedHeight = (40 * 1.1) + (22 * 1.1);
  assert.ok(Math.abs(m.height - expectedHeight) < 1e-6);
  // 1行目（4文字・size40）の方が2行目（3文字・size22）より幅が広い
  assert.ok(m.width > 22 * 0.6 * 3);
  assert.ok(Math.abs(m.width - 4 * 40 * 0.6) < 1e-6);
});
test("measureTitleLines: sizeが極端に小さくても最低8pxを下回らない", () => {
  const ctx = makeFakeMeasureCtx();
  const m = core.measureTitleLines(ctx, [{ text: "A", size: 0.01, underline: false }], 40, "default");
  assert.strictEqual(m.lines[0].sizePx, 8);
});

/* ---- buildProjectJSON/parseProjectJSON: テロップのフォント選択（title_font/title_font_jp）の往復 ---- */

test("buildProjectJSON: フォントが既定のままなら title_font/title_font_jp は出力されない（完全後方互換）", () => {
  const state = sampleState();
  const project = core.buildProjectJSON(state);
  assert.strictEqual(project.style.title_font, undefined);
  assert.strictEqual(project.style.title_font_jp, undefined);
});
test("buildProjectJSON/parseProjectJSON: 全体既定のフォントを往復できる", () => {
  const state = sampleState();
  state.titleFontKey = "zenkakugothicnew";
  const project = core.buildProjectJSON(state);
  assert.strictEqual(project.style.title_font, "assets/fonts/ZenKakuGothicNew-Bold.ttf");
  assert.strictEqual(project.style.title_font_jp, "assets/fonts/ZenKakuGothicNew-Bold.ttf");
  const loaded = core.parseProjectJSON(project);
  assert.strictEqual(loaded.titleFontKey, "zenkakugothicnew");
});
test("buildProjectJSON/parseProjectJSON: フリーズ単位のフォント上書きを往復できる（上書きの無い方はnullのまま）", () => {
  const state = sampleState();
  state.freezes[0].titleFontKey = "shipporimincho";
  const project = core.buildProjectJSON(state);
  const fz = project.freezes.filter(f => f.time === state.freezes[0].time)[0];
  assert.strictEqual(fz.title_font, "assets/fonts/ShipporiMincho-Bold.ttf");
  const loaded = core.parseProjectJSON(project);
  const loadedFz = loaded.freezes.filter(f => f.time === state.freezes[0].time)[0];
  assert.strictEqual(loadedFz.titleFontKey, "shipporimincho");
  const otherFz = loaded.freezes.filter(f => f.time !== state.freezes[0].time)[0];
  assert.strictEqual(otherFz.titleFontKey, null);
});

/* ---- テロップの外接矩形計算・画面端クランプ（ドラッグUI・render.pyと同じ考え方） ---- */

test("telopBoxFromAnchor: centerはアンカーを中心に左右へ半分ずつ広がる", () => {
  const box = core.telopBoxFromAnchor(100, 50, 40, 20, "center");
  assert.deepStrictEqual(box, { left: 80, top: 40, right: 120, bottom: 60 });
});
test("telopBoxFromAnchor: leftはアンカーが左端になる", () => {
  const box = core.telopBoxFromAnchor(100, 50, 40, 20, "left");
  assert.strictEqual(box.left, 100);
  assert.strictEqual(box.right, 140);
});
test("telopBoxFromAnchor: rightはアンカーが右端になる", () => {
  const box = core.telopBoxFromAnchor(100, 50, 40, 20, "right");
  assert.strictEqual(box.left, 60);
  assert.strictEqual(box.right, 100);
});

test("clampBoxToCanvas: はみ出していなければ移動量は0", () => {
  const d = core.clampBoxToCanvas({ left: 10, top: 10, right: 90, bottom: 90 }, 100, 100);
  assert.deepStrictEqual(d, { dx: 0, dy: 0 });
});
test("clampBoxToCanvas: 左右上下にはみ出す場合は内側に寄せる移動量を返す", () => {
  const d1 = core.clampBoxToCanvas({ left: -20, top: 10, right: 30, bottom: 40 }, 100, 100);
  assert.strictEqual(d1.dx, 20);
  const d2 = core.clampBoxToCanvas({ left: 70, top: 10, right: 120, bottom: 40 }, 100, 100);
  assert.strictEqual(d2.dx, -20);
});

test("clampTitlePosRatio: 画面内に収まっていれば位置は変わらない", () => {
  const pos = core.clampTitlePosRatio([0.5, 0.5], 40, 20, "center", 200, 100);
  assert.strictEqual(pos[0], 0.5);
  assert.strictEqual(pos[1], 0.5);
});
test("clampTitlePosRatio: 画面外にはみ出す位置は端で止まる（左上端ぎりぎりに寄せる）", () => {
  const pos = core.clampTitlePosRatio([0, 0], 40, 20, "center", 200, 100);
  // center align: left=x-20 >= 0 => x>=20, top=y-10 >= 0 => y>=10
  assert.strictEqual(pos[0], 20 / 200);
  assert.strictEqual(pos[1], 10 / 100);
});
test("clampTitlePosRatio: 右下にはみ出す位置も端で止まる", () => {
  const pos = core.clampTitlePosRatio([1, 1], 40, 20, "center", 200, 100);
  assert.strictEqual(pos[0], (200 - 20) / 200);
  assert.strictEqual(pos[1], (100 - 10) / 100);
});
test("clampTitlePosRatio: left寄せでは右側の余白ぶんだけ余計にクランプされる", () => {
  const pos = core.clampTitlePosRatio([1, 0.5], 40, 20, "left", 200, 100);
  // left align: right = x+40 <= 200 => x <= 160
  assert.strictEqual(pos[0], 160 / 200);
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

/* ---- estimateForegroundMaskCoarse（切り抜き結果の簡易プレビュー用、GrabCut風の粗い前景推定） ---- */

function makeSyntheticFrame(W, H, bgColor, subjectColor, subjectRect) {
  const rgba = new Uint8ClampedArray(W * H * 4);
  for (let y = 0; y < H; y++) {
    for (let x = 0; x < W; x++) {
      const i = (y * W + x) * 4;
      const inSubject = x >= subjectRect.x0 && x < subjectRect.x1 && y >= subjectRect.y0 && y < subjectRect.y1;
      const c = inSubject ? subjectColor : bgColor;
      rgba[i] = c[0]; rgba[i + 1] = c[1]; rgba[i + 2] = c[2]; rgba[i + 3] = 255;
    }
  }
  return rgba;
}

test("estimateForegroundMaskCoarse: 一様な単色背景の中央に別色の矩形があれば、中央は前景・四隅は背景と判定される", () => {
  const W = 60, H = 100;
  const subjectRect = { x0: 20, x1: 40, y0: 30, y1: 80 };
  const rgba = makeSyntheticFrame(W, H, [30, 30, 200], [230, 200, 60], subjectRect);
  const mask = core.estimateForegroundMaskCoarse(rgba, W, H);
  assert.strictEqual(mask.length, W * H);
  const at = (x, y) => mask[y * W + x];
  assert.strictEqual(at(30, 55), 255, "被写体矩形の中心は前景と判定される");
  assert.strictEqual(at(2, 2), 0, "左上の隅は背景と判定される");
  assert.strictEqual(at(W - 3, 2), 0, "右上の隅は背景と判定される");
  assert.strictEqual(at(2, H - 3), 0, "左下の隅は背景と判定される");
  assert.strictEqual(at(W - 3, H - 3), 0, "右下の隅は背景と判定される");
});

test("estimateForegroundMaskCoarse: 完全な単色画像（被写体なし）でもクラッシュせず、外周は背景のまま", () => {
  const W = 40, H = 60;
  const rgba = makeSyntheticFrame(W, H, [100, 100, 100], [100, 100, 100], { x0: 0, x1: 0, y0: 0, y1: 0 });
  const mask = core.estimateForegroundMaskCoarse(rgba, W, H);
  assert.strictEqual(mask.length, W * H);
  assert.strictEqual(mask[2 * W + 2], 0, "単色画像では外周（背景に固定される領域）は背景のまま");
});

test("estimateForegroundMaskCoarse: 幅・高さが0以下なら空配列を返す（クラッシュしない）", () => {
  assert.strictEqual(core.estimateForegroundMaskCoarse(new Uint8ClampedArray(0), 0, 0).length, 0);
  assert.strictEqual(core.estimateForegroundMaskCoarse(new Uint8ClampedArray(0), 10, 0).length, 0);
});

test("estimateForegroundMaskCoarse: 解析用の解像度を引き上げても（長辺260px相当）中央は前景・四隅は背景のまま", () => {
  const W = 180, H = 260;
  const subjectRect = { x0: 60, x1: 120, y0: 60, y1: 220 };
  const rgba = makeSyntheticFrame(W, H, [40, 60, 90], [210, 180, 40], subjectRect);
  const mask = core.estimateForegroundMaskCoarse(rgba, W, H);
  assert.strictEqual(mask.length, W * H);
  const at = (x, y) => mask[y * W + x];
  assert.strictEqual(at(90, 140), 255, "被写体矩形の中心は前景と判定される");
  assert.strictEqual(at(3, 3), 0, "左上の隅は背景と判定される");
  assert.strictEqual(at(W - 4, H - 4), 0, "右下の隅は背景と判定される");
});

function addNoise(rgba, W, H, amplitude, seed) {
  // 決定論的な疑似乱数（テストを再現可能にするため、Math.randomは使わない）
  let s = seed;
  function next() { s = (s * 1103515245 + 12345) & 0x7fffffff; return s / 0x7fffffff; }
  const out = new Uint8ClampedArray(rgba.length);
  for (let i = 0; i < W * H; i++) {
    const o = i * 4;
    for (let c = 0; c < 3; c++) {
      out[o + c] = rgba[o + c] + Math.round((next() - 0.5) * 2 * amplitude);
    }
    out[o + 3] = 255;
  }
  return out;
}

test("estimateForegroundMaskCoarse: 背景に多少のノイズ（撮影環境の粒状感相当）があっても、深い内側の判定は揺らがない", () => {
  const W = 60, H = 100;
  const subjectRect = { x0: 20, x1: 40, y0: 30, y1: 80 };
  const clean = makeSyntheticFrame(W, H, [30, 30, 200], [230, 200, 60], subjectRect);
  const noisy = addNoise(clean, W, H, 10, 42);
  const mask = core.estimateForegroundMaskCoarse(noisy, W, H);
  const at = (x, y) => mask[y * W + x];
  assert.strictEqual(at(30, 55), 255, "ノイズがあっても被写体矩形の中心は前景と判定される");
  assert.strictEqual(at(2, 2), 0, "ノイズがあっても左上の隅は背景と判定される");
  assert.strictEqual(at(W - 3, H - 3), 0, "ノイズがあっても右下の隅は背景と判定される");
});

/* ---- validateProjectForConfirmation（「動画を作る」実行前の内容チェック） ---- */
function makeFreeze(overrides) {
  return Object.assign({
    time: 1, name: "テスト", maskMode: "brush", strokes: [{ width: 0.1, points: [[0, 0], [1, 1]] }]
  }, overrides);
}
function makeLogo(overrides) {
  return Object.assign({ imageFile: null, imageName: "", at: "end" }, overrides);
}

test("validateProjectForConfirmation: 問題が無ければ警告は空", () => {
  const warnings = core.validateProjectForConfirmation({ freezes: [makeFreeze()], logo: makeLogo() });
  assert.deepStrictEqual(warnings, []);
});

test("validateProjectForConfirmation: 名前が空のフリーズは警告される", () => {
  const warnings = core.validateProjectForConfirmation({ freezes: [makeFreeze({ name: "" })], logo: makeLogo() });
  assert.strictEqual(warnings.length, 1);
  assert.ok(warnings[0].message.indexOf("名前が入力されていません") >= 0, warnings[0].message);
  assert.strictEqual(warnings[0].freezeIndex, 0);
});

test("validateProjectForConfirmation: 名前が空白のみのフリーズも警告される", () => {
  const warnings = core.validateProjectForConfirmation({ freezes: [makeFreeze({ name: "   " })], logo: makeLogo() });
  assert.strictEqual(warnings.length, 1);
});

test("validateProjectForConfirmation: ストロークが無いbrushフリーズは警告される", () => {
  const warnings = core.validateProjectForConfirmation({
    freezes: [makeFreeze({ maskMode: "brush", strokes: [] })], logo: makeLogo()
  });
  assert.strictEqual(warnings.length, 1);
  assert.ok(warnings[0].message.indexOf("ブラシで何も塗られていません") >= 0, warnings[0].message);
});

test("validateProjectForConfirmation: ストロークが無くてもauto/auto+brushフリーズは警告されない（自動切り抜きのみで成立するため）", () => {
  const warnings = core.validateProjectForConfirmation({
    freezes: [makeFreeze({ maskMode: "auto", strokes: [] }), makeFreeze({ maskMode: "auto+brush", strokes: [] })],
    logo: makeLogo()
  });
  assert.deepStrictEqual(warnings, []);
});

test("validateProjectForConfirmation: 未選択のロゴでatが既定値(end)から変更されていれば警告される", () => {
  const warnings = core.validateProjectForConfirmation({
    freezes: [], logo: makeLogo({ imageName: "", at: "last_freeze" })
  });
  assert.strictEqual(warnings.length, 1);
  assert.ok(warnings[0].message.indexOf("ロゴ画像が選択されていない") >= 0, warnings[0].message);
  assert.strictEqual(warnings[0].freezeIndex, undefined);
});

test("validateProjectForConfirmation: ロゴ画像が選択済みならatを変更していても警告されない", () => {
  const warnings = core.validateProjectForConfirmation({
    freezes: [], logo: makeLogo({ imageName: "logo.png", at: "last_freeze" })
  });
  assert.deepStrictEqual(warnings, []);
});

test("validateProjectForConfirmation: ロゴ未選択でatも既定値のままなら警告されない", () => {
  const warnings = core.validateProjectForConfirmation({ freezes: [], logo: makeLogo({ imageName: "", at: "end" }) });
  assert.deepStrictEqual(warnings, []);
});

test("validateProjectForConfirmation: 複数のフリーズ・複数の問題が同時に一覧化される", () => {
  const warnings = core.validateProjectForConfirmation({
    freezes: [
      makeFreeze({ time: 1, name: "" }),
      makeFreeze({ time: 2, maskMode: "brush", strokes: [] }),
      makeFreeze({ time: 0.5, name: "", maskMode: "brush", strokes: [] })
    ],
    logo: makeLogo({ imageName: "", at: "last_freeze" })
  });
  // 時刻でソートされるため、0.5秒のフリーズが先頭（インデックス0）になる。
  // 内訳: 1秒フリーズ(名前)+2秒フリーズ(ストローク)+0.5秒フリーズ(名前・ストローク)+ロゴ = 5件
  assert.strictEqual(warnings.length, 5);
  assert.strictEqual(warnings.filter((w) => w.freezeIndex === 0).length, 2, "0.5秒のフリーズは名前・ストローク両方で警告される");
});

/* ---- makeJobTag ---- */
test("makeJobTag: job-YYYYMMDD-HHMMSS 形式（UTC基準）になる", () => {
  const d = new Date(Date.UTC(2026, 7, 29, 15, 30, 5)); // 2026-08-29T15:30:05Z
  assert.strictEqual(core.makeJobTag(d), "job-20260829-153005");
});
test("makeJobTag: 各要素が2桁ゼロ埋めされる", () => {
  const d = new Date(Date.UTC(2026, 0, 5, 3, 4, 9));
  assert.strictEqual(core.makeJobTag(d), "job-20260105-030409");
});

/* ---- formatJobTimestamp / makeConfirmTag ---- */
test("formatJobTimestamp: YYYYMMDD-HHMMSS 形式（UTC基準）になる", () => {
  const d = new Date(Date.UTC(2026, 7, 29, 15, 30, 5));
  assert.strictEqual(core.formatJobTimestamp(d), "20260829-153005");
});
test("makeConfirmTag: job-confirm-<timestamp>-<suffix> 形式になり、cleanup.ymlのjob-*対象prefixを持つ", () => {
  const d = new Date(Date.UTC(2026, 7, 29, 15, 30, 5));
  assert.strictEqual(core.makeConfirmTag(d, "ab12"), "job-confirm-20260829-153005-ab12");
});
test("makeConfirmTag: サフィックス省略時はランダムな短い文字列が付く（毎回変わる）", () => {
  const d = new Date(Date.UTC(2026, 7, 29, 15, 30, 5));
  const a = core.makeConfirmTag(d);
  const b = core.makeConfirmTag(d);
  assert.match(a, /^job-confirm-20260829-153005-[a-z0-9]+$/);
  assert.notStrictEqual(a, b);
});

/* ---- buildConfirmParams / confirmedAlphaAssetName / confirmedAlphaCachePath ---- */
test("buildConfirmParams: extract_and_cache.pyが読むparams.jsonの形にする", () => {
  const p = core.buildConfirmParams("birefnet-portrait", null, false, 3.4, 540.4, 960.6);
  assert.deepStrictEqual(p, {
    model: "birefnet-portrait",
    refine: null,
    decontaminate: false,
    time: 3.4,
    output_width: 540,
    output_height: 961
  });
});
test("buildConfirmParams: clipInfoを渡すと動画モード（RVM）用のclip_*フィールドを追加する", () => {
  const p = core.buildConfirmParams("rvm-mobilenetv3", null, false, 3.4, 540.4, 960.6, { fps: 12, frameCount: 19, targetIndex: 18 });
  assert.deepStrictEqual(p, {
    model: "rvm-mobilenetv3",
    refine: null,
    decontaminate: false,
    time: 3.4,
    output_width: 540,
    output_height: 961,
    clip_fps: 12,
    clip_frame_count: 19,
    clip_target_index: 18
  });
});
test("buildConfirmParams: clipInfoを渡さなければclip_*フィールドは含まれない（静止画モード）", () => {
  const p = core.buildConfirmParams("isnet-general-use", null, false, 3.4, 540, 960);
  assert.strictEqual("clip_fps" in p, false);
  assert.strictEqual("clip_frame_count" in p, false);
  assert.strictEqual("clip_target_index" in p, false);
});
test("confirmedAlphaAssetName: render.pyのcache_path_for_alphaと同じ命名規則（video_<time:.3f>.npz）", () => {
  assert.strictEqual(core.confirmedAlphaAssetName(3.4), "video_3.400.npz");
  assert.strictEqual(core.confirmedAlphaAssetName(0), "video_0.000.npz");
});
test("confirmedAlphaCachePath: cache/<アセット名> になる（render.pyのcache_dir='cache'配下）", () => {
  assert.strictEqual(core.confirmedAlphaCachePath(3.4), "cache/video_3.400.npz");
});

/* ---- isConfirmedAlphaValid ---- */
test("isConfirmedAlphaValid: 動画名・時刻・モデルがすべて一致すればtrue", () => {
  const confirmed = { videoFileName: "a.mp4", time: 3.4, model: "birefnet-portrait", tag: "job-confirm-x" };
  assert.strictEqual(core.isConfirmedAlphaValid(confirmed, "a.mp4", 3.4, "birefnet-portrait"), true);
});
test("isConfirmedAlphaValid: confirmedAlphaが無ければfalse", () => {
  assert.strictEqual(core.isConfirmedAlphaValid(null, "a.mp4", 3.4, "birefnet-portrait"), false);
});
test("isConfirmedAlphaValid: 動画名がズレていればfalse", () => {
  const confirmed = { videoFileName: "a.mp4", time: 3.4, model: "birefnet-portrait" };
  assert.strictEqual(core.isConfirmedAlphaValid(confirmed, "b.mp4", 3.4, "birefnet-portrait"), false);
});
test("isConfirmedAlphaValid: モデルがズレていればfalse", () => {
  const confirmed = { videoFileName: "a.mp4", time: 3.4, model: "birefnet-portrait" };
  assert.strictEqual(core.isConfirmedAlphaValid(confirmed, "a.mp4", 3.4, "isnet-general-use"), false);
});
test("isConfirmedAlphaValid: 時刻がズレていればfalse", () => {
  const confirmed = { videoFileName: "a.mp4", time: 3.4, model: "birefnet-portrait" };
  assert.strictEqual(core.isConfirmedAlphaValid(confirmed, "a.mp4", 3.5, "birefnet-portrait"), false);
});
test("isConfirmedAlphaValid: 浮動小数点誤差程度の時刻差は一致扱いにする", () => {
  const confirmed = { videoFileName: "a.mp4", time: 3.4, model: "birefnet-portrait" };
  assert.strictEqual(core.isConfirmedAlphaValid(confirmed, "a.mp4", 3.4000001, "birefnet-portrait"), true);
});

/* ---- resolveTargetOutputDims / evenDown ---- */
test("evenDown: 偶数はそのまま、奇数は切り捨てて偶数にする", () => {
  assert.strictEqual(core.evenDown(540), 540);
  assert.strictEqual(core.evenDown(541), 540);
  assert.strictEqual(core.evenDown(1.9), 0);
});
test("resolveTargetOutputDims: プリセット指定時はそのプリセットの解像度を返す", () => {
  const dims = core.resolveTargetOutputDims("1080x1920", 999, 999);
  assert.deepStrictEqual(dims, core.OUTPUT_PRESETS["1080x1920"]);
});
test("resolveTargetOutputDims: 'original'等プリセット無しの場合は動画の実効解像度を偶数に丸める", () => {
  const dims = core.resolveTargetOutputDims("original", 541, 961);
  assert.deepStrictEqual(dims, { width: 540, height: 960 });
});

/* ---- findMatchingRun（namePrefix指定） ---- */
test("findMatchingRun: namePrefixを指定すると\"<prefix> \"+tagで一致判定する", () => {
  const runs = [
    { name: "render job-confirm-xxx", created_at: "2026-01-01T00:00:00Z", id: 1 },
    { name: "extract job-confirm-xxx", created_at: "2026-01-02T00:00:00Z", id: 2 }
  ];
  const found = core.findMatchingRun(runs, "job-confirm-xxx", "extract");
  assert.strictEqual(found.id, 2);
});

/* ---- videoAssetName ---- */
test("videoAssetName: 拡張子を保ったまま video.<ext> にする", () => {
  assert.strictEqual(core.videoAssetName("IMG_1234.MOV"), "video.mov");
  assert.strictEqual(core.videoAssetName("input.mp4"), "video.mp4");
});
test("videoAssetName: 拡張子が無ければ video.mp4 にフォールバックする", () => {
  assert.strictEqual(core.videoAssetName("no_extension"), "video.mp4");
  assert.strictEqual(core.videoAssetName(""), "video.mp4");
});

/* ---- needsChunkedUpload / chunkCount / chunkPartPath ---- */
test("needsChunkedUpload: 20MBちょうどは分割不要", () => {
  assert.strictEqual(core.needsChunkedUpload(core.CHUNK_SIZE_BYTES), false);
});
test("needsChunkedUpload: 20MBを1バイトでも超えたら分割対象", () => {
  assert.strictEqual(core.needsChunkedUpload(core.CHUNK_SIZE_BYTES + 1), true);
});
test("needsChunkedUpload: 小さいファイルは分割不要", () => {
  assert.strictEqual(core.needsChunkedUpload(1024), false);
});

test("chunkCount: 20MBちょうどは1パート", () => {
  assert.strictEqual(core.chunkCount(core.CHUNK_SIZE_BYTES), 1);
});
test("chunkCount: 20MB+1バイトは2パート", () => {
  assert.strictEqual(core.chunkCount(core.CHUNK_SIZE_BYTES + 1), 2);
});
test("chunkCount: 60MBちょうどは3パート", () => {
  assert.strictEqual(core.chunkCount(3 * core.CHUNK_SIZE_BYTES), 3);
});
test("chunkCount: 0バイトでも最低1パート", () => {
  assert.strictEqual(core.chunkCount(0), 1);
});

test("chunkPartPath: 3桁ゼロ埋めのpartNNNサフィックスを付ける", () => {
  assert.strictEqual(core.chunkPartPath("video.mp4", 0), "video.mp4.part000");
  assert.strictEqual(core.chunkPartPath("video.mp4", 1), "video.mp4.part001");
  assert.strictEqual(core.chunkPartPath("video.mp4", 12), "video.mp4.part012");
});
test("chunkPartPath: 3桁を超えるパート数でも桁が伸びるだけで壊れない", () => {
  assert.strictEqual(core.chunkPartPath("video.mp4", 123), "video.mp4.part123");
  assert.strictEqual(core.chunkPartPath("video.mp4", 1234), "video.mp4.part1234");
});

/* ---- exceedsTotalUploadLimit ---- */
test("exceedsTotalUploadLimit: 上限ちょうどは超過ではない", () => {
  assert.strictEqual(core.exceedsTotalUploadLimit(core.MAX_TOTAL_UPLOAD_BYTES), false);
});
test("exceedsTotalUploadLimit: 上限を1バイトでも超えたら超過", () => {
  assert.strictEqual(core.exceedsTotalUploadLimit(core.MAX_TOTAL_UPLOAD_BYTES + 1), true);
});
test("exceedsTotalUploadLimit: 小さい合計サイズは超過ではない", () => {
  assert.strictEqual(core.exceedsTotalUploadLimit(60 * 1024 * 1024), false);
});
test("MAX_TOTAL_UPLOAD_BYTES: 1.5GBになっている", () => {
  assert.strictEqual(core.MAX_TOTAL_UPLOAD_BYTES, 1.5 * 1024 * 1024 * 1024);
});

/* ---- formatBytesShort ---- */
test("formatBytesShort: 1GB未満は整数MB", () => {
  assert.strictEqual(core.formatBytesShort(620 * 1024 * 1024), "620MB");
});
test("formatBytesShort: 1GB以上は小数点1桁のGB", () => {
  assert.strictEqual(core.formatBytesShort(1.4 * 1024 * 1024 * 1024), "1.4GB");
});
test("formatBytesShort: 0バイトは0MB", () => {
  assert.strictEqual(core.formatBytesShort(0), "0MB");
});
test("formatBytesShort: 負値は0MB扱い", () => {
  assert.strictEqual(core.formatBytesShort(-100), "0MB");
});

/* ---- formatEstimatedDuration ---- */
test("formatEstimatedDuration: 60秒未満は「約N秒」", () => {
  assert.strictEqual(core.formatEstimatedDuration(30), "約30秒");
});
test("formatEstimatedDuration: 60秒以上・1時間未満は「約N分」", () => {
  assert.strictEqual(core.formatEstimatedDuration(240), "約4分");
});
test("formatEstimatedDuration: 1時間以上は「約N時間M分」", () => {
  assert.strictEqual(core.formatEstimatedDuration(65 * 60), "約1時間5分");
});
test("formatEstimatedDuration: ちょうど1時間は分を省く", () => {
  assert.strictEqual(core.formatEstimatedDuration(60 * 60), "約1時間");
});
test("formatEstimatedDuration: 1秒未満は最低でも「約1秒」", () => {
  assert.strictEqual(core.formatEstimatedDuration(0.2), "約1秒");
});

/* ---- estimateUploadSeconds ---- */
test("estimateUploadSeconds: 合計バイト数÷速度", () => {
  assert.strictEqual(core.estimateUploadSeconds(1000, 100), 10);
});
test("estimateUploadSeconds: 速度が0/未計測ならnull", () => {
  assert.strictEqual(core.estimateUploadSeconds(1000, 0), null);
  assert.strictEqual(core.estimateUploadSeconds(1000, null), null);
  assert.strictEqual(core.estimateUploadSeconds(1000, undefined), null);
});

/* ---- buildUploadEstimateMessage ---- */
test("buildUploadEstimateMessage: サイズと推定時間を「・」で連結する", () => {
  const msg = core.buildUploadEstimateMessage(620 * 1024 * 1024, 240);
  assert.strictEqual(msg, "合計 620MB・約4分");
});
test("buildUploadEstimateMessage: 推定時間nullなら計測失敗の注記のみ", () => {
  const msg = core.buildUploadEstimateMessage(620 * 1024 * 1024, null);
  assert.ok(msg.indexOf("合計 620MB") === 0);
  assert.ok(msg.indexOf("約") === -1);
});

/* ---- buildManifestEntry / buildManifestPayload ---- */
test("buildManifestEntry: name/originalName/parts/size/sha256を記録する", () => {
  const e = core.buildManifestEntry("video.mp4", "IMG_1234.MOV", 3 * core.CHUNK_SIZE_BYTES, "deadbeef");
  assert.strictEqual(e.name, "video.mp4");
  assert.strictEqual(e.originalName, "IMG_1234.MOV");
  assert.strictEqual(e.parts, 3);
  assert.strictEqual(e.size, 3 * core.CHUNK_SIZE_BYTES);
  assert.strictEqual(e.sha256, "deadbeef");
});
test("buildManifestEntry: originalNameが無ければnameで補う", () => {
  const e = core.buildManifestEntry("video.mp4", null, 1024, "abc");
  assert.strictEqual(e.originalName, "video.mp4");
});

test("buildManifestPayload: versionとfiles配列を持つ", () => {
  const entries = [core.buildManifestEntry("video.mp4", "a.mov", 1024, "abc")];
  const payload = core.buildManifestPayload(entries);
  assert.strictEqual(payload.version, 1);
  assert.deepStrictEqual(payload.files, entries);
});
test("buildManifestPayload: 未指定なら空配列になる", () => {
  assert.deepStrictEqual(core.buildManifestPayload(), { version: 1, files: [] });
});

/* ---- bufferToHex ---- */
test("bufferToHex: バイト列を小文字16進文字列に変換する", () => {
  const buf = new Uint8Array([0, 1, 15, 16, 255]).buffer;
  assert.strictEqual(core.bufferToHex(buf), "00010f10ff");
});
test("bufferToHex: 空バッファは空文字列", () => {
  assert.strictEqual(core.bufferToHex(new Uint8Array([]).buffer), "");
});

/* ---- buildReleasePayload / buildDispatchPayload ---- */
test("buildReleasePayload: tag_name/name にタグを設定し draft/prerelease はfalse", () => {
  const p = core.buildReleasePayload("job-20260829-153005");
  assert.strictEqual(p.tag_name, "job-20260829-153005");
  assert.strictEqual(p.name, "job-20260829-153005");
  assert.strictEqual(p.draft, false);
  assert.strictEqual(p.prerelease, false);
});
test("buildDispatchPayload: refとinputs.tagを含む", () => {
  const p = core.buildDispatchPayload("main", "job-20260829-153005");
  assert.deepStrictEqual(p, { ref: "main", inputs: { tag: "job-20260829-153005" } });
});

/* ---- buildBlobPayload / buildGitTreePayload / buildGitCommitPayload / buildGitRefPayload ---- */
test("buildBlobPayload: contentとencoding=base64を含む", () => {
  const p = core.buildBlobPayload("aGVsbG8=");
  assert.deepStrictEqual(p, { content: "aGVsbG8=", encoding: "base64" });
});
test("buildGitTreePayload: base_treeとtreeエントリ(mode=100644, type=blob)を組み立てる", () => {
  const p = core.buildGitTreePayload("base-sha", [
    { path: "project.json", sha: "json-sha" },
    { path: "video.mp4", sha: "video-sha" }
  ]);
  assert.deepStrictEqual(p, {
    base_tree: "base-sha",
    tree: [
      { path: "project.json", mode: "100644", type: "blob", sha: "json-sha" },
      { path: "video.mp4", mode: "100644", type: "blob", sha: "video-sha" }
    ]
  });
});
test("buildGitCommitPayload: message/tree/parentsを組み立てる", () => {
  const p = core.buildGitCommitPayload("job-20260829-153005", "tree-sha", "parent-sha");
  assert.deepStrictEqual(p, { message: "job-20260829-153005", tree: "tree-sha", parents: ["parent-sha"] });
});
test("buildGitRefPayload: refはrefs/heads/<ブランチ名>になる", () => {
  const p = core.buildGitRefPayload("job-20260829-153005", "commit-sha");
  assert.deepStrictEqual(p, { ref: "refs/heads/job-20260829-153005", sha: "commit-sha" });
});

/* ---- findMatchingRun ---- */
test("findMatchingRun: run-name(\"render \"+tag)と完全一致するrunを返す", () => {
  const runs = [
    { name: "render job-aaa", created_at: "2026-01-01T00:00:00Z", id: 1 },
    { name: "render job-bbb", created_at: "2026-01-02T00:00:00Z", id: 2 },
    { name: "cleanup-old-releases", created_at: "2026-01-03T00:00:00Z", id: 3 }
  ];
  const found = core.findMatchingRun(runs, "job-bbb");
  assert.strictEqual(found.id, 2);
});
test("findMatchingRun: 一致するrunが無ければnull", () => {
  assert.strictEqual(core.findMatchingRun([{ name: "render job-aaa" }], "job-zzz"), null);
  assert.strictEqual(core.findMatchingRun([], "job-zzz"), null);
});
test("findMatchingRun: 複数一致した場合は最も新しいものを返す", () => {
  const runs = [
    { name: "render job-aaa", created_at: "2026-01-01T00:00:00Z", id: "old" },
    { name: "render job-aaa", created_at: "2026-01-05T00:00:00Z", id: "new" }
  ];
  assert.strictEqual(core.findMatchingRun(runs, "job-aaa").id, "new");
});

/* ---- interpretRunStatus ---- */
test("interpretRunStatus: 未完了(queued/in_progress)はpending", () => {
  assert.strictEqual(core.interpretRunStatus({ status: "queued" }), "pending");
  assert.strictEqual(core.interpretRunStatus({ status: "in_progress" }), "pending");
});
test("interpretRunStatus: completed かつ conclusion=success は success", () => {
  assert.strictEqual(core.interpretRunStatus({ status: "completed", conclusion: "success" }), "success");
});
test("interpretRunStatus: completed かつ conclusion!=success は failure", () => {
  assert.strictEqual(core.interpretRunStatus({ status: "completed", conclusion: "failure" }), "failure");
  assert.strictEqual(core.interpretRunStatus({ status: "completed", conclusion: "cancelled" }), "failure");
});
test("interpretRunStatus: run自体が無い場合はpending扱い", () => {
  assert.strictEqual(core.interpretRunStatus(null), "pending");
});

/* ---- findReleaseAsset ---- */
test("findReleaseAsset: 名前が一致するアセットを返す", () => {
  const assets = [{ name: "project.json", id: 1 }, { name: "output.mp4", id: 2 }];
  assert.strictEqual(core.findReleaseAsset(assets, "output.mp4").id, 2);
});
test("findReleaseAsset: 一致が無ければnull", () => {
  assert.strictEqual(core.findReleaseAsset([{ name: "a" }], "b"), null);
  assert.strictEqual(core.findReleaseAsset([], "b"), null);
});

/* ---- maskTokenStatus ---- */
test("maskTokenStatus: トークンが無ければ未設定メッセージ", () => {
  assert.strictEqual(core.maskTokenStatus(""), "未設定です。トークンを入力して保存してください。");
  assert.strictEqual(core.maskTokenStatus(null), "未設定です。トークンを入力して保存してください。");
  assert.strictEqual(core.maskTokenStatus("   "), "未設定です。トークンを入力して保存してください。");
});
test("maskTokenStatus: トークンがあれば末尾4文字だけを表示する", () => {
  assert.strictEqual(core.maskTokenStatus("github_pat_abcdEFGH1234"), "保存済み（トークン末尾 …1234）");
});
test("maskTokenStatus: 4文字未満のトークンはそのまま表示する", () => {
  assert.strictEqual(core.maskTokenStatus("ab"), "保存済み（トークン末尾 …ab）");
});

/* ---- 複数クリップ（clips[]） ---- */
test("defaultTransitionOut: 既定はcut・0.4秒", () => {
  assert.deepStrictEqual(core.defaultTransitionOut(), { type: "cut", sec: 0.4 });
});
test("resolveTransitionOut: 不正なtypeは既定(cut)にフォールバックする", () => {
  assert.deepStrictEqual(core.resolveTransitionOut({ type: "nonsense", sec: 1.0 }), { type: "cut", sec: 1.0 });
});
test("resolveTransitionOut: 正常なtype/secはそのまま通す", () => {
  assert.deepStrictEqual(core.resolveTransitionOut({ type: "crossfade", sec: 0.6 }), { type: "crossfade", sec: 0.6 });
});
test("resolveTransitionOut: 未指定/nullは既定値になる", () => {
  assert.deepStrictEqual(core.resolveTransitionOut(null), core.defaultTransitionOut());
  assert.deepStrictEqual(core.resolveTransitionOut(undefined), core.defaultTransitionOut());
});
test("resolveTransitionOut: secが小さすぎる/負の場合は最低値にクランプする", () => {
  assert.strictEqual(core.resolveTransitionOut({ type: "cut", sec: -1 }).sec, 0.05);
  assert.strictEqual(core.resolveTransitionOut({ type: "cut", sec: 0 }).sec, 0.05);
});

test("clipVideoAssetName: インデックスごとに一意な名前になり、拡張子は元ファイルから引き継ぐ", () => {
  assert.strictEqual(core.clipVideoAssetName(0, "IMG_0001.MOV"), "clip0.mov");
  assert.strictEqual(core.clipVideoAssetName(1, "b.mp4"), "clip1.mp4");
  assert.strictEqual(core.clipVideoAssetName(2, "no_ext"), "clip2.mp4");
});

test("clampClipTrim: in/outを0以上・尺以内にクランプする", () => {
  assert.deepStrictEqual(core.clampClipTrim(-1, 100, 10), { in: 0, out: 10 });
  assert.deepStrictEqual(core.clampClipTrim(2, 5, 10), { in: 2, out: 5 });
});
test("clampClipTrim: outがnullなら「最後まで」として維持する", () => {
  assert.deepStrictEqual(core.clampClipTrim(1, null, 10), { in: 1, out: null });
});
test("clampClipTrim: outがinより小さい/近すぎる場合は最低差分を確保する", () => {
  const r = core.clampClipTrim(5, 5, 10);
  // 内部は2桁に丸めるため、0.05ちょうどの比較は浮動小数の誤差で崩れうる。
  // 「ほぼ0.05以上の間隔が確保されている」ことだけを見る。
  assert.ok(r.out - r.in >= 0.049);
});
test("clampClipTrim: 尺が未確定(null)でもin/outの相対関係だけはクランプする", () => {
  assert.deepStrictEqual(core.clampClipTrim(0, 20, null), { in: 0, out: 20 });
});

function sampleClip(overrides) {
  return Object.assign({
    videoFileName: "a.mp4", in: 0, out: null, duration: 12,
    freezes: [{ id: "x", time: 1.5, name: "クリップ内フリーズ", strokes: [] }],
    transitionOut: { type: "crossfade", sec: 0.5 }
  }, overrides || {});
}

test("buildProjectJSON: clipsがあればproject.clipsを出力し、video/freezesキーは省略する", () => {
  const state = sampleState();
  state.clips = [sampleClip({ videoFileName: "a.mp4" }), sampleClip({ videoFileName: "b.mp4", transitionOut: { type: "cut", sec: 0.4 } })];
  const project = core.buildProjectJSON(state);
  assert.strictEqual(project.video, undefined);
  assert.strictEqual(project.freezes, undefined);
  assert.strictEqual(project.clips.length, 2);
  assert.strictEqual(project.clips[0].video, "clip0.mp4");
  assert.strictEqual(project.clips[1].video, "clip1.mp4");
});

test("buildProjectJSON: clipsが空ならこれまでどおり単一video/freezesを出力する（完全後方互換）", () => {
  const state = sampleState();
  state.clips = [];
  const project = core.buildProjectJSON(state);
  assert.strictEqual(project.video, "dummy_input.mp4");
  assert.ok(Array.isArray(project.freezes));
  assert.strictEqual(project.clips, undefined);
});

test("buildProjectJSON: 各クリップのin/out/transition_outが契約どおりに出力される", () => {
  const state = sampleState();
  state.clips = [
    sampleClip({ videoFileName: "a.mp4", in: 1.2, out: 9.5, transitionOut: { type: "wipe", sec: 0.3 } }),
    sampleClip({ videoFileName: "b.mp4", in: 0, out: null })
  ];
  const project = core.buildProjectJSON(state);
  assert.strictEqual(project.clips[0].in, 1.2);
  assert.strictEqual(project.clips[0].out, 9.5);
  assert.deepStrictEqual(project.clips[0].transition_out, { type: "wipe", sec: 0.3 });
  assert.strictEqual(project.clips[1].out, null);
});

test("buildProjectJSON: 各クリップのfreezesは、そのクリップ自身の配列からtime昇順で出力される", () => {
  const state = sampleState();
  state.clips = [
    sampleClip({
      videoFileName: "a.mp4",
      freezes: [
        { id: "b", time: 5, name: "後", strokes: [] },
        { id: "a", time: 1, name: "先", strokes: [] }
      ]
    })
  ];
  const project = core.buildProjectJSON(state);
  assert.strictEqual(project.clips[0].freezes.length, 2);
  assert.strictEqual(project.clips[0].freezes[0].time, 1);
  assert.strictEqual(project.clips[0].freezes[1].time, 5);
});

test("buildProjectJSON: style/logo/watermark/hashtagsはclips使用時もプロジェクト全体で共有される（クリップごとに分裂しない）", () => {
  const state = sampleState();
  state.logo = { imageName: "logo.png", at: "end" };
  state.watermark = { enabled: true, imageName: "wm.png" };
  state.hashtags = { enabled: true, text: "#test" };
  state.clips = [sampleClip({ videoFileName: "a.mp4" }), sampleClip({ videoFileName: "b.mp4" })];
  const project = core.buildProjectJSON(state);
  assert.strictEqual(project.logo.image, "logo.png");
  assert.strictEqual(project.watermark.image, "wm.png");
  assert.strictEqual(project.hashtags.text, "#test");
  assert.ok(project.style);
});

/* ---- まとめ ---- */
console.log("");
console.log(passed + " 件成功 / " + failures + " 件失敗");
if (failures > 0) {
  process.exit(1);
}
