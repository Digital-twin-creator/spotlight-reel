#!/usr/bin/env node
// -*- coding: utf-8 -*-
//
// index.html の「動画を作る」ボタン（GitHub Actions自動レンダリングのジョブ連携）を、
// api.github.com への実際の通信をモックした状態で検証する回帰テスト。
// 実際のGitHubには一切アクセスしない（Release作成・Git Data APIでのブランチコミット
// （blob→tree→commit→ref）・workflow_dispatch・runのポーリング・成功時のReleaseページ
// リンク表示・失敗時のエラー表示・100MB超過時の中断・GitHub連携設定の保存表示・
// runが自動確認できなかった場合の案内表示、を全てモックで再現する）。
//
// uploads.github.com は使わない（実機検証でCORS拒否されることが判明したため、
// project.json/動画は Git Data API 経由でリポジトリのブランチにコミットする方式に変更した）。
//
// 注意: シナリオ6は「workflow_dispatch後、runが90秒間見つからない」ケースを
// 実際に90秒待って検証するため、このファイル全体の実行に2分半程度かかる。
//
// 実行方法:
//   npm install --no-save playwright-core
//   node tests/make_video_job.playwright.test.mjs
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

const OWNER = "Digital-twin-creator";
const REPO = "spotlight-jobs";
const TOKEN = "github_pat_test_dummy_token";

const BASE_COMMIT_SHA = "base-commit-sha";
const BASE_TREE_SHA = "base-tree-sha";
const JSON_BLOB_SHA = "json-blob-sha";
const VIDEO_BLOB_SHA = "video-blob-sha";
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

    // POST /repos/{owner}/{repo}/releases  … Release作成
    if (method === "POST" && pathname === `/repos/${owner}/${repo}/releases`) {
      mock.createReleaseCalls++;
      return json(201, {
        id: 1, tag_name: mock.tag,
        html_url: `https://github.com/${owner}/${repo}/releases/tag/${mock.tag}`,
      });
    }

    // GET /repos/{owner}/{repo}/git/ref/heads/main  … コミット先ブランチの基点
    if (method === "GET" && pathname === `/repos/${owner}/${repo}/git/ref/heads/main`) {
      mock.getRefCalls++;
      return json(200, { ref: "refs/heads/main", object: { sha: BASE_COMMIT_SHA, type: "commit" } });
    }

    // GET /repos/{owner}/{repo}/git/commits/{sha}  … 基点コミットのtree shaを取る
    if (method === "GET" && pathname === `/repos/${owner}/${repo}/git/commits/${BASE_COMMIT_SHA}`) {
      mock.getCommitCalls++;
      return json(200, { sha: BASE_COMMIT_SHA, tree: { sha: BASE_TREE_SHA } });
    }

    // POST /repos/{owner}/{repo}/git/blobs  … project.json / 動画 のblob作成（1回目=json、2回目=動画）
    if (method === "POST" && pathname === `/repos/${owner}/${repo}/git/blobs`) {
      mock.blobCalls++;
      let body = {};
      try { body = JSON.parse(request.postData() || "{}"); } catch (e) { /* 無視 */ }
      if (body.encoding !== "base64" || typeof body.content !== "string") {
        mock.blobPayloadInvalid = true;
      }
      const sha = mock.blobCalls === 1 ? JSON_BLOB_SHA : VIDEO_BLOB_SHA;
      if (mock.blobCalls === 1) mock.jsonBlobContentLength = body.content ? body.content.length : 0;
      else mock.videoBlobContentLength = body.content ? body.content.length : 0;
      return json(201, { sha });
    }

    // POST /repos/{owner}/{repo}/git/trees  … tree作成
    if (method === "POST" && pathname === `/repos/${owner}/${repo}/git/trees`) {
      mock.createTreeCalls++;
      let body = {};
      try { body = JSON.parse(request.postData() || "{}"); } catch (e) { /* 無視 */ }
      mock.treePayload = body;
      return json(201, { sha: NEW_TREE_SHA });
    }

    // POST /repos/{owner}/{repo}/git/commits  … commit作成
    if (method === "POST" && pathname === `/repos/${owner}/${repo}/git/commits`) {
      mock.createCommitCalls++;
      let body = {};
      try { body = JSON.parse(request.postData() || "{}"); } catch (e) { /* 無視 */ }
      mock.commitPayload = body;
      return json(201, { sha: NEW_COMMIT_SHA });
    }

    // POST /repos/{owner}/{repo}/git/refs  … job-<tag> ブランチ作成
    if (method === "POST" && pathname === `/repos/${owner}/${repo}/git/refs`) {
      mock.createRefCalls++;
      let body = {};
      try { body = JSON.parse(request.postData() || "{}"); } catch (e) { /* 無視 */ }
      mock.refPayload = body;
      return json(201, { ref: body.ref, object: { sha: body.sha } });
    }

    // POST /repos/{owner}/{repo}/actions/workflows/render.yml/dispatches  … workflow_dispatch
    if (method === "POST" && /\/actions\/workflows\/render\.yml\/dispatches$/.test(pathname)) {
      mock.dispatchCalls++;
      await route.fulfill({ status: 204, headers: CORS_HEADERS, body: "" });
      return;
    }

    // GET /repos/{owner}/{repo}/actions/workflows/render.yml/runs  … 起動したrunを探す
    if (method === "GET" && /\/actions\/workflows\/render\.yml\/runs$/.test(pathname)) {
      mock.listRunsCalls++;
      const runs = mock.listRunsCalls >= mock.matchRunAfterCalls
        ? [{
            id: mock.runId,
            name: "render " + mock.tag,
            status: mock.runStatusSequence[0].status,
            conclusion: mock.runStatusSequence[0].conclusion,
            created_at: new Date().toISOString(),
            html_url: `https://github.com/${owner}/${repo}/actions/runs/${mock.runId}`,
          }]
        : [];
      return json(200, { workflow_runs: runs });
    }

    // GET /repos/{owner}/{repo}/actions/runs/{id}  … runの状態ポーリング
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

    // GET /repos/{owner}/{repo}/releases/tags/{tag}  … 完成確認（本番タグ）／
    // サーバー確認済みアルファの再利用時は、フリーズが覚えている確認タグ(mock.confirmTag)への
    // 問い合わせも同じエンドポイントで発生する（reuseConfirmedAlphaForFreezes参照）。
    const releaseTagsMatch = /^\/repos\/[^/]+\/[^/]+\/releases\/tags\/(.+)$/.exec(pathname);
    if (method === "GET" && releaseTagsMatch && decodeURIComponent(releaseTagsMatch[1]) === mock.tag) {
      mock.getReleaseByTagCalls++;
      const assets = mock.includeOutputAsset
        ? [{
            name: "output.mp4", id: 999,
            browser_download_url: `https://github.com/${owner}/${repo}/releases/download/${mock.tag}/output.mp4`,
          }]
        : [];
      return json(200, {
        tag_name: mock.tag,
        html_url: `https://github.com/${owner}/${repo}/releases/tag/${mock.tag}`,
        assets,
      });
    }
    if (method === "GET" && releaseTagsMatch && mock.confirmTag && decodeURIComponent(releaseTagsMatch[1]) === mock.confirmTag) {
      mock.getConfirmReleaseCalls = (mock.getConfirmReleaseCalls || 0) + 1;
      const assets = mock.confirmNpzAssetName ? [{ name: mock.confirmNpzAssetName, id: 9001 }] : [];
      return json(200, {
        tag_name: mock.confirmTag,
        html_url: `https://github.com/${owner}/${repo}/releases/tag/${mock.confirmTag}`,
        assets,
      });
    }

    // GET /repos/{owner}/{repo}/contents/{npz名}?ref={confirmTag}  …
    // job-confirm-<tag>ブランチにコミットされたサーバー確認済みアルファ(.npz)の取得
    // （Contents API。ghDownloadBranchFile参照。非公開Releaseアセットの実体は
    // CORS非対応のホストへ302リダイレクトされブラウザから読み取れないため、
    // ブランチへのコミット＋Contents APIに切り替えた）
    if (method === "GET" && mock.confirmNpzAssetName &&
        pathname === `/repos/${owner}/${repo}/contents/${mock.confirmNpzAssetName}`) {
      mock.downloadConfirmedAlphaCalls = (mock.downloadConfirmedAlphaCalls || 0) + 1;
      return json(200, {
        name: mock.confirmNpzAssetName, path: mock.confirmNpzAssetName, sha: "confirmed-alpha-blob-sha",
        content: Buffer.from([1, 2, 3, 4]).toString("base64"), // 中身は無関係（treeエントリに載るかどうかだけを見る）
        encoding: "base64",
      });
    }

    console.log("  [警告] モックされていないGitHub APIリクエスト: " + method + " " + pathname);
    await route.fulfill({ status: 404, headers: CORS_HEADERS, body: "{}" });
  };
}

function makeMock(overrides) {
  return Object.assign({
    tag: null, runId: 555001,
    createReleaseCalls: 0, getRefCalls: 0, getCommitCalls: 0,
    blobCalls: 0, blobPayloadInvalid: false, jsonBlobContentLength: 0, videoBlobContentLength: 0,
    createTreeCalls: 0, treePayload: null,
    createCommitCalls: 0, commitPayload: null,
    createRefCalls: 0, refPayload: null,
    dispatchCalls: 0, listRunsCalls: 0, getRunCalls: 0, getReleaseByTagCalls: 0,
    matchRunAfterCalls: 1,
    runStatusSequence: [{ status: "completed", conclusion: "success" }],
    includeOutputAsset: true,
    // サーバー確認済みアルファの再利用（reuseConfirmedAlphaForFreezes）検証用。
    // confirmTagを指定すると、そのタグへの/releases/tags問い合わせにconfirmNpzAssetNameの
    // アセットを返すようになる（未指定なら通常どおり本番タグのみ応答する）。
    confirmTag: null, confirmNpzAssetName: null,
    getConfirmReleaseCalls: 0, downloadConfirmedAlphaCalls: 0,
  }, overrides);
}

async function routeApiGithub(page, mock) {
  await page.route("https://api.github.com/**", (route) => {
    const u = new URL(route.request().url());
    const isReleaseCreate = /^\/repos\/[^/]+\/[^/]+\/releases$/.test(u.pathname);
    if (isReleaseCreate && route.request().method() === "POST" && !mock.tag) {
      try {
        const body = JSON.parse(route.request().postData() || "{}");
        mock.tag = body.tag_name;
      } catch (e) { /* 無視 */ }
    }
    return makeGithubMockRouter(mock)(route);
  });
}

async function fillGhSettings(page, { user, repo, token }) {
  await page.evaluate(() => { document.getElementById("ghSettingsDetails").open = true; });
  await page.fill("#ghUserInput", user);
  await page.fill("#ghRepoInput", repo);
  await page.fill("#ghTokenInput", token);
}

async function loadVideoAndOpenSettings(page, videoPath) {
  await page.goto(BASE_URL, { waitUntil: "load" });
  await page.click("#guideCloseBtn").catch(() => {});
  await page.setInputFiles("#videoFileInput", videoPath);
  await page.waitForFunction(() => document.getElementById("video").duration > 0, null, { timeout: 10000 });
}

async function main() {
  const videoPath = prepareTestVideo();
  const iphone = devices["iPhone 13"];
  const browser = await chromium.launch({ executablePath: CHROMIUM_PATH, headless: true });

  console.log("=== シナリオ1: 正常系（Git Data APIでブランチにコミット → dispatch → ポーリング → 完成リンク表示） ===");
  {
    const context = await browser.newContext({ ...iphone });
    const page = await context.newPage();
    const pageErrors = [];
    page.on("pageerror", (e) => pageErrors.push(e.message));

    const mock = makeMock({
      runId: 555001,
      matchRunAfterCalls: 2, // 1回目は空、2回目でrunが見つかる（ポーリングの実動作を確認）
      runStatusSequence: [{ status: "in_progress", conclusion: null }, { status: "completed", conclusion: "success" }],
    });
    await routeApiGithub(page, mock);

    await loadVideoAndOpenSettings(page, videoPath);
    await fillGhSettings(page, { user: OWNER, repo: REPO, token: TOKEN });
    await page.click("#makeVideoBtn");

    await page.waitForFunction(
      () => document.getElementById("makeVideoBtn").disabled === true,
      null, { timeout: 3000 }
    ).catch(() => {});
    check(true, "「動画を作る」タップ後、ボタンが処理中状態になる");

    await page.waitForFunction(
      () => !document.getElementById("jobResultLink").hidden,
      null, { timeout: 30000 }
    );

    const resultHref = await page.getAttribute("#jobResultLink", "href");
    const resultText = await page.evaluate(() => document.getElementById("jobResultLink").textContent);
    const statusText = await page.evaluate(() => document.getElementById("jobStatusLine").textContent);
    const statusClass = await page.evaluate(() => document.getElementById("jobStatusLine").className);
    const btnDisabled = await page.evaluate(() => document.getElementById("makeVideoBtn").disabled);
    const btnText = await page.evaluate(() => document.getElementById("makeVideoBtn").textContent);

    check(resultHref && resultHref.indexOf(`/${OWNER}/${REPO}/releases/download/`) >= 0 && resultHref.indexOf("output.mp4") >= 0,
      "完成後、output.mp4への直接ダウンロードリンク（browser_download_url）が表示される（Releaseページ経由ではない）: " + resultHref);
    check(resultText.indexOf("完成動画を保存") >= 0, "リンクのボタン文言が「完成動画を保存」になっている: " + resultText);
    check(statusClass.indexOf("ok") >= 0, "完了メッセージが成功(ok)スタイルで表示される");
    check(statusText.indexOf("完成") >= 0, "完了メッセージに「完成」の文言が含まれる: " + statusText);
    check(btnDisabled === false, "完了後、ボタンが再度有効になる");
    check(btnText.indexOf("動画を作る") >= 0, "完了後、ボタンの表示が元に戻る");

    check(mock.createReleaseCalls === 1, "Release作成APIが1回呼ばれる");
    check(mock.getRefCalls >= 1, "コミット先ブランチの基点(refs/heads/main)を取得する");
    check(mock.getCommitCalls >= 1, "基点コミットのtree shaを取得する");
    check(mock.blobCalls === 2, "project.jsonと動画の2つのblobが作成される: " + mock.blobCalls + "回");
    check(!mock.blobPayloadInvalid, "blob作成リクエストがcontent/encoding=base64の形になっている");
    check(mock.jsonBlobContentLength > 0 && mock.videoBlobContentLength > mock.jsonBlobContentLength,
      "動画blobの方がproject.jsonのblobより十分大きい（base64化されている）: json=" +
      mock.jsonBlobContentLength + "chars, video=" + mock.videoBlobContentLength + "chars");
    check(mock.createTreeCalls === 1 && mock.treePayload && mock.treePayload.base_tree === BASE_TREE_SHA,
      "tree作成が基点tree(base_tree)を正しく指定している");
    check(mock.treePayload && mock.treePayload.tree.some((e) => e.path === "project.json" && e.sha === JSON_BLOB_SHA)
      && mock.treePayload.tree.some((e) => e.path === "video.webm" && e.sha === VIDEO_BLOB_SHA),
      "treeにproject.jsonとvideo.webmの両方が正しいshaで含まれる");
    check(mock.createCommitCalls === 1 && mock.commitPayload && mock.commitPayload.tree === NEW_TREE_SHA
      && JSON.stringify(mock.commitPayload.parents) === JSON.stringify([BASE_COMMIT_SHA]),
      "commit作成が新しいtreeと親コミット(基点)を正しく指定している");
    check(mock.createRefCalls === 1 && mock.refPayload && mock.refPayload.sha === NEW_COMMIT_SHA
      && mock.refPayload.ref === "refs/heads/" + mock.tag,
      "ref作成でjob-<tag>という名前のブランチが新しいコミットを指して作られる: " + (mock.refPayload && mock.refPayload.ref));
    check(mock.dispatchCalls === 1, "workflow_dispatchが1回呼ばれる");
    check(mock.listRunsCalls >= 2, "run一覧のポーリングが複数回行われる（空→一致、の動作を確認）: " + mock.listRunsCalls + "回");
    check(mock.getRunCalls >= 2, "runステータスのポーリングが複数回行われる（pending→success、の動作を確認）: " + mock.getRunCalls + "回");
    check(pageErrors.length === 0, "ページ例外が発生していない: " + JSON.stringify(pageErrors));

    await context.close();
  }

  console.log("");
  console.log("=== シナリオ2: レンダリング失敗（runのconclusionがfailure） ===");
  {
    const context = await browser.newContext({ ...iphone });
    const page = await context.newPage();
    const pageErrors = [];
    page.on("pageerror", (e) => pageErrors.push(e.message));

    const mock = makeMock({
      runId: 555002,
      runStatusSequence: [{ status: "completed", conclusion: "failure" }],
      includeOutputAsset: false,
    });
    await routeApiGithub(page, mock);

    await loadVideoAndOpenSettings(page, videoPath);
    await fillGhSettings(page, { user: OWNER, repo: REPO, token: TOKEN });
    await page.click("#makeVideoBtn");

    await page.waitForFunction(
      () => document.getElementById("jobStatusLine").className.indexOf("err") >= 0,
      null, { timeout: 30000 }
    );

    const statusText = await page.evaluate(() => document.getElementById("jobStatusLine").textContent);
    const resultLinkHidden = await page.evaluate(() => document.getElementById("jobResultLink").hidden);
    const btnDisabled = await page.evaluate(() => document.getElementById("makeVideoBtn").disabled);

    check(statusText.indexOf("失敗") >= 0, "失敗時のエラーメッセージが表示される: " + statusText);
    check(resultLinkHidden === true, "失敗時はReleaseページへのリンクが表示されない");
    check(btnDisabled === false, "失敗後、ボタンが再度押せる状態に戻る");
    check(pageErrors.length === 0, "ページ例外が発生していない: " + JSON.stringify(pageErrors));

    await context.close();
  }

  console.log("");
  console.log("=== シナリオ3: バリデーション（トークン未入力なら通信せずエラー表示） ===");
  {
    const context = await browser.newContext({ ...iphone });
    const page = await context.newPage();
    const pageErrors = [];
    page.on("pageerror", (e) => pageErrors.push(e.message));

    let calledUnexpectedly = false;
    await page.route("https://api.github.com/**", (route) => { calledUnexpectedly = true; route.abort(); });

    await loadVideoAndOpenSettings(page, videoPath);

    await page.evaluate(() => { document.getElementById("ghSettingsDetails").open = true; });
    await page.fill("#ghUserInput", "");
    await page.fill("#ghTokenInput", "");
    await page.click("#makeVideoBtn");
    await page.waitForTimeout(300);

    const statusText = await page.evaluate(() => document.getElementById("jobStatusLine").textContent);
    const statusClass = await page.evaluate(() => document.getElementById("jobStatusLine").className);
    const settingsOpen = await page.evaluate(() => document.getElementById("ghSettingsDetails").open);

    check(statusClass.indexOf("err") >= 0, "未設定でタップするとエラー表示になる");
    check(statusText.indexOf("設定") >= 0, "エラーメッセージが設定不足を示す: " + statusText);
    check(settingsOpen === true, "GitHub連携の設定パネルが自動的に開く");
    check(calledUnexpectedly === false, "設定未入力の場合、GitHub APIへは一切通信しない");
    check(pageErrors.length === 0, "ページ例外が発生していない: " + JSON.stringify(pageErrors));

    await context.close();
  }

  console.log("");
  console.log("=== シナリオ4: 動画が100MBを超える場合は通信せず中断する ===");
  {
    const context = await browser.newContext({ ...iphone });
    const page = await context.newPage();
    const pageErrors = [];
    page.on("pageerror", (e) => pageErrors.push(e.message));

    let calledUnexpectedly = false;
    await page.route("https://api.github.com/**", (route) => { calledUnexpectedly = true; route.abort(); });

    await loadVideoAndOpenSettings(page, videoPath);
    await fillGhSettings(page, { user: OWNER, repo: REPO, token: TOKEN });

    // 実ファイルを100MB超に差し替える代わりに、読み込み済みFileの.sizeだけを偽装する
    // （exceedsBlobLimitはFileを読む前にsizeプロパティだけを見て中断するため、これで十分再現できる）
    await page.evaluate(() => {
      Object.defineProperty(appState.videoFile, "size", { value: 200 * 1024 * 1024, configurable: true });
    });

    await page.click("#makeVideoBtn");
    await page.waitForTimeout(300);

    const statusText = await page.evaluate(() => document.getElementById("jobStatusLine").textContent);
    const statusClass = await page.evaluate(() => document.getElementById("jobStatusLine").className);
    const btnDisabled = await page.evaluate(() => document.getElementById("makeVideoBtn").disabled);

    check(statusClass.indexOf("err") >= 0, "100MB超の動画でタップするとエラー表示になる");
    check(statusText.indexOf("大きすぎます") >= 0 && statusText.indexOf("100MB") >= 0,
      "エラーメッセージが上限(100MB)を示す: " + statusText);
    check(calledUnexpectedly === false, "100MB超の場合、GitHub APIへは一切通信しない（変換すら始めない）");
    check(btnDisabled === false, "中断後、ボタンが押せる状態のまま（処理中で固まらない）");
    check(pageErrors.length === 0, "ページ例外が発生していない: " + JSON.stringify(pageErrors));

    await context.close();
  }

  console.log("");
  console.log("=== シナリオ5: GitHub連携設定の「保存済み」表示 ===");
  {
    const context = await browser.newContext({ ...iphone });
    const page = await context.newPage();
    const pageErrors = [];
    page.on("pageerror", (e) => pageErrors.push(e.message));

    await page.goto(BASE_URL, { waitUntil: "load" });
    await page.click("#guideCloseBtn").catch(() => {});
    await page.evaluate(() => { document.getElementById("ghSettingsDetails").open = true; });

    const initialStatus = await page.evaluate(() => document.getElementById("ghSettingsStatus").textContent);
    check(initialStatus.indexOf("未設定") >= 0, "未保存の初期状態では「未設定」と表示される: " + initialStatus);

    await page.fill("#ghUserInput", OWNER);
    await page.fill("#ghRepoInput", REPO);
    await page.fill("#ghTokenInput", TOKEN);
    await page.click("#ghSettingsSaveBtn");

    const savedStatus = await page.evaluate(() => document.getElementById("ghSettingsStatus").textContent);
    check(savedStatus.indexOf("保存済み") >= 0 && savedStatus.indexOf(TOKEN.slice(-4)) >= 0,
      "保存後は「保存済み（トークン末尾 …xxxx）」の形式で表示される（トークン本体は表示しない）: " + savedStatus);
    check(savedStatus.indexOf(TOKEN) < 0, "トークン全体は画面のテキストに現れない: " + savedStatus);

    // リロード後もlocalStorageから復元され、保存済み状態・入力値ともに保持される
    await page.reload({ waitUntil: "load" });
    await page.click("#guideCloseBtn").catch(() => {});
    await page.evaluate(() => { document.getElementById("ghSettingsDetails").open = true; });

    const reloadedUser = await page.inputValue("#ghUserInput");
    const reloadedRepo = await page.inputValue("#ghRepoInput");
    const reloadedStatus = await page.evaluate(() => document.getElementById("ghSettingsStatus").textContent);

    check(reloadedUser === OWNER, "リロード後もユーザー名が保存値のまま表示される: " + reloadedUser);
    check(reloadedRepo === REPO, "リロード後もリポジトリ名が保存値のまま表示される: " + reloadedRepo);
    check(reloadedStatus.indexOf("保存済み") >= 0 && reloadedStatus.indexOf(TOKEN.slice(-4)) >= 0,
      "リロード後も「保存済み」表示が復元される（再入力なしで分かる）: " + reloadedStatus);
    check(pageErrors.length === 0, "ページ例外が発生していない: " + JSON.stringify(pageErrors));

    await context.close();
  }

  console.log("");
  console.log("=== シナリオ6: 90秒待ってもrunが見つからない場合、エラーにせずGitHub側の確認リンクを出す ===");
  console.log("（このシナリオは実際に90秒以上待つため、他のシナリオより時間がかかります）");
  {
    const context = await browser.newContext({ ...iphone });
    const page = await context.newPage();
    const pageErrors = [];
    page.on("pageerror", (e) => pageErrors.push(e.message));

    const mock = makeMock({
      runId: 555003,
      matchRunAfterCalls: 999, // 19回のポーリング（最低90秒）の間、一度も一致させない
    });
    await routeApiGithub(page, mock);

    await loadVideoAndOpenSettings(page, videoPath);
    await fillGhSettings(page, { user: OWNER, repo: REPO, token: TOKEN });
    await page.click("#makeVideoBtn");

    // 90秒のポーリングが終わり、情報表示（エラーではない）に切り替わるまで待つ
    await page.waitForFunction(
      () => document.getElementById("jobStatusLine").textContent.indexOf("自動確認できませんでした") >= 0,
      null, { timeout: 2 * 60 * 1000 }
    );

    const statusText = await page.evaluate(() => document.getElementById("jobStatusLine").textContent);
    const statusClass = await page.evaluate(() => document.getElementById("jobStatusLine").className);
    const actionsLinkHidden = await page.evaluate(() => document.getElementById("jobActionsLink").hidden);
    const actionsLinkHref = await page.getAttribute("#jobActionsLink", "href");
    const resultLinkHidden = await page.evaluate(() => document.getElementById("jobResultLink").hidden);
    const resultLinkHref = await page.getAttribute("#jobResultLink", "href");
    const btnDisabled = await page.evaluate(() => document.getElementById("makeVideoBtn").disabled);

    check(statusClass.indexOf("err") < 0, "runが見つからなくてもエラー(err)扱いにはならない: " + statusClass);
    check(statusText.indexOf("自動確認できませんでした") >= 0, "自動確認できなかった旨のメッセージが表示される: " + statusText);
    check(!actionsLinkHidden && actionsLinkHref && actionsLinkHref.indexOf("/actions/workflows/render.yml") >= 0,
      "Actionsページへのリンクが表示される: " + actionsLinkHref);
    check(!resultLinkHidden && resultLinkHref && resultLinkHref.indexOf(`/${OWNER}/${REPO}/releases/tag/`) >= 0,
      "（事前に作成済みの）Releaseページへのリンクも表示される: " + resultLinkHref);
    check(btnDisabled === false, "自動確認できなかった後も、ボタンが再度押せる状態に戻る");
    check(mock.listRunsCalls >= 19, "run一覧を最低19回（90秒相当）ポーリングしてから諦める: " + mock.listRunsCalls + "回");
    check(pageErrors.length === 0, "ページ例外が発生していない: " + JSON.stringify(pageErrors));

    await context.close();
  }

  console.log("");
  console.log("=== シナリオ7: 内容チェック（名前未入力・ストローク無しのフリーズ）で確認モーダルが出る ===");
  {
    const context = await browser.newContext({ ...iphone });
    const page = await context.newPage();
    const pageErrors = [];
    page.on("pageerror", (e) => pageErrors.push(e.message));

    const mock = makeMock({ runId: 555007 });
    await routeApiGithub(page, mock);

    await loadVideoAndOpenSettings(page, videoPath);
    await fillGhSettings(page, { user: OWNER, repo: REPO, token: TOKEN });

    // 名前を入力せず、ブラシも塗らずにフリーズを追加してコミットする
    // （既定のmaskModeは"brush"なので、これだけで「名前未入力」「ストローク無し」の
    // 2つの警告条件を両方満たす）
    await page.click("#addFreezeBtn");
    await page.waitForFunction(() => !document.getElementById("editorSection").hidden, null, { timeout: 5000 });
    await page.click("#commitFreezeBtn");
    await page.waitForFunction(() => document.getElementById("editorSection").hidden, null, { timeout: 5000 });

    await page.click("#makeVideoBtn");

    await page.waitForFunction(
      () => document.getElementById("confirmWarningsModal").hidden === false,
      null, { timeout: 3000 }
    );
    const warningTexts = await page.$$eval("#confirmWarningsList li", (els) => els.map((el) => el.textContent));
    check(warningTexts.length === 2, "警告が2件（名前未入力・ストローク無し）リストされる: " + JSON.stringify(warningTexts));
    check(warningTexts.some((t) => t.indexOf("名前が入力されていません") >= 0),
      "「名前が入力されていません」の警告が含まれる");
    check(warningTexts.some((t) => t.indexOf("ブラシで何も塗られていません") >= 0),
      "「ブラシで何も塗られていません」の警告が含まれる");
    check(mock.createReleaseCalls === 0, "確認モーダル表示中はまだGitHub APIへ通信していない");

    // 「戻って直す」を押すと、モーダルが閉じるだけでアップロードは始まらない
    await page.click("#confirmWarningsBackBtn");
    const hiddenAfterBack = await page.evaluate(() => document.getElementById("confirmWarningsModal").hidden);
    check(hiddenAfterBack === true, "「戻って直す」を押すとモーダルが閉じる");
    check(mock.createReleaseCalls === 0, "「戻って直す」後もGitHub APIへは通信していない（中断できている）");
    const btnDisabledAfterBack = await page.evaluate(() => document.getElementById("makeVideoBtn").disabled);
    check(btnDisabledAfterBack === false, "「戻って直す」後、「動画を作る」ボタンは押せる状態のまま");

    // 再度タップし、今度は「このまま作る」を選ぶと、通常どおりアップロードが進む
    await page.click("#makeVideoBtn");
    await page.waitForFunction(
      () => document.getElementById("confirmWarningsModal").hidden === false,
      null, { timeout: 3000 }
    );
    await page.click("#confirmWarningsProceedBtn");

    const hiddenAfterProceed = await page.evaluate(() => document.getElementById("confirmWarningsModal").hidden);
    check(hiddenAfterProceed === true, "「このまま作る」を押すとモーダルが閉じる");

    await page.waitForFunction(
      () => !document.getElementById("jobResultLink").hidden,
      null, { timeout: 30000 }
    );
    check(mock.createReleaseCalls === 1, "「このまま作る」を選ぶと通常どおりRelease作成から始まる");
    check(mock.dispatchCalls === 1, "「このまま作る」を選ぶと通常どおりworkflow_dispatchまで進む");
    check(pageErrors.length === 0, "ページ例外が発生していない: " + JSON.stringify(pageErrors));

    await context.close();
  }

  console.log("");
  console.log("=== シナリオ8: GitHub連携設定は「保存」ボタンを押さなくても入力のたびに保存され、クラッシュ後も消えない ===");
  {
    const context = await browser.newContext({ ...iphone });
    const page = await context.newPage();
    const pageErrors = [];
    page.on("pageerror", (e) => pageErrors.push(e.message));

    await page.goto(BASE_URL, { waitUntil: "load" });
    await page.click("#guideCloseBtn").catch(() => {});
    await page.evaluate(() => { document.getElementById("ghSettingsDetails").open = true; });

    // 「設定を保存」ボタンは一切押さず、入力するだけにする
    // （page.fillはvalueを設定した上でinputイベントを発火させる。
    //   実機で「保存を押し忘れたままページがクラッシュした」状況に相当する）
    await page.fill("#ghUserInput", OWNER);
    await page.fill("#ghRepoInput", REPO);
    await page.fill("#ghTokenInput", TOKEN);

    const statusBeforeSaveClick = await page.evaluate(() => document.getElementById("ghSettingsStatus").textContent);
    check(statusBeforeSaveClick.indexOf("保存済み") >= 0 && statusBeforeSaveClick.indexOf(TOKEN.slice(-4)) >= 0,
      "「保存」ボタンを押す前でも、入力するだけでステータス表示が「保存済み」になる: " + statusBeforeSaveClick);

    // ここでリロード（＝ボタンを押す前にクラッシュ・再読み込みされた状況を再現）
    await page.reload({ waitUntil: "load" });
    await page.click("#guideCloseBtn").catch(() => {});
    await page.evaluate(() => { document.getElementById("ghSettingsDetails").open = true; });

    const userAfterCrash = await page.inputValue("#ghUserInput");
    const repoAfterCrash = await page.inputValue("#ghRepoInput");
    const tokenAfterCrash = await page.inputValue("#ghTokenInput");
    check(userAfterCrash === OWNER, "「保存」ボタンを押さず入力しただけでも、ユーザー名はクラッシュ後の再読み込みで消えない: " + userAfterCrash);
    check(repoAfterCrash === REPO, "同上：リポジトリ名も消えない: " + repoAfterCrash);
    check(tokenAfterCrash === TOKEN, "同上：トークンも消えない: " + tokenAfterCrash);

    console.log("");
    console.log("--- 保存キーのバージョンが変わっても(v1→v2相当)、GitHub連携設定だけは自動的に引き継がれる ---");
    // GH_SETTINGS_KEYを次のバージョンに見立てた別名に切り替え、現行キーが空の状態で
    // loadGhSettings()を呼び直す。旧キー(現行のGH_SETTINGS_KEY)がGH_SETTINGS_LEGACY_KEYSに
    // 含まれていれば、そこから読み込んで新しいキーへ自動移行されるはずである。
    const migrationResult = await page.evaluate(() => {
      var oldKey = GH_SETTINGS_KEY;
      var newKey = "spotlightReel.ghSettings.v2-test";
      GH_SETTINGS_LEGACY_KEYS.push(oldKey);
      GH_SETTINGS_KEY = newKey;
      try {
        loadGhSettings();
        var userAfterMigration = document.getElementById("ghUserInput").value;
        var tokenAfterMigration = document.getElementById("ghTokenInput").value;
        var migratedRaw = localStorage.getItem(newKey);
        return {
          userAfterMigration: userAfterMigration,
          tokenAfterMigration: tokenAfterMigration,
          migratedSaved: !!migratedRaw && JSON.parse(migratedRaw).token === tokenAfterMigration
        };
      } finally {
        GH_SETTINGS_KEY = oldKey;
        GH_SETTINGS_LEGACY_KEYS.length = 0;
        localStorage.removeItem(newKey);
      }
    });
    check(migrationResult.userAfterMigration === OWNER,
      "現行キーを切り替えても、旧キーからユーザー名が自動的に引き継がれる: " + migrationResult.userAfterMigration);
    check(migrationResult.tokenAfterMigration === TOKEN,
      "同上：トークンも自動的に引き継がれる");
    check(migrationResult.migratedSaved === true,
      "引き継いだ設定は新しいキーの下にも保存され、以後は移行処理なしで読み込める");

    check(pageErrors.length === 0, "ページ例外が発生していない: " + JSON.stringify(pageErrors));
    await context.close();
  }

  console.log("");
  console.log("=== シナリオ9: サーバー確認済みのアルファがあるフリーズは、本番ジョブブランチへcache/video_<time>.npzとして同梱される ===");
  {
    const context = await browser.newContext({ ...iphone });
    const page = await context.newPage();
    const pageErrors = [];
    page.on("pageerror", (e) => pageErrors.push(e.message));

    const mock = makeMock({
      runId: 555009,
      confirmTag: "job-confirm-20260901-120000-ab12",
      confirmNpzAssetName: "video_1.500.npz",
    });
    await routeApiGithub(page, mock);

    await loadVideoAndOpenSettings(page, videoPath);
    await fillGhSettings(page, { user: OWNER, repo: REPO, token: TOKEN });

    // フリーズを1つ作り（mask=auto）、あらかじめ「確認完了」したことにする
    // （実際のサーバー確認フローはmask_shadow_ui.playwright.test.mjs側で別途検証済みのため、
    // ここではconfirmedAlphaが記録された状態を直接再現し、本番アップロード側の再利用だけを見る）。
    await page.click("#addFreezeBtn");
    await page.waitForFunction(() => !document.getElementById("editorSection").hidden, null, { timeout: 5000 });
    await page.selectOption("#maskModeSelect", "auto");
    await page.fill(".title-line-text", "確認済みフリーズ");
    await page.evaluate((confirmTag) => {
      draft.time = 1.5;
      draft.confirmedAlpha = {
        videoFileName: appState.videoFileName,
        time: 1.5,
        model: resolveMaskModel(appState.maskModel),
        tag: confirmTag,
      };
    }, mock.confirmTag);
    await page.click("#commitFreezeBtn");
    await page.waitForFunction(() => document.getElementById("editorSection").hidden, null, { timeout: 5000 });

    // 比較対象として、確認していない（confirmedAlphaが無い）フリーズも1つ追加する。
    // こちらは再利用の対象にならず、treeにcache/video_*.npzが追加されないことを確認する。
    await page.click("#addFreezeBtn");
    await page.waitForFunction(() => !document.getElementById("editorSection").hidden, null, { timeout: 5000 });
    await page.selectOption("#maskModeSelect", "auto");
    await page.fill(".title-line-text", "未確認フリーズ");
    await page.evaluate(() => { draft.time = 2.5; });
    await page.click("#commitFreezeBtn");
    await page.waitForFunction(() => document.getElementById("editorSection").hidden, null, { timeout: 5000 });

    await page.click("#makeVideoBtn");
    await page.waitForFunction(
      () => !document.getElementById("jobResultLink").hidden,
      null, { timeout: 30000 }
    );

    check(mock.getConfirmReleaseCalls >= 1, "確認時のReleaseを再取得する（サーバー確認済みアルファの所在確認）: " + mock.getConfirmReleaseCalls);
    check(mock.downloadConfirmedAlphaCalls === 1, "確認済みアルファ(.npz)を1件だけ認証付きでダウンロードする: " + mock.downloadConfirmedAlphaCalls);
    check(mock.treePayload && mock.treePayload.tree.some((e) => e.path === "cache/video_1.500.npz"),
      "本番ジョブブランチのtreeに、確認済みフリーズのcache/video_1.500.npzが同梱される: " +
      JSON.stringify(mock.treePayload && mock.treePayload.tree.map((e) => e.path)));
    check(!mock.treePayload.tree.some((e) => e.path === "cache/video_2.500.npz"),
      "確認していないフリーズ（confirmedAlpha無し）については、cache/*.npzが追加されない");
    check(mock.treePayload.tree.some((e) => e.path === "project.json") && mock.treePayload.tree.some((e) => e.path.indexOf("video.") === 0),
      "project.json・動画本体も引き続き通常どおりtreeに含まれる");

    check(pageErrors.length === 0, "ページ例外が発生していない: " + JSON.stringify(pageErrors));
    await context.close();
  }

  console.log("");
  console.log("=== シナリオ10: サーバー確認済みでも、動画名/時刻/モデルがズレていれば再利用しない（モデル再実行にフォールバック） ===");
  {
    const context = await browser.newContext({ ...iphone });
    const page = await context.newPage();
    const pageErrors = [];
    page.on("pageerror", (e) => pageErrors.push(e.message));

    let confirmReleaseCalledUnexpectedly = false;
    const mock = makeMock({ runId: 555010 });
    await page.route("https://api.github.com/**", (route) => {
      const u = new URL(route.request().url());
      if (/^\/repos\/[^/]+\/[^/]+\/releases\/tags\/job-confirm-/.test(u.pathname)) {
        confirmReleaseCalledUnexpectedly = true;
      }
      const isReleaseCreate = /^\/repos\/[^/]+\/[^/]+\/releases$/.test(u.pathname);
      if (isReleaseCreate && route.request().method() === "POST" && !mock.tag) {
        try {
          const body = JSON.parse(route.request().postData() || "{}");
          mock.tag = body.tag_name;
        } catch (e) { /* 無視 */ }
      }
      return makeGithubMockRouter(mock)(route);
    });

    await loadVideoAndOpenSettings(page, videoPath);
    await fillGhSettings(page, { user: OWNER, repo: REPO, token: TOKEN });

    await page.click("#addFreezeBtn");
    await page.waitForFunction(() => !document.getElementById("editorSection").hidden, null, { timeout: 5000 });
    await page.selectOption("#maskModeSelect", "auto");
    await page.fill(".title-line-text", "モデル違いフリーズ");
    await page.evaluate(() => {
      draft.time = 1.5;
      // モデルが現在の設定(isnet-general-use)と異なる確認結果 → isConfirmedAlphaValidがfalseになるはず
      draft.confirmedAlpha = {
        videoFileName: appState.videoFileName, time: 1.5, model: "birefnet-portrait", tag: "job-confirm-mismatch",
      };
    });
    await page.click("#commitFreezeBtn");
    await page.waitForFunction(() => document.getElementById("editorSection").hidden, null, { timeout: 5000 });

    await page.click("#makeVideoBtn");
    await page.waitForFunction(
      () => !document.getElementById("jobResultLink").hidden,
      null, { timeout: 30000 }
    );

    check(confirmReleaseCalledUnexpectedly === false, "モデルが一致しない確認結果は再利用対象にせず、確認用Releaseへ問い合わせすらしない");
    check(mock.treePayload && !mock.treePayload.tree.some((e) => e.path.indexOf("cache/") === 0),
      "treeにcache/配下のエントリが含まれない（フォールバックして通常どおりrender.py側で自動切り抜きされる）");

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
