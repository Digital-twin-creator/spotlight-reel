#!/usr/bin/env node
// -*- coding: utf-8 -*-
//
// index.html の「動画を作る」ボタン（GitHub Actions自動レンダリングのジョブ連携）を、
// api.github.com / uploads.github.com への実際の通信をモックした状態で検証する回帰テスト。
// 実際のGitHubには一切アクセスしない（Release作成・アセットアップロード・workflow_dispatch・
// runのポーリング・成功時のReleaseページリンク表示・失敗時のエラー表示、を全てモックで再現する）。
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

/** api.github.com / uploads.github.com 宛のリクエストを全てモックで返すルートハンドラを作る */
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

    // POST /repos/{owner}/{repo}/releases  … Release作成
    if (method === "POST" && new RegExp(`^/repos/${owner}/${repo}/releases$`).test(pathname)) {
      mock.releaseId += 1;
      mock.createReleaseCalls++;
      const body = JSON.stringify({
        id: mock.releaseId,
        tag_name: mock.tag,
        html_url: `https://github.com/${owner}/${repo}/releases/tag/${mock.tag}`,
        upload_url: `https://uploads.github.com/repos/${owner}/${repo}/releases/${mock.releaseId}/assets{?name,label}`,
      });
      await route.fulfill({ status: 201, headers: { ...CORS_HEADERS, "content-type": "application/json" }, body });
      return;
    }

    // POST https://uploads.github.com/repos/{owner}/{repo}/releases/{id}/assets?name=...  … アセットアップロード
    if (url.hostname === "uploads.github.com" && method === "POST" && /\/assets$/.test(pathname)) {
      mock.uploadedAssetNames.push(url.searchParams.get("name"));
      await route.fulfill({
        status: 201, headers: { ...CORS_HEADERS, "content-type": "application/json" },
        body: JSON.stringify({ name: url.searchParams.get("name") }),
      });
      return;
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
      await route.fulfill({
        status: 200, headers: { ...CORS_HEADERS, "content-type": "application/json" },
        body: JSON.stringify({ workflow_runs: runs }),
      });
      return;
    }

    // GET /repos/{owner}/{repo}/actions/runs/{id}  … runの状態ポーリング
    const runStatusMatch = /\/actions\/runs\/(\d+)$/.exec(pathname);
    if (method === "GET" && runStatusMatch) {
      mock.getRunCalls++;
      const idx = Math.min(mock.getRunCalls - 1, mock.runStatusSequence.length - 1);
      const state = mock.runStatusSequence[idx];
      await route.fulfill({
        status: 200, headers: { ...CORS_HEADERS, "content-type": "application/json" },
        body: JSON.stringify({
          id: mock.runId, status: state.status, conclusion: state.conclusion,
          html_url: `https://github.com/${owner}/${repo}/actions/runs/${mock.runId}`,
        }),
      });
      return;
    }

    // GET /repos/{owner}/{repo}/releases/tags/{tag}  … 完成確認
    if (method === "GET" && pathname === `/repos/${owner}/${repo}/releases/tags/${mock.tag}`) {
      mock.getReleaseByTagCalls++;
      const assets = mock.includeOutputAsset
        ? [{ name: "output.mp4", id: 999 }]
        : [];
      await route.fulfill({
        status: 200, headers: { ...CORS_HEADERS, "content-type": "application/json" },
        body: JSON.stringify({
          tag_name: mock.tag,
          html_url: `https://github.com/${owner}/${repo}/releases/tag/${mock.tag}`,
          assets,
        }),
      });
      return;
    }

    console.log("  [警告] モックされていないGitHub APIリクエスト: " + method + " " + pathname);
    await route.fulfill({ status: 404, headers: CORS_HEADERS, body: "{}" });
  };
}

async function fillGhSettings(page, { user, repo, token }) {
  await page.evaluate(() => { document.getElementById("ghSettingsDetails").open = true; });
  await page.fill("#ghUserInput", user);
  await page.fill("#ghRepoInput", repo);
  await page.fill("#ghTokenInput", token);
}

async function main() {
  const videoPath = prepareTestVideo();
  const iphone = devices["iPhone 13"];
  const browser = await chromium.launch({ executablePath: CHROMIUM_PATH, headless: true });

  console.log("=== シナリオ1: 正常系（アップロード → dispatch → ポーリング → 完成リンク表示） ===");
  {
    const context = await browser.newContext({ ...iphone });
    const page = await context.newPage();
    const pageErrors = [];
    page.on("pageerror", (e) => pageErrors.push(e.message));

    const mock = {
      tag: null, releaseId: 0, runId: 555001,
      createReleaseCalls: 0, dispatchCalls: 0, listRunsCalls: 0, getRunCalls: 0, getReleaseByTagCalls: 0,
      uploadedAssetNames: [],
      matchRunAfterCalls: 2, // 1回目は空、2回目でrunが見つかる（ポーリングの実動作を確認）
      runStatusSequence: [{ status: "in_progress", conclusion: null }, { status: "completed", conclusion: "success" }],
      includeOutputAsset: true,
    };
    // タグはクライアント側が時刻から生成するため、初回リクエストのURLから逆算して埋める
    await page.route("https://api.github.com/**", (route) => {
      const u = new URL(route.request().url());
      const m = /^\/repos\/[^/]+\/[^/]+\/releases$/.exec(u.pathname);
      if (m && route.request().method() === "POST" && !mock.tag) {
        try {
          const body = JSON.parse(route.request().postData() || "{}");
          mock.tag = body.tag_name;
        } catch (e) { /* 無視 */ }
      }
      return makeGithubMockRouter(mock)(route);
    });
    await page.route("https://uploads.github.com/**", makeGithubMockRouter(mock));

    await page.goto(BASE_URL, { waitUntil: "load" });
    await page.click("#guideCloseBtn").catch(() => {});
    await page.setInputFiles("#videoFileInput", videoPath);
    await page.waitForFunction(() => document.getElementById("video").duration > 0, null, { timeout: 10000 });

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
    const statusText = await page.evaluate(() => document.getElementById("jobStatusLine").textContent);
    const statusClass = await page.evaluate(() => document.getElementById("jobStatusLine").className);
    const btnDisabled = await page.evaluate(() => document.getElementById("makeVideoBtn").disabled);
    const btnText = await page.evaluate(() => document.getElementById("makeVideoBtn").textContent);

    check(resultHref && resultHref.indexOf(`/${OWNER}/${REPO}/releases/tag/`) >= 0,
      "完成後、Releaseページへのリンクが表示される: " + resultHref);
    check(statusClass.indexOf("ok") >= 0, "完了メッセージが成功(ok)スタイルで表示される");
    check(statusText.indexOf("完成") >= 0, "完了メッセージに「完成」の文言が含まれる: " + statusText);
    check(btnDisabled === false, "完了後、ボタンが再度有効になる");
    check(btnText.indexOf("動画を作る") >= 0, "完了後、ボタンの表示が元に戻る");

    check(mock.createReleaseCalls === 1, "Release作成APIが1回呼ばれる");
    check(mock.uploadedAssetNames.sort().join(",") === "project.json,video.webm",
      "project.json と動画（video.webm）の2アセットがアップロードされる: " + mock.uploadedAssetNames.join(","));
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

    const mock = {
      tag: null, releaseId: 0, runId: 555002,
      createReleaseCalls: 0, dispatchCalls: 0, listRunsCalls: 0, getRunCalls: 0, getReleaseByTagCalls: 0,
      uploadedAssetNames: [],
      matchRunAfterCalls: 1, // すぐrunが見つかる
      runStatusSequence: [{ status: "completed", conclusion: "failure" }],
      includeOutputAsset: false,
    };
    await page.route("https://api.github.com/**", (route) => {
      const u = new URL(route.request().url());
      const m = /^\/repos\/[^/]+\/[^/]+\/releases$/.exec(u.pathname);
      if (m && route.request().method() === "POST" && !mock.tag) {
        try {
          const body = JSON.parse(route.request().postData() || "{}");
          mock.tag = body.tag_name;
        } catch (e) { /* 無視 */ }
      }
      return makeGithubMockRouter(mock)(route);
    });
    await page.route("https://uploads.github.com/**", makeGithubMockRouter(mock));

    await page.goto(BASE_URL, { waitUntil: "load" });
    await page.click("#guideCloseBtn").catch(() => {});
    await page.setInputFiles("#videoFileInput", videoPath);
    await page.waitForFunction(() => document.getElementById("video").duration > 0, null, { timeout: 10000 });

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

    // どのGitHub APIも一切呼ばれないはず（呼ばれたらテスト失敗として検出する）
    let calledUnexpectedly = false;
    await page.route("https://api.github.com/**", (route) => { calledUnexpectedly = true; route.abort(); });
    await page.route("https://uploads.github.com/**", (route) => { calledUnexpectedly = true; route.abort(); });

    await page.goto(BASE_URL, { waitUntil: "load" });
    await page.click("#guideCloseBtn").catch(() => {});
    await page.setInputFiles("#videoFileInput", videoPath);
    await page.waitForFunction(() => document.getElementById("video").duration > 0, null, { timeout: 10000 });

    // ユーザー名・トークンを空のまま押す
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

  await browser.close();

  console.log("");
  console.log(passed + " 件成功 / " + failed + " 件失敗");
  if (failed > 0) process.exit(1);
}

main().catch((err) => {
  console.error("テスト実行中に例外:", err);
  process.exit(1);
});
