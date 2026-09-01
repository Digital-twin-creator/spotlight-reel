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

/* ---- 演出追加：影（フィルム色）／テロップバウンス／ラストロゴ ---- */

test("DEFAULT_STYLE: freeze_secの既定値は1.2、film_offset等は後方互換パース専用の無効化デフォルト", () => {
  assert.strictEqual(core.DEFAULT_STYLE.freeze_sec, 1.2);
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
  assert.strictEqual(core.DEFAULT_SHADOW.slideSec, 0.2);
  assert.strictEqual(core.SHADOW_SLIDE_BACK_SEC, 0.1);
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

test("easeOutExpo: t=0で0、t=1で1、序盤速く終盤で急停止するイージング（render.pyのease_out_expoと同じ）", () => {
  assert.strictEqual(core.easeOutExpo(0), 0);
  assert.strictEqual(core.easeOutExpo(1), 1);
  assert.strictEqual(core.easeOutExpo(0.1), 1 - Math.pow(2, -1)); // 1 - 2^(-10*0.1)
  // 序盤(0→0.3)の伸びが終盤(0.7→1.0)の伸びよりずっと大きい（急停止イージングの形）
  const earlyGain = core.easeOutExpo(0.3) - core.easeOutExpo(0);
  const lateGain = core.easeOutExpo(1.0) - core.easeOutExpo(0.7);
  assert.ok(earlyGain > lateGain);
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
    offset_y: core.DEFAULT_SHADOW.offsetY, blur: 0.02, slide_sec: core.DEFAULT_SHADOW.slideSec
  });
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

test("buildProjectJSON: logoにimageNameがあればlogoブロックを出力し、無ければ省略する", () => {
  const withLogo = core.buildProjectJSON(Object.assign(sampleState(), {
    logo: { imageName: "logo.png", at: "last_freeze", background: "auto", durationSec: 1.2, sfx: "don" }
  }));
  assert.deepStrictEqual(withLogo.logo,
    { image: "logo.png", at: "last_freeze", background: "auto", duration_sec: 1.2, sfx: "don" });

  const withoutLogo = core.buildProjectJSON(Object.assign(sampleState(), {
    logo: { imageFile: null, imageName: "", at: "end", background: "auto", durationSec: 1.2, sfx: "don" }
  }));
  assert.strictEqual(withoutLogo.logo, undefined);
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
    durationSec: 1.2, sfx: "don", autoColorHex: ""
  });
});

test("parseProjectJSON: logo.backgroundが省略されていれば既定値'auto'を補う", () => {
  const loaded = core.parseProjectJSON({
    version: 1, video: "v.mp4", freezes: [],
    logo: { image: "logo.png", at: "end", duration_sec: 1.2 }
  });
  assert.strictEqual(loaded.logo.background, "auto");
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

test("DEFAULT_LOGO_DURATION_SEC は着地からの表示時間として1.2秒", () => {
  assert.strictEqual(core.DEFAULT_LOGO_DURATION_SEC, 1.2);
});

test("logoLandingScale/logoLandingEase: t=0で初期スケール(200%)・不透明度0、t=1で100%・不透明度1", () => {
  assert.strictEqual(core.logoLandingScale(0), core.LOGO_PREVIEW_SCALE_FROM);
  assert.strictEqual(core.logoLandingScale(1), 1);
  assert.strictEqual(core.logoLandingEase(0), 0);
  assert.strictEqual(core.logoLandingEase(1), 1);
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

/* ---- makeJobTag ---- */
test("makeJobTag: job-YYYYMMDD-HHMMSS 形式（UTC基準）になる", () => {
  const d = new Date(Date.UTC(2026, 7, 29, 15, 30, 5)); // 2026-08-29T15:30:05Z
  assert.strictEqual(core.makeJobTag(d), "job-20260829-153005");
});
test("makeJobTag: 各要素が2桁ゼロ埋めされる", () => {
  const d = new Date(Date.UTC(2026, 0, 5, 3, 4, 9));
  assert.strictEqual(core.makeJobTag(d), "job-20260105-030409");
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

/* ---- exceedsBlobLimit ---- */
test("exceedsBlobLimit: 100MBちょうどは超過ではない", () => {
  assert.strictEqual(core.exceedsBlobLimit(core.MAX_BLOB_BYTES), false);
});
test("exceedsBlobLimit: 100MBを1バイトでも超えたら超過", () => {
  assert.strictEqual(core.exceedsBlobLimit(core.MAX_BLOB_BYTES + 1), true);
});
test("exceedsBlobLimit: 小さいファイルは超過ではない", () => {
  assert.strictEqual(core.exceedsBlobLimit(1024), false);
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

/* ---- まとめ ---- */
console.log("");
console.log(passed + " 件成功 / " + failures + " 件失敗");
if (failures > 0) {
  process.exit(1);
}
