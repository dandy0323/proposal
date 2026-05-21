from typing import Optional, Dict
import anthropic
import os
import re
import time
from tavily import TavilyClient

from backend.constants import SUB_PHASE_LABELS

CHART_INSTRUCTIONS = """
## CSSバーチャート（ピクセル高さ指定・JavaScriptなし・CDN不要）

【px高さの計算式】max = 最大値。バーのpx高さ = round(値 / max × 160)
【例】値が[1200, 1680, 2100]の場合 max=2100 → px高さ: [91, 128, 160]

【テンプレート（★を実データに必ず置き換えること）】

<div style="margin:1.5rem 0;padding:16px;background:#f8fafc;border-radius:8px;border:1px solid #e2e8f0;">
  <p style="font-size:13px;font-weight:600;color:#334155;margin:0 0 10px 0;">★グラフタイトル（単位）★</p>
  <div style="display:flex;align-items:flex-end;gap:10px;">
    <div style="flex:1;text-align:center;">
      <div style="font-size:11px;font-weight:700;color:#1e40af;margin-bottom:4px;">★値1★</div>
      <div style="height:★px1★px;background:rgba(59,130,246,0.75);border-radius:3px 3px 0 0;min-height:4px;"></div>
      <div style="font-size:11px;color:#64748b;margin-top:4px;border-top:2px solid #cbd5e1;padding-top:2px;">★ラベル1★</div>
    </div>
    <div style="flex:1;text-align:center;">
      <div style="font-size:11px;font-weight:700;color:#1e40af;margin-bottom:4px;">★値2★</div>
      <div style="height:★px2★px;background:rgba(59,130,246,0.75);border-radius:3px 3px 0 0;min-height:4px;"></div>
      <div style="font-size:11px;color:#64748b;margin-top:4px;border-top:2px solid #cbd5e1;padding-top:2px;">★ラベル2★</div>
    </div>
    <div style="flex:1;text-align:center;">
      <div style="font-size:11px;font-weight:700;color:#1e40af;margin-bottom:4px;">★値3★</div>
      <div style="height:★px3★px;background:rgba(59,130,246,0.75);border-radius:3px 3px 0 0;min-height:4px;"></div>
      <div style="font-size:11px;color:#64748b;margin-top:4px;border-top:2px solid #cbd5e1;padding-top:2px;">★ラベル3★</div>
    </div>
  </div>
</div>

【バーが4本以上の場合】上記の <div style="flex:1;..."> ... </div> を増やすだけ
【注意事項】
- ★マークは必ず実データに置き換えること（★が残っていたら不完全）
- heightは必ず計算したpx値（例: height:134px）— height:0px・height:auto・height:N%は不可
- JavaScriptもCDNも一切不要
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
        result = _get_tavily().search(query=query, max_results=3)
        lines = []
        for r in result.get("results", []):
            lines.append(f"タイトル: {r.get('title', '')}")
            lines.append(f"URL: {r.get('url', '')}")
            lines.append(f"内容: {r.get('content', '')[:300]}")
            lines.append("---")
        return "\n".join(lines) if lines else "検索結果なし"
    except Exception as e:
        return f"検索エラー: {e}"


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


def _run_simple(
    system: str,
    user_msg: str,
    model: str = "claude-haiku-4-5-20251001",
    max_tokens: int = 8192,
    complete_fn=None,
) -> str:
    """Run Claude without tools. Auto-continues up to 2 times if output is truncated.

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

    # --- Continuation loop (up to 2 additional attempts) ---
    for _ in range(2):
        html_closed = bool(re.search(r'</html\s*>', accumulated, re.IGNORECASE))
        sections_complete = complete_fn is None or complete_fn(accumulated)
        if response.stop_reason != "max_tokens" and html_closed and sections_complete:
            break

        # Fresh single-turn: provide only the tail as context — no original research noise
        tail = accumulated[-1500:]
        cont_messages = [{
            "role": "user",
            "content": (
                "以下のHTMLが途中で切れています。末尾の直後から続くHTMLコードのみを出力してください。\n\n"
                f"【現在の末尾】\n{tail}\n\n"
                "【絶対ルール】\n"
                "- 上記末尾の直後から続くHTMLのみ出力（冒頭の重複は禁止）\n"
                "- 謝罪文・説明文・Markdownは一切出力禁止\n"
                "- <!DOCTYPE>/<html>/<head>/<body>タグは出力不要\n"
                "- 残りの全セクションを省略せず完全に出力すること\n"
                "- 最後は必ず</body></html>で終了"
            )
        }]
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
            if new_doc:
                accumulated = new_doc
        else:
            cont = _strip_continuation(chunk)
            if cont:
                # Overlap guard: if cont's opening text already appears early in accumulated,
                # the AI restarted without DOCTYPE — replace rather than append
                cont_text_head = re.sub(r'<[^>]+>', '', cont[:300]).strip()
                acc_text = re.sub(r'<[^>]+>', '', accumulated[:6000]).strip()
                if len(cont_text_head) > 30 and cont_text_head[:80] in acc_text[:int(len(acc_text) * 0.7)]:
                    accumulated = _strip(chunk) or accumulated
                else:
                    base = re.sub(r'\s*</body>\s*</html>\s*$', '', accumulated.rstrip(), flags=re.IGNORECASE).rstrip()
                    accumulated = base + "\n" + cont

    # Final completeness check
    html_closed = bool(re.search(r'</html\s*>', accumulated, re.IGNORECASE))
    sections_complete = complete_fn is None or complete_fn(accumulated)
    if not (html_closed and sections_complete):
        accumulated += "\n<!-- __TRUNCATED__ -->"

    return accumulated


def _strip(text: str) -> str:
    """Return just the HTML content, stripping preamble text, code fences, and ⚠ annotations."""
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
        parts.append(f"### {label}\n{html[:1500]}")
    return "\n\n".join(parts)


def _edit_block(previous_output: Optional[str], edit_instruction: Optional[str]) -> str:
    if previous_output and edit_instruction:
        return f"\n## 修正指示\n{edit_instruction}\n\n## 前回の出力（修正対象の抜粋）\n{previous_output[:3000]}\n\n上記の修正指示に従ってHTMLを修正してください。"
    return ""


# ── Sub-phase handlers ────────────────────────────────────────────────────────

def _why_background(form_data, approved, previous_output, edit_instruction):
    system = f"""あなたは優秀なITコンサルタントです。
「背景と目的の明確化」のレポートHTMLを作成してください。

含める内容（各項目は簡潔に）:
- 事業課題と現状のペイン
- As-Is / To-Be ギャップ分析
- ステークホルダー分析（主要3者）
- KGI / KPI の仮説設定

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


def _market_analysis_complete(html: str) -> bool:
    """Return True when all sections promised in the TOC are present in the body.

    If no TOC is found, falls back to checking for the 3 essential sections.
    """
    # Must have a visible bar chart — check for the blue bar color used in the template
    has_chart = bool(re.search(r'rgba\(59,\s*130,\s*246', html))
    if not has_chart:
        has_chart = bool(re.search(r'<canvas\b', html, re.IGNORECASE))
    if not has_chart:
        return False
    # Must be properly closed
    if not re.search(r'</html\s*>', html, re.IGNORECASE):
        return False

    # --- TOC-based check ---
    toc_match = re.search(r'(?:目次|もくじ)', html)
    if toc_match:
        toc_area = html[toc_match.start():toc_match.start() + 3000]
        # Match only top-level entries: "1." "2." ... (not "1-1." sub-entries)
        toc_entries = re.findall(r'(?:^|>)\s*(\d+)[\.．]\s+\S', toc_area, re.MULTILINE)
        if toc_entries:
            required = max(int(n) for n in toc_entries)
            # Only count top-level h2 headings (text starts with "N. " not "N-M. ")
            actual = _count_top_level_sections(html)
            return actual >= required

    # --- Fallback: require all 6 mandated sections ---
    checks = [
        bool(re.search(r'市場規模|成長性|market.size', html, re.IGNORECASE)),
        bool(re.search(r'競合|competitor|comparison', html, re.IGNORECASE)),
        bool(re.search(r'トレンド|trend|動向', html, re.IGNORECASE)),
        bool(re.search(r'参入障壁|リスク|risk|barrier', html, re.IGNORECASE)),
    ]
    return all(checks) and len(html) >= 5000


def _why_market(form_data, approved, previous_output, edit_instruction, deep_dive_request=None):
    industry = form_data.get("industry", "")
    competitors = form_data.get("competitors", "")

    system = f"""あなたはHTMLレポート生成ツールです。入力されたデータをHTMLに変換して出力するだけです。

## 絶対禁止事項（違反禁止）
- ⚠補足・事実確認注記・ファクトチェック・「確認できない」などの注釈をHTMLに含めること
- <!DOCTYPE html>より前に文字・説明・コードフェンスを出力すること

## 必須セクション（以下を全て含むこと・省略禁止）
1. 市場規模と成長性（CSSバーチャート必須 — 下記テンプレート使用・ピクセル高さ指定）
2. グローバル市場 vs 日本市場の比較
3. 競合サービス・プロダクト分析（比較表）
4. 市場トレンド・技術動向
5. 参入障壁・リスク分析
6. 市場機会・成長ドライバー

## バーチャート仕様（必ずこのテンプレートを使うこと）
{CHART_INSTRUCTIONS}

## 完了要件（最重要）
- 上記6セクションを全て本文に出力すること（1つでも欠落したら不完全）
- 全セクション出力後に </body></html> で閉じること
- トークン不足の場合は各セクションの文章を短くして全6セクション完成を絶対優先する
- 目次を作る場合は本文と一致させること

{HTML_RULES}"""

    if deep_dive_request and previous_output:
        search_results = _search(deep_dive_request)
        user = f"""## プロジェクト情報
{_form_summary(form_data)}

## 追加深掘り調査リクエスト
{deep_dive_request}

## 追加調査結果
{search_results}

## 既存レポート（追記対象）
{previous_output[:4000]}

上記のデータをもとに、既存HTMLの末尾に「追加深掘り調査結果」セクションを追記した完全なHTMLを返してください。必ず<!DOCTYPE html>から始め、⚠補足などの注釈は一切含めないこと。"""
    else:
        queries = [f"{industry} 市場規模 成長率 2024"]
        if competitors:
            for comp in competitors.replace("、", ",").replace("・", ",").split(",")[:3]:
                comp = comp.strip()
                if comp:
                    queries.append(f"{comp} 機能 料金 評判")
        queries.append(f"{industry} 市場トレンド 最新")

        search_section = ""
        for q in queries[:4]:
            result = _search(q)
            search_section += f"\n### 検索: {q}\n{result}\n"

        user = f"""## プロジェクト情報
{_form_summary(form_data)}

## Web調査結果
{search_section}
{_edit_block(previous_output, edit_instruction)}

上記のデータをHTMLに変換してください。必ず<!DOCTYPE html>から始め、目次に記載した全セクションを本文に出力し、⚠補足などの注釈は一切含めないこと。"""

    return _run_simple(system, user, model="claude-sonnet-4-6", max_tokens=16000, complete_fn=_market_analysis_complete)


def _why_business_model(form_data, approved, previous_output, edit_instruction):
    system = f"""あなたは事業戦略・ビジネスモデル設計の専門家です。
「ビジネスモデル・収益化」のレポートHTMLを作成してください。

含める内容（各項目は簡潔に）:
- 推奨マネタイズ手法（2〜3案比較）
- KGI / KPI の仮説（MAU・CVR・LTV等、主要5指標）
- 初期投資とランニングコストの概算
- 損益分岐点の予測

{HTML_RULES}"""
    ctx = _approved_context(approved)
    user = f"## プロジェクト情報\n{_form_summary(form_data)}\n\n## 承認済み分析結果\n{ctx}{_edit_block(previous_output, edit_instruction)}\n\nビジネスモデル・収益化レポートHTMLを<!DOCTYPE html>から始めて作成してください。"
    return _run_simple(system, user)


def _who_persona(form_data, approved, previous_output, edit_instruction):
    system = f"""あなたはUXリサーチャーです。
「ペルソナ定義とユーザー理解」のレポートHTMLを作成してください。

## ペルソナ数：3名のみ（超過禁止）

## 各ペルソナに含める内容（3名分）:
- 基本プロフィール（氏名・年齢・職業・ITリテラシー）
- ペインポイント 3項目
- ゲインポイント 3項目
- 代表的なコメント（1文）

## 共通セクション（ペルソナの後に1回だけ）:
- Pain/Gain分析マトリクス（表形式）
- 主要利用シナリオ（2シナリオ・各3行以内）

{HTML_RULES}"""
    ctx = _approved_context(approved)
    user = f"## プロジェクト情報\n{_form_summary(form_data)}\n\n## 承認済み分析結果\n{ctx}{_edit_block(previous_output, edit_instruction)}\n\nペルソナ定義レポートHTMLを<!DOCTYPE html>から始めて作成してください。"
    return _run_simple(system, user)


def _who_value(form_data, approved, previous_output, edit_instruction):
    system = f"""あなたはプロダクトマネージャーです。
「提供価値（バリュープロポジション）」のレポートHTMLを作成してください。

含める内容（各項目は簡潔に）:
- バリュープロポジションキャンバス（ペイン・ゲイン・プロダクト）
- 競合との差別化ポイント（上位3点）
- USP（ユニークセリングポイント）の言語化
{HTML_RULES}"""
    ctx = _approved_context(approved)
    user = f"## プロジェクト情報\n{_form_summary(form_data)}\n\n## 承認済み分析結果\n{ctx}{_edit_block(previous_output, edit_instruction)}\n\n提供価値レポートHTMLを<!DOCTYPE html>から始めて作成してください。"
    return _run_simple(system, user)


def _who_ux(form_data, approved, previous_output, edit_instruction):
    system = f"""あなたはUXデザイナーです。
「UX設計・カスタマージャーニー」のレポートHTMLを作成してください。

含める内容（各項目は簡潔に）:
- カスタマージャーニーマップ（認知→検討→利用開始→継続→推奨の5フェーズ、表形式）
- 主要タッチポイントと改善機会（上位5件）
- Web/スマホ別UX方針（各3点以内）
- オンボーディング設計の要点（3点以内）

{HTML_RULES}"""
    ctx = _approved_context(approved)
    user = f"## プロジェクト情報\n{_form_summary(form_data)}\n\n## 承認済み分析結果\n{ctx}{_edit_block(previous_output, edit_instruction)}\n\nUX設計レポートHTMLを<!DOCTYPE html>から始めて作成してください。"
    return _run_simple(system, user)


def _what_features(form_data, approved, previous_output, edit_instruction):
    system = f"""あなたはプロダクトマネージャーです。
「機能洗い出しと優先順位付け」のレポートHTMLを作成してください。

含める内容（各項目は簡潔に）:
- 機能リスト（コア・周辺・将来に分類、各5件以内）
- MoSCoW優先順位付け
- MVP の定義
- フェーズ別リリース計画（3フェーズ）

{HTML_RULES}"""
    ctx = _approved_context(approved)
    user = f"## プロジェクト情報\n{_form_summary(form_data)}\n\n## 承認済み分析結果\n{ctx}{_edit_block(previous_output, edit_instruction)}\n\n機能定義レポートHTMLを<!DOCTYPE html>から始めて作成してください。"
    return _run_simple(system, user)


def _what_ia(form_data, approved, previous_output, edit_instruction):
    system = f"""あなたはUI/UXデザイナーです。
「情報設計とUIの方向性」のレポートHTMLを作成してください。

含める内容（各項目は簡潔に）:
- サイトマップ / 主要画面一覧（階層構造）
- 主要画面遷移図（テキストベース）
- UIトーン&マナー（カラー・フォント方針）
- レスポンシブ対応方針
{HTML_RULES}"""
    ctx = _approved_context(approved)
    user = f"## プロジェクト情報\n{_form_summary(form_data)}\n\n## 承認済み分析結果\n{ctx}{_edit_block(previous_output, edit_instruction)}\n\n情報設計・UI方向性レポートHTMLを<!DOCTYPE html>から始めて作成してください。"
    return _run_simple(system, user)


def _what_nonfunc(form_data, approved, previous_output, edit_instruction):
    system = f"""あなたはシステムアーキテクトです。
「非機能要件の方向性」のレポートHTMLを作成してください。

含める内容（各項目は簡潔に、要点のみ）:
- 性能要件（レスポンスタイム・同時接続数）
- 可用性・セキュリティ要件
- スケーラビリティ方針
- 運用・保守方針
{HTML_RULES}"""
    ctx = _approved_context(approved)
    user = f"## プロジェクト情報\n{_form_summary(form_data)}\n\n## 承認済み分析結果\n{ctx}{_edit_block(previous_output, edit_instruction)}\n\n非機能要件レポートHTMLを<!DOCTYPE html>から始めて作成してください。"
    return _run_simple(system, user)


def _how_platform(form_data, approved, previous_output, edit_instruction):
    system = f"""あなたはシステムアーキテクトです。
「プラットフォームとアーキテクチャ」のレポートHTMLを作成してください。

含める内容（各項目は簡潔に）:
- 推奨プラットフォームと選定理由
- システムアーキテクチャ概要（テキストベース）
- 技術スタック候補（FE・BE・DB）
- インフラ構成の方向性
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

含める内容（各項目は簡潔に）:
- 外部API・システム連携一覧（上位5件）
- データフロー概要
- 主要データモデル（エンティティ3〜5件）
- API設計方針
{HTML_RULES}"""
    ctx = _approved_context(approved)
    user = f"## プロジェクト情報\n{_form_summary(form_data)}\n\n## 承認済み分析結果\n{ctx}{_edit_block(previous_output, edit_instruction)}\n\n外部連携・データ設計レポートHTMLを<!DOCTYPE html>から始めて作成してください。"
    return _run_simple(system, user)


def _project_schedule(form_data, approved, previous_output, edit_instruction):
    system = f"""あなたはプロジェクトマネージャーです。
「スケジュールとマイルストーン」のレポートHTMLを作成してください。

含める内容（各項目は簡潔に）:
- フェーズ別スケジュール（要件定義→設計→開発→テスト→リリース）
- 主要マイルストーンと成果物
- フェーズ2・3のロードマップ概要
- クリティカルパスとリスク

{HTML_RULES}"""
    ctx = _approved_context(approved)
    user = f"## プロジェクト情報\n{_form_summary(form_data)}\n\n## 承認済み分析結果\n{ctx}{_edit_block(previous_output, edit_instruction)}\n\nスケジュール・マイルストーンレポートHTMLを<!DOCTYPE html>から始めて作成してください。"
    return _run_simple(system, user)


def _project_budget(form_data, approved, previous_output, edit_instruction):
    system = f"""あなたはITプロジェクトのコンサルタントです。
「予算と体制」のレポートHTMLを作成してください。

含める内容（各項目は簡潔に）:
- 初期開発費の内訳概算
- ランニングコスト概算
- 開発体制案（役割・人数）
- コスト最適化のポイント

{HTML_RULES}"""
    ctx = _approved_context(approved)
    user = f"## プロジェクト情報\n{_form_summary(form_data)}\n\n## 承認済み分析結果\n{ctx}{_edit_block(previous_output, edit_instruction)}\n\n予算・体制レポートHTMLを<!DOCTYPE html>から始めて作成してください。"
    return _run_simple(system, user)


def _project_legal(form_data, approved, previous_output, edit_instruction):
    system = f"""あなたは法務・コンプライアンスの専門家です。
「法務・コンプライアンス」のレポートHTMLを作成してください。

含める内容（各項目は簡潔に）:
- 関連法規チェックリスト（主要5〜7法）
- 業界特有の規制・ガイドライン
- プライバシーポリシー・利用規約の必要事項
- 法的リスク優先度マトリクス
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
