"""SiYuan 客户端测试。"""

from unittest.mock import MagicMock

import pytest

from src.config import SiyuanConfig
from src.siyuan import SiyuanClient, SiyuanError


class FakeResponse:
    def __init__(self, status_code=200, body=None, text=""):
        self.status_code = status_code
        self._body = body
        self.text = text

    def json(self):
        return self._body


def _client_with(monkeypatch, responses):
    session = MagicMock()
    session.headers = {}
    session.post.side_effect = [
        FakeResponse(200, body) if isinstance(body, dict) else FakeResponse(200, body)
        for body in responses
    ]
    monkeypatch.setattr("src.siyuan.requests.Session", lambda: session)
    return SiyuanClient(SiyuanConfig(token="abc"))


def test_post_returns_data(monkeypatch):
    client = _client_with(monkeypatch, [{"code": 0, "data": {"notebooks": [{"id": "n1", "name": "N"}]}}])
    notebooks = client.list_notebooks()
    assert notebooks == [{"id": "n1", "name": "N"}]


def test_post_raises_on_business_error(monkeypatch):
    session = MagicMock()
    session.headers = {}
    session.post.return_value = FakeResponse(202, {"code": 403, "msg": "forbidden"})
    monkeypatch.setattr("src.siyuan.requests.Session", lambda: session)
    client = SiyuanClient(SiyuanConfig(token="abc"))
    with pytest.raises(SiyuanError):
        client.list_notebooks()