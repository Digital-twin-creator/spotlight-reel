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
│   ├── sfx/                # shakin.wav, don.wav（差し替え可。後述「効果音を差し替える」参照）
│   └── brushes/            # round/hake/marker/spray.png（筆先スタンプ画像、白＋アルファ）
├── examples/
│   ├── sample.json         # make_dummy.py が生成するサンプル
│   ├── dummy_input.mp4     # ダミー動画（縦 1080x1920）
│   ├── dummy_input_landscape.mp4 # ダミー動画（横 1920x1080。縦横回帰テスト用）
│   ├── dummy_input_vfr.mp4 # ダミー動画（縦・可変フレームレート。VFR正規化の回帰テスト用）
│   └── store_logo.png      # make_dummy.py が生成するダミーのラストロゴ画像
├── tests/
│   ├── editor_logic.test.js                    # index.html のJSON生成・ジョブ連携ロジックのユニットテスト（依存なし）
│   ├── render_quality.test.py                  # render.pyの品質回帰テスト（音声連続性・VFR正規化・テロップ描画・
│   │                                            #   ブラシの白フェード・フォント読み込み失敗時のエラー終了。要ffmpeg/numpy）
│   ├── player_ui.playwright.test.mjs           # 再生/停止・シークのUI回帰テスト（任意、要playwright-core）
│   ├── freeze_black_screen.playwright.test.mjs # フリーズ追加時の黒画面回帰テスト（任意、要playwright-core）
│   ├── resize_scroll_black_screen.playwright.test.mjs # スクロール/リサイズ時の黒画面回帰テスト（任意、要playwright-core）
│   ├── brush_shape.playwright.test.mjs         # ブラシ形状選択・筆先スタンプ描画の回帰テスト（任意、要playwright-core）
│   ├── film_logo_bounce.playwright.test.mjs    # フィルム縁取り・テロップバウンス・ラストロゴ設定の回帰テスト（任意、要playwright-core）
│   ├── portrait_landscape_freezes.playwright.test.mjs # 縦横動画それぞれでフリーズ複数追加→JSON書き出しの回帰テスト（任意、要playwright-core）
│   ├── title_pos_drag.playwright.test.mjs      # テロップのドラッグ移動・サイズ/寄せ変更の回帰テスト（任意、要playwright-core）
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
   名前を入力すると映像の上にテロップが仮表示され、それを指でドラッグすると
   このフリーズだけの表示位置（`title_pos`）を指定できる（テロップの上をタッチした時だけ
   移動モードになるので、ブラシ描画とは干渉しない。画面外に出ないよう端で止まる）。
   大きさは小さなスライダー、寄せ（左/中央/右）はセレクトで調整でき、どちらも
   「▶ プレビュー」の簡易再生に反映される。
   「▶ プレビュー」で、フィルム縁取り・テロップのバウンス・（ラストフリーズなら）ロゴの
   着地とスイープ演出も含めた雰囲気をその場で確認できる。
4. 「全体設定」でフリーズの長さ・フィルム縁取り（ズレ量・色プリセット・不透明度）・
   テロップのバウンスON/OFFなどを、「ラストロゴ」で動画末尾やラストフリーズに重ねる
   ロゴ画像・背景（ロゴに合わせる／動画に重ねる／色指定）・表示時間・タイミングを、
   それぞれ必要に応じて調整できる。
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
    "title_pos": [0.5, 0.78],
    "title_size": 0.06,
    "title_align": "center",
    "brush_fade_sec": 0.3,
    "audio_during_freeze": "mute"
  },
  "freezes": [
    {
      "time": 3.4,
      "name": "山田 太郎",
      "sfx": "shakin",
      "brush_shape": "hake",
      "film_color": "#00C8FF",
      "title_pos": [0.15, 0.15],
      "title_size": 0.08,
      "title_align": "left",
      "strokes": [
        { "width": 0.12, "points": [[0.42, 0.31], [0.44, 0.45], [0.43, 0.60]] }
      ]
    }
  ],
  "logo": {
    "image": "store_logo.png",
    "at": "last_freeze",
    "background": "auto",
    "duration_sec": 1.2,
    "sfx": "don"
  }
}
```

### ルール

- `points` の座標と `width`（太さ）は動画サイズに対する **0〜1の比率** です。
  `x` は幅、`y` は高さ、`width` は幅を基準にします。
- `style` は全体の既定値です。各 `freezes[]` 要素に同名キーがあれば、そちらが優先されます
  （`freeze_sec` / `brush_anim_sec` / `brush_width` / `brush_shape` / `mono_contrast` /
  `film_color` / `film_alpha` / `background` / `font` / `audio_during_freeze` /
  `title_pos` / `title_size` / `title_align` / `brush_fade_sec` を個別上書き可能。
  `film_offset` / `title_font` / `title_font_jp` / `title_bounce` は `style` のみで指定します）。
- `title_pos`：テロップ中心の位置（出力サイズに対する **0〜1の比率** `[x, y]`）。
  既定は `[0.5, 0.78]`（従来どおり横中央・高さ78%）。`title_align` が `left`/`right` の場合、
  `x` はそれぞれテロップの左端／右端の位置として扱われる（`center` のときのみ中心）。
  画面端にはみ出す場合は自動で内側に寄せられる。
- `title_size`：文字サイズ（出力の高さに対する比率）。既定 `0.06`。
- `title_align`：テロップの水平寄せ。`left` / `center`（既定） / `right`。
  不明な値は `center` として扱われる。
- `brush_fade_sec`：ブラシ完了時点でまだ先端に残っている「乾いていない白い絵の具」を、
  何秒かけてフェードアウトさせるか（既定 `0.3`）。`0` を指定すると従来どおり
  フェードなし（ブラシ完了と同時に即座に消える）になる。この既定値0.3は、
  実機で「白いブラシ跡がカラー復元後も不透明のまま残って見える」不具合の
  修正として導入したもので、他の新キーと異なり**省略時の見た目が旧バージョンの
  render.pyとは変わる**（旧バージョンは実質 `brush_fade_sec: 0` と同じ挙動だった）。
- `brush_shape` は `round`（丸筆・既定値）/ `hake`（ハケ）/ `marker`（平筆マーカー）/
  `spray`（スプレー）のいずれか。未指定・不明な値は `round` として扱われます
  （後方互換：この項目が無い既存のJSONもそのまま動きます）。
- `output` を省略した場合は、元動画と同じ解像度・fpsで出力します。
- 未知のキーは無視されます（将来の拡張用に安全に読み飛ばします）。
- `freezes` は `render.py` 内部で `time` 順にソートしてから処理します。JSON内の記述順は問いません。
- 新しいキー（`mono_contrast` / `film_offset` / `film_color` / `film_alpha` / `title_font` /
  `title_font_jp` / `title_bounce` / `title_pos` / `title_size` / `title_align` / `logo`）は
  すべて省略可能で、省略時は旧バージョンの `render.py` と同じ見た目で動きます
  （後方互換。詳細は次項）。`brush_fade_sec` だけは例外で、省略時は既定値 `0.3` が
  使われます（＝白い絵の具がフェードアウトする、見た目の不具合修正が既定で有効）。

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
3. **ブラシ完了時点**：ブラシ先端にまだ残っている白い絵の具を `brush_fade_sec`
   （既定0.3秒）かけてフェードアウトさせ、人物カラーだけが残るようにする
   （`brush_fade_sec: 0` で無効化＝旧バージョンと同じ即時消灯）。同時に名前テロップ
   （位置は `title_pos`・既定 `[0.5, 0.78]`＝横中央/高さ78%、寄せは `title_align`・
   既定 `center`、文字サイズは `title_size`・既定は高さの6%。日本語を含む名前は
   `title_font_jp`、それ以外は `title_font` を使用。どちらも未指定なら `font` に
   フォールバックする。白文字＋薄い黒ドロップシャドウ。`title_pos` が画面端に
   はみ出す位置を指しても、自動で内側に寄せられ画面外に出ない。指定したフォント
   ファイルが見つからない・読み込めない場合は、文字が描かれないまま気づかずに
   完成する事態を避けるため、その場でエラー終了する）を0.15秒でフェード
   インさせ、効果音 `sfx`（`assets/sfx/{sfx}.wav`。`assets/sfx/` 内のファイルを
   差し替えれば、差し替えた音がそのまま使われる。詳細は
   [「効果音を差し替える」](#効果音を差し替える)）を鳴らす。`title_bounce: true` なら
   フェードインと同じ0.15秒の間に130%→100%へ急停止イージングで縮む「はずむ」演出になる
   （このときテロップの外接矩形の中心を基準に拡大縮小する）
4. **残り時間**：`freeze_sec` になるまでカラー化＋テロップを保持
   （`logo.at: "last_freeze"` を指定した場合、時刻が一番遅いフリーズだけこの保持時間が
   ラストロゴの表示に必要な長さを満たすよう自動的に延長される。詳細は次項）
5. 通常再生に戻る

### ラストロゴ（`logo`）演出「インパクト着地＋光彩スイープ」

- `logo.image`：ロゴ画像（PNG推奨、透過対応）のパス。省略するとロゴ演出は無効
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
- `logo.duration_sec`：**着地してからの表示時間**（秒）。既定 1.2 秒
- `logo.sfx`：着地の瞬間に鳴らす効果音（省略可、既定は用意していれば `don`）
- タイムライン（着地開始 t=0 として、既定値の場合）
  1. `0.00-0.15s` **着地**：Scale 200%→100%・不透明度0→1 を Ease-Out Expo
     （`cubic-bezier(0.16,1,0.3,1)`）でアニメーションさせる
  2. `0.15-0.20s` **白フラッシュ**：着地の瞬間に効果音を鳴らし、画面全体を白く
     screen合成で0.05秒フラッシュさせる（強さは `flash_strength`）
  3. `0.20-0.50s` **光彩スイープ**：ロゴの左上→右下へ45度の白いハイライト帯
     （透明→白→透明の線形グラデーション、幅はロゴ幅の25%）を走らせる。ロゴ画像の
     輝度をマスクにしてscreen合成するため、黒背景の不透明PNGでも「明るい部分
     （文字など）」だけに光が乗る
  4. `0.50s-終了` **保持**：ゆっくり103%まで拡大しながら維持し、終了直前の0.3秒で
     背景色（`"video"` の場合は黒）へ暗転する
  5. 合計の表示区間は「着地(`landing_sec`) + `duration_sec`」秒
- 上記のうち以下のパラメータは `logo{}` 配下で個別に上書きできます（それ以外は固定値）：

  | キー | 既定値 | 意味 |
  | --- | --- | --- |
  | `scale_from` | `2.0` | スタンバイ時の初期スケール（200%） |
  | `landing_sec` | `0.15` | 着地（縮小＋フェードイン）にかかる時間 |
  | `sweep_sec` | `0.30` | 光彩スイープにかかる時間 |
  | `flash_strength` | `0.6` | 白フラッシュの強さ（0〜1） |
  | `duration_sec` | `1.2` | 着地からの表示時間 |

### 効果音を差し替える

`assets/sfx/shakin.wav` / `assets/sfx/don.wav` は、`make_dummy.py` が最初に生成する
**仮の合成音**です。フリー効果音サイト（例: [効果音ラボ](https://soundeffect-lab.info/)、
[OtoLogic](https://otologic.jp/)、[PIXABAY Sound Effects](https://pixabay.com/sound-effects/)
など、利用規約を確認の上で商用可否に注意して選んでください）から気に入った音を
ダウンロードし、同じファイル名（`shakin.wav` / `don.wav`。他の名前で使う場合は
プロジェクトJSONの `sfx` / `logo.sfx` にその名前を指定）で `assets/sfx/` に
上書き保存するだけで、以後 `render.py` はその音をそのまま使います
（特別な設定は不要。ファイルの存在確認だけで優先的に読み込む仕組みのため）。
`python make_dummy.py` を再実行しても、既に存在する `assets/sfx/*.wav` は
上書きされません（差し替えたファイルが仮の合成音に戻ってしまうことはありません）。

### 音声仕様

- 元動画の音声は、フリーズ区間を挿入した分だけ後ろへずれます。
- フリーズ中は `audio_during_freeze` が `mute` なら無音、`keep` なら直前0.5秒をループします。
- 効果音はブラシ完了の瞬間にミックスされます。
- `logo.sfx` は、ロゴが着地する瞬間（`landing_sec` 経過後。`at: "last_freeze"` かつ背景色
  クロスフェードがある場合はそのぶんも加算した後）に鳴ります。`logo.at: "end"` の場合、
  動画末尾に無音区間（着地+表示ぶん）を追加した上で、その中の着地位置で鳴ります。
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

node tests/editor_logic.test.js   # index.html のJSON生成・ジョブ連携ロジックのユニットテスト（依存なし）

python tests/render_quality.test.py   # render.pyの品質回帰テスト（音声連続性・VFR正規化・
                                       # テロップ描画・ブラシの白フェード・フォント読み込み
                                       # 失敗時のエラー終了。要ffmpeg/numpy、初回はダミーVFR動画を生成）

# 任意：UI回帰テスト（playwright-coreとChromiumが必要）
npm install --no-save playwright-core
python3 -m http.server 8794 &   # index.html をどこかで配信しておく
node tests/player_ui.playwright.test.mjs
node tests/freeze_black_screen.playwright.test.mjs
node tests/resize_scroll_black_screen.playwright.test.mjs
node tests/portrait_landscape_freezes.playwright.test.mjs # 縦動画・横動画それぞれでフリーズ3件追加→JSON件数一致を確認
node tests/brush_shape.playwright.test.mjs      # ブラシ形状選択・筆先スタンプ描画のE2E
node tests/film_logo_bounce.playwright.test.mjs # フィルム縁取り・テロップバウンス・ラストロゴ設定のE2E
node tests/title_pos_drag.playwright.test.mjs   # テロップのドラッグ移動・サイズ/寄せ変更のE2E
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
