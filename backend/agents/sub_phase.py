from typing import Optional, Dict
import anthropic
import os
import re
import time
from tavily import TavilyClient

from backend.constants import SUB_PHASE_LABELS

CHART_INSTRUCTIONS = """
## 横型バーチャート（JavaScriptなし・CDN不要・確実に表示される）

【width%の計算式】max = 最大値。各バーのwidth = round(値 / max × 100)%

【完成例 — このHTMLをそのままコピーして実データに書き換えること】

<div style="margin:1.5rem 0;padding:16px;background:#f8fafc;border-radius:8px;border:1px solid #e2e8f0;">
  <p style="font-size:13px;font-weight:600;color:#334155;margin:0 0 12px 0;">市場規模推移（億円）</p>

  <div style="margin-bottom:10px;">
    <div style="display:flex;justify-content:space-between;margin-bottom:3px;">
      <span style="font-size:12px;color:#64748b;">2022年</span>
      <span style="font-size:12px;font-weight:700;color:#1e40af;">1,200億</span>
    </div>
    <div style="height:20px;background:#e2e8f0;border-radius:4px;">
      <div style="height:20px;width:57%;background:#3b82f6;border-radius:4px;"></div>
    </div>
  </div>

  <div style="margin-bottom:10px;">
    <div style="display:flex;justify-content:space-between;margin-bottom:3px;">
      <span style="font-size:12px;color:#64748b;">2023年</span>
      <span style="font-size:12px;font-weight:700;color:#1e40af;">1,680億</span>
    </div>
    <div style="height:20px;background:#e2e8f0;border-radius:4px;">
      <div style="height:20px;width:80%;background:#3b82f6;border-radius:4px;"></div>
    </div>
  </div>

  <div style="margin-bottom:10px;">
    <div style="display:flex;justify-content:space-between;margin-bottom:3px;">
      <span style="font-size:12px;color:#64748b;">2024年</span>
      <span style="font-size:12px;font-weight:700;color:#1e40af;">2,100億</span>
    </div>
    <div style="height:20px;background:#e2e8f0;border-radius:4px;">
      <div style="height:20px;width:100%;background:#3b82f6;border-radius:4px;"></div>
    </div>
  </div>
</div>

【書き換え手順】
1. 表示値（1,200億 等）を実データの値に変える
2. widthの%値（57%, 80%, 100%）を round(値/max×100) で計算した値に変える
3. 年ラベルを実際の期間・カテゴリ名に変える
4. グラフタイトルを変える
5. 行が4本以上ある場合は <div style="margin-bottom:10px;">...</div> ブロックを追加するだけ
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
    max_tokens: int = 16000,
    complete_fn=None,
) -> str:
    """Run Claude without tools. Auto-continues up to 4 times if output is truncated.

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

    # --- Continuation loop (up to 4 additional attempts) ---
    for attempt in range(4):
        html_closed = bool(re.search(r'</html\s*>', accumulated, re.IGNORECASE))
        sections_complete = complete_fn is None or complete_fn(accumulated)
        print(f"[run_simple] attempt={attempt} closed={html_closed} complete={sections_complete} len={len(accumulated)}")
        if response.stop_reason != "max_tokens" and html_closed and sections_complete:
            break

        if html_closed and not sections_complete:
            # Premature close: AI ended the document but required sections are missing.
            # Strip closing tags from the tail so the continuation AI can append.
            base_open = re.sub(r'\s*</body>\s*</html>\s*$', '', accumulated.rstrip(), flags=re.IGNORECASE).rstrip()
            tail = base_open[-2000:]
            cont_content = (
                "以下のHTMLは途中で閉じられており、必須セクションが欠落しています。"
                "末尾の</body></html>を取り除いた状態から、欠落セクションのHTMLを追記してください。\n\n"
                f"【現在の末尾（閉じタグ除外）】\n{tail}\n\n"
                "【指示】\n"
                "- 不足しているセクションのHTMLのみ出力（冒頭の重複禁止）\n"
                "- <!DOCTYPE>/<html>/<head>/<body>タグは出力不要\n"
                "- 謝罪文・説明文・Markdownは絶対不要\n"
                "- 全不足セクションを出力後、必ず</body></html>で終了"
            )
        else:
            # Truly truncated mid-content — continue from the cut-off point
            tail = accumulated[-1500:]
            cont_content = (
                "以下のHTMLが途中で切れています。末尾の直後から続くHTMLコードのみを出力してください。\n\n"
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


def _market_analysis_complete(html: str) -> bool:
    """Return True only when ALL 6 required section topics are present.

    Deliberately ignores TOC: AI-generated TOCs may have fewer entries than
    required (e.g. only 2) which previously caused this check to return True
    for an incomplete document, suppressing continuation entirely.
    """
    if not re.search(r'</html\s*>', html, re.IGNORECASE):
        return False
    if len(html) < 8000:
        return False

    # Every one of the 6 mandated sections must appear in the body
    required = [
        r'市場規模|成長性|market.size',
        r'グローバル|日本市場|global.*market|japan.*market',
        r'競合|competitor|comparison',
        r'トレンド|trend|動向|技術動向',
        r'参入障壁|リスク|risk|barrier',
        r'市場機会|成長ドライバー|opportunity|growth.driver',
    ]
    return all(bool(re.search(pat, html, re.IGNORECASE)) for pat in required)


def _why_market(form_data, approved, previous_output, edit_instruction, deep_dive_request=None):
    industry = form_data.get("industry", "")
    competitors = form_data.get("competitors", "")

    system = f"""あなたはHTMLレポート生成ツールです。入力されたデータをHTMLに変換して出力するだけです。

## 絶対禁止事項（違反禁止）
- ⚠補足・事実確認注記・ファクトチェック・「確認できない」などの注釈をHTMLに含めること
- <!DOCTYPE html>より前に文字・説明・コードフェンスを出力すること

## 必須セクション（以下を全て含むこと・省略禁止）
1. 市場規模と成長性（横型バーチャート必須 — 下記テンプレートをコピーして実データに変えること）
2. グローバル市場 vs 日本市場の比較
3. 競合サービス・プロダクト分析（下記の詳細フォーマット必須）
4. 市場トレンド・技術動向
5. 参入障壁・リスク分析
6. 市場機会・成長ドライバー

## バーチャート仕様（必ずこのテンプレートを使うこと）
{CHART_INSTRUCTIONS}

## セクション3「競合サービス・プロダクト分析」の必須フォーマット

各競合サービス・製品ごとに以下を全て記載すること（省略禁止）:

### 各競合エントリの構造（競合1社/1製品につき1ブロック）
1. **サービス名 + 公式URL**（クリッカブルリンク）
2. **強み**（箇条書き・具体的に）
3. **弱み**（箇条書き・具体的に）
4. **主要機能一覧**（表形式 or チェックリスト形式）
5. **不足機能 / 追加提案候補**（自社が差別化できる機能・今後提供すべき機能）
6. **外部連携システム一覧**（全ての連携先を列挙）:
   - 連携先システム名
   - 本体 → 連携先へ送信するデータ・情報
   - 連携先 → 本体へ受け取るデータ・情報
   - 連携の目的・概要

競合エントリはグローバル競合・国内競合を分けてセクション化すること。
URLが不明な場合は「（※公式サイト要確認）」と記載し、推測URLは使わないこと。

## 完了要件（最重要）
- 上記6セクションを全て本文に出力すること（1つでも欠落したら不完全）
- セクション3は全競合について詳細ブロックを記載すること
- 全セクション出力後に </body></html> で閉じること
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

        user = f"""## プロジェクト情報
{_form_summary(form_data)}

## Web調査結果
{search_section}
{_edit_block(previous_output, edit_instruction)}

上記のデータをHTMLに変換してください。必ず<!DOCTYPE html>から始め、目次に記載した全セクションを本文に出力し、⚠補足などの注釈は一切含めないこと。
セクション3の競合分析は各社について「強み・弱み・機能一覧・不足機能・外部連携システム（双方向データフロー）・公式URL」を全て記載すること。"""

    return _run_simple(system, user, model="claude-sonnet-4-6", max_tokens=32000, complete_fn=_market_analysis_complete)


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
