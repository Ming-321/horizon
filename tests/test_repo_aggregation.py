"""Tests for repo update aggregation in orchestrator."""

from datetime import datetime, timezone

from src.models import ContentItem, SourceType
from src.orchestrator import HorizonOrchestrator


def _make_commit_item(feed_name: str, title: str, published_at: datetime = None, idx: int = 0) -> ContentItem:
    return ContentItem(
        id=f"rss:{feed_name}:{idx}",
        source_type=SourceType.RSS,
        title=title,
        url=f"https://github.com/{feed_name}/commit/{idx}",
        content=f"Commit message for {title}",
        published_at=published_at or datetime(2026, 3, 12, 10, idx, tzinfo=timezone.utc),
        category="github-updates",
        metadata={"feed_name": feed_name},
    )


def test_multiple_commits_merged():
    items = [
        _make_commit_item("Superpowers Updates", "obra: Fix rendering bug", idx=0),
        _make_commit_item("Superpowers Updates", "obra: Add new feature", idx=1),
        _make_commit_item("Superpowers Updates", "obra: Update docs", idx=2),
    ]
    result = HorizonOrchestrator._aggregate_repo_updates(items)
    assert len(result) == 1

    merged = result[0]
    assert "Superpowers Updates: 3 updates" in merged.title
    assert merged.metadata["commit_count"] == 3
    assert merged.category == "github-updates"
    assert "- [" in merged.content
    lines = merged.content.strip().split("\n")
    assert len(lines) == 3


def test_single_commit_not_merged():
    items = [
        _make_commit_item("MemSearch Updates", "zilliz: Release v2.0", idx=0),
    ]
    result = HorizonOrchestrator._aggregate_repo_updates(items)
    assert len(result) == 1
    assert result[0].title == "zilliz: Release v2.0"
    assert "commit_count" not in result[0].metadata


def test_non_github_updates_unaffected():
    other_item = ContentItem(
        id="rss:hn:1",
        source_type=SourceType.RSS,
        title="Some HN Post",
        url="https://example.com/1",
        content="Content",
        published_at=datetime(2026, 3, 12, 10, 0, tzinfo=timezone.utc),
        category="hackernews",
        metadata={"feed_name": "HN"},
    )
    commit = _make_commit_item("Superpowers Updates", "obra: Fix bug", idx=0)
    result = HorizonOrchestrator._aggregate_repo_updates([other_item, commit])
    assert len(result) == 2
    titles = {r.title for r in result}
    assert "Some HN Post" in titles


def test_multiple_repos_separate_buckets():
    items = [
        _make_commit_item("Repo A", "Author: Commit A1", idx=0),
        _make_commit_item("Repo A", "Author: Commit A2", idx=1),
        _make_commit_item("Repo B", "Author: Commit B1", idx=0),
        _make_commit_item("Repo B", "Author: Commit B2", idx=1),
        _make_commit_item("Repo B", "Author: Commit B3", idx=2),
    ]
    result = HorizonOrchestrator._aggregate_repo_updates(items)
    assert len(result) == 2
    counts = sorted([r.metadata.get("commit_count", 1) for r in result])
    assert counts == [2, 3]


def test_merged_item_uses_newest_published_at():
    older = datetime(2026, 3, 12, 8, 0, tzinfo=timezone.utc)
    newer = datetime(2026, 3, 12, 12, 0, tzinfo=timezone.utc)
    items = [
        _make_commit_item("Test Repo", "Old commit", published_at=older, idx=0),
        _make_commit_item("Test Repo", "New commit", published_at=newer, idx=1),
    ]
    result = HorizonOrchestrator._aggregate_repo_updates(items)
    assert len(result) == 1
    assert result[0].published_at == newer
