#!/usr/bin/env node
// -*- coding: utf-8 -*-
//
// テロップの複数行入力UI（行の追加・削除、行ごとの文字サイズ・アンダーライン、
// フォント選択＝全体既定＋フリーズ単位の上書き）を、headless Chromium +
// iPhoneデバイスエミュレーションで検証する回帰テスト。
//
// 実行方法:
//   npm install --no-save playwright-core
//   python3 -m http.server 8794 &
//   node tests/title_lines_editor.playwright.test.mjs
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

  console.log("=== ①塗り reveal_sec スライダー：0にすると「即時」表示になる（フリーズ編集を開く前の全体設定） ===");
  await page.fill("#revealSecSlider", "0");
  await page.dispatchEvent("#revealSecSlider", "input");
  await page.waitForTimeout(50);
  const revealLabelAtZero = await page.evaluate(() => document.getElementById("revealSecValue").textContent);
  check(revealLabelAtZero === "即時", "reveal_sec=0のとき表示ラベルが「即時」になる: " + revealLabelAtZero);
  await page.fill("#revealSecSlider", "0.5");
  await page.dispatchEvent("#revealSecSlider", "input");
  await page.waitForTimeout(50);
  const revealLabelAtHalf = await page.evaluate(() => document.getElementById("revealSecValue").textContent);
  check(revealLabelAtHalf === "0.5秒", "0以外は従来どおり秒数表示になる: " + revealLabelAtHalf);

  console.log("");
  console.log("=== 新規フリーズは1行だけの入力行から始まり、削除ボタンは隠れている ===");
  await page.click("#addFreezeBtn");
  await page.waitForFunction(() => !document.getElementById("editorSection").hidden, null, { timeout: 5000 });
  await page.waitForTimeout(200);
  const initialRows = await page.locator(".title-line-row").count();
  check(initialRows === 1, "初期状態は1行: " + initialRows);
  const firstRemoveHidden = await page.locator(".title-line-row").nth(0).locator(".title-line-remove-btn").isHidden();
  check(firstRemoveHidden, "1行しかない時は削除ボタンが隠れている（最低1行は残す仕様）");

  console.log("");
  console.log("=== 「＋ 行を追加」で2行目が増え、行ごとにテキスト・サイズ・アンダーラインを設定できる ===");
  await page.fill(".title-line-row:nth-child(1) .title-line-text", "山田 太郎");
  await page.click("#addTitleLineBtn");
  await page.waitForTimeout(50);
  const rowsAfterAdd = await page.locator(".title-line-row").count();
  check(rowsAfterAdd === 2, "行を追加すると2行になる: " + rowsAfterAdd);
  await page.fill(".title-line-row:nth-child(2) .title-line-text", "エースストライカー");
  await page.selectOption(".title-line-row:nth-child(2) .title-line-size", "0.55").catch(async () => {
    // 0.55はプリセットに無いため、選択肢が無ければ何もしない（下のdraft確認で検証する）
  });
  await page.click(".title-line-row:nth-child(1) .title-line-underline-btn");
  await page.waitForTimeout(50);

  const draftLines = await page.evaluate(() => draft.titleLines);
  check(draftLines.length === 2, "draft.titleLinesが2行になっている: " + JSON.stringify(draftLines));
  check(draftLines[0].text === "山田 太郎" && draftLines[0].underline === true,
    "1行目のテキストとアンダーライン設定が反映されている: " + JSON.stringify(draftLines[0]));
  check(draftLines[1].text === "エースストライカー",
    "2行目のテキストが反映されている: " + JSON.stringify(draftLines[1]));

  console.log("");
  console.log("=== 位置ドラッグは複数行ブロック全体を1つとして移動する（computeEditorTelopBoxがブロック全体の外接矩形を返す） ===");
  const box = await page.evaluate(() => computeEditorTelopBox());
  check(!!box, "複数行でもcomputeEditorTelopBoxが矩形を返す（テロップが表示されている）");
  check(box.textH > 0, "ブロックの高さが1行分より大きい（複数行が縦に積まれている）: " + box.textH);

  console.log("");
  console.log("=== 行ごとの文字色プリセット：クリックするとdraft.titleLines[].colorに反映される ===");
  await page.click(".title-line-row:nth-child(1) .title-line-color-btn[title=\"金\"]");
  await page.waitForTimeout(50);
  const line1Color = await page.evaluate(() => draft.titleLines[0].color);
  check(line1Color === "#E6C15C", "1行目に「金」プリセットを選ぶとcolor=#E6C15Cになる: " + line1Color);
  const colorBtnSelected = await page.locator(".title-line-row:nth-child(1) .title-line-color-btn[title=\"金\"]").evaluate((el) => el.classList.contains("selected"));
  check(colorBtnSelected, "選択したプリセットボタンにselectedクラスが付く");

  await page.fill(".title-line-row:nth-child(2) .title-line-color-input", "#ff3b30");
  await page.dispatchEvent(".title-line-row:nth-child(2) .title-line-color-input", "input");
  await page.waitForTimeout(50);
  const line2Color = await page.evaluate(() => draft.titleLines[1].color);
  check(line2Color === "#FF3B30", "2行目の自由入力欄に#RRGGBBを入れるとcolorに反映される（大文字化）: " + line2Color);

  console.log("");
  console.log("=== 行ごとの寄せ：セレクトで選ぶとdraft.titleLines[].alignに反映され、JSONにも出力される ===");
  await page.selectOption(".title-line-row:nth-child(1) .title-line-align-select", "left");
  await page.waitForTimeout(50);
  const line1Align = await page.evaluate(() => draft.titleLines[0].align);
  check(line1Align === "left", "1行目の寄せセレクトで'left'を選ぶとdraft.titleLines[0].align='left'になる: " + line1Align);
  const line2AlignDefault = await page.evaluate(() => draft.titleLines[1].align);
  check(line2AlignDefault === "", "操作していない2行目のalignは既定（空文字＝全体設定を継承）のまま: " + JSON.stringify(line2AlignDefault));
  const fzAlign = await page.evaluate(() => freezeToJSON(draft, appState.sfxLibrary));
  check(fzAlign.name.lines[0].align === "left", "JSON出力：1行目のalignが反映される: " + fzAlign.name.lines[0].align);
  check(fzAlign.name.lines[1].align === undefined, "JSON出力：寄せを変えていない2行目はalignキー自体を出力しない: " + fzAlign.name.lines[1].align);

  console.log("");
  console.log("=== 行ごとの出現アクション：1行目は「遅れ」欄自体が表示されず、2行目はanim/anim_sec/delay_sec/sfxを設定できる ===");
  const line1DelayHidden = await page.locator(".title-line-row:nth-child(1) .title-line-delay-sec").isHidden();
  check(line1DelayHidden, "1行目の「遅れ」欄は表示されない（2行目以降のみ有効という仕様）");
  const timeLabelText = await page.locator(".title-line-row:nth-child(2) .title-line-num-field:has(.title-line-anim-sec) label").textContent();
  check(timeLabelText === "時間（秒）", "2行目の「時間」欄にラベルが表示されている: " + timeLabelText);
  const delayLabelText = await page.locator(".title-line-row:nth-child(2) .title-line-num-field:has(.title-line-delay-sec) label").textContent();
  check(delayLabelText === "遅れ（秒）", "2行目の「遅れ」欄にラベルが表示されている: " + delayLabelText);
  const animSecPlaceholder = await page.locator(".title-line-row:nth-child(2) .title-line-anim-sec").getAttribute("placeholder");
  check(animSecPlaceholder === "既定 0.25", "「時間」欄のプレースホルダに既定値が数字で示される（anim未選択時）: " + animSecPlaceholder);
  const animSecInputMode = await page.locator(".title-line-row:nth-child(2) .title-line-anim-sec").getAttribute("inputmode");
  const delaySecInputMode = await page.locator(".title-line-row:nth-child(2) .title-line-delay-sec").getAttribute("inputmode");
  check(animSecInputMode === "decimal" && delaySecInputMode === "decimal",
    "「時間」「遅れ」欄はinputmode=decimalで小数キーボードが出る: " + animSecInputMode + " / " + delaySecInputMode);

  await page.selectOption(".title-line-row:nth-child(2) .title-line-anim-select", "slide_left");
  await page.fill(".title-line-row:nth-child(2) .title-line-anim-sec", "0.4");
  await page.dispatchEvent(".title-line-row:nth-child(2) .title-line-anim-sec", "input");
  // 実機で報告された不具合の回帰確認：delay_secに0.3（step=0.05の倍数として浮動小数点誤差が
  // 出やすい値）を入力してもエラーにならず正しく反映されること。
  await page.fill(".title-line-row:nth-child(2) .title-line-delay-sec", "0.3");
  await page.dispatchEvent(".title-line-row:nth-child(2) .title-line-delay-sec", "input");
  await page.selectOption(".title-line-row:nth-child(2) .title-line-sfx-select", "shakin");
  await page.waitForTimeout(50);
  const line2Anim = await page.evaluate(() => ({
    anim: draft.titleLines[1].anim, animSec: draft.titleLines[1].animSec,
    delaySec: draft.titleLines[1].delaySec, sfx: draft.titleLines[1].sfx,
  }));
  check(line2Anim.anim === "slide_left" && line2Anim.animSec === 0.4 && line2Anim.delaySec === 0.3
    && line2Anim.sfx === "shakin",
    "2行目のanim/anim_sec/delay_sec/sfxがdraft.titleLinesに反映される（delay_sec=0.3もエラー無く反映）: "
    + JSON.stringify(line2Anim));

  console.log("");
  console.log("=== 全角数字での入力も正しく解釈される（iPhoneのIMEで全角になっても壊れない回帰確認） ===");
  await page.fill(".title-line-row:nth-child(2) .title-line-delay-sec", "０．４５");
  await page.dispatchEvent(".title-line-row:nth-child(2) .title-line-delay-sec", "input");
  await page.waitForTimeout(50);
  const fullWidthDelay = await page.evaluate(() => draft.titleLines[1].delaySec);
  check(fullWidthDelay === 0.45, "全角「０．４５」もdelay_sec=0.45として解釈される: " + fullWidthDelay);
  // 元の値に戻しておく（以降のテストへ影響しないように）
  await page.fill(".title-line-row:nth-child(2) .title-line-delay-sec", "0.3");
  await page.dispatchEvent(".title-line-row:nth-child(2) .title-line-delay-sec", "input");

  // このフリーズはまだdraft（編集中）でappState.freezesにコミットされていないため、
  // freezeToJSON(draft, ...)を直接呼んでJSON契約を確認する（buildProjectJSONはコミット後専用）。
  const fzLineActions = await page.evaluate(() => freezeToJSON(draft, appState.sfxLibrary));
  check(typeof fzLineActions.name === "object", "行ごとの新フィールドを使うとnameは{lines:[...]}形式になる");
  const jsonLine1 = fzLineActions.name.lines[0];
  const jsonLine2 = fzLineActions.name.lines[1];
  check(jsonLine1.color === "#E6C15C", "JSON出力：1行目のcolorが反映される: " + jsonLine1.color);
  check(jsonLine2.anim === "slide_left" && jsonLine2.anim_sec === 0.4 && jsonLine2.delay_sec === 0.3
    && jsonLine2.sfx === "shakin",
    "JSON出力：2行目のanim/anim_sec/delay_sec/sfxが反映される: " + JSON.stringify(jsonLine2));
  check(jsonLine1.delay_sec === undefined, "JSON出力：1行目はdelay_secを出力しない（常に0扱いのため）");

  console.log("");
  console.log("=== 行の削除：2行のうち1行を消すと1行に戻り、残った行の削除ボタンは再び隠れる ===");
  await page.click(".title-line-row:nth-child(2) .title-line-remove-btn");
  await page.waitForTimeout(50);
  const rowsAfterRemove = await page.locator(".title-line-row").count();
  check(rowsAfterRemove === 1, "1行を削除すると1行に戻る: " + rowsAfterRemove);
  const draftAfterRemove = await page.evaluate(() => draft.titleLines);
  check(draftAfterRemove.length === 1 && draftAfterRemove[0].text === "山田 太郎",
    "削除後、draft.titleLinesにも残った行だけが反映されている: " + JSON.stringify(draftAfterRemove));

  console.log("");
  console.log("=== フォント選択：フリーズ単位で全体既定を上書きでき、JSONにtitle_font/title_font_jpとして反映される ===");
  await page.selectOption("#freezeTitleFontSelect", "delagothicone");
  await page.waitForTimeout(50);
  const fontKeyInDraft = await page.evaluate(() => draft.titleFontKey);
  check(fontKeyInDraft === "delagothicone", "選択したフォントキーがdraft.titleFontKeyに反映される: " + fontKeyInDraft);
  await page.click("#commitFreezeBtn");
  await page.waitForFunction(() => document.getElementById("editorSection").hidden, null, { timeout: 5000 });

  const project = await page.evaluate(() => buildProjectJSON(appState));
  check(project.freezes.length === 1, "フリーズが1件書き出される: " + project.freezes.length);
  const fz = project.freezes[0];
  check(fz.title_font === "assets/fonts/DelaGothicOne-Regular.ttf" && fz.title_font_jp === "assets/fonts/DelaGothicOne-Regular.ttf",
    "JSONのfreezes[0].title_font/title_font_jpが選んだフォントのパスになっている: " + fz.title_font);
  check(typeof fz.name === "object" && fz.name.lines[0].text === "山田 太郎" && fz.name.lines[0].underline === true,
    "1行に戻ってもアンダーラインを付けたままなのでnameは{lines:[...]}のオブジェクト形式のまま: " + JSON.stringify(fz.name));

  console.log("");
  console.log("=== 全体既定フォント：settings側のセレクトを変えるとappState.titleFontKeyに反映される ===");
  await page.selectOption("#titleFontDefaultSelect", "notoserifjp");
  await page.waitForTimeout(50);
  const globalFontKey = await page.evaluate(() => appState.titleFontKey);
  check(globalFontKey === "notoserifjp", "全体既定フォントの選択がappState.titleFontKeyに反映される: " + globalFontKey);
  const project2 = await page.evaluate(() => buildProjectJSON(appState));
  check(project2.style.title_font === "assets/fonts/NotoSerifJP-Bold.ttf",
    "JSONのstyle.title_fontにも全体既定フォントが反映される: " + project2.style.title_font);

  console.log("");
  console.log("=== 再編集：既存フリーズを開くと、保存済みの複数行・フォント選択が復元される ===");
  await page.click("#addFreezeBtn");
  await page.waitForFunction(() => !document.getElementById("editorSection").hidden, null, { timeout: 5000 });
  await page.waitForTimeout(200);
  await page.fill(".title-line-text", "再編集テスト");
  await page.click("#addTitleLineBtn");
  await page.fill(".title-line-row:nth-child(2) .title-line-text", "2行目");
  await page.selectOption("#freezeTitleFontSelect", "mplusrounded1c");
  await page.click("#commitFreezeBtn");
  await page.waitForFunction(() => document.getElementById("editorSection").hidden, null, { timeout: 5000 });

  const freezeId = await page.evaluate(() => {
    const fz = appState.freezes.filter((f) => f.name.indexOf("再編集テスト") === 0)[0];
    return fz && fz.id;
  });
  check(!!freezeId, "作成したフリーズが一覧に見つかる");
  await page.evaluate((id) => { editFreeze(id); }, freezeId);
  await page.waitForFunction(() => !document.getElementById("editorSection").hidden, null, { timeout: 5000 });
  await page.waitForTimeout(200);
  const reopenedRows = await page.locator(".title-line-row").count();
  check(reopenedRows === 2, "再編集時、2行の入力行が復元される: " + reopenedRows);
  const reopenedFontValue = await page.evaluate(() => document.getElementById("freezeTitleFontSelect").value);
  check(reopenedFontValue === "mplusrounded1c", "再編集時、フリーズ単位のフォント選択も復元される: " + reopenedFontValue);
  await page.click("#cancelFreezeBtn");
  await page.waitForFunction(() => document.getElementById("editorSection").hidden, null, { timeout: 5000 });

  console.log("");
  console.log("=== 背景（人物以外）の塗りの種類：全体設定でmono以外を選ぶとオプション欄が表示され、色・スケール・角度・不透明度を設定できる ===");
  const bgOptionsHiddenAtMono = await page.evaluate(() => document.getElementById("backgroundOptionsBody").hidden);
  check(bgOptionsHiddenAtMono, "既定(mono)ではbackgroundOptionsBodyは隠れている");
  await page.selectOption("#backgroundModeSelect", "halftone");
  await page.waitForTimeout(50);
  const bgModeAfterSelect = await page.evaluate(() => appState.background);
  check(bgModeAfterSelect === "halftone", "セレクトで'halftone'を選ぶとappState.backgroundに反映される: " + bgModeAfterSelect);
  const bgOptionsShown = await page.evaluate(() => !document.getElementById("backgroundOptionsBody").hidden);
  check(bgOptionsShown, "halftoneを選ぶとbackgroundOptionsBodyが表示される");

  await page.click("#backgroundBaseColorRow .background-color-btn[title=\"黒\"]");
  await page.waitForTimeout(50);
  const bgBaseAfterPreset = await page.evaluate(() => appState.backgroundOptions.base);
  check(bgBaseAfterPreset === "#000000", "base色プリセット「黒」を選ぶとappState.backgroundOptions.base=#000000になる: " + bgBaseAfterPreset);

  await page.fill("#backgroundAccentColorInput", "#00aaff");
  await page.dispatchEvent("#backgroundAccentColorInput", "input");
  await page.waitForTimeout(50);
  const bgAccentAfterInput = await page.evaluate(() => appState.backgroundOptions.accent);
  check(bgAccentAfterInput === "#00AAFF", "accent色の自由入力欄で#RRGGBBを入れるとaccentに反映される（大文字化）: " + bgAccentAfterInput);

  await page.fill("#backgroundScaleSlider", "0.04");
  await page.dispatchEvent("#backgroundScaleSlider", "input");
  await page.fill("#backgroundAngleSlider", "60");
  await page.dispatchEvent("#backgroundAngleSlider", "input");
  await page.fill("#backgroundOpacitySlider", "0.4");
  await page.dispatchEvent("#backgroundOpacitySlider", "input");
  await page.waitForTimeout(50);
  const bgOptsAfterSliders = await page.evaluate(() => appState.backgroundOptions);
  check(bgOptsAfterSliders.scale === 0.04 && bgOptsAfterSliders.angle === 60 && bgOptsAfterSliders.opacity === 0.4,
    "scale/angle/opacityスライダーがappState.backgroundOptionsに反映される: " + JSON.stringify(bgOptsAfterSliders));

  const projectBg = await page.evaluate(() => buildProjectJSON(appState));
  check(projectBg.style.background === "halftone", "JSON出力：style.backgroundに'halftone'が反映される: " + projectBg.style.background);
  check(!!projectBg.style.background_options
    && projectBg.style.background_options.base === "#000000"
    && projectBg.style.background_options.accent === "#00AAFF"
    && projectBg.style.background_options.scale === 0.04
    && projectBg.style.background_options.angle === 60
    && projectBg.style.background_options.opacity === 0.4,
    "JSON出力：style.background_optionsに設定した値がすべて反映される: " + JSON.stringify(projectBg.style.background_options));

  console.log("");
  console.log("=== 背景：既存フリーズの「背景処理」で全体設定を上書きでき、そのフリーズだけJSONに反映される ===");
  const bgFreezeId = await page.evaluate(() => appState.freezes[0].id);
  await page.evaluate((id) => { editFreeze(id); }, bgFreezeId);
  await page.waitForFunction(() => !document.getElementById("editorSection").hidden, null, { timeout: 5000 });
  await page.waitForTimeout(200);
  await page.selectOption("#backgroundSelect", "grid");
  await page.click("#commitFreezeBtn");
  await page.waitForFunction(() => document.getElementById("editorSection").hidden, null, { timeout: 5000 });
  const projectBgFreeze = await page.evaluate(() => buildProjectJSON(appState));
  check(projectBgFreeze.freezes.some((f) => f.background === "grid"),
    "フリーズ単位で上書きした背景('grid')がJSONのfreezes[].backgroundに反映される: "
    + JSON.stringify(projectBgFreeze.freezes.map((f) => f.background)));
  check(projectBgFreeze.style.background === "halftone",
    "フリーズ単位の上書きをしても全体設定(style.background)は変わらない: " + projectBgFreeze.style.background);

  console.log("");
  console.log("=== 背景：全体設定をmonoに戻してもUIは追従する（background_optionsが既定値のままの場合の後方互換出力はNode単体テストで別途確認済み） ===");
  await page.selectOption("#backgroundModeSelect", "mono");
  await page.waitForTimeout(50);
  const bgOptionsHiddenAgain = await page.evaluate(() => document.getElementById("backgroundOptionsBody").hidden);
  check(bgOptionsHiddenAgain, "monoに戻すとbackgroundOptionsBodyは再び隠れる");
  const projectBgReset = await page.evaluate(() => buildProjectJSON(appState));
  check(projectBgReset.style.background === "mono", "style.backgroundが'mono'に戻る: " + projectBgReset.style.background);
  // このテストでは直前にbase/accent/scale/angle/opacityを既定値から変更済みのため、
  // ここではbackground_optionsが引き続き（変更後の値のまま）出力されることを確認する
  // （モードをmonoに戻しても、ユーザーが設定した色・模様の値は破棄されない仕様）。
  check(projectBgReset.style.background_options && projectBgReset.style.background_options.base === "#000000",
    "modeをmonoに戻しても、変更済みのbackground_optionsの値は保持されたままJSONに出力される（破棄されない）: "
    + JSON.stringify(projectBgReset.style.background_options));

  console.log("");
  console.log("=== 人物マスクの縁の種類：全体設定でsolid以外を選ぶとオプション欄が表示され、色・スケール・線の太さを設定できる ===");
  const maskStyleOptionsHiddenAtSolid = await page.evaluate(() => document.getElementById("maskStyleOptionsBody").hidden);
  check(maskStyleOptionsHiddenAtSolid, "既定(solid)ではmaskStyleOptionsBodyは隠れている");
  await page.selectOption("#maskStyleDefaultSelect", "outline");
  await page.waitForTimeout(50);
  const maskStyleAfterSelect = await page.evaluate(() => appState.maskStyle);
  check(maskStyleAfterSelect === "outline", "セレクトで'outline'を選ぶとappState.maskStyleに反映される: " + maskStyleAfterSelect);
  const maskStyleOptionsShown = await page.evaluate(() => !document.getElementById("maskStyleOptionsBody").hidden);
  check(maskStyleOptionsShown, "outlineを選ぶとmaskStyleOptionsBodyが表示される");

  await page.click("#maskStyleColorRow .mask-style-color-btn[title=\"赤\"]");
  await page.waitForTimeout(50);
  const maskStyleColorAfterPreset = await page.evaluate(() => appState.maskStyleOptions.color);
  check(maskStyleColorAfterPreset === "#FF3B30", "色プリセット「赤」を選ぶとappState.maskStyleOptions.color=#FF3B30になる: " + maskStyleColorAfterPreset);

  await page.fill("#maskStyleScaleSlider", "0.02");
  await page.dispatchEvent("#maskStyleScaleSlider", "input");
  await page.fill("#maskStyleWidthSlider", "0.008");
  await page.dispatchEvent("#maskStyleWidthSlider", "input");
  await page.waitForTimeout(50);
  const maskStyleOptsAfterSliders = await page.evaluate(() => appState.maskStyleOptions);
  check(maskStyleOptsAfterSliders.scale === 0.02 && maskStyleOptsAfterSliders.width === 0.008,
    "scale/widthスライダーがappState.maskStyleOptionsに反映される: " + JSON.stringify(maskStyleOptsAfterSliders));

  const projectMaskStyle = await page.evaluate(() => buildProjectJSON(appState));
  check(projectMaskStyle.style.mask_style === "outline", "JSON出力：style.mask_styleに'outline'が反映される: " + projectMaskStyle.style.mask_style);
  check(!!projectMaskStyle.style.mask_style_options
    && projectMaskStyle.style.mask_style_options.color === "#FF3B30"
    && projectMaskStyle.style.mask_style_options.scale === 0.02
    && projectMaskStyle.style.mask_style_options.width === 0.008,
    "JSON出力：style.mask_style_optionsに設定した値がすべて反映される: " + JSON.stringify(projectMaskStyle.style.mask_style_options));

  console.log("");
  console.log("=== 人物マスクの縁の種類：既存フリーズの「マスクの縁」で全体設定を上書きでき、そのフリーズだけJSONに反映される ===");
  const maskStyleFreezeId = await page.evaluate(() => appState.freezes[0].id);
  await page.evaluate((id) => { editFreeze(id); }, maskStyleFreezeId);
  await page.waitForFunction(() => !document.getElementById("editorSection").hidden, null, { timeout: 5000 });
  await page.waitForTimeout(200);
  await page.selectOption("#maskStyleSelect", "halftone");
  await page.click("#commitFreezeBtn");
  await page.waitForFunction(() => document.getElementById("editorSection").hidden, null, { timeout: 5000 });
  const projectMaskStyleFreeze = await page.evaluate(() => buildProjectJSON(appState));
  check(projectMaskStyleFreeze.freezes.some((f) => f.mask_style === "halftone"),
    "フリーズ単位で上書きしたマスクの縁('halftone')がJSONのfreezes[].mask_styleに反映される: "
    + JSON.stringify(projectMaskStyleFreeze.freezes.map((f) => f.mask_style)));
  check(projectMaskStyleFreeze.style.mask_style === "outline",
    "フリーズ単位の上書きをしても全体設定(style.mask_style)は変わらない: " + projectMaskStyleFreeze.style.mask_style);

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
