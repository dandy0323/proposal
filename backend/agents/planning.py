from typing import Optional
import anthropic
import os

def _get_client():
    return anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))

SYSTEM_PROMPT = """あなたは優秀なITコンサルタント・システムアナリストです。
ユーザーから提供されたプロジェクト情報をもとに、Webシステム／スマホアプリ開発の企画・検討を行い、
調査結果をグラフィカルなHTMLダッシュボードとして出力してください。

## 出力仕様
- 単一のHTMLファイルとして出力する
- Tailwind CSS CDNを使用してスタイリングする
- Chart.js CDNを使用してグラフ・チャートを作成する
- 日本語で記述する
- 以下の5カテゴリを必ずカバーする（タブ切り替えUIで実装）

## 5カテゴリ
1. ビジネス・戦略（Why）: 背景と目的の明確化、市場・競合分析、ビジネスモデル・収益化
2. ユーザー・体験（Who）: ターゲット設定とユーザー理解、提供価値、UX設計
3. プロダクト・機能（What）: 機能の洗い出しと優先順位付け、情報設計とUIの方向性、非機能要件の方向性
4. システム・技術（How）: プラットフォームとアーキテクチャ、技術的実現可能性、外部連携とデータ
5. 計画・制約（Project）: スケジュールとマイルストーン、予算と体制、法務・コンプライアンス

## グラフ・ビジュアル化の指針
- 市場規模はバーチャートまたはドーナツチャートで表現
- 機能優先度はMoSCoW法のマトリクスで表現
- スケジュールはガントチャート風のタイムラインで表現
- 競合比較は比較表またはレーダーチャートで表現

## 注意事項
- 提供された情報から推論・補完してよいが、推定であることを明記する
- 根拠のある数値は引用元を脚注に記す
- HTMLのみを出力し、前後の説明文は不要
"""


def run(form_data: dict, previous_output: Optional[str] = None, edit_instruction: Optional[str] = None) -> str:
    user_content = _build_user_message(form_data, previous_output, edit_instruction)

    response = _get_client().messages.create(
        model="claude-sonnet-4-6",
        max_tokens=16000,
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


def _build_user_message(form_data: dict, previous_output: Optional[str], edit_instruction: Optional[str]) -> str:
    lines = ["## プロジェクト情報\n"]
    field_labels = {
        "project_name": "プロジェクト名",
        "system_type": "システム種別",
        "industry": "業種・ドメイン",
        "background": "背景・目的",
        "target_business": "ターゲット（事業上）",
        "target_users": "ターゲットユーザ（サービス利用者）",
        "competitors": "競合情報",
        "budget": "予算規模",
        "roadmap": "ロードマップ",
        "system_components": "システム構成要素",
        "overview": "プロジェクト概要・課題感",
        "notes": "特記事項・制約",
    }
    for key, label in field_labels.items():
        val = form_data.get(key, "")
        if val:
            lines.append(f"**{label}**: {val}")

    if previous_output and edit_instruction:
        lines.append("\n## 修正指示")
        lines.append(edit_instruction)
        lines.append("\n## 前回の出力（修正対象）")
        lines.append(previous_output)
        lines.append("\n上記の修正指示に従って、ダッシュボードHTMLを修正してください。")
    else:
        lines.append("\n上記プロジェクト情報をもとに、企画・検討ダッシュボードHTMLを作成してください。")

    return "\n".join(lines)
