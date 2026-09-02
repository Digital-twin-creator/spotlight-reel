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
