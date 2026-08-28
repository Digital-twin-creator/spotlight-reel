#!/usr/bin/env node
// -*- coding: utf-8 -*-
//
// iOS Safariでアドレスバーが出入りするたびに resize イベントが連発し、
// それによって描画モード中の映像エリアが真っ黒になったまま戻らない、
// という不具合に対する回帰テスト。
//
//   A-1. canvasのサイズ再設定は幅かアスペクト比が変わった時だけに限定
//   A-2. 再設定後は必ず frozenFrameCanvas とストロークを再描画
//   A-3. 描画モード中は映像エリアをposition:fixedで上部固定、設定項目だけスクロール
//   A-4. 通常モードでもresize後は現在フレームを即再描画
// をまとめて検証する。
//
// 実行方法:
//   npm install --no-save playwright-core
//   node tests/resize_scroll_black_screen.playwright.test.mjs
//
// 環境変数 PW_URL / PW_CHROMIUM_PATH / PW_VIDEO は他のplaywrightテストと同じ。

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
    execSync(`ffmpeg -y -v error -i "${src}" -c:v libvpx-vp9 -crf 32 -b:v 0 -c:a libopus "${out}"`, { stdio: "inherit" });
  }
  return out;
}

let failed = 0;
let passed = 0;
function check(cond, label) {
  if (cond) { passed++; console.log("  ok - " + label); }
  else { failed++; console.log("  NG - " + label); }
}

function readCenterPixel(page, canvasId) {
  return page.evaluate((id) => {
    const c = document.getElementById(id);
    const ctx = c.getContext("2d");
    const x = Math.floor(c.width / 2), y = Math.floor(c.height / 2);
    return Array.from(ctx.getImageData(x, y, 1, 1).data);
  }, canvasId);
}
function isBlack(px) {
  return px[0] < 8 && px[1] < 8 && px[2] < 8;
}

async function main() {
  const videoPath = prepareTestVideo();
  const iphone = devices["iPhone 13"];
  // iPhone 13 の viewport は 390x664（Safariのアドレスバー表示中に相当）。
  // アドレスバーが引っ込むと高さだけ大きくなる（幅は変わらない）ことが多いため、
  // 幅一定・高さだけ変化する resize を「アドレスバーの出入り」の模擬として使う。
  const browser = await chromium.launch({ executablePath: CHROMIUM_PATH, headless: true });
  const context = await browser.newContext({ ...iphone });
  const page = await context.newPage();
  const pageErrors = [];
  page.on("pageerror", (e) => pageErrors.push(e.message));

  await page.goto(BASE_URL, { waitUntil: "load" });
  await page.click("#guideCloseBtn").catch(() => {});
  await page.setInputFiles("#videoFileInput", videoPath);
  await page.waitForFunction(() => document.getElementById("video").duration > 0, null, { timeout: 10000 });
  await page.waitForTimeout(300);

  await page.evaluate(() => new Promise((resolve) => {
    const v = document.getElementById("video");
    v.addEventListener("seeked", resolve, { once: true });
    v.currentTime = 2.5;
    setTimeout(resolve, 500);
  }));
  await page.waitForTimeout(200);

  console.log("=== 描画モード中に resize が連発しても黒くならない（アドレスバー出入りの模擬） ===");
  await page.click("#addFreezeBtn");
  await page.waitForFunction(() => !document.getElementById("editorSection").hidden, null, { timeout: 5000 });
  await page.waitForTimeout(300);

  const beforePixel = await readCenterPixel(page, "playerCanvas");
  check(!isBlack(beforePixel), "フリーズ直後、playerCanvas中央画素が黒でない");

  // 幅は同じまま高さだけ何度も変える = アドレスバー出入りを模擬したresize連打
  const heights = [664, 844, 664, 780, 664, 844, 664];
  for (const h of heights) {
    await page.setViewportSize({ width: 390, height: h });
    await page.waitForTimeout(80);
  }
  await page.waitForTimeout(200);

  const afterResizeStormPixel = await readCenterPixel(page, "playerCanvas");
  check(!isBlack(afterResizeStormPixel), "resize連打後もplayerCanvas中央画素が黒くならない: " + JSON.stringify(afterResizeStormPixel));

  const fixedLayoutOk = await page.evaluate(() => {
    const body = getComputedStyle(document.body);
    const vw = getComputedStyle(document.getElementById("videoWrap"));
    const es = getComputedStyle(document.getElementById("editorSection"));
    return body.position === "fixed" && vw.position === "fixed" && es.position === "fixed" && es.overflowY === "auto";
  });
  check(fixedLayoutOk, "描画モード中は body/videoWrap/editorSectionがposition:fixedで固定されている");

  console.log("");
  console.log("=== 描画モード中、editorSection内をスクロールしても映像は黒くならない ===");
  await page.setViewportSize({ width: 390, height: 664 });
  await page.waitForTimeout(150);
  await page.evaluate(() => { document.getElementById("editorSection").scrollTop = 400; });
  await page.waitForTimeout(150);
  await page.evaluate(() => { document.getElementById("editorSection").scrollTop = 0; });
  await page.waitForTimeout(150);
  const afterScrollPixel = await readCenterPixel(page, "playerCanvas");
  check(!isBlack(afterScrollPixel), "editorSection内スクロール後もplayerCanvas中央画素が黒くならない");

  const commitBtnReachable = await page.evaluate(() => {
    const r = document.getElementById("commitFreezeBtn").getBoundingClientRect();
    return r.width > 0 && r.top >= 0 && r.bottom <= window.innerHeight;
  });
  check(commitBtnReachable, "スクロール後も完了ボタンがビューポート内に留まる（固定バー）");

  // ストロークを1本描いてから resize しても消えないことも確認する
  const box = await page.locator("#drawCanvas").boundingBox();
  await page.mouse.move(box.x + box.width * 0.4, box.y + box.height * 0.3);
  await page.mouse.down();
  await page.mouse.move(box.x + box.width * 0.6, box.y + box.height * 0.5);
  await page.mouse.up();
  const strokeCountBefore = await page.evaluate(() => draft.strokes.length);
  check(strokeCountBefore === 1, "ストロークが1本記録されている");

  await page.setViewportSize({ width: 390, height: 844 });
  await page.waitForTimeout(200);
  await page.setViewportSize({ width: 390, height: 664 });
  await page.waitForTimeout(200);
  const strokeCountAfter = await page.evaluate(() => draft.strokes.length);
  check(strokeCountAfter === 1, "resize後もストローク数のデータは保持されている: " + strokeCountAfter);

  // drawCanvas自体は毎回再描画されるので、resize後に見た目上ストロークが
  // 消えていないか、非黒画素の存在で簡易確認する
  const drawCanvasHasInk = await page.evaluate(() => {
    const c = document.getElementById("drawCanvas");
    const ctx = c.getContext("2d");
    const data = ctx.getImageData(0, 0, c.width, c.height).data;
    for (let i = 3; i < data.length; i += 4 * 97) { // alphaチャンネルを間引きサンプリング
      if (data[i] > 10) return true;
    }
    return false;
  });
  check(drawCanvasHasInk, "resize後もdrawCanvas上にストロークの描画が残っている");

  console.log("");
  console.log("=== 通常モード（描画モードではない）でも resize 後は現在フレームを即再描画する ===");
  await page.click("#cancelFreezeBtn");
  await page.waitForFunction(() => document.getElementById("editorSection").hidden, null, { timeout: 5000 });
  await page.waitForTimeout(300);

  const normalLayoutOk = await page.evaluate(() => getComputedStyle(document.body).position !== "fixed");
  check(normalLayoutOk, "キャンセル後、bodyのposition:fixedが解除されている");

  await page.evaluate(() => new Promise((resolve) => {
    const v = document.getElementById("video");
    v.addEventListener("seeked", resolve, { once: true });
    v.currentTime = 5.0;
    setTimeout(resolve, 500);
  }));
  await page.waitForTimeout(150);
  const beforeNormalResize = await readCenterPixel(page, "playerCanvas");

  // 幅を変える＝実際にcanvasが再設定されるresize。直後（rAFを待たず）に
  // 即座に再描画されているはずなので、ごく短い待機時間だけで確認する。
  await page.setViewportSize({ width: 414, height: 700 });
  await page.waitForTimeout(30);
  const rightAfterResize = await readCenterPixel(page, "playerCanvas");
  check(!isBlack(rightAfterResize),
    "通常モードで幅の変わるresize直後、playerCanvasが黒のままでない（即時再描画されている）: " + JSON.stringify(rightAfterResize));

  console.log("");
  console.log("page errors:", pageErrors.length, pageErrors);
  check(pageErrors.length === 0, "ページ例外が発生していない");

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
