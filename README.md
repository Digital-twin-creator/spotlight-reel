# spotlight-reel

リール向け短尺動画（10秒〜1分）に対して、指定した時刻で人物を**フリーズ**させ、
指でなぞった軌跡を**太いブラシのストロークアニメーション**でカラー化し、
**名前テロップ**と**効果音**を合成する動画レンダラーです。

編集操作はスマホのブラウザ（別途作成予定のエディタ）で行い、その結果を**プロジェクトJSON**として受け取ります。
このリポジトリの `render.py` が、そのJSONと元動画からMP4を生成します。

実行環境は **Google Colab**（Python 3.10以上、ffmpeg あり）を想定しています。

## リポジトリ構成

```
spotlight-reel/
├── render.py            # メイン：JSON + 動画 → MP4
├── make_dummy.py         # テスト用ダミー動画・ダミーJSON生成
├── requirements.txt      # opencv-python-headless, numpy, pillow
├── assets/
│   ├── fonts/            # NotoSansJP-Bold.ttf
│   └── sfx/               # shakin.wav, don.wav
├── examples/
│   └── sample.json        # make_dummy.py が生成するサンプル
├── colab.ipynb            # Colab用ノートブック
└── README.md
```

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
```

## 今後の予定

スマホ用の編集エディタ（`index.html`、GitHub Pages でホスト予定）を別途追加し、
そこで作成したプロジェクトJSONをこのリポジトリの `render.py` で処理する構成にする予定です。
