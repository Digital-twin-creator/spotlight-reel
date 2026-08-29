#!/usr/bin/env node
// -*- coding: utf-8 -*-
//
// 「動画を作る」ボタンが、api.github.com への"本物の"ブラウザ発リクエストでCORS拒否されずに
// 動くことを実証する、モック無しの一回限りの検証テスト。
//
// tests/make_video_job.playwright.test.mjs はGitHub APIを全てモックしているため、
// 「本当にCORSが通るか」は証明していない（実際、Release アセットへのアップロード
// (uploads.github.com) はモックテストでは全件成功していたにもかかわらず、実機では
// CORS拒否されることが後から判明した）。このテストはその教訓を踏まえ、モック無しで
// 実際の api.github.com に実トークンでアクセスし、Git Data API 経由のコミット
// （blob→tree→commit→ref）がブラウザから正しく完了することを確認する。
//
// 実行方法（実トークンが必要。CIやこのサンドボックス環境では既定でスキップされる）:
//   npm install --no-save playwright-core
//   python3 -m http.server 8794 &
//   SPOTLIGHT_LIVE_PAT=github_pat_xxxx \
//   SPOTLIGHT_LIVE_USER=Digital-twin-creator \
//   SPOTLIGHT_LIVE_REPO=spotlight-jobs \
//     node tests/make_video_job_live_cors.playwright.test.mjs
//
// トークンは spotlight-jobs だけにアクセスできるFine-grained PAT
// （Contents: Read and write / Actions: Read and write）を使うこと。
// このテストは実際に spotlight-jobs へブランチ作成・Release作成・workflow_dispatchを行う
// （後片付けは render.yml 側の「job-<tag>ブランチを削除」ステップに任せる）。
//
// 注意: このサンドボックス実行環境自体は、エージェント（Claude）がGitHub APIへ直接
// アクセスすることをポリシーで禁止しており、Bash/curlだけでなくこのテストのような
// ブラウザ発リクエストであっても迂回にあたるため、Claude自身の資格情報でこのテストを
// 実行することはない。SPOTLIGHT_LIVE_PAT が未設定の場合は何も通信せずスキップする。

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

const TOKEN = process.env.SPOTLIGHT_LIVE_PAT || "";
const OWNER = process.env.SPOTLIGHT_LIVE_USER || "Digital-twin-creator";
const REPO = process.env.SPOTLIGHT_LIVE_REPO || "spotlight-jobs";

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
  if (!TOKEN) {
    console.log("SPOTLIGHT_LIVE_PAT が未設定のため、このテストはスキップします。");
    console.log("（実トークンを使う一回限りのCORS実証テストのため、既定では何もしません）");
    process.exit(0);
  }

  const videoPath = prepareTestVideo();
  const iphone = devices["iPhone 13"];
  const browser = await chromium.launch({ executablePath: CHROMIUM_PATH, headless: true });
  const context = await browser.newContext({ ...iphone });
  const page = await context.newPage();
  const pageErrors = [];
  const corsFailureRequests = [];
  page.on("pageerror", (e) => pageErrors.push(e.message));
  page.on("requestfailed", (req) => {
    if (req.url().indexOf("api.github.com") >= 0) {
      corsFailureRequests.push(req.url() + " :: " + (req.failure() && req.failure().errorText));
    }
  });

  console.log("=== 実GitHub・実トークンでの「動画を作る」検証（モック無し） ===");
  console.log("対象: https://api.github.com/repos/" + OWNER + "/" + REPO);

  await page.goto(BASE_URL, { waitUntil: "load" });
  await page.click("#guideCloseBtn").catch(() => {});
  await page.setInputFiles("#videoFileInput", videoPath);
  await page.waitForFunction(() => document.getElementById("video").duration > 0, null, { timeout: 10000 });

  await page.evaluate(() => { document.getElementById("ghSettingsDetails").open = true; });
  await page.fill("#ghUserInput", OWNER);
  await page.fill("#ghRepoInput", REPO);
  await page.fill("#ghTokenInput", TOKEN);

  await page.click("#makeVideoBtn");

  // 完成(jobResultLinkが表示される) or 失敗(jobStatusLineがerrクラスになる) のどちらかで停止するまで待つ
  // （レンダリングには数十秒かかりうるため、長めに待つ）
  await page.waitForFunction(
    () => !document.getElementById("jobResultLink").hidden
      || document.getElementById("jobStatusLine").className.indexOf("err") >= 0,
    null, { timeout: 5 * 60 * 1000 }
  );

  const statusText = await page.evaluate(() => document.getElementById("jobStatusLine").textContent);
  const statusClass = await page.evaluate(() => document.getElementById("jobStatusLine").className);
  const resultLinkHidden = await page.evaluate(() => document.getElementById("jobResultLink").hidden);

  console.log("最終状態: " + statusClass + " / " + statusText);

  // CORSで拒否された場合の典型的な症状：fetch()のネットワーク例外、またはXHRのonerror
  // （いずれも「GitHub API エラー (ステータスコード): ...」のような構造化メッセージにはならない）
  const looksLikeCorsOrNetworkFailure =
    statusText.indexOf("Failed to fetch") >= 0 ||
    statusText.indexOf("通信エラーが発生しました") >= 0 ||
    statusText.indexOf("NetworkError") >= 0;

  check(corsFailureRequests.length === 0,
    "api.github.com 宛のリクエストがブラウザレベルで失敗していない（CORS/ネットワークエラー無し）: " +
    JSON.stringify(corsFailureRequests));
  check(!looksLikeCorsOrNetworkFailure,
    "画面上のエラーメッセージがCORS/ネットワーク失敗の症状を示していない: " + statusText);
  check(!resultLinkHidden || statusClass.indexOf("err") >= 0,
    "処理が最終状態（成功 or 明確な失敗）まで進んだ（応答なしでハングしていない）");
  check(pageErrors.length === 0, "ページ例外が発生していない: " + JSON.stringify(pageErrors));

  if (!resultLinkHidden) {
    const resultHref = await page.getAttribute("#jobResultLink", "href");
    console.log("完成: " + resultHref);
  } else {
    console.log("完成には至らなかった（詳細は上記の最終状態を参照。CORS自体は通っているかを主目的として確認）");
  }

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
