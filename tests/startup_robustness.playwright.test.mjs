#!/usr/bin/env node
// -*- coding: utf-8 -*-
//
// 実機iOS Safariで「ページを開いた瞬間にクラッシュループする」という報告への
// 回帰テスト。起動処理（localStorageの復元・自動保存データの読み込み）が、
//   1. 巨大な自動保存データ（大きなサムネイルを持つフリーズを多数）
//   2. 壊れたJSON（自動保存データが破損している）
//   3. 起動処理が致命的に失敗した場合の復旧UI（「初期化して開く」）
// のいずれの状況でも、ページ自体はクラッシュせず正常に開けることを確認する。
//
// 実行方法:
//   npm install --no-save playwright-core
//   python3 -m http.server 8794 &
//   node tests/startup_robustness.playwright.test.mjs
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
  const videoBaseName = path.basename(videoPath);
  const iphone = devices["iPhone 13"];
  const browser = await chromium.launch({ executablePath: CHROMIUM_PATH, headless: true });

  console.log("=== シナリオ1: 巨大な自動保存データ（大きなサムネイルを持つフリーズ多数）があっても正常に起動する ===");
  {
    const context = await browser.newContext({ ...iphone });
    const page = await context.newPage();
    const pageErrors = [];
    page.on("pageerror", (e) => pageErrors.push(e.message));

    await page.goto(BASE_URL, { waitUntil: "load" });
    await page.click("#guideCloseBtn").catch(() => {});

    // 30件のフリーズ、それぞれに80KB相当のダミーサムネイル(dataURL文字列)を持たせた、
    // 保存サイズ上限（2MB）を確実に超える自動保存データを仕込む（合計で2.4MB程度）
    const seedResult = await page.evaluate((name) => {
      const bigThumb = "data:image/jpeg;base64," + "A".repeat(80 * 1024);
      const freezes = [];
      for (let i = 0; i < 30; i++) {
        freezes.push({
          id: "fz-seed-" + i, time: i, name: "巨大データ" + i, background: "mono",
          brush_shape: "round", maskMode: "brush", strokes: [{ width: 0.1, points: [[0.1, 0.1], [0.2, 0.2]] }],
          thumb: bigThumb
        });
      }
      const payload = {
        revealSec: 0.5, slideSec: 0.5, holdSec: 2, monoContrast: 1, titleBounce: false,
        audioDuringFreeze: "mute", reveal: "wipe",
        shadowEnabled: true, shadowColor: "#FF6432", shadowAlpha: 0.8, shadowDistanceRatio: 0.03,
        shadowDirection: "auto", shadowBlurRatio: 0, shadowSource: "same",
        outputMode: "original", freezes: freezes, logo: null
      };
      const text = JSON.stringify(payload);
      localStorage.setItem("spotlightReel:v3:" + name, text);
      return { bytes: new Blob([text]).size, freezeCount: freezes.length };
    }, videoBaseName);
    check(seedResult.bytes > 1024 * 1024, "仕込んだ自動保存データが1MBを超える巨大なものになっている: " + seedResult.bytes + " bytes");

    await page.setInputFiles("#videoFileInput", videoPath);
    await page.waitForFunction(() => document.getElementById("video").duration > 0, null, { timeout: 10000 });

    // 復元は動画選択後に遅延実行されるため、restoreStatusに文言が入るまで待つ
    await page.waitForFunction(
      () => document.getElementById("restoreStatus").textContent.indexOf("復元しました") >= 0,
      null, { timeout: 5000 }
    );
    const restoreText = await page.textContent("#restoreStatus");
    check(restoreText.indexOf("30") >= 0, "巨大な自動保存データからでも30件のフリーズが復元される: " + restoreText);

    const freezeCount = await page.evaluate(() => appState.freezes.length);
    check(freezeCount === 30, "appState.freezesにも30件反映されている: " + freezeCount);

    const cardCount = await page.locator(".freeze-card").count();
    check(cardCount === 30, "フリーズ一覧にも30件のカードが表示される: " + cardCount);

    // 復元直後にもう一度保存させ（何らかの操作をトリガーに）、サイズ上限を超える場合は
    // サムネイルを諦めて座標データだけが保存されることを確認する
    const savedResult = await page.evaluate((name) => {
      saveToLocalStorage();
      const raw = localStorage.getItem("spotlightReel:v3:" + name);
      const data = JSON.parse(raw);
      return {
        bytes: new Blob([raw]).size,
        freezeCount: data.freezes.length,
        firstThumbEmpty: data.freezes[0].thumb === "",
        firstStrokesKept: data.freezes[0].strokes.length === 1
      };
    }, videoBaseName);
    check(savedResult.bytes < seedResult.bytes,
      "サイズ上限を超えるため、再保存時はサムネイルを諦めてデータが小さくなる: " +
      savedResult.bytes + " bytes（元は " + seedResult.bytes + " bytes）");
    check(savedResult.freezeCount === 30, "再保存後もフリーズの件数自体は変わらない: " + savedResult.freezeCount);
    check(savedResult.firstThumbEmpty === true, "サムネイルは空文字になる（座標データは失わない）");
    check(savedResult.firstStrokesKept === true, "ストローク（座標データ）は保持される");

    check(pageErrors.length === 0, "ページ例外が発生していない: " + JSON.stringify(pageErrors));
    await context.close();
  }

  console.log("");
  console.log("=== シナリオ2: 壊れたJSON（自動保存データが破損）でもクラッシュせず、初期状態で開ける ===");
  {
    const context = await browser.newContext({ ...iphone });
    const page = await context.newPage();
    const pageErrors = [];
    page.on("pageerror", (e) => pageErrors.push(e.message));

    await page.goto(BASE_URL, { waitUntil: "load" });
    await page.click("#guideCloseBtn").catch(() => {});

    await page.evaluate((name) => {
      localStorage.setItem("spotlightReel:v3:" + name, "{ this is not valid JSON !!!");
    }, videoBaseName);

    await page.setInputFiles("#videoFileInput", videoPath);
    await page.waitForFunction(() => document.getElementById("video").duration > 0, null, { timeout: 10000 });
    await page.waitForTimeout(500); // 遅延実行される復元処理が完了するのを待つ

    const freezeCount = await page.evaluate(() => appState.freezes.length);
    check(freezeCount === 0, "壊れたJSONの場合、フリーズ0件（初期状態）で開始する: " + freezeCount);
    check(await page.isVisible("#playerSection"), "壊れたJSONがあっても動画プレイヤー自体は正常に表示される");

    // 壊れたデータのままでも、引き続き普通に操作できることを確認する
    // （クラッシュループにならず、アプリとして機能し続けることの確認）
    await page.click("#addFreezeBtn");
    await page.waitForFunction(() => !document.getElementById("editorSection").hidden, null, { timeout: 5000 });
    await page.click("#cancelFreezeBtn");
    await page.waitForFunction(() => document.getElementById("editorSection").hidden, null, { timeout: 5000 });
    check(true, "壊れたJSONがあっても、その後のフリーズ追加・キャンセル操作は正常に行える");

    check(pageErrors.length === 0, "ページ例外が発生していない: " + JSON.stringify(pageErrors));
    await context.close();
  }

  console.log("");
  console.log("=== シナリオ3: 起動処理が致命的に失敗した場合の復旧UI（「初期化して開く」） ===");
  {
    const context = await browser.newContext({ ...iphone });
    const page = await context.newPage();
    const pageErrors = [];
    page.on("pageerror", (e) => pageErrors.push(e.message));

    await page.goto(BASE_URL, { waitUntil: "load" });
    await page.click("#guideCloseBtn").catch(() => {});

    // このアプリのキーだと分かる、無関係なゴミデータを仕込んでおく
    await page.evaluate(() => {
      localStorage.setItem("spotlightReel.ghSettings.v1", "not json either");
      localStorage.setItem("spotlightReel:v3:dummy.mp4", "also not json");
    });

    check(await page.evaluate(() => document.getElementById("fatalInitBanner") === null),
      "通常時は復旧バナーは表示されていない");

    // init()自体が例外で失敗した状況を、復旧処理を直接呼んで再現する
    // （実際にinit内部で例外を発生させるのは難しいため、失敗時のハンドラ自体の
    // 動作を検証する）
    await page.evaluate(() => { handleFatalInitError(new Error("擬似的な起動失敗（テスト用）")); });

    check(await page.isVisible("#fatalInitBanner"), "起動失敗時に復旧バナーが表示される");
    const bannerText = await page.textContent("#fatalInitBanner");
    check(bannerText.indexOf("読み込みに失敗") >= 0, "「保存データの読み込みに失敗しました」旨のメッセージが表示される: " + bannerText);
    check(bannerText.indexOf("擬似的な起動失敗") >= 0, "エラー内容の詳細も表示される");
    check(await page.isVisible("#fatalInitResetBtn"), "「初期化して開く」ボタンが表示される");

    await page.click("#fatalInitResetBtn");
    await page.waitForLoadState("load");

    // guideCloseBtnを押す（＝ガイド既読キーを新たに書き込む）より前に、
    // 「初期化して開く」直後の時点でこのアプリのキーが本当に全て消えているかを確認する
    const remainingKeys = await page.evaluate(() => {
      const keys = [];
      for (let i = 0; i < localStorage.length; i++) keys.push(localStorage.key(i));
      return keys.filter((k) => k && k.indexOf("spotlightReel") === 0);
    });
    check(remainingKeys.length === 0,
      "「初期化して開く」を押すと、このアプリのlocalStorageキーが全て消えて再読み込みされる: " + JSON.stringify(remainingKeys));
    check(await page.evaluate(() => document.getElementById("fatalInitBanner") === null),
      "再読み込み後は復旧バナーが出ておらず、通常どおりページが開けている");

    await page.click("#guideCloseBtn").catch(() => {});
    check(await page.isVisible("#playerSection") === false || await page.isVisible("#pickVideoBtn"),
      "再読み込み後、通常の画面（動画選択前の状態）が表示されている");

    check(pageErrors.length === 0, "ページ例外が発生していない: " + JSON.stringify(pageErrors));
    await context.close();
  }

  await browser.close();

  console.log("");
  console.log(passed + " 件成功 / " + failed + " 件失敗");
  if (failed > 0) process.exit(1);
}

main().catch((err) => {
  console.error("テスト実行中に例外:", err);
  process.exit(1);
});
