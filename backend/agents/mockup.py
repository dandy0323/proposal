from typing import Optional
import anthropic
import os

def _get_client():
    return anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY", ""))

SYSTEM_PROMPT = """あなたは優秀なUIデザイナー・フロントエンドエンジニアです。
提案書骨子をもとに、代表画面3つを自動選定してHTML/CSSワイヤーフレームを作成してください。

## Step 1: 代表画面3つの選定基準
提案書の機能・UX設計から、ユーザーインパクトの大きい画面を以下の観点で選定:
1. **ホーム/ダッシュボード画面** — アプリの第一印象となるエントリーポイント
2. **コア機能画面** — プロダクトの最重要機能の操作画面
3. **サブ機能 or 一覧画面** — 2番目に重要な機能、または主要一覧・検索画面

## Step 2: HTML出力仕様

出力は必ず以下の構造の**単一HTMLファイル**とする:

```
<!DOCTYPE html>
<html>
<head>
  <!-- Tailwind CSS CDN: https://cdn.tailwindcss.com -->
  <!-- 選定画面の説明セクション + タブUI + 各画面のワイヤーフレーム -->
</head>
<body>
  <!-- 1. 選定画面の説明（3画面それぞれの選定理由を1〜2文） -->
  <!-- 2. タブナビゲーション（固定ヘッダー）: ① 画面名 ② 画面名 ③ 画面名 -->
  <!-- 3. 各タブに対応するワイヤーフレーム本体 -->
</body>
</html>
```

### タブUI要件
- タブは画面上部に固定表示（sticky top）
- タブラベル: 「① [画面名]」「② [画面名]」「③ [画面名]」
- アクティブタブは視覚的に区別（背景色変更等）
- JavaScriptでタブ切り替え（シンプルなshow/hide）

### ワイヤーフレーム要件
- モノトーン基調（グレースケール）
- 画像はプレースホルダー（背景色＋説明テキスト）で代替
- 実データはダミーテキストで表現（実在するような具体的な内容にする）
- ナビゲーション構造（ヘッダー/フッター/サイドバー等）を各画面で統一
- スマホ/Webのどちらのレイアウトかを画面タイトル下に明示
- コンポーネントは実際のUIを想起できる程度のディテールで表現
- 各画面は十分な高さ・詳細度で作成（省略せず全要素を描画）

## 注意事項
- HTMLのみを出力し、前後の説明文・コードフェンスは不要
- `</html>` まで完全に出力すること（途中で切れないこと）
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
        lines.append("\n上記情報をもとに、代表3画面のタブ切り替えワイヤーフレームHTMLを作成してください。")

    return "\n".join(lines)
