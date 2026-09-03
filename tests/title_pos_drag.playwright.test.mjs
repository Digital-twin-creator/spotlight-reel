#!/usr/bin/env node
// -*- coding: utf-8 -*-
//
// フリーズ編集画面で、名前を入力すると映像上にテロップが仮表示され、それを指（マウス）で
// ドラッグして位置を決められること、サイズスライダー・寄せセレクトの変更、画面端での
// クランプ、ブラシ描画との競合が起きないこと（テロップ上のタッチだけが移動モードになる）を、
// headless Chromium + iPhoneデバイスエミュレーションで検証する回帰テスト。
//
// 実行方法:
//   npm install --no-save playwright-core
//   python3 -m http.server 8794 &
//   node tests/title_pos_drag.playwright.test.mjs
//
// 環境変数 PW_URL / PW_CHROMIUM_PATH / PW_VIDEO は他のPlaywrightテストと同じ。

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

/** テロップの現在の外接矩形の中心を、ページ座標(clientX/clientY)で返す */
function telopCenterInPage(page) {
  return page.evaluate(() => {
    const info = computeEditorTelopBox();
    if (!info) return null;
    const rect = document.getElementById("drawCanvas").getBoundingClientRect();
    const cx = (info.box.left + info.box.right) / 2;
    const cy = (info.box.top + info.box.bottom) / 2;
    return {
      x: rect.left + (cx / overlaySize.width) * rect.width,
      y: rect.top + (cy / overlaySize.height) * rect.height
    };
  });
}

/** ratio座標(0〜1)を、drawCanvasのページ座標(clientX/clientY)に変換する */
function ratioToPage(page, rx, ry) {
  return page.evaluate(({ rx, ry }) => {
    const rect = document.getElementById("drawCanvas").getBoundingClientRect();
    return { x: rect.left + rx * rect.width, y: rect.top + ry * rect.height };
  }, { rx, ry });
}

async function dragMouse(page, from, to, steps) {
  await page.mouse.move(from.x, from.y);
  await page.mouse.down();
  for (let i = 1; i <= steps; i++) {
    const t = i / steps;
    await page.mouse.move(from.x + (to.x - from.x) * t, from.y + (to.y - from.y) * t);
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

  console.log("=== フリーズ編集画面で名前を入力すると、テロップが仮表示される ===");
  await page.click("#addFreezeBtn");
  await page.waitForFunction(() => !document.getElementById("editorSection").hidden, null, { timeout: 5000 });
  await page.waitForTimeout(300);

  const beforeName = await page.evaluate(() => !!computeEditorTelopBox());
  check(beforeName === false, "名前未入力の間はテロップ仮表示が無い（computeEditorTelopBoxがnull）");

  const modeBeforeName = await page.evaluate(() => draft.telopEditMode);
  check(modeBeforeName === "brush", "名前入力前の既定モードは「✏ ブラシ」: " + modeBeforeName);

  await page.fill(".title-line-text", "山田太郎");
  await page.waitForTimeout(100);
  const afterName = await page.evaluate(() => !!computeEditorTelopBox());
  check(afterName === true, "名前を入力すると仮表示のテロップが現れる（computeEditorTelopBoxが矩形を返す）");

  const defaultPos = await page.evaluate(() => draft.titlePos);
  check(defaultPos === null, "ドラッグ前はdraft.titlePosがnull（全体設定[0.5,0.78]を継承）");

  console.log("");
  console.log("=== 名前を入力した直後は自動で「✥ テロップ移動」モードに切り替わる ===");
  const modeAfterName = await page.evaluate(() => draft.telopEditMode);
  check(modeAfterName === "move", "名前入力直後、draft.telopEditModeが'move'になる: " + modeAfterName);
  const moveBtnSelectedAfterName = await page.evaluate(
    () => document.getElementById("telopModeMoveBtn").classList.contains("selected"));
  check(moveBtnSelectedAfterName, "「✥ テロップ移動」ボタンがselected状態になる");
  const brushBtnNotSelectedAfterName = await page.evaluate(
    () => document.getElementById("telopModeBrushBtn").classList.contains("selected"));
  check(!brushBtnNotSelectedAfterName, "「✏ ブラシ」ボタンはselectedでなくなる");

  console.log("");
  console.log("=== テロップ移動モード中はテロップをドラッグすると位置(draft.titlePos)が変わり、ストロークは増えない ===");
  const strokesBefore = await page.evaluate(() => draft.strokes.length);
  const from = await telopCenterInPage(page);
  check(from !== null, "テロップの現在位置(ページ座標)を取得できる");
  const to = await ratioToPage(page, 0.25, 0.3);
  await dragMouse(page, from, to, 10);
  await page.waitForTimeout(100);

  const draggedPos = await page.evaluate(() => draft.titlePos);
  check(Array.isArray(draggedPos), "ドラッグ後、draft.titlePosが配列として設定される: " + JSON.stringify(draggedPos));
  check(Math.abs(draggedPos[0] - 0.25) < 0.03 && Math.abs(draggedPos[1] - 0.3) < 0.03,
    "ドラッグ先の比率(0.25, 0.3)付近にdraft.titlePosが反映される: " + JSON.stringify(draggedPos));

  const strokesAfter = await page.evaluate(() => draft.strokes.length);
  check(strokesAfter === strokesBefore,
    "テロップをドラッグしてもストロークは増えない（ブラシ描画と競合しない）: " + strokesBefore + " -> " + strokesAfter);

  console.log("");
  console.log("=== テロップ移動モード中は、テロップから離れた場所（ブロック外）をドラッグしても、"
    + "描画されず唯一のテロップブロックを掴んで動かす ===");
  const posBeforeOutsideGrab = await page.evaluate(() => draft.titlePos);
  const strokesBeforeOutsideGrab = await page.evaluate(() => draft.strokes.length);
  const drawBoxForGrab = await page.locator("#drawCanvas").boundingBox();
  // テロップは現在(0.25, 0.3)付近にあるので、明らかに離れた右上（かつ映像下端の
  // 「ブラシ／テロップ移動」切替バーとは重ならない位置）から始める
  const outsideGrabFrom = { x: drawBoxForGrab.x + drawBoxForGrab.width * 0.85, y: drawBoxForGrab.y + drawBoxForGrab.height * 0.15 };
  const outsideGrabTo = await ratioToPage(page, 0.6, 0.5);
  await dragMouse(page, outsideGrabFrom, outsideGrabTo, 10);
  await page.waitForTimeout(100);
  const posAfterOutsideGrab = await page.evaluate(() => draft.titlePos);
  const strokesAfterOutsideGrab = await page.evaluate(() => draft.strokes.length);
  check(strokesAfterOutsideGrab === strokesBeforeOutsideGrab,
    "テロップ移動モードではブロック外から始めたドラッグでもストロークは増えない: "
    + strokesBeforeOutsideGrab + " -> " + strokesAfterOutsideGrab);
  check(JSON.stringify(posAfterOutsideGrab) !== JSON.stringify(posBeforeOutsideGrab),
    "テロップ移動モードではブロック外から始めたドラッグでも、最も近い（唯一の）テロップブロックを掴んで"
    + "位置が変わる: " + JSON.stringify(posBeforeOutsideGrab) + " -> " + JSON.stringify(posAfterOutsideGrab));

  console.log("");
  console.log("=== 「✏ ブラシ」に切り替えると、テロップの上をなぞっても移動ではなく通常どおりストロークが描かれる ===");
  await page.click("#telopModeBrushBtn");
  await page.waitForTimeout(50);
  const modeAfterBrushClick = await page.evaluate(() => draft.telopEditMode);
  check(modeAfterBrushClick === "brush", "「✏ ブラシ」をタップするとdraft.telopEditMode='brush'になる: " + modeAfterBrushClick);
  const brushBtnSelectedAfterClick = await page.evaluate(
    () => document.getElementById("telopModeBrushBtn").classList.contains("selected"));
  check(brushBtnSelectedAfterClick, "「✏ ブラシ」ボタンがselected状態になる");

  const strokesBeforeOnTelop = await page.evaluate(() => draft.strokes.length);
  const posBeforeOnTelop = await page.evaluate(() => draft.titlePos);
  const onTelopFrom = await telopCenterInPage(page);
  const onTelopTo = await ratioToPage(page, 0.4, 0.4);
  await dragMouse(page, onTelopFrom, onTelopTo, 8);
  await page.waitForTimeout(100);
  const strokesAfterOnTelop = await page.evaluate(() => draft.strokes.length);
  const posAfterOnTelop = await page.evaluate(() => draft.titlePos);
  check(strokesAfterOnTelop === strokesBeforeOnTelop + 1,
    "ブラシモードでは、テロップの上をなぞってもドラッグ移動ではなく通常どおりストロークが描かれる: "
    + strokesBeforeOnTelop + " -> " + strokesAfterOnTelop);
  check(JSON.stringify(posAfterOnTelop) === JSON.stringify(posBeforeOnTelop),
    "ブラシモードでは、テロップの上をなぞってもdraft.titlePosは変わらない: " + JSON.stringify(posAfterOnTelop));

  console.log("");
  console.log("=== テロップの外（映像の何もない場所）をドラッグすると、通常どおりストロークが描かれる ===");
  const strokesBeforeOutsideDraw = await page.evaluate(() => draft.strokes.length);
  const drawBox = await page.locator("#drawCanvas").boundingBox();
  // テロップは現在(0.25, 0.3)付近にあるので、離れた右下をなぞる
  await dragMouse(
    page,
    { x: drawBox.x + drawBox.width * 0.7, y: drawBox.y + drawBox.height * 0.75 },
    { x: drawBox.x + drawBox.width * 0.85, y: drawBox.y + drawBox.height * 0.9 },
    8
  );
  await page.waitForTimeout(100);
  const strokesAfterDraw = await page.evaluate(() => draft.strokes.length);
  check(strokesAfterDraw === strokesBeforeOutsideDraw + 1,
    "テロップ以外の場所をなぞると通常どおりストロークが1本追加される: " + strokesAfterDraw);

  console.log("");
  console.log("=== 「✥ テロップ移動」に戻すと、再びドラッグで位置を変えられる ===");
  await page.click("#telopModeMoveBtn");
  await page.waitForTimeout(50);
  const modeAfterMoveClick = await page.evaluate(() => draft.telopEditMode);
  check(modeAfterMoveClick === "move", "「✥ テロップ移動」をタップするとdraft.telopEditMode='move'になる: " + modeAfterMoveClick);

  console.log("");
  console.log("=== 画面端に向けてドラッグすると、はみ出さないようクランプされる ===");
  const infoBeforeClamp = await page.evaluate(() => {
    const info = computeEditorTelopBox();
    return { textW: info.textW, textH: info.textH, align: info.align };
  });
  const cornerFrom = await telopCenterInPage(page);
  const cornerTo = await ratioToPage(page, 0.0, 0.0);
  await dragMouse(page, cornerFrom, cornerTo, 10);
  await page.waitForTimeout(100);
  const clampedPos = await page.evaluate(() => draft.titlePos);
  const expectedClamped = await page.evaluate(({ textW, textH, align }) => {
    const safeRect = safeZoneRectPx(resolveSafeZone(appState.safeZone), overlaySize.width, overlaySize.height);
    return clampTitlePosRatio([0, 0], textW, textH, align, overlaySize.width, overlaySize.height, safeRect);
  }, infoBeforeClamp);
  check(clampedPos[0] >= 0 && clampedPos[0] <= 1 && clampedPos[1] >= 0 && clampedPos[1] <= 1,
    "画面外に出そうな位置へドラッグしても比率は0〜1の範囲に収まる: " + JSON.stringify(clampedPos));
  check(Math.abs(clampedPos[0] - expectedClamped[0]) < 0.02 && Math.abs(clampedPos[1] - expectedClamped[1]) < 0.02,
    "クランプ結果がclampTitlePosRatio（render.pyと同じロジック）の計算どおりになる（既定で有効なセーフゾーンぶん画面端より内側になる）: " +
    JSON.stringify(clampedPos) + " ≈ " + JSON.stringify(expectedClamped));
  check(clampedPos[1] > 0.02,
    "既定のセーフゾーン（top=120px@1080x1920基準）により、画面端(y=0)そのものより下に留まる: " + clampedPos[1]);

  console.log("");
  console.log("=== セーフゾーンを無効化すると、ドラッグは画面端そのものまでクランプされる ===");
  // フリーズ編集中は設定カードのチェックボックスをUI操作しにくいため、appStateを直接
  // 書き換える（updateTelopDrag自身がappState.safeZoneを毎回参照する実装なので、
  // これだけでドラッグの挙動が変わることを確認できる）
  await page.evaluate(() => { appState.safeZone.enabled = false; });
  const cornerFrom2 = await telopCenterInPage(page);
  const cornerTo2 = await ratioToPage(page, 0.0, 0.0);
  await dragMouse(page, cornerFrom2, cornerTo2, 10);
  await page.waitForTimeout(100);
  const clampedPosNoSafeZone = await page.evaluate(() => draft.titlePos);
  const expectedNoSafeZone = await page.evaluate(({ textW, textH, align }) => {
    return clampTitlePosRatio([0, 0], textW, textH, align, overlaySize.width, overlaySize.height);
  }, infoBeforeClamp);
  check(Math.abs(clampedPosNoSafeZone[0] - expectedNoSafeZone[0]) < 0.02 &&
    Math.abs(clampedPosNoSafeZone[1] - expectedNoSafeZone[1]) < 0.02,
    "セーフゾーンを無効化すれば従来どおり画面端まで（セーフゾーンぶんの余白なしで）クランプされる: " +
    JSON.stringify(clampedPosNoSafeZone) + " ≈ " + JSON.stringify(expectedNoSafeZone));
  await page.evaluate(() => { appState.safeZone.enabled = true; });
  // このブロックの直前のドラッグでdraft.titlePosが動いているため、以降のJSON比較は
  // ここで取り直した最新位置を基準にする
  const clampedPosFinal = await page.evaluate(() => draft.titlePos);

  console.log("");
  console.log("=== サイズスライダー・寄せセレクトの変更がdraftに反映される ===");
  await page.fill("#titleSizeSlider", "0.1");
  await page.dispatchEvent("#titleSizeSlider", "input");
  const sizeAfter = await page.evaluate(() => draft.titleSize);
  check(Math.abs(sizeAfter - 0.1) < 1e-6, "titleSizeSliderを動かすとdraft.titleSizeが更新される: " + sizeAfter);

  await page.selectOption("#titleAlignSelect", "left");
  const alignAfter = await page.evaluate(() => draft.titleAlign);
  check(alignAfter === "left", "titleAlignSelectで選ぶとdraft.titleAlignが更新される: " + alignAfter);

  console.log("");
  console.log("=== 完了後、JSONにtitle_pos/title_size/title_alignが反映される ===");
  await page.click("#commitFreezeBtn");
  await page.waitForFunction(() => document.getElementById("editorSection").hidden, null, { timeout: 5000 });

  const project = await page.evaluate(() => buildProjectJSON(appState));
  check(project.freezes.length === 1, "フリーズが1件書き出される: " + project.freezes.length);
  const fz = project.freezes[0];
  check(Array.isArray(fz.title_pos) && Math.abs(fz.title_pos[0] - clampedPosFinal[0]) < 0.01 &&
    Math.abs(fz.title_pos[1] - clampedPosFinal[1]) < 0.01,
    "書き出したJSONのfreezes[0].title_posがドラッグ後の位置になっている: " + JSON.stringify(fz.title_pos));
  check(Math.abs(fz.title_size - 0.1) < 1e-6, "freezes[0].title_sizeがスライダーの値になっている: " + fz.title_size);
  check(fz.title_align === "left", "freezes[0].title_alignがセレクトの値になっている: " + fz.title_align);

  console.log("");
  console.log("=== 何も操作しなければtitle_pos/size/alignは省略され、全体設定を継承する（後方互換） ===");
  await page.click("#addFreezeBtn");
  await page.waitForFunction(() => !document.getElementById("editorSection").hidden, null, { timeout: 5000 });
  await page.waitForTimeout(200);
  await page.fill(".title-line-text", "位置指定なし");
  await page.click("#commitFreezeBtn");
  await page.waitForFunction(() => document.getElementById("editorSection").hidden, null, { timeout: 5000 });
  const project2 = await page.evaluate(() => buildProjectJSON(appState));
  const untouchedFz = project2.freezes.filter((f) => f.name === "位置指定なし")[0];
  check(untouchedFz && !("title_pos" in untouchedFz) && !("title_size" in untouchedFz) && !("title_align" in untouchedFz),
    "ドラッグ・スライダー操作をしなければ title_pos/title_size/title_align キーは省略される: " +
    JSON.stringify(untouchedFz));
  check(JSON.stringify(project2.style.title_pos) === JSON.stringify([0.5, 0.78]) &&
    project2.style.title_size === 0.06 && project2.style.title_align === "center",
    "全体設定(style)は既定値[0.5,0.78]/0.06/centerのまま（後方互換）: " + JSON.stringify(project2.style));

  console.log("");
  console.log("=== 「切り抜き：自動」のフリーズはブラシが不要なので、既定でテロップ移動モードになる ===");
  await page.click("#addFreezeBtn");
  await page.waitForFunction(() => !document.getElementById("editorSection").hidden, null, { timeout: 5000 });
  await page.waitForTimeout(200);
  await page.selectOption("#maskModeSelect", "auto");
  await page.waitForTimeout(50);
  const modeForAutoMask = await page.evaluate(() => draft.telopEditMode);
  check(modeForAutoMask === "move",
    "切り抜き方法を「自動」にするとdraft.telopEditModeの既定が'move'になる: " + modeForAutoMask);
  const moveBtnSelectedForAutoMask = await page.evaluate(
    () => document.getElementById("telopModeMoveBtn").classList.contains("selected"));
  check(moveBtnSelectedForAutoMask, "「✥ テロップ移動」ボタンがselected状態になる（切り抜き：自動）");
  const brushBtnDisabledForAutoMask = await page.evaluate(
    () => document.getElementById("telopModeBrushBtn").disabled);
  check(brushBtnDisabledForAutoMask, "切り抜き方法「自動」では「✏ ブラシ」ボタンがグレーアウト（disabled）される");

  await page.selectOption("#maskModeSelect", "brush");
  await page.waitForTimeout(50);
  const brushBtnEnabledAfterBackToBrush = await page.evaluate(
    () => !document.getElementById("telopModeBrushBtn").disabled);
  check(brushBtnEnabledAfterBackToBrush, "切り抜き方法を「ブラシ」に戻すと「✏ ブラシ」ボタンのグレーアウトが解除される");

  await page.click("#cancelFreezeBtn");
  await page.waitForFunction(() => document.getElementById("editorSection").hidden, null, { timeout: 5000 });

  console.log("");
  console.log("=== 「ブラシ／テロップ移動」切替は映像の上ではなく、映像直下の「切り抜き方法」の上に横並びで表示される ===");
  await page.click("#addFreezeBtn");
  await page.waitForFunction(() => !document.getElementById("editorSection").hidden, null, { timeout: 5000 });
  await page.waitForTimeout(200);
  const toggleLayout = await page.evaluate(() => {
    const toggle = document.getElementById("telopModeToggle");
    const videoWrap = document.getElementById("videoWrap");
    const maskModeField = document.getElementById("maskModeSelect").closest(".field");
    return {
      isInsideVideoWrap: videoWrap.contains(toggle),
      toggleTop: toggle.getBoundingClientRect().top,
      toggleBottom: toggle.getBoundingClientRect().bottom,
      videoWrapBottom: videoWrap.getBoundingClientRect().bottom,
      maskModeTop: maskModeField.getBoundingClientRect().top
    };
  });
  check(!toggleLayout.isInsideVideoWrap, "切替ボタンは#videoWrap（映像）の中には存在しない＝映像の上に重ならない");
  check(toggleLayout.toggleTop >= toggleLayout.videoWrapBottom,
    "切替ボタンは映像エリアより下（映像の外）に配置され、映像と縦方向に重ならない: toggleTop=" + toggleLayout.toggleTop +
    " videoWrapBottom=" + toggleLayout.videoWrapBottom);
  check(toggleLayout.toggleBottom <= toggleLayout.maskModeTop,
    "切替ボタンは「切り抜き方法」の上に配置されている: toggleBottom=" + toggleLayout.toggleBottom +
    " maskModeTop=" + toggleLayout.maskModeTop);
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
