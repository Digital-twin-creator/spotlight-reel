#!/usr/bin/env node
// -*- coding: utf-8 -*-
//
// index.html のフリーズ編集画面にある「ブラシ形状」選択（round/hake/marker/spray）が、
// 実際にdrawCanvas上の描画（筆先スタンプ方式）に反映されること、選んだ形状が
// 完了後のJSON書き出し（style.brush_shape相当のフィールド）にも正しく載ることを、
// headless Chromium + iPhoneデバイスエミュレーションで検証する回帰テスト。
//
// 実行方法:
//   npm install --no-save playwright-core
//   python3 -m http.server 8794 &
//   node tests/brush_shape.playwright.test.mjs
//
// 環境変数:
//   PW_URL             index.html を配信しているURL（既定: http://127.0.0.1:8794/index.html）
//   PW_CHROMIUM_PATH   Chromium実行ファイルのパス
//   PW_VIDEO           テストに使う動画ファイル（VP9/WebM推奨。既定はexamples配下から自動生成）

import { chromium, devices } from "playwright-core";
import path from "node:path";
import os from "node:os";
import { execSync } from "node:child_process";
import fs from "node:fs";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.join(__dirname, "..");
const BASE_URL = process.env.PW_URL || "http://127.0.0.1:8794/index.html";
const CHROMIUM_PATH = process.env.PW_CHROMIUM_PATH || "/opt/pw-browsers/chromium-1194/chrome-linux/chrome";

function prepareTestVideo() {
  if (process.env.PW_VIDEO) return process.env.PW_VIDEO;
  const src = path.join(REPO_ROOT, "examples", "dummy_input.mp4");
  const out = path.join(os.tmpdir(), "spotlight_reel_test_vp9.webm");
  if (!fs.existsSync(out)) {
    execSync(
      `ffmpeg -y -v error -i "${src}" -c:v libvpx-vp9 -crf 32 -b:v 0 -c:a libopus "${out}"`,
      { stdio: "inherit" }
    );
  }
  return out;
}

let failed = 0;
let passed = 0;
function check(cond, label) {
  if (cond) { passed++; console.log("  ok - " + label); }
  else { failed++; console.log("  NG - " + label); }
}

/** drawCanvas上でマウスドラッグして1本ストロークを描く */
async function dragStroke(page, box) {
  const x0 = box.x + box.width * 0.3, y0 = box.y + box.height * 0.3;
  const x1 = box.x + box.width * 0.7, y1 = box.y + box.height * 0.6;
  await page.mouse.move(x0, y0);
  await page.mouse.down();
  for (let i = 1; i <= 8; i++) {
    const t = i / 8;
    await page.mouse.move(x0 + (x1 - x0) * t, y0 + (y1 - y0) * t);
  }
  await page.mouse.up();
}

/** drawCanvasの非透明ピクセル数を数える（何か描かれているかの簡易チェック） */
async function countOpaquePixels(page) {
  return page.evaluate(() => {
    const c = document.getElementById("drawCanvas");
    const ctx = c.getContext("2d");
    const data = ctx.getImageData(0, 0, c.width, c.height).data;
    let n = 0;
    for (let i = 3; i < data.length; i += 4) if (data[i] > 10) n++;
    return n;
  });
}

async function main() {
  const videoPath = prepareTestVideo();
  const iphone = devices["iPhone 13"];
  const browser = await chromium.launch({ executablePath: CHROMIUM_PATH, headless: true });
  const context = await browser.newContext({ ...iphone });
  const page = await context.newPage();
  const pageErrors = [];
  page.on("pageerror", (e) => pageErrors.push(e.message));

  await page.goto(BASE_URL, { waitUntil: "load" });
  await page.click("#guideCloseBtn").catch(() => {});
  await page.setInputFiles("#videoFileInput", videoPath);
  await page.waitForFunction(() => document.getElementById("video").duration > 0, null, { timeout: 10000 });

  console.log("=== ブラシ形状セレクタの基本動作 ===");
  await page.click("#addFreezeBtn");
  await page.waitForFunction(() => !document.getElementById("editorSection").hidden, null, { timeout: 5000 });

  const selectOptions = await page.$$eval("#brushShapeSelect option", (opts) => opts.map((o) => o.value));
  check(JSON.stringify(selectOptions) === JSON.stringify(["round", "hake", "marker", "spray"]),
    "ブラシ形状の選択肢が round/hake/marker/spray の4つ（この順）: " + JSON.stringify(selectOptions));

  const initialShape = await page.inputValue("#brushShapeSelect");
  check(initialShape === "round", "初期状態は round: " + initialShape);

  // 筆先画像の読み込みを待つ（先読み済みのはずだが、念のため）
  await page.waitForTimeout(300);

  const box = await page.locator("#drawCanvas").boundingBox();
  await dragStroke(page, box);
  const pixelsAfterRoundStroke = await countOpaquePixels(page);
  check(pixelsAfterRoundStroke > 0, "round形状でストロークを描くとdrawCanvasに何か描画される: " + pixelsAfterRoundStroke + "px");

  console.log("");
  console.log("=== 形状を切り替えると見た目（ピクセル）が変わる ===");
  const shapes = ["round", "hake", "marker", "spray"];
  const snapshots = {};
  for (const shape of shapes) {
    await page.selectOption("#brushShapeSelect", shape);
    await page.waitForTimeout(50);
    const dataUrl = await page.evaluate(() => document.getElementById("drawCanvas").toDataURL());
    snapshots[shape] = dataUrl;
    const px = await countOpaquePixels(page);
    check(px > 0, "形状=" + shape + " に切り替えた後もストロークが描画されたまま: " + px + "px");
  }
  const uniqueSnapshots = new Set(Object.values(snapshots));
  check(uniqueSnapshots.size === shapes.length,
    "4形状それぞれでdrawCanvasの見た目（画素データ）が異なる（同じ絵になっていない）: ユニーク数=" + uniqueSnapshots.size);

  console.log("");
  console.log("=== 完了後、選んだ形状がJSONに反映される ===");
  await page.selectOption("#brushShapeSelect", "marker");
  await page.fill("#nameInput", "形状テスト");
  await page.click("#commitFreezeBtn");
  await page.waitForFunction(() => document.getElementById("editorSection").hidden, null, { timeout: 5000 });

  const projectJson = await page.evaluate(() => buildProjectJSON(appState));
  check(projectJson.freezes.length === 1 && projectJson.freezes[0].brush_shape === "marker",
    "書き出したJSONの freezes[0].brush_shape が選択した 'marker' になっている: " +
    JSON.stringify(projectJson.freezes[0] && projectJson.freezes[0].brush_shape));

  console.log("");
  console.log("=== 既存フリーズの再編集でも選択した形状が復元される ===");
  const freezeId = await page.evaluate(() => appState.freezes[0].id);
  await page.evaluate((id) => { editFreeze(id); }, freezeId);
  await page.waitForFunction(() => !document.getElementById("editorSection").hidden, null, { timeout: 5000 });
  const restoredShape = await page.inputValue("#brushShapeSelect");
  check(restoredShape === "marker", "再編集時にブラシ形状選択が 'marker' に復元される: " + restoredShape);
  await page.click("#cancelFreezeBtn");

  check(pageErrors.length === 0, "ページ例外が発生していない: " + JSON.stringify(pageErrors));

  await context.close();
  await browser.close();

  console.log("");
  console.log(passed + " 件成功 / " + failed + " 件失敗");
  if (failed > 0) process.exit(1);
}

main().catch((err) => {
  console.error("テスト実行中に例外:", err);
  process.exit(1);
});
