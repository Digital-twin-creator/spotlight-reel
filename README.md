# spotlight-reel

リール向け短尺動画（10秒〜1分）に対して、指定した時刻で人物を**フリーズ**させ、
指でなぞった軌跡を**太いブラシのストロークアニメーション**でカラー化し、
**名前テロップ**と**効果音**を合成する動画レンダラーです。

編集操作はスマホのブラウザ上のエディタ（`index.html`、GitHub Pagesで公開）で行います。
エディタの「🎬 動画を作る」を押すだけで、非公開の処理専用リポジトリ
[`spotlight-jobs`](#動画を作るgithub-actionsでの自動レンダリング) 上の GitHub Actions が
`render.py` を実行し、完成したら「💾 完成動画を保存」ボタンから直接保存できます
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
│   ├── fonts/             # NotoSansJP-Bold.ttf, Anton-Regular.ttf（欧文タイトル用）
│   ├── sfx/                # shakin.wav, don.wav
│   └── brushes/            # round/hake/marker/spray.png（筆先スタンプ画像、白＋アルファ）
├── examples/
│   ├── sample.json         # make_dummy.py が生成するサンプル
│   └── store_logo.png      # make_dummy.py が生成するダミーのラストロゴ画像
├── tests/
│   ├── editor_logic.test.js                    # index.html のJSON生成・ジョブ連携ロジックのユニットテスト（依存なし）
│   ├── player_ui.playwright.test.mjs           # 再生/停止・シークのUI回帰テスト（任意、要playwright-core）
│   ├── freeze_black_screen.playwright.test.mjs # フリーズ追加時の黒画面回帰テスト（任意、要playwright-core）
│   ├── resize_scroll_black_screen.playwright.test.mjs # スクロール/リサイズ時の黒画面回帰テスト（任意、要playwright-core）
│   ├── brush_shape.playwright.test.mjs         # ブラシ形状選択・筆先スタンプ描画の回帰テスト（任意、要playwright-core）
│   ├── film_logo_bounce.playwright.test.mjs    # フィルム縁取り・テロップバウンス・ラストロゴ設定の回帰テスト（任意、要playwright-core）
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
3. 表示された静止フレームの上を指でなぞって軌跡を描き、名前・効果音・背景処理・
   ブラシ形状（round/hake/marker/spray）・フィルム縁取り色（このフリーズだけ全体設定の
   色を上書きしたい場合）を選んで「完了」。
   ブラシ形状は描いている途中で切り替えると、その場でストロークの見た目が変わる。
   「▶ プレビュー」で、フィルム縁取り・テロップのバウンス演出も含めた雰囲気をその場で確認できる。
4. 「全体設定」でフリーズの長さ・フィルム縁取り（ズレ量・色プリセット・不透明度）・
   テロップのバウンスON/OFFなどを、「ラストロゴ」で動画末尾やラストフリーズに重ねる
   ロゴ画像とその表示時間・タイミングを、それぞれ必要に応じて調整できる。
5. 必要なだけフリーズを追加したら（一覧のカードから編集・削除・シークが可能）、
   「⑦ 動画を作る」の「🎬 動画を作る」を押す（初回のみ下記のGitHub連携設定が必要）。
   進捗（アップロード％→起動待ち→レンダリング中→完了）が画面に表示され、完了すると
   「💾 完成動画を保存」ボタンが出るので、それを押すと `output.mp4` が直接ダウンロードされる
   （GitHubのReleaseページを自分で探す必要はない）。
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
  👁ボタンで表示切り替え可能）。保存済みのときは設定欄に
  「保存済み（トークン末尾 …xxxx）」と表示され、再入力しなくても保存されているかが分かります
  （ユーザー名・リポジトリ名は保存した値がそのまま入力欄に残ります）。

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
4. 起動したワークフローの実行がGitHub Actions側の一覧に現れるまで、最低90秒はポーリングで
   待つ（「起動を待っています…」と表示）。GitHub側の反映が遅く90秒待っても見つからない場合でも
   **失敗扱いにはしません**。「自動確認できませんでした」という案内と、GitHubの
   Actionsページ・Releaseページを直接開くリンクを表示して終わります（実際にはGitHub側で
   実行・成功していることがあります）。
5. runが見つかった場合は、完了まで（最大30分）ポーリングで待つ（ページを閉じずにお待ちください）。
6. 成功したら、Releaseに追加された `output.mp4` の**アセット本体への直接ダウンロードURL**
   （`browser_download_url`）を確認し、「💾 完成動画を保存」ボタンを表示する。押すと
   そのURLを新しいタブで開くだけ（JSのfetch/XHRでバイト列を読むわけではない）なので、
   Releaseページを経由する必要もCORSの影響を受けることもない
   （ダウンロードはGitHub自身のサーバーが直接行うため、その端末のブラウザで
   **GitHubにログインしている必要があります**）。
7. 失敗した場合は、画面にエラー内容を表示します（GitHub Actionsのログ・ジョブサマリーも参照できます）。

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
    "freeze_sec": 1.2,
    "brush_anim_sec": 0.8,
    "brush_width": 0.12,
    "brush_shape": "round",
    "mono_contrast": 1.3,
    "film_offset": [0.0074, 0.0074],
    "film_color": "#FF6432",
    "film_alpha": 0.8,
    "background": "mono",
    "font": "assets/fonts/NotoSansJP-Bold.ttf",
    "title_font": "assets/fonts/Anton-Regular.ttf",
    "title_font_jp": "assets/fonts/NotoSansJP-Bold.ttf",
    "title_bounce": true,
    "audio_during_freeze": "mute"
  },
  "freezes": [
    {
      "time": 3.4,
      "name": "山田 太郎",
      "sfx": "shakin",
      "brush_shape": "hake",
      "film_color": "#00C8FF",
      "strokes": [
        { "width": 0.12, "points": [[0.42, 0.31], [0.44, 0.45], [0.43, 0.60]] }
      ]
    }
  ],
  "logo": {
    "image": "store_logo.png",
    "at": "last_freeze",
    "duration_sec": 1.5,
    "sfx": "don"
  }
}
```

### ルール

- `points` の座標と `width`（太さ）は動画サイズに対する **0〜1の比率** です。
  `x` は幅、`y` は高さ、`width` は幅を基準にします。
- `style` は全体の既定値です。各 `freezes[]` 要素に同名キーがあれば、そちらが優先されます
  （`freeze_sec` / `brush_anim_sec` / `brush_width` / `brush_shape` / `mono_contrast` /
  `film_color` / `film_alpha` / `background` / `font` / `audio_during_freeze` を個別上書き可能。
  `film_offset` / `title_font` / `title_font_jp` / `title_bounce` は `style` のみで指定します）。
- `brush_shape` は `round`（丸筆・既定値）/ `hake`（ハケ）/ `marker`（平筆マーカー）/
  `spray`（スプレー）のいずれか。未指定・不明な値は `round` として扱われます
  （後方互換：この項目が無い既存のJSONもそのまま動きます）。
- `output` を省略した場合は、元動画と同じ解像度・fpsで出力します。
- 未知のキーは無視されます（将来の拡張用に安全に読み飛ばします）。
- `freezes` は `render.py` 内部で `time` 順にソートしてから処理します。JSON内の記述順は問いません。
- 新しいキー（`mono_contrast` / `film_offset` / `film_color` / `film_alpha` / `title_font` /
  `title_font_jp` / `title_bounce` / `logo`）はすべて省略可能で、省略時は旧バージョンの
  `render.py` と同じ見た目で動きます（後方互換。詳細は次項）。

### 演出仕様（1フリーズあたり）

1. **フリーズ開始〜ブラシ開始（0.3秒固定）**：静止フレームを `background` で処理して表示
   - `mono`：グレースケール化（`mono_contrast` でコントラストを強調。1.0が無効化＝既定値）
   - `dark`：元のカラーを30%の明るさに減光
2. **ブラシ描画（`brush_anim_sec`）**：ストロークが先頭から順に伸びていく。静止画レイヤーは
   下から順に「①モノクロ／減光背景 → ②フィルム縁取り → ③人物カラー」の3枚重ねになる
   - `brush_shape` の筆先画像（`assets/brushes/<shape>.png`、白＋アルファ）を軌跡に沿って
     一定間隔でスタンプする「筆先スタンプ方式」。各スタンプは進行方向に合わせて回転する
   - `round`：柔らかい縁の丸筆（従来の丸キャップ相当）
   - `hake`：毛筋（縦方向の濃淡バンド）・縁のギザつき・わずかな不透明度ムラを持つ平たいハケ
   - `marker`：角の丸い長方形。縁はくっきりしていて、重なった部分は自然と少し濃くなる
   - `spray`：円形範囲にランダムな点をまき散らしたスプレー
   - **フィルム縁取り**：本番のブラシマスクを `film_offset`（出力幅に対する比率。x・yとも
     同じ量）だけ平行移動し、`film_color` を `film_alpha` の不透明度で敷いてから、その上に
     本来の位置の人物カラーを重ねる。人物ごとに縁取りの色を変えたい場合は、`freezes[]` の
     `film_color` で個別に上書きできる。`film_offset` が `[0, 0]`（既定値）のときは
     人物カラーの真下に完全に隠れるため、見た目には現れない（＝後方互換）
   - 複数ストロークがある場合は合計時間 `brush_anim_sec` の中で順番に描く
3. **ブラシ完了時点**：名前テロップ（高さ78%あたり、中央揃え、日本語を含む名前は
   `title_font_jp`、それ以外は `title_font` を使用。どちらも未指定なら `font` にフォール
   バックする、文字サイズは高さの6%、白文字＋薄い黒ドロップシャドウ）を0.15秒でフェード
   インさせ、効果音 `sfx`（`assets/sfx/{sfx}.wav`）を鳴らす。`title_bounce: true` なら
   フェードインと同じ0.15秒の間に130%→100%へ急停止イージングで縮む「はずむ」演出になる
4. **残り時間**：`freeze_sec` になるまでカラー化＋テロップを保持
   （`logo.at: "last_freeze"` を指定した場合、時刻が一番遅いフリーズだけこの保持時間が
   `logo.duration_sec` を満たすよう自動的に延長され、その間ロゴが重ねて表示される）
5. 通常再生に戻る

### ラストロゴ（`logo`）

- `logo.image`：ロゴ画像（PNG推奨、透過対応）のパス。省略するとロゴ演出は無効
- `logo.at`：`"end"`（動画の末尾に新しい区間として追加。既定値）または
  `"last_freeze"`（時刻が一番遅いフリーズの保持中に重ねて表示）
- `logo.duration_sec`：表示時間（秒）。既定 1.5 秒
- `logo.sfx`：表示開始と同時に鳴らす効果音（省略可）
- ロゴは基準表示幅（出力幅の40%）を200%から100%へ、0.3秒で急停止イージングしながら
  縮小して表示され、以後は等倍のまま `duration_sec` 秒間保持されます。

### 音声仕様

- 元動画の音声は、フリーズ区間を挿入した分だけ後ろへずれます。
- フリーズ中は `audio_during_freeze` が `mute` なら無音、`keep` なら直前0.5秒をループします。
- 効果音はブラシ完了の瞬間にミックスされます。
- `logo.at: "end"` の場合、動画末尾に無音＋ロゴの効果音を追加した分だけ音声トラックも延長
  されます。`"last_freeze"` の場合は、そのフリーズの保持区間内でロゴの効果音が鳴ります。

## 実装メモ

- フレームは1枚ずつ読み書きし、全フレームをメモリに保持しません。
- 映像は `cv2.VideoWriter` を使わず、ffmpegへ rawvideo をパイプして
  `libx264 / yuv420p / crf 20` でエンコードします（Colabで確実にH.264 MP4を出すため）。
- 縦動画の回転メタデータを考慮し、表示上の向きでフレームを処理します。
- 音声の切り貼り・ミックスは numpy で行い、最後に ffmpeg で映像と結合します。

## 動作確認

```bash
python make_dummy.py   # assets/brushes/*.png（筆先スタンプ画像）もここで生成される
python render.py examples/sample.json --out examples/out.mp4
ffprobe examples/out.mp4   # 長さ・音声トラックを確認
python render.py examples/sample.json --preview examples/preview.png   # brush_shapeはJSON側で指定

node tests/editor_logic.test.js   # index.html のJSON生成・ジョブ連携ロジックのユニットテスト（依存なし）

# 任意：UI回帰テスト（playwright-coreとChromiumが必要）
npm install --no-save playwright-core
python3 -m http.server 8794 &   # index.html をどこかで配信しておく
node tests/player_ui.playwright.test.mjs
node tests/freeze_black_screen.playwright.test.mjs
node tests/resize_scroll_black_screen.playwright.test.mjs
node tests/brush_shape.playwright.test.mjs      # ブラシ形状選択・筆先スタンプ描画のE2E
node tests/film_logo_bounce.playwright.test.mjs # フィルム縁取り・テロップバウンス・ラストロゴ設定のE2E
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

### 解決済み（実機確認まで完了）

- 「動画を作る」ボタンの `api.github.com` 呼び出し（Git Data API でのblob作成含む）が
  実機のブラウザからCORSで拒否されずに通ることを確認済み（実際に動画のアップロードと
  ワークフローの起動・完了・`output.mp4` のRelease追加まで成功した実行を確認した）。
- 一方で、workflow_dispatch後にGitHub Actions側の一覧に新しいrunが反映されるまで
  数十秒かかることがあり、以前の30秒のポーリング上限では「実行が見つかりません」という
  誤った失敗表示になっていた不具合を修正済み（最低90秒待つ・見つからなくても失敗扱いに
  せずGitHub側の確認リンクを出す、という挙動に変更）。
