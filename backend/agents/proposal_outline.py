from typing import Optional
import anthropic
import os

def _get_client():
    return anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))

SYSTEM_PROMPT = """あなたは優秀なITコンサルタントです。
承認された企画・検討ダッシュボードの内容をもとに、PowerPoint提案書の詳細なスライド構成仕様を作成してください。

## 出力形式
Tailwind CSS CDNを使用したHTMLで出力。以下の構成で作成:

---

### セクション1: 使用スライド一覧（チェックリスト）
各スライドの使用/不使用と選定理由を表形式で一覧化。

固定スライド:
- P4  サービス概要（必須）
- P8  ターゲット（必須）
- P11 世界観イメージ（必須）

選択スライド（AI×GF機能 — 案件要件に応じて選定）:
- P12 AI×GFコンテンツ
- P13 GF×AI PDCA改善
- P14 GF×AIユーザエクスペリエンス
- P42〜P45 GF分析ダッシュボード / AI分析

プラットフォーム選定（1枚必須）:
- P15 レスポンシブハイブリッド（ネイティブ+Web スマホ・PC対応）
- P16 ハイブリッド（ネイティブ+Web PC対応）
- P17 レスポンシブ（Webのみ スマホ・PC対応）

インフラ構成（1枚必須）:
- P48〜P51 個人情報保持の有無・要件に応じて選定

---

### セクション2: 固定スライドのレイアウト仕様（スライド番号順）

各スライドについて以下を詳細に記述:
**スライド番号・タイトル**
- 伝えるメッセージ: （1文）
- レイアウト構成:
  - 上部エリア: 配置するオブジェクト種別・具体的テキスト
  - 中央エリア: 配置するオブジェクト種別・具体的テキスト／数値
  - 下部エリア: 配置するオブジェクト種別・具体的テキスト
  - 左/右エリア（2カラム時）: 各エリアの内容詳細

---

### セクション3: 機能要件スライド群（1機能 = 1スライド、全機能分）

企画フェーズで洗い出した **全機能** について、それぞれ1スライド分のレイアウト仕様を作成する。
機能数が多い場合もすべての機能分を作成すること（省略禁止）。

各機能スライドの構成（テンプレート）:
**[機能名] — P[番号]**
- 上部エリア（タイトルバー）: 機能名、機能カテゴリ（コア/周辺/将来）
- 中央左エリア（約60%幅）:
  - 機能説明テキスト（3〜4文）
  - 主要ベネフィット（箇条書き3点）
  - 対象ユーザ・利用シナリオ（1〜2文）
- 中央右エリア（約40%幅）:
  - 画面イメージの説明（テキストベース、モック作成フェーズへの指示として記述）
  - 関連機能・連携ポイント
- 下部エリア: MVP対象か否か、開発フェーズ（Phase 1/2/3）

---

### セクション4: 注意事項・補足指示

- P8 ターゲット画像の方向性（被写体・シーン・雰囲気を具体的に）
- P11 世界観イメージ（モック作成フェーズへの具体的な指示）

---

HTMLのみを出力し、前後の説明文・コードフェンスは不要。
"""


def run(
    form_data: dict,
    planning_html: str,
    previous_output: Optional[str] = None,
    edit_instruction: Optional[str] = None,
) -> str:
    user_content = _build_user_message(form_data, planning_html, previous_output, edit_instruction)

    response = _get_client().messages.create(
        model="claude-sonnet-4-6",
        max_tokens=64000,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_content}],
    )
    return _strip_code_fence(response.content[0].text)


def _strip_code_fence(text: str) -> str:
    text = text.strip()
    if text.startswith("```html"):
        text = text[7:]
    elif text.startswith("```"):
        text = text[3:]
    if text.endswith("```"):
        text = text[:-3]
    return text.strip()


def _build_user_message(
    form_data: dict,
    planning_html: str,
    previous_output: Optional[str],
    edit_instruction: Optional[str],
) -> str:
    lines = [
        "## プロジェクト基本情報",
        f"プロジェクト名: {form_data.get('project_name', '')}",
        f"システム種別: {form_data.get('system_type', '')}",
        f"業種・ドメイン: {form_data.get('industry', '')}",
        f"ターゲット（事業上）: {form_data.get('target_business', '')}",
        f"ターゲットユーザ: {form_data.get('target_users', '')}",
        f"予算規模: {form_data.get('budget', '')}",
        "",
        "## 承認済み企画・検討ダッシュボード（HTML）",
        planning_html,
    ]

    if previous_output and edit_instruction:
        lines += [
            "",
            "## 修正指示",
            edit_instruction,
            "",
            "## 前回の骨子レポート（修正対象）",
            previous_output,
            "",
            "上記の修正指示に従って、提案書骨子レポートHTMLを修正してください。",
        ]
    else:
        lines.append("\n上記情報をもとに、提案書スライド構成仕様HTMLを作成してください。")

    return "\n".join(lines)
