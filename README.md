# rsne_make_llm_label

膝MRIレポートを **1件ごとに必ず新しいCodexセッション** で読み、12ターゲットの状態と原文根拠を保存する専用作業環境です。Python標準ライブラリだけで実行・テストできます。画像学習、正解ラベルの読み込み、学習用0/1への変換は行いません。

## 配置

```text
/workspaces/rsne_make_llm_label/
├── AGENTS.md                         # 実装・保守用
├── .devcontainer/
│   ├── devcontainer.json
│   └── post-create.sh
├── config/
│   ├── codex-version.txt              # CLI 0.153.4を固定
│   └── labeler.toml                   # 読解用設定の正本
├── prompts/
│   └── codex_report_labeling_instructions_v1.md
├── schemas/
│   └── annotation.schema.json
├── scripts/
│   ├── setup_worker.py
│   └── run_labels.py
├── tests/
│   └── test_labeler.py
├── data/                             # README以外はGit管理外
└── outputs/                          # README以外はGit管理外

/home/vscode/label-worker/
└── AGENTS.md                         # 共通指示v1の無変更コピー

/home/vscode/.codex-labeler/           # 読解専用CODEX_HOME・専用Docker volume
├── config.toml                       # config/labeler.tomlのコピー
└── 認証情報など                      # Gitに追加しない
```

`label-worker` と読解用 `CODEX_HOME` はセットアップ時にコンテナ内へ作成します。リポジトリ内にworker用AGENTS.mdの別正本は作りません。開発用Codexへ `CODEX_HOME` をグローバル設定せず、読解プロセスにだけ専用の値を渡します。

## 開始手順

このリポジトリだけをVS Codeで開き、**Dev Containers: Reopen in Container** を実行します。Python 3.12 / Node.js 22を用意し、`config/codex-version.txt` に固定したCodex CLIをインストールします。初回セットアップにはイメージ・npmパッケージ取得のネットワーク接続が必要です。認証・有料推論・データダウンロードは自動実行しません。

コンテナのターミナルで読解用アカウントを認証します。

```bash
CODEX_HOME="$LABELER_CODEX_HOME" codex login --device-auth
```

認証情報は専用volumeに保持します。ホストの `~/.codex`、元の学習リポジトリ、gold、画像予測、画像データを追加マウントしないでください。データ利用条件と利用するCodex環境への入力可否は、実データ送信前に確認してください。

`data/reports.jsonl` に、あらかじめラベル列を除外したデータを配置します。UTF-8 JSONLで、各行のキーは `study_id` と `report` の2つだけです。改行を含むレポート本文はJSON文字列内でエスケープします。本文の改変・要約・正規化はしません。

以下は実データではなく、入力形式の人工例です。

```json
{"study_id":"synthetic-001","report":"The ACL is intact. Small joint effusion."}
```

モデルは自動選択しません。アカウントで利用可能なモデルIDを設定し、全件処理中は固定してください。

```bash
export CODEX_MODEL="利用可能なモデルIDに置き換える"

# 入力・指示配置の確認だけ。Codexは起動しません。
python scripts/run_labels.py \
  --input data/reports.jsonl --output-dir outputs/labels_v1 \
  --model "$CODEX_MODEL" --limit 5 --dry-run

# 最初の5件。ここからCodexを実行し、利用枠を消費します。
python scripts/run_labels.py \
  --input data/reports.jsonl --output-dir outputs/labels_v1 \
  --model "$CODEX_MODEL" --reasoning-effort medium --limit 5
```

結果と使用量を確認後、同じコマンドから `--limit 5` を外すと、完了済みの5件をスキップして残りを処理します。`--limit N` は「先頭N件」であり、完了分もN件に含みます。対象数の統計確認やgoldの選別は読解前に別環境で済ませてください。

## 実行と再開の契約

共通指示はworkerのAGENTS.mdから読み込ませ、標準入力には1件分のJSONだけを渡します。`codex exec --ephemeral --json --output-schema ...` を毎回新規起動します。`resume`、`fork`、前件の履歴、複数レポートの同時投入は使いません。プロンプトと出力形式は固定しますが、新規セッション間のキャッシュ命中は保証せず使用量で確認します。

デフォルトは逐次処理・1試行・1件300秒のタイムアウトです。`--attempts 2` のように指定した場合も、各再試行は新規セッションです。最終失敗時はそこで停止し、原因を直して同じコマンドを再実行します。失敗を陰性や未言及に置き換えません。タイムアウトやCtrl-CではCLIのプロセスグループを終了させます。

同じ出力先では、モデル・推論設定・CLI版・共通指示・スキーマ・読解設定・実行コードの変更を拒否します。完了済みのstudyで本文が変わった場合も停止します。変更した版は新しい出力先を使ってください。設定を更新する場合は実行を止めてから `python scripts/setup_worker.py --replace` でコピーを更新します。

## 出力

```text
outputs/labels_v1/
├── manifest.json                     # 固定設定と各ファイルのSHA-256
├── annotations/<study_id>.json       # 12ターゲットのstate/evidence
├── receipts/<study_id>.json          # 検証済み完了マーカー・使用量・thread ID
└── attempts/<study_id>/attempt-*/
    ├── response.json                 # 各試行のモデル応答（未生成の場合あり）
    ├── events.jsonl                  # CLIのJSONイベント
    ├── stderr.log
    └── failure.json                  # 失敗時だけ
```

ID一致、12キーと順序、4状態、根拠の空配列条件、根拠が原文の部分文字列であることを検証します。医学的な正誤を正規表現で判定・上書きしません。予期しないツール利用や複数ターンをイベントで検出した場合も完了扱いにしません。

`receipts` は結果保存後に最後に発行します。再開時は本文ハッシュ・結果ハッシュ・形式を再確認して完了分だけをスキップします。完了済みファイルの破損は黙って上書きせず停止します。`manifest.json` の存在だけでは全件完了を意味しません。

使用量は各receiptの `usage.input_tokens`、`usage.cached_input_tokens`、`usage.output_tokens` などです。失敗した試行でも消費が発生し得るため、試行ごとの `events.jsonl` も残します。ログや根拠にはレポートの情報が含まれるので、Gitへ追加したり公開共有したりしないでください。`--ephemeral` はこのアプリの結果・イベントログを削除する指定ではありません。

## テスト

```bash
python -m unittest discover -s tests -v
python -m compileall -q scripts tests
```

テストは人工レポートとモックを使い、Codexへの問い合わせは行いません。オフラインテストの合格は、実際の認証・モデル利用可否・ラベル精度・キャッシュ命中の検証を意味しません。実データ全件処理の前に少数件で確認してください。

このdevcontainerは不要なデータを持ち込まない作業分離です。悪意あるコードからの完全な隔離ではありません。読解用にカスタムskills、MCP、プラグイン、グローバルAGENTS.mdを追加しないでください。

## 参照

判定指示は提供済み `codex_report_labeling_instructions_v1.md` を無変更で採用しています。医学的な条件はそのファイル内の出典に従います。

- [Codex non-interactive mode](https://developers.openai.com/codex/noninteractive)
- [AGENTS.md](https://developers.openai.com/codex/guides/agents-md)
- [Codex configuration reference](https://developers.openai.com/codex/config-reference)
- [Codex authentication](https://developers.openai.com/codex/auth)
- [Codex CLI 0.153.4 release](https://github.com/openai/codex/releases/tag/rust-v0.153.4)
- [Dev Containers Python image](https://github.com/devcontainers/images/tree/main/src/python)
