#!/usr/bin/env node
// -*- coding: utf-8 -*-
//
// index.html の「切り抜き方法（ブラシ／自動／自動＋ブラシ修正）」セレクタ、
// auto+brush時の「足す／消す」ブラシトグル、全体設定の「影」「reveal」が、
// render.py の契約どおりのJSON（mask/mode/style.shadow/style.reveal）を
// 作れることを、headless Chromium + iPhoneデバイスエミュレーションで検証する回帰テスト。
//
// 実行方法:
//   npm install --no-save playwright-core
//   python3 -m http.server 8794 &
//   node tests/mask_shadow_ui.playwright.test.mjs
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

/** drawCanvas上でマウスドラッグして1本ストロークを描く（x0/y0〜x1/y1は矩形に対する比率） */
async function dragStroke(page, box, rx0, ry0, rx1, ry1) {
  const x0 = box.x + box.width * rx0, y0 = box.y + box.height * ry0;
  const x1 = box.x + box.width * rx1, y1 = box.y + box.height * ry1;
  await page.mouse.move(x0, y0);
  await page.mouse.down();
  for (let i = 1; i <= 8; i++) {
    const t = i / 8;
    await page.mouse.move(x0 + (x1 - x0) * t, y0 + (y1 - y0) * t);
  }
  await page.mouse.up();
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

  console.log("=== 切り抜き方法セレクタの基本動作 ===");
  await page.click("#addFreezeBtn");
  await page.waitForFunction(() => !document.getElementById("editorSection").hidden, null, { timeout: 5000 });

  const maskOptions = await page.$$eval("#maskModeSelect option", (opts) => opts.map((o) => o.value));
  check(JSON.stringify(maskOptions) === JSON.stringify(["brush", "auto", "auto+brush"]),
    "切り抜き方法の選択肢が brush/auto/auto+brush の3つ（この順）: " + JSON.stringify(maskOptions));
  check((await page.inputValue("#maskModeSelect")) === "brush", "初期状態は brush");
  check(await page.isHidden("#autoMaskHint"), "brush時はautoMaskHintが隠れている");
  check(await page.isHidden("#brushEditModeRow"), "brush時はbrushEditModeRowが隠れている");

  await page.selectOption("#maskModeSelect", "auto");
  check(await page.isVisible("#autoMaskHint"), "auto選択時はautoMaskHint（なぞらなくてOK）が表示される");
  check(await page.isHidden("#brushEditModeRow"), "auto選択時はbrushEditModeRowが隠れたまま");

  await page.selectOption("#maskModeSelect", "auto+brush");
  check(await page.isHidden("#autoMaskHint"), "auto+brush選択時はautoMaskHintが隠れる");
  check(await page.isVisible("#brushEditModeRow"), "auto+brush選択時はbrushEditModeRow（足す/消す）が表示される");
  check(await page.locator("#brushModeAddBtn").evaluate((el) => el.classList.contains("selected")),
    "auto+brush選択直後は既定で「足す」が選択状態");

  console.log("");
  console.log("=== auto+brushで「足す」「消す」ストロークを描き、strokes[].modeに反映される ===");
  const box = await page.locator("#drawCanvas").boundingBox();
  await page.waitForTimeout(200);
  await dragStroke(page, box, 0.2, 0.2, 0.4, 0.3); // add（既定）
  await page.click("#brushModeEraseBtn");
  check(await page.locator("#brushModeEraseBtn").evaluate((el) => el.classList.contains("selected")),
    "「消す」ボタンを押すと選択状態になる");
  check(!(await page.locator("#brushModeAddBtn").evaluate((el) => el.classList.contains("selected"))),
    "「消す」を選ぶと「足す」の選択状態が外れる");
  await dragStroke(page, box, 0.6, 0.5, 0.8, 0.6); // erase

  const strokeModes = await page.evaluate(() => draft.strokes.map((s) => s.mode));
  check(JSON.stringify(strokeModes) === JSON.stringify(["add", "erase"]),
    "draft.strokesが描いた順に['add','erase']になる: " + JSON.stringify(strokeModes));

  await page.fill("#nameInput", "自動＋ブラシ修正テスト");
  await page.click("#commitFreezeBtn");
  await page.waitForFunction(() => document.getElementById("editorSection").hidden, null, { timeout: 5000 });

  let projectJson = await page.evaluate(() => buildProjectJSON(appState));
  let fz0 = projectJson.freezes[0];
  check(fz0.mask === "auto+brush", "書き出したJSONのfreezes[0].maskが'auto+brush': " + fz0.mask);
  check(fz0.strokes.length === 2 && fz0.strokes[0].mode === "add" && fz0.strokes[1].mode === "erase",
    "freezes[0].strokesのmodeが順に add/erase: " + JSON.stringify(fz0.strokes.map((s) => s.mode)));

  console.log("");
  console.log("=== 既定（brush）のフリーズはmask='brush'、strokesにmodeキーを持たない（後方互換） ===");
  await page.click("#addFreezeBtn");
  await page.waitForFunction(() => !document.getElementById("editorSection").hidden, null, { timeout: 5000 });
  const box2 = await page.locator("#drawCanvas").boundingBox();
  await page.waitForTimeout(200);
  await dragStroke(page, box2, 0.3, 0.3, 0.5, 0.4);
  await page.fill("#nameInput", "従来ブラシテスト");
  await page.click("#commitFreezeBtn");
  await page.waitForFunction(() => document.getElementById("editorSection").hidden, null, { timeout: 5000 });

  projectJson = await page.evaluate(() => buildProjectJSON(appState));
  const brushFz = projectJson.freezes.filter((f) => f.name === "従来ブラシテスト")[0];
  check(brushFz.mask === "brush", "brushフリーズのmaskが'brush': " + brushFz.mask);
  check(brushFz.strokes.length === 1 && !("mode" in brushFz.strokes[0]),
    "brushフリーズのストロークにmodeキーが付与されない（JSON出力が従来どおり）: " +
    JSON.stringify(brushFz.strokes[0]));

  console.log("");
  console.log("=== 再編集でmaskMode・ブラシ操作トグルが復元される ===");
  const autoBrushFreezeId = await page.evaluate(() =>
    appState.freezes.filter((f) => f.name === "自動＋ブラシ修正テスト")[0].id);
  await page.evaluate((id) => { editFreeze(id); }, autoBrushFreezeId);
  await page.waitForFunction(() => !document.getElementById("editorSection").hidden, null, { timeout: 5000 });
  check((await page.inputValue("#maskModeSelect")) === "auto+brush", "再編集時にmaskModeSelectが'auto+brush'に復元される");
  check(await page.isVisible("#brushEditModeRow"), "再編集時にbrushEditModeRowが表示される");
  await page.click("#cancelFreezeBtn");
  await page.waitForFunction(() => document.getElementById("editorSection").hidden, null, { timeout: 5000 });

  console.log("");
  console.log("=== 全体設定：影（shadow）のオン/オフとスライダー ===");
  // 全体設定は <details class="card"> の中にあり既定で閉じているため、先に開く
  await page.evaluate(() => { document.getElementById("settingsSection").open = true; });
  check(await page.isHidden("#shadowOptionsBody"), "影は既定でオフ（shadowOptionsBodyが隠れている）");
  let styleNoShadow = (await page.evaluate(() => buildProjectJSON(appState))).style;
  check(!("shadow" in styleNoShadow), "影オフ時はstyle.shadowキーが省略される");

  await page.check("#shadowEnabledCheckbox");
  check(await page.isVisible("#shadowOptionsBody"), "影オンでshadowOptionsBodyが表示される");
  await page.fill("#shadowOffsetSlider", "30");
  await page.dispatchEvent("#shadowOffsetSlider", "input");
  await page.fill("#shadowBlurSlider", "0.04");
  await page.dispatchEvent("#shadowBlurSlider", "input");
  await page.fill("#shadowAlphaSlider", "0.75");
  await page.dispatchEvent("#shadowAlphaSlider", "input");

  const styleWithShadow = (await page.evaluate(() => buildProjectJSON(appState))).style;
  check(!!styleWithShadow.shadow, "影オン時はstyle.shadowが出力される: " + JSON.stringify(styleWithShadow.shadow));
  check(styleWithShadow.shadow.blur === 0.04, "style.shadow.blurがスライダー値どおり: " + styleWithShadow.shadow.blur);
  check(styleWithShadow.shadow.alpha === 0.75, "style.shadow.alphaがスライダー値どおり: " + styleWithShadow.shadow.alpha);
  check(Array.isArray(styleWithShadow.shadow.offset) && styleWithShadow.shadow.offset.length === 2,
    "style.shadow.offsetが[x,y]の配列: " + JSON.stringify(styleWithShadow.shadow.offset));
  check(styleWithShadow.shadow.color === "#000000", "style.shadow.colorが既定の黒: " + styleWithShadow.shadow.color);

  console.log("");
  console.log("=== 全体設定：reveal（wipe/fade） ===");
  check((await page.inputValue("#revealSelect")) === "wipe", "revealSelectの既定はwipe");
  let styleDefaultReveal = (await page.evaluate(() => buildProjectJSON(appState))).style;
  check(styleDefaultReveal.reveal === "wipe", "既定のstyle.revealが'wipe': " + styleDefaultReveal.reveal);

  await page.selectOption("#revealSelect", "fade");
  const styleFadeReveal = (await page.evaluate(() => buildProjectJSON(appState))).style;
  check(styleFadeReveal.reveal === "fade", "revealSelectを'fade'にするとstyle.revealが'fade'になる: " + styleFadeReveal.reveal);

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
