import pytest

from obsion.harness.general import everyday_request


@pytest.mark.parametrize(
    "question",
    [
        "依据《自循环验证手册》，青禾批次的最终处理动作是什么？请注明出处，只陈述这份虚构验证资料的内容。",
        "参照培训手册，准入条件是什么？",
        "基于接入指南，连接方式是什么？",
        "根据《长标题的开发验证手册》，主要流程是什么？",
        "According to the onboarding handbook, what is the final step?",
        "Based on the deployment manual, what is the prerequisite?",
    ],
)
def test_explicit_source_grounding_wins_over_generic_what_is(question):
    assert not everyday_request(question, context_refs=[], previous_route="GENERAL")


@pytest.mark.parametrize(
    "question",
    [
        "请写一段虚构故事。",
        "请解释比喻是什么意思。",
        "请翻译这段文字：今天有雨。",
    ],
)
def test_ordinary_creative_and_language_tasks_stay_general(question):
    assert everyday_request(question, context_refs=[])
