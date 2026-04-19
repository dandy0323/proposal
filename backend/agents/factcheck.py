import anthropic
import os
import json
from tavily import TavilyClient

def _get_anthropic():
    return anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))

def _get_tavily():
    return TavilyClient(api_key=os.environ.get("TAVILY_API_KEY", ""))

SYSTEM_PROMPT = """あなたは優秀なファクトチェッカーです。
提供された企画・検討ダッシュボードHTMLの内容を精査し、事実確認が必要な箇所を検証してください。

## タスク
1. HTMLから事実確認が必要な主張・数値・統計・市場情報などを抽出する
2. 各主張についてWeb検索ツールを使って最新情報を調査する
3. 誤りや古い情報がある場合、HTMLにインライン修正を加えた新しいHTMLを出力する

## 修正方法（HTMLへのインライン埋め込み）
誤りがある箇所: <span style="text-decoration:line-through;color:#ef4444;">誤った情報</span>
              <span style="color:#22c55e;font-weight:bold;">✓ 正しい情報</span>
              <a href="URL" target="_blank" style="color:#3b82f6;font-size:0.8em;">[出典]</a>

補足追記が必要な箇所: 既存テキストの後に
              <span style="color:#f59e0b;">⚠ 補足: 追加情報</span>
              <a href="URL" target="_blank" style="color:#3b82f6;font-size:0.8em;">[出典]</a>

## 出力仕様
- 修正済みHTMLのみを出力する（前後の説明不要）
- 事実として正しい情報はそのまま保持する
- 修正がない場合も元のHTMLをそのまま返す
- 根拠URLは必ず記載する
"""

TOOLS = [
    {
        "name": "web_search",
        "description": "最新情報や事実確認のためにWeb検索を行います",
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "検索クエリ（日本語または英語）",
                }
            },
            "required": ["query"],
        },
    }
]


def run(planning_html: str) -> str:
    messages = [
        {
            "role": "user",
            "content": f"以下の企画・検討ダッシュボードHTMLをファクトチェックしてください。\n\n```html\n{planning_html}\n```",
        }
    ]
    anthropic_client = _get_anthropic()

    while True:
        response = anthropic_client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=16000,
            system=SYSTEM_PROMPT,
            tools=TOOLS,
            messages=messages,
        )

        if response.stop_reason == "tool_use":
            tool_results = []
            for block in response.content:
                if block.type == "tool_use" and block.name == "web_search":
                    query = block.input.get("query", "")
                    search_result = _search(query)
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": search_result,
                    })

            assistant_content = []
            for block in response.content:
                if block.type == "text":
                    assistant_content.append({"type": "text", "text": block.text})
                elif block.type == "tool_use":
                    assistant_content.append({
                        "type": "tool_use", "id": block.id,
                        "name": block.name, "input": block.input,
                    })
            messages.append({"role": "assistant", "content": assistant_content})
            messages.append({"role": "user", "content": tool_results})

        else:
            for block in response.content:
                if hasattr(block, "text"):
                    html = block.text.strip()
                    if html.startswith("```html"):
                        html = html[7:]
                    if html.endswith("```"):
                        html = html[:-3]
                    return html.strip()
            return planning_html


def _search(query: str) -> str:
    try:
        result = _get_tavily().search(query=query, max_results=3)
        lines = []
        for r in result.get("results", []):
            lines.append(f"タイトル: {r.get('title', '')}")
            lines.append(f"URL: {r.get('url', '')}")
            lines.append(f"概要: {r.get('content', '')[:500]}")
            lines.append("---")
        return "\n".join(lines) if lines else "検索結果なし"
    except Exception as e:
        return f"検索エラー: {e}"
