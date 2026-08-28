# spotlight-reel

リール向け短尺動画（10秒〜1分）に対して、指定した時刻で人物を**フリーズ**させ、
指でなぞった軌跡を**太いブラシのストロークアニメーション**でカラー化し、
**名前テロップ**と**効果音**を合成する動画レンダラーです。

編集操作はスマホのブラウザ上のエディタ（`index.html`、GitHub Pagesで公開）で行い、
その結果を**プロジェクトJSON**として受け取ります。
このリポジトリの `render.py` が、そのJSONと元動画からMP4を生成します。

実行環境は **Google Colab**（Python 3.10以上、ffmpeg あり）を想定しています。

## リポジトリ構成

```
spotlight-reel/
├── index.html            # スマホ用エディタ（GitHub Pagesで公開、ビルド不要の単一HTML）
├── render.py              # メイン：JSON + 動画 → MP4
├── make_dummy.py          # テスト用ダミー動画・ダミーJSON生成
├── requirements.txt       # opencv-python-headless, numpy, pillow
├── assets/
│   ├── fonts/             # NotoSansJP-Bold.ttf
│   └── sfx/                # shakin.wav, don.wav
├── examples/
│   └── sample.json         # make_dummy.py が生成するサンプル
├── tests/
│   └── editor_logic.test.js # index.html のJSON生成ロジックのユニットテスト（Node実行）
├── colab.ipynb             # Colab用ノートブック
└── README.md
```

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
   「📤 JSONを書き出す」で `project.json` を保存する（共有シート対応時はそのままファイル/ドライブへ、
   非対応ブラウザではダウンロードになる。「📋 JSONをコピー」でクリップボードにも取れる）。

編集内容は動画ファイル名をキーに端末の `localStorage` に自動保存されるため、途中でリロードしても
同じ動画ファイルを選び直せば復元されます（動画そのものは保存されないので、再選択が必要です）。

### Colabへの受け渡し手順

1. スマホで書き出した `project.json` と、元の動画ファイルを **同じフォルダ名で** Googleドライブに置く
   （例: `MyDrive/spotlight_reel/input.mp4` と `MyDrive/spotlight_reel/project.json`）。
2. `colab.ipynb` を開き、「本番用（任意）」セクションでドライブをマウントし、
   `VIDEO_PATH` / `JSON_PATH` / `OUT_PATH` をそのパスに書き換えて実行する。
3. `OUT_PATH` に出力されたMP4をドライブから確認・ダウンロードする。

（`colab.ipynb` の最初の3セルは、動作確認用のダミー動画で「ランタイム→すべてのセルを実行」
だけで一気に試せるようになっています。）

## 使い方（Google Colab）

1. `colab.ipynb` を Colab で開く（GitHub連携から直接開くか、`File > Upload notebook`）。
2. セルを上から順に実行：
   1. リポジトリの `git clone` と `pip install -r requirements.txt`
   2. Googleドライブのマウント
   3. `VIDEO_PATH` / `JSON_PATH` / `OUT_PATH`（すべてドライブ内のパス）を指定
   4. `render.py` の実行
   5. 出力ファイルの確認・プレビュー再生

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

node tests/editor_logic.test.js   # index.html のJSON生成ロジックのユニットテスト
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
