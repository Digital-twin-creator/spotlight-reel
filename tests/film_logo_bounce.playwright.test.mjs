#!/usr/bin/env node
// -*- coding: utf-8 -*-
//
// 「演出追加：影（フィルム色）／テロップバウンス／ラストロゴ」で
// index.html に追加したUI（全体設定のfreeze_sec・title_bounce、ラストロゴのファイル選択）が、
// 実際に project.json の契約どおりのキーを書き出すことを、
// headless Chromium + iPhoneデバイスエミュレーションで検証する回帰テスト。
// 影（フィルム色）まわりのUI（色プリセット・濃さ・ズレ距離・方向・ぼかし）は
// tests/mask_shadow_ui.playwright.test.mjs で検証する。
//
// 実行方法:
//   npm install --no-save playwright-core
//   python3 -m http.server 8794 &
//   node tests/film_logo_bounce.playwright.test.mjs
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

async function main() {
  const videoPath = prepareTestVideo();
  const logoPath = path.join(REPO_ROOT, "examples", "store_logo.png");
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

  // 全体設定・ラストロゴは <details class="card"> の中にあり既定で閉じているため、先に開く
  await page.evaluate(() => {
    document.getElementById("settingsSection").open = true;
    document.getElementById("logoSection").open = true;
  });

  console.log("=== 全体設定：freeze_secの既定値・title_bounce ===");
  check(await page.inputValue("#freezeSecInput") === "1.2",
    "freeze_secの入力欄の既定値が1.2になっている: " + (await page.inputValue("#freezeSecInput")));

  check(await page.$("#filmColorOverrideSelect") === null,
    "フリーズ単位のフィルム色上書きセレクトは廃止され存在しない（影は全体設定に一本化）");
  check(await page.$("#filmOffsetSlider") === null,
    "旧フィルム縁取りズレ量スライダーは廃止され存在しない（影のズレ距離に統合）");

  await page.check("#titleBounceCheckbox");

  let project = await page.evaluate(() => buildProjectJSON(appState));
  check(project.style.title_bounce === true, "title_boundeチェックボックスがJSONに反映される: " + project.style.title_bounce);

  console.log("");
  console.log("=== フリーズを追加してもfilm_color系のキーは出力されない（旧・per-freeze上書きの完全撤去） ===");
  await page.click("#addFreezeBtn");
  await page.waitForFunction(() => !document.getElementById("editorSection").hidden, null, { timeout: 5000 });
  await page.fill("#nameInput", "継承テスト");
  await page.click("#commitFreezeBtn");
  await page.waitForFunction(() => document.getElementById("editorSection").hidden, null, { timeout: 5000 });
  project = await page.evaluate(() => buildProjectJSON(appState));
  const inheritedFreeze = project.freezes.filter((f) => f.name === "継承テスト")[0];
  check(inheritedFreeze && !("film_color" in inheritedFreeze),
    "フリーズはfilm_colorキーを持たない（影は全体設定 style.shadow のみに一本化）");

  console.log("");
  console.log("=== ラストロゴ：画像選択でlogoブロックが組み立てられる ===");
  check(await page.textContent("#logoFileName") === "まだロゴ画像が選ばれていません",
    "ロゴ未選択時の表示文言");
  check(await page.inputValue("#logoDurationSlider") === "2.2",
    "duration_secスライダーの既定値が2.2秒（以前は1.2秒）: " + (await page.inputValue("#logoDurationSlider")));

  await page.setInputFiles("#logoFileInput", logoPath);
  await page.selectOption("#logoAtSelect", "end");
  await page.fill("#logoDurationSlider", "2");
  await page.dispatchEvent("#logoDurationSlider", "input");
  await page.selectOption("#logoSfxSelect", "don");

  project = await page.evaluate(() => buildProjectJSON(appState));
  check(!!project.logo, "logo画像を選ぶとJSONにlogoブロックが現れる");
  check(project.logo && project.logo.image === "logo.png",
    "logo.imageが固定名logo.<ext>になる（元ファイル名に依存しない）: " + (project.logo && project.logo.image));
  check(project.logo && project.logo.at === "end", "logo.atが選択どおりになる: " + (project.logo && project.logo.at));
  check(project.logo && project.logo.background === "auto", "logo.backgroundの既定値がauto: " + (project.logo && project.logo.background));
  check(project.logo && project.logo.duration_sec === 2, "logo.duration_secがスライダー操作どおりになる: " + (project.logo && project.logo.duration_sec));
  check(project.logo && project.logo.sfx === "don", "logo.sfxが選択どおりになる: " + (project.logo && project.logo.sfx));

  console.log("");
  console.log("=== ラストロゴ：背景モード（自動検出色／動画に重ねる／色指定） ===");
  await page.waitForFunction(() => {
    var el = document.getElementById("logoColorHint");
    return el && el.textContent.indexOf("検出色:") === 0;
  }, null, { timeout: 5000 });
  const detectedHint = await page.textContent("#logoColorHint");
  check(/^検出色: #[0-9A-Fa-f]{6}$/.test(detectedHint),
    "ロゴ選択後、自動検出した色の見本(#RRGGBBのヒント)が表示される: " + detectedHint);

  await page.selectOption("#logoBackgroundSelect", "video");
  check(await page.isHidden("#logoColorRow"), "background='動画に重ねる'選択時は色スウォッチ行が隠れる");
  project = await page.evaluate(() => buildProjectJSON(appState));
  check(project.logo.background === "video", "background選択が'video'としてJSONに反映される: " + project.logo.background);

  await page.selectOption("#logoBackgroundSelect", "color");
  await page.fill("#logoColorInput", "#112233");
  await page.dispatchEvent("#logoColorInput", "input");
  project = await page.evaluate(() => buildProjectJSON(appState));
  check(project.logo.background === "#112233", "色指定モードで選んだ色がJSONに反映される: " + project.logo.background);

  await page.selectOption("#logoBackgroundSelect", "auto");
  project = await page.evaluate(() => buildProjectJSON(appState));
  check(project.logo.background === "auto", "'auto'に戻すとbackgroundが'auto'になる: " + project.logo.background);

  console.log("");
  console.log("=== ラストロゴ：設定を削除するとlogoブロックが消える ===");
  await page.click("#clearLogoBtn");
  project = await page.evaluate(() => buildProjectJSON(appState));
  check(project.logo === undefined, "「ロゴ設定を削除」を押すとlogoブロックが出力されなくなる");
  check(await page.textContent("#logoFileName") === "まだロゴ画像が選ばれていません",
    "削除後の表示文言が未選択状態に戻る");

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
