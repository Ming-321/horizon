"""Tests for Phase 1: CompletionResult, TokenUsage, TokenUsageTracker."""

from src.ai.client import TokenUsage, CompletionResult, TokenUsageTracker


def test_token_usage_defaults():
    u = TokenUsage()
    assert u.prompt_tokens == 0
    assert u.completion_tokens == 0


def test_completion_result_basic():
    cr = CompletionResult(text="hello")
    assert cr.text == "hello"
    assert cr.usage.prompt_tokens == 0


def test_completion_result_with_usage():
    cr = CompletionResult(
        text='{"score": 8}',
        usage=TokenUsage(prompt_tokens=100, completion_tokens=50),
    )
    assert cr.usage.prompt_tokens == 100
    assert cr.usage.completion_tokens == 50


def test_tracker_empty():
    t = TokenUsageTracker()
    assert t.call_count == 0
    assert "0 total" in t.summary()


def test_tracker_accumulates():
    t = TokenUsageTracker()
    t.track(TokenUsage(prompt_tokens=100, completion_tokens=50))
    t.track(TokenUsage(prompt_tokens=200, completion_tokens=80))
    assert t.total_prompt_tokens == 300
    assert t.total_completion_tokens == 130
    assert t.call_count == 2
    s = t.summary()
    assert "430" in s
    assert "2 calls" in s
