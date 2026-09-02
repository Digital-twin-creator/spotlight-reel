#!/usr/bin/env node
// -*- coding: utf-8 -*-
//
// 「＋フリーズ追加」をタップすると映像エリアが真っ黒になり戻らない、という
// 実機iOS Safariの不具合に対する回帰テスト。
// playerCanvasから静止フレームをコピーする方式に変更したことで、
//   1. フリーズ追加直後、playerCanvas（フリーズ表示に使われる静止フレーム）の
//      中央画素が黒でないこと
//   2. キャンセルで描画モードを抜けたあと、映像が再び動く（=黒画面のまま
//      固まらない）こと
// を検証する。
//
// 実行方法:
//   npm install --no-save playwright-core
//   node tests/freeze_black_screen.playwright.test.mjs
//
// 環境変数 PW_URL / PW_CHROMIUM_PATH / PW_VIDEO は player_ui.playwright.test.mjs と同じ。

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
  await page.waitForTimeout(200); // playerCanvasにフレームが乗るのを待つ

  console.log("=== フリーズ追加 → playerCanvasが黒くならない ===");
  await page.click("#addFreezeBtn");
  await page.waitForFunction(() => !document.getElementById("editorSection").hidden, null, { timeout: 5000 });
  await page.waitForTimeout(300); // captureFrozenFrameの取得・再試行分の余裕

  const pixelAfterFreeze = await readCenterPixel(page, "playerCanvas");
  console.log("  フリーズ追加直後のplayerCanvas中央画素:", pixelAfterFreeze);
  check(!isBlack(pixelAfterFreeze), "フリーズ追加直後、playerCanvasの中央画素が黒でない（＝静止フレームが表示されている）");

  const fixedBarVisible = await page.evaluate(() => {
    const bar = document.getElementById("fixedEditorBar");
    const r = bar.getBoundingClientRect();
    return r.width > 0 && r.height > 0 && getComputedStyle(bar).position === "fixed";
  });
  check(fixedBarVisible, "キャンセル/完了ボタンがposition:fixedで表示されている");

  // 数フレーム分待って、playerCanvasが黒く上書きされていないこと（＝ライブ描画ループが
  // 描画モード中は video を描いていないこと）も確認する
  await page.waitForTimeout(500);
  const pixelStillGood = await readCenterPixel(page, "playerCanvas");
  check(!isBlack(pixelStillGood), "描画モード中もしばらく待って黒に変わらない（フリーズ表示が維持される）");

  console.log("");
  console.log("=== キャンセル → 映像が再び動く ===");
  const tBeforeCancel = await page.evaluate(() => document.getElementById("video").currentTime);
  await page.click("#cancelFreezeBtn");
  await page.waitForFunction(() => document.getElementById("editorSection").hidden, null, { timeout: 5000 });

  const editingClassGone = await page.evaluate(() => !document.getElementById("videoWrap").classList.contains("editing"));
  check(editingClassGone, "キャンセル後、video-wrapからeditingクラスが外れる");

  // 再生してcurrentTimeが実際に進むこと（＝黒画面のまま固まっていない証拠）
  await page.click("#playPauseBtn");
  await page.waitForFunction(() => !document.getElementById("video").paused, null, { timeout: 3000 });
  await page.waitForTimeout(600);
  const tAfterCancel = await page.evaluate(() => document.getElementById("video").currentTime);
  check(tAfterCancel > tBeforeCancel, "キャンセル後に再生すると currentTime が進む: " +
    tBeforeCancel.toFixed(2) + "s -> " + tAfterCancel.toFixed(2) + "s");

  const pixelAfterCancel = await readCenterPixel(page, "playerCanvas");
  check(!isBlack(pixelAfterCancel), "キャンセル後、playerCanvasが再びライブ映像を描いている（黒でない）");
  await page.click("#playPauseBtn");

  console.log("");
  console.log("=== 完了 → 再びフリーズ追加してもplayerCanvasが黒くならない（連続実行の確認） ===");
  await page.evaluate(() => new Promise((resolve) => {
    const v = document.getElementById("video");
    v.addEventListener("seeked", resolve, { once: true });
    v.currentTime = 4.0;
    setTimeout(resolve, 500);
  }));
  await page.waitForTimeout(200);
  await page.click("#addFreezeBtn");
  await page.waitForFunction(() => !document.getElementById("editorSection").hidden, null, { timeout: 5000 });
  await page.waitForTimeout(300);
  const pixelSecondFreeze = await readCenterPixel(page, "playerCanvas");
  check(!isBlack(pixelSecondFreeze), "2回目のフリーズ追加でもplayerCanvasが黒くならない");
  await page.fill(".title-line-text", "黒画面回帰テスト");
  await page.click("#commitFreezeBtn");
  await page.waitForFunction(() => document.getElementById("editorSection").hidden, null, { timeout: 5000 });
  const freezeCount = await page.evaluate(() => appState.freezes.length);
  check(freezeCount === 1, "完了後、フリーズが1件記録されている: " + freezeCount);
  const thumbOk = await page.evaluate(() => appState.freezes[0].thumb && appState.freezes[0].thumb.length > 100);
  check(thumbOk, "サムネイルがfrozenFrameCanvasから正しく生成されている");

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
