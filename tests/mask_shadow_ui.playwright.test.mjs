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

  // 影の既定値をOFF→ONに変更した際、同じ動画ファイル名で保存されていた「旧バージョン
  // (v1)・影OFFの復元データ」が動画選択のたびに自動復元され、新しい既定値(影ON)を
  // 静かに上書きしてしまう回帰があった（storageKeyをv2へ変更して修正）。
  // ここでは、その旧v1データを意図的に仕込んでから動画を選び、無視されることを確認する。
  const videoBaseName = path.basename(videoPath);
  await page.evaluate((name) => {
    localStorage.setItem("spotlightReel:v1:" + name, JSON.stringify({
      freezeSec: 1.2, brushAnimSec: 0.8, monoContrast: 1.0, titleBounce: false,
      audioDuringFreeze: "mute", reveal: "wipe",
      shadowEnabled: false, shadowOffsetRatio: 0, shadowBlurRatio: 0.02, shadowAlpha: 0.6,
      outputMode: "original", freezes: [], logo: null
    }));
  }, videoBaseName);

  await page.setInputFiles("#videoFileInput", videoPath);
  await page.waitForFunction(() => document.getElementById("video").duration > 0, null, { timeout: 10000 });
  // duration>0はメタデータ取得の合図でしかなく、ライブ描画ループがplayerCanvasに
  // 最初の有効なフレームを描き終えるまでには一瞬かかる。ここで少し待たないと、
  // 直後の「＋フリーズ追加」がcaptureFrozenFrame（黒フレーム検出＋1回だけ100ms再試行）の
  // 両方の試行タイミングにぶつかり、静止フレーム取得に失敗することがある。
  await page.waitForTimeout(300);

  console.log("=== 影：旧v1形式のlocalStorageデータ(影OFF)は無視され、新しい既定値(影ON)が使われる ===");
  check((await page.evaluate(() => appState.shadowEnabled)) === true,
    "同名動画で保存されていた旧v1データ(影OFF)があっても、appState.shadowEnabledは新しい既定値trueのまま");
  check(await page.isChecked("#shadowEnabledCheckbox"),
    "同上：shadowEnabledCheckboxもチェック済みのまま（旧データに上書きされない）");

  console.log("");
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
  check(brushFz.color_source === "brush" && !("mask" in brushFz),
    "brushフリーズはcolor_source='brush'で出力され、旧maskキーは出力しない: " + brushFz.color_source);
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
  console.log("=== 全体設定：影（フィルム色）のオン/オフとスライダー・方向 ===");
  // 全体設定は <details class="card"> の中にあり既定で閉じているため、先に開く
  await page.evaluate(() => { document.getElementById("settingsSection").open = true; });

  // 実機で影が一切出ない不具合の再発防止：エディタの初期状態は必ずONで、
  // 何も操作していない状態でもJSONにstyle.shadowが（{"enabled":false}ではなく）出力される。
  check(await page.isChecked("#shadowEnabledCheckbox"), "影は初期状態でON（shadowEnabledCheckboxがチェック済み）");
  check(await page.isVisible("#shadowOptionsBody"), "影は初期状態でON（shadowOptionsBodyが表示されている）");
  const styleInitial = (await page.evaluate(() => buildProjectJSON(appState))).style;
  check(!!styleInitial.shadow && styleInitial.shadow.enabled !== false,
    "初期状態（未操作）でもstyle.shadowが有効な設定として出力される: " + JSON.stringify(styleInitial.shadow));

  await page.uncheck("#shadowEnabledCheckbox");
  check(await page.isHidden("#shadowOptionsBody"), "オフにするとshadowOptionsBodyが隠れる");
  const styleDisabled = (await page.evaluate(() => buildProjectJSON(appState))).style;
  check(JSON.stringify(styleDisabled.shadow) === JSON.stringify({ enabled: false }),
    "オフにするとstyle.shadow={\"enabled\":false}が明示的に出力される（キー省略ではない）: " +
    JSON.stringify(styleDisabled.shadow));

  await page.check("#shadowEnabledCheckbox");
  check(await page.isVisible("#shadowOptionsBody"), "影オンでshadowOptionsBodyが表示される");
  check(await page.isVisible("#filmColorPresetRow"), "影オンで色プリセット行（#filmColorPresetRow）が表示される");
  await page.fill("#shadowDistanceSlider", "0.05");
  await page.dispatchEvent("#shadowDistanceSlider", "input");
  await page.fill("#shadowBlurSlider", "0.04");
  await page.dispatchEvent("#shadowBlurSlider", "input");
  await page.fill("#shadowAlphaSlider", "0.75");
  await page.dispatchEvent("#shadowAlphaSlider", "input");
  await page.selectOption("#shadowDirectionSelect", "left");

  const styleWithShadow = (await page.evaluate(() => buildProjectJSON(appState))).style;
  check(!!styleWithShadow.shadow, "影オン時はstyle.shadowが出力される: " + JSON.stringify(styleWithShadow.shadow));
  check(styleWithShadow.shadow.blur === 0.04, "style.shadow.blurがスライダー値どおり: " + styleWithShadow.shadow.blur);
  check(styleWithShadow.shadow.alpha === 0.75, "style.shadow.alphaがスライダー値どおり: " + styleWithShadow.shadow.alpha);
  check(styleWithShadow.shadow.distance === 0.05, "style.shadow.distanceがスライダー値どおり: " + styleWithShadow.shadow.distance);
  check(styleWithShadow.shadow.direction === "left", "style.shadow.directionが選択どおり'left': " + styleWithShadow.shadow.direction);

  await page.selectOption("#shadowDirectionSelect", "auto");
  const styleAutoDir = (await page.evaluate(() => buildProjectJSON(appState))).style;
  check(styleAutoDir.shadow.direction === "auto", "shadowDirectionSelectを'自動'に戻すとstyle.shadow.directionが'auto'になる: " + styleAutoDir.shadow.direction);

  const presetHex = await page.evaluate(() => Object.values(FILM_COLOR_PRESETS)[1]);
  await page.locator("#filmColorPresetRow .film-color-btn").nth(1).click();
  const styleWithPresetColor = (await page.evaluate(() => buildProjectJSON(appState))).style;
  check(styleWithPresetColor.shadow.color.toLowerCase() === presetHex.toLowerCase(),
    "色プリセットをクリックするとstyle.shadow.colorがそのプリセット色になる: " + styleWithPresetColor.shadow.color);

  console.log("");
  console.log("=== 全体設定：reveal（wipe/fade） ===");
  check((await page.inputValue("#revealSelect")) === "wipe", "revealSelectの既定はwipe");
  let styleDefaultReveal = (await page.evaluate(() => buildProjectJSON(appState))).style;
  check(styleDefaultReveal.reveal === "wipe", "既定のstyle.revealが'wipe': " + styleDefaultReveal.reveal);

  await page.selectOption("#revealSelect", "fade");
  const styleFadeReveal = (await page.evaluate(() => buildProjectJSON(appState))).style;
  check(styleFadeReveal.reveal === "fade", "revealSelectを'fade'にするとstyle.revealが'fade'になる: " + styleFadeReveal.reveal);

  console.log("");
  console.log("=== 全体設定：shadow.source（影に使うマスク） ===");
  check((await page.inputValue("#shadowSourceSelect")) === "same", "shadowSourceSelectの既定はsame");
  await page.selectOption("#shadowSourceSelect", "auto");
  const styleShadowSourceAuto = (await page.evaluate(() => buildProjectJSON(appState))).style;
  check(styleShadowSourceAuto.shadow.source === "auto",
    "shadowSourceSelectを'auto'にするとstyle.shadow.sourceが'auto'になる: " + styleShadowSourceAuto.shadow.source);
  await page.selectOption("#shadowSourceSelect", "same");

  console.log("");
  console.log("=== 全体設定：①塗り②ズレ③静止（reveal_sec/slide_sec/hold_sec） ===");
  check((await page.inputValue("#revealSecSlider")) === "0.5", "revealSecSliderの既定は0.5");
  check((await page.inputValue("#slideSecSlider")) === "0.5", "slideSecSliderの既定は0.5");
  check((await page.inputValue("#holdSecSlider")) === "2", "holdSecSliderの既定は2.0");
  await page.fill("#revealSecSlider", "0.8");
  await page.dispatchEvent("#revealSecSlider", "input");
  await page.fill("#slideSecSlider", "0.4");
  await page.dispatchEvent("#slideSecSlider", "input");
  await page.fill("#holdSecSlider", "3");
  await page.dispatchEvent("#holdSecSlider", "input");
  const styleTiming = (await page.evaluate(() => buildProjectJSON(appState))).style;
  check(styleTiming.reveal_sec === 0.8 && styleTiming.slide_sec === 0.4 && styleTiming.hold_sec === 3,
    "3つのスライダーを操作するとstyle.reveal_sec/slide_sec/hold_secに反映される: " +
    JSON.stringify({ reveal_sec: styleTiming.reveal_sec, slide_sec: styleTiming.slide_sec, hold_sec: styleTiming.hold_sec }));
  await page.selectOption("#revealSelect", "wipe");

  console.log("");
  console.log("=== 簡易プレビュー：影ありのフリーズで、着地前後に人物の元の位置の見え方が変わる（スライドで影が現れる） ===");
  await page.click("#addFreezeBtn");
  await page.waitForFunction(() => !document.getElementById("editorSection").hidden, null, { timeout: 5000 });
  await page.selectOption("#maskModeSelect", "auto");
  await page.fill("#nameInput", "プレビュー影テスト");
  await page.evaluate(() => {
    appState.shadowEnabled = true;
    appState.shadowDirection = "right";
    appState.shadowDistanceRatio = 0.08;
    appState.shadowBlurRatio = 0;
    appState.shadowAlpha = 1.0;
    appState.shadowColor = "#00FF00"; // ダミー動画の色と被らない、はっきり判別できる色にする
  });

  /** previewCanvasの、mask="auto"代用マスクの左端付近・縦中央よりやや下の1px色をCSS座標基準で読む */
  const readProbePixel = () => page.evaluate(() => {
    var dpr = window.devicePixelRatio || 1;
    var ctx = document.getElementById("previewCanvas").getContext("2d");
    var W = overlaySize.width, H = overlaySize.height;
    var bx = W * 0.25; // buildAutoPlaceholderMaskのbxと同じ式
    var x = Math.round((bx + 6) * dpr), y = Math.round(H * 0.7 * dpr);
    var d = ctx.getImageData(x, y, 1, 1).data;
    return [d[0], d[1], d[2]];
  });

  await page.click("#previewBtn");
  check((await page.textContent("#previewBtn")) === "■ 停止", "プレビュー開始でボタン表示が「■ 停止」になる");
  // HOLD(0.3s)+BRUSH(auto既定wipeで0.4s)の途中(0.6s、progress=0.75)。まだスライド開始前で、
  // wipeはH*0.75まで下から現れているのでプローブ位置(H*0.7)は「人物」で覆われている。
  await page.waitForTimeout(600);
  const beforeSlide = await readProbePixel();
  // さらに700ms待つ（合計1.3s）と、着地(0.3+0.4+0.2=0.9s)を過ぎ保持中。人物は右へ8%W分
  // ずれた位置にあり、プローブ位置(元の左端付近)は人物が去って影（#123456）が見えているはず。
  await page.waitForTimeout(700);
  const afterSlide = await readProbePixel();

  const dist = Math.hypot(
    beforeSlide[0] - afterSlide[0], beforeSlide[1] - afterSlide[1], beforeSlide[2] - afterSlide[2]);
  check(dist > 30,
    "スライドインの着地前後でプローブ位置の色が大きく変わる(=影が現れる演出が反映されている): " +
    "before=" + JSON.stringify(beforeSlide) + " after=" + JSON.stringify(afterSlide) + " dist=" + dist.toFixed(1));

  await page.click("#previewBtn");
  check((await page.textContent("#previewBtn")) === "▶ プレビュー", "「■ 停止」を押すとプレビューが止まりボタン表示が戻る");
  await page.click("#cancelFreezeBtn");
  await page.waitForFunction(() => document.getElementById("editorSection").hidden, null, { timeout: 5000 });

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
