from typing import Optional, Dict
import anthropic
import os
import re
import time
from tavily import TavilyClient

from backend.constants import SUB_PHASE_LABELS

CHART_INSTRUCTIONS = """
## バーチャートの出力方法（必須・HTMLを自分で書かないこと）

グラフが必要な箇所に、以下の形式のHTMLテーブルを生成すること。
サーバーが自動的に横棒グラフに変換する。自分でdivやcanvasを書く必要はない。

【書式】
<table class="barchart-data" summary="グラフタイトル">
  <tr><td>ラベル1</td><td>数値のみ（単位なし）</td></tr>
  <tr><td>ラベル2</td><td>数値のみ</td></tr>
</table>

【絶対禁止】空のdiv・min-height付きの空コンテナ・canvasタグを絶対に生成しないこと

【使用例】
<table class="barchart-data" summary="市場規模推移（億円）">
  <tr><td>2022年</td><td>1200</td></tr>
  <tr><td>2023年</td><td>1680</td></tr>
  <tr><td>2024年</td><td>2100</td></tr>
  <tr><td>2025年（予測）</td><td>2500</td></tr>
</table>

<table class="barchart-data" summary="市場シェア（%）">
  <tr><td>企業A</td><td>35</td></tr>
  <tr><td>企業B</td><td>28</td></tr>
  <tr><td>企業C</td><td>18</td></tr>
  <tr><td>その他</td><td>19</td></tr>
</table>
"""

HTML_RULES = """
## HTML出力ルール（最優先・違反禁止）
- 出力の最初のトークンは必ず「<!DOCTYPE html>」にすること（説明文・前置き・コードフェンスは絶対不要）
- HTMLコードのみを出力すること
- Tailwind CSS CDN使用: <script src="https://cdn.tailwindcss.com"></script>
- 単一HTMLファイルで完結させる
- 日本語で記述
- 推定値は「※推定」と明記する
- ⚠補足・事実確認注記・ファクトチェックコメント・「確認できない」「推測です」などの注釈は絶対に出力しない（違反禁止）

## 出典の記載ルール
- 具体的な数値・統計・事実を記載する際は、その直後に出典URLをインラインでリンク表示する
  形式: <a href="URL" target="_blank" class="text-xs text-blue-500 underline ml-1">出典</a>
- Web検索結果に含まれるURLを優先して使用する
- 推定・仮説の場合は「（※推定）」と付記する
"""

TOOLS = [
    {
        "name": "web_search",
        "description": "最新情報や事実確認のためにWeb検索を行います",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "検索クエリ（日本語または英語）"}
            },
            "required": ["query"],
        },
    }
]

