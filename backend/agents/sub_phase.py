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


def _get_anthropic():
    return anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))


def _get_tavily():
    return TavilyClient(api_key=os.environ.get("TAVILY_API_KEY", ""))


def _search(query: str) -> str:
    try:
        result = _get_tavily().search(query=query, max_results=5)
        lines = []
        for r in result.get("results", []):
            lines.append(f"タイトル: {r.get('title', '')}")
            lines.append(f"URL: {r.get('url', '')}")
            lines.append(f"内容: {r.get('content', '')[:800]}")
            lines.append("---")
        return "\n".join(lines) if lines else "検索結果なし"
    except Exception as e:
        return f"検索エラー: {e}"


def _bars_to_html(bars: list, title: str) -> str:
    """Convert a list of (label, value) pairs to horizontal bar chart HTML."""
    if not bars:
        return ''
    max_val = max(v for _, v in bars)
    if max_val == 0:
        return ''
    bar_rows = []
    for label, val in bars:
        pct = max(2, round(val / max_val * 100))
        display = f'{int(val):,}' if val == int(val) else f'{val:,.1f}'
        bar_rows.append(
            f'<div style="margin-bottom:10px;">'
            f'<div style="display:flex;justify-content:space-between;margin-bottom:3px;">'
            f'<span style="font-size:12px;color:#64748b;">{label}</span>'
            f'<span style="font-size:12px;font-weight:700;color:#1e40af;">{display}</span>'
            f'</div>'
            f'<div style="height:20px;background:#e2e8f0;border-radius:4px;overflow:hidden;">'
            f'<div style="height:20px;width:{pct}%;background:#3b82f6;border-radius:4px;"></div>'
            f'</div></div>'
        )
    return (
        '<div style="margin:1.5rem 0;padding:16px;background:#f8fafc;border-radius:8px;border:1px solid #e2e8f0;">'
        f'<p style="font-size:13px;font-weight:600;color:#334155;margin:0 0 12px 0;">{title}</p>'
        + ''.join(bar_rows) +
        '</div>'
    )


def _inject_charts(html: str) -> str:
    """Convert chart markers to horizontal bar chart HTML (server-side, always correct).

    Handles two formats:
    1. <table class="barchart-data" summary="title"><tr><td>label</td><td>value</td></tr></table>
    2. <!-- CHART: title | label:val, ... --> (legacy fallback)
    """
    # Format 1: <table class="barchart-data">
    def convert_table(m):
        summary_match = re.search(r'summary="([^"]*)"', m.group(0))
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
        chart = _bars_to_html(bars, title)
        return chart if chart else ''  # Remove empty barchart-data tables entirely

    html = re.sub(
        r'<table\b[^>]*class="barchart-data"[^>]*>.*?</table>',
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

    # Remove AI-generated empty chart placeholder divs.
    # AI often creates: <div style="...min-height:200px;..."> <!-- placeholder --> </div>
    # These produce large blank white boxes. Remove any div with min-height that has
    # no visible content (only HTML comments or whitespace inside).
    html = re.sub(
        r'<div\b[^>]*min-height[^>]*>\s*(?:<!--.*?-->\s*)*</div>',
        '',
        html,
        flags=re.IGNORECASE | re.DOTALL,
    )

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
    "あなたはHTMLコード補完専門AIです。渡されたHTMLの末尾から続くHTMLコードのみを出力してください。\n"
    "【絶対禁止】謝罪文・説明文・解説・Markdown・コードフェンス・<!DOCTYPE>/<html>/<head>/<body>タグの出力\n"
    "【必須】HTMLタグのみ出力。最後は必ず</body></html>で終了。途中で諦めることは絶対禁止。"
)

_SECTION_FRAGMENT_SYSTEM = """あなたはHTMLコンテンツ生成ツールです。
指定されたセクションの本文HTMLのみを出力してください。

【絶対禁止】
- <!DOCTYPE>/<html>/<head>/<body>タグの出力
- 謝罪文・説明文・注釈・⚠マーク・Markdown・コードフェンス
- 空のdiv・canvas・min-height付きplaceholder container

【バーチャートの出力方法（必須）】
数値データがある箇所は必ず以下のHTMLテーブル形式でグラフとして表示すること:
<table class="barchart-data" summary="グラフタイトル">
  <tr><td>ラベル</td><td>数値のみ（単位なし）</td></tr>
</table>

【出力形式】
- 指定された<h2>タグから始めること
- Tailwind CSSクラスを使って見やすくレイアウトすること
- 数値・事実・根拠を含め詳細に記述すること
"""


def _gen_market_fragment(user_content: str, max_tokens: int = 8000) -> str:
    """Generate one HTML body section fragment for the market analysis report.

    Uses a dedicated fresh API call per section so the AI can't skip or truncate
    sections due to running out of context. If the section hits max_tokens,
    one continuation is attempted automatically.
    """
    client = _get_anthropic()
    response = _create_with_retry(
        client,
        model="claude-sonnet-4-6",
        max_tokens=max_tokens,
        system=_SECTION_FRAGMENT_SYSTEM,
        messages=[{"role": "user", "content": user_content}],
    )
    text = response.content[0].text
    print(f"[market_fragment] stop={response.stop_reason} len={len(text)}")

    if response.stop_reason == "max_tokens":
        cont_resp = _create_with_retry(
            client,
            model="claude-sonnet-4-6",
            max_tokens=6000,
            system=_CONTINUATION_SYSTEM,
            messages=[{"role": "user", "content": (
                "以下のHTMLが途中で切れています。末尾から続くHTMLのみを出力してください。"
                "最後は開いているタグを閉じて終了すること。\n\n"
                f"【末尾】\n{text[-1500:]}"
            )}],
        )
        text += _strip_continuation(cont_resp.content[0].text)

    # Clean the fragment:
    # 1. Remove full HTML boilerplate (DOCTYPE/html/head/body start tags via _strip_continuation)
    result = _strip_continuation(text)
    # 2. Strip </body></html> from the END — _strip_continuation only strips opening tags,
    #    not closing ones. Without this every fragment ends with </body></html>, causing
    #    multiple body/html closing tags in the assembled report which browsers interpret
    #    as separate documents — sections 2-6 appear "outside" the first document.
    result = re.sub(r'(?:\s*</body>)?\s*</html>\s*$', '', result.rstrip(), flags=re.IGNORECASE).rstrip()
    # 3. Strip any AI preamble text that appears before the first HTML tag
    m_first = re.search(r'<[a-zA-Z]', result)
    if m_first and m_first.start() > 0:
        result = result[m_first.start():]
    return result.strip()


def _build_market_report_html(industry: str, *fragments: str) -> str:
    """Assemble section fragments into a complete, well-formed HTML document."""
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
    model: str = "claude-haiku-4-5-20251001",
    max_tokens: int = 16000,
    complete_fn=None,
    continuation_hint: str = "",
    max_continuations: int = 4,
) -> str:
    """Run Claude without tools. Auto-continues up to max_continuations times if output is truncated.

    Each continuation is a FRESH single-turn conversation to avoid the model getting
    confused by the original long research context in a multi-turn history.
    """
    client = _get_anthropic()

    # --- Initial generation ---
    response = _create_with_retry(
        client, model=model, max_tokens=max_tokens, system=system,
        messages=[{"role": "user", "content": user_msg}],
    )
    accumulated = _strip(response.content[0].text)
    print(f"[run_simple] initial: stop={response.stop_reason} len={len(accumulated)} model={model}")

    # Strip closing tags helper (makes </body> optional since AI sometimes omits it)
    def _strip_close(html: str) -> str:
        return re.sub(r'(?:\s*</body>)?\s*</html>\s*$', '', html.rstrip(), flags=re.IGNORECASE).rstrip()

    # --- Continuation loop ---
    for attempt in range(max_continuations):
        html_closed = bool(re.search(r'</html\s*>', accumulated, re.IGNORECASE))
        sections_complete = complete_fn is None or complete_fn(accumulated)
        print(f"[run_simple] attempt={attempt} closed={html_closed} complete={sections_complete} len={len(accumulated)}")
        if response.stop_reason != "max_tokens" and html_closed and sections_complete:
            break

        if html_closed and not sections_complete:
            # Premature close: AI ended the document but required sections are missing.
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
            # Truly truncated mid-content — continue from the cut-off point
            tail = accumulated[-1500:]
            hint_block = f"\n{continuation_hint}\n" if continuation_hint else ""
            cont_content = (
                f"以下のHTMLが途中で切れています。末尾の直後から続くHTMLコードのみを出力してください。{hint_block}\n\n"
                f"【現在の末尾】\n{tail}\n\n"
                "【絶対ルール】\n"
                "- 上記末尾の直後から続くHTMLのみ出力（冒頭の重複は禁止）\n"
                "- 謝罪文・説明文・Markdownは一切出力禁止\n"
                "- <!DOCTYPE>/<html>/<head>/<body>タグは出力不要\n"
                "- 残りの全セクションを省略せず完全に出力すること\n"
                "- 最後は必ず</body></html>で終了"
            )

        cont_messages = [{"role": "user", "content": cont_content}]
        response = _create_with_retry(
            client, model=model, max_tokens=max_tokens,
            system=_CONTINUATION_SYSTEM, messages=cont_messages,
        )
        chunk = response.content[0].text

        # Detect full document restart: DOCTYPE or <html> near the top of the chunk
        chunk_head = chunk[:600]
        is_restart = bool(
            re.search(r'(?i)<!DOCTYPE\s+html', chunk)
            or re.search(r'(?i)<html\b', chunk_head)
        )
        if is_restart:
            new_doc = _strip(chunk)
            # Only use restart if it's a complete, better document
            if new_doc and re.search(r'</html\s*>', new_doc, re.IGNORECASE):
                new_complete = complete_fn is None or complete_fn(new_doc)
                if new_complete:
                    accumulated = new_doc
                    break  # Complete restart — done
            # Incomplete restart — discard, try again next iteration
        else:
            cont = _strip_continuation(chunk)
            if cont:
                # Overlap guard: duplicate content detection
                cont_text_head = re.sub(r'<[^>]+>', '', cont[:300]).strip()
                acc_text = re.sub(r'<[^>]+>', '', accumulated[:6000]).strip()
                if len(cont_text_head) > 30 and cont_text_head[:80] in acc_text[:int(len(acc_text) * 0.7)]:
                    # Looks like a body-only restart — discard
                    pass
                else:
                    base = _strip_close(accumulated)
                    accumulated = base + "\n" + cont

    # Final completeness check
    html_closed = bool(re.search(r'</html\s*>', accumulated, re.IGNORECASE))
    sections_complete = complete_fn is None or complete_fn(accumulated)
    if not (html_closed and sections_complete):
        accumulated += "\n<!-- __TRUNCATED__ -->"

    return accumulated


def _strip(text: str) -> str:
    """Return just the HTML content, stripping preamble text, code fences, and AI annotations."""
    text = text.strip()
    # Find code fence in any format: ```html, ```, or ``` + newline + language line
    m = re.search(r'```[ \t]*\w*[ \t]*\n', text)
    if m:
        text = text[m.end():]
        if text.rstrip().endswith('```'):
            text = text.rstrip()[:-3]
        text = text.strip()
    # Strip any remaining preamble before <!DOCTYPE or <html (handles no-fence preamble)
    m2 = re.search(r'(?i)<!DOCTYPE|<html\b', text)
    if m2 and m2.start() > 0:
        text = text[m2.start():]
    # Strip ⚠ annotation leaf elements that escaped prompt filtering
    text = re.sub(r'<(p|div|span|li|td)\b[^>]*>[^<]*⚠[^<]*</\1>', '', text, flags=re.IGNORECASE)
    # Strip AI meta-commentary elements: "修正内容：", "注記：", "補足：", etc.
    text = re.sub(
        r'<(p|div|span|li|td)\b[^>]*>[^<]*(修正内容|注記|補足|ファクトチェック)[：:][^<]*</\1>',
        '', text, flags=re.IGNORECASE,
    )
    # Also strip loose markdown-style bold annotations that leak outside tags
    text = re.sub(r'\*\*(修正内容|注記|補足)[：:].*?\*\*', '', text, flags=re.DOTALL)
    # Remove empty structural elements left by continuation artifacts
    text = re.sub(r'<(div|section|blockquote|aside)\b[^>]*>\s*</\1>', '', text, flags=re.IGNORECASE | re.DOTALL)
    return text.strip()


def _strip_continuation(text: str) -> str:
    """Extract HTML body content from a continuation chunk.

    Returns empty string if the AI returned an apology/explanation instead of HTML,
    preventing error text from being injected into the accumulated HTML.
    """
    text = _strip(text)
    # Reject if the result has no HTML tags — AI sent plain text / markdown instead of HTML
    if not re.search(r'<[a-zA-Z][^>]{0,100}>', text):
        return ''
    # Strip full document boilerplate if AI restarted an HTML document
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
        parts.append(f"### {label}\n{html[:3000]}")
    return "\n\n".join(parts)


def _edit_block(previous_output: Optional[str], edit_instruction: Optional[str]) -> str:
    if previous_output and edit_instruction:
        return f"\n## 修正指示\n{edit_instruction}\n\n## 前回の出力（修正対象の抜粋）\n{previous_output[:3000]}\n\n上記の修正指示に従ってHTMLを修正してください。"
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
    """Count <h2> headings whose text content starts with 'N.' (top-level only, not '1-1.')."""
    count = 0
    for m in re.finditer(r'<h2\b[^>]*>(.*?)</h2>', html, re.IGNORECASE | re.DOTALL):
        text = re.sub(r'<[^>]+>', '', m.group(1)).strip()
        if re.match(r'\d+[\.．]\s', text):
            count += 1
    return count


def _missing_market_sections(accumulated: str) -> list[str]:
    """Return list of section labels missing from accumulated (no heading with body content)."""
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
    """Return True only when ALL 6 required sections have headings WITH substantive body content.

    Checks that each required section heading has at least 300 chars of body content after it.
    This prevents false positives from:
    - TOC <li>/<a> items (caught by earlier approach too)
    - AI-generated TOC that uses <h3> headings for each section title with no body content
      (e.g. <h3>3. 競合分析</h3><ul><li>...</li></ul> where <ul> is only 50 chars)
    """
    if not re.search(r'</html\s*>', html, re.IGNORECASE):
        print("[market_complete] INCOMPLETE: no </html>")
        return False
    if len(html) < 15000:
        print(f"[market_complete] INCOMPLETE: too short ({len(html)})")
        return False

    # Build list of (heading_text, content_length_until_next_heading)
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
        # At least one heading matching this pattern must have 300+ chars of body content after it
        # TOC heading entries typically have 0-100 chars; real body sections have 300+ chars
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

    # --- Build search results (shared across all sections) ---
    queries = [
        f"{industry} 市場規模 成長率 2024 2025",
        f"{industry} 市場規模 億円 予測 レポート",
        f"{industry} 市場トレンド 最新 2024",
        f"{industry} 参入障壁 課題 リスク",
        f"{industry} 市場シェア 競合 比較",
    ]
    if competitors:
        for comp in competitors.replace("、", ",").replace("・", ",").split(",")[:4]:
            comp = comp.strip()
            if comp:
                queries.append(f"{comp} 機能 料金 強み 弱み 評判 公式サイト")
                queries.append(f"{comp} 外部連携 API 連携システム 統合")
    queries.append(f"{industry} 成長ドライバー 市場機会")
    queries.append(f"{industry} グローバル 日本 市場比較")

    search_section = ""
    for q in queries[:10]:
        result = _search(q)
        search_section += f"\n### 検索: {q}\n{result}\n"

    project_info = _form_summary(form_data)

    # --- Deep dive (append to existing report) ---
    if deep_dive_request and previous_output:
        extra_results = _search(deep_dive_request)
        system = f"""あなたはHTMLレポート生成ツールです。{HTML_RULES}"""
        user = (
            f"## プロジェクト情報\n{project_info}\n\n"
            f"## 追加深掘り調査リクエスト\n{deep_dive_request}\n\n"
            f"## 追加調査結果\n{extra_results}\n\n"
            f"## 既存レポート（追記対象）\n{previous_output[:4000]}\n\n"
            "既存HTMLの末尾に「追加深掘り調査結果」セクションを追記した完全なHTMLを返してください。"
            "必ず<!DOCTYPE html>から始め、⚠補足などの注釈は一切含めないこと。"
        )
        return _inject_charts(_run_simple(system, user, model="claude-sonnet-4-6", max_tokens=16000))

    # ── Multi-section generation ─────────────────────────────────────────────────
    # Each section is a separate focused API call.
    # This eliminates truncation and completion-check false-positives entirely.

    ctx = f"## プロジェクト情報\n{project_info}\n\n## Web調査結果\n{search_section}\n\n"

    # Section 1: Market size + growth (with bar charts)
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
        max_tokens=8000,
    )

    # Section 2: Global vs Japan comparison
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
        max_tokens=6000,
    )

    # Section 3: Competitor analysis (most token-intensive)
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
        max_tokens=16000,
    )

    # Sections 4-6: Trends, Risks, Opportunities
    s456 = _gen_market_fragment(
        ctx +
        "## 指示\n"
        "以下の3セクションのHTMLを全て生成してください（省略禁止）。\n\n"
        "**セクション4**: <h2>4. 市場トレンド・技術動向</h2>\n"
        "  - 最新市場トレンド・技術革新・DX・AI活用動向・将来展望を詳細に\n\n"
        "**セクション5**: <h2>5. 参入障壁・リスク分析</h2>\n"
        "  - 規制・技術的障壁・競合強度・市場リスクを詳細に\n\n"
        "**セクション6**: <h2>6. 市場機会・成長ドライバー</h2>\n"
        "  - 未開拓領域・成長機会・差別化ポイント・推奨アクションを詳細に\n\n"
        "<h2>4. 市場トレンド・技術動向</h2> から始めること。3セクション全て出力すること。",
        max_tokens=12000,
    )

    html = _build_market_report_html(industry, s1, s2, s3, s456)
    return _inject_charts(html)


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
    # Pre-fetch relevant technology searches
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
    return _run_simple(system, user, model="claude-sonnet-4-6", max_tokens=16000)


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
    return handler(form_data, approved_outputs, previous_output, edit_instruction)
