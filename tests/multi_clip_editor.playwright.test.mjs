#!/usr/bin/env node
// -*- coding: utf-8 -*-
//
// 複数クリップ（clips[]）エディタUIの回帰テスト。api.github.com への通信はモックし、
// 実際のGitHubには一切アクセスしない。
//
// 検証する内容:
// - 「動画を追加」で2本目のクリップを追加すると、1本目が自動的にclip 0として取り込まれ、
//   クリップ一覧に2枚のカードが表示される
// - クリップを切り替えて、それぞれのクリップに対して独立にフリーズを追加できる
//   （フリーズがクリップをまたいで混ざらない）
// - クリップ間のトランジション種類を選ぶとJSONに反映される
// - 「動画を作る」を押すと、project.json に clips[]（各クリップの video/freezes/transition_out）
//   が書き出され、クリップごとに別々の動画blobがコミットされる（video.<ext>ではなくclip0.<ext>等）
//
// 実行方法:
//   npm install --no-save playwright-core
//   node tests/multi_clip_editor.playwright.test.mjs
//
// 環境変数:
//   PW_URL             index.html を配信しているURL（既定: http://127.0.0.1:8794/index.html）
//   PW_CHROMIUM_PATH   Chromium実行ファイルのパス

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

const OWNER = "Digital-twin-creator";
const REPO = "spotlight-jobs";
const TOKEN = "github_pat_test_dummy_token";

const BASE_COMMIT_SHA = "base-commit-sha";
const BASE_TREE_SHA = "base-tree-sha";
const NEW_TREE_SHA = "new-tree-sha";
const NEW_COMMIT_SHA = "new-commit-sha";

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

const CORS_HEADERS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "*",
  "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
};

function makeMock() {
  return {
    tag: null,
    createReleaseCalls: 0, getRefCalls: 0, getCommitCalls: 0,
    blobCalls: 0, speedTestBlobCalls: 0,
    videoBlobPaths: [], // POSTされた動画系blobのパス名は分からない（blobs APIはpathを持たない）ので、
                          // treeエントリ側（treePayload）でpathを確認する
    createTreeCalls: 0, treePayload: null,
    createCommitCalls: 0, createRefCalls: 0, refPayload: null,
    dispatchCalls: 0, listRunsCalls: 0, getRunCalls: 0,
    runId: 777001,
    jsonPayloadText: null,
  };
}

/** api.github.com 宛のリクエストを全てモックで返すルートハンドラを作る */
function makeGithubMockRouter(mock) {
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
    const owner = m ? m[1] : OWNER;
    const repo = m ? m[2] : REPO;
    const json = (status, obj) => route.fulfill({
      status, headers: { ...CORS_HEADERS, "content-type": "application/json" }, body: JSON.stringify(obj),
    });

    if (method === "POST" && pathname === `/repos/${owner}/${repo}/releases`) {
      mock.createReleaseCalls++;
      try {
        const body = JSON.parse(request.postData() || "{}");
        if (!mock.tag) mock.tag = body.tag_name;
      } catch (e) { /* 無視 */ }
      return json(201, {
        id: 1, tag_name: mock.tag,
        html_url: `https://github.com/${owner}/${repo}/releases/tag/${mock.tag}`,
      });
    }

    if (method === "GET" && pathname === `/repos/${owner}/${repo}/git/ref/heads/main`) {
      mock.getRefCalls++;
      return json(200, { ref: "refs/heads/main", object: { sha: BASE_COMMIT_SHA, type: "commit" } });
    }

    if (method === "GET" && pathname === `/repos/${owner}/${repo}/git/commits/${BASE_COMMIT_SHA}`) {
      mock.getCommitCalls++;
      return json(200, { sha: BASE_COMMIT_SHA, tree: { sha: BASE_TREE_SHA } });
    }

    // POST /repos/{owner}/{repo}/git/blobs
    // 回線速度計測用のダミーblob（マーカー文字列で始まる）はカウントせず素通しする
    // （make_video_job.playwright.test.mjsと同じ理由）。project.jsonのblob本文は、
    // 最初の「本物の」blob呼び出しとしてテキストを保存しておき、後でclips[]の中身を検証する。
    if (method === "POST" && pathname === `/repos/${owner}/${repo}/git/blobs`) {
      let body = {};
      try { body = JSON.parse(request.postData() || "{}"); } catch (e) { /* 無視 */ }
      const decoded = body.content ? Buffer.from(body.content, "base64").toString("utf8") : "";
      if (decoded.startsWith("SPOTLIGHT_UPLOAD_SPEED_TEST")) {
        mock.speedTestBlobCalls++;
        return json(201, { sha: "speed-test-blob-sha" });
      }
      mock.blobCalls++;
      if (mock.blobCalls === 1) mock.jsonPayloadText = decoded;
      return json(201, { sha: "blob-sha-" + mock.blobCalls });
    }

    if (method === "POST" && pathname === `/repos/${owner}/${repo}/git/trees`) {
      mock.createTreeCalls++;
      let body = {};
      try { body = JSON.parse(request.postData() || "{}"); } catch (e) { /* 無視 */ }
      mock.treePayload = body;
      return json(201, { sha: NEW_TREE_SHA });
    }

    if (method === "POST" && pathname === `/repos/${owner}/${repo}/git/commits`) {
      mock.createCommitCalls++;
      return json(201, { sha: NEW_COMMIT_SHA });
    }

    if (method === "POST" && pathname === `/repos/${owner}/${repo}/git/refs`) {
      mock.createRefCalls++;
      let body = {};
      try { body = JSON.parse(request.postData() || "{}"); } catch (e) { /* 無視 */ }
      mock.refPayload = body;
      return json(201, { ref: body.ref, object: { sha: body.sha } });
    }

    if (method === "POST" && /\/actions\/workflows\/render\.yml\/dispatches$/.test(pathname)) {
      mock.dispatchCalls++;
      await route.fulfill({ status: 204, headers: CORS_HEADERS, body: "" });
      return;
    }

    if (method === "GET" && /\/actions\/workflows\/render\.yml\/runs$/.test(pathname)) {
      mock.listRunsCalls++;
      return json(200, {
        workflow_runs: [{
          id: mock.runId, name: "render " + mock.tag, status: "completed", conclusion: "success",
          created_at: new Date().toISOString(),
          html_url: `https://github.com/${owner}/${repo}/actions/runs/${mock.runId}`,
        }],
      });
    }

    const runStatusMatch = /\/actions\/runs\/(\d+)$/.exec(pathname);
    if (method === "GET" && runStatusMatch) {
      mock.getRunCalls++;
      return json(200, {
        id: mock.runId, status: "completed", conclusion: "success",
        html_url: `https://github.com/${owner}/${repo}/actions/runs/${mock.runId}`,
      });
    }

    const releaseTagsMatch = /^\/repos\/[^/]+\/[^/]+\/releases\/tags\/(.+)$/.exec(pathname);
    if (method === "GET" && releaseTagsMatch && decodeURIComponent(releaseTagsMatch[1]) === mock.tag) {
      return json(200, {
        tag_name: mock.tag,
        html_url: `https://github.com/${owner}/${repo}/releases/tag/${mock.tag}`,
        assets: [{
          name: "output.mp4", id: 999,
          browser_download_url: `https://github.com/${owner}/${repo}/releases/download/${mock.tag}/output.mp4`,
        }],
      });
    }

    console.log("  [警告] モックされていないGitHub APIリクエスト: " + method + " " + pathname);
    await route.fulfill({ status: 404, headers: CORS_HEADERS, body: "{}" });
  };
}

async function routeApiGithub(page, mock) {
  await page.route("https://api.github.com/**", makeGithubMockRouter(mock));
}

async function fillGhSettings(page, { user, repo, token }) {
  await page.evaluate(() => { document.getElementById("ghSettingsDetails").open = true; });
  await page.fill("#ghUserInput", user);
  await page.fill("#ghRepoInput", repo);
  await page.fill("#ghTokenInput", token);
}

async function clickMakeVideoBtnAndProceed(page) {
  await page.click("#makeVideoBtn");
  await page.waitForFunction(
    () => document.getElementById("uploadEstimateModal").hidden === false,
    null, { timeout: 5000 }
  );
  await page.click("#uploadEstimateProceedBtn");
  // ブラシで何も塗っていない「簡易フリーズ」を使うテストでは、この後
  // 内容確認の警告モーダル（confirmWarningsModal）が挟まる。出ていれば「このまま作る」で進める。
  await page.waitForTimeout(200);
  const warningsShown = await page.evaluate(() => document.getElementById("confirmWarningsModal").hidden === false);
  if (warningsShown) await page.click("#confirmWarningsProceedBtn");
}

/** シーク不要の位置（0秒）でフリーズを追加し、名前だけ入れてすぐ完了する簡易ヘルパー */
async function addSimpleFreeze(page, name) {
  await page.click("#addFreezeBtn");
  await page.waitForFunction(() => !document.getElementById("editorSection").hidden, null, { timeout: 5000 });
  await page.fill(".title-line-text", name);
  await page.click("#commitFreezeBtn");
  await page.waitForFunction(() => document.getElementById("editorSection").hidden, null, { timeout: 5000 });
}

async function main() {
  const videoPath = prepareTestVideo();
  const iphone = devices["iPhone 13"];
  const browser = await chromium.launch({ executablePath: CHROMIUM_PATH, headless: true });

  console.log("=== シナリオ1: 「動画を追加」でクリップ一覧が現れ、1本目がclip0として取り込まれる ===");
  {
    const context = await browser.newContext({ ...iphone });
    const page = await context.newPage();
    const pageErrors = [];
    page.on("pageerror", (e) => pageErrors.push(e.message));

    await page.goto(BASE_URL, { waitUntil: "load" });
    await page.click("#guideCloseBtn").catch(() => {});
    await page.setInputFiles("#videoFileInput", videoPath);
    await page.waitForFunction(() => document.getElementById("video").duration > 0, null, { timeout: 10000 });

    const clipSectionHiddenBefore = await page.evaluate(() => document.getElementById("clipListSection").hidden);
    check(clipSectionHiddenBefore === true, "1本目を選んだだけではクリップ一覧はまだ表示されない（従来どおりの単一動画扱い）");

    await page.setInputFiles("#clipFileInput", videoPath);
    await page.waitForFunction(() => document.getElementById("clipListSection").hidden === false, null, { timeout: 10000 });
    const clipCardCount = await page.$$eval(".clip-card", (els) => els.length);
    check(clipCardCount === 2, "「動画を追加」後、クリップ一覧に2枚のカードが表示される: " + clipCardCount);

    const clipsInState = await page.evaluate(() => appState.clips.length);
    check(clipsInState === 2, "appState.clips が2件になっている: " + clipsInState);

    check(pageErrors.length === 0, "ページ例外が発生していない: " + JSON.stringify(pageErrors));
    await context.close();
  }

  console.log("");
  console.log("=== シナリオ2: クリップを切り替えて、それぞれ独立にフリーズを追加できる ===");
  {
    const context = await browser.newContext({ ...iphone });
    const page = await context.newPage();
    const pageErrors = [];
    page.on("pageerror", (e) => pageErrors.push(e.message));

    await page.goto(BASE_URL, { waitUntil: "load" });
    await page.click("#guideCloseBtn").catch(() => {});
    await page.setInputFiles("#videoFileInput", videoPath);
    await page.waitForFunction(() => document.getElementById("video").duration > 0, null, { timeout: 10000 });
    await page.setInputFiles("#clipFileInput", videoPath);
    await page.waitForFunction(() => document.getElementById("clipListSection").hidden === false, null, { timeout: 10000 });

    // 「動画を追加」直後は2本目（クリップ2）が選択状態になっている想定。
    // そのままクリップ2にフリーズを1つ追加する。
    await page.waitForFunction(() => document.getElementById("video").duration > 0, null, { timeout: 10000 });
    await addSimpleFreeze(page, "クリップ2のフリーズ");

    const clip2FreezeCount = await page.evaluate(() => appState.freezes.length);
    check(clip2FreezeCount === 1, "クリップ2（選択中）にフリーズが1件追加される: " + clip2FreezeCount);

    // クリップ1の「編集」を押して切り替える（1枚目のカード＝クリップ1）
    await page.evaluate(() => {
      const firstCard = document.querySelectorAll(".clip-card")[0];
      const editBtn = Array.from(firstCard.querySelectorAll("button")).filter((b) => b.textContent === "編集")[0];
      editBtn.click();
    });
    await page.waitForFunction(() => document.getElementById("video").duration > 0, null, { timeout: 10000 });
    const clip1FreezeCountBeforeAdd = await page.evaluate(() => appState.freezes.length);
    check(clip1FreezeCountBeforeAdd === 0, "クリップ1へ切り替えると、クリップ2のフリーズは見えない（appState.freezesが0件）: " + clip1FreezeCountBeforeAdd);

    await addSimpleFreeze(page, "クリップ1のフリーズ");
    const clip1FreezeCountAfterAdd = await page.evaluate(() => appState.freezes.length);
    check(clip1FreezeCountAfterAdd === 1, "クリップ1にフリーズを追加できる: " + clip1FreezeCountAfterAdd);

    const bothClipsFreezeCounts = await page.evaluate(() => appState.clips.map((c) => c.freezes.length));
    check(JSON.stringify(bothClipsFreezeCounts) === JSON.stringify([1, 1]),
      "最終的に各クリップが独立して1件ずつフリーズを保持している（混ざっていない）: " + JSON.stringify(bothClipsFreezeCounts));

    check(pageErrors.length === 0, "ページ例外が発生していない: " + JSON.stringify(pageErrors));
    await context.close();
  }

  console.log("");
  console.log("=== シナリオ3: トランジション選択とJSONプレビューへの反映 ===");
  {
    const context = await browser.newContext({ ...iphone });
    const page = await context.newPage();
    const pageErrors = [];
    page.on("pageerror", (e) => pageErrors.push(e.message));

    await page.goto(BASE_URL, { waitUntil: "load" });
    await page.click("#guideCloseBtn").catch(() => {});
    await page.setInputFiles("#videoFileInput", videoPath);
    await page.waitForFunction(() => document.getElementById("video").duration > 0, null, { timeout: 10000 });
    await page.setInputFiles("#clipFileInput", videoPath);
    await page.waitForFunction(() => document.getElementById("clipListSection").hidden === false, null, { timeout: 10000 });

    await page.selectOption(".clip-transition-row select", "crossfade");
    await page.fill('.clip-transition-row input[type="number"]', "0.8");
    await page.dispatchEvent('.clip-transition-row input[type="number"]', "change");

    const transitionInState = await page.evaluate(() => appState.clips[0].transitionOut);
    check(transitionInState.type === "crossfade" && Math.abs(transitionInState.sec - 0.8) < 1e-6,
      "選んだトランジション種類・秒数がクリップの状態に反映される: " + JSON.stringify(transitionInState));

    const jsonPreview = await page.evaluate(() => JSON.parse(document.getElementById("jsonPreviewArea").value));
    check(Array.isArray(jsonPreview.clips) && jsonPreview.clips.length === 2,
      "JSONプレビューに2件のclipsが出力される");
    check(jsonPreview.clips[0].transition_out.type === "crossfade" && jsonPreview.clips[0].transition_out.sec === 0.8,
      "1本目クリップのtransition_outがJSONプレビューに反映されている: " + JSON.stringify(jsonPreview.clips[0].transition_out));
    check(jsonPreview.video === undefined && jsonPreview.freezes === undefined,
      "clips使用時はトップレベルのvideo/freezesキーが出力されない");

    check(pageErrors.length === 0, "ページ例外が発生していない: " + JSON.stringify(pageErrors));
    await context.close();
  }

  console.log("");
  console.log("=== シナリオ4: 「動画を作る」で clips[] を含む project.json が送られ、クリップごとに別々の動画blobがコミットされる ===");
  {
    const context = await browser.newContext({ ...iphone });
    const page = await context.newPage();
    const pageErrors = [];
    page.on("pageerror", (e) => pageErrors.push(e.message));
    page.on("console", (msg) => console.log("  [console] " + msg.text()));

    const mock = makeMock();
    await routeApiGithub(page, mock);

    await page.goto(BASE_URL, { waitUntil: "load" });
    await page.click("#guideCloseBtn").catch(() => {});
    await page.setInputFiles("#videoFileInput", videoPath);
    await page.waitForFunction(() => document.getElementById("video").duration > 0, null, { timeout: 10000 });
    await page.setInputFiles("#clipFileInput", videoPath);
    await page.waitForFunction(() => document.getElementById("clipListSection").hidden === false, null, { timeout: 10000 });
    await page.waitForFunction(() => document.getElementById("video").duration > 0, null, { timeout: 10000 });

    await addSimpleFreeze(page, "クリップ2のフリーズ");
    await page.evaluate(() => {
      const firstCard = document.querySelectorAll(".clip-card")[0];
      const editBtn = Array.from(firstCard.querySelectorAll("button")).filter((b) => b.textContent === "編集")[0];
      editBtn.click();
    });
    await page.waitForFunction(() => document.getElementById("video").duration > 0, null, { timeout: 10000 });
    await addSimpleFreeze(page, "クリップ1のフリーズ");

    await fillGhSettings(page, { user: OWNER, repo: REPO, token: TOKEN });
    await clickMakeVideoBtnAndProceed(page);

    try {
      await page.waitForFunction(
        () => !document.getElementById("jobResultLink").hidden,
        null, { timeout: 20000 }
      );
    } catch (err) {
      const statusText = await page.evaluate(() => document.getElementById("jobStatusLine").textContent);
      const statusClass = await page.evaluate(() => document.getElementById("jobStatusLine").className);
      console.log("  [debug] jobStatusLine: " + statusClass + " / " + statusText);
      console.log("  [debug] mock状態: " + JSON.stringify({
        createReleaseCalls: mock.createReleaseCalls, getRefCalls: mock.getRefCalls, getCommitCalls: mock.getCommitCalls,
        blobCalls: mock.blobCalls, speedTestBlobCalls: mock.speedTestBlobCalls,
        createTreeCalls: mock.createTreeCalls, createCommitCalls: mock.createCommitCalls, createRefCalls: mock.createRefCalls,
        dispatchCalls: mock.dispatchCalls, listRunsCalls: mock.listRunsCalls, getRunCalls: mock.getRunCalls,
      }));
      throw err;
    }

    const project = mock.jsonPayloadText ? JSON.parse(mock.jsonPayloadText) : null;
    check(!!project, "project.json のコミット内容を捕捉できた");
    check(project && Array.isArray(project.clips) && project.clips.length === 2,
      "project.json に2件のclipsが出力されている: " + JSON.stringify(project && project.clips && project.clips.map((c) => c.video)));
    check(project && project.clips[0].video === "clip0.webm" && project.clips[1].video === "clip1.webm",
      "各clipのvideoが clipVideoAssetName どおり(clip0.webm/clip1.webm)になっている: " +
      JSON.stringify(project && project.clips.map((c) => c.video)));
    check(project && project.clips[0].freezes.length === 1 && project.clips[1].freezes.length === 1,
      "各clipのfreezesが1件ずつ、正しく分かれて出力されている");

    const treePaths = (mock.treePayload && mock.treePayload.tree || []).map((e) => e.path);
    check(treePaths.indexOf("clip0.webm") >= 0 && treePaths.indexOf("clip1.webm") >= 0,
      "treeにclip0.webm/clip1.webmの両方が別々のパスとして含まれる: " + JSON.stringify(treePaths));
    check(treePaths.indexOf("video.webm") < 0,
      "単一動画時の固定名 video.webm はtreeに含まれない（複数クリップ時は使わない）");

    check(mock.dispatchCalls === 1, "workflow_dispatchが1回呼ばれる");
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
