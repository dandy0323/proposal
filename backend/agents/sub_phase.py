from typing import Optional, Dict
import anthropic
import os
from tavily import TavilyClient

from backend.constants import SUB_PHASE_LABELS

CHART_INSTRUCTIONS = """
## チャート描画の必須ルール
- Chart.js CDN: <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
- 全チャートは必ず window.addEventListener('DOMContentLoaded', function() { ... }); の中で初期化する
- データは必ず実際の数値または調査に基づく推定値を設定する（空配列・0埋めは禁止）
- canvas要素のidを正確に参照してからnew Chart()を呼ぶ
- グラフごとに固有のidをcanvasに付与する
"""

HTML_RULES = """
## HTML出力ルール
- Tailwind CSS CDN使用: <script src="https://cdn.tailwindcss.com"></script>
- 単一HTMLファイルで完結させる
- 日本語で記述
- HTMLのみを出力し、前後の説明文・コードフェンス(```)は不要
- 推定値は「※推定」と明記する
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
            lines.append(f"内容: {r.get('content', '')[:600]}")
            lines.append("---")
        return "\n".join(lines) if lines else "検索結果なし"
    except Exception as e:
        return f"検索エラー: {e}"


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

    for _ in range(8):
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=16000,
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


def _run_simple(system: str, user_msg: str) -> str:
    """Run Claude without tools."""
    response = _get_anthropic().messages.create(
        model="claude-sonnet-4-6",
        max_tokens=16000,
        system=system,
        messages=[{"role": "user", "content": user_msg}],
    )
    return _strip(response.content[0].text)


def _strip(text: str) -> str:
    text = text.strip()
    if text.startswith("```html"):
        text = text[7:]
    elif text.startswith("```"):
        text = text[3:]
    if text.endswith("```"):
        text = text[:-3]
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
プロジェクトの「背景と目的の明確化」を深掘り分析し、詳細なレポートHTMLを作成してください。

含める内容（それぞれ詳細に）:
- 解決すべき事業課題の詳細分析（現状のペイン・ボトルネック）
- 現状（As-Is）と理想状態（To-Be）のギャップ分析
- このシステムが解決する本質的な問題の定義
- ビジョン・ミッションとの整合性
- ステークホルダー分析（誰が影響を受けるか）
- 成功の定義：KGI / KPI の仮説設定

{HTML_RULES}"""
    user = f"## プロジェクト情報\n{_form_summary(form_data)}{_edit_block(previous_output, edit_instruction)}\n\n背景と目的の明確化レポートHTMLを作成してください。"
    return _run_simple(system, user)


def _why_market(form_data, approved, previous_output, edit_instruction, deep_dive_request=None):
    system = f"""あなたは市場調査・競合分析の専門家です。
Web検索ツールを活用して徹底的な市場・競合調査を行い、詳細なレポートHTMLを作成してください。

## 調査内容
1. **市場規模と成長性**: 実際の市場規模データを検索して取得し、数値を明記する
2. **競合サービス分析**: ユーザーが指定した競合（および類似サービス）について以下を調査:
   - 主要機能一覧（スクリーンショットや機能比較表で表現）
   - ユーザーレビュー・評判・評価（実際のレビューサイトから取得）
   - 強み（Strengths）と弱み（Weaknesses）
   - 料金体系・ビジネスモデル
3. **市場トレンド・技術動向**: 業界の最新トレンドを調査

## チャート仕様
- 市場規模推移: 実際の数値を使ったbarまたはlineチャートで描画（必ず具体的な数値を設定）
- 競合比較レーダーチャート: 機能充実度・価格競争力・UX・ブランド力などを数値化（5段階評価）
{CHART_INSTRUCTIONS}
{HTML_RULES}"""

    competitors = form_data.get("competitors", "")
    industry = form_data.get("industry", "")

    if deep_dive_request and previous_output:
        user = f"""## プロジェクト情報
{_form_summary(form_data)}

## 追加深掘り調査リクエスト
{deep_dive_request}

## 既存レポート（追記対象）
{previous_output[:6000]}

上記の追加調査リクエストについてWeb検索で徹底調査し、既存HTMLの末尾に「## 追加深掘り調査結果」セクションを追記した完全なHTMLを返してください。"""
    else:
        user = f"""## プロジェクト情報
{_form_summary(form_data)}

業種「{industry}」の市場規模・競合「{competitors}」を中心に徹底調査し、市場・競合分析レポートHTMLを作成してください。
各競合については機能一覧・ユーザー評価・強弱を必ずWeb検索で調査してください。{_edit_block(previous_output, edit_instruction)}"""

    return _run_with_tools(system, user)


def _why_business_model(form_data, approved, previous_output, edit_instruction):
    system = f"""あなたは事業戦略・ビジネスモデル設計の専門家です。
「ビジネスモデル・収益化」を深掘り分析し、詳細なレポートHTMLを作成してください。

含める内容:
- 推奨マネタイズ手法（複数案を比較検討）
- 収益モデルの詳細設計（価格設定、課金タイミング等）
- KGI / KPI の設定（ダウンロード数、MAU、CVR、LTV、ARPU等）
- 初期投資とランニングコストの概算
- 損益分岐点の予測（グラフ化）
- 類似サービスの収益モデルベンチマーク

{CHART_INSTRUCTIONS}
{HTML_RULES}"""
    ctx = _approved_context(approved)
    user = f"## プロジェクト情報\n{_form_summary(form_data)}\n\n## 承認済み分析結果\n{ctx}{_edit_block(previous_output, edit_instruction)}\n\nビジネスモデル・収益化レポートHTMLを作成してください。"
    return _run_simple(system, user)


def _who_persona(form_data, approved, previous_output, edit_instruction):
    system = f"""あなたはUXリサーチャー・マーケターです。
「ペルソナ定義とユーザー理解」を深掘り分析し、詳細なレポートHTMLを作成してください。

含める内容:
- メインペルソナ（2〜3名）の詳細プロフィール（属性・ライフスタイル・ITリテラシー・1日の流れ）
- ペインポイント（課題・不満）の詳細
- ゲインポイント（欲求・期待）の詳細
- ユーザーインタビュー想定Q&A
- ペルソナごとの利用シナリオ
{HTML_RULES}"""
    ctx = _approved_context(approved)
    user = f"## プロジェクト情報\n{_form_summary(form_data)}\n\n## 承認済み分析結果\n{ctx}{_edit_block(previous_output, edit_instruction)}\n\nペルソナ定義レポートHTMLを作成してください。"
    return _run_simple(system, user)


def _who_value(form_data, approved, previous_output, edit_instruction):
    system = f"""あなたはプロダクトマネージャーです。
「提供価値（バリュープロポジション）」を深掘り分析し、詳細なレポートHTMLを作成してください。

含める内容:
- バリュープロポジションキャンバス（ペインリリーバー・ゲインクリエーター・プロダクト&サービス）
- 競合との差別化ポイント（なぜこのサービスが選ばれるか）
- ユニークセリングポイント（USP）の言語化
- ユーザーへの約束（プロミス）の定義
- 価値提供の優先順位マトリクス
{HTML_RULES}"""
    ctx = _approved_context(approved)
    user = f"## プロジェクト情報\n{_form_summary(form_data)}\n\n## 承認済み分析結果\n{ctx}{_edit_block(previous_output, edit_instruction)}\n\n提供価値レポートHTMLを作成してください。"
    return _run_simple(system, user)


def _who_ux(form_data, approved, previous_output, edit_instruction):
    system = f"""あなたはUXデザイナーです。
「UX設計・カスタマージャーニー」を深掘り分析し、詳細なレポートHTMLを作成してください。

含める内容:
- カスタマージャーニーマップ（認知→検討→利用開始→継続→推奨の各フェーズ）
- 各接点におけるユーザーの感情・行動・思考
- タッチポイント一覧と改善機会
- プラットフォーム別（Web/スマホ）UXの最適化方針
- オンボーディング設計の考え方
{HTML_RULES}"""
    ctx = _approved_context(approved)
    user = f"## プロジェクト情報\n{_form_summary(form_data)}\n\n## 承認済み分析結果\n{ctx}{_edit_block(previous_output, edit_instruction)}\n\nUX設計レポートHTMLを作成してください。"
    return _run_simple(system, user)


def _what_features(form_data, approved, previous_output, edit_instruction):
    system = f"""あなたはプロダクトマネージャーです。
「機能洗い出しと優先順位付け」を深掘り分析し、詳細なレポートHTMLを作成してください。

含める内容:
- 全機能リスト（コア機能・周辺機能・将来機能に分類）
- MoSCoW法による優先順位付け（Must/Should/Could/Won't）
- MVP（最小実用製品）の定義と根拠
- 機能ロードマップ（フェーズ別リリース計画）
- 機能間の依存関係マップ

{CHART_INSTRUCTIONS}
{HTML_RULES}"""
    ctx = _approved_context(approved)
    user = f"## プロジェクト情報\n{_form_summary(form_data)}\n\n## 承認済み分析結果\n{ctx}{_edit_block(previous_output, edit_instruction)}\n\n機能定義レポートHTMLを作成してください。"
    return _run_simple(system, user)


def _what_ia(form_data, approved, previous_output, edit_instruction):
    system = f"""あなたはUI/UXデザイナーです。
「情報設計とUIの方向性」を深掘り分析し、詳細なレポートHTMLを作成してください。

含める内容:
- サイトマップ / 画面一覧（階層構造で表現）
- 主要画面の画面遷移図（テキストベースで表現）
- UIデザインのトーン&マナー（カラー・フォント・コンポーネント方針）
- ナビゲーション設計方針
- レスポンシブ対応方針（ブレークポイント・レイアウト変化）
{HTML_RULES}"""
    ctx = _approved_context(approved)
    user = f"## プロジェクト情報\n{_form_summary(form_data)}\n\n## 承認済み分析結果\n{ctx}{_edit_block(previous_output, edit_instruction)}\n\n情報設計・UI方向性レポートHTMLを作成してください。"
    return _run_simple(system, user)


def _what_nonfunc(form_data, approved, previous_output, edit_instruction):
    system = f"""あなたはシステムアーキテクトです。
「非機能要件の方向性」を深掘り分析し、詳細なレポートHTMLを作成してください。

含める内容:
- 性能要件（レスポンスタイム・スループット・同時接続数）
- 可用性・信頼性要件（稼働率・障害対応）
- セキュリティ要件（認証・認可・暗号化・個人情報保護）
- スケーラビリティ要件（想定ユーザー数増加への対応）
- 運用・保守要件（監視・バックアップ・デプロイ）
{HTML_RULES}"""
    ctx = _approved_context(approved)
    user = f"## プロジェクト情報\n{_form_summary(form_data)}\n\n## 承認済み分析結果\n{ctx}{_edit_block(previous_output, edit_instruction)}\n\n非機能要件レポートHTMLを作成してください。"
    return _run_simple(system, user)


def _how_platform(form_data, approved, previous_output, edit_instruction):
    system = f"""あなたはシステムアーキテクトです。
「プラットフォームとアーキテクチャ」を深掘り分析し、詳細なレポートHTMLを作成してください。

含める内容:
- 推奨プラットフォーム（Web/iOS/Android/両対応）と選定理由
- 開発方式（ネイティブ/クロスプラットフォーム/PWA等）の比較と推奨
- システム全体アーキテクチャ図（テキストベースで表現）
- フロントエンド・バックエンド・DBの技術スタック候補
- インフラ構成の方向性（クラウドサービス選定）
- 開発・ステージング・本番環境の構成
{HTML_RULES}"""
    ctx = _approved_context(approved)
    user = f"## プロジェクト情報\n{_form_summary(form_data)}\n\n## 承認済み分析結果\n{ctx}{_edit_block(previous_output, edit_instruction)}\n\nプラットフォーム・アーキテクチャレポートHTMLを作成してください。"
    return _run_simple(system, user)


def _how_feasibility(form_data, approved, previous_output, edit_instruction):
    system = f"""あなたは技術調査の専門家です。
Web検索ツールを活用して「技術的実現可能性（フィジビリティ）」を徹底調査し、詳細なレポートHTMLを作成してください。

含める内容:
- 必要な技術要素の調査（AI・AR・位置情報・決済等）と成熟度
- 各技術の実装難易度・リスク評価
- PoC（概念実証）が必要な技術要素の特定
- オープンソース・ライブラリ・SDKの調査
- 技術的リスクと対策
{HTML_RULES}"""
    ctx = _approved_context(approved)
    user = f"## プロジェクト情報\n{_form_summary(form_data)}\n\n## 承認済み分析結果\n{ctx}{_edit_block(previous_output, edit_instruction)}\n\n技術実現可能性レポートHTMLを作成してください。Web検索で最新の技術情報を調査してください。"
    return _run_with_tools(system, user)


def _how_integration(form_data, approved, previous_output, edit_instruction):
    system = f"""あなたはシステムインテグレーションの専門家です。
「外部連携とデータ」を深掘り分析し、詳細なレポートHTMLを作成してください。

含める内容:
- 外部システム・API連携一覧（連携先・データ種別・通信方式）
- データフロー図（システム間のデータの流れ）
- 既存社内システムとの連携設計方針
- データモデルの概要（主要エンティティと関係）
- データ移行・初期データ投入の考え方
- API設計方針（REST/GraphQL等）
{HTML_RULES}"""
    ctx = _approved_context(approved)
    user = f"## プロジェクト情報\n{_form_summary(form_data)}\n\n## 承認済み分析結果\n{ctx}{_edit_block(previous_output, edit_instruction)}\n\n外部連携・データ設計レポートHTMLを作成してください。"
    return _run_simple(system, user)


def _project_schedule(form_data, approved, previous_output, edit_instruction):
    system = f"""あなたはプロジェクトマネージャーです。
「スケジュールとマイルストーン」を深掘り分析し、詳細なレポートHTMLを作成してください。

含める内容:
- プロジェクト全体スケジュール（ガントチャート風タイムラインで表現）
- フェーズ別の作業内容と期間（要件定義→設計→開発→テスト→リリース）
- 主要マイルストーンと成果物（デリバラブル）
- フェーズ2・3のロードマップ
- リスクとなる依存関係・クリティカルパス

{CHART_INSTRUCTIONS}
{HTML_RULES}"""
    ctx = _approved_context(approved)
    user = f"## プロジェクト情報\n{_form_summary(form_data)}\n\n## 承認済み分析結果\n{ctx}{_edit_block(previous_output, edit_instruction)}\n\nスケジュール・マイルストーンレポートHTMLを作成してください。"
    return _run_simple(system, user)


def _project_budget(form_data, approved, previous_output, edit_instruction):
    system = f"""あなたはITプロジェクトのコンサルタントです。
「予算と体制」を深掘り分析し、詳細なレポートHTMLを作成してください。

含める内容:
- 初期開発費の内訳概算（設計・開発・インフラ・テスト・PM費用）
- ランニングコストの内訳概算（インフラ・保守・運用・ライセンス）
- 開発体制案（役割・人数・工数）
- インハウス vs アウトソーシングの比較
- コスト最適化のポイント

{CHART_INSTRUCTIONS}
{HTML_RULES}"""
    ctx = _approved_context(approved)
    user = f"## プロジェクト情報\n{_form_summary(form_data)}\n\n## 承認済み分析結果\n{ctx}{_edit_block(previous_output, edit_instruction)}\n\n予算・体制レポートHTMLを作成してください。"
    return _run_simple(system, user)


def _project_legal(form_data, approved, previous_output, edit_instruction):
    system = f"""あなたは法務・コンプライアンスの専門家です。
「法務・コンプライアンス・ガイドライン」を深掘り分析し、詳細なレポートHTMLを作成してください。

含める内容:
- 関連法規チェックリスト（個人情報保護法・資金決済法・特定商取引法・著作権法等）
- 業界特有の規制・ガイドライン
- プラットフォーム規約（App Store / Google Play）の対応事項
- プライバシーポリシー・利用規約の必要事項
- 対応が必要な法的リスクの優先度マトリクス
{HTML_RULES}"""
    ctx = _approved_context(approved)
    user = f"## プロジェクト情報\n{_form_summary(form_data)}\n\n## 承認済み分析結果\n{ctx}{_edit_block(previous_output, edit_instruction)}\n\n法務・コンプライアンスレポートHTMLを作成してください。"
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
