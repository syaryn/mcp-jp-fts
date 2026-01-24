# 日本語全文検索 MCP サーバー

[English](./README_en.md)

**FastMCP**、**SQLite FTS5**、**SudachiPy** を使用した日本語全文検索用の Model Context Protocol (MCP) サーバーです。

## 特徴

- **日本語対応の全文検索**: SudachiPy（モード A）を使用して、日本語テキストを適切にトークン化し、高精度な検索を実現
- **ローカルファイルのインデックス作成**: ディレクトリを再帰的にスキャンしてテキストファイルをインデックス化
- **自動クリーンアップ機能**: ディレクトリの再インデックス時に、削除されたファイルのエントリを自動的に削除してインデックスをクリーンに保つ
- **FastMCP 統合**: `index_directory` と `search_documents` を MCP ツールとして公開

## grep との比較における優位性

LLM が grep を使ってファイル検索を行う場合と比較して、この全文検索サーバーには以下の優位性があります：

### 1. 日本語の形態素解析による高精度検索

**grep の場合:**
- 文字列の単純な部分一致で検索
- 「東京都」を検索すると「東京都庁」「東京都民」などは見つかるが、文脈や単語の境界を理解しない
- 複合語や活用形の検索が困難（例：「走る」で検索しても「走った」「走っている」は見つからない）

**本サーバーの場合:**
- SudachiPy による形態素解析で単語単位で正しく分割
- 日本語の言語的な構造を理解した検索が可能
- トークン化により、より精度の高い検索結果を提供

### 2. インデックス化による高速検索

**grep の場合:**
- 検索のたびに全ファイルをスキャン
- 大量のファイルがある場合、毎回時間がかかる
- LLM のコンテキストウィンドウやトークン数の制約により、複数回の grep 実行が必要になることがある

**本サーバーの場合:**
- 事前にインデックスを作成するため、検索が高速
- SQLite FTS5 による最適化された全文検索
- 一度のクエリで関連する全ての結果を取得可能

### 3. 関連性スコアリングとスニペット表示

**grep の場合:**
- マッチした行をそのまま表示
- どの結果がより関連性が高いかの判断が困難
- コンテキストの把握に追加の `cat` や `head` コマンドが必要

**本サーバーの場合:**
- FTS5 の rank 機能により、関連性の高い順に結果を表示
- マッチ箇所を含むスニペット（前後の文脈付き）を自動生成
- 検索結果の品質が向上し、LLM がより適切な判断が可能

### 4. 検索パターンの柔軟性

**grep の場合:**
- 正規表現の知識が必要
- 複雑な検索条件は正規表現が複雑になり、エラーが発生しやすい
- 日本語の特性（ひらがな、カタカナ、漢字の混在）を考慮した検索が困難

**本サーバーの場合:**
- 自然言語クエリで検索可能
- トークン化により、単語の区切りを自動認識
- FTS5 の演算子（AND、OR、NEAR など）を活用した柔軟な検索

### 使用例の比較

**grep を使った検索（LLM が実行する場合）:**
```bash
# 「データベース」という単語を含むファイルを探す
$ grep -r "データベース" /path/to/docs/
# → 大量の結果が返り、LLM が処理しきれない可能性
# → 関連性の低い結果も含まれる
# → 複数回の実行で絞り込みが必要
```

**本サーバーを使った検索:**
```json
{
  "query": "データベース 設計",
  "limit": 5
}
```
→ 形態素解析により「データベース」と「設計」の両方を含む関連性の高い結果を、スニペット付きで返却



## 必要条件

- Python 3.10 以上
- [uv](https://docs.astral.sh/uv/) (パッケージマネージャー)
- [mise](https://mise.jdx.dev/) (オプション、開発ツール管理用)

## インストール

### 1. リポジトリのクローン

```bash
git clone <repository_url>
cd mcp-jp-fts
```

### 2. 依存関係のインストール

```bash
uv sync
```

## 使い方

### サーバーの起動

#### 開発モード（ホットリロード付き）

```bash
uv run fastmcp dev server.py
# または
mise run dev
```

#### 本番モード

```bash
uv run server.py
# または
mise run start
```

### MCP ツール

#### `index_directory`

指定されたパス内のすべてのテキストファイルをインデックス化します。

**入力例:**
```json
{
  "root_path": "/path/to/docs"
}
```

**出力例:**
```
Indexed 42 files in /path/to/docs (Previous entries cleared).
```

**注意:** このパスの既存のインデックスをクリアしてから新しいデータを追加します。

#### `search_documents`

SudachiPy のトークン化を使用してインデックス化されたドキュメントを検索します。

**入力例:**
```json
{
  "query": "猫",
  "limit": 5
}
```

**出力例:**
```
File: /path/to/wagahai.txt
Snippet: 吾輩は<b>猫</b>である...

File: /path/to/other.txt
Snippet: この<b>猫</b>は...
```

## 開発

### 開発環境のセットアップ

このプロジェクトは `mise` を使用して開発ツールを管理しています。

```bash
# mise のインストール（未インストールの場合）
curl https://mise.run | sh

# ツールのインストール
mise install

# Git フックのインストール
mise exec -- lefthook install
```

### よく使うコマンド

#### mise タスク

```bash
mise run dev          # 開発サーバーを起動（ホットリロード付き）
mise run start        # 本番サーバーを起動
mise run test         # テストを実行（pytest）
mise run test-all     # すべての Python バージョンでテストを実行（tox）
mise run lint         # リンターを実行（ruff）
mise run format       # コードをフォーマット（ruff）
mise run type         # 型チェックを実行（ty）
mise run check        # lint、type、test を一括実行
mise run scan         # 脆弱性スキャンを実行（osv-scanner）
mise run scan-license # ライセンスコンプライアンスチェックを実行
```

#### 手動コマンド

```bash
# テストの実行
uv run pytest tests/

# 複数バージョンでのテスト
uv run tox

# リント
uv run ruff check .

# フォーマット
uv run ruff format .

# 型チェック
uv run ty check .
```

### Git フック

`lefthook` を使用して自動チェックを実行します：

- **pre-commit**: `ruff check`（リント）、`ruff format`（フォーマット）、`ty check`（型チェック）を実行
- **pre-push**: `pytest`（テスト）と `osv-scanner`（脆弱性スキャン）を実行

手動でフックを実行：
```bash
lefthook run pre-commit
lefthook run pre-push
```

## テスト

プロジェクトには以下のテストが含まれています：

1. **トークン化テスト**: 日本語テキストの正しい分割を確認
2. **インデックス作成テスト**: ファイルのウォーク、読み取り、トークン化、SQLite への挿入を検証
3. **アトミック更新テスト**: ディレクトリの再インデックス時に存在しないファイルの削除を確認
4. **検索テスト**: クエリが正しいドキュメントを返し、日本語のトークン化を処理することを確認

```bash
# すべてのテストを実行
mise run test

# 複数の Python バージョンでテスト（3.10, 3.11, 3.12, 3.13）
mise run test-all
```

## コード品質

このプロジェクトは以下のツールを使用してコード品質を維持しています：

- **[ruff](https://github.com/astral-sh/ruff)**: リントとフォーマット
- **[ty](https://github.com/google/tyche)**: 型チェック
- **[pytest](https://pytest.org/)**: テスティングフレームワーク
- **[tox](https://tox.wiki/)**: 複数バージョンでのテスト自動化
- **[osv-scanner](https://github.com/google/osv-scanner)**: 脆弱性とライセンスのスキャン
- **[lefthook](https://github.com/evilmartians/lefthook)**: Git フック管理

## プロジェクト構成

```
mcp-jp-fts/
├── server.py              # FastMCP サーバーのメイン実装
├── tests/
│   ├── test_server.py     # サーバー機能のテスト
│   └── resources/         # テスト用リソース（サンプルテキストファイル）
├── pyproject.toml         # プロジェクトメタデータと依存関係
├── tox.ini                # Tox 設定
├── mise.toml              # Mise ツールとタスク設定
├── lefthook.yml           # Git フック設定
├── osv-scanner.toml       # OSV Scanner 設定
└── uv.lock                # ロックファイル
```

## 技術スタック

- **FastMCP**: MCP サーバーフレームワーク
- **SQLite FTS5**: 全文検索エンジン
- **SudachiPy**: 日本語形態素解析ライブラリ
- **uv**: パッケージマネージャー
- **mise**: 開発ツール管理

## ライセンス

このプロジェクトは MIT ライセンスの下で公開されています。

## 貢献

プルリクエストを歓迎します！大きな変更の場合は、まず issue を開いて変更内容について議論してください。

## トラブルシューティング

### Python バージョンの問題

このプロジェクトは Python 3.10 以上をサポートしています。SudachiPy の wheel が利用可能なバージョンを使用してください。

```bash
# 現在の Python バージョンを確認
python --version

# uv で特定のバージョンを使用
uv python install 3.13
uv sync
```

### SudachiPy の辞書エラー

初回実行時に SudachiPy が自動的に辞書をダウンロードします。ネットワークエラーが発生した場合は、手動でインストールできます：

```bash
uv run python -c "import sudachipy; sudachipy.Dictionary()"
```
