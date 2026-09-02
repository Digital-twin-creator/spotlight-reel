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
├── extract.py             # 自動切り抜き（アルファマット）モジュール。単体でも他プロジェクトからでも使える
├── make_dummy.py          # テスト用ダミー動画・ダミーJSON生成
├── requirements.txt       # opencv-python-headless, numpy, pillow（render.py本体の依存。extract.pyは含まない）
├── requirements-extract.txt # rembg, onnxruntime, numpy, pillow（extract.py / mask:"auto"系のみで必要）
├── assets/
│   ├── fonts/             # NotoSansJP-Bold.ttf, Anton-Regular.ttf（欧文タイトル用）
│   ├── sfx/                # shakin.wav, don.wav, impact.wav（差し替え可。後述「効果音を差し替える」参照）
│   └── brushes/            # round/hake/marker/spray.png（筆先スタンプ画像、白＋アルファ）
├── cache/                  # render.pyが自動切り抜きのアルファをキャッシュするディレクトリ（gitignore対象）
├── examples/
│   ├── sample.json         # make_dummy.py が生成するサンプル
│   ├── dummy_input.mp4     # ダミー動画（縦 1080x1920）
│   ├── dummy_input_landscape.mp4 # ダミー動画（横 1920x1080。縦横回帰テスト用）
│   ├── dummy_input_vfr.mp4 # ダミー動画（縦・可変フレームレート。VFR正規化の回帰テスト用）
│   ├── extract_test_silhouette.png # extract.py検証用（髪風の細線・指風の隙間を持つシルエット画像）
│   ├── store_logo.png      # make_dummy.py が生成するダミーのラストロゴ画像（透過PNG、余白16%）
│   └── store_logo_opaque_black.png/.jpg # 自動クロップ検証用ダミーロゴ（不透明・黒背景・余白30%、PNG/JPG双方）
├── tests/
│   ├── editor_logic.test.js                    # index.html のJSON生成・ジョブ連携ロジックのユニットテスト（依存なし）
│   ├── render_quality.test.py                  # render.pyの品質回帰テスト（音声連続性・VFR正規化・テロップ描画・
│   │                                            #   ブラシの白フェード・フォント読み込み失敗時のエラー終了・shadow/
│   │                                            #   mask=auto/auto+brush。要ffmpeg/numpy。rembg未インストール時は
│   │                                            #   mask=auto系の検証のみ自動でスキップされる）
│   ├── player_ui.playwright.test.mjs           # 再生/停止・シークのUI回帰テスト（任意、要playwright-core）
│   ├── freeze_black_screen.playwright.test.mjs # フリーズ追加時の黒画面回帰テスト（任意、要playwright-core）
│   ├── resize_scroll_black_screen.playwright.test.mjs # スクロール/リサイズ時の黒画面回帰テスト（任意、要playwright-core）
│   ├── brush_shape.playwright.test.mjs         # ブラシ形状選択・筆先スタンプ描画の回帰テスト（任意、要playwright-core）
│   ├── film_logo_bounce.playwright.test.mjs    # freeze_sec・テロップバウンス・ラストロゴ設定の回帰テスト（任意、要playwright-core）
│   ├── portrait_landscape_freezes.playwright.test.mjs # 縦横動画それぞれでフリーズ複数追加→JSON書き出しの回帰テスト（任意、要playwright-core）
│   ├── title_pos_drag.playwright.test.mjs      # テロップのドラッグ移動・サイズ/寄せ変更の回帰テスト（任意、要playwright-core）
│   ├── title_lines_editor.playwright.test.mjs  # テロップ複数行入力（行の追加/削除・サイズ/アンダーライン・フォント選択）の回帰テスト（任意、要playwright-core）
│   ├── mask_shadow_ui.playwright.test.mjs      # 切り抜き方法セレクタ・足す/消すトグル・影（スライド演出含む）/revealのUI回帰テスト（任意、要playwright-core）
│   ├── make_video_job.playwright.test.mjs      # 「動画を作る」ボタンのE2Eテスト（GitHub APIはモック、任意、要playwright-core）
│   ├── startup_robustness.playwright.test.mjs  # 巨大/壊れた自動保存データ・起動失敗時の復旧UIの回帰テスト（任意、要playwright-core）
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
   ブラシ形状（round/hake/marker/spray）を選んで「完了」。
   ブラシ形状は描いている途中で切り替えると、その場でストロークの見た目が変わる。
   名前を入力すると映像の上にテロップが仮表示され、それを指でドラッグすると
   このフリーズだけの表示位置（`title_pos`）を指定できる（テロップの上をタッチした時だけ
   移動モードになるので、ブラシ描画とは干渉しない。画面外に出ないよう端で止まる）。
   大きさは小さなスライダー、寄せ（左/中央/右）はセレクトで調整でき、どちらも
   「▶ プレビュー」の簡易再生に反映される。
   「▶ プレビュー」で、影（フィルム色）のスライド演出・テロップのバウンス・
   （ラストフリーズなら）ロゴの着地とスイープ演出も含めた雰囲気をその場で確認できる。
4. 「全体設定」で演出の3段構成（①塗り・②ズレ・③静止それぞれの秒数）・
   影（フィルム色。色プリセット・濃さ・ズレ距離・方向（自動/左/右）・ぼかし・
   影に使うマスク）・テロップのバウンスON/OFFなどを、「ラストロゴ」で
   動画末尾やラストフリーズに重ねるロゴ画像・背景（ロゴに合わせる／動画に重ねる／
   色指定）・表示時間・タイミングを、それぞれ必要に応じて調整できる。
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

- 復元処理（サムネイル等を含む）は、動画を選んだ直後の同期処理からは切り離し、
  次のタスクに遅延実行しています。フリーズ一覧はまず空の状態で即座に表示され、
  復元が終わり次第反映されます。
- 自動保存1件あたりのサイズには約2MBの上限を設けています。フリーズ数が多い等で
  上限を超える場合は、サムネイル（フリーズごとのプレビュー画像）を諦めて座標データ
  （ストローク・時刻・名前等）だけを保存します。サムネイル自体も保存時に長辺120pxへ
  縮小しています。
- 自動保存データが壊れている（JSONとして読めない）場合や、`localStorage`の保存容量
  上限に達した場合でも、エラーにはせず初期状態（フリーズ0件）から開始します。
- 起動処理そのものが失敗した場合（保存データの破損などで復旧できない状況）は、
  「保存データの読み込みに失敗しました。初期化して開く」という復旧画面を表示します。
  ボタンを押すとこのアプリの自動保存・GitHub連携設定等をすべて削除して再読み込みします。

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
  保存は「💾 設定を保存」ボタンを押した時だけでなく、各入力欄を変更するたびにも
  自動的に行われます。ページがクラッシュ・再読み込みされても、ボタンを押し忘れた
  入力内容が消えないようにするためです。

保存後は、動画を選んでフリーズを編集したあと「🎬 動画を作る」を押すだけです。

**アップロード前の内容チェック**：「🎬 動画を作る」を押すと、実際にアップロードを始める前に
`project.json` の内容をチェックし、レンダリング自体はエラーなく通るものの意図と違う
結果になりそうな状態（名前が入力されていないフリーズ、ブラシで何も塗られていない
`brush` モードのフリーズ、ロゴ画像が選ばれていないのにラストロゴの表示位置だけ
変更されている、等）があれば、警告の一覧を確認モーダルで表示します。「🔙 戻って直す」で
アップロードを中断して編集画面に戻るか、「このまま作る」でそのままアップロードを
続行するかを選べます（問題が無ければこのモーダルは出ず、そのままアップロードが始まります）。

内部では、

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
python render.py examples/sample.json --preview preview.png             # 確認用PNGを出力（後述）
```

プロジェクトJSONで `mask: "auto"` / `"auto+brush"` を使う場合は、別途
`pip install -r requirements-extract.txt` が必要です（詳細は次項「自動切り抜き」）。

`--video` を省略した場合は、プロジェクトJSON内の `video` に指定されたパスが使われます
（JSONファイルと同じディレクトリ、またはリポジトリ直下からの相対パスとして解決されます）。

## 自動切り抜き（`extract.py`）

画像1枚から被写体を自動で切り抜き、0/1ではない連続値の **アルファマット**（髪の毛や
指の間などの半透明境界を含む）を得る、**独立実行可能な**モジュールです。
[`rembg`](https://github.com/danielgatis/rembg)（MITライセンス）をラップしており、
`render.py` から使われるほか、`python extract.py` 単体で他プロジェクトからも
そのまま利用できます。

```bash
pip install -r requirements-extract.txt    # render.py本体の requirements.txt とは別（依存が重いため分離）
python extract.py input.png --out outdir/ \
    [--model birefnet-portrait|birefnet-general-lite|isnet-general-use] \
    [--refine vitmatte] [--decontaminate]
```

- `--model`：切り抜きモデル。既定は **`isnet-general-use`**。
  仕様上の既定は `birefnet-portrait` ですが、**実際のGitHub Actionsランナー**（CPU/
  onnxruntime、2vCPU共有）で1080x1920 1枚を実測したところ、モデル読み込み＋推論で
  **約64秒**（30秒を大きく超える）かかりました。要件どおりのフォールバック先である
  `birefnet-general-lite` も試しましたが、こちらは**モデルキャッシュの有無に関わらず
  約81.6秒**かかり（`actions/cache` でモデルファイルのダウンロードを省いても
  ほぼ同じ時間＝ダウンロードではなく推論自体がActionsランナーのCPU上で遅いことを
  2回の実行で確認済み）、依然として30秒を大きく超えてしまいます。そのため、
  候補3モデルのうち実機Actionsランナーで唯一30秒を大きく下回った `isnet-general-use`
  （実測 **約8.7秒**）を既定にしています。
  - `birefnet-portrait` / `birefnet-general-lite` は人物にチューニングされた新しめの
    モデルで、実際の人物写真に対する精度はより高い可能性があります。処理時間に余裕が
    ある用途（ローカル実行、GPU環境、より高性能なCIランナーなど）では明示的に指定
    してください。
  - `isnet-general-use` は最速ですが、古めの汎用モデル（人物専用にチューニングされて
    いない）です。
  - （参考：手元のサンドボックス環境での実測は `birefnet-portrait` 約64秒 /
    `birefnet-general-lite` 約20〜26秒 / `isnet-general-use` 約7秒で、
    実際のGitHub Actionsランナーとは異なる結果になりました。CPU性能はランナーごとに
    大きく異なるため、既定モデルの選定は必ず実際に動かす環境で計測することを
    お勧めします。）
- `--refine vitmatte`：rembg組み込みのViTMatteによる境界精密化（髪の毛など細い構造の
  アルファを推定し直す。初回実行時に専用のONNXモデルを追加でダウンロードする）
- `--decontaminate`：半透明の境界画素に残る背景色のにじみを、前景色を推定し直すことで
  除去する（rembg組み込み、pymattingベース）。`--refine vitmatte` を指定した場合は
  rembgの仕様により常に同等の処理が行われる
- 出力（`outdir/` 配下、**固定のファイル名・形式**。他プロジェクトと共有できる契約）：

  | ファイル | 内容 |
  | --- | --- |
  | `subject-rgba.png` | 前景RGB＋アルファ（透過PNG） |
  | `alpha.png` | アルファチャンネルのみ（8bitグレースケール、0〜255の連続値） |
  | `mask.png` | アルファ≥128 の2値マスク（0 または 255） |
  | `preview.png` | チェッカー背景に合成した確認用プレビュー |
  | `metadata.json` | `{"extractor", "refine", "decontaminate", "input", "size", "elapsedSec", "createdAt"}` |

- モデルファイルは `~/.rembg/models/<model名>/` にダウンロード・キャッシュされます
  （GitHub Actionsでは `actions/cache` でこのディレクトリをキャッシュし、2回目以降の
  ダウンロードを省いています。詳細は [`spotlight-jobs` のREADME](https://github.com/Digital-twin-creator/spotlight-jobs#readme)）。
- `render.py` は `extract.py` の `extract_alpha()` を直接importして使います。import自体は
  関数が実際に呼ばれるまで遅延されるため、`mask: "brush"` のみのプロジェクトでは
  `rembg` / `onnxruntime` が未インストールでも `render.py` は問題なく動作します。

## プロジェクトJSON契約

```json
{
  "version": 1,
  "video": "input.mp4",
  "output": { "width": 1080, "height": 1920, "fps": 30 },
  "style": {
    "reveal_sec": 0.5,
    "slide_sec": 0.5,
    "hold_sec": 2.0,
    "brush_width": 0.12,
    "brush_shape": "round",
    "mono_contrast": 1.3,
    "background": "mono",
    "font": "assets/fonts/NotoSansJP-Bold.ttf",
    "title_font": "assets/fonts/Anton-Regular.ttf",
    "title_font_jp": "assets/fonts/NotoSansJP-Bold.ttf",
    "title_bounce": true,
    "title_pos": [0.5, 0.78],
    "title_size": 0.06,
    "title_align": "center",
    "brush_fade_sec": 0.3,
    "audio_during_freeze": "mute",
    "reveal": "wipe",
    "shadow": {
      "color": "#FF6432", "alpha": 0.8, "distance": 0.03,
      "direction": "auto", "offset_y": 0.02, "blur": 0, "source": "same"
    }
  },
  "freezes": [
    {
      "time": 3.4,
      "name": "山田 太郎",
      "sfx": "shakin",
      "brush_shape": "hake",
      "title_pos": [0.15, 0.15],
      "title_size": 0.08,
      "title_align": "left",
      "color_source": "brush",
      "strokes": [
        { "width": 0.12, "points": [[0.42, 0.31], [0.44, 0.45], [0.43, 0.60]] }
      ]
    },
    {
      "time": 5.5,
      "name": "自動くん",
      "color_source": "auto",
      "mask_options": { "model": "birefnet-general-lite", "refine": null, "decontaminate": false }
    },
    {
      "time": 7.2,
      "name": "自動＋修正くん",
      "mask": "auto+brush",
      "strokes": [
        { "width": 0.08, "mode": "add", "points": [[0.30, 0.20], [0.34, 0.22]] },
        { "width": 0.06, "mode": "erase", "points": [[0.55, 0.60], [0.58, 0.63]] }
      ]
    },
    {
      "time": 9.0,
      "name": "混在くん（カラーはブラシ・影は自動切り抜き）",
      "color_source": "brush",
      "shadow": { "source": "auto" },
      "strokes": [
        { "width": 0.12, "points": [[0.5, 0.5], [0.52, 0.55]] }
      ]
    },
    {
      "time": 11.0,
      "name": "自分の効果音くん",
      "sfx": { "file": "sfx/rise.mp3", "align": "end_at_landing" },
      "strokes": [
        { "width": 0.12, "points": [[0.5, 0.5], [0.52, 0.55]] }
      ]
    },
    {
      "time": 13.0,
      "name": {
        "lines": [
          { "text": "複数行くん", "size": 1.0, "underline": true },
          { "text": "サブタイトル", "size": 0.55 }
        ]
      },
      "title_font": "assets/fonts/ZenKakuGothicNew-Bold.ttf",
      "title_font_jp": "assets/fonts/ZenKakuGothicNew-Bold.ttf",
      "strokes": [
        { "width": 0.12, "points": [[0.5, 0.5], [0.52, 0.55]] }
      ]
    }
  ],
  "logo": {
    "image": "store_logo.png",
    "at": "last_freeze",
    "background": "auto",
    "duration_sec": 2.2,
    "width_ratio": 0.62,
    "sfx": "impact"
  }
}
```

### ルール

- `points` の座標と `width`（太さ）は動画サイズに対する **0〜1の比率** です。
  `x` は幅、`y` は高さ、`width` は幅を基準にします。
- `style` は全体の既定値です。各 `freezes[]` 要素に同名キーがあれば、そちらが優先されます
  （`reveal_sec` / `slide_sec` / `hold_sec` / `brush_width` / `brush_shape` / `mono_contrast` /
  `background` / `font` / `audio_during_freeze` /
  `title_pos` / `title_size` / `title_align` / `title_font` / `title_font_jp` / `brush_fade_sec` /
  `color_source` / `mask_options` / `reveal` / `shadow` を個別上書き可能。
  `title_bounce` のみ `style` でしか指定できません）。
  1フリーズの流れは「①塗り（`reveal_sec`）→②ズレ（`slide_sec`）→③静止（`hold_sec`）」の
  3段構成です。詳細は[「演出仕様（1フリーズあたり）」](#演出仕様1フリーズあたり)を参照。
- `title_pos`：テロップ（複数行の場合はブロック全体）中心の位置
  （出力サイズに対する **0〜1の比率** `[x, y]`）。
  既定は `[0.5, 0.78]`（従来どおり横中央・高さ78%）。`title_align` が `left`/`right` の場合、
  `x` はそれぞれテロップの左端／右端の位置として扱われる（`center` のときのみ中心）。
  画面端にはみ出す場合はブロックごと自動で内側に寄せられる。
- `title_size`：文字サイズ（出力の高さに対する比率。複数行の場合は各行の基準サイズ）。既定 `0.06`。
- `title_align`：テロップの水平寄せ（複数行の場合は行ごとの揃え位置＝ブロック全体の寄せ）。
  `left` / `center`（既定） / `right`。不明な値は `center` として扱われる。
- `title_font` / `title_font_jp`：テロップに使うフォントファイルのパス（`title_font` は日本語を
  含まない名前、`title_font_jp` は日本語を含む名前に使われ、どちらも未指定なら `font` に
  フォールバックする。複数行の場合は全行を結合したテキストで日本語判定する）。
  `assets/fonts/` には既定のAnton／Noto Sans JPに加え、Noto Serif JP（明朝）・
  Zen Kaku Gothic New（ゴシック）・Dela Gothic One（極太見出し）・Shippori Mincho（明朝）・
  M PLUS Rounded 1c（丸ゴシック）を同梱しており（`make_dummy.py` で取得）、
  エディタからはこれらを全体既定・フリーズ単位のどちらでも選べます。
- `name`：テロップの文字列。**プレーンな文字列**（従来どおり・1行）に加えて、
  複数行・行ごとの文字サイズ／アンダーラインを指定できる**オブジェクト形式**にも対応しています。
  ```json
  "name": {
    "lines": [
      { "text": "山田 太郎", "size": 1.0, "underline": true },
      { "text": "エースストライカー", "size": 0.55 }
    ]
  }
  ```
  各行は `text`（本文）・`size`（`title_size` に対する倍率。既定 `1.0`）・
  `underline`（アンダーラインの有無。既定 `false`）を持ちます。行間の詰め方はシンプルに
  隙間なし（各行の高さをそのまま積み上げる）。複数行はブロック全体を1つのまとまりとして
  `title_pos`/`title_align` で位置・寄せを決め、エディタの位置ドラッグもブロック単位で
  移動します。アンダーラインは各行の文字幅に合わせ、太さは文字サイズの約6%、
  色は文字色（白）と同じです。1行・既定サイズ・アンダーラインなしの場合は
  従来どおりプレーンな文字列のまま出力され、JSONの見た目は変わりません（完全後方互換）。
- `brush_fade_sec`：ブラシ完了時点でまだ先端に残っている「乾いていない白い絵の具」を、
  何秒かけてフェードアウトさせるか（既定 `0.3`）。`0` を指定すると従来どおり
  フェードなし（ブラシ完了と同時に即座に消える）になる。この既定値0.3は、
  実機で「白いブラシ跡がカラー復元後も不透明のまま残って見える」不具合の
  修正として導入したもので、他の新キーと異なり**省略時の見た目が旧バージョンの
  render.pyとは変わる**（旧バージョンは実質 `brush_fade_sec: 0` と同じ挙動だった）。
- `brush_shape` は `round`（丸筆・既定値）/ `hake`（ハケ）/ `marker`（平筆マーカー）/
  `spray`（スプレー）のいずれか。未指定・不明な値は `round` として扱われます
  （後方互換：この項目が無い既存のJSONもそのまま動きます）。
- `color_source`：カラー化に使うマスクの種類。`brush`（既定・従来どおりブラシスタンプ） /
  `auto`（`extract.py` の自動切り抜きをそのまま使う）のいずれか。未指定・不明な値は
  `brush` として扱われます。旧 `mask` キー（`brush` / `auto` / `auto+brush`）も引き続き
  読み込めます（`color_source` が無ければ `mask` を読み替える）。`auto+brush`
  （自動アルファをブラシで修正するハイブリッドモード）は `color_source` には存在せず、
  `mask: "auto+brush"` のままお使いください。
  詳細は次項「[マスクの作り方（`color_source`）と出現アニメ（`reveal`）](#マスクの作り方color_sourceと出現アニメreveal)」参照。
- `mask_options`：`color_source: "auto"` のときの自動切り抜き設定。
  `{"model": "...", "refine": "vitmatte"|null, "decontaminate": true|false}`。
  省略可（省略時は `extract.py` の既定モデル・`refine: null`・`decontaminate: false`）。
  エディタの全体設定に「自動切り抜きのモデル」の選択肢があり、
  **標準（速い）**＝`isnet-general-use`（既定・約3〜9秒/枚）と
  **高精度（遅い・約+1分）**＝`birefnet-portrait`（GitHub Actions実測で約60〜70秒/枚）
  のどちらかを選べます。標準を選んだ場合はJSONに `mask_options` キー自体を出力しません
  （完全後方互換）。
- `reveal`：人物の出現アニメーション。`wipe`（既定・下から上へ`reveal_sec`秒で拭き取るように
  表示） / `fade`（`reveal_sec`秒でフェードイン） / `none`（一瞬で全体を表示し、
  `reveal_sec`秒だけ動かず静止して待つ。`color_source: "auto"` のときのみ意味を持つ） /
  `brush`（`mask: "auto+brush"` のときのみ有効。従来どおりストロークで塗って出す）
  のいずれか。`color_source: "brush"` のときは無関係（常にストローク自体の伸びる
  アニメーションになる）。
- `shadow`：影（フィルム色）演出。**省略時は既定で有効**（`SHADOW_*_DEFAULT` の値。
  詳細は次項）。無効にしたい場合は `"shadow": null` または
  `"shadow": {"enabled": false}` を明示的に指定してください。詳細は
  [「影（`shadow`）演出」](#影shadow演出)を参照。
- `output` を省略した場合は、元動画と同じ解像度・fpsで出力します。
- 未知のキーは無視されます（将来の拡張用に安全に読み飛ばします）。
- `freezes` は `render.py` 内部で `time` 順にソートしてから処理します。JSON内の記述順は問いません。
- 新しいキー（`mono_contrast` / `title_font` /
  `title_font_jp` / `title_bounce` / `title_pos` / `title_size` / `title_align` / `logo` /
  `color_source` / `mask_options` / `reveal`）は
  すべて省略可能で、省略時は旧バージョンの `render.py` と同じ見た目で動きます
  （後方互換。詳細は次項）。`brush_fade_sec` と `shadow` は例外です：
  `brush_fade_sec` は省略時に既定値 `0.3` が使われます（＝白い絵の具がフェードアウトする、
  見た目の不具合修正が既定で有効）。`shadow` は省略時に**既定で有効**になります
  （詳細は[「影（`shadow`）演出」](#影shadow演出)を参照。無効にするには明示的な指定が必要）。
  旧バージョンの `film_offset` / `film_color` / `film_alpha`（`style` のみ・人物ごとの
  上書き不可）は今も読み込めますが、`shadow` キー自体が無く、かつ `film_offset` が
  非ゼロの場合のみ `shadow` の設定へ自動的に読み替えられます（詳細は次項）。
  新規プロジェクトでは `shadow` を使ってください。
  同様に旧 `mask` / `freeze_sec` / `brush_anim_sec`（それぞれ `color_source` /
  `hold_sec` / `reveal_sec` に相当）も引き続き読み込めます。ただし `freeze_sec` は
  **意味が変わっています**：旧バージョンでは「フリーズ全体の長さ」でしたが、新バージョンでは
  「③静止（`hold_sec`）だけの長さ」として読み替えられます（詳細は
  [「演出仕様（1フリーズあたり）」](#演出仕様1フリーズあたり)を参照）。

### 演出仕様（1フリーズあたり）

1フリーズの流れは「**①塗り（`reveal_sec`）→②ズレ（`slide_sec`）→③静止（`hold_sec`）**」の
3段構成です（それぞれ独立した秒数を指定でき、旧バージョンのような全体予算による
比例縮小は行いません）。

0. **フリーズ開始〜①塗り開始（0.3秒固定）**：静止フレームを `background` で処理して表示
   - `mono`：グレースケール化（`mono_contrast` でコントラストを強調。1.0が無効化＝既定値）
   - `dark`：元のカラーを30%の明るさに減光
1. **①塗り（`reveal_sec` 秒。既定0.5秒）**：人物は「元の位置」に出現する。この間、
   `shadow` があってもまだ人物の真下に完全に隠れているため見えない
   - `color_source: "brush"`（既定）：`brush_shape` の筆先画像
     （`assets/brushes/<shape>.png`、白＋アルファ）を軌跡に沿って一定間隔でスタンプする
     「筆先スタンプ方式」。各スタンプは進行方向に合わせて回転する
     （`round`＝柔らかい縁の丸筆／`hake`＝毛筋・縁のギザつき・わずかな不透明度ムラを持つ
     平たいハケ／`marker`＝角の丸い長方形、重なった部分は自然と少し濃くなる／
     `spray`＝円形範囲にランダムな点をまき散らしたスプレー）。複数ストロークがある場合は
     合計時間 `reveal_sec` の中で順番に描く
   - `color_source: "auto"`：`extract.py` の自動切り抜きアルファを `reveal` に従って
     出現させる（`wipe`＝下から上へワイプ、`fade`＝フェードイン、`none`＝一瞬で全体表示して
     `reveal_sec` 秒だけ静止して待つ）。詳細は次項
     「[マスクの作り方（`color_source`）と出現アニメ（`reveal`）](#マスクの作り方color_sourceと出現アニメreveal)」
2. **②ズレ（`shadow` があれば `slide_sec` 秒。既定0.5秒）**：
   人物レイヤー（マスク＋カラー）だけを、元の位置から `distance` ぶんEase-Out Cubic
   （急停止ではなく滑らかに減速するイージング）でずらす。影自体は元の位置に
   固定されたまま動かないため、そのズレ量ぶん影が覗くように「現れる」。
   `shadow` が無ければこの区間は無い（0秒）。詳細は
   [「影（`shadow`）演出」](#影shadow演出)を参照
3. **着地の瞬間（②の終わり＝③の始まり）**：ブラシ先端にまだ残っている白い絵の具を
   `brush_fade_sec`（既定0.3秒）かけてフェードアウトさせ、人物カラーだけが残るようにする
   （`brush_fade_sec: 0` で無効化＝旧バージョンと同じ即時消灯）。同時に名前テロップ
   （位置は `title_pos`・既定 `[0.5, 0.78]`＝横中央/高さ78%、寄せは `title_align`・
   既定 `center`、文字サイズは `title_size`・既定は高さの6%。日本語を含む名前は
   `title_font_jp`、それ以外は `title_font` を使用。どちらも未指定なら `font` に
   フォールバックする。白文字＋薄い黒ドロップシャドウ。`name` を `{"lines":[...]}`
   形式にすると複数行になり、行ごとに文字サイズ倍率・アンダーラインの有無を指定できる
   （行間は詰めずそのまま積み上げる。複数行でも `title_pos`/`title_align` はブロック
   全体を1つの単位として位置・寄せを決める）。`title_pos` が画面端に
   はみ出す位置を指しても、自動で内側に寄せられ画面外に出ない。指定したフォント
   ファイルが見つからない・読み込めない場合は、文字が描かれないまま気づかずに
   完成する事態を避けるため、その場でエラー終了する）を0.15秒でフェード
   インさせ、効果音 `sfx`（`assets/sfx/{sfx}.wav`。`assets/sfx/` 内のファイルを
   差し替えれば、差し替えた音がそのまま使われる。詳細は
   [「効果音を差し替える」](#効果音を差し替える)）を鳴らす。`title_bounce: true` なら
   フェードインと同じ0.15秒の間に130%→100%へ急停止イージングで縮む「はずむ」演出になる
   （このときテロップの外接矩形の中心を基準に拡大縮小する）。`shadow` が無い場合、
   この「着地の瞬間」は①塗り完了と同時（従来どおり）
4. **③静止（`hold_sec` 秒。既定2.0秒）**：カラー化＋テロップを保持する。テロップは
   この着地の瞬間に出現し、③静止の終わりまで表示され続ける
   （`logo.at: "last_freeze"` を指定した場合、時刻が一番遅いフリーズだけこの③静止が
   ラストロゴの表示に必要な長さを満たすよう自動的に延長される。詳細は次項）
5. **影演出のスライドバック（`shadow` があり、かつこのフリーズでラストロゴを表示しない場合、
   0.25秒固定）**：通常再生に戻る直前に、人物を元の位置へ戻し影を隠す（戻さないと
   再生再開時に人物が飛んで見えるため）。ラストロゴを表示するフリーズではこの区間は無い
   （＝通常再生に戻る演出自体がそこには無いため）
6. 通常再生に戻る

`reveal_sec` / `slide_sec` / `hold_sec` は旧バージョンの `brush_anim_sec` / なし
（`shadow.slide_sec` 相当） / `freeze_sec` を改名・統合したものです。旧キーは
今も読み込めます（詳細は[「ルール」](#ルール)の後方互換に関する記述を参照）。

### マスクの作り方（`color_source`）と出現アニメ（`reveal`）

人物の「どこをカラー復元するか」（マスク）の作り方を、フリーズ単位（または `style` で
全体既定）で選べます。ブラシは「マスクの作り方の1つ」および「自動切り抜きの修正手段」
として引き続き使えます。**カラー化に使うマスク（`color_source`）と、影の形に使うマスク
（`shadow.source`）は別々に指定できます**（後述）。

- **`color_source: "brush"`**（既定）：従来どおり、ストロークを筆先スタンプで描いた範囲を
  マスクにする。出現アニメは常にストローク自体が `reveal_sec` かけて伸びていく演出
  （`reveal` は無関係）。
- **`color_source: "auto"`**：`extract.py`（`mask_options` の設定で自動切り抜き）で得た
  アルファをそのままマスクとして使う。ブラシは不要（ストロークがあっても無視される）。
  出現は `reveal` に従う：
  - `reveal: "wipe"`（既定）：下から上へ `reveal_sec` 秒で拭き取るように表示
  - `reveal: "fade"`：`reveal_sec` 秒でフェードイン
  - `reveal: "none"`：一瞬で全体を表示し、`reveal_sec` 秒だけ動かず静止して待つ
    （＝先に色が付いた状態でしばらく間を置いてから②ズレへ進む）
- **`mask: "auto+brush"`**（旧キー・`color_source` には存在しない後方互換のハイブリッド
  モード）：自動アルファを、ブラシストロークで修正してからマスクにする。
  ストロークに `"mode": "add"`（省略時の既定。塗った範囲のアルファを1相当に塗り足す）または
  `"mode": "erase"`（塗った範囲のアルファを0相当に削る）を持たせる。修正はフリーズごとに
  1回だけ計算する静的な補正で、時間変化（出現アニメ）は別途 `reveal` が担当する：
  - `reveal: "wipe"`（既定）／`"fade"`：修正後のアルファ全体を、下から上へのワイプ
    （0.4秒固定）／フェードイン（0.3秒固定）で出現させる（ストロークの形はマスクの
    "どこを直したか" にのみ使われ、出現の順序には関係しない）
  - `reveal: "brush"`：従来のブラシ演出と同じく、ストロークが伸びる進み具合（`reveal_sec`）
    に沿って、なぞった範囲から順に修正後のアルファを出現させる（`add`/`erase`どちらの
    ストロークも「なぞった」範囲として出現タイミングの判定に使われる）
- 自動切り抜きの結果（アルファ）は `cache/<動画のファイル名>_<フリーズ時刻>.npz` に
  キャッシュされ、同じ動画・同じフリーズ時刻での再レンダリング時は再計算されません
  （`cache/` はプロジェクトのカレントディレクトリ基準。gitignore対象）。
- **エディタ内での確認（サーバー確認プレビュー）**：`color_source`/切り抜き方法が
  `auto` または `auto+brush` のフリーズでは、編集画面に「🔍 切り抜き結果を確認」
  ボタンが表示されます。以前はスマホ上での粗いヒューリスティック推定（簡易プレビュー）
  でしたが、現在は**本番と同じモデル（`extract.py`／rembg）でサーバー側（GitHub Actions）
  に実際に切り抜かせて**結果を表示します：
  1. 押すと即座にモーダルが開き、静止フレームを長辺約1280pxのJPEGに縮小して
     `spotlight-jobs` の一時ブランチ（`job-confirm-YYYYMMDD-HHMMSS-<乱数>`）へ
     Git Data API経由でコミットし、動画のフルレンダリングは行わない軽量ワークフロー
     `extract.yml` を起動します。進捗バーと「起動を待っています…」「サーバーで
     切り抜き中…（経過約N秒）」といった状況表示が進み、**所要は概ね1〜2分程度**です。
  2. 完了すると、サーバー側が生成したチェッカー背景合成のプレビュー画像
     （`preview.png`）を取得してモーダルに表示します。透明（背景）部分がチェッカー柄で
     見えるため、本番と同じ精度で切り抜き結果を確認できます。
     **実機検証の結果、非公開リポジトリのRelease アセットは
     `GET /releases/assets/{id}`（`Accept: application/octet-stream`）で取得しても、
     実体が `release-assets.githubusercontent.com` への302リダイレクトで返され、
     そのリダイレクト先がCORS非対応（`Access-Control-Allow-Origin`を返さない）ため、
     ブラウザの`fetch`ではプリフライトの時点で必ず失敗する（headless Chromiumで
     再現・確認済み）ことが判明しました。**そのため `preview.png` とサーバー確認済み
     アルファ（`.npz`）は、`extract.yml` がRelease（存在確認・手動閲覧用）だけでなく
     `job-confirm-<tag>` ブランチにもコミットするようにし、エディタ側は
     Contents API（`GET /repos/{owner}/{repo}/contents/{path}?ref={branch}`。
     リダイレクトを伴わずAPI上で完結するため CORS 対応）でその中身を取得します
     （動画・project.json のアップロードに Git Data API を使っているのと同じ理由）。
  3. 結果を待たずに編集を続けたい場合は「確認せずに進む」でいつでも中断できます
     （進捗バー横に常時表示）。GitHub Actions側の実行自体は止まりませんが、
     エディタ側はその結果を待たずにモーダルを閉じます。
  4. **確認で得たアルファは自動的に再利用されます**：確認が完了すると、そのフリーズの
     動画ファイル名・時刻・自動切り抜きモデルとともに確認結果（タグ名）を記録しておき、
     続けて「🎬 動画を作る」を実行した際、動画ファイル名・フリーズ時刻・モデルの
     いずれも変わっていなければ、確認時に生成されたアルファキャッシュ
     （`video_<フリーズ時刻>.npz`）をそのまま本番ジョブブランチの `cache/` 配下へ
     同梱します。`render.py` 側の変更は一切不要です（`get_or_extract_alpha` の
     既存のキャッシュヒット判定が、本番の出力解像度に一致する `.npz` をそのまま
     採用する仕組みをそのまま利用しています）。動画ファイル名・時刻・モデルのいずれか
     一つでもズレていれば再利用は行わず、通常どおり本番レンダリング時にモデルが
     再実行されます（安全側フォールバック）。
     確認結果は本番タグと同じ `job-*` 命名規則を使っているため、`spotlight-jobs` 側の
     7日後クリーンアップ（後述。Release だけでなく `job-confirm-*` ブランチも一緒に
     削除します）でも自動的に削除対象になります（確認してからあまり
     日を置かずに動画を作ることを想定しています）。
  - 表示中のフリーズに常に追従します：プレビューを開いたまま「完了」してフリーズ編集を
    抜けた場合でも、モーダルは自動的に閉じます（進行中のサーバー確認は打ち切られ、
    アルファは記録されません）。別のフリーズを新たに編集して改めてボタンを押すと、
    その時点の静止フレームで必ず新しい確認が走るため、古いフリーズの結果を誤って
    見せ続けることはありません。
  - **表示は画面幅いっぱいの全画面オーバーレイ**（黒背景・映像は幅100%で縦は収まる
    範囲まで拡大表示）です。「▶ プレビュー」の再生も同様に全画面表示になります
    （実機での「プレビューが小さすぎる」という指摘への対応。小さなcanvasのままの
    表示は残していません）。閉じ方は右上の大きな「✕」ボタン（44px角以上）・Escキーの
    いずれでも可能です（全画面化に伴い、背景タップでの close は廃止しました。
    背景に露出したピクセルが無くなり判定が成立しなくなったため）。閉じると編集画面の
    スクロール・タッチ操作がすぐに復帰します。
  - **タブのバックグラウンド化・再読み込みへの耐性**：確認用Releaseを作成した直後、
    その情報（タグ・時刻・モデル・動画ファイル名）を `localStorage` に記録しておき、
    同じフリーズを再び開いた時、まずそのReleaseを直接確認します。iOSでタブが
    バックグラウンドに回ってJavaScriptの処理が中断され、サーバー側の確認自体は
    成功していてもエディタ側が結果を取得できないまま待ち続ける、という状況が
    発生しても、次に開いた時点で `preview.png` が既にあれば再dispatchせずそのまま
    表示します（無ければ通常どおり新しく確認をやり直します）。
  - **失敗時は無言にならない**：取得に失敗した場合は、エラーメッセージとともに
    該当のGitHub Actions実行ページへのリンク（「🔍 Actionsページで確認」）を表示します。
    `extract.yml`／`render.yml` 側にも「確認用Releaseが無ければ自動作成する」保険の
    ステップを追加しており、エディタ側のRelease作成とdispatchの間にタブが閉じられる
    等の想定外の順序になった場合でも、アップロード自体は失敗しません。
  - **メモリ対策**：iPhone実機でページがクラッシュする不具合の対策として、プレビュー
    表示用のcanvasのバッキングストア解像度は端末の実DPRを最大2倍相当に留め
    （全画面表示は解像度が大きく上がるため、フル DPR のままだと描画負荷が
    増えすぎる）、使い終わり次第 `width`/`height` を0にして即座にバッキングストアを
    解放します（参照を外すだけだとiOS SafariのGCが遅れ、繰り返し開閉するとメモリを
    使い切ってページごと落ちることがあるため）。
- **`color_source` と `shadow.source` の混在**：例えば「カラー化はブラシ、影は自動切り抜き」
  （`color_source: "brush"`、`shadow: {"source": "auto"}`）のような組み合わせも可能です。
  この場合、自動切り抜きはそのフリーズだけ実行されます（他のフリーズが
  `color_source: "brush"` のままなら、そちらでは自動切り抜きは走りません）。
  詳細は次項「[影（`shadow`）演出」](#影shadow演出)の `source` を参照。
- `color_source: "auto"` を使うプロジェクトは、`extract.py` の依存
  （`requirements-extract.txt`。詳細は[「自動切り抜き（`extract.py`）」](#自動切り抜きextractpy)）
  が別途必要です。`color_source: "brush"`（既定）のみのプロジェクトはこの依存が無くても
  動作します。

### 影（`shadow`）演出

人物アルファ（`color_source` が `brush` / `auto` のいずれでも）がある場合に、
人物が少しスライドして自分の「元の位置」に影を覗かせる演出です。
以前の「フィルム色縁取り」（旧 `film_offset` / `film_color` / `film_alpha`）と
統合されており、影レイヤーの実体はそのフィルム色のベタ塗りです（`blur: 0` が既定の
ため、既定では従来どおりのベタ塗りに見えます）。

**`shadow` キー省略時は既定で有効**（`SHADOW_*_DEFAULT` の値。下記JSON例と同じ内容）
になります。無効にしたい場合は、次のいずれかを明示的に指定してください：

- `"shadow": null`
- `"shadow": {"enabled": false}`

（`style.shadow` に `{"enabled": true, ...}` のように明示的に `enabled: true` を
書いても、他のキーを省略すれば既定値で埋められます。`enabled` キー自体を持たない
`"shadow": {...}` も同様に有効として扱われます。）

```json
"shadow": {
  "color": "#FF6432", "alpha": 0.8, "distance": 0.03,
  "direction": "auto", "offset_y": 0.02, "blur": 0, "source": "same"
}
```

- `color`：影の色（`"#RRGGBB"`。既定 `"#FF6432"`）。エディタには6つの色プリセット
  （白 `#FFFFFF` / 黒 `#000000` / フィルムオレンジ `#FF6432` / シアン `#32C8FF` /
  マゼンタ `#FF32A0` / イエロー `#FFD232`）を用意していますが、JSON上は任意の
  `#RRGGBB` を指定できます。
- `alpha`：影の濃さ（不透明度、0〜1。既定 `0.8`）
- `distance`：人物がスライドする距離。出力幅に対する比率（既定 `0.03`）。
  影自体は動かず「人物の元の位置」に固定されたままなので、このスライド量ぶんだけ
  影が覗いて見える
- `direction`：スライドする向き。`"auto"`（既定）／`"left"`／`"right"`。
  `"auto"` の場合、影の形に使うマスク（`source` 参照）のX重心を計算し、画面中心より
  右なら右へ・左なら左へ自動判定する（中心±5%以内は判定があいまいなため、既定の
  `"right"` になる）
- `offset_y`：Y方向のズレ量。出力幅に対する比率、下方向がプラス（既定 `0.02`）。
  X方向とは違い自動判定は無く、常にこの比率がそのまま使われる
- `blur`：影のぼかし量。出力幅に対する比率（既定 `0`＝ぼかし無し）
- `source`：**影の形に使うマスクの種類**。`"same"`（既定・`color_source` と同じマスクを
  使う） / `"brush"`（色付けの方式に関係なく、このフリーズの全ストロークを使う） /
  `"auto"`（色付けの方式に関係なく、自動切り抜きのアルファを使う。キャッシュ済みで
  なければこのフリーズだけ切り抜きを実行する）。例えば `color_source: "brush"` と
  `source: "auto"` を組み合わせると、「カラー化はブラシで塗るが、影の形は自動切り抜きの
  シルエットに沿う」という演出になります。
- 人物がスライドインする時間（②ズレ）は、`style`/フリーズの `slide_sec`
  （既定 `0.5`。旧 `shadow.slide_sec` を統合・改名したもの。互換のため
  `shadow.slide_sec` を明示した場合はそちらが優先されます）で指定します。
  人物の①塗りが完了した直後から、この時間かけて Ease-Out Cubic
  （急停止ではなく最後まで滑らかに減速するイージング。以前はEase-Out Expoで
  終盤ほぼ完全に停止していた）イージングで0〜`distance` までスライドし、
  着地の瞬間にテロップ（`title_bounce` があればバウンス）と効果音が発火する。
  ③静止が終わり通常再生に戻る直前には、0.25秒（固定。以前は0.1秒）かけて
  人物を元の位置へ戻し、影を隠す（詳細は[「演出仕様（1フリーズあたり）」](#演出仕様1フリーズあたり)参照）
- レイヤー順は **背景 → 影（人物の元の位置。動かない） → 人物（スライド後の位置）
  → テロップ → ロゴ**
- 旧バージョンの `film_offset` / `film_color` / `film_alpha` は、`shadow` キー自体が
  無く、かつ `film_offset` が非ゼロなら次のように `shadow` へ自動的に読み替えられます
  （後方互換）：
  `color` ← `film_color`、`alpha` ← `film_alpha`、`distance` ← `|film_offset.x|`、
  `direction` ← `film_offset.x >= 0 ? "right" : "left"`、`offset_y` ← `film_offset.y`、
  `blur` ← `0`、`slide_sec` ← 既定値 `0.5`、`source` ← `"same"`。ただし旧バージョンの
  縁取りは静止した演出だったのに対し、読み替え後は新しいスライドアニメが付くため、
  見た目は「フィルム色そのもの」から「スライドして現れる影」に変わります。
- **確認用のログ**：`render.py` はフリーズごとに `影=有効/無効`・`方向=left/right`・
  `マスクX中心=0.xx`（影の形に使うマスクのX重心、画面幅に対する比率。0が左端、
  1が右端）・`source=same/brush/auto`（影に使ったマスクの種類）を標準出力に出します
  （GitHub Actionsのログでも確認できます）。実機で影が出ない・向きが想定と違う場合は、
  まずこのログで「影が無効になっていないか」「方向が意図どおりか」を確認してください。

### ラストロゴ（`logo`）演出「画面いっぱい→縮小して定位置」＋光彩スイープ

- `logo.image`：ロゴ画像（PNG推奨。透過対応、不透明なJPEG/PNGも可）のパス。
  省略するとロゴ演出は無効。読み込んだ画像は、内容部分（アルファチャンネルに
  実際の透明部分があればそのしきい値、無ければ四隅の背景色との画素差分）を
  自動検出してクロップしてから使われます。実行ログに元サイズ→クロップ後
  サイズが出るので、実際に効いているか確認できます（例:
  `ロゴ自動クロップ: 640x640 → 288x288（余白を検出して切り抜き）`）。
  背景色との差分判定は、JPEG圧縮ノイズ等で背景の1画素だけが外れ値になっても
  誤検出しないよう、四隅は1点でなく小さなパッチで平均し、「内容」判定マスクにも
  ノイズ除去（モルフォロジー・オープニング）をかけてから外接矩形を求めています。
- `logo.at`：`"end"`（動画の末尾に新しい区間として追加。既定値）または
  `"last_freeze"`（時刻が一番遅いフリーズの保持中に表示）
- `logo.background`：ロゴの背後に敷く画面
  - `"auto"`（既定値）：ロゴ画像の四隅の画素を平均した色で画面全体を塗りつぶす
    （黒背景のロゴ画像なら画面全体が同じ黒になる）
  - `"video"`：背景色を敷かず、映像／静止フレームの上にそのままロゴを重ねる
  - `"#RRGGBB"`：指定した色で画面全体を塗りつぶす
  - `at: "end"` は動画末尾に背景色（または `"video"` なら映像最終フレーム）の画面を新しい
    区間として追加します。`at: "last_freeze"` は、背景色を敷くモード（`"auto"` / 色指定）の
    場合のみ、静止フレームから背景色へ0.15秒でクロスフェードしてから始まります
    （`"video"` の場合はクロスフェードせず、静止フレームの上に直接重ねます）。
- `logo.duration_sec`：**着地してからの表示時間**（秒）。既定 2.2 秒
- `logo.width_ratio`：**着地後**のロゴの基準表示幅（出力幅に対する比率、等倍=100%
  のとき）。既定 `0.62`（以前は0.55、その前は0.4固定）
- `logo.sfx`：着地の瞬間に鳴らす効果音。既定は `impact`（低く重いインパクト音。
  `don` より尾が長い。詳細は[「効果音を差し替える」](#効果音を差し替える)）
- `logo.sfx_tail`：`logo.sfx` に、簡易的な減衰ディレイ（コムフィルタ風のエコーを
  0.6秒かけて重ねる簡易リバーブ近似）を付けて低音の余韻を残すか。既定 `true`。
  `false` にすると効果音ファイルをそのまま（テールなしで）鳴らす
- タイムライン（着地開始 t=0 として、既定値の場合）
  1. `0.00-0.60s` **着地**：クロップ後のロゴを**画面幅の105%（＝画面いっぱいから
     わずかにはみ出す状態）**で表示を始め、`width_ratio`（既定62%）まで縮小して
     着地する。**アンティシペーション＋セトル**でアニメーションさせ、ゆっくり
     入って（0〜60%はEase-Inで加速しながら）一気に縮み、着地後サイズを2%だけ
     沈み込んでから、残り40%でEase-Outしながら定位置サイズへ戻る（不透明度は
     0→1へ、沈み込み区間の終わりまでに先行して完全不透明になる）。単純な
     Ease-Out Quartより重厚感・弾力が出るため、`--preview` とダミーレンダリングで
     見比べて採用した
  2. `0.60-0.65s` **白フラッシュ**：着地の瞬間に効果音を鳴らし、画面全体を白く
     screen合成で0.05秒フラッシュさせる（強さは `flash_strength`）
  3. `0.60-0.85s` **画面の揺れ**：着地の瞬間、画面全体をごくわずかに揺らす
     （振幅は出力幅の`shake_amplitude`比率、3周期の減衰振動で`shake_sec`秒かけて
     収まる。画面端は黒帯にならないよう縁を伸ばして埋める）
  4. `0.95-1.65s` **光彩スイープ**：着地完了から`sweep_start_sec`秒後に開始し
     （＝着地直後にすぐ始めず「間」を置く）、ロゴの左上→右下へ45度の白い
     ハイライト帯（透明→白→透明の線形グラデーション、幅はロゴ幅の25%）を
     `sweep_sec`秒かけて走らせる。ロゴ画像の輝度をマスクにしてscreen合成するため、
     黒背景の不透明PNGでも「明るい部分（文字など）」だけに光が乗る
  5. `1.65s-終了` **保持**：ゆっくり103%まで拡大しながら維持し、終了直前の
     `fade_sec`秒（既定0.4秒）で背景色（`"video"` の場合は黒）へ暗転する
  6. 合計の表示区間は「着地(`landing_sec`) + `duration_sec`」秒
- 上記のうち以下のパラメータは `logo{}` 配下で個別に上書きできます（それ以外は固定値）：

  | キー | 既定値 | 意味 |
  | --- | --- | --- |
  | `scale_from` | `1.05 ÷ width_ratio`（既定値どうしでは約`1.69`） | 着地アニメ開始時のスケール。未指定なら「画面幅の105%」÷`width_ratio`から自動算出され、`width_ratio`をいくつに変えても開始時は常に画面幅105%（画面いっぱい）から揃う。直接指定すればそちらが優先される |
  | `landing_sec` | `0.6` | 着地（アンティシペーション＋セトル）にかかる時間（以前は0.45秒、その前は0.15秒） |
  | `shake_sec` | `0.25` | 着地の瞬間の画面の揺れが収まるまでの時間 |
  | `shake_amplitude` | `0.004` | 画面の揺れの振幅（出力幅に対する比率） |
  | `sweep_start_sec` | `0.35` | 着地完了から光彩スイープ開始までの間隔 |
  | `sweep_sec` | `0.70` | 光彩スイープにかかる時間 |
  | `flash_strength` | `0.35` | 白フラッシュの強さ（0〜1） |
  | `duration_sec` | `2.2` | 着地からの表示時間 |
  | `fade_sec` | `0.4` | 終了直前、背景色へ暗転する時間 |
  | `sfx_tail` | `true` | `logo.sfx` に減衰ディレイのテールを付けるか |
  | `width_ratio` | `0.62` | 着地後のロゴの基準表示幅（出力幅に対する比率。以前は0.55、その前は0.4固定） |

  ※ `--preview` にロゴ設定のあるプロジェクトを渡すと、影のスライド前後2枚に加えて
  自動クロップ後のロゴ単体（アルファ付きPNG）も `<出力名>_logo.png` として書き出され、
  クロップが実際に効いているかを画像で確認できます。

### 効果音を差し替える

`assets/sfx/shakin.wav` / `assets/sfx/don.wav` / `assets/sfx/impact.wav` は、
`make_dummy.py` が最初に生成する**仮の合成音**（「映画予告編ふう」の質感を意識した
もの。`shakin` は複数の非整数倍音を重ねた金属的な高域シマー＋短い残響、`don` は
40〜60Hzのサブベース＋アタックの打撃ノイズ＋1.2秒以上の減衰リバーブ尾、`impact` は
`don` よりさらに低く重く、尾も長い、ラストロゴ着地用のSE）です。いずれもピークで
歪まないようリミッター的に正規化して仕上げています。

フリー効果音サイト（例: [効果音ラボ](https://soundeffect-lab.info/)、
[OtoLogic](https://otologic.jp/)、[PIXABAY Sound Effects](https://pixabay.com/sound-effects/)
など、利用規約を確認の上で商用可否に注意して選んでください）から気に入った音を
ダウンロードし、同じファイル名（`shakin.wav` / `don.wav` / `impact.wav`。他の名前で
使う場合はプロジェクトJSONの `sfx` / `logo.sfx` にその名前を指定）で `assets/sfx/` に
上書き保存するだけで、以後 `render.py` はその音をそのまま使います
（特別な設定は不要。ファイルの存在確認だけで優先的に読み込む仕組みのため）。
`python make_dummy.py` を再実行しても、既に存在する `assets/sfx/*.wav` は
上書きされません（差し替えたファイルが仮の合成音に戻ってしまうことはありません）。

### 自分のファイルを効果音として使う（`sfx` のオブジェクト形式）

`sfx` / `logo.sfx` は、`assets/sfx/` 同梱のプリセット名（文字列。上記）に加えて、
以下のオブジェクト形式も受け付けます。エディタでは「効果音ライブラリ」に登録した
mp3/wavファイルを選ぶとこの形式が自動的に書き出されます。

```json
"sfx": { "file": "sfx/rise.mp3", "align": "end_at_landing" }
```

- `file`：音声ファイルへの相対パス（mp3/wavなど、ffmpegが対応する形式）。
  カレントディレクトリ→プロジェクトJSONと同じ場所→`render.py`と同じ場所、の順で
  解決します（`logo.image` と同じ方針）。job実行時は、エディタが `sfx/` 配下に
  project.json・動画と一緒にコミットするので、そのまま解決できます。
- `align`：効果音の再生開始位置を、着地の瞬間からどう逆算するか。
  - `sfx`（フリーズ用）: `start_at_landing`（既定・従来どおり、着地の瞬間に再生開始） /
    `end_at_landing`（音の終わり——末尾の-50dB以下の無音を除いた位置——が着地に一致する
    よう、再生開始位置を前にずらす）
  - `logo.sfx`（ロゴ用）: `start_at_landing`（既定） /
    `peak_at_landing`（音声ファイルの振幅ピーク位置を解析し、その瞬間が着地
    ——ロゴが最小スケールに達する瞬間——に一致するよう再生開始位置を前にずらす）
  逆算した結果、動画の先頭より前になる場合は先頭にクランプされます。
- **音量正規化**：オブジェクト形式（ユーザー提供ファイル）は、既存の「元動画より
  約3dB大きく」というRMS基準の音量バランス調整に乗せる前に、まずピークを-1dBFSに
  正規化します（プリセットは既にバランス調整済みの前提のため、この正規化は
  オブジェクト形式のときだけ適用されます）。

### 音声仕様

- 元動画の音声は、フリーズ区間を挿入した分だけ後ろへずれます。
- フリーズ中は `audio_during_freeze` が `mute` なら無音、`keep` なら直前0.5秒をループします。
- 効果音は①塗り完了の瞬間（`shadow` があれば②ズレの着地の瞬間）にミックスされます。
- `logo.sfx` は、ロゴが着地する瞬間（`landing_sec` 経過後。`at: "last_freeze"` かつ背景色
  クロスフェードがある場合はそのぶんも加算した後）に鳴ります。`logo.at: "end"` の場合、
  動画末尾に無音区間（着地+表示ぶん）を追加した上で、その中の着地位置で鳴ります。
  `logo.sfx_tail`（既定 `true`）が有効な場合、この効果音に0.6秒の減衰ディレイの
  テールが付き、低音の余韻が残ります。
- **音量バランス**：各効果音（`sfx` / `logo.sfx`）は、元動画の音声（RMS基準）より
  約3dB大きく聞こえるよう自動的に正規化されます（元動画がほぼ無音の場合は、
  測定不能な基準に引きずられないよう既定のラウドネスを使います）。最終ミックス後は
  単純な `clip` ではなく、しきい値を超えた部分だけtanhで滑らかに丸め込む簡易
  リミッターを通すため、音量アップしてもピークで歪みません。
- 入力動画が可変フレームレート(VFR)（iPhoneのスクリーン録画などで典型的。負荷でフレーム
  間隔がばらつき、`ffprobe` の `r_frame_rate` と `avg_frame_rate` が大きく食い違う）と
  判定された場合、`render.py` は処理前に自動でffmpegを使い固定フレームレート(CFR)へ
  正規化してから以降の処理を行います（「フレーム番号 = 時刻 × fps」という前提で
  フリーズ挿入位置・音声の切り貼り位置を計算しているため、VFRのままだと映像と音声が
  徐々にズレてしまうのを防ぐため）。正規化が行われた場合はログに警告として表示されます。

## 実装メモ

- フレームは1枚ずつ読み書きし、全フレームをメモリに保持しません。
- 映像は `cv2.VideoWriter` を使わず、ffmpegへ rawvideo をパイプして
  `libx264 / yuv420p / crf 20` でエンコードします（Colabで確実にH.264 MP4を出すため）。
- 縦動画の回転メタデータを考慮し、表示上の向きでフレームを処理します。
- 音声の切り貼り・ミックスは numpy で行い、最後に ffmpeg で映像と結合します。
  フレーム数→サンプル数の変換は `frames_to_samples()` に一本化し、境界計算の
  ズレ（無音の細切れなど）が起きないようにしています。また音声デコード時は
  ffmpegの `aresample=async=1` で、実機録画に時々見られる音声タイムスタンプの
  小さな不連続を吸収します。
- `index.html`（エディタ）側は、一部のiOS Safariで `HTMLVideoElement.videoWidth/videoHeight` が
  回転メタデータを反映しない既知の不具合があるため、選択した動画ファイル自体（MP4/MOVの
  `tkhd` 変換行列）をJSで直接読み取って補正する（`parseMp4DisplayRotation` /
  `resolveEffectiveVideoDims`）。playerCanvasのサイズ計算・レターボックス・タッチ座標の
  正規化はすべてこの補正済みの値を使う。

## 動作確認

```bash
python make_dummy.py   # assets/brushes/*.png（筆先スタンプ画像）もここで生成される
python render.py examples/sample.json --out examples/out.mp4
ffprobe examples/out.mp4   # 長さ・音声トラックを確認
python render.py examples/sample.json --preview examples/preview.png   # brush_shapeはJSON側で指定
# --preview <path> は <path> から拡張子を除いた名前を基準に、
# <name>_before.png（影のスライド前＝影が隠れている）と
# <name>_after.png（スライド後＝影が見えている）の2枚を出力する
# （shadowが無効なプロジェクトでは2枚とも同じ内容になる）

node tests/editor_logic.test.js   # index.html のJSON生成・ジョブ連携ロジックのユニットテスト（依存なし）

python tests/render_quality.test.py   # render.pyの品質回帰テスト（音声連続性・VFR正規化・
                                       # テロップ描画・ブラシの白フェード・フォント読み込み
                                       # 失敗時のエラー終了・shadow・mask=auto/auto+brush・
                                       # ①②③の3段時間・color_source/shadow.sourceの混在・
                                       # ロゴの自動クロップ/着地カーブ/sfx_tail・旧キーのみの
                                       # JSONの後方互換。要ffmpeg/numpy、初回はダミーVFR動画を
                                       # 生成。mask=auto系・混在検証はrembgが無ければ自動でスキップされる）

# 任意：extract.py単体の検証（要 pip install -r requirements-extract.txt）
python extract.py examples/extract_test_silhouette.png --out /tmp/extract_out/
python -c "import numpy as np; from PIL import Image; \
  a = np.array(Image.open('/tmp/extract_out/alpha.png')); \
  print('distinct alpha values:', len(np.unique(a)))"   # 0/255の二値ではなく連続値になっていることを確認

# 任意：UI回帰テスト（playwright-coreとChromiumが必要）
npm install --no-save playwright-core
python3 -m http.server 8794 &   # index.html をどこかで配信しておく
node tests/player_ui.playwright.test.mjs
node tests/freeze_black_screen.playwright.test.mjs
node tests/resize_scroll_black_screen.playwright.test.mjs
node tests/portrait_landscape_freezes.playwright.test.mjs # 縦動画・横動画それぞれでフリーズ3件追加→JSON件数一致を確認
node tests/brush_shape.playwright.test.mjs      # ブラシ形状選択・筆先スタンプ描画のE2E
node tests/film_logo_bounce.playwright.test.mjs # freeze_sec・テロップバウンス・ラストロゴ設定のE2E
node tests/title_pos_drag.playwright.test.mjs   # テロップのドラッグ移動・サイズ/寄せ変更のE2E
node tests/title_lines_editor.playwright.test.mjs # テロップ複数行入力（行の追加/削除・サイズ/アンダーライン・フォント選択）のE2E
node tests/mask_shadow_ui.playwright.test.mjs   # 切り抜き方法セレクタ・足す/消すトグル・影（スライド演出含む）/revealのE2E、
                                                 # サーバー確認プレビュー（extract.ymlのdispatch・進捗表示・
                                                 # 「確認せずに進む」・confirmedAlphaの記録、GitHub APIはモック）
node tests/make_video_job.playwright.test.mjs   # 「動画を作る」ボタンのE2E（GitHub APIはモック、実際の通信はしない）。
                                                 # サーバー確認済みアルファがあるフリーズをcache/*.npzとして
                                                 # 本番ジョブブランチへ同梱する再利用ロジックの検証も含む
node tests/startup_robustness.playwright.test.mjs # 巨大/壊れた自動保存データ・起動失敗時の復旧UIのE2E

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
