"""Tests for WxPusherService."""

import os
from unittest.mock import patch, MagicMock

from src.models import WxPusherConfig
from src.services.wxpusher import WxPusherService, MAX_SUMMARY_LEN, MAX_CONTENT_LEN


def _make_config(**overrides):
    return WxPusherConfig(**overrides)


def _make_service(token="test_token", uids="UID_A,UID_B", topic_ids="", **cfg):
    env = {
        "WXPUSHER_APP_TOKEN": token,
        "WXPUSHER_UIDS": uids,
        "WXPUSHER_TOPIC_IDS": topic_ids,
    }
    with patch.dict(os.environ, env, clear=False):
        return WxPusherService(_make_config(**cfg))


@patch("wxpusher.WxPusher.send_message")
def test_push_success(mock_send):
    mock_send.return_value = {
        "success": True,
        "data": [{"uid": "UID_A", "code": 1000}, {"uid": "UID_B", "code": 1000}],
    }
    svc = _make_service()
    result = svc.push("<p>Hello</p>", "Daily Brief")
    assert result is True
    mock_send.assert_called_once()
    call_kwargs = mock_send.call_args
    assert call_kwargs.kwargs["content_type"] == 2
    assert call_kwargs.kwargs["uids"] == ["UID_A", "UID_B"]


def test_push_no_token():
    svc = _make_service(token="")
    result = svc.push("<p>test</p>", "test")
    assert result is False


def test_push_no_uids_no_topics():
    svc = _make_service(uids="", topic_ids="")
    result = svc.push("<p>test</p>", "test")
    assert result is False


@patch("wxpusher.WxPusher.send_message")
def test_push_api_exception(mock_send):
    mock_send.side_effect = ConnectionError("network error")
    svc = _make_service()
    result = svc.push("<p>test</p>", "test")
    assert result is False


@patch("wxpusher.WxPusher.send_message")
def test_summary_truncation(mock_send):
    mock_send.return_value = {"success": True, "data": [{"code": 1000}]}
    svc = _make_service()
    long_summary = "A" * 50
    svc.push("<p>test</p>", long_summary)
    call_kwargs = mock_send.call_args.kwargs
    assert len(call_kwargs["summary"]) == MAX_SUMMARY_LEN


@patch("wxpusher.WxPusher.send_message")
def test_content_truncation(mock_send):
    mock_send.return_value = {"success": True, "data": [{"code": 1000}]}
    svc = _make_service()
    long_content = "X" * (MAX_CONTENT_LEN + 100)
    svc.push(long_content, "test")
    actual_content = mock_send.call_args.args[0]
    assert len(actual_content) <= MAX_CONTENT_LEN


@patch("wxpusher.WxPusher.send_message")
def test_push_with_topic_ids(mock_send):
    mock_send.return_value = {"success": True, "data": [{"code": 1000}]}
    svc = _make_service(uids="UID_A", topic_ids="123,456")
    svc.push("<p>test</p>", "test")
    call_kwargs = mock_send.call_args.kwargs
    assert call_kwargs["topic_ids"] == [123, 456]
