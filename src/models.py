"""Core data models for Horizon."""

from datetime import datetime
from enum import Enum
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, HttpUrl, Field


class SourceType(str, Enum):
    """Supported information source types."""
    GITHUB = "github"
    HACKERNEWS = "hackernews"
    RSS = "rss"
    REDDIT = "reddit"
    TELEGRAM = "telegram"


class ContentItem(BaseModel):
    """Unified content item model from any source."""

    id: str  # Format: {source}:{subtype}:{native_id}
    source_type: SourceType
    title: str
    url: HttpUrl
    content: Optional[str] = None
    author: Optional[str] = None
    published_at: datetime
    fetched_at: datetime = Field(default_factory=datetime.utcnow)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    category: Optional[str] = None

    # AI analysis results
    ai_score: Optional[float] = None  # 0-10 importance score
    ai_reason: Optional[str] = None
    ai_summary: Optional[str] = None
    ai_tags: List[str] = Field(default_factory=list)


class AIProvider(str, Enum):
    """Supported AI providers."""
    ANTHROPIC = "anthropic"
    OPENAI = "openai"
    GEMINI = "gemini"
    DOUBAO = "doubao"


class AIConfig(BaseModel):
    """AI client configuration."""

    provider: AIProvider
    model: str
    base_url: Optional[str] = None
    api_key_env: str
    temperature: float = 0.3
    max_tokens: int = 4096
    languages: List[str] = Field(default_factory=lambda: ["en"])


class GitHubSourceConfig(BaseModel):
    """GitHub source configuration."""

    type: str  # "user_events", "repo_releases", etc.
    username: Optional[str] = None
    owner: Optional[str] = None
    repo: Optional[str] = None
    enabled: bool = True
    category: Optional[str] = None
    scoring_prompt_file: Optional[str] = None


class HackerNewsConfig(BaseModel):
    """Hacker News configuration."""

    enabled: bool = True
    fetch_top_stories: int = 30
    min_score: int = 100
    category: str = "hackernews"
    scoring_prompt_file: Optional[str] = None


class RSSSourceConfig(BaseModel):
    """RSS feed source configuration."""

    name: str
    url: HttpUrl
    enabled: bool = True
    category: Optional[str] = None
    scoring_prompt_file: Optional[str] = None


class RedditSubredditConfig(BaseModel):
    """Configuration for monitoring a specific subreddit."""
    subreddit: str
    enabled: bool = True
    sort: str = "hot"           # hot, new, top, rising
    time_filter: str = "day"    # hour, day, week, month, year, all (only for top/controversial)
    fetch_limit: int = 25
    min_score: int = 10
    category: str = "reddit"
    scoring_prompt_file: Optional[str] = None


class RedditUserConfig(BaseModel):
    """Configuration for monitoring a specific Reddit user."""
    username: str               # without u/ prefix
    enabled: bool = True
    sort: str = "new"
    fetch_limit: int = 10
    category: str = "reddit"
    scoring_prompt_file: Optional[str] = None


class RedditConfig(BaseModel):
    """Reddit source configuration."""
    enabled: bool = True
    subreddits: List[RedditSubredditConfig] = Field(default_factory=list)
    users: List[RedditUserConfig] = Field(default_factory=list)
    fetch_comments: int = 5     # top comments per post, 0 to disable


class TelegramChannelConfig(BaseModel):
    """Configuration for monitoring a specific Telegram channel."""
    channel: str            # channel username, e.g. "zaihuapd"
    enabled: bool = True
    fetch_limit: int = 20
    category: str = "telegram"
    scoring_prompt_file: Optional[str] = None


class TelegramConfig(BaseModel):
    """Telegram source configuration."""
    enabled: bool = True
    channels: List[TelegramChannelConfig] = Field(default_factory=list)


class SourcesConfig(BaseModel):
    """All sources configuration."""

    github: List[GitHubSourceConfig] = Field(default_factory=list)
    hackernews: HackerNewsConfig = Field(default_factory=HackerNewsConfig)
    rss: List[RSSSourceConfig] = Field(default_factory=list)
    reddit: RedditConfig = Field(default_factory=RedditConfig)
    telegram: TelegramConfig = Field(default_factory=TelegramConfig)


class EmailConfig(BaseModel):
    """Email configuration for updates/subscriptions."""
    imap_server: str
    imap_port: int = 993
    smtp_server: str
    smtp_port: int = 465
    email_address: str
    password_env: str = "EMAIL_PASSWORD"
    sender_name: str = "Horizon Daily"
    subscribe_keyword: str = "SUBSCRIBE"
    unsubscribe_keyword: str = "UNSUBSCRIBE"
    enabled: bool = False


class FilteringConfig(BaseModel):
    """Content filtering configuration."""

    ai_score_threshold: float = 7.0
    time_window_hours: int = 24


class ScoringConfig(BaseModel):
    """Per-group scoring configuration."""
    enabled: bool = True
    prompt_file: Optional[str] = None
    threshold: float = 7.0


class SummaryGroupConfig(BaseModel):
    """Per-group summary configuration (future extension)."""
    prompt_file: Optional[str] = None


class GroupConfig(BaseModel):
    """Content group (newspaper section) configuration."""
    id: str
    name: str
    default: bool = False
    categories: List[str] = Field(default_factory=list)
    scoring: ScoringConfig = Field(default_factory=ScoringConfig)
    summary: SummaryGroupConfig = Field(default_factory=SummaryGroupConfig)
    enrichment_mode: str = "full"
    enrichment_prompt_file: Optional[str] = None


class BriefConfig(BaseModel):
    """Brief (concise) output configuration."""
    enabled: bool = False
    top_n: int = 10


class HtmlConfig(BaseModel):
    """HTML detail output configuration."""
    enabled: bool = False
    serve_port: int = 8080


class OutputConfig(BaseModel):
    """Output channels configuration."""
    brief: BriefConfig = Field(default_factory=BriefConfig)
    html: HtmlConfig = Field(default_factory=HtmlConfig)


class WxPusherConfig(BaseModel):
    """WxPusher notification configuration."""
    enabled: bool = False
    app_token_env: str = "WXPUSHER_APP_TOKEN"
    uids_env: str = "WXPUSHER_UIDS"
    topic_ids_env: str = "WXPUSHER_TOPIC_IDS"


class NotificationsConfig(BaseModel):
    """Notification channels configuration."""
    wxpusher: WxPusherConfig = Field(default_factory=WxPusherConfig)


class Config(BaseModel):
    """Main configuration model."""

    version: str = "1.0"
    ai: AIConfig
    sources: SourcesConfig
    filtering: FilteringConfig
    email: Optional[EmailConfig] = None
    groups: List[GroupConfig] = Field(default_factory=list)
    output: OutputConfig = Field(default_factory=OutputConfig)
    notifications: NotificationsConfig = Field(default_factory=NotificationsConfig)
