#!/usr/bin/env node
// -*- coding: utf-8 -*-
//
// 「演出追加：フィルム色オフセット縁取り／テロップバウンス／ラストロゴ」で
// index.html に追加したUI（全体設定のフィルム縁取り・title_bounce、フリーズ単位の
// フィルム色上書き、ラストロゴのファイル選択）が、実際に project.json の
// 契約どおりのキーを書き出すことを、headless Chromium + iPhoneデバイスエミュレーションで
// 検証する回帰テスト。
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

  console.log("=== 全体設定：freeze_secの既定値・フィルム縁取り・title_bounce ===");
  check(await page.inputValue("#freezeSecInput") === "1.2",
    "freeze_secの入力欄の既定値が1.2になっている: " + (await page.inputValue("#freezeSecInput")));

  const presetLabels = await page.$$eval("#filmColorPresetRow button", (btns) => btns.map((b) => b.textContent.trim()));
  check(presetLabels.length === 4, "フィルム色プリセットが4色分表示される: " + JSON.stringify(presetLabels));

  await page.click('#filmColorPresetRow button[data-film-color="#00C8FF"]');
  await page.fill("#filmOffsetSlider", "8");
  await page.dispatchEvent("#filmOffsetSlider", "input");
  const offsetLabel = await page.textContent("#filmOffsetValue");
  check(offsetLabel === "8px", "ズレ量スライダーをpx単位で操作すると表示に反映される: " + offsetLabel);

  await page.check("#titleBounceCheckbox");

  let project = await page.evaluate(() => buildProjectJSON(appState));
  check(project.style.film_color === "#00C8FF", "全体設定のフィルム色プリセットがJSONに反映される: " + project.style.film_color);
  check(Math.abs(project.style.film_offset[0] - 8 / 1080) < 0.0005,
    "ズレ量(px)が出力幅比のfilm_offsetに変換される: " + JSON.stringify(project.style.film_offset));
  check(project.style.title_bounce === true, "title_boundeチェックボックスがJSONに反映される: " + project.style.title_bounce);

  console.log("");
  console.log("=== フリーズ単位のフィルム色上書き ===");
  await page.click("#addFreezeBtn");
  await page.waitForFunction(() => !document.getElementById("editorSection").hidden, null, { timeout: 5000 });

  const overrideOptions = await page.$$eval("#filmColorOverrideSelect option", (opts) => opts.map((o) => o.value));
  check(overrideOptions[0] === "", "フリーズ単位の上書きセレクトの先頭は「全体設定を使う」(空文字)");
  check(overrideOptions.length === 5, "フリーズ単位の上書きセレクトに4色分の選択肢がある: " + overrideOptions.length);

  await page.selectOption("#filmColorOverrideSelect", "#FF32C8");
  await page.fill("#nameInput", "縁取りテスト");
  await page.click("#commitFreezeBtn");
  await page.waitForFunction(() => document.getElementById("editorSection").hidden, null, { timeout: 5000 });

  project = await page.evaluate(() => buildProjectJSON(appState));
  check(project.freezes.length === 1 && project.freezes[0].film_color === "#FF32C8",
    "フリーズ単位で選んだ色がfreezes[0].film_colorに載る: " + JSON.stringify(project.freezes[0] && project.freezes[0].film_color));

  console.log("");
  console.log("=== 全体設定の色を上書きしていないフリーズはfilm_colorキーを持たない ===");
  await page.click("#addFreezeBtn");
  await page.waitForFunction(() => !document.getElementById("editorSection").hidden, null, { timeout: 5000 });
  const inheritedValue = await page.inputValue("#filmColorOverrideSelect");
  check(inheritedValue === "", "新規フリーズの上書きセレクトは既定で「全体設定を使う」: " + JSON.stringify(inheritedValue));
  await page.fill("#nameInput", "継承テスト");
  await page.click("#commitFreezeBtn");
  await page.waitForFunction(() => document.getElementById("editorSection").hidden, null, { timeout: 5000 });
  project = await page.evaluate(() => buildProjectJSON(appState));
  const inheritedFreeze = project.freezes.filter((f) => f.name === "継承テスト")[0];
  check(inheritedFreeze && !("film_color" in inheritedFreeze),
    "上書きしなかったフリーズはfilm_colorキーを持たない（全体設定を継承）");

  console.log("");
  console.log("=== ラストロゴ：画像選択でlogoブロックが組み立てられる ===");
  check(await page.textContent("#logoFileName") === "まだロゴ画像が選ばれていません",
    "ロゴ未選択時の表示文言");
  await page.setInputFiles("#logoFileInput", logoPath);
  await page.selectOption("#logoAtSelect", "end");
  await page.fill("#logoDurationInput", "2");
  await page.dispatchEvent("#logoDurationInput", "change");
  await page.selectOption("#logoSfxSelect", "don");

  project = await page.evaluate(() => buildProjectJSON(appState));
  check(!!project.logo, "logo画像を選ぶとJSONにlogoブロックが現れる");
  check(project.logo && project.logo.image === "logo.png",
    "logo.imageが固定名logo.<ext>になる（元ファイル名に依存しない）: " + (project.logo && project.logo.image));
  check(project.logo && project.logo.at === "end", "logo.atが選択どおりになる: " + (project.logo && project.logo.at));
  check(project.logo && project.logo.duration_sec === 2, "logo.duration_secが入力どおりになる: " + (project.logo && project.logo.duration_sec));
  check(project.logo && project.logo.sfx === "don", "logo.sfxが選択どおりになる: " + (project.logo && project.logo.sfx));

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
