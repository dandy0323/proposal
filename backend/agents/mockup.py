from typing import Optional
import anthropic
import os

def _get_client():
    return anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))

SYSTEM_PROMPT = """あなたは優秀なUIデザイナー・フロントエンドエンジニアです。
承認された提案書骨子レポートをもとに、HTML/CSSワイヤーフレームを作成してください。

## 出力仕様
- 単一HTMLファイルとして出力する
- Tailwind CSS CDNを使用してスタイリングする（CDN: https://cdn.tailwindcss.com）
- 画像はプレースホルダー（背景色＋テキスト）で代替する
- 実際のデータはダミーテキストで表現する
- 日本語UIで記述する

## ワイヤーフレームの構成
骨子レポートに記載された主要画面を実装する。最低限以下を含める:
- トップ/ホーム画面
- メイン機能画面（コア機能1〜2画面）
- ナビゲーション構造（ヘッダー/フッター/サイドバー等）

## デザイン方針
- モノトーン基調（グレースケール）のワイヤーフレーム
- 画面遷移はページ内タブ切り替えで表現
- スマホ/Web両対応の場合はレスポンシブレイアウトで実装
- コンポーネントは実際のUIを想起できる程度のディテールで表現

## 注意事項
- HTMLのみを出力し、前後の説明文は不要
- JavaScriptは最小限（タブ切り替え程度）に留める
"""


def run(
    form_data: dict,
    proposal_outline_html: str,
    previous_output: Optional[str] = None,
    edit_instruction: Optional[str] = None,
) -> str:
    user_content = _build_user_message(form_data, proposal_outline_html, previous_output, edit_instruction)

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


def _build_user_message(
    form_data: dict,
    proposal_outline_html: str,
    previous_output: Optional[str],
    edit_instruction: Optional[str],
) -> str:
    lines = [
        "## プロジェクト基本情報",
        f"プロジェクト名: {form_data.get('project_name', '')}",
        f"システム種別: {form_data.get('system_type', '')}",
        f"ターゲットユーザ: {form_data.get('target_users', '')}",
        "",
        "## 承認済み提案書骨子レポート（HTML）",
        proposal_outline_html,
    ]

    if previous_output and edit_instruction:
        lines += [
            "",
            "## 修正指示",
            edit_instruction,
            "",
            "## 前回のワイヤーフレーム（修正対象）",
            previous_output,
            "",
            "上記の修正指示に従って、ワイヤーフレームHTMLを修正してください。",
        ]
    else:
        lines.append("\n上記情報をもとに、HTML/CSSワイヤーフレームを作成してください。")

    return "\n".join(lines)
