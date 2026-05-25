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

## グラフ・チャートの出力方法（重要）
- <canvas>・Chart.js・Plotly・D3.js等のJSライブラリは使用禁止（サーバーが自動削除するため空白になる）
- 数値データを棒グラフで表示したい場合は必ず以下の形式を使うこと。サーバーが自動で横棒グラフに変換する:
  <table class="barchart-data" summary="グラフタイトル">
    <tr><td>ラベル</td><td>数値のみ（単位なし）</td></tr>
  </table>

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


def _get_anthropic():
    return anthropic.Anthropic(
        api_key=os.environ.get("ANTHROPIC_API_KEY", ""),
        timeout=1800.0,  # 30 minutes — large token responses can take many minutes
    )


def _get_tavily():
    return TavilyClient(api_key=os.environ.get("TAVILY_API_KEY", ""))


def _search(query: str) -> str:
    try:
        result = _get_tavily().search(query=query, max_results=10)
        lines = []
        for r in result.get("results", []):
            lines.append(f"タイトル: {r.get('title', '')}")
            lines.append(f"URL: {r.get('url', '')}")
            lines.append(f"内容: {r.get('content', '')}")
            lines.append("---")
        return "\n".join(lines) if lines else "検索結果なし"
    except Exception as e:
        return f"検索エラー: {e}"


def _bars_to_html(bars: list, title: str) -> str:
    if not bars:
        return ''
    max_val = max(v for _, v in bars)
    if max_val == 0:
        return ''
    rows = []
    for label, val in bars:
        pct = max(2, round(val / max_val * 100))
        display = f'{int(val):,}' if val == int(val) else f'{val:,.1f}'
        rows.append(
            f'<tr>'
            f'<td style="padding:4px 8px 4px 0;font-size:12px;color:#64748b;'
            f'white-space:nowrap;vertical-align:middle;width:35%;">{label}</td>'
            f'<td style="padding:4px 8px;vertical-align:middle;">'
            f'<div style="height:18px;border-radius:3px;'
            f'background:linear-gradient(to right,#3b82f6 {pct}%,#e2e8f0 {pct}%);'
            f'font-size:1px;color:transparent;">.</div></td>'
            f'<td style="padding:4px 0 4px 8px;font-size:12px;font-weight:700;'
            f'color:#1e40af;white-space:nowrap;vertical-align:middle;width:15%;">{display}</td>'
            f'</tr>'
        )
    title_row = (
        f'<tr><th colspan="3" style="text-align:left;font-size:13px;font-weight:600;'
        f'color:#334155;padding:0 0 8px 0;border:none;background:none;">{title}</th></tr>'
        if title else ''
    )
    return (
        '<div style="margin:1.5rem 0;padding:16px;background:#f8fafc;'
        'border-radius:8px;border:1px solid #e2e8f0;overflow:hidden;">'
        f'<table style="width:100%;border-collapse:collapse;">'
        f'<tbody>{title_row}{"".join(rows)}</tbody>'
        f'</table></div>'
    )


def _inject_charts(html: str) -> str:
    """Convert chart markers to horizontal bar chart HTML (server-side, always correct).

    Handles two formats:
    1. <table class="barchart-data" summary="title"><tr><td>label</td><td>value</td></tr></table>
    2. <!-- CHART: title | label:val, ... --> (legacy fallback)
    """
    # Format 1: <table class="barchart-data">
    def convert_table(m):
        summary_match = re.search(r'summary=["\']([^"\']*)["\']', m.group(0))
        title = summary_match.group(1) if summary_match else ''
        rows = re.findall(r'<tr>\s*<td[^>]*>(.*?)</td>\s*<td[^>]*>(.*?)</td>\s*</tr>', m.group(0), re.IGNORECASE | re.DOTALL)
        bars = []
        for label_html, val_html in rows:
            label = re.sub(r'<[^>]+>', '', label_html).strip()
            val_raw = re.sub(r'[^\d.]', '', re.sub(r'<[^>]+>', '', val_html).strip())
            try:
                bars.append((label, float(val_raw)))
            except ValueError:
                pass
        print(f"[inject_charts] barchart-data title={title!r} bars={bars}")
        chart = _bars_to_html(bars, title)
        return chart if chart else ''

    html = re.sub(
        r'<table\b[^>]*class=["\'][^"\']*barchart-data[^"\']*["\'][^>]*>.*?</table>',
        convert_table,
        html,
        flags=re.IGNORECASE | re.DOTALL,
    )

    # Format 2: <!-- CHART: title | label:val, ... -->
    def convert_comment(m):
        content = m.group(1).strip()
        parts = content.split('|', 1)
        title = parts[0].strip()
        data_str = parts[1].strip() if len(parts) > 1 else ''
        bars = []
        for entry in data_str.split(','):
            entry = entry.strip()
            colon_idx = entry.rfind(':')
            if colon_idx == -1:
                continue
            label = entry[:colon_idx].strip()
            val_raw = re.sub(r'[^\d.]', '', entry[colon_idx + 1:].strip())
            try:
                bars.append((label, float(val_raw)))
            except ValueError:
                pass
        chart = _bars_to_html(bars, title)
        return chart if chart else ''

    html = re.sub(r'<!--\s*CHART:\s*(.*?)\s*-->', convert_comment, html, flags=re.DOTALL)

    # Remove inline <script> tags (those without src=) BEFORE placeholder cleanup.
    html = re.sub(
        r'<script\b(?![^>]*\bsrc\s*=)[^>]*>.*?</script>',
        '',
        html,
        flags=re.IGNORECASE | re.DOTALL,
    )

    # Remove canvas elements
    html = re.sub(r'<canvas\b[^>]*/>', '', html, flags=re.IGNORECASE)
    html = re.sub(r'<canvas\b[^>]*>.*?</canvas>', '', html, flags=re.IGNORECASE | re.DOTALL)

    # Remove empty AI-generated chart placeholder divs.
    for _ in range(6):
        before = html
        html = re.sub(
            r'<div\b[^>]*(?:min-height|height)\s*:\s*\d+[^>]*>\s*(?:<!--.*?-->\s*)*</div>',
            '',
            html,
            flags=re.IGNORECASE | re.DOTALL,
        )
        html = re.sub(
            r'<div\b[^>]*(?:class|id)=["\'][^"\']*chart[^"\']*["\'][^>]*>\s*(?:<!--.*?-->\s*)*</div>',
            '',
            html,
            flags=re.IGNORECASE | re.DOTALL,
        )
        html = re.sub(
            r'<div\b[^>]*>\s*(?:<!--.*?-->\s*)*</div>',
            '',
            html,
            flags=re.IGNORECASE | re.DOTALL,
        )
        if html == before:
            break

    return html


def _create_with_retry(client, **kwargs):
    """API call with retry on 429 rate limit errors."""
    for attempt in range(3):
        try:
            return client.messages.create(**kwargs)
        except anthropic.RateLimitError as e:
            if attempt < 2:
                wait_sec = 60 * (attempt + 1)
                print(f"Rate limit hit. Waiting {wait_sec}s before retry {attempt + 2}/3...")
                time.sleep(wait_sec)
            else:
                raise


def _blocks_to_dicts(content) -> list:
    """Convert SDK ContentBlock objects to plain dicts for message history."""
    result = []
    for block in content:
        if block.type == "text":
            result.append({"type": "text", "text": block.text})
        elif block.type == "tool_use":
            result.append({
                "type": "tool_use",
                "id": block.id,
                "name": block.name,
                "input": block.input,
            })
        else:
            result.append({"type": block.type})
    return result


def _run_with_tools(system: str, user_msg: str) -> str:
    """Run Claude with Tavily tool use loop (max 8 iterations)."""
    client = _get_anthropic()
    messages = [{"role": "user", "content": user_msg}]

    for _ in range(5):
        response = _create_with_retry(
            client,
            model="claude-haiku-4-5-20251001",
            max_tokens=8192,
            system=system,
            tools=TOOLS,
            messages=messages,
        )
        if response.stop_reason == "tool_use":
            tool_results = []
            for block in response.content:
                if block.type == "tool_use" and block.name == "web_search":
                    search_result = _search(block.input.get("query", ""))
                    tool_results.append({
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": search_result,
                    })
            messages.append({"role": "assistant", "content": _blocks_to_dicts(response.content)})
            messages.append({"role": "user", "content": tool_results})
        else:
            for block in response.content:
                if hasattr(block, "text"):
                    return _strip(block.text)
            return ""
    return ""


_CONTINUATION_SYSTEM = (
    "あなたはHTMLレポート生成AIです。途中で切れたHTMLの続きを生成してください。\n"
    "【最重要】欠落しているすべてのコンテンツ・データ・分析内容を完全に生成すること。タグを閉じるだけで終わることは絶対禁止。\n"
    "【絶対禁止】謝罪文・説明文・解説・Markdown・コードフェンス・<!DOCTYPE>/<html>/<head>/<body>タグの出力\n"
    "【必須】残りのすべてのセクション・コンテンツを省略なく詳細に出力してから、最後に</body></html>で終了。"
)

_SECTION_FRAGMENT_SYSTEM = """あなたはHTMLコンテンツ生成ツールです。
指定されたセクションの本文HTMLのみを出力してください。

【絶対禁止】
- <script>タグ・JavaScriptコード（1行も書かないこと）
- <canvas>要素・SVGグラフ
- Chart.js・Plotly・D3.js・ECharts等のJSライブラリ使用
- <!DOCTYPE>/<html>/<head>/<body>タグ
- 謝罪文・説明文・⚠マーク・Markdown・コードフェンス

【重要：上記を違反した場合の動作】
サーバーは<canvas>と<script>を自動削除します。
その結果、chart用に作ったdivだけが残り、グラフエリアが「白い空白」になります。
絶対に使用しないでください。

【数値データの表示方法（これだけ使うこと）】
数値比較・推移・ランキングのデータは必ず次の形式で出力してください。
サーバーが自動的に横棒グラフに変換して表示します。

<table class="barchart-data" summary="グラフのタイトル">
  <tr><td>ラベル名</td><td>数値（単位なし・数字のみ）</td></tr>
  <tr><td>ラベル名</td><td>数値</td></tr>
</table>

この形式だけを使い、canvas・div・scriptは一切書かないこと。

【出力形式】
- 指定された<h2>タグから始めること
- Tailwind CSSクラスで見やすくレイアウトすること
- 数値・事実・根拠を含め詳細に記述すること
"""


def _gen_market_fragment(user_content: str, max_tokens: int = 16000) -> str:
    client = _get_anthropic()
    try:
        response = _create_with_retry(
            client,
            model="claude-sonnet-4-6",
            max_tokens=max_tokens,
            system=_SECTION_FRAGMENT_SYSTEM,
            messages=[{"role": "user", "content": user_content}],
        )
    except Exception as e:
        import traceback
        print(f"[market_fragment] ERROR on initial call: {e}")
        traceback.print_exc()
        return ''

    text = response.content[0].text
    print(f"[market_fragment] stop={response.stop_reason} len={len(text)}")

    for _cont_attempt in range(5):
        if response.stop_reason != "max_tokens":
            break
        try:
            cont_resp = _create_with_retry(
                client,
                model="claude-sonnet-4-6",
                max_tokens=32000,
                system=_CONTINUATION_SYSTEM,
                messages=[{"role": "user", "content": (
                    "以下のHTMLが途中で切れています。末尾から続くHTMLのみを出力してください。"
                    "最後は開いているタグを閉じて終了すること。\n\n"
                    f"【末尾】\n{text[-4000:]}"
                )}],
            )
        except Exception as e:
            print(f"[market_fragment] ERROR on cont{_cont_attempt+1}: {e}")
            break
        chunk = _strip_continuation(cont_resp.content[0].text)
        print(f"[market_fragment] cont{_cont_attempt+1} stop={cont_resp.stop_reason} len={len(chunk)}")
        text += chunk
        response = cont_resp

    result = text

    m_fence = re.search(r'```[ \t]*\w*[ \t]*\n', result)
    if m_fence:
        result = result[m_fence.end():]
        if result.rstrip().endswith('```'):
            result = result.rstrip()[:-3]

    result = re.sub(r'(?i)<!DOCTYPE\b[^>]*>\s*', '', result)
    result = re.sub(r'(?i)<html\b[^>]*>\s*', '', result)
    result = re.sub(r'(?i)<head\b.*?</head>\s*', '', result, flags=re.DOTALL)
    result = re.sub(r'(?i)<body\b[^>]*>\s*', '', result)
    result = re.sub(r'(?i)\s*</body>\s*</html>', '', result)
    result = re.sub(r'(?i)\s*</html>', '', result)

    if not re.search(r'<[a-zA-Z][^>]{0,100}>', result):
        print(f"[market_fragment] WARN: no HTML tags in result, returning empty")
        return ''

    m_first = re.search(r'<[a-zA-Z]', result)
    if m_first and m_first.start() > 0:
        result = result[m_first.start():]

    return result.strip()


def _build_market_report_html(industry: str, *fragments: str) -> str:
    head = f"""<!DOCTYPE html>
<html lang="ja">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1.0">
<title>{industry} 市場分析レポート</title>
<script src="https://cdn.tailwindcss.com"></script>
<style>
  body {{ font-family:'Hiragino Kaku Gothic ProN','Meiryo',sans-serif; }}
  h2 {{ border-bottom:2px solid #3b82f6; padding-bottom:.5rem; margin-top:2rem; }}
  h3 {{ color:#334155; margin-top:1.25rem; }}
  table:not(.barchart-data) {{ width:100%; border-collapse:collapse; margin:1rem 0; }}
  table:not(.barchart-data) th,table:not(.barchart-data) td {{ padding:8px 12px; border:1px solid #e2e8f0; text-align:left; }}
  table:not(.barchart-data) th {{ background:#f8fafc; font-weight:600; }}
  table:not(.barchart-data) tr:nth-child(even) {{ background:#f9fafb; }}
  ul,ol {{ padding-left:1.5rem; margin:.5rem 0; }}
  li {{ margin-bottom:.25rem; }}
</style>
</head>
<body class="max-w-5xl mx-auto p-8">
"""
    body = "\n\n".join(f.strip() for f in fragments if f.strip())
    return head + body + "\n</body>\n</html>"


def _run_simple(
    system: str,
    user_msg: str,
    model: str = "claude-sonnet-4-6",
    max_tokens: int = 32000,
    complete_fn=None,
    continuation_hint: str = "",
    max_continuations: int = 8,
) -> str:
    client = _get_anthropic()

    response = _create_with_retry(
        client, model=model, max_tokens=max_tokens, system=system,
        messages=[{"role": "user", "content": user_msg}],
    )
    accumulated = _strip(response.content[0].text)
    print(f"[run_simple] initial: stop={response.stop_reason} len={len(accumulated)} model={model}")

    def _strip_close(html: str) -> str:
        return re.sub(r'(?:\s*</body>)?\s*</html>\s*$', '', html.rstrip(), flags=re.IGNORECASE).rstrip()

    for attempt in range(max_continuations):
        html_closed = bool(re.search(r'</html\s*>', accumulated, re.IGNORECASE))
        sections_complete = complete_fn is None or complete_fn(accumulated)
        print(f"[run_simple] attempt={attempt} closed={html_closed} complete={sections_complete} len={len(accumulated)}")
        if response.stop_reason != "max_tokens" and html_closed and sections_complete:
            break

        if html_closed and not sections_complete:
            base_open = _strip_close(accumulated)
            tail = base_open[-2000:]
            hint_block = f"\n{continuation_hint}\n" if continuation_hint else ""
            cont_content = (
                f"以下のHTMLは途中で閉じられており、必須セクションが欠落しています。{hint_block}"
                "末尾の</body></html>を取り除いた状態から、欠落セクションのHTMLを追記してください。\n\n"
                f"【現在の末尾（閉じタグ除外）】\n{tail}\n\n"
                "【指示】\n"
                "- 欠落セクションのHTMLのみ出力（冒頭の重複禁止）\n"
                "- <!DOCTYPE>/<html>/<head>/<body>タグは出力不要\n"
                "- 謝罪文・説明文・Markdownは絶対不要\n"
                "- 全欠落セクションを出力後、必ず</body></html>で終了"
            )
        else:
            tail = accumulated[-4000:]
            hint_block = f"\n{continuation_hint}\n" if continuation_hint else ""
            cont_content = (
                f"以下のHTMLが途中で切れています。末尾の直後から続くHTMLコードを出力してください。{hint_block}\n\n"
                f"【現在の末尾（最後の4000文字）】\n{tail}\n\n"
                "【絶対ルール】\n"
                "- 上記末尾の直後から続くHTMLのみ出力（冒頭の重複は禁止）\n"
                "- 謝罪文・説明文・Markdownは一切出力禁止\n"
                "- <!DOCTYPE>/<html>/<head>/<body>タグは出力不要\n"
                "- 途中で切れているセル・リスト・テーブルの内容も省略せず完全に生成すること\n"
                "- 残りの全セクションを省略せず完全に出力すること\n"
                "- タグを閉じるだけで終わることは絶対禁止\n"
                "- 最後は必ず</body></html>で終了"
            )

        cont_messages = [{"role": "user", "content": cont_content}]
        response = _create_with_retry(
            client, model=model, max_tokens=max_tokens,
            system=_CONTINUATION_SYSTEM, messages=cont_messages,
        )
        chunk = response.content[0].text

        chunk_head = chunk[:600]
        is_restart = bool(
            re.search(r'(?i)<!DOCTYPE\s+html', chunk)
            or re.search(r'(?i)<html\b', chunk_head)
        )
        if is_restart:
            new_doc = _strip(chunk)
            if new_doc and re.search(r'</html\s*>', new_doc, re.IGNORECASE):
                new_complete = complete_fn is None or complete_fn(new_doc)
                if new_complete:
                    accumulated = new_doc
                    break
        else:
            cont = _strip_continuation(chunk)
            if cont:
                cont_text_head = re.sub(r'<[^>]+>', '', cont[:300]).strip()
                acc_text = re.sub(r'<[^>]+>', '', accumulated[:6000]).strip()
                if len(cont_text_head) > 30 and cont_text_head[:80] in acc_text[:int(len(acc_text) * 0.7)]:
                    pass
                else:
                    base = _strip_close(accumulated)
                    accumulated = base + "\n" + cont

    html_closed = bool(re.search(r'</html\s*>', accumulated, re.IGNORECASE))
    sections_complete = complete_fn is None or complete_fn(accumulated)
    if not (html_closed and sections_complete):
        accumulated += "\n<!-- __TRUNCATED__ -->"

    return accumulated


def _strip(text: str) -> str:
    text = text.strip()
    m = re.search(r'```[ \t]*\w*[ \t]*\n', text)
    if m:
        text = text[m.end():]
        if text.rstrip().endswith('```'):
            text = text.rstrip()[:-3]
        text = text.strip()
    m2 = re.search(r'(?i)<!DOCTYPE|<html\b', text)
    if m2 and m2.start() > 0:
        text = text[m2.start():]
    text = re.sub(r'<(p|div|span|li|td)\b[^>]*>[^<]*⚠[^<]*</\1>', '', text, flags=re.IGNORECASE)
    text = re.sub(
        r'<(p|div|span|li|td)\b[^>]*>[^<]*(修正内容|注記|補足|ファクトチェック)[：:][^<]*</\1>',
        '', text, flags=re.IGNORECASE,
    )
    text = re.sub(r'\*\*(修正内容|注記|補足)[：:].*?\*\*', '', text, flags=re.DOTALL)
    text = re.sub(r'<(div|section|blockquote|aside)\b[^>]*>\s*</\1>', '', text, flags=re.IGNORECASE | re.DOTALL)
    return text.strip()


def _strip_continuation(text: str) -> str:
    text = _strip(text)
    if not re.search(r'<[a-zA-Z][^>]{0,100}>', text):
        return ''
    text = re.sub(r'(?i)^\s*<!DOCTYPE[^>]*>\s*', '', text)
    text = re.sub(r'(?i)^\s*<html[^>]*>\s*', '', text)
    text = re.sub(r'(?i)^\s*<head\b.*?</head>\s*', '', text, flags=re.DOTALL)
    text = re.sub(r'(?i)^\s*<body[^>]*>\s*', '', text)
    return text.strip()


def _form_summary(form_data: dict) -> str:
    labels = {
        "project_name": "プロジェクト名", "system_type": "システム種別",
        "industry": "業種・ドメイン", "background": "背景・目的",
        "target_business": "ターゲット（事業上）", "target_users": "ターゲットユーザ",
        "competitors": "競合情報", "budget": "予算規模",
        "roadmap": "ロードマップ", "system_components": "システム構成要素",
        "overview": "プロジェクト概要・課題感", "notes": "特記事項・制約",
    }
    lines = []
    for k, label in labels.items():
        v = form_data.get(k, "")
        if v:
            lines.append(f"- {label}: {v}")
    return "\n".join(lines)


def _approved_context(approved: Dict[str, str]) -> str:
    if not approved:
        return ""
    parts = []
    for key, html in approved.items():
        label = SUB_PHASE_LABELS.get(key, key)
        parts.append(f"### {label}\n{html}")
    return "\n\n".join(parts)


def _edit_block(previous_output: Optional[str], edit_instruction: Optional[str]) -> str:
    if previous_output and edit_instruction:
        return f"\n## 修正指示\n{edit_instruction}\n\n## 前回の出力（修正対象）\n{previous_output}\n\n上記の修正指示に従ってHTMLを修正してください。"
    return ""


# ── Sub-phase handlers ────────────────────────────────────────────────────────

def _why_background(form_data, approved, previous_output, edit_instruction):
    system = f"""あなたは優秀なITコンサルタントです。
「背景と目的の明確化」のレポートHTMLを作成してください。

含める内容:
- 事業課題と現状のペイン（定量的な数値・具体的なエピソードを含む詳細分析）
- As-Is / To-Be ギャップ分析（現状の問題点と理想状態を対比、ギャップの定量化）
- ステークホルダー分析（全関係者・役割・影響度・期待・懸念点）
- KGI / KPI の仮説設定（数値目標・測定方法・達成期限）
- プロジェクト発足の背景と市場機会
- 解決しないリスク（現状維持のコスト）

{HTML_RULES}"""
    user = f"## プロジェクト情報\n{_form_summary(form_data)}{_edit_block(previous_output, edit_instruction)}\n\n背景と目的の明確化レポートHTMLを<!DOCTYPE html>から始めて作成してください。"
    return _run_simple(system, user)


def _count_top_level_sections(html: str) -> int:
    count = 0
    for m in re.finditer(r'<h2\b[^>]*>(.*?)</h2>', html, re.IGNORECASE | re.DOTALL):
        text = re.sub(r'<[^>]+>', '', m.group(1)).strip()
        if re.match(r'\d+[\.．]\s', text):
            count += 1
    return count


def _missing_market_sections(accumulated: str) -> list[str]:
    heading_re = re.compile(r'<h[23]\b[^>]*>(.*?)</h[23]>', re.IGNORECASE | re.DOTALL)
    spans = [(m.start(), m.end(), re.sub(r'<[^>]+>', '', m.group(1)).strip())
             for m in heading_re.finditer(accumulated)]

    sections_with_content = []
    for i, (h_start, h_end, h_text) in enumerate(spans):
        next_h_start = spans[i + 1][0] if i + 1 < len(spans) else len(accumulated)
        sections_with_content.append((h_text, next_h_start - h_end))

    sections = [
        (r'市場規模|成長性',
         '1. 市場規模と成長性（バーチャートは<table class="barchart-data" summary="タイトル">形式で）'),
        (r'グローバル|日本市場', '2. グローバル vs 日本市場比較'),
        (r'競合', '3. 競合サービス・プロダクト分析（各社: 強み・弱み・機能一覧・不足機能・外部連携・公式URL）'),
        (r'トレンド|技術動向|動向', '4. 市場トレンド・技術動向'),
        (r'参入障壁|リスク', '5. 参入障壁・リスク分析'),
        (r'市場機会|成長ドライバー', '6. 市場機会・成長ドライバー'),
    ]
    missing = []
    for pat, label in sections:
        has_body = any(
            re.search(pat, h_text, re.IGNORECASE) and content_len >= 300
            for h_text, content_len in sections_with_content
        )
        if not has_body:
            missing.append(label)
    return missing


def _market_analysis_complete(html: str) -> bool:
    if not re.search(r'</html\s*>', html, re.IGNORECASE):
        print("[market_complete] INCOMPLETE: no </html>")
        return False
    if len(html) < 15000:
        print(f"[market_complete] INCOMPLETE: too short ({len(html)})")
        return False

    heading_re = re.compile(r'<h[23]\b[^>]*>(.*?)</h[23]>', re.IGNORECASE | re.DOTALL)
    spans = [(m.start(), m.end(), re.sub(r'<[^>]+>', '', m.group(1)).strip())
             for m in heading_re.finditer(html)]

    sections_with_content = []
    for i, (h_start, h_end, h_text) in enumerate(spans):
        next_h_start = spans[i + 1][0] if i + 1 < len(spans) else len(html)
        content_len = next_h_start - h_end
        sections_with_content.append((h_text, content_len))

    required = [
        (r'市場規模|成長性', 'section1'),
        (r'グローバル|日本市場', 'section2'),
        (r'競合', 'section3'),
        (r'トレンド|技術動向|動向', 'section4'),
        (r'参入障壁|リスク', 'section5'),
        (r'市場機会|成長ドライバー', 'section6'),
    ]

    for pat, name in required:
        has_body = any(
            re.search(pat, h_text, re.IGNORECASE) and content_len >= 300
            for h_text, content_len in sections_with_content
        )
        if not has_body:
            print(f"[market_complete] INCOMPLETE: {name} has no heading with >=300 chars content")
            return False

    print(f"[market_complete] COMPLETE: all 6 sections have substantive content, len={len(html)}")
    return True


def _why_market(form_data, approved, previous_output, edit_instruction, deep_dive_request=None):
    industry = form_data.get("industry", "")
    competitors = form_data.get("competitors", "")

    queries = [
        f"{industry} 市場規模 成長率 2024 2025",
        f"{industry} 市場規模 億円 予測 レポート",
        f"{industry} 市場トレンド 最新 2024",
        f"{industry} 参入障壁 課題 リスク",
        f"{industry} 市場シェア 競合 比較",
    ]
    if competitors:
        for comp in competitors.replace("、", ",").replace("・", ",").split(","):
            comp = comp.strip()
            if comp:
                queries.append(f"{comp} 機能 料金 強み 弱み 評判 公式サイト")
                queries.append(f"{comp} 外部連携 API 連携システム 統合")
    queries.append(f"{industry} 成長ドライバー 市場機会")
    queries.append(f"{industry} グローバル 日本 市場比較")

    search_section = ""
    for q in queries:
        result = _search(q)
        search_section += f"\n### 検索: {q}\n{result}\n"

    project_info = _form_summary(form_data)

    if deep_dive_request and previous_output:
        extra_results = _search(deep_dive_request)
        system = f"""あなたはHTMLレポート生成ツールです。{HTML_RULES}"""
        user = (
            f"## プロジェクト情報\n{project_info}\n\n"
            f"## 追加深掘り調査リクエスト\n{deep_dive_request}\n\n"
            f"## 追加調査結果\n{extra_results}\n\n"
            f"## 既存レポート（追記対象）\n{previous_output}\n\n"
            "既存HTMLの末尾に「追加深掘り調査結果」セクションを追記した完全なHTMLを返してください。"
            "必ず<!DOCTYPE html>から始め、⚠補足などの注釈は一切含めないこと。"
        )
        return _inject_charts(_run_simple(system, user, model="claude-sonnet-4-6", max_tokens=32000))

    ctx = f"## プロジェクト情報\n{project_info}\n\n## Web調査結果\n{search_section}\n\n"

    s1 = _gen_market_fragment(
        ctx +
        "## 指示\n"
        "「1. 市場規模と成長性」セクションのHTMLを生成してください。\n"
        "<h2>1. 市場規模と成長性</h2> から始めること。\n"
        "含める内容:\n"
        "- 国内・グローバル市場規模（直近3〜5年の推移と今後5年の予測）\n"
        "- CAGR（年平均成長率）\n"
        "- 主要成長セグメント\n"
        "市場規模の推移・予測は必ず <table class=\"barchart-data\" summary=\"タイトル\"> 形式のバーチャートで表示すること。",
        max_tokens=32000,
    )

    s2 = _gen_market_fragment(
        ctx +
        "## 指示\n"
        "「2. グローバル市場 vs 日本市場の比較」セクションのHTMLを生成してください。\n"
        "<h2>2. グローバル市場 vs 日本市場の比較</h2> から始めること。\n"
        "含める内容:\n"
        "- グローバル市場規模と日本市場規模の対比\n"
        "- 地域別市場シェア（北米・欧州・アジア等）\n"
        "- 日本市場の特性・課題・機会\n"
        "比較データは <table class=\"barchart-data\" summary=\"タイトル\"> 形式のバーチャートで表示すること。",
        max_tokens=32000,
    )

    comp_list = competitors or "（競合情報未記入）"
    s3 = _gen_market_fragment(
        ctx +
        "## 指示\n"
        "「3. 競合サービス・プロダクト分析」セクションのHTMLを生成してください。\n"
        "<h2>3. 競合サービス・プロダクト分析</h2> から始めること。\n"
        f"競合情報: {comp_list}\n\n"
        "各競合サービス・製品ごとに以下を全て記載すること（省略禁止）:\n"
        "1. サービス名 + 公式URL（クリッカブルリンク）\n"
        "2. 強み（箇条書き・具体的に）\n"
        "3. 弱み（箇条書き・具体的に）\n"
        "4. 主要機能一覧（表形式）\n"
        "5. 不足機能 / 追加提案候補\n"
        "6. 外部連携システム一覧（連携先・本体→連携先のデータ・連携先→本体のデータ・目的）\n"
        "グローバル競合と国内競合を分けてサブセクション化すること。",
        max_tokens=32000,
    )

    s4 = _gen_market_fragment(
        ctx +
        "## 指示\n"
        "「4. 市場トレンド・技術動向」セクションのHTMLのみを生成してください。\n"
        "<h2>4. 市場トレンド・技術動向</h2> から始めること。\n"
        "含める内容:\n"
        "- 最新市場トレンド（直近2〜3年の動向）\n"
        "- 技術革新・DX・AI活用動向（具体的な技術名・導入事例）\n"
        "- 将来展望（3〜5年後の予測）\n"
        "数値データがあれば <table class=\"barchart-data\" summary=\"タイトル\"> 形式で表示すること。",
        max_tokens=32000,
    )

    s5 = _gen_market_fragment(
        ctx +
        "## 指示\n"
        "「5. 参入障壁・リスク分析」セクションのHTMLのみを生成してください。\n"
        "<h2>5. 参入障壁・リスク分析</h2> から始めること。\n"
        "含める内容:\n"
        "- 規制・法的障壁（具体的な法規制名）\n"
        "- 技術的障壁（開発難易度・必要技術）\n"
        "- 競合強度・市場リスク（定量的に）\n"
        "- リスクマトリクス（発生確率×影響度）\n",
        max_tokens=32000,
    )

    s6 = _gen_market_fragment(
        ctx +
        "## 指示\n"
        "「6. 市場機会・成長ドライバー」セクションのHTMLのみを生成してください。\n"
        "<h2>6. 市場機会・成長ドライバー</h2> から始めること。\n"
        "含める内容:\n"
        "- 未開拓領域・成長機会（具体的なニッチ市場・顧客セグメント）\n"
        "- 成長ドライバー（技術・規制・社会的要因）\n"
        "- 差別化ポイント・推奨アクション\n"
        "数値データがあれば <table class=\"barchart-data\" summary=\"タイトル\"> 形式で表示すること。",
        max_tokens=32000,
    )

    import pathlib
    for i, (label, frag) in enumerate(zip(['s1','s2','s3','s4','s5','s6'], [s1,s2,s3,s4,s5,s6]), 1):
        barchart_count = frag.count('class="barchart-data"') + frag.count("class='barchart-data'")
        print(f"[why_market] {label} len={len(frag)} barchart_tables={barchart_count} preview={frag[:200]!r}")
        pathlib.Path(f'/tmp/debug_{label}.html').write_text(frag)

    html = _build_market_report_html(industry, s1, s2, s3, s4, s5, s6)
    pathlib.Path('/tmp/debug_market_raw.html').write_text(html)
    print(f"[why_market] raw html saved to /tmp/debug_market_raw.html (before chart injection)")

    result = _inject_charts(html)
    barchart_in_result = result.count('linear-gradient')
    print(f"[why_market] final html len={len(result)} h2_count={result.count('<h2')} bar_count={barchart_in_result}")

    pathlib.Path('/tmp/debug_market.html').write_text(result)
    print("[why_market] final HTML saved to /tmp/debug_market.html")

    return result


def _why_business_model(form_data, approved, previous_output, edit_instruction):
    system = f"""あなたは事業戦略・ビジネスモデル設計の専門家です。
「ビジネスモデル・収益化」のレポートHTMLを作成してください。

含める内容:
- 推奨マネタイズ手法（複数案の詳細比較・選定理由・実装難易度）
- KGI / KPI の仮説（MAU・CVR・LTV・ARPU・CAC等、目標値と根拠）
- 収益モデルの詳細シミュレーション（3年間の収益予測）
- 初期投資とランニングコストの概算（フェーズ別）
- 損益分岐点の予測（シナリオ別：楽観・中立・悲観）
- 競合のマネタイズ手法との比較
- グロースハック戦略

{HTML_RULES}"""
    ctx = _approved_context(approved)
    user = f"## プロジェクト情報\n{_form_summary(form_data)}\n\n## 承認済み分析結果\n{ctx}{_edit_block(previous_output, edit_instruction)}\n\nビジネスモデル・収益化レポートHTMLを<!DOCTYPE html>から始めて作成してください。"
    return _run_simple(system, user)


def _who_persona(form_data, approved, previous_output, edit_instruction):
    system = f"""あなたはUXリサーチャーです。
「ペルソナ定義とユーザー理解」のレポートHTMLを作成してください。

## ペルソナ数：3名のみ（超過禁止）

## 各ペルソナに含める内容（3名分）:
- 基本プロフィール（氏名・年齢・職業・ITリテラシー・一日の流れ）
- ペインポイント（5項目以上、具体的なエピソードを含む）
- ゲインポイント（5項目以上、実現したい状態を具体的に）
- 代表的なコメント（実際に言いそうな言葉を2〜3文）

## 共通セクション（ペルソナの後に1回だけ）:
- Pain/Gain分析マトリクス（表形式、全ペルソナ横断）
- 主要利用シナリオ（3シナリオ・各詳細に）
- ユーザーインサイトのまとめ

{HTML_RULES}"""
    ctx = _approved_context(approved)
    user = f"## プロジェクト情報\n{_form_summary(form_data)}\n\n## 承認済み分析結果\n{ctx}{_edit_block(previous_output, edit_instruction)}\n\nペルソナ定義レポートHTMLを<!DOCTYPE html>から始めて作成してください。"
    return _run_simple(system, user)


def _who_value(form_data, approved, previous_output, edit_instruction):
    system = f"""あなたはプロダクトマネージャーです。
「提供価値（バリュープロポジション）」のレポートHTMLを作成してください。

含める内容:
- バリュープロポジションキャンバス（ペイン・ゲイン・プロダクト、各項目を詳細に）
- 競合との差別化ポイント（5点以上、具体的な根拠を含む）
- USP（ユニークセリングポイント）の言語化（複数のメッセージ案）
- 提供価値の定量的インパクト試算
- 顧客セグメント別の価値提案
{HTML_RULES}"""
    ctx = _approved_context(approved)
    user = f"## プロジェクト情報\n{_form_summary(form_data)}\n\n## 承認済み分析結果\n{ctx}{_edit_block(previous_output, edit_instruction)}\n\n提供価値レポートHTMLを<!DOCTYPE html>から始めて作成してください。"
    return _run_simple(system, user)


def _who_ux(form_data, approved, previous_output, edit_instruction):
    system = f"""あなたはUXデザイナーです。
「UX設計・カスタマージャーニー」のレポートHTMLを作成してください。

含める内容:
- カスタマージャーニーマップ（認知→検討→利用開始→継続→推奨の5フェーズ、表形式・各フェーズの感情・思考・行動を記載）
- 主要タッチポイントと改善機会（全タッチポイントを洗い出す）
- Web/スマホ別UX方針（各詳細に）
- オンボーディング設計（ステップ別の詳細設計）
- ユーザーの離脱リスクと対策
- アクセシビリティ対応方針

{HTML_RULES}"""
    ctx = _approved_context(approved)
    user = f"## プロジェクト情報\n{_form_summary(form_data)}\n\n## 承認済み分析結果\n{ctx}{_edit_block(previous_output, edit_instruction)}\n\nUX設計レポートHTMLを<!DOCTYPE html>から始めて作成してください。"
    return _run_simple(system, user)


def _what_features(form_data, approved, previous_output, edit_instruction):
    system = f"""あなたはプロダクトマネージャーです。
「機能洗い出しと優先順位付け」のレポートHTMLを作成してください。

含める内容:
- 機能リスト（コア・周辺・将来に分類、各機能の説明・対象ユーザー・優先度を含む）
- MoSCoW優先順位付け（全機能を分類し理由も記載）
- MVP の定義（含む機能・含まない機能・判断理由）
- フェーズ別リリース計画（3フェーズ・各フェーズの目標・期間・KPI）
- 機能間の依存関係
- 技術的実装難易度評価

{HTML_RULES}"""
    ctx = _approved_context(approved)
    user = f"## プロジェクト情報\n{_form_summary(form_data)}\n\n## 承認済み分析結果\n{ctx}{_edit_block(previous_output, edit_instruction)}\n\n機能定義レポートHTMLを<!DOCTYPE html>から始めて作成してください。"
    return _run_simple(system, user)


def _what_ia(form_data, approved, previous_output, edit_instruction):
    system = f"""あなたはUI/UXデザイナーです。
「情報設計とUIの方向性」のレポートHTMLを作成してください。

含める内容:
- サイトマップ / 主要画面一覧（全画面の階層構造）
- 主要画面遷移図（テキストベース・各画面の役割説明）
- UIトーン&マナー（カラーパレット・フォント・スペーシング方針・ブランドイメージ）
- レスポンシブ対応方針（PC・タブレット・スマホの各対応）
- コンポーネント設計方針
- デザインシステムの概要
{HTML_RULES}"""
    ctx = _approved_context(approved)
    user = f"## プロジェクト情報\n{_form_summary(form_data)}\n\n## 承認済み分析結果\n{ctx}{_edit_block(previous_output, edit_instruction)}\n\n情報設計・UI方向性レポートHTMLを<!DOCTYPE html>から始めて作成してください。"
    return _run_simple(system, user)


def _what_nonfunc(form_data, approved, previous_output, edit_instruction):
    system = f"""あなたはシステムアーキテクトです。
「非機能要件の方向性」のレポートHTMLを作成してください。

含める内容:
- 性能要件（レスポンスタイム・スループット・同時接続数・具体的な数値目標）
- 可用性要件（SLA・RTO/RPO・冗長化方針）
- セキュリティ要件（認証・認可・暗号化・脆弱性対策）
- スケーラビリティ方針（水平/垂直スケーリング・ボトルネック分析）
- 運用・保守方針（監視・ログ・アラート・障害対応フロー）
- バックアップ・DR（災害復旧）方針
- コンプライアンス・監査要件
{HTML_RULES}"""
    ctx = _approved_context(approved)
    user = f"## プロジェクト情報\n{_form_summary(form_data)}\n\n## 承認済み分析結果\n{ctx}{_edit_block(previous_output, edit_instruction)}\n\n非機能要件レポートHTMLを<!DOCTYPE html>から始めて作成してください。"
    return _run_simple(system, user)


def _how_platform(form_data, approved, previous_output, edit_instruction):
    system = f"""あなたはシステムアーキテクトです。
「プラットフォームとアーキテクチャ」のレポートHTMLを作成してください。

含める内容:
- 推奨プラットフォームと選定理由（複数案の比較評価マトリクス）
- システムアーキテクチャ概要（テキストベース図・各コンポーネントの役割）
- 技術スタック候補（FE・BE・DB・インフラ・CI/CD・各選定理由）
- インフラ構成の方向性（クラウドサービス比較・コスト試算）
- APIアーキテクチャ方針（REST/GraphQL等）
- マイクロサービスvsモノリス判断
{HTML_RULES}"""
    ctx = _approved_context(approved)
    user = f"## プロジェクト情報\n{_form_summary(form_data)}\n\n## 承認済み分析結果\n{ctx}{_edit_block(previous_output, edit_instruction)}\n\nプラットフォーム・アーキテクチャレポートHTMLを<!DOCTYPE html>から始めて作成してください。"
    return _run_simple(system, user)


def _how_feasibility(form_data, approved, previous_output, edit_instruction):
    system = f"""あなたは技術調査の専門家です。
提供されたWeb調査結果をもとに「技術的実現可能性（フィジビリティ）」を分析し、詳細なレポートHTMLを作成してください。

含める内容:
- 必要な技術要素と成熟度
- 各技術の実装難易度・リスク評価
- PoC（概念実証）が必要な技術要素の特定
- オープンソース・ライブラリ・SDKの調査
- 技術的リスクと対策
{HTML_RULES}"""

    system_type = form_data.get("system_type", "")
    queries = [
        f"{system_type} 開発 技術スタック 2024",
        f"{system_type} フレームワーク ライブラリ",
    ]
    search_section = ""
    for q in queries:
        result = _search(q)
        search_section += f"\n### 検索: {q}\n{result}\n"

    ctx = _approved_context(approved)
    user = f"## プロジェクト情報\n{_form_summary(form_data)}\n\n## 承認済み分析結果\n{ctx}\n\n## Web調査結果\n{search_section}{_edit_block(previous_output, edit_instruction)}\n\n技術実現可能性レポートHTMLを<!DOCTYPE html>から始めて作成してください。"
    return _run_simple(system, user, model="claude-sonnet-4-6", max_tokens=32000)


def _how_integration(form_data, approved, previous_output, edit_instruction):
    system = f"""あなたはシステムインテグレーションの専門家です。
「外部連携とデータ」のレポートHTMLを作成してください。

含める内容:
- 外部API・システム連携一覧（全件・各サービスの役割・コスト・認証方式）
- データフロー概要（入力→処理→出力の全経路）
- 主要データモデル（エンティティと関係性・ER図相当）
- API設計方針（エンドポイント設計例・バージョニング・エラーハンドリング）
- データ品質・整合性管理方針
- GDPR/個人情報保護法対応のデータ管理
{HTML_RULES}"""
    ctx = _approved_context(approved)
    user = f"## プロジェクト情報\n{_form_summary(form_data)}\n\n## 承認済み分析結果\n{ctx}{_edit_block(previous_output, edit_instruction)}\n\n外部連携・データ設計レポートHTMLを<!DOCTYPE html>から始めて作成してください。"
    return _run_simple(system, user)


def _project_schedule(form_data, approved, previous_output, edit_instruction):
    system = f"""あなたはプロジェクトマネージャーです。
「スケジュールとマイルストーン」のレポートHTMLを作成してください。

含める内容:
- フェーズ別スケジュール（要件定義→設計→開発→テスト→リリース・各フェーズの期間・タスク・担当）
- 主要マイルストーンと成果物（具体的な完了条件付き）
- フェーズ2・3の中長期ロードマップ（3年間の計画）
- クリティカルパス分析（遅延リスクの高いタスクと対策）
- リスク管理計画（リスク一覧・発生確率・影響度・対応策）
- ガントチャート相当の視覚的スケジュール表示

{HTML_RULES}"""
    ctx = _approved_context(approved)
    user = f"## プロジェクト情報\n{_form_summary(form_data)}\n\n## 承認済み分析結果\n{ctx}{_edit_block(previous_output, edit_instruction)}\n\nスケジュール・マイルストーンレポートHTMLを<!DOCTYPE html>から始めて作成してください。"
    return _run_simple(system, user)


def _project_budget(form_data, approved, previous_output, edit_instruction):
    system = f"""あなたはITプロジェクトのコンサルタントです。
「予算と体制」のレポートHTMLを作成してください。

含める内容:
- 初期開発費の内訳概算（工程別・役割別の詳細内訳、工数・単価・費用）
- ランニングコスト概算（インフラ・ライセンス・人件費・月次/年次）
- 開発体制案（役割・人数・スキル要件・採用/外注判断）
- コスト最適化のポイント（クラウド・OSS活用・フェーズ分割）
- ROI分析（投資回収期間・期待収益シナリオ）
- 予算リスクと予備費の考え方

{HTML_RULES}"""
    ctx = _approved_context(approved)
    user = f"## プロジェクト情報\n{_form_summary(form_data)}\n\n## 承認済み分析結果\n{ctx}{_edit_block(previous_output, edit_instruction)}\n\n予算・体制レポートHTMLを<!DOCTYPE html>から始めて作成してください。"
    return _run_simple(system, user)


def _project_legal(form_data, approved, previous_output, edit_instruction):
    system = f"""あなたは法務・コンプライアンスの専門家です。
「法務・コンプライアンス」のレポートHTMLを作成してください。

含める内容:
- 関連法規チェックリスト（全関連法規・各法規の対応要否・対応方法）
- 業界特有の規制・ガイドライン・認証（取得要否と手順）
- プライバシーポリシー・利用規約の必要事項（具体的な条文例）
- 法的リスク優先度マトリクス（リスク別の影響度・発生確率・対策）
- 知的財産権・ライセンス管理
- 契約・SLA設計の要点
{HTML_RULES}"""
    ctx = _approved_context(approved)
    user = f"## プロジェクト情報\n{_form_summary(form_data)}\n\n## 承認済み分析結果\n{ctx}{_edit_block(previous_output, edit_instruction)}\n\n法務・コンプライアンスレポートHTMLを<!DOCTYPE html>から始めて作成してください。"
    return _run_simple(system, user)


# ── Requirements Definition handlers ─────────────────────────────────────────

def _req_business(form_data, approved, previous_output, edit_instruction):
    system = f"""あなたはITプロジェクトの要件定義専門コンサルタントです。
「事業・業務要件」の要件定義レポートHTMLを作成してください。

含める内容:
- 目的・ゴールの再確認（KGI/KPI確定・成功/失敗判断基準）
- 現行業務の整理（現在のフロー・使用ツール・課題：二重入力/承認待ち/属人化/転記ミス等）
- 新業務フローの定義（To-Be・人とシステムの切り分け・自動化範囲・承認フロー）
- 業務ルールの定義（申込条件・予約条件・ポイント付与・会員ランク・通知・承認条件等）
- 業務範囲・対象範囲の確定（対象店舗/部署/ユーザー/商品・対象外業務・STEP1/2の切り分け）

{HTML_RULES}"""
    ctx = _approved_context(approved)
    user = f"## プロジェクト情報\n{_form_summary(form_data)}\n\n## 承認済み企画フェーズ結果\n{ctx}{_edit_block(previous_output, edit_instruction)}\n\n事業・業務要件の要件定義レポートHTMLを<!DOCTYPE html>から始めて作成してください。"
    return _run_simple(system, user, model="claude-sonnet-4-6", max_tokens=32000)


def _req_stakeholders(form_data, approved, previous_output, edit_instruction):
    system = f"""あなたはITプロジェクトの要件定義専門コンサルタントです。
「ステークホルダー・利用者要件」の要件定義レポートHTMLを作成してください。

含める内容:
- 利用者種別の整理（一般/会員/非会員ユーザー・店舗スタッフ・本部担当者・管理者・CS担当者・外部パートナー等）
- 権限・ロールの定義（閲覧/登録/編集/削除/承認できる情報と操作・店舗別/部署別/全社権限差）
- ユーザー行動の具体化（初回/通常/再訪時・離脱/問い合わせ発生ポイント・スマホ/PC/利用シーン別差異）
- ユーザー課題の再整理（操作難解・入力負荷・情報発見困難・通知過多・管理者の確認工数等）
- 利用環境の定義（対象端末/OS/ブラウザ・ネットワーク環境・高齢者/低ITリテラシー配慮・アクセシビリティ）

{HTML_RULES}"""
    ctx = _approved_context(approved)
    user = f"## プロジェクト情報\n{_form_summary(form_data)}\n\n## 承認済み企画フェーズ結果\n{ctx}{_edit_block(previous_output, edit_instruction)}\n\nステークホルダー・利用者要件レポートHTMLを<!DOCTYPE html>から始めて作成してください。"
    return _run_simple(system, user, model="claude-sonnet-4-6", max_tokens=32000)


def _req_functional(form_data, approved, previous_output, edit_instruction):
    system = f"""あなたはITプロジェクトの要件定義専門コンサルタントです。
「機能要件」の要件定義レポートHTMLを作成してください。

含める内容:
- 機能一覧（ユーザー向け/管理者向け/バッチ/通知/外部連携/集計分析機能）
- 機能ごとの詳細定義（機能名・概要・利用者・利用シーン・入力/表示項目・処理内容・正常系/異常系・完了条件）
- 優先順位付け（Must/Should/Could/Won't・MVP定義・2027年以降追加候補）
- 画面単位の機能整理（ログイン/会員登録/マイページ/商品一覧/予約/決済/管理画面等の機能）
- CRUD整理（Create/Read/Update/Delete・論理/物理削除・履歴保持要否）
- 検索・絞り込み・並び替え要件
- 通知・配信要件（メール/プッシュ/SMS/LINE/アプリ内通知・タイミング・対象者・配信予約・停止）

{HTML_RULES}"""
    ctx = _approved_context(approved)
    user = f"## プロジェクト情報\n{_form_summary(form_data)}\n\n## 承認済み企画フェーズ結果\n{ctx}{_edit_block(previous_output, edit_instruction)}\n\n機能要件レポートHTMLを<!DOCTYPE html>から始めて作成してください。"
    return _run_simple(system, user, model="claude-sonnet-4-6", max_tokens=32000)


def _req_ui_ux(form_data, approved, previous_output, edit_instruction):
    system = f"""あなたはUI/UXデザイナー兼要件定義コンサルタントです。
「画面・UI/UX要件」の要件定義レポートHTMLを作成してください。

含める内容:
- 画面一覧（ユーザー画面/管理画面/スタッフ画面/エラー画面/通知テンプレート/モーダル一覧）
- 画面遷移の定義（初回アクセス〜完了・ログイン前後・会員登録後・エラー時/ブラウザバック/セッション切れ時の挙動）
- ワイヤーフレーム（各画面構成・ヘッダー/フッター/メニュー・ボタン/フォーム配置・スマホ画面での見やすさ）
- 入力フォーム要件（項目・必須/任意・入力形式・文字数制限・バリデーション・エラーメッセージ・確認画面要否）
- UIルールの定義（ボタン文言・ステータス表示・色/アイコン使用・ローディング/トースト/モーダル・データなし時表示）
- UX上の重要ポイント（最短操作・入力項目数・次の行動導線・管理者の情報到達・スマホ片手操作・ショートカット）

{HTML_RULES}"""
    ctx = _approved_context(approved)
    user = f"## プロジェクト情報\n{_form_summary(form_data)}\n\n## 承認済み企画フェーズ結果\n{ctx}{_edit_block(previous_output, edit_instruction)}\n\n画面・UI/UX要件レポートHTMLを<!DOCTYPE html>から始めて作成してください。"
    return _run_simple(system, user, model="claude-sonnet-4-6", max_tokens=32000)


def _req_data(form_data, approved, previous_output, edit_instruction):
    system = f"""あなたはデータアーキテクト兼要件定義コンサルタントです。
「データ・情報設計要件」の要件定義レポートHTMLを作成してください。

含める内容:
- 管理対象データの洗い出し（ユーザー/会員/商品/店舗/予約/注文/決済/ポイント/クーポン/問い合わせ/ログ/権限情報）
- データ項目定義（項目名・型・桁数・必須/任意・初期値・一意制約・暗号化/マスキング要否・保存期間・削除条件）
- データ関係の整理（ユーザー⇔予約・会員⇔ポイント・店舗⇔スタッフ・注文⇔決済・親子/多対多関係）
- マスタデータの定義（店舗/商品/カテゴリ/権限/ステータス/通知テンプレートマスタ・更新者・タイミング）
- データ移行要件（既存データ有無・移行対象・移行元形式・タイミング・リハーサル要否・クレンジング・確認方法）
- ログ・履歴要件（ログイン/操作/更新/通知/決済/エラー/監査ログ・保存期間）

{HTML_RULES}"""
    ctx = _approved_context(approved)
    user = f"## プロジェクト情報\n{_form_summary(form_data)}\n\n## 承認済み企画フェーズ結果\n{ctx}{_edit_block(previous_output, edit_instruction)}\n\nデータ・情報設計要件レポートHTMLを<!DOCTYPE html>から始めて作成してください。"
    return _run_simple(system, user, model="claude-sonnet-4-6", max_tokens=32000)


def _req_integration(form_data, approved, previous_output, edit_instruction):
    system = f"""あなたはシステムインテグレーション専門の要件定義コンサルタントです。
「外部連携・API要件」の要件定義レポートHTMLを作成してください。

含める内容:
- 連携先システムの整理（基幹/CRM/POS/EC/決済代行/会員管理/地図API/SNSログイン/MA/BI/LINE/Slack/GA/Firebase等）
- 連携方式の定義（API/CSV/バッチ/Webhook/SFTP/手動アップロード・リアルタイム/日次/月次連携）
- 連携データの定義（送受信データ・データ形式JSON/XML/CSV・文字コード・タイムゾーン・ID連携ルール・エラー再送条件）
- 認証・認可方式（APIキー/OAuth2.0/OpenID Connect/Basic認証/IP制限/クライアント証明書・トークン有効期限・権限スコープ）
- 外部サービス依存リスク（API仕様変更・料金改定・レート制限・障害時代替手段・審査要件・契約主体）

{HTML_RULES}"""
    ctx = _approved_context(approved)
    user = f"## プロジェクト情報\n{_form_summary(form_data)}\n\n## 承認済み企画フェーズ結果\n{ctx}{_edit_block(previous_output, edit_instruction)}\n\n外部連携・API要件レポートHTMLを<!DOCTYPE html>から始めて作成してください。"
    return _run_simple(system, user, model="claude-sonnet-4-6", max_tokens=32000)


def _req_nonfunc(form_data, approved, previous_output, edit_instruction):
    system = f"""あなたはシステムアーキテクト兼要件定義コンサルタントです。
「非機能要件」の要件定義レポートHTMLを作成してください（数値・条件で合意できる形で記述）。

含める内容:
- 性能要件（想定同時/月間アクセス数・画面表示速度・検索/CSV出力/バッチ処理時間・ピーク時間帯・大量データ挙動）
- 可用性要件（サービス提供時間・メンテナンス可能時間・年間稼働率・障害復旧目標RTO/RPO・冗長化・バックアップ頻度）
- 拡張性要件（店舗追加・会員数増加・多言語対応・機能/連携追加・複数ブランド展開）
- 保守性要件（ソースコード管理・環境分離dev/stg/prod・ログ確認・監視・設定変更のしやすさ・引き継ぎ資料）
- 互換性要件（対応ブラウザ/OS/端末・最低サポートバージョン・アプリストア審査要件）
- ユーザビリティ要件（完了までのステップ数・入力補助・エラー文言・管理画面一覧性・初見ユーザー導線）

{HTML_RULES}"""
    ctx = _approved_context(approved)
    user = f"## プロジェクト情報\n{_form_summary(form_data)}\n\n## 承認済み企画フェーズ結果\n{ctx}{_edit_block(previous_output, edit_instruction)}\n\n非機能要件レポートHTMLを<!DOCTYPE html>から始めて作成してください。"
    return _run_simple(system, user, model="claude-sonnet-4-6", max_tokens=32000)


def _req_security(form_data, approved, previous_output, edit_instruction):
    system = f"""あなたはセキュリティ・法務専門の要件定義コンサルタントです。
「セキュリティ・法務・コンプライアンス要件」の要件定義レポートHTMLを作成してください。

含める内容:
- 認証要件（ID/PW・メール/SMS認証・SNSログイン・SSO・多要素認証・PW強度ポリシー・ログイン失敗制御・セッション管理）
- 認可要件（ロール別権限・画面/API/データ単位アクセス制御・管理者権限分離・退職者権限削除）
- 個人情報保護（取得情報・利用目的・同意取得・PP/利用規約・第三者提供・委託先管理・削除/開示請求対応）
- 機密情報保護（暗号化・マスキング・アクセスログ・ダウンロード制御・IP制限・本番データ取り扱い）
- 脆弱性対策（SQLインジェクション/XSS/CSRF対策・認証突破対策・ファイルアップロード制御・API不正利用対策）
- 関連法令・規約（個人情報保護法・特商法・資金決済法・景表法・電気通信事業法・業界別ガイドライン・AppStore/Google Playポリシー）

{HTML_RULES}"""
    ctx = _approved_context(approved)
    user = f"## プロジェクト情報\n{_form_summary(form_data)}\n\n## 承認済み企画フェーズ結果\n{ctx}{_edit_block(previous_output, edit_instruction)}\n\nセキュリティ・法務・コンプライアンス要件レポートHTMLを<!DOCTYPE html>から始めて作成してください。"
    return _run_simple(system, user, model="claude-sonnet-4-6", max_tokens=32000)


def _req_operation(form_data, approved, previous_output, edit_instruction):
    system = f"""あなたは運用設計専門の要件定義コンサルタントです。
「運用・保守要件」の要件定義レポートHTMLを作成してください。

含める内容:
- 運用体制（運用責任者・問い合わせ窓口・障害一次対応者・保守/コンテンツ/マスタ/権限管理担当）
- 管理画面で対応する業務（ユーザー/店舗/商品/予約/注文/お知らせ/クーポン/ポイント/問い合わせ/CSV出力/権限管理）
- 問い合わせ対応（経路・管理者通知方法・ステータス・回答履歴・FAQ連携・エスカレーション・対応期限）
- 障害対応（検知方法・通知先・初動手順・復旧手順・顧客告知方法・障害報告書・再発防止管理）
- 保守・改修運用（軽微改修受付・仕様変更判断基準・追加見積条件・月次定例・SLA・バージョンアップ方針）

{HTML_RULES}"""
    ctx = _approved_context(approved)
    user = f"## プロジェクト情報\n{_form_summary(form_data)}\n\n## 承認済み企画フェーズ結果\n{ctx}{_edit_block(previous_output, edit_instruction)}\n\n運用・保守要件レポートHTMLを<!DOCTYPE html>から始めて作成してください。"
    return _run_simple(system, user, model="claude-sonnet-4-6", max_tokens=32000)


def _req_testing(form_data, approved, previous_output, edit_instruction):
    system = f"""あなたはQAエンジニア兼要件定義コンサルタントです。
「テスト・受入基準」の要件定義レポートHTMLを作成してください。

含める内容:
- テスト方針（単体/結合/システム/受入/回帰/性能/セキュリティ/端末/ブラウザ検証）
- 受入条件（主要機能動作・必須業務フロー完了・管理者データ確認・エラー表示・指定ブラウザ/端末・性能・セキュリティ）
- テスト観点の整理（正常系/異常系/境界値/権限別/データなし/大量データ/通信エラー/外部APIエラー/決済失敗/セッション切れ/二重送信）
- 受入テストの役割分担（ベンダー/発注者/業務担当者/管理者/店舗担当・不具合報告・修正確認方法）
- 検収条件（納品物一覧・検収期間・不具合重大度分類・検収対象外条件・仕様変更扱い条件・リリース判定会議）

{HTML_RULES}"""
    ctx = _approved_context(approved)
    user = f"## プロジェクト情報\n{_form_summary(form_data)}\n\n## 承認済み企画フェーズ結果\n{ctx}{_edit_block(previous_output, edit_instruction)}\n\nテスト・受入基準レポートHTMLを<!DOCTYPE html>から始めて作成してください。"
    return _run_simple(system, user, model="claude-sonnet-4-6", max_tokens=32000)


def _req_release(form_data, approved, previous_output, edit_instruction):
    system = f"""あなたはリリースマネジメント専門の要件定義コンサルタントです。
「リリース・移行要件」の要件定義レポートHTMLを作成してください。

含める内容:
- リリース方式（一括/段階/店舗別/ユーザー限定/β版・旧システム並行稼働・STEP1/2の切り分け）
- 本番切替（切替日時・メンテナンス時間・旧システム停止・データ移行タイミング・DNS切替・アプリ公開申請・審査期間・判定基準）
- リリース前準備（管理者アカウント作成・初期マスタ登録・通知文面・FAQ整備・マニュアル・社内説明会・現場トレーニング・問い合わせ窓口）
- リリース後対応（初期監視・問い合わせ増加対応・障害即時対応・アクセス/利用状況確認・改善要望収集・初回改善リリース予定）

{HTML_RULES}"""
    ctx = _approved_context(approved)
    user = f"## プロジェクト情報\n{_form_summary(form_data)}\n\n## 承認済み企画フェーズ結果\n{ctx}{_edit_block(previous_output, edit_instruction)}\n\nリリース・移行要件レポートHTMLを<!DOCTYPE html>から始めて作成してください。"
    return _run_simple(system, user, model="claude-sonnet-4-6", max_tokens=32000)


def _req_management(form_data, approved, previous_output, edit_instruction):
    system = f"""あなたはプロジェクトマネージャー兼要件定義コンサルタントです。
「プロジェクト管理・合意形成」の要件定義レポートHTMLを作成してください。

含める内容:
- ステークホルダー整理（決裁者・業務責任者・現場担当者・情シス・法務・マーケ・CS・ベンダーPM・デザイナー・エンジニア）
- 会議体の設計（定例会・業務ヒアリング・画面レビュー・技術確認会・法務確認会・リリース判定会・課題管理会議）
- 課題・リスク管理（未決事項・確認事項・仕様/技術/外部連携/スケジュール/法務/運用リスク・意思決定待ち事項）
- 仕様変更管理（要件定義中/基本設計後/開発着手後の変更・追加見積対象・スケジュール影響・変更履歴管理）
- 成果物の定義（要件定義書・業務フロー・機能一覧・画面一覧・画面遷移図・ワイヤーフレーム・データ項目定義・外部連携一覧・非機能要件一覧・権限一覧・テスト方針・移行方針・課題管理表・見積前提条件）

{HTML_RULES}"""
    ctx = _approved_context(approved)
    user = f"## プロジェクト情報\n{_form_summary(form_data)}\n\n## 承認済み企画フェーズ結果\n{ctx}{_edit_block(previous_output, edit_instruction)}\n\nプロジェクト管理・合意形成レポートHTMLを<!DOCTYPE html>から始めて作成してください。"
    return _run_simple(system, user, model="claude-sonnet-4-6", max_tokens=32000)


# ── Sub-phase chat (any sub-phase) ────────────────────────────────────────────

def chat(
    sub_phase_key: str,
    form_data: dict,
    approved_outputs: Dict[str, str],
    existing_html: Optional[str],
    message: str,
    append_to_report: bool = False,
) -> dict:
    """Search + answer a question about a sub-phase report.
    If append_to_report=True, appends the research result as a new section in existing_html.
    Returns {"answer": str, "html": str|None}.
    """
    search_result = _search(message)

    answer_system = (
        "あなたは優秀なITコンサルタントです。プロジェクトに関する質問にWeb調査結果を元に詳しく回答してください。"
        "回答は日本語・具体的・数値や根拠を含む内容にしてください。"
    )
    answer_user = (
        f"## プロジェクト情報\n{_form_summary(form_data)}\n\n"
        f"## 質問\n{message}\n\n"
        f"## Web調査結果\n{search_result}"
    )
    client = _get_anthropic()
    ans_resp = _create_with_retry(
        client, model="claude-sonnet-4-6", max_tokens=4000,
        system=answer_system,
        messages=[{"role": "user", "content": answer_user}],
    )
    answer = ans_resp.content[0].text.strip()

    if not append_to_report or not existing_html:
        return {"answer": answer, "html": None}

    # Build an HTML section for the appended content
    section_system = (
        "あなたはHTMLコンテンツ生成ツールです。"
        "質問・回答・調査結果から、レポートに追記するHTMLセクションを生成してください。"
        "出力はHTMLコードのみ（<section>タグから始める）。<!DOCTYPE>等の文書構造は不要。"
        "Tailwind CSSクラスを使用してください。数値データがあれば barchart-data 形式のテーブルで表示。"
    )
    section_user = (
        f"以下の内容をTailwind CSSスタイルのHTMLセクションに変換してください:\n\n"
        f"## 質問\n{message}\n\n"
        f"## 回答・調査結果\n{answer}\n\n"
        "出典URLがあれば <a href='URL' target='_blank' class='text-xs text-blue-500 underline ml-1'>出典</a> 形式でインラインに含めてください。\n"
        "<section class='mt-8 pt-8 border-t-2 border-blue-200'> から始めてください。"
    )
    sec_resp = _create_with_retry(
        client, model="claude-haiku-4-5-20251001", max_tokens=4000,
        system=section_system,
        messages=[{"role": "user", "content": section_user}],
    )
    section_html = sec_resp.content[0].text.strip()

    # Strip any document boilerplate the model may have added
    section_html = re.sub(r'(?i)<!DOCTYPE[^>]*>\s*', '', section_html)
    section_html = re.sub(r'(?i)<html[^>]*>\s*', '', section_html)
    section_html = re.sub(r'(?i)<head\b.*?</head>\s*', '', section_html, flags=re.DOTALL)
    section_html = re.sub(r'(?i)<body[^>]*>\s*', '', section_html)
    section_html = re.sub(r'(?i)\s*</body>\s*</html>\s*$', '', section_html)

    wrapped = (
        f'\n\n<div class="mx-auto max-w-5xl px-8">\n'
        f'<p class="text-xs text-blue-400 mb-1">📝 追加調査結果</p>\n'
        f'{section_html.strip()}\n</div>'
    )

    if re.search(r'</body>', existing_html, re.IGNORECASE):
        new_html = re.sub(r'(?i)</body>', wrapped + '\n</body>', existing_html, count=1)
    else:
        new_html = existing_html + wrapped

    new_html = _inject_charts(new_html)
    return {"answer": answer, "html": new_html}


# ── Public entry point ────────────────────────────────────────────────────────

_HANDLERS = {
    "why_background": _why_background,
    "why_market": _why_market,
    "why_business_model": _why_business_model,
    "who_persona": _who_persona,
    "who_value": _who_value,
    "who_ux": _who_ux,
    "what_features": _what_features,
    "what_ia": _what_ia,
    "what_nonfunc": _what_nonfunc,
    "how_platform": _how_platform,
    "how_feasibility": _how_feasibility,
    "how_integration": _how_integration,
    "project_schedule": _project_schedule,
    "project_budget": _project_budget,
    "project_legal": _project_legal,
    "req_business": _req_business,
    "req_stakeholders": _req_stakeholders,
    "req_functional": _req_functional,
    "req_ui_ux": _req_ui_ux,
    "req_data": _req_data,
    "req_integration": _req_integration,
    "req_nonfunc": _req_nonfunc,
    "req_security": _req_security,
    "req_operation": _req_operation,
    "req_testing": _req_testing,
    "req_release": _req_release,
    "req_management": _req_management,
}


def continue_from_truncation(truncated_html: str) -> str:
    """Close open HTML tags in a truncated document — no new content generated."""
    base = re.sub(r'\s*</body>\s*</html>\s*$', '', truncated_html.rstrip(), flags=re.IGNORECASE).rstrip()
    tail = base[-2000:]

    response = _create_with_retry(
        _get_anthropic(),
        model="claude-haiku-4-5-20251001",
        max_tokens=600,
        system="""You close truncated HTML documents.
Output ONLY the closing tags needed to make the document valid HTML.
Rules:
- NO new paragraphs, headings, list items, tables, or any content
- NO new research, data, analysis, or descriptions
- If a word/sentence is cut off, add "..." to end it, then close tags
- End with </body></html>
- Output should be 5-30 lines of closing tags maximum""",
        messages=[{"role": "user", "content": f"Close this truncated HTML:\n\n{tail}"}],
    )
    closing = response.content[0].text.strip()
    if closing.startswith("```html"):
        closing = closing[7:]
    elif closing.startswith("```"):
        closing = closing[3:]
    if closing.endswith("```"):
        closing = closing[:-3]
    return base + "\n" + closing.strip()


def run(
    sub_phase_key: str,
    form_data: dict,
    approved_outputs: Dict[str, str],
    previous_output: Optional[str] = None,
    edit_instruction: Optional[str] = None,
    deep_dive_request: Optional[str] = None,
) -> str:
    handler = _HANDLERS.get(sub_phase_key)
    if not handler:
        raise ValueError("Unknown sub_phase_key: {}".format(sub_phase_key))

    if sub_phase_key == "why_market":
        return handler(form_data, approved_outputs, previous_output, edit_instruction, deep_dive_request)
    return _inject_charts(handler(form_data, approved_outputs, previous_output, edit_instruction))
