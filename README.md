# spotlight-reel

リール向け短尺動画（10秒〜1分）に対して、指定した時刻で人物を**フリーズ**させ、
指でなぞった軌跡を**太いブラシのストロークアニメーション**でカラー化し、
**名前テロップ**と**効果音**を合成する動画レンダラーです。

編集操作はスマホのブラウザ上のエディタ（`index.html`、GitHub Pagesで公開）で行います。
エディタの「🎬 動画を作る」を押すだけで、非公開の処理専用リポジトリ
[`spotlight-jobs`](#動画を作るgithub-actionsでの自動レンダリング) 上の GitHub Actions が
`render.py` を実行し、完成した動画を GitHub の Release ページから保存できます
（GitHub無料枠のみで完結し、追加のサーバー・課金は不要です）。

ネットワークが使えない場合や動作確認用に、書き出した **プロジェクトJSON** を
**Google Colab**（Python 3.10以上、ffmpeg あり）で処理する手動フローも引き続き使えます。

## リポジトリ構成

```
spotlight-reel/
├── index.html            # スマホ用エディタ（GitHub Pagesで公開、ビルド不要の単一HTML）
├── diag.html              # 動画再生・シークの自己診断ツール（実機での不具合調査用）
├── render.py              # メイン：JSON + 動画 → MP4
├── make_dummy.py          # テスト用ダミー動画・ダミーJSON生成
├── requirements.txt       # opencv-python-headless, numpy, pillow
├── assets/
│   ├── fonts/             # NotoSansJP-Bold.ttf
│   └── sfx/                # shakin.wav, don.wav
├── examples/
│   └── sample.json         # make_dummy.py が生成するサンプル
├── tests/
│   ├── editor_logic.test.js                    # index.html のJSON生成・ジョブ連携ロジックのユニットテスト（依存なし）
│   ├── player_ui.playwright.test.mjs           # 再生/停止・シークのUI回帰テスト（任意、要playwright-core）
│   ├── freeze_black_screen.playwright.test.mjs # フリーズ追加時の黒画面回帰テスト（任意、要playwright-core）
│   ├── resize_scroll_black_screen.playwright.test.mjs # スクロール/リサイズ時の黒画面回帰テスト（任意、要playwright-core）
│   ├── make_video_job.playwright.test.mjs      # 「動画を作る」ボタンのE2Eテスト（GitHub APIはモック、任意、要playwright-core）
│   └── make_video_job_live_cors.playwright.test.mjs # 実トークンでのCORS実証テスト（モック無し、既定はスキップ、要SPOTLIGHT_LIVE_PAT）
├── colab.ipynb             # Colab用ノートブック（手動フォールバック）
└── README.md
```

処理専用の非公開リポジトリ `spotlight-jobs`（GitHub Actionsワークフローのみを置く）は
別リポジトリです。詳細は [`spotlight-jobs` のREADME](https://github.com/Digital-twin-creator/spotlight-jobs#readme)
を参照してください。

## スマホ用エディタ（index.html）

**公開URL**: https://digital-twin-creator.github.io/spotlight-reel/
（GitHub Pagesの有効化手順は本ページ末尾「GitHub Pages の有効化」を参照）

外部ライブラリ・ビルド一切なしの単一HTMLファイルです。iPhone Safari / Android Chrome の
両方で、開くだけでそのまま動作します。

### 使い方（スマホでの操作手順）

1. 上記URLをスマホのブラウザで開き、「🎬 動画を選ぶ」で編集したい動画を選択する
   （動画はサーバーに送信されず、その場で再生されるだけ）。
2. シークバーや微調整ボタン（−1秒/−0.1秒/+0.1秒/+1秒）で止めたい瞬間に合わせ、
   「＋ フリーズ追加」をタップする。
3. 表示された静止フレームの上を指でなぞって軌跡を描き、名前・効果音・背景処理を選んで「完了」。
   「▶ プレビュー」で演出の雰囲気をその場で確認できる。
4. 必要なだけフリーズを追加したら（一覧のカードから編集・削除・シークが可能）、
   「⑦ 動画を作る」の「🎬 動画を作る」を押す（初回のみ下記のGitHub連携設定が必要）。
   進捗（アップロード％→レンダリング中→完了）が画面に表示され、完了すると
   GitHubのReleaseページを開くボタンが出るので、そこから完成動画を保存する。
   うまくいかない場合は「⑧ 書き出し・読み込み」の「📤 JSONを書き出す」で
   `project.json` を書き出し、Colabへの手動受け渡し（後述）にフォールバックできる
   （共有シート対応時はそのままファイル/ドライブへ、非対応ブラウザではダウンロードになる。
   「📋 JSONをコピー」でクリップボードにも取れる）。

編集内容は動画ファイル名をキーに端末の `localStorage` に自動保存されるため、途中でリロードしても
同じ動画ファイルを選び直せば復元されます（動画そのものは保存されないので、再選択が必要です）。

### 動画を作る（GitHub Actionsでの自動レンダリング）

初回だけ、エディタの「⑦ 動画を作る」内の「GitHub連携の設定」に、以下の3つを入力して保存します。

- **GitHubユーザー名（組織名）**: `Digital-twin-creator`
- **処理用リポジトリ名**: `spotlight-jobs`（既定値のままでOK）
- **Personal Access Token**: `spotlight-jobs` だけにアクセスできる
  Fine-grained PAT（**Contents: Read and write** / **Actions: Read and write**）。
  発行手順は [`spotlight-jobs` のREADME](https://github.com/Digital-twin-creator/spotlight-jobs#readme)
  に記載しています。トークンはこの端末の `localStorage` にのみ保存され、
  `api.github.com` 以外には送信されません（画面上はマスク表示、
  👁ボタンで表示切り替え可能）。

保存後は、動画を選んでフリーズを編集したあと「🎬 動画を作る」を押すだけです。内部では、

1. 動画・`project.json` を、`spotlight-jobs` の一時ブランチ（`job-YYYYMMDD-HHMMSS`）に
   Git Data API（`api.github.com`）経由でコミットする（大きな動画でも進捗％が表示されます）。
   **動画は1本100MBまで**です。超える場合はアップロード前にエラーを表示して中断します
   （実機で `uploads.github.com` へのブラウザからの直接アップロードがCORSで拒否されることが
   判明したため、この方式にしています）。
2. `spotlight-jobs` に空のReleaseを作成する（タグは同じ `job-YYYYMMDD-HHMMSS`）。
3. `spotlight-jobs` の GitHub Actions ワークフロー（`render.yml`）を起動する。
   ワークフロー側は先のブランチをcheckoutしてrender.pyを実行し、`output.mp4` を
   同じReleaseにアップロード（Actions側からのアップロードなのでCORSの影響を受けません）、
   最後に一時ブランチを削除する。
4. ワークフローの完了をポーリングで待つ（ページを閉じずにお待ちください）。
5. 成功したら、Releaseに追加された `output.mp4` を確認し、Releaseページを開くボタンを表示する
   （ダウンロードはGitHub自身のページから行うため、その端末のブラウザで **GitHubにログインしている必要があります**）。
6. 失敗した場合は、画面にエラー内容を表示します（GitHub Actionsのログ・ジョブサマリーも参照できます）。

処理済みのRelease（出力動画）は `spotlight-jobs` 側で7日後に自動削除されます。

### 動画が再生・シークできないときは（diag.html）

実機（特にiPhone Safari）で動画の再生やシークが動かない場合は、
https://digital-twin-creator.github.io/spotlight-reel/diag.html を開いてください。
開くだけで自動的に、動画の取得・デコード・再生・シークまわりの状態を順に調べ、
画面に一覧表示します（途中1回だけ、再生テストのためのタップが必要です）。
「📋 結果をコピー」で全文をコピーできるので、不具合報告の際に貼り付けてください。
自分の動画ファイルを選んで同じ診断を行うこともできます。

### Colabへの受け渡し手順（手動フォールバック）

「動画を作る」が使えない・失敗する場合の代替手段です。

1. `colab.ipynb` を Colab で開く（GitHub連携から直接開くか、`File > Upload notebook`）。
2. スマホで書き出した `project.json` と、元の動画ファイルを、Googleドライブの
   **`マイドライブ/spotlight_reel/`** フォルダに置く
   （例: `MyDrive/spotlight_reel/input.mp4` と `MyDrive/spotlight_reel/project.json`）。
   動画ファイル名は `project.json` の `"video"` と完全に一致していなくても構いません
   （下記参照）。
3. 「ランタイム → すべてのセルを実行」する。上から順に、
   1. リポジトリの `git clone` と `pip install -r requirements.txt`
   2. Googleドライブのマウント
   3. `project.json` の読み込み・動画の自動解決・`render.py` の実行
   4. 成功時のみ、出力動画の再生とスマホへのダウンロード

   が自動的に行われます（パスを手で書き換える必要はありません）。
4. 出力は `MyDrive/spotlight_reel/output_YYYYMMDD_HHMM.mp4` として保存されます。

動画の自動解決は次の順で行われます:
- `project.json` の `"video"` の**ファイル名本体（拡張子を除いた部分）**と一致する
  ファイルを、拡張子を問わず `spotlight_reel` フォルダの中から探す
  （例: `"video": "input.mov"` でも、フォルダ内に `input.mp4` があればそれを使う）。
- 見つからなければ、フォルダ内で**最終更新日時が最も新しい動画ファイル**を使う。

途中で失敗した場合（Googleドライブ未接続・`project.json` が無い・動画が見つからない・
`render.py` のエラーなど）は、原因をノートブック上に大きく表示して処理を止めます。
その場合、続く再生・ダウンロードのセルは実行されません（エラーにはなりません）。

## 使い方（ローカル / CLIから直接）

```bash
pip install -r requirements.txt
python make_dummy.py                                   # テスト用の動画・JSON・SFX・フォントを生成
python render.py examples/sample.json --out examples/out.mp4
python render.py examples/sample.json --video other.mp4 --out out.mp4   # 動画を差し替え
python render.py examples/sample.json --preview preview.png             # 確認用PNGを1枚だけ出力
```

`--video` を省略した場合は、プロジェクトJSON内の `video` に指定されたパスが使われます
（JSONファイルと同じディレクトリ、またはリポジトリ直下からの相対パスとして解決されます）。

## プロジェクトJSON契約

```json
{
  "version": 1,
  "video": "input.mp4",
  "output": { "width": 1080, "height": 1920, "fps": 30 },
  "style": {
    "freeze_sec": 2.5,
    "brush_anim_sec": 0.8,
    "brush_width": 0.12,
    "background": "mono",
    "font": "assets/fonts/NotoSansJP-Bold.ttf",
    "audio_during_freeze": "mute"
  },
  "freezes": [
    {
      "time": 3.4,
      "name": "山田 太郎",
      "sfx": "shakin",
      "strokes": [
        { "width": 0.12, "points": [[0.42, 0.31], [0.44, 0.45], [0.43, 0.60]] }
      ]
    }
  ]
}
```

### ルール

- `points` の座標と `width`（太さ）は動画サイズに対する **0〜1の比率** です。
  `x` は幅、`y` は高さ、`width` は幅を基準にします。
- `style` は全体の既定値です。各 `freezes[]` 要素に同名キーがあれば、そちらが優先されます
  （`freeze_sec` / `brush_anim_sec` / `brush_width` / `background` / `font` / `audio_during_freeze` を個別上書き可能）。
- `output` を省略した場合は、元動画と同じ解像度・fpsで出力します。
- 未知のキーは無視されます（将来の拡張用に安全に読み飛ばします）。
- `freezes` は `render.py` 内部で `time` 順にソートしてから処理します。JSON内の記述順は問いません。

### 演出仕様（1フリーズあたり）

1. **フリーズ開始〜ブラシ開始（0.3秒固定）**：静止フレームを `background` で処理して表示
   - `mono`：グレースケール化
   - `dark`：元のカラーを30%の明るさに減光
2. **ブラシ描画（`brush_anim_sec`）**：ストロークが先頭から順に伸びていく
   - 丸キャップ・丸ジョイントの太線（白、不透明度0.85）
   - 描画済みの領域だけ元のカラーが見える。マスクの縁は数ピクセルぼかす
   - 複数ストロークがある場合は合計時間 `brush_anim_sec` の中で順番に描く
3. **ブラシ完了時点**：名前テロップ（高さ78%あたり、中央揃え、`font`使用、
   文字サイズは高さの6%、白文字＋黒縁取り、0.15秒でフェードイン）を表示し、
   効果音 `sfx`（`assets/sfx/{sfx}.wav`）を鳴らす
4. **残り時間**：`freeze_sec` になるまでカラー化＋テロップを保持
5. 通常再生に戻る

### 音声仕様

- 元動画の音声は、フリーズ区間を挿入した分だけ後ろへずれます。
- フリーズ中は `audio_during_freeze` が `mute` なら無音、`keep` なら直前0.5秒をループします。
- 効果音はブラシ完了の瞬間にミックスされます。

## 実装メモ

- フレームは1枚ずつ読み書きし、全フレームをメモリに保持しません。
- 映像は `cv2.VideoWriter` を使わず、ffmpegへ rawvideo をパイプして
  `libx264 / yuv420p / crf 20` でエンコードします（Colabで確実にH.264 MP4を出すため）。
- 縦動画の回転メタデータを考慮し、表示上の向きでフレームを処理します。
- 音声の切り貼り・ミックスは numpy で行い、最後に ffmpeg で映像と結合します。

## 動作確認

```bash
python make_dummy.py
python render.py examples/sample.json --out examples/out.mp4
ffprobe examples/out.mp4   # 長さ・音声トラックを確認
python render.py examples/sample.json --preview examples/preview.png

node tests/editor_logic.test.js   # index.html のJSON生成・ジョブ連携ロジックのユニットテスト（依存なし）

# 任意：UI回帰テスト（playwright-coreとChromiumが必要）
npm install --no-save playwright-core
python3 -m http.server 8794 &   # index.html をどこかで配信しておく
node tests/player_ui.playwright.test.mjs
node tests/freeze_black_screen.playwright.test.mjs
node tests/resize_scroll_black_screen.playwright.test.mjs
node tests/make_video_job.playwright.test.mjs   # 「動画を作る」ボタンのE2E（GitHub APIはモック、実際の通信はしない）

# 任意：モック無しでの実CORS実証（実トークンが必要。既定ではSPOTLIGHT_LIVE_PAT未設定でスキップされる）
SPOTLIGHT_LIVE_PAT=github_pat_xxxx node tests/make_video_job_live_cors.playwright.test.mjs
```

## GitHub Pages の有効化

`index.html` をスマホから開けるようにするには、リポジトリの GitHub Pages を有効化してください
（このリポジトリでは自動化する権限がなかったため、以下は手動手順です）。

1. GitHubでこのリポジトリの **Settings → Pages** を開く
2. **Build and deployment → Source** を `Deploy from a branch` に設定
3. **Branch** を `main` / `/ (root)` にして **Save**
4. 数分後に `https://digital-twin-creator.github.io/spotlight-reel/` で公開される

## 今後の予定

- スマホ用エディタ（`index.html`）の実機（iPhone Safari / Android Chrome）での最終確認
- エディタから書き出したJSONを使った、より長い実動画でのレンダリング確認
- 「動画を作る」ボタンの、実トークンを使ったブラウザからの `api.github.com` 呼び出し
  （Git Data API でのblob作成含む）がCORS的に問題なく通ることの実機確認。
  新方式（`job-<tag>` ブランチへのコミット）自体は git 経由でエンドツーエンドの
  動作確認済み（ブランチコミット→render.yml起動→output.mp4のRelease追加→ブランチ自動削除）
  だが、それはブラウザのfetch/XHRを経由した確認ではないため、実機での最終確認が必要。
  モック無しの検証用テスト（`tests/make_video_job_live_cors.playwright.test.mjs`、
  実トークンを環境変数 `SPOTLIGHT_LIVE_PAT` で渡して実行）を用意済み。
