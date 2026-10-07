from pathlib import Path
import unittest

from pku_radar.ranking import RANKING_PROMPT, load_preferences


class RankingPolicyTests(unittest.TestCase):
    def test_opportunities_require_notice_evidence(self):
        for rule in (
            "仍然开放”本身不足以构成推荐理由",
            "必须由 Notice 现有信息提供具体证据",
            "证据不足，无法判断是否匹配用户，默认 recommend=false",
            "“可以点进去看看是否匹配”“可能存在相关技术岗”“需要查看原文确认”不是推荐理由",
            "没有明确技术岗位、技术方向、AI/软件/系统/工程相关信号，通常不主动推荐",
        ):
            with self.subTest(rule=rule):
                self.assertIn(rule, RANKING_PROMPT)

    def test_employer_identity_is_not_matching_evidence(self):
        self.assertIn(
            "不要仅根据雇主或公司名称、知名度、行业印象猜测相关性或推断与用户匹配",
            RANKING_PROMPT,
        )

    def test_matching_actionable_opportunities_remain_eligible(self):
        self.assertIn("不要统一降低招聘信息优先级", RANKING_PROMPT)
        self.assertIn("有具体匹配证据且有明确、即时行动价值的机会仍可推荐", RANKING_PROMPT)
        for example in ("明确匹配技术方向的岗位", "与用户目标直接相关的校内宣讲",
                        "明确匹配兴趣的科研/竞赛/工程实践机会"):
            with self.subTest(example=example):
                self.assertIn(example, RANKING_PROMPT)

    def test_preferences_focus_on_technical_job_directions(self):
        path = Path(__file__).resolve().parents[1] / "config" / "preferences.yaml"
        preferences = load_preferences(path)
        self.assertEqual(set(preferences), {"primary_interests", "secondary_interests", "low_interest"})
        for interests in preferences.values():
            self.assertNotIn("实习", interests)
        self.assertIn("AI / LLM / Agent 相关实习与校招", preferences["primary_interests"])
        self.assertIn("软件工程 / 系统 / AI Infrastructure 技术岗位", preferences["primary_interests"])


if __name__ == "__main__":
    unittest.main()
