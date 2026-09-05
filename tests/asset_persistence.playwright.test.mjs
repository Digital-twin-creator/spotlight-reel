#!/usr/bin/env node
// -*- coding: utf-8 -*-
//
// 「透かしロゴ(watermark)を無効にしても『動画を作る』が行き止まりでブロックされ続ける」
// バグの修正を検証する回帰テスト。
//
// 検証内容:
//   1. ロゴ/透かしロゴの画像はIndexedDBに保存され、ページのリロード後（同じ動画を
//      選び直した後）も、ファイルを選び直すことなく自動的に復元される。
//   2. IndexedDBに実体が見つからない場合（別ブラウザ・保存領域クリア等を想定し、
//      IndexedDB自体を消して再現する）は、「動画を作る」を行き止まりにせず、
//      「画像を選ぶ」「オフ（削除）にして進む」のどちらかをその場で選べるモーダルを出す。
//   3. 透かしロゴをオフにして進む/ロゴ設定を削除して進むを選ぶと、そのアセットは
//      以後ブロック要因にならず（JSONからも除外され）、「動画を作る」の処理が
//      次のステップ（GitHub連携設定チェック）まで進む。
//
// 実行方法:
//   npm install --no-save playwright-core
//   python3 -m http.server 8794 &
//   node tests/asset_persistence.playwright.test.mjs
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

async function loadVideo(page, videoPath) {
  await page.goto(BASE_URL, { waitUntil: "load" });
  await page.click("#guideCloseBtn").catch(() => {});
  await page.setInputFiles("#videoFileInput", videoPath);
  await page.waitForFunction(() => document.getElementById("video").duration > 0, null, { timeout: 10000 });
}

/** ページ再読み込み後、同じ動画を選び直して復元処理(loadFromLocalStorage/IndexedDB)を走らせる */
async function reloadAndReselectVideo(page, videoPath) {
  await page.reload({ waitUntil: "load" });
  await page.click("#guideCloseBtn").catch(() => {});
  await page.setInputFiles("#videoFileInput", videoPath);
  await page.waitForFunction(() => document.getElementById("video").duration > 0, null, { timeout: 10000 });
}

async function deleteImageAssetDb(page) {
  await page.evaluate(() => new Promise((resolve, reject) => {
    const req = indexedDB.deleteDatabase("spotlight_reel_image_assets");
    req.onsuccess = () => resolve();
    req.onerror = () => reject(req.error);
    req.onblocked = () => resolve(); // 他に開いているハンドルが無い前提だが、念のため
  }));
}

async function main() {
  const videoPath = prepareTestVideo();
  const logoPath = path.join(REPO_ROOT, "examples", "store_logo.png");
  const iphone = devices["iPhone 13"];
  const browser = await chromium.launch({ executablePath: CHROMIUM_PATH, headless: true });
  const context = await browser.newContext({ ...iphone });
  const page = await context.newPage();
  const pageErrors = [];
  page.on("pageerror", (e) => pageErrors.push(e.message));

  console.log("=== シナリオ1: ロゴ・透かしロゴの画像をIndexedDBに保存し、リロード後も選び直し不要で復元される ===");
  {
    await loadVideo(page, videoPath);

    // ラストロゴを設定
    await page.evaluate(() => { document.getElementById("logoSection").open = true; });
    await page.setInputFiles("#logoFileInput", logoPath);
    await page.waitForFunction(
      () => document.getElementById("logoFileName").textContent.indexOf("選択中") >= 0,
      null, { timeout: 5000 }
    );

    // 透かしロゴを有効化して画像を設定
    await page.evaluate(() => { document.getElementById("watermarkSection").open = true; });
    await page.click("#watermarkEnabledCheckbox");
    await page.setInputFiles("#watermarkFileInput", logoPath);
    await page.waitForFunction(
      () => document.getElementById("watermarkFileName").textContent.indexOf("選択中") >= 0,
      null, { timeout: 5000 }
    );

    // IndexedDBへの保存（imageAssetSave、fire-and-forget）が完了するのを待つ
    await page.waitForFunction(async () => {
      const logoRec = await imageAssetGet("logo");
      const wmRec = await imageAssetGet("watermark");
      return !!(logoRec && logoRec.blob) && !!(wmRec && wmRec.blob);
    }, null, { timeout: 5000 });
    check(true, "ロゴ・透かしロゴの画像がIndexedDBに保存された");

    const jsonBeforeReload = await page.evaluate(() => currentProjectJSONText());
    check(jsonBeforeReload.indexOf('"watermark"') >= 0 && jsonBeforeReload.indexOf('"logo"') >= 0,
      "リロード前: 有効な透かしロゴ・ロゴがproject.jsonに含まれる");

    // リロード（appStateはリセットされ、Fileはメモリから失われる）→ 同じ動画を選び直す
    await reloadAndReselectVideo(page, videoPath);

    // localStorage復元 + IndexedDB復元（restoreImageAssetsFromIndexedDB）を待つ
    await page.waitForFunction(
      () => document.getElementById("logoFileName").textContent.indexOf("選択中") >= 0,
      null, { timeout: 5000 }
    );
    await page.waitForFunction(
      () => document.getElementById("watermarkFileName").textContent.indexOf("選択中") >= 0,
      null, { timeout: 5000 }
    );
    check(true, "リロード後、ロゴ画像を選び直さなくても「選択中」表示に自動復元される");
    check(true, "リロード後、透かしロゴ画像を選び直さなくても「選択中」表示に自動復元される");

    const watermarkEnabledAfterReload = await page.evaluate(() => appState.watermark.enabled);
    check(watermarkEnabledAfterReload === true, "リロード後も透かしロゴの有効状態(enabled)が維持される");

    // 「動画を作る」を押しても、画像未選択の警告（行き止まり）は出ない
    await page.click("#makeVideoBtn");
    await page.waitForFunction(
      () => document.getElementById("jobStatusLine").textContent.indexOf("GitHub連携の設定") >= 0,
      null, { timeout: 5000 }
    );
    const missingModalHiddenAfterClick = await page.evaluate(() => document.getElementById("missingAssetModal").hidden);
    check(missingModalHiddenAfterClick === true,
      "リロード後に「動画を作る」を押しても画像未選択モーダルは出ず、次のGitHub設定チェックまで進む");
  }

  console.log("=== シナリオ2: IndexedDBに実体が無い場合（別端末相当）、行き止まりにせず選択肢を提示する ===");
  {
    // シナリオ1の続き（appStateにはlogo/watermarkのimageNameが残っている）から、
    // IndexedDB自体を丸ごと消して「別端末・保存領域クリア」を再現する
    await deleteImageAssetDb(page);
    await reloadAndReselectVideo(page, videoPath);

    // localStorageのimageNameは残るがIndexedDBには実体が無いため、Fileは復元されない
    await page.waitForFunction(
      () => document.getElementById("logoFileName").textContent.indexOf("選び直してください") >= 0,
      null, { timeout: 5000 }
    );
    check(true, "IndexedDBに実体が無い場合はFileが復元されず「選び直してください」表示のまま");

    // 「動画を作る」→ ロゴの画像未選択モーダルが行き止まりにならず出る
    await page.click("#makeVideoBtn");
    await page.waitForFunction(
      () => document.getElementById("missingAssetModal").hidden === false,
      null, { timeout: 5000 }
    );
    const logoModalDisableText = await page.evaluate(() => document.getElementById("missingAssetDisableBtn").textContent);
    const logoModalHasPickBtn = await page.evaluate(() => !document.getElementById("missingAssetPickBtn").hidden);
    check(logoModalDisableText.indexOf("ロゴ設定を削除") >= 0,
      "ロゴの画像未選択モーダルに「ロゴ設定を削除して進む」ボタンがある: " + logoModalDisableText);
    check(logoModalHasPickBtn, "ロゴの画像未選択モーダルに「画像を選ぶ」ボタンも併記されている（行き止まりではない）");

    // 「ロゴ設定を削除して進む」→ ロゴはブロック要因でなくなり、続けて透かしロゴのモーダルが出る
    await page.click("#missingAssetDisableBtn");
    await page.waitForFunction(
      () => document.getElementById("missingAssetModal").hidden === false,
      null, { timeout: 5000 }
    );
    const logoCleared = await page.evaluate(() => appState.logo.imageName === "");
    const watermarkModalDisableText = await page.evaluate(() => document.getElementById("missingAssetDisableBtn").textContent);
    check(logoCleared, "「ロゴ設定を削除して進む」でロゴ設定がクリアされる");
    check(watermarkModalDisableText.indexOf("透かしをオフにして進む") >= 0,
      "続けて透かしロゴの画像未選択モーダルが出て「透かしをオフにして進む」ボタンがある: " + watermarkModalDisableText);

    // 「透かしをオフにして進む」→ 警告が消え、そのまま次のGitHub設定チェックまで進む（行き止まりにならない）
    await page.click("#missingAssetDisableBtn");
    await page.waitForFunction(
      () => document.getElementById("jobStatusLine").textContent.indexOf("GitHub連携の設定") >= 0,
      null, { timeout: 5000 }
    );
    const watermarkDisabledNow = await page.evaluate(() => appState.watermark.enabled === false);
    const finalModalHidden = await page.evaluate(() => document.getElementById("missingAssetModal").hidden);
    check(watermarkDisabledNow, "「透かしをオフにして進む」で透かしロゴが無効化される");
    check(finalModalHidden, "モーダルが閉じ、警告が行き止まりにならず「動画を作る」の処理が先へ進む");

    const jsonAfterDisable = await page.evaluate(() => currentProjectJSONText());
    check(jsonAfterDisable.indexOf('"watermark"') < 0, "透かしオフ後、project.jsonからwatermarkキーが除外される");
    check(jsonAfterDisable.indexOf('"logo"') < 0, "ロゴ設定削除後、project.jsonからlogoキーが除外される");
  }

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
