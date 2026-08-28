#!/usr/bin/env node
// -*- coding: utf-8 -*-
//
// index.html の再生/停止トグル・シークが実際に動画を動かすかを、
// headless Chromium + iPhoneデバイスエミュレーションで検証する回帰テスト。
//
// これは tests/editor_logic.test.js（依存なしで動く純粋ロジックのテスト）とは違い、
// playwright-core と（プロキシ経由で取得できる）Chromiumバイナリが必要な、
// 手動実行用の任意テストです。index.html 自体はビルド・依存なしのまま。
//
// 実行方法:
//   npm install --no-save playwright-core
//   node tests/player_ui.playwright.test.mjs
//
// 環境変数:
//   PW_URL             index.html を配信しているURL（既定: http://127.0.0.1:8794/index.html）
//   PW_CHROMIUM_PATH   Chromium実行ファイルのパス
//   PW_VIDEO           テストに使う動画ファイル（VP9/WebM推奨。既定はexamples配下から自動生成）
//
// 注意: このヘッドレスChromiumビルドはH.264を再生できないため、
// examples/dummy_input.mp4 をそのまま使えない場合はVP9へ変換したものを使う。
// 実WebKit(Safari)は取得できない環境のため、iPhoneデバイスエミュレーション
// （ビューポート/タッチ/UA）で代替している。

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

  console.log("=== シナリオ1: pause → play → currentTime が進む ===");
  {
    // まず再生し、確実に再生状態にしてから停止する
    await page.click("#playPauseBtn");
    await page.waitForFunction(() => !document.getElementById("video").paused, null, { timeout: 3000 });
    await page.waitForTimeout(400);

    // 停止（ここがバグの引き金だった：以前は以後 play() が効かなくなっていた）
    await page.click("#playPauseBtn");
    const pausedState = await page.evaluate(() => document.getElementById("video").paused);
    check(pausedState === true, "「停止」タップ後、video.pausedがtrueになる");

    const tBeforeReplay = await page.evaluate(() => document.getElementById("video").currentTime);

    // 再度「再生」をタップ → これが実機で効かなくなっていた操作
    await page.click("#playPauseBtn");
    await page.waitForTimeout(150);
    const playingAgain = await page.evaluate(() => !document.getElementById("video").paused);
    check(playingAgain, "停止後に再び「▶ 再生」をタップすると video.paused が false になる");

    await page.waitForTimeout(600);
    const tAfterReplay = await page.evaluate(() => document.getElementById("video").currentTime);
    check(tAfterReplay > tBeforeReplay,
      "停止→再生後、currentTime が実際に進む: " + tBeforeReplay.toFixed(2) + "s -> " + tAfterReplay.toFixed(2) + "s");

    // 状態バーの表示も正しく追従しているか
    const playStatus = await page.evaluate(() => document.getElementById("playStatusChip").textContent);
    check(playStatus.indexOf("再生中") >= 0, "状態バーが「再生中」を示す: " + playStatus);

    await page.click("#playPauseBtn"); // 後続テストのため停止しておく
    await page.waitForTimeout(200);
  }

  console.log("");
  console.log("=== シナリオ2: シークバー操作 → currentTime と canvas 中央画素が変わる ===");
  {
    await page.evaluate(() => new Promise((resolve) => {
      const v = document.getElementById("video");
      v.addEventListener("seeked", resolve, { once: true });
      v.currentTime = 0.5;
      setTimeout(resolve, 500);
    }));
    await page.waitForTimeout(150);

    function readCenterPixel() {
      return page.evaluate(() => {
        const pc = document.getElementById("playerCanvas");
        const ctx = pc.getContext("2d");
        const x = Math.floor(pc.width / 2), y = Math.floor(pc.height / 2);
        return Array.from(ctx.getImageData(x, y, 1, 1).data);
      });
    }

    const tBefore = await page.evaluate(() => document.getElementById("video").currentTime);
    const pixelBefore = await readCenterPixel();

    // シークバーをドラッグではなく、実際のUI操作として input イベントを伴う値変更で6秒付近へ
    const seekBarBox = await page.locator("#seekBar").boundingBox();
    await page.mouse.click(seekBarBox.x + seekBarBox.width * 0.75, seekBarBox.y + seekBarBox.height / 2);
    await page.waitForTimeout(300); // シーク中フラグ等でブロックされていないことも兼ねて確認

    const tAfter = await page.evaluate(() => document.getElementById("video").currentTime);
    check(Math.abs(tAfter - tBefore) > 1.0,
      "シークバー操作で currentTime が大きく変わる: " + tBefore.toFixed(2) + "s -> " + tAfter.toFixed(2) + "s");

    await page.waitForTimeout(200); // rAFループが新しいフレームを描くのを待つ
    const pixelAfter = await readCenterPixel();
    const changed = pixelBefore.some((v, i) => Math.abs(v - pixelAfter[i]) > 5);
    check(changed, "シーク後、playerCanvas中央画素が変化する（新しいフレームが描かれている）: " +
      JSON.stringify(pixelBefore) + " -> " + JSON.stringify(pixelAfter));

    // 微調整ボタンも同じ経路(currentTime直接代入)で動くこと
    const t1 = await page.evaluate(() => document.getElementById("video").currentTime);
    await page.click('[data-seek="1"]');
    await page.waitForTimeout(100);
    const t2 = await page.evaluate(() => document.getElementById("video").currentTime);
    check(Math.abs(t2 - t1 - 1) < 0.05, "+1秒ボタンでcurrentTimeがちょうど1秒進む: " + t1.toFixed(2) + " -> " + t2.toFixed(2));

    await page.click('[data-seek="-0.1"]');
    await page.waitForTimeout(100);
    const t3 = await page.evaluate(() => document.getElementById("video").currentTime);
    check(Math.abs(t3 - (t2 - 0.1)) < 0.05, "-0.1秒ボタンでcurrentTimeがちょうど0.1秒戻る: " + t2.toFixed(2) + " -> " + t3.toFixed(2));
  }

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
