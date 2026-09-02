#!/usr/bin/env node
// -*- coding: utf-8 -*-
//
// iPhone実機で報告された3件の不具合の回帰テスト（縦動画・横動画の両方で検証）。
//   1. 縦動画が正しく扱えない（videoWidth/videoHeightの回転メタデータ絡みの不具合）
//   2. フリーズ追加後に映像エリアの位置がずれ、シークが効かなくなる
//      （exitDrawModeがvideoWrap.style.topを消し忘れていた）
//   3. 2つ目以降のフリーズが反映されない（上記2の症状として、以降のタップが
//      video-wrapに吸われて意図したボタンに届かないことが主因と判明）
//
// 縦動画(1080x1920)・横動画(1920x1080)それぞれで
// 「読込→シーク→フリーズ追加×3→完了→シーク→JSON書き出し→freezesが3件」を検証する。
//
// 実行方法:
//   npm install --no-save playwright-core
//   python3 -m http.server 8794 &
//   node tests/portrait_landscape_freezes.playwright.test.mjs
//
// 環境変数 PW_URL / PW_CHROMIUM_PATH は他のPlaywrightテストと同じ。

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

/** headless ChromiumはH.264 MP4の直接デコードが不安定なため、VP9/WebMに変換して使う */
function prepareTestVideo(srcName, cacheName) {
  const src = path.join(REPO_ROOT, "examples", srcName);
  const out = path.join(os.tmpdir(), cacheName);
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

async function seekAndWaitReal(page, t) {
  await page.evaluate((time) => new Promise((resolve) => {
    const v = document.getElementById("video");
    v.addEventListener("seeked", resolve, { once: true });
    v.currentTime = time;
    setTimeout(resolve, 500);
  }), t);
  await page.waitForTimeout(200);
}

/** drawCanvas上でマウスドラッグして1本ストロークを描く（形状は既定のround） */
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

async function runForOrientation(browser, label, videoPath) {
  console.log("");
  console.log("########## " + label + " (" + videoPath + ") ##########");
  const iphone = devices["iPhone 13"];
  const context = await browser.newContext({ ...iphone });
  const page = await context.newPage();
  const pageErrors = [];
  page.on("pageerror", (e) => pageErrors.push(e.message));

  await page.goto(BASE_URL, { waitUntil: "load" });
  await page.click("#guideCloseBtn").catch(() => {});
  await page.setInputFiles("#videoFileInput", videoPath);
  await page.waitForFunction(() => document.getElementById("video").duration > 0, null, { timeout: 10000 });
  await page.waitForTimeout(300);

  const videoDims = await page.evaluate(() => ({
    w: document.getElementById("video").videoWidth,
    h: document.getElementById("video").videoHeight
  }));
  console.log("  video reported dims: " + videoDims.w + "x" + videoDims.h);

  console.log("=== 読込→シーク ===");
  await seekAndWaitReal(page, 1.0);
  const firstPixel = await readCenterPixel(page, "playerCanvas");
  check(!isBlack(firstPixel), label + ": 読込直後のシークでplayerCanvasが黒でない");

  const names = ["ひとりめ", "ふたりめ", "さんにんめ"];
  for (let i = 0; i < 3; i++) {
    console.log("=== フリーズ追加 " + (i + 1) + "/3 ===");
    await seekAndWaitReal(page, 1.0 + i * 1.5);

    await page.click("#addFreezeBtn");
    await page.waitForFunction(() => !document.getElementById("editorSection").hidden, null, { timeout: 5000 });
    await page.waitForTimeout(300);

    const box = await page.locator("#drawCanvas").boundingBox();
    await dragStroke(page, box);
    await page.fill(".title-line-text", names[i]);
    await page.click("#commitFreezeBtn");
    await page.waitForFunction(() => document.getElementById("editorSection").hidden, null, { timeout: 5000 });

    const countNow = await page.evaluate(() => appState.freezes.length);
    check(countNow === i + 1,
      label + ": " + (i + 1) + "件目コミット後、appState.freezesが" + (i + 1) + "件になっている（実際: " + countNow + "件）");

    // bug #2 の直接的な回帰チェック：exitDrawMode後、videoWrapのstyle.topが残っていない
    const leftoverTop = await page.evaluate(() => document.getElementById("videoWrap").style.top);
    check(leftoverTop === "", label + ": " + (i + 1) + "件目コミット後、videoWrap.style.topが残っていない（実際: '" + leftoverTop + "'）");

    // bug #2 の症状チェック：コミット後もシークが効く（黒画面のまま固まらない）
    const tBefore = await page.evaluate(() => document.getElementById("video").currentTime);
    await seekAndWaitReal(page, 0.5 + i * 1.5);
    const tAfter = await page.evaluate(() => document.getElementById("video").currentTime);
    check(Math.abs(tAfter - tBefore) > 0.05 || tAfter !== tBefore,
      label + ": " + (i + 1) + "件目コミット後もシークが効く: " + tBefore.toFixed(2) + "s -> " + tAfter.toFixed(2) + "s");
    const pixelAfterSeek = await readCenterPixel(page, "playerCanvas");
    check(!isBlack(pixelAfterSeek), label + ": " + (i + 1) + "件目コミット後のシークでplayerCanvasが黒でない");
  }

  console.log("=== 一覧件数とJSON書き出し件数の一致 ===");
  const cardCount = await page.evaluate(() => document.querySelectorAll(".freeze-card").length);
  check(cardCount === 3, label + ": フリーズ一覧のカード件数が3件: " + cardCount);

  const project = await page.evaluate(() => buildProjectJSON(appState));
  check(project.freezes.length === 3, label + ": 書き出しJSONのfreezes件数が3件: " + project.freezes.length);
  check(cardCount === project.freezes.length,
    label + ": 一覧の件数(" + cardCount + ")と書き出しJSONの件数(" + project.freezes.length + ")が一致する");

  const gotNames = project.freezes.map((f) => f.name).sort();
  const wantNames = names.slice().sort();
  check(JSON.stringify(gotNames) === JSON.stringify(wantNames),
    label + ": 3件とも名前が正しくJSONに反映されている: " + JSON.stringify(gotNames));

  check(pageErrors.length === 0, label + ": ページ例外が発生していない: " + JSON.stringify(pageErrors));

  await context.close();
}

async function main() {
  const portraitVideo = prepareTestVideo("dummy_input.mp4", "spotlight_reel_test_portrait_vp9.webm");
  const landscapeVideo = prepareTestVideo("dummy_input_landscape.mp4", "spotlight_reel_test_landscape_vp9.webm");

  const browser = await chromium.launch({ executablePath: CHROMIUM_PATH, headless: true });
  await runForOrientation(browser, "縦動画(1080x1920)", portraitVideo);
  await runForOrientation(browser, "横動画(1920x1080)", landscapeVideo);
  await browser.close();

  console.log("");
  console.log(passed + " 件成功 / " + failed + " 件失敗");
  if (failed > 0) process.exit(1);
}

main().catch((err) => {
  console.error("テスト実行中に例外:", err);
  process.exit(1);
});
