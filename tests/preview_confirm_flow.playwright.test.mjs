#!/usr/bin/env node
// -*- coding: utf-8 -*-
//
// サーバー確認プレビューの新方式（モーダルではなく別ページ preview.html への通常の
// ページ遷移）を、headless Chromium + iPhoneデバイスエミュレーションで検証する回帰テスト。
// 「確認完了 → 結果を見る → preview.htmlで画像表示（naturalWidth>0） → 戻る →
// 編集状態が復元される」という一連の流れを実際にページをまたいで検証する。
//
// この環境（headless Chromium + Playwright）ではhistory.back()によるbfcache復帰が
// 効かず、preview.htmlから戻ると index.html は必ず作り直される（実機のブラウザに
// よってはbfcacheが効くこともあるが、効かない場合の正しい動作を検証するのが本テストの
// 目的でもある）。そのため「戻る」の後は、アプリの設計どおり同じ動画ファイルを選び直し、
// localStorageからの復元（confirmedResult・pendingConfirmの反映を含む）を検証する。
//
// 実行方法:
//   npm install --no-save playwright-core
//   python3 -m http.server 8794 &
//   node tests/preview_confirm_flow.playwright.test.mjs
//
// 環境変数:
//   PW_URL             index.html を配信しているベースURL（既定: http://127.0.0.1:8794/）
//   PW_CHROMIUM_PATH   Chromium実行ファイルのパス
//   PW_VIDEO           テストに使う動画ファイル（VP9/WebM推奨。既定はexamples配下から自動生成）

import { chromium, devices } from "playwright-core";
import path from "node:path";
import os from "node:os";
import { execSync } from "node:child_process";
import fs from "node:fs";
import zlib from "node:zlib";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const REPO_ROOT = path.join(__dirname, "..");
const BASE = process.env.PW_URL || "http://127.0.0.1:8794/";
const INDEX_URL = BASE + "index.html";
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

/* ---------------- api.github.com モック（mask_shadow_ui.playwright.test.mjsと同じ考え方） ---------------- */

const CONFIRM_OWNER = "Digital-twin-creator";
const CONFIRM_REPO = "spotlight-jobs";
const CONFIRM_TOKEN = "github_pat_test_dummy_token";

const CORS_HEADERS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "*",
  "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
};

function makeTestPng(width, height) {
  function chunk(type, data) {
    const len = Buffer.alloc(4);
    len.writeUInt32BE(data.length, 0);
    const typeData = Buffer.concat([Buffer.from(type, "ascii"), data]);
    const crc = Buffer.alloc(4);
    crc.writeInt32BE(crc32(typeData), 0);
    return Buffer.concat([len, typeData, crc]);
  }
  function crc32(buf) {
    const table = crc32.table || (crc32.table = (() => {
      const t = new Int32Array(256);
      for (let n = 0; n < 256; n++) {
        let c = n;
        for (let k = 0; k < 8; k++) c = (c & 1) ? (0xEDB88320 ^ (c >>> 1)) : (c >>> 1);
        t[n] = c;
      }
      return t;
    })());
    let c = 0xFFFFFFFF;
    for (let i = 0; i < buf.length; i++) c = table[(c ^ buf[i]) & 0xFF] ^ (c >>> 8);
    return (c ^ 0xFFFFFFFF) | 0;
  }
  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(width, 0);
  ihdr.writeUInt32BE(height, 4);
  ihdr[8] = 8; ihdr[9] = 2; ihdr[10] = 0; ihdr[11] = 0; ihdr[12] = 0; // 8bit, RGB, no interlace
  const rowBytes = 1 + width * 3;
  const raw = Buffer.alloc(rowBytes * height);
  for (let y = 0; y < height; y++) {
    raw[y * rowBytes] = 0;
    for (let x = 0; x < width; x++) {
      const off = y * rowBytes + 1 + x * 3;
      raw[off] = 210; raw[off + 1] = 190; raw[off + 2] = 160;
    }
  }
  const idat = zlib.deflateSync(raw);
  const signature = Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]);
  return Buffer.concat([signature, chunk("IHDR", ihdr), chunk("IDAT", idat), chunk("IEND", Buffer.alloc(0))]);
}

const TEST_PREVIEW_PNG = makeTestPng(24, 40);

function makeMock(overrides) {
  return Object.assign({
    tag: null, runId: 888001,
    runStatusSequence: [{ status: "completed", conclusion: "success" }],
    matchRunAfterCalls: 1,
    listRunsCalls: 0, getRunCalls: 0,
    lastParamsTime: null,
  }, overrides);
}

function makeMockRouter(mock) {
  return async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const method = request.method();
    const pathname = url.pathname;

    if (method === "OPTIONS") {
      await route.fulfill({ status: 204, headers: CORS_HEADERS, body: "" });
      return;
    }
    const m = /^\/repos\/([^/]+)\/([^/]+)\//.exec(pathname);
    const owner = m ? m[1] : CONFIRM_OWNER;
    const repo = m ? m[2] : CONFIRM_REPO;
    const json = (status, obj) => route.fulfill({
      status, headers: { ...CORS_HEADERS, "content-type": "application/json" }, body: JSON.stringify(obj),
    });

    if (method === "POST" && pathname === `/repos/${owner}/${repo}/releases`) {
      let body = {};
      try { body = JSON.parse(request.postData() || "{}"); } catch (e) { /* 無視 */ }
      mock.tag = body.tag_name;
      return json(201, { id: 1, tag_name: body.tag_name, html_url: `https://github.com/${owner}/${repo}/releases/tag/${body.tag_name}` });
    }
    if (method === "GET" && pathname === `/repos/${owner}/${repo}/git/ref/heads/main`) {
      return json(200, { ref: "refs/heads/main", object: { sha: "base-commit-sha", type: "commit" } });
    }
    if (method === "GET" && pathname === `/repos/${owner}/${repo}/git/commits/base-commit-sha`) {
      return json(200, { sha: "base-commit-sha", tree: { sha: "base-tree-sha" } });
    }
    if (method === "POST" && pathname === `/repos/${owner}/${repo}/git/blobs`) {
      let body = {};
      try { body = JSON.parse(request.postData() || "{}"); } catch (e) { /* 無視 */ }
      try {
        const p = JSON.parse(Buffer.from(body.content || "", "base64").toString("utf-8"));
        if (p && typeof p.time === "number") mock.lastParamsTime = p.time;
      } catch (e) { /* JPEGブロブなので無視 */ }
      return json(201, { sha: "blob-sha-" + Math.random().toString(36).slice(2) });
    }
    if (method === "POST" && pathname === `/repos/${owner}/${repo}/git/trees`) {
      return json(201, { sha: "new-tree-sha" });
    }
    if (method === "POST" && pathname === `/repos/${owner}/${repo}/git/commits`) {
      return json(201, { sha: "new-commit-sha" });
    }
    if (method === "POST" && pathname === `/repos/${owner}/${repo}/git/refs`) {
      let body = {};
      try { body = JSON.parse(request.postData() || "{}"); } catch (e) { /* 無視 */ }
      return json(201, { ref: body.ref, object: { sha: body.sha } });
    }
    if (method === "POST" && /\/actions\/workflows\/extract\.yml\/dispatches$/.test(pathname)) {
      await route.fulfill({ status: 204, headers: CORS_HEADERS, body: "" });
      return;
    }
    if (method === "GET" && /\/actions\/workflows\/extract\.yml\/runs$/.test(pathname)) {
      mock.listRunsCalls++;
      const runs = mock.listRunsCalls >= mock.matchRunAfterCalls
        ? [{ id: mock.runId, name: "extract " + mock.tag, status: mock.runStatusSequence[0].status,
             conclusion: mock.runStatusSequence[0].conclusion, created_at: new Date().toISOString(),
             html_url: `https://github.com/${owner}/${repo}/actions/runs/${mock.runId}` }]
        : [];
      return json(200, { workflow_runs: runs });
    }
    const runStatusMatch = /\/actions\/runs\/(\d+)$/.exec(pathname);
    if (method === "GET" && runStatusMatch) {
      mock.getRunCalls++;
      const idx = Math.min(mock.getRunCalls - 1, mock.runStatusSequence.length - 1);
      const state = mock.runStatusSequence[idx];
      return json(200, { id: mock.runId, status: state.status, conclusion: state.conclusion,
        html_url: `https://github.com/${owner}/${repo}/actions/runs/${mock.runId}` });
    }
    if (method === "GET" && pathname === `/repos/${owner}/${repo}/releases/tags/${mock.tag}`) {
      const timeLabel = Number(mock.lastParamsTime || 0).toFixed(3);
      return json(200, {
        tag_name: mock.tag, html_url: `https://github.com/${owner}/${repo}/releases/tag/${mock.tag}`,
        assets: [{ name: "preview.png", id: 501 }, { name: `video_${timeLabel}.npz`, id: 502 }],
      });
    }
    if (method === "GET" && pathname === `/repos/${owner}/${repo}/contents/preview.png`) {
      return json(200, { name: "preview.png", path: "preview.png", sha: "preview-blob-sha",
        content: TEST_PREVIEW_PNG.toString("base64"), encoding: "base64" });
    }
    console.log("  [警告] モックされていないAPIリクエスト: " + method + " " + pathname);
    await route.fulfill({ status: 404, headers: CORS_HEADERS, body: "{}" });
  };
}

async function routeApi(page, mock) {
  await page.route("https://api.github.com/**", makeMockRouter(mock));
}

async function fillGhSettings(page) {
  await page.evaluate(() => { document.getElementById("ghSettingsDetails").open = true; });
  await page.fill("#ghUserInput", CONFIRM_OWNER);
  await page.fill("#ghRepoInput", CONFIRM_REPO);
  await page.fill("#ghTokenInput", CONFIRM_TOKEN);
}

async function main() {
  const videoPath = prepareTestVideo();
  const iphone = devices["iPhone 13"];
  const browser = await chromium.launch({ executablePath: CHROMIUM_PATH, headless: true });
  const context = await browser.newContext({ ...iphone });
  const page = await context.newPage();
  const pageErrors = [];
  page.on("pageerror", (e) => pageErrors.push(e.message));

  await page.goto(INDEX_URL, { waitUntil: "load" });
  await page.click("#guideCloseBtn").catch(() => {});

  const mock = makeMock();
  await routeApi(page, mock);
  await fillGhSettings(page);

  await page.setInputFiles("#videoFileInput", videoPath);
  await page.waitForFunction(() => document.getElementById("video").duration > 0, null, { timeout: 10000 });
  await page.evaluate(() => { document.getElementById("video").currentTime = 1.0; });
  await page.waitForFunction(() => Math.abs(document.getElementById("video").currentTime - 1.0) < 0.2,
    null, { timeout: 5000 });
  await page.waitForTimeout(400);

  console.log("=== 1回目：フリーズを作成し、静止画モデルでコミットする（後で再編集して確認する） ===");
  await page.click("#addFreezeBtn");
  await page.waitForFunction(() => !document.getElementById("editorSection").hidden, null, { timeout: 5000 });
  await page.evaluate(() => { appState.maskModel = "isnet-general-use"; });
  await page.selectOption("#maskModeSelect", "auto");
  await page.fill(".title-line-text", "確認プレビュー遷移テスト");
  await page.click("#commitFreezeBtn");
  await page.waitForFunction(() => document.getElementById("editorSection").hidden, null, { timeout: 5000 });

  const freezeId = await page.evaluate(() =>
    appState.freezes.filter((f) => f.name === "確認プレビュー遷移テスト")[0].id);
  check(!!freezeId, "テスト前提：フリーズがコミットされ、idが振られている");

  console.log("");
  console.log("=== 再編集で「切り抜き結果を確認」→ 完了後に「結果を見る」ボタンが出る（モーダルは表示しない） ===");
  await page.evaluate((id) => { editFreeze(id); }, freezeId);
  await page.waitForFunction(() => !document.getElementById("editorSection").hidden, null, { timeout: 5000 });
  check((await page.inputValue("#maskModeSelect")) === "auto", "再編集時にmaskModeSelectが'auto'に復元される");
  check(await page.isVisible("#maskPreviewBtn"), "「切り抜き結果を確認」ボタンが表示される");

  await page.click("#maskPreviewBtn");
  check(await page.isVisible("#maskPreviewStatusLine"), "ボタンを押すとステータス文言（テキストのみ）が表示される");
  check(await page.isHidden("#maskPreviewViewResultBtn"), "処理中は「結果を見る」ボタンはまだ隠れている");

  await page.waitForFunction(() => !document.getElementById("maskPreviewViewResultBtn").hidden, null, { timeout: 15000 });
  check((await page.textContent("#maskPreviewStatusLine")).indexOf("確認完了") >= 0,
    "確認完了のステータス文言が表示される");
  check(await page.isVisible("#maskPreviewViewResultBtn"), "確認完了後、「結果を見る」ボタンが表示される");

  const confirmTag = mock.tag;
  check(!!confirmTag, "テスト前提：確認ジョブのタグが記録されている: " + confirmTag);

  console.log("");
  console.log("=== 「結果を見る」で同じタブのpreview.htmlへ遷移し、画像が表示される ===");
  await Promise.all([
    page.waitForURL(/preview\.html\?/, { timeout: 5000 }),
    page.click("#maskPreviewViewResultBtn"),
  ]);
  check(page.url().indexOf("tag=" + encodeURIComponent(confirmTag)) >= 0,
    "preview.htmlのURLに確認タグ(tag)がクエリとして渡される: " + page.url());
  check(page.url().indexOf("time=") >= 0 && page.url().indexOf("model=") >= 0 && page.url().indexOf("video=") >= 0,
    "time/model/videoもクエリに含まれる（戻ってきた時にconfirmedAlphaを記録するため）: " + page.url());

  await page.waitForFunction(() => !document.getElementById("previewImg").hidden, null, { timeout: 10000 });
  const imgNaturalWidth = await page.evaluate(() => document.getElementById("previewImg").naturalWidth);
  check(imgNaturalWidth > 0, "preview.htmlで結果画像が実際に表示されている（naturalWidth>0）: " + imgNaturalWidth);
  check(await page.isHidden("#statusText"), "画像表示後は読み込み中テキストが隠れる");
  check(await page.isVisible("#footerActions"), "下部の操作（OK/やり直す）が表示される");
  check(!(await page.isDisabled("#okBtn")), "本番用キャッシュも見つかっているのでOKボタンは有効");

  const viewportMeta = await page.getAttribute('head meta[name="viewport"]', "content");
  check(viewportMeta && viewportMeta.indexOf("user-scalable=no") === -1 && viewportMeta.indexOf("maximum-scale=1") === -1,
    "viewportメタタグがピンチズームを禁止していない: " + viewportMeta);

  console.log("");
  console.log("=== 「この切り抜きでOK」→ エディタへ戻る（この環境ではbfcacheが効かず再読み込みになる） ===");
  await Promise.all([
    page.waitForURL(/index\.html/, { timeout: 5000 }),
    page.click("#okBtn"),
  ]);
  check(page.url().indexOf("index.html") >= 0, "OKを押すとエディタ（index.html）へ戻る: " + page.url());
  check(await page.isHidden("#playerSection"), "この環境では再読み込みになるため、動画未選択の初期画面に戻っている");

  console.log("");
  console.log("=== 同じ動画ファイルを選び直すと、編集内容とconfirmedAlpha（確認結果）の両方が復元される ===");
  if (await page.isVisible("#guideCloseBtn")) await page.click("#guideCloseBtn");
  await routeApi(page, mock); // 再読み込みでルートハンドラが外れるため張り直す
  // ブラウザのセッション履歴復元（bfcacheとは別の、フォーム入力値を戻る/進むで復元する機能）に
  // より、再読み込み後も#videoFileInputに前回と同じファイルが既に入っていることがあり、
  // その状態でPlaywrightのsetInputFilesに同じファイルを渡すと「変化なし」とみなされ
  // changeイベントが発火しないことがある（実機のネイティブなファイル選択ダイアログでは
  // 毎回changeが発火するため、これはテスト自動化特有の事情）。確実に発火させるため、
  // 一度空にしてから選び直す。
  await page.evaluate(() => { document.getElementById("videoFileInput").value = ""; });
  await page.setInputFiles("#videoFileInput", videoPath);
  await page.waitForFunction(() => document.getElementById("video").duration > 0, null, { timeout: 10000 });
  await page.waitForFunction(() =>
    appState.freezes && appState.freezes.length > 0, null, { timeout: 5000 });
  await page.waitForFunction(() => {
    var t = document.getElementById("restoreStatus").textContent;
    return t.indexOf("復元") >= 0 || t.indexOf("反映") >= 0;
  }, null, { timeout: 5000 });

  const restoredFreeze = await page.evaluate(() =>
    appState.freezes.filter((f) => f.name === "確認プレビュー遷移テスト")[0]);
  check(!!restoredFreeze, "動画を選び直すと、以前コミットしたフリーズが復元される");
  check(!!restoredFreeze && !!restoredFreeze.confirmedAlpha && restoredFreeze.confirmedAlpha.tag === confirmTag,
    "「OK」で確認した結果（confirmedAlpha）が、復元されたフリーズに反映されている: " +
    JSON.stringify(restoredFreeze && restoredFreeze.confirmedAlpha));

  const leftoverRecord = await page.evaluate((videoFileName) =>
    localStorage.getItem("spotlightReel:confirmedResult:" + videoFileName), await page.evaluate(() => appState.videoFileName));
  check(leftoverRecord === null, "反映が終わるとconfirmedResultの記録はlocalStorageから消える");

  check(pageErrors.length === 0, "一連の遷移でページ例外が発生していない: " + JSON.stringify(pageErrors));

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
