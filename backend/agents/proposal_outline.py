import anthropic
import os

def _get_client():
    return anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))

SYSTEM_PROMPT = """あなたは優秀なITコンサルタントです。
承認された企画・検討ダッシュボードの内容をもとに、PowerPoint提案書の骨子を作成してください。

## 提案書フォーマット（必須知識）

### 固定使用スライド
- P4  サービス概要: シート下半分の青四角に「提案機能一覧」を配置（機能名・機能数は案件に応じて設定）
- P8  ターゲット: シート右下ピンク四角にターゲットユーザのイメージ画像の方向性を記述
- P11 世界観イメージ案: 提案するシステムのUI画像を掲載（モック作成フェーズで作成）

### 選択使用スライド（AI×GF機能）
以下は案件の要件に応じて使用/不使用を判断:
- P12 AI×GFコンテンツ
- P13 GF×AI による PDCA改善効果の最大化
- P14 GF×AIによるユーザエクスペリエンスの向上
- P42 GF分析ダッシュボード①
- P43 GF分析ダッシュボード②
- P44 GF分析ダッシュボード③
- P45 AI ゲーミフィケーション分析

### プラットフォームに応じて1枚選定（必須）
- P15 アプリ方式（レスポンシブハイブリッド）: ネイティブアプリ＋Web（スマホ・PC対応）
- P16 アプリ方式（ハイブリッド）: ネイティブアプリ＋Web（PC対応）
- P17 レスポンシブデザイン: Webのみ（スマホ・PC対応）

### 機能要件シート群（要件に応じてカスタマイズ）
- P19-P39 各機能要件（利用規約〜AIサポーター）
- P46 対応端末
- P53,54 ミドルウェア等構成（サーバサイド・クライアントサイド）

### インフラ構成（1枚選定・必須）
- P48-P51 個人情報保持の有無・要件に応じて選定

## 出力形式
提案書作成用レポートをHTMLで出力する（Tailwind CSS CDN使用）。
以下の構成でレポートを作成:

1. **使用スライド一覧**（チェックリスト形式）
   - 各スライドの使用/不使用と選定理由

2. **スライド別骨子**（スライド番号順）
   - スライド番号・タイトル
   - このスライドで伝えるメッセージ（1〜2文）
   - 掲載する構成要素（箇条書き）
   - 記載すべき具体的な内容・数値・キーワード

3. **P4 機能一覧の詳細**
   - 提案機能名リスト（優先度付き）

4. **注意事項・補足**
   - P8ターゲット画像の方向性（写真の被写体・雰囲気）
   - P11世界観イメージの参照先（モック作成フェーズへの指示）

HTMLのみを出力し、前後の説明文は不要。
"""


def run(
    form_data: dict,
    planning_html: str,
    previous_output: str | None = None,
    edit_instruction: str | None = None,
) -> str:
    user_content = _build_user_message(form_data, planning_html, previous_output, edit_instruction)

    response = _get_client().messages.create(
        model="claude-sonnet-4-6",
        max_tokens=16000,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_content}],
    )
    return response.content[0].text


def _build_user_message(
    form_data: dict,
    planning_html: str,
    previous_output: str | None,
    edit_instruction: str | None,
) -> str:
    system_type = form_data.get("system_type", "")
    lines = [
        "## プロジェクト基本情報",
        f"プロジェクト名: {form_data.get('project_name', '')}",
        f"システム種別: {system_type}",
        f"業種・ドメイン: {form_data.get('industry', '')}",
        f"ターゲット（事業上）: {form_data.get('target_business', '')}",
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
        lines.append("\n上記情報をもとに、提案書骨子レポートHTMLを作成してください。")

    return "\n".join(lines)
