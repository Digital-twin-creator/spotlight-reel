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
    document.getElementById("watermarkSection").open = true;
    document.getElementById("hashtagsSection").open = true;
  });

  console.log("=== 全体設定：①塗り②ズレ③静止（reveal_sec/slide_sec/hold_sec）の既定値・title_bounce ===");
  check(await page.inputValue("#revealSecSlider") === "0.5",
    "reveal_secの既定値が0.5になっている: " + (await page.inputValue("#revealSecSlider")));
  check(await page.inputValue("#slideSecSlider") === "0.5",
    "slide_secの既定値が0.5になっている: " + (await page.inputValue("#slideSecSlider")));
  check(await page.inputValue("#holdSecSlider") === "2",
    "hold_secの既定値が2.0になっている: " + (await page.inputValue("#holdSecSlider")));

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
  await page.fill(".title-line-text", "継承テスト");
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
  check(await page.inputValue("#logoStartWidthRatioSlider") === "1.6",
    "start_width_ratioスライダーの既定値が1.6（画面外に見切れる大きさ）: " + (await page.inputValue("#logoStartWidthRatioSlider")));
  check(await page.inputValue("#logoHoldBigSecSlider") === "0.15",
    "hold_big_secスライダーの既定値が0.15秒: " + (await page.inputValue("#logoHoldBigSecSlider")));
  check(await page.inputValue("#logoShrinkSecSlider") === "0.5",
    "shrink_secスライダーの既定値が0.5秒: " + (await page.inputValue("#logoShrinkSecSlider")));
  check(await page.inputValue("#logoSettleSecSlider") === "0.15",
    "settle_secスライダーの既定値が0.15秒: " + (await page.inputValue("#logoSettleSecSlider")));

  await page.setInputFiles("#logoFileInput", logoPath);
  await page.selectOption("#logoAtSelect", "end");
  await page.fill("#logoStartWidthRatioSlider", "2.1");
  await page.dispatchEvent("#logoStartWidthRatioSlider", "input");
  await page.fill("#logoHoldBigSecSlider", "0.3");
  await page.dispatchEvent("#logoHoldBigSecSlider", "input");
  await page.fill("#logoShrinkSecSlider", "0.8");
  await page.dispatchEvent("#logoShrinkSecSlider", "input");
  await page.fill("#logoSettleSecSlider", "0.25");
  await page.dispatchEvent("#logoSettleSecSlider", "input");
  await page.fill("#logoDurationSlider", "2");
  await page.dispatchEvent("#logoDurationSlider", "input");
  await page.selectOption("#logoSfxSelect", "don");

  project = await page.evaluate(() => buildProjectJSON(appState));
  check(!!project.logo, "logo画像を選ぶとJSONにlogoブロックが現れる");
  check(project.logo && project.logo.image === "logo.png",
    "logo.imageが固定名logo.<ext>になる（元ファイル名に依存しない）: " + (project.logo && project.logo.image));
  check(project.logo && project.logo.at === "end", "logo.atが選択どおりになる: " + (project.logo && project.logo.at));
  check(project.logo && project.logo.background === "auto", "logo.backgroundの既定値がauto: " + (project.logo && project.logo.background));
  check(project.logo && project.logo.start_width_ratio === 2.1,
    "logo.start_width_ratioがスライダー操作どおりになる: " + (project.logo && project.logo.start_width_ratio));
  check(project.logo && project.logo.hold_big_sec === 0.3,
    "logo.hold_big_secがスライダー操作どおりになる: " + (project.logo && project.logo.hold_big_sec));
  check(project.logo && project.logo.shrink_sec === 0.8,
    "logo.shrink_secがスライダー操作どおりになる: " + (project.logo && project.logo.shrink_sec));
  check(project.logo && project.logo.settle_sec === 0.25,
    "logo.settle_secがスライダー操作どおりになる: " + (project.logo && project.logo.settle_sec));
  check(project.logo && project.logo.duration_sec === 2, "logo.duration_secがスライダー操作どおりになる: " + (project.logo && project.logo.duration_sec));
  check(project.logo && project.logo.sfx === "don", "logo.sfxが選択どおりになる: " + (project.logo && project.logo.sfx));

  console.log("");
  console.log("=== ラストロゴ：背景を自動で透明化するチェックボックス・プレビュー ===");
  check(await page.isChecked("#logoAutoTransparentBgCheckbox"), "既定でauto_transparent_bgチェックボックスはONになっている");
  check(project.logo && project.logo.auto_transparent_bg === true,
    "既定ではlogo.auto_transparent_bg=trueがJSONに出力される");
  check(await page.isVisible("#logoAutoTransparentPreviewCanvas"),
    "画像選択後、背景自動透明化のプレビューcanvasが表示される");
  await page.uncheck("#logoAutoTransparentBgCheckbox");
  project = await page.evaluate(() => buildProjectJSON(appState));
  check(project.logo.auto_transparent_bg === false, "チェックを外すとlogo.auto_transparent_bg=falseになる");
  await page.check("#logoAutoTransparentBgCheckbox");
  project = await page.evaluate(() => buildProjectJSON(appState));
  check(project.logo.auto_transparent_bg === true, "チェックを戻すとlogo.auto_transparent_bg=trueに戻る");

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
  console.log("=== 透かしロゴ（watermark）：常時表示ONで画像選択・位置・サイズを設定するとJSONに反映される ===");
  check(await page.isHidden("#watermarkOptionsBody"), "既定では透かしロゴOFFなのでwatermarkOptionsBodyは隠れている");
  project = await page.evaluate(() => buildProjectJSON(appState));
  check(project.watermark === undefined, "透かしロゴが無効な間はJSONにwatermarkブロックが出ない");

  await page.check("#watermarkEnabledCheckbox");
  check(await page.isVisible("#watermarkOptionsBody"), "常時表示をONにするとwatermarkOptionsBodyが表示される");
  await page.setInputFiles("#watermarkFileInput", logoPath);
  await page.selectOption("#watermarkPositionSelect", "top_left");
  await page.fill("#watermarkWidthRatioSlider", "0.2");
  await page.dispatchEvent("#watermarkWidthRatioSlider", "input");
  await page.fill("#watermarkOpacitySlider", "0.6");
  await page.dispatchEvent("#watermarkOpacitySlider", "input");
  await page.fill("#watermarkMarginSlider", "0.05");
  await page.dispatchEvent("#watermarkMarginSlider", "input");

  project = await page.evaluate(() => buildProjectJSON(appState));
  check(!!project.watermark, "画像を選ぶとJSONにwatermarkブロックが現れる");
  check(project.watermark && project.watermark.image === "watermark.png",
    "watermark.imageが固定名watermark.<ext>になる: " + (project.watermark && project.watermark.image));
  check(project.watermark && project.watermark.position === "top_left",
    "watermark.positionが選択どおりになる: " + (project.watermark && project.watermark.position));
  check(project.watermark && project.watermark.width_ratio === 0.2,
    "watermark.width_ratioがスライダー操作どおりになる: " + (project.watermark && project.watermark.width_ratio));
  check(project.watermark && project.watermark.opacity === 0.6,
    "watermark.opacityがスライダー操作どおりになる: " + (project.watermark && project.watermark.opacity));
  check(project.watermark && project.watermark.margin === 0.05,
    "watermark.marginがスライダー操作どおりになる: " + (project.watermark && project.watermark.margin));
  check(project.watermark && project.watermark.shine && project.watermark.shine.enabled === true,
    "watermark.shine.enabledの既定はtrue");
  check(project.watermark && project.watermark.spin && project.watermark.spin.enabled === true,
    "watermark.spin.enabledの既定はtrue");

  console.log("");
  console.log("=== 透かしロゴ：shine/spinをそれぞれ個別にOFFにでき、周期・長さも変更できる ===");
  await page.uncheck("#watermarkShineEnabledCheckbox");
  await page.fill("#watermarkSpinIntervalSlider", "5");
  await page.dispatchEvent("#watermarkSpinIntervalSlider", "input");
  await page.fill("#watermarkSpinSecSlider", "1.5");
  await page.dispatchEvent("#watermarkSpinSecSlider", "input");
  project = await page.evaluate(() => buildProjectJSON(appState));
  check(project.watermark.shine.enabled === false, "shineのチェックを外すとwatermark.shine.enabled=falseになる");
  check(project.watermark.spin.interval_sec === 5,
    "spinの周期スライダーがwatermark.spin.interval_secに反映される: " + project.watermark.spin.interval_sec);
  check(project.watermark.spin.sec === 1.5,
    "spinの長さスライダーがwatermark.spin.secに反映される: " + project.watermark.spin.sec);

  console.log("");
  console.log("=== 透かしロゴ：「ラストロゴの画像を流用する」ボタンでlogo画像をそのままwatermarkにも使える ===");
  await page.click("#reuseLastLogoForWatermarkBtn");
  project = await page.evaluate(() => buildProjectJSON(appState));
  check(project.watermark && project.watermark.image === "watermark.png",
    "流用ボタンでもwatermark.imageは固定名watermark.<ext>のまま: " + (project.watermark && project.watermark.image));
  const watermarkFileNameAfterReuse = await page.textContent("#watermarkFileName");
  check(watermarkFileNameAfterReuse.indexOf("流用") >= 0,
    "流用したことが分かる表示文言になる: " + watermarkFileNameAfterReuse);

  console.log("");
  console.log("=== 透かしロゴ：背景を自動で透明化するチェックボックス・プレビュー ===");
  check(await page.isChecked("#watermarkAutoTransparentBgCheckbox"),
    "既定でauto_transparent_bgチェックボックスはONになっている");
  check(project.watermark && project.watermark.auto_transparent_bg === true,
    "既定ではwatermark.auto_transparent_bg=trueがJSONに出力される");
  check(await page.isVisible("#watermarkAutoTransparentPreviewCanvas"),
    "画像選択後、背景自動透明化のプレビューcanvasが表示される");
  await page.uncheck("#watermarkAutoTransparentBgCheckbox");
  project = await page.evaluate(() => buildProjectJSON(appState));
  check(project.watermark.auto_transparent_bg === false, "チェックを外すとwatermark.auto_transparent_bg=falseになる");
  await page.check("#watermarkAutoTransparentBgCheckbox");
  project = await page.evaluate(() => buildProjectJSON(appState));
  check(project.watermark.auto_transparent_bg === true, "チェックを戻すとwatermark.auto_transparent_bg=trueに戻る");

  console.log("");
  console.log("=== ハッシュタグ表示：表示ONでテキスト・位置・サイズ・色・背景を設定するとJSONに反映される ===");
  check(await page.isHidden("#hashtagsOptionsBody"), "既定ではハッシュタグOFFなのでhashtagsOptionsBodyは隠れている");
  project = await page.evaluate(() => buildProjectJSON(appState));
  check(project.hashtags === undefined, "ハッシュタグが無効な間はJSONにhashtagsブロックが出ない");

  await page.check("#hashtagsEnabledCheckbox");
  check(await page.isVisible("#hashtagsOptionsBody"), "表示をONにするとhashtagsOptionsBodyが表示される");
  check(await page.isHidden("#hashtagsCustomPosBody"),
    "position既定値はbottomなのでhashtagsCustomPosBodyは隠れている");
  await page.fill("#hashtagsTextInput", "#京都 #祇園 #ClubIRIS");
  await page.selectOption("#hashtagsPositionSelect", "top");
  await page.fill("#hashtagsSizeSlider", "0.032");
  await page.dispatchEvent("#hashtagsSizeSlider", "input");
  await page.fill("#hashtagsColorInput", "#ff0000");
  await page.dispatchEvent("#hashtagsColorInput", "input");
  await page.selectOption("#hashtagsBackingSelect", "box");

  project = await page.evaluate(() => buildProjectJSON(appState));
  check(!!project.hashtags, "テキストを入力するとJSONにhashtagsブロックが現れる");
  check(project.hashtags && project.hashtags.text === "#京都 #祇園 #ClubIRIS",
    "hashtags.textが入力どおりになる: " + (project.hashtags && project.hashtags.text));
  check(project.hashtags && project.hashtags.position === "top",
    "hashtags.positionが選択どおりになる: " + (project.hashtags && project.hashtags.position));
  check(project.hashtags && project.hashtags.size === 0.032,
    "hashtags.sizeがスライダー操作どおりになる: " + (project.hashtags && project.hashtags.size));
  check(project.hashtags && project.hashtags.color === "#ff0000",
    "hashtags.colorが入力どおりになる: " + (project.hashtags && project.hashtags.color));
  check(project.hashtags && project.hashtags.backing === "box",
    "hashtags.backingが選択どおりになる: " + (project.hashtags && project.hashtags.backing));
  check(project.hashtags && project.hashtags.always === true, "hashtags.alwaysの既定はtrue");
  check(project.hashtags && project.hashtags.pos === undefined,
    "position=topのときはposキーを出力しない");

  console.log("");
  console.log("=== ハッシュタグ表示：position=customにするとpos指定スライダーが現れ、posがJSONに出る ===");
  await page.selectOption("#hashtagsPositionSelect", "custom");
  check(await page.isVisible("#hashtagsCustomPosBody"), "position=customにするとhashtagsCustomPosBodyが表示される");
  await page.fill("#hashtagsPosXSlider", "0.3");
  await page.dispatchEvent("#hashtagsPosXSlider", "input");
  await page.fill("#hashtagsPosYSlider", "0.6");
  await page.dispatchEvent("#hashtagsPosYSlider", "input");
  project = await page.evaluate(() => buildProjectJSON(appState));
  check(project.hashtags && Array.isArray(project.hashtags.pos)
    && project.hashtags.pos[0] === 0.3 && project.hashtags.pos[1] === 0.6,
    "hashtags.posがスライダー操作どおりになる: " + JSON.stringify(project.hashtags && project.hashtags.pos));

  console.log("");
  console.log("=== ハッシュタグ表示：常時表示チェックを外すとalways=falseになる ===");
  await page.uncheck("#hashtagsAlwaysCheckbox");
  project = await page.evaluate(() => buildProjectJSON(appState));
  check(project.hashtags && project.hashtags.always === false,
    "常時表示チェックを外すとhashtags.always=falseになる");

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
