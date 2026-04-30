from typing import List, Dict

SUB_PHASES: List[str] = [
    "why_background",
    "why_market",
    "why_business_model",
    "who_persona",
    "who_value",
    "who_ux",
    "what_features",
    "what_ia",
    "what_nonfunc",
    "how_platform",
    "how_feasibility",
    "how_integration",
    "project_schedule",
    "project_budget",
    "project_legal",
]

SUB_PHASE_LABELS: Dict[str, str] = {
    "why_background": "背景と目的の明確化",
    "why_market": "市場・競合分析",
    "why_business_model": "ビジネスモデル・収益化",
    "who_persona": "ペルソナ定義とユーザー理解",
    "who_value": "提供価値（バリュープロポジション）",
    "who_ux": "UX設計・カスタマージャーニー",
    "what_features": "機能洗い出しと優先順位付け",
    "what_ia": "情報設計とUIの方向性",
    "what_nonfunc": "非機能要件の方向性",
    "how_platform": "プラットフォームとアーキテクチャ",
    "how_feasibility": "技術的実現可能性",
    "how_integration": "外部連携とデータ",
    "project_schedule": "スケジュールとマイルストーン",
    "project_budget": "予算と体制",
    "project_legal": "法務・コンプライアンス",
}

SUB_PHASE_GROUPS: Dict[str, List[str]] = {
    "ビジネス・戦略（Why）": ["why_background", "why_market", "why_business_model"],
    "ユーザー・体験（Who）": ["who_persona", "who_value", "who_ux"],
    "プロダクト・機能（What）": ["what_features", "what_ia", "what_nonfunc"],
    "システム・技術（How）": ["how_platform", "how_feasibility", "how_integration"],
    "計画・制約（Project）": ["project_schedule", "project_budget", "project_legal"],
}

MAIN_PHASE_ORDER = ["planning", "proposal_outline", "mockup", "done"]
