"""校验 httpx 实际路由选择；不启动服务、不发送请求。"""
import httpx
import pytest

from m3.m1_client import M1Client
from shared.config import Settings


@pytest.mark.parametrize('base_url', ['http://127.0.0.1:8000', 'http://m1:8000'])
def test_internal_m1_requests_ignore_environment_proxy(monkeypatch, base_url):
    for key in ('HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY'):
        monkeypatch.delenv(key.lower(), raising=False)
        monkeypatch.setenv(key, 'http://127.0.0.1:9')
    monkeypatch.delenv('no_proxy', raising=False)
    monkeypatch.setenv('NO_PROXY', '')
    url = httpx.URL(base_url + '/api/meta')
    # 先确认测试环境确实会将默认客户端导向代理，防止测试空跑。
    # httpx 已固定为 0.28.1；这里检查其 transport 选择，不访问私有业务数据。
    with httpx.Client() as default_client:
        assert default_client._transport_for_url(url) is not default_client._transport
    adapter = M1Client(Settings(m1_base_url=base_url))
    try:
        assert adapter.client._transport_for_url(url) is adapter.client._transport
    finally:
        adapter.close()
