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
import zlib from "node:zlib";
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

/** 効果音ライブラリのアップロードテスト用に、ごく短い有効なPCM16 WAVファイルを作る（外部依存なし） */
function prepareTestSfxWav(fileName, durationSec) {
  const sr = 8000;
  const n = Math.round(sr * durationSec);
  const dataBytes = n * 2;
  const buf = Buffer.alloc(44 + dataBytes);
  buf.write("RIFF", 0);
  buf.writeUInt32LE(36 + dataBytes, 4);
  buf.write("WAVE", 8);
  buf.write("fmt ", 12);
  buf.writeUInt32LE(16, 16);
  buf.writeUInt16LE(1, 20);   // PCM
  buf.writeUInt16LE(1, 22);   // mono
  buf.writeUInt32LE(sr, 24);
  buf.writeUInt32LE(sr * 2, 28);
  buf.writeUInt16LE(2, 32);
  buf.writeUInt16LE(16, 34);
  buf.write("data", 36);
  buf.writeUInt32LE(dataBytes, 40);
  for (let i = 0; i < n; i++) {
    const sample = Math.round(Math.sin(2 * Math.PI * 440 * (i / sr)) * 16000);
    buf.writeInt16LE(sample, 44 + i * 2);
  }
  const out = path.join(os.tmpdir(), fileName);
  fs.writeFileSync(out, buf);
  return out;
}

let failed = 0;
let passed = 0;
function check(cond, label) {
  if (cond) { passed++; console.log("  ok - " + label); }
  else { failed++; console.log("  NG - " + label); }
}

/* ---------------- サーバー確認（extract.yml）用の api.github.com モック ----------------
 * 「切り抜き結果を確認」は、静止フレームJPEG＋params.jsonをjob-confirm-<tag>ブランチへ
 * コミットし、extract.ymlをdispatch・ポーリングし、完了したらpreview.pngを取得して
 * 画面に表示する（実装はrunServerMaskPreview、index.html参照）。ここではapi.github.comへの
 * 通信を全てモックし、実際のGitHub Actionsを起動せずにこのフロー全体を検証する。 */

const CONFIRM_OWNER = "Digital-twin-creator";
const CONFIRM_REPO = "spotlight-jobs";
const CONFIRM_TOKEN = "github_pat_test_dummy_token";

const CONFIRM_CORS_HEADERS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "*",
  "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
};

/** テスト用に、width x height の単色RGB PNGバイナリを組み立てる（preview.pngダウンロード検証用。画素の中身は無関係）。 */
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
    raw[y * rowBytes] = 0; // フィルタなし
    for (let x = 0; x < width; x++) {
      const off = y * rowBytes + 1 + x * 3;
      raw[off] = 200; raw[off + 1] = 200; raw[off + 2] = 200;
    }
  }
  const idat = zlib.deflateSync(raw);
  const signature = Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]);
  return Buffer.concat([signature, chunk("IHDR", ihdr), chunk("IDAT", idat), chunk("IEND", Buffer.alloc(0))]);
}

const TEST_PREVIEW_PNG = makeTestPng(16, 16);

function makeConfirmMock(overrides) {
  return Object.assign({
    tag: null, runId: 777001,
    createReleaseCalls: 0, getRefCalls: 0, getCommitCalls: 0,
    blobCalls: 0, createTreeCalls: 0, createCommitCalls: 0, createRefCalls: 0,
    dispatchCalls: 0, listRunsCalls: 0, getRunCalls: 0, getReleaseByTagCalls: 0,
    downloadAssetCalls: 0,
    matchRunAfterCalls: 1,
    runStatusSequence: [{ status: "completed", conclusion: "success" }],
    includeCacheAsset: true,
    cacheAssetTimeLabel: null, // 例: "1.500"（省略時はリクエストされたparams.jsonのtimeから自動決定）
  }, overrides);
}

/** api.github.com 宛のリクエストを全て、extract.yml確認フローに沿ってモックで返すルートハンドラを作る */
function makeConfirmMockRouter(mock) {
  return async (route) => {
    const request = route.request();
    const url = new URL(request.url());
    const method = request.method();
    const pathname = url.pathname;

    if (method === "OPTIONS") {
      await route.fulfill({ status: 204, headers: CONFIRM_CORS_HEADERS, body: "" });
      return;
    }

    const m = /^\/repos\/([^/]+)\/([^/]+)\//.exec(pathname);
    const owner = m ? m[1] : CONFIRM_OWNER;
    const repo = m ? m[2] : CONFIRM_REPO;
    const json = (status, obj) => route.fulfill({
      status, headers: { ...CONFIRM_CORS_HEADERS, "content-type": "application/json" }, body: JSON.stringify(obj),
    });

    if (method === "POST" && pathname === `/repos/${owner}/${repo}/releases`) {
      mock.createReleaseCalls++;
      let body = {};
      try { body = JSON.parse(request.postData() || "{}"); } catch (e) { /* 無視 */ }
      // 1回のテストファイル内で複数回「確認」する（＝複数回Releaseを作る）ため、
      // 常に最新のtagで上書きする（以降のrun一覧/Release取得はこのtagで一致判定する）。
      mock.tag = body.tag_name;
      return json(201, {
        id: 1, tag_name: body.tag_name,
        html_url: `https://github.com/${owner}/${repo}/releases/tag/${body.tag_name}`,
      });
    }

    if (method === "GET" && pathname === `/repos/${owner}/${repo}/git/ref/heads/main`) {
      mock.getRefCalls++;
      return json(200, { ref: "refs/heads/main", object: { sha: "base-commit-sha", type: "commit" } });
    }

    if (method === "GET" && pathname === `/repos/${owner}/${repo}/git/commits/base-commit-sha`) {
      mock.getCommitCalls++;
      return json(200, { sha: "base-commit-sha", tree: { sha: "base-tree-sha" } });
    }

    // POST .../git/blobs … frame.jpg（1回目）・params.json（2回目）
    if (method === "POST" && pathname === `/repos/${owner}/${repo}/git/blobs`) {
      mock.blobCalls++;
      if (mock.blobCalls === 2) {
        // params.json のblob。中身からtime（フリーズのtime）を読み取り、
        // アルファキャッシュのアセット名（video_<time>.npz）を決めるのに使う。
        let body = {};
        try { body = JSON.parse(request.postData() || "{}"); } catch (e) { /* 無視 */ }
        try {
          const params = JSON.parse(Buffer.from(body.content || "", "base64").toString("utf-8"));
          mock.lastParamsTime = params.time;
        } catch (e) { /* 無視 */ }
      }
      return json(201, { sha: "blob-sha-" + mock.blobCalls });
    }

    if (method === "POST" && pathname === `/repos/${owner}/${repo}/git/trees`) {
      mock.createTreeCalls++;
      let body = {};
      try { body = JSON.parse(request.postData() || "{}"); } catch (e) { /* 無視 */ }
      mock.treePayload = body;
      return json(201, { sha: "new-tree-sha" });
    }

    if (method === "POST" && pathname === `/repos/${owner}/${repo}/git/commits`) {
      mock.createCommitCalls++;
      return json(201, { sha: "new-commit-sha" });
    }

    if (method === "POST" && pathname === `/repos/${owner}/${repo}/git/refs`) {
      mock.createRefCalls++;
      let body = {};
      try { body = JSON.parse(request.postData() || "{}"); } catch (e) { /* 無視 */ }
      mock.refPayload = body;
      return json(201, { ref: body.ref, object: { sha: body.sha } });
    }

    if (method === "POST" && /\/actions\/workflows\/extract\.yml\/dispatches$/.test(pathname)) {
      mock.dispatchCalls++;
      await route.fulfill({ status: 204, headers: CONFIRM_CORS_HEADERS, body: "" });
      return;
    }

    if (method === "GET" && /\/actions\/workflows\/extract\.yml\/runs$/.test(pathname)) {
      mock.listRunsCalls++;
      const runs = mock.listRunsCalls >= mock.matchRunAfterCalls
        ? [{
            id: mock.runId,
            name: "extract " + mock.tag,
            status: mock.runStatusSequence[0].status,
            conclusion: mock.runStatusSequence[0].conclusion,
            created_at: new Date().toISOString(),
            html_url: `https://github.com/${owner}/${repo}/actions/runs/${mock.runId}`,
          }]
        : [];
      return json(200, { workflow_runs: runs });
    }

    const runStatusMatch = /\/actions\/runs\/(\d+)$/.exec(pathname);
    if (method === "GET" && runStatusMatch) {
      mock.getRunCalls++;
      const idx = Math.min(mock.getRunCalls - 1, mock.runStatusSequence.length - 1);
      const state = mock.runStatusSequence[idx];
      return json(200, {
        id: mock.runId, status: state.status, conclusion: state.conclusion,
        html_url: `https://github.com/${owner}/${repo}/actions/runs/${mock.runId}`,
      });
    }

    if (method === "GET" && pathname === `/repos/${owner}/${repo}/releases/tags/${mock.tag}`) {
      mock.getReleaseByTagCalls++;
      const timeLabel = mock.cacheAssetTimeLabel || Number(mock.lastParamsTime || 0).toFixed(3);
      const assets = [{ name: "preview.png", id: 501 }];
      if (mock.includeCacheAsset) assets.push({ name: `video_${timeLabel}.npz`, id: 502 });
      return json(200, {
        tag_name: mock.tag,
        html_url: `https://github.com/${owner}/${repo}/releases/tag/${mock.tag}`,
        assets,
      });
    }

    if (method === "GET" && pathname === `/repos/${owner}/${repo}/releases/assets/501`) {
      mock.downloadAssetCalls++;
      await route.fulfill({
        status: 200,
        headers: { ...CONFIRM_CORS_HEADERS, "content-type": "image/png" },
        body: TEST_PREVIEW_PNG,
      });
      return;
    }

    if (method === "GET" && pathname === `/repos/${owner}/${repo}/releases/assets/502`) {
      mock.downloadAssetCalls++;
      await route.fulfill({
        status: 200,
        headers: { ...CONFIRM_CORS_HEADERS, "content-type": "application/octet-stream" },
        body: Buffer.from([0, 1, 2, 3]), // 中身は無関係（treeエントリに載るかどうかだけを見る）
      });
      return;
    }

    console.log("  [警告] モックされていない確認フローAPIリクエスト: " + method + " " + pathname);
    await route.fulfill({ status: 404, headers: CONFIRM_CORS_HEADERS, body: "{}" });
  };
}

async function routeConfirmApiGithub(page, mock) {
  await page.route("https://api.github.com/**", makeConfirmMockRouter(mock));
}

async function fillConfirmGhSettings(page) {
  await page.evaluate(() => { document.getElementById("ghSettingsDetails").open = true; });
  await page.fill("#ghUserInput", CONFIRM_OWNER);
  await page.fill("#ghRepoInput", CONFIRM_REPO);
  await page.fill("#ghTokenInput", CONFIRM_TOKEN);
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

  // 「切り抜き結果を確認」は本番と同じモデルでのサーバー確認（extract.ymlのdispatch）に
  // 置き換わっているため、このファイル全体を通してGitHub連携設定を入力し、
  // api.github.comへの通信をモックしておく（実際のGitHub Actionsは起動しない）。
  const confirmMock = makeConfirmMock();
  await routeConfirmApiGithub(page, confirmMock);
  await fillConfirmGhSettings(page);

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

  console.log("");
  console.log("=== 「切り抜き結果を確認」ボタン：自動系モードでのみ表示され、押すとサーバー確認（extract.yml）が走る ===");
  check(await page.isVisible("#maskPreviewBtn"), "mask='auto'のときは「切り抜き結果を確認」ボタンが表示される");
  check(await page.isHidden("#maskPreviewModal"), "押す前はモーダルが隠れている");
  await page.click("#maskPreviewBtn");
  check(await page.isVisible("#maskPreviewModal"), "ボタンを押すと（結果を待たず）すぐにモーダルが表示される");
  check(await page.isVisible("#maskPreviewProgressWrap"), "進捗バーが表示される");
  check(await page.isVisible("#maskPreviewSkipBtn"), "「確認せずに進む」の選択肢が表示される");
  const modalBox = await page.locator("#maskPreviewModal").boundingBox();
  const viewportSize = page.viewportSize();
  check(Math.abs(modalBox.width - viewportSize.width) < 1 && Math.abs(modalBox.height - viewportSize.height) < 1,
    "モーダルは画面幅いっぱいの全画面オーバーレイになっている: " + JSON.stringify({ modalBox, viewportSize }));
  const closeBtnBox = await page.locator("#maskPreviewCloseBtn").boundingBox();
  check(closeBtnBox.width >= 44 && closeBtnBox.height >= 44,
    "右上の閉じるボタンが十分大きい（44px角以上）: " + JSON.stringify(closeBtnBox));
  await page.waitForFunction(() => {
    var status = document.getElementById("maskPreviewStatus");
    return status && status.textContent.indexOf("確認完了") >= 0;
  }, null, { timeout: 15000 });
  const previewCanvasSize = await page.evaluate(() => {
    var c = document.getElementById("maskPreviewCanvas");
    return { w: c.width, h: c.height };
  });
  check(previewCanvasSize.w > 1 && previewCanvasSize.h > 1,
    "サーバーから取得したpreview.pngがcanvasに実際のサイズで描画されている: " + JSON.stringify(previewCanvasSize));
  check((await page.textContent("#maskPreviewStatus")).indexOf("確認完了") >= 0,
    "確認完了のステータス文言が表示される");
  check(await page.isHidden("#maskPreviewProgressWrap"), "完了すると進捗バーが隠れる");
  check(await page.isHidden("#maskPreviewSkipBtn"), "完了すると「確認せずに進む」も隠れる（もう待つものが無いため）");
  check(confirmMock.createReleaseCalls === 1, "確認用のReleaseが作成される");
  check(confirmMock.blobCalls === 2, "静止フレーム(frame.jpg)とparams.jsonの2つのblobが作成される: " + confirmMock.blobCalls);
  check(confirmMock.treePayload && confirmMock.treePayload.tree.some((e) => e.path === "frame.jpg")
    && confirmMock.treePayload.tree.some((e) => e.path === "params.json"),
    "treeにframe.jpgとparams.jsonが含まれる: " + JSON.stringify(confirmMock.treePayload && confirmMock.treePayload.tree));
  check(confirmMock.dispatchCalls === 1, "render.ymlではなくextract.ymlがdispatchされる（モックのURLパターンが一致）: " + confirmMock.dispatchCalls);
  check(confirmMock.downloadAssetCalls >= 1, "Releaseアセット（preview.png）を認証付きでダウンロードする");
  const confirmedAlphaOnDraft = await page.evaluate(() => draft.confirmedAlpha);
  check(!!confirmedAlphaOnDraft && confirmedAlphaOnDraft.tag === confirmMock.tag,
    "確認完了後、draft.confirmedAlphaに確認結果（タグ等）が記録される（本番レンダリング時の再利用用）: " +
    JSON.stringify(confirmedAlphaOnDraft));

  await page.click("#maskPreviewCloseBtn");
  check(await page.isHidden("#maskPreviewModal"), "閉じるボタンでモーダルが隠れる");
  const canvasSizeAfterCloseBtn = await page.evaluate(() => {
    var c = document.getElementById("maskPreviewCanvas");
    return { w: c.width, h: c.height };
  });
  check(canvasSizeAfterCloseBtn.w === 0 && canvasSizeAfterCloseBtn.h === 0,
    "✕で閉じるとプレビューcanvasのバッキングストアを即座に解放する（width/height=0）: " + JSON.stringify(canvasSizeAfterCloseBtn));

  console.log("");
  console.log("=== サーバー確認：タブのバックグラウンド化等で見失った結果も、次に開いた時に拾える（pendingConfirm復旧） ===");
  {
    // タブのバックグラウンド化・端末側の再読み込み等で元のPromiseチェーンが失われても、
    // サーバー側では既に完了していることがある（実機で確認された事例）。次にこの
    // フリーズで確認を開いた時、dispatchし直さずそのままその結果を拾えることを検証する。
    const recoveryTag = "job-confirm-recovery-test";
    const currentTime = await page.evaluate(() => draft.time);
    const currentModel = await page.evaluate(() => resolveMaskModel(appState.maskModel));
    const currentVideoFileName = await page.evaluate(() => appState.videoFileName);
    await page.evaluate(({ tag, time, model, videoFileName }) => {
      localStorage.setItem("spotlightReel:pendingConfirm:" + videoFileName, JSON.stringify({
        tag, time, model, videoFileName, dispatchedAt: Date.now()
      }));
    }, { tag: recoveryTag, time: currentTime, model: currentModel, videoFileName: currentVideoFileName });

    const recoveryMock = makeConfirmMock({
      tag: recoveryTag, runId: 777015, cacheAssetTimeLabel: Number(currentTime).toFixed(3)
    });
    await routeConfirmApiGithub(page, recoveryMock);
    await page.evaluate(() => { draft.confirmedAlpha = null; });

    await page.click("#maskPreviewBtn");
    await page.waitForFunction(() => document.getElementById("maskPreviewStatus").textContent.indexOf("確認完了") >= 0,
      null, { timeout: 15000 });

    check(recoveryMock.createReleaseCalls === 0, "復旧時はReleaseを新規作成しない（既存のものをそのまま使う）: " + recoveryMock.createReleaseCalls);
    check(recoveryMock.blobCalls === 0, "復旧時は静止フレーム等の再アップロードをしない: " + recoveryMock.blobCalls);
    check(recoveryMock.dispatchCalls === 0, "復旧時はextract.ymlを再dispatchしない: " + recoveryMock.dispatchCalls);
    check(recoveryMock.getReleaseByTagCalls >= 1, "保存しておいたタグでReleaseを直接確認する");
    const confirmedAlphaAfterRecovery = await page.evaluate(() => draft.confirmedAlpha);
    check(!!confirmedAlphaAfterRecovery && confirmedAlphaAfterRecovery.tag === recoveryTag,
      "復旧した結果がdraft.confirmedAlphaに記録される: " + JSON.stringify(confirmedAlphaAfterRecovery));
    const pendingAfterRecovery = await page.evaluate((videoFileName) =>
      localStorage.getItem("spotlightReel:pendingConfirm:" + videoFileName), currentVideoFileName);
    check(pendingAfterRecovery === null, "復旧が完了するとpendingConfirmの記録は消される");

    await page.click("#maskPreviewCloseBtn");
  }

  console.log("");
  console.log("=== サーバー確認：失敗時はエラーメッセージとActionsへのリンクを表示する（無言にならない） ===");
  {
    const failMock = makeConfirmMock({
      runId: 777016,
      runStatusSequence: [{ status: "completed", conclusion: "failure" }],
    });
    await routeConfirmApiGithub(page, failMock);

    await page.click("#maskPreviewBtn");
    await page.waitForFunction(() => document.getElementById("maskPreviewStatus").textContent.indexOf("失敗しました") >= 0,
      null, { timeout: 15000 });

    check(await page.isVisible("#maskPreviewActionsLink"), "失敗時はActionsページへのリンクが表示される（無言にならない）");
    const actionsHref = await page.getAttribute("#maskPreviewActionsLink", "href");
    check(!!actionsHref && actionsHref.indexOf("/actions/runs/777016") >= 0,
      "リンク先が該当のActions run（777016）を指している: " + actionsHref);
    check((await page.textContent("#maskPreviewStatus")).indexOf("確認せずに進む") >= 0,
      "エラーメッセージに「確認せずに進む」で続けられる旨が含まれる");

    await page.click("#maskPreviewCloseBtn");
    check(await page.isHidden("#maskPreviewActionsLink"), "閉じるとActionsリンクも隠れる（次回の確認に持ち越さない）");
  }

  console.log("");
  console.log("=== 「確認せずに進む」：結果を待たずにモーダルを閉じられ、confirmedAlphaは記録されない ===");
  const skipMock = makeConfirmMock({ runId: 777010 });
  await routeConfirmApiGithub(page, skipMock);
  await page.evaluate(() => { draft.confirmedAlpha = null; });
  await page.click("#maskPreviewBtn");
  await page.waitForFunction(() => !document.getElementById("maskPreviewSkipBtn").hidden, null, { timeout: 3000 });
  await page.click("#maskPreviewSkipBtn");
  check(await page.isHidden("#maskPreviewModal"), "「確認せずに進む」ですぐにモーダルが閉じる（結果を待たない）");
  await page.waitForTimeout(500); // 打ち切り後もバックグラウンドの非同期処理が例外を投げないことを確認する猶予
  const confirmedAlphaAfterSkip = await page.evaluate(() => draft.confirmedAlpha);
  check(!confirmedAlphaAfterSkip, "「確認せずに進む」を選ぶと確認結果は記録されない（そのままdraft.confirmedAlphaはnullのまま）");
  check(pageErrors.length === 0, "スキップ後もページ例外が発生していない: " + JSON.stringify(pageErrors));

  console.log("");
  console.log("=== サーバー確認：Escキーでも閉じ、閉じた後は編集操作が復帰する（全画面化により背景タップは廃止） ===");
  // 全画面オーバーレイ化により、映像とチロームが画面全体を隙間なく覆うため、
  // 「カード外（背景）をタップして閉じる」という以前の操作は成立しなくなった
  // （閉じるボタン・Escキーのみが閉じる手段になる）。
  const drawBox = await page.locator("#drawCanvas").boundingBox();
  const escTapMock = makeConfirmMock({ runId: 777011 });
  await routeConfirmApiGithub(page, escTapMock);

  await page.click("#maskPreviewBtn");
  await page.waitForFunction(() => document.getElementById("maskPreviewStatus").textContent.indexOf("確認完了") >= 0,
    null, { timeout: 15000 });
  check(await page.isVisible("#maskPreviewModal"), "再度開くとモーダルが表示される");
  await page.keyboard.press("Escape");
  check(await page.isHidden("#maskPreviewModal"), "Escキーでもモーダルが閉じる");

  const closeBtnMock = makeConfirmMock({ runId: 777012 });
  await routeConfirmApiGithub(page, closeBtnMock);
  await page.click("#maskPreviewBtn");
  await page.waitForFunction(() => document.getElementById("maskPreviewStatus").textContent.indexOf("確認完了") >= 0,
    null, { timeout: 15000 });
  await page.click("#maskPreviewCloseBtn");
  check(await page.isHidden("#maskPreviewModal"), "閉じるボタンでモーダルが閉じる");

  // 閉じた直後、下に隠れていた描画キャンバスへのタッチがすぐ復帰する
  // （モーダルの残骸がブロックし続けていないことの確認）
  const strokesBeforeTouchBack = await page.evaluate(() => draft.strokes.length);
  await dragStroke(page, drawBox, 0.15, 0.6, 0.3, 0.7);
  const strokesAfterTouchBack = await page.evaluate(() => draft.strokes.length);
  check(strokesAfterTouchBack === strokesBeforeTouchBack + 1,
    "モーダルを閉じた直後、描画キャンバスへのブラシ操作がすぐ復帰する（裏でブロックされていない）");
  // 上の確認用に描いたストロークは、後続のauto+brushテスト（drawn順の検証）に
  // 影響しないようここで消しておく
  await page.click("#clearStrokesBtn");

  console.log("");
  console.log("=== サーバー確認：GitHub連携の設定が未入力だと通信せずエラー表示になる ===");
  {
    let calledUnexpectedly = false;
    await page.route("https://api.github.com/**", (route) => { calledUnexpectedly = true; route.abort(); });
    // ghSettingsDetailsはフリーズ編集中は隠れた領域(mainSection側)にあるため、
    // page.fillではなく直接値を書き換える（showMaskPreview側は表示状態に関わらず
    // .value.trim()を読むだけなので、これで「未入力状態」を正しく再現できる）。
    await page.evaluate(() => {
      document.getElementById("ghUserInput").value = "";
      document.getElementById("ghTokenInput").value = "";
    });
    await page.click("#maskPreviewBtn");
    await page.waitForTimeout(300);
    check(await page.isHidden("#maskPreviewModal"), "設定未入力の場合、モーダルは開かない");
    const errStatus = await page.evaluate(() => document.getElementById("errorBannerText").textContent);
    check(errStatus.indexOf("設定") >= 0, "GitHub連携設定の入力を促すエラーが表示される: " + errStatus);
    check(calledUnexpectedly === false, "設定未入力の場合、GitHub APIへは一切通信しない");
    const settingsOpenedAfterError = await page.evaluate(() => document.getElementById("ghSettingsDetails").open);
    check(settingsOpenedAfterError === true, "エラー時、GitHub連携の設定パネルが自動的に開く（次に見た時にすぐ入力できる）");
    // 後続のテストのため、設定を元に戻しモックを張り直す
    await page.evaluate(({ u, r, t }) => {
      document.getElementById("ghUserInput").value = u;
      document.getElementById("ghRepoInput").value = r;
      document.getElementById("ghTokenInput").value = t;
    }, { u: CONFIRM_OWNER, r: CONFIRM_REPO, t: CONFIRM_TOKEN });
    await routeConfirmApiGithub(page, makeConfirmMock({ runId: 777020 }));
  }

  await page.selectOption("#maskModeSelect", "brush");
  check(await page.isHidden("#maskPreviewBtn"), "mask='brush'に戻すと「切り抜き結果を確認」ボタンは隠れる");
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

  await page.fill(".title-line-text", "自動＋ブラシ修正テスト");
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
  await page.fill(".title-line-text", "従来ブラシテスト");
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
  console.log("=== 簡易プレビュー：閉じずにフリーズ編集を終えても自動的に閉じ、別フリーズでは改めて追従する ===");
  // フリーズDを新規に追加し、プレビューを開いたまま（閉じずに）「完了」してみる。
  // モーダルは画面全体を覆うposition:fixedのため、実際の指操作では下の「完了」ボタンを
  // タップすることはできない（=ユーザーが誤って古い結果を持ち越す経路は無い）が、
  // 万一プログラム的にcommitFreezeEdit()が呼ばれた場合の安全網として、
  // exitDrawMode側で確実にモーダルを閉じることを直接確認しておく。
  await page.click("#addFreezeBtn");
  await page.waitForFunction(() => !document.getElementById("editorSection").hidden, null, { timeout: 5000 });
  await page.selectOption("#maskModeSelect", "auto");
  const commitWhileOpenMock = makeConfirmMock({ runId: 777013 });
  await routeConfirmApiGithub(page, commitWhileOpenMock);
  await page.click("#maskPreviewBtn");
  await page.waitForFunction(() => document.getElementById("maskPreviewStatus").textContent.indexOf("確認完了") >= 0,
    null, { timeout: 15000 });
  check(await page.isVisible("#maskPreviewModal"), "「完了」を押す前提として、プレビューを開いたままにしておく");
  await page.evaluate(() => { draft.name = "プレビュー開いたまま完了テスト"; commitFreezeEdit(); });
  await page.waitForFunction(() => document.getElementById("editorSection").hidden, null, { timeout: 5000 });
  check(await page.isHidden("#maskPreviewModal"),
    "プレビューを開いたまま「完了」しても、フリーズ編集を抜けると同時にモーダルは自動的に閉じる");

  // 続けてフリーズEを新規に開き、プレビューが（フリーズDの結果を持ち越さず）新しい
  // 静止フレームで改めて解析されることを確認する
  await page.click("#addFreezeBtn");
  await page.waitForFunction(() => !document.getElementById("editorSection").hidden, null, { timeout: 5000 });
  await page.selectOption("#maskModeSelect", "auto");
  check(await page.isHidden("#maskPreviewModal"), "別フリーズの編集を開始した時点でもモーダルは閉じたまま");
  const followMock = makeConfirmMock({ runId: 777014 });
  await routeConfirmApiGithub(page, followMock);
  await page.click("#maskPreviewBtn");
  await page.waitForFunction(() => document.getElementById("maskPreviewStatus").textContent.indexOf("確認完了") >= 0,
    null, { timeout: 15000 });
  check(await page.isVisible("#maskPreviewModal"),
    "別フリーズの編集でも「切り抜き結果を確認」は改めて開いてサーバー確認できる（追従する）");
  const followCanvasSize = await page.evaluate(() => {
    var c = document.getElementById("maskPreviewCanvas");
    return { w: c.width, h: c.height };
  });
  check(followCanvasSize.w > 1 && followCanvasSize.h > 1,
    "新しいフリーズの静止フレームで改めて描画されている: " + JSON.stringify(followCanvasSize));
  await page.click("#maskPreviewCloseBtn");
  await page.click("#cancelFreezeBtn");
  await page.waitForFunction(() => document.getElementById("editorSection").hidden, null, { timeout: 5000 });

  console.log("");
  console.log("=== 全体設定：影の色プリセットUIを見つけやすくするため、既定で開いている ===");
  // 実機で「影の色プリセットが見つからない」という報告があったため、settingsSectionが
  // ページ読み込み直後から既定で開いていて、追加の操作なしに影の色プリセットが見える
  // ことを確認する（以前は既定で閉じていたため見落とされていた）。
  check(await page.evaluate(() => document.getElementById("settingsSection").open),
    "settingsSectionはページ読み込み直後から既定で開いている（影の色プリセットが最初から見える）");
  check(await page.isVisible("#filmColorPresetRow"),
    "何も操作しなくても影の色プリセット行（#filmColorPresetRow）が最初から見えている");

  console.log("");
  console.log("=== 全体設定：影（フィルム色）のオン/オフとスライダー・方向 ===");
  // （既に開いているが、念のため明示的にも開いておく）
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
  console.log("=== 全体設定：自動切り抜きのモデル（mask_options.model） ===");
  check((await page.inputValue("#maskModelSelect")) === "isnet-general-use", "maskModelSelectの既定はisnet-general-use（標準）");
  const styleDefaultModel = (await page.evaluate(() => buildProjectJSON(appState))).style;
  check(!("mask_options" in styleDefaultModel), "既定モデルのままなら style.mask_options キー自体を出力しない（後方互換）: " + JSON.stringify(styleDefaultModel.mask_options));

  await page.selectOption("#maskModelSelect", "birefnet-portrait");
  const styleHqModel = (await page.evaluate(() => buildProjectJSON(appState))).style;
  check(styleHqModel.mask_options && styleHqModel.mask_options.model === "birefnet-portrait",
    "maskModelSelectを'高精度'にするとstyle.mask_options.modelが'birefnet-portrait'になる: " + JSON.stringify(styleHqModel.mask_options));
  await page.selectOption("#maskModelSelect", "isnet-general-use");

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
  await page.fill(".title-line-text", "プレビュー影テスト");
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
    var canvas = document.getElementById("previewCanvas");
    var ctx = canvas.getContext("2d");
    var W = overlaySize.width, H = overlaySize.height;
    // previewCanvasのバッキングストア解像度は、全画面プレビュー時のカクつき対策で
    // window.devicePixelRatioより低いdprに抑えていることがある(PREVIEW_CANVAS_MAX_DPR)。
    // CSSピクセル(W/H基準)から実際の物理ピクセルへは、canvas自身の実測比率で変換する。
    var effectiveDpr = canvas.width / W;
    var bx = W * 0.25; // buildAutoPlaceholderMaskのbxと同じ式
    var x = Math.round((bx + 6) * effectiveDpr), y = Math.round(H * 0.7 * effectiveDpr);
    var d = ctx.getImageData(x, y, 1, 1).data;
    return [d[0], d[1], d[2]];
  });

  const videoWrapBeforePreview = await page.locator("#videoWrap").boundingBox();
  await page.click("#previewBtn");
  check((await page.textContent("#previewBtn")) === "■ 停止", "プレビュー開始でボタン表示が「■ 停止」になる");
  check(await page.isVisible("#previewStopBtn"), "プレビュー開始で全画面用の閉じるボタンが表示される（下の操作バーには指が届かないため）");
  const videoWrapDuringPreview = await page.locator("#videoWrap").boundingBox();
  check(videoWrapDuringPreview.y < 1 && videoWrapDuringPreview.height > videoWrapBeforePreview.height * 1.3,
    "「▶ プレビュー」再生中はvideoWrapが画面全体を覆う全画面オーバーレイになる（編集時より高さが大幅に増える）: " +
    JSON.stringify({ beforeHeight: videoWrapBeforePreview.height, duringHeight: videoWrapDuringPreview.height, duringY: videoWrapDuringPreview.y }));
  // HOLD(0.3s)開始直後はまだスライド前で、プローブ位置(H*0.7)は「人物」で覆われている
  // （＝影色(#00FF00)にはなっていない）はずである。全画面化により1フレームあたりの
  // 描画コストが増えて実時間の進み方が実行環境のCPU負荷に左右されやすくなったため、
  // 固定待機time+固定サンプリングではなく、早い段階のスナップショット1点と、
  // その後の遷移をポーリングで待つ形にして、CPU負荷による揺らぎに頑健にする。
  await page.waitForTimeout(150);
  const earlySlide = await readProbePixel();
  const distFromShadowColor = (px) => Math.hypot(px[0] - 0, px[1] - 255, px[2] - 0);
  check(distFromShadowColor(earlySlide) > 60,
    "プレビュー開始直後（HOLD中のはず）はまだプローブ位置が影色になっていない: " + JSON.stringify(earlySlide));

  // 着地（HOLD+wipe+スライドの合計、既定で0.9s程度）を過ぎると、人物は右へ8%W分ずれた
  // 位置にあり、プローブ位置（元の左端付近）は人物が去って影（#00FF00）が見えているはず。
  // 実時間の進みが遅い環境でも確実に検出できるよう、十分な猶予(8秒)でポーリングする。
  await page.waitForFunction(() => {
    var canvas = document.getElementById("previewCanvas");
    var ctx = canvas.getContext("2d");
    var W = overlaySize.width, H = overlaySize.height;
    var effectiveDpr = canvas.width / W;
    var bx = W * 0.25;
    var x = Math.round((bx + 6) * effectiveDpr), y = Math.round(H * 0.7 * effectiveDpr);
    var d = ctx.getImageData(x, y, 1, 1).data;
    return d[1] > 200 && d[0] < 60 && d[2] < 60; // ほぼ純粋な緑(#00FF00)
  }, null, { timeout: 8000 });
  check(true, "スライドインの着地後、プローブ位置の色が影色（緑）に変わる(=影が現れる演出が反映されている)");

  // フルスクリーン中は#previewBtn自体がvideoWrap(z-index高)の下に隠れて指が届かないため、
  // 実際に押せる#previewStopBtnで止める（下の操作バーには指が届かないという設計どおり）。
  await page.click("#previewStopBtn");
  check((await page.textContent("#previewBtn")) === "▶ プレビュー", "全画面用の閉じるボタンで停止するとプレビューが止まりボタン表示も戻る");
  check(await page.isHidden("#previewStopBtn"), "停止すると全画面用の閉じるボタンも隠れる");
  const videoWrapAfterStop = await page.locator("#videoWrap").boundingBox();
  check(Math.abs(videoWrapAfterStop.height - videoWrapBeforePreview.height) < 5,
    "停止すると通常の編集レイアウト（全画面ではない高さ）に戻る: " +
    JSON.stringify({ beforeHeight: videoWrapBeforePreview.height, afterStopHeight: videoWrapAfterStop.height }));

  await page.click("#cancelFreezeBtn");
  await page.waitForFunction(() => document.getElementById("editorSection").hidden, null, { timeout: 5000 });

  console.log("");
  console.log("=== 効果音ライブラリ：mp3/wavファイルの登録・選択・JSON出力（{file,align}オブジェクト） ===");
  const sfxWavPath = prepareTestSfxWav("spotlight_reel_test_sfx.wav", 0.3);
  await page.setInputFiles("#sfxLibraryFileInput", sfxWavPath);
  await page.waitForFunction(
    () => document.getElementById("sfxLibraryStatus").textContent.indexOf("登録しました") >= 0,
    null, { timeout: 5000 });
  check((await page.evaluate(() => sfxLibrary.length)) === 1,
    "ファイルを選ぶとsfxLibrary（メモリキャッシュ）に1件登録される");
  const libId = await page.evaluate(() => sfxLibrary[0].id);
  check(!!libId, "登録されたファイルにidが振られる: " + libId);

  // ページをリロードしてもIndexedDBから復元されることを確認する
  await page.reload({ waitUntil: "load" });
  await page.click("#guideCloseBtn").catch(() => {});
  await page.waitForFunction(
    (id) => window.sfxLibrary && window.sfxLibrary.some((f) => f.id === id),
    libId, { timeout: 5000 }
  );
  check(true, "リロード後もIndexedDBから効果音ライブラリが復元される（sfxLibraryに同じidが残る）");

  await page.setInputFiles("#videoFileInput", videoPath);
  await page.waitForFunction(() => document.getElementById("video").duration > 0, null, { timeout: 10000 });
  await page.waitForTimeout(300);
  await page.click("#addFreezeBtn");
  await page.waitForFunction(() => !document.getElementById("editorSection").hidden, null, { timeout: 5000 });

  await page.fill(".title-line-text", "SFXライブラリテスト");
  await page.selectOption("#sfxSelect", "lib:" + libId);
  check(!(await page.isHidden("#sfxAlignSelect")), "ライブラリファイルを選ぶとsfxAlignSelectが表示される");
  await page.selectOption("#sfxAlignSelect", "end_at_landing");
  await page.click("#commitFreezeBtn");
  await page.waitForFunction(() => document.getElementById("editorSection").hidden, null, { timeout: 5000 });

  const styleAfterFreezeSfx = await page.evaluate(() => buildProjectJSON(appState));
  const fzWithSfx = styleAfterFreezeSfx.freezes.filter((f) => f.name === "SFXライブラリテスト")[0];
  check(!!fzWithSfx, "テスト前提：名前を付けたフリーズがJSON出力に含まれている");
  check(!!fzWithSfx && fzWithSfx.sfx && fzWithSfx.sfx.align === "end_at_landing" &&
    typeof fzWithSfx.sfx.file === "string" && fzWithSfx.sfx.file.indexOf("sfx/" + libId) === 0,
    "フリーズでライブラリ効果音を選ぶと、JSON出力のsfxが{file,align}オブジェクトになる: " +
    JSON.stringify(fzWithSfx && fzWithSfx.sfx));

  await page.click("#logoSection summary");
  await page.selectOption("#logoSfxSelect", "lib:" + libId);
  check(!(await page.isHidden("#logoSfxAlignSelect")), "ロゴでライブラリファイルを選ぶとlogoSfxAlignSelectが表示される");
  await page.selectOption("#logoSfxAlignSelect", "peak_at_landing");
  check((await page.evaluate(() => appState.logo.sfxLibraryId)) === libId,
    "appState.logo.sfxLibraryIdが選択したファイルのidになる");
  check((await page.evaluate(() => appState.logo.sfxAlign)) === "peak_at_landing",
    "appState.logo.sfxAlignがpeak_at_landingになる");

  console.log("");
  console.log("=== 効果音ライブラリ：一覧からの削除 ===");
  await page.click("#sfxLibrarySection summary");
  page.once("dialog", (d) => d.accept());  // confirm()ダイアログをOKで受ける
  await page.click("#sfxLibraryList button.danger");
  await page.waitForFunction(() => window.sfxLibrary.length === 0, null, { timeout: 5000 });
  check(true, "削除ボタンでライブラリから除去され、sfxLibraryが空になる");

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
