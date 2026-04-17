# AI Design Automation

Webシステム・スマホアプリ開発の企画〜モック作成を自動化するローカルWebアプリ。

## セットアップ

```bash
# 依存パッケージのインストール
pip install -r requirements.txt

# 環境変数の設定
cp .env.example .env
# .env を編集して API キーを設定
#   ANTHROPIC_API_KEY=...
#   TAVILY_API_KEY=...

# 起動
python -m uvicorn main:app --reload --port 8000
```

ブラウザで http://localhost:8000 を開く。

## フロー

```
新規プロジェクト作成（フォーム入力）
  ↓
[企画・検討エージェント] → 調査ダッシュボード(HTML)
  ↓ ※任意でファクトチェックを手動実行可能
[ファクトチェックエージェント] → インライン修正済みHTML
  ↓ 承認
[提案書骨子エージェント] → 骨子レポート(HTML)
  ↓ 承認
[モック作成エージェント] → ワイヤーフレーム(HTML/CSS)
  ↓ 承認
完了
```

各フェーズで **承認 / 差し戻し / 一部修正** を選択可能。

## 使用API

| サービス | 用途 |
|---|---|
| Anthropic Claude API (Sonnet) | 企画・検討、提案書骨子、モック作成 |
| Anthropic Claude API (Haiku) | ファクトチェック |
| Tavily API | ファクトチェック用Web検索 |
