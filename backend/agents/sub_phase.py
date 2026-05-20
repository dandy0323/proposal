from typing import Optional, Dict
import anthropic
import os
import re
import time
from tavily import TavilyClient

from backend.constants import SUB_PHASE_LABELS

CHART_INSTRUCTIONS = """
## チャート描画の必須ルール
- Chart.js CDN: <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
- 全チャートは必ず window.onload = function() { ... }; の中で初期化する（DOMContentLoadedは使用しない）
- データは必ず実際の数値または調査に基づく推定値を設定する（空配列・0埋めは禁止）
- canvas要素のidを正確に参照してからnew Chart()を呼ぶ
- グラフごとに固有のidをcanvasに付与する
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
    "HTMLコード補完アシスタントとして、途中で切れたHTMLの続きを生成してください。"
    "HTMLのみ出力。コードフェンス・説明文・分析テキスト・<!DOCTYPE>/<html>/<head>/<body>タグは一切不要。"
    "最後は必ず</body></html>で終了。"
)


def _run_simple(
    system: str,
    user_msg: str,
    model: str = "claude-haiku-4-5-20251001",
    max_tokens: int = 8192,
    complete_fn=None,
) -> str:
    """Run Claude without tools. Auto-continues up to 2 times if output is truncated or prematurely closed.

    complete_fn: optional callable(html: str) -> bool that returns True only when all
    required sections are present. When supplied, end_turn + html_closed is NOT treated
    as complete unless complete_fn also returns True.
    """
    client = _get_anthropic()
    messages = [{"role": "user", "content": user_msg}]
    accumulated = ""

    for attempt in range(3):
        active_system = system if attempt == 0 else _CONTINUATION_SYSTEM
        response = _create_with_retry(
            client,
            model=model,
            max_tokens=max_tokens,
            system=active_system,
            messages=messages,
        )
        chunk = response.content[0].text
        if attempt == 0:
            accumulated = _strip(chunk)
        else:
            base = re.sub(r'\s*</body>\s*</html>\s*$', '', accumulated.rstrip(), flags=re.IGNORECASE).rstrip()
            accumulated = base + "\n" + _strip_continuation(chunk)

        html_closed = bool(re.search(r'</html\s*>', accumulated, re.IGNORECASE))
        # Accept as complete only when: end_turn AND html_closed AND (no custom check or custom check passes)
        sections_complete = complete_fn is None or complete_fn(accumulated)
        if response.stop_reason != "max_tokens" and html_closed and sections_complete:
            break

        if attempt < 2:
            tail = accumulated[-800:]
            messages.append({"role": "assistant", "content": tail})
            messages.append({
                "role": "user",
                "content": (
                    "HTMLが途中で切れました。上記の末尾の直後から続きのHTMLのみを出力してください。\n"
                    "【ルール】<!DOCTYPE>/<html>/<head>/<body>は不要。コードフェンス不要。説明文不要。</body></html>で終了。"
                )
            })
    else:
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
    return text.strip()


def _strip_continuation(text: str) -> str:
    """Extract HTML body content from a continuation chunk."""
    text = _strip(text)
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


def _market_analysis_complete(html: str) -> bool:
    """Return True only when all 3 required market analysis sections are detectably present."""
    has_chart = bool(re.search(r'<canvas\b', html, re.IGNORECASE))
    has_table = bool(re.search(r'<table\b', html, re.IGNORECASE))
    # Ensure there is substantial content after the last </table> (section 3 - trends)
    table_end = html.lower().rfind('</table>')
    has_trends = table_end > 0 and len(html) > table_end + 400
    return has_chart and has_table and has_trends


def _why_market(form_data, approved, previous_output, edit_instruction, deep_dive_request=None):
    industry = form_data.get("industry", "")
    competitors = form_data.get("competitors", "")

    system = f"""あなたはHTMLレポート生成ツールです。入力されたデータをHTMLに変換して出力するだけです。

## 絶対禁止事項（違反禁止）
- ⚠補足・事実確認注記・ファクトチェック・「確認できない」などの注釈をHTMLに含めること
- <!DOCTYPE html>より前に文字・説明・コードフェンスを出力すること
- サブセクション（例: 2.1 / 2.2）や指定外セクションの追加

## 出力構成（この3セクションのみ・順番厳守・追加禁止）
### セクション1: 市場規模と成長性
- barチャート（3年分の数値）を1つ
- 箇条書き3件のみ（1項目1行・簡潔に）

### セクション2: 競合サービス分析
- 最大3社の比較表（サービス名・特徴・強み・弱みの4列のみ）

### セクション3: 市場トレンド
- 箇条書き3件のみ（1項目1行・簡潔に）

## チャート仕様（セクション1に1つのみ）
- 市場規模推移barチャート（3年分）
{CHART_INSTRUCTIONS}

## 完了要件（最重要）
- セクション1→2→3の順番で全て出力すること（欠落禁止）
- セクション3の後に必ず </body></html> で閉じること
- トークン不足の場合は各テキストを1行に短縮して全3セクション完成を絶対優先する

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

上記のデータをHTMLに変換してください。必ず<!DOCTYPE html>から始め、セクション1→2→3の順で全て出力し、⚠補足などの注釈は一切含めないこと。"""

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
