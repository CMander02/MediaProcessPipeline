import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import ssl
import urllib.error

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
from app.services.ingestion.platform.bilibili import api, auth, dash, login


def test_anonymous_api_never_loads_stored_cookie(monkeypatch):
    monkeypatch.setattr(api, 'get_cookie', Mock(side_effect=AssertionError('stale cookie')))
    calls = []
    monkeypatch.setattr(api, 'http_json', lambda url, **kw: calls.append((url, kw)) or {'data': {}})
    api.view('BVtest', cookie='')
    api.playurl('BVtest', 1, 2, qn=16, fnval=1, cookie='')
    assert all(kw['cookie'] == '' for _, kw in calls)
    assert '/x/player/playurl?' in calls[1][0]
    assert 'qn=16' in calls[1][0]


@pytest.mark.parametrize('logged_in', [True, False])
def test_download_recovers_with_fresh_anonymous_progressive(monkeypatch, tmp_path, logged_in):
    check = Mock(return_value=logged_in)
    monkeypatch.setattr(auth, 'is_logged_in', check)
    monkeypatch.setattr(auth, 'get_cookie', lambda: 'SESSDATA=old')
    view = Mock(return_value={'title': '访谈', 'aid': 1, 'pages': [
        {'page': 1, 'cid': 10}, {'page': 2, 'cid': 20, 'duration': 120}]})
    monkeypatch.setattr(api, 'view', view)
    play = Mock(side_effect=[{'dash': {'video': [{'id': 64, 'baseUrl': 'video'}],
                                      'audio': [{'id': 1, 'baseUrl': 'audio'}]}},
                             {'quality': 16, 'durl': [{'url': 'progressive'}]}])
    monkeypatch.setattr(api, 'playurl', play)
    transfers = []
    def transfer(url, backups, dest, title, referer, cookie):
        transfers.append((url, cookie))
        dest.write_bytes(b'partial')
        if url == 'audio':
            raise dash.DownloadError('SSL EOF')
    monkeypatch.setattr(dash, '_try_download_with_backup', transfer)
    def mux(command, **kwargs):
        Path(command[-1]).write_bytes(b'mp4')
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(dash.subprocess, 'run', mux)
    result, info = dash.download_video('BVtest', tmp_path, page_number=2)
    assert result.read_bytes() == b'mp4'
    assert info['actual_qn'] == 16 and info['cid'] == 20
    assert check.call_count == 1
    assert play.call_args.kwargs == {'qn': 16, 'fnval': 1, 'cookie': ''}
    assert transfers[-1] == ('progressive', '')
    assert not list(tmp_path.glob('*.m4s'))
    assert view.call_args.kwargs['cookie'] == ('SESSDATA=old' if logged_in else '')


def test_tls_eof_uses_second_transport(monkeypatch, tmp_path):
    monkeypatch.setattr(dash, '_download_stream', Mock(side_effect=urllib.error.URLError(ssl.SSLEOFError())))
    fallback = Mock()
    monkeypatch.setattr(dash, '_download_stream_httpx', fallback)
    dash._try_download_with_backup('https://cdn.test/a', [], tmp_path / 'a', 'audio', 'ref')
    assert fallback.call_count == 1


def test_disk_failure_does_not_retry_network(monkeypatch, tmp_path):
    monkeypatch.setattr(dash, '_download_stream', Mock(side_effect=OSError('disk full')))
    fallback = Mock()
    monkeypatch.setattr(dash, '_download_stream_httpx', fallback)
    with pytest.raises(OSError):
        dash._try_download_with_backup('url', [], tmp_path / 'a', 'audio', 'ref')
    fallback.assert_not_called()


def qr_service(monkeypatch, responses):
    service = login.BilibiliLoginService()
    def handler(request):
        return httpx.Response(200, json=responses.pop(0), request=request)
    monkeypatch.setattr(service, '_client', lambda: httpx.Client(transport=httpx.MockTransport(handler)))
    saved = Mock()
    monkeypatch.setattr(login, 'patch_runtime_settings', saved)
    return service, saved


def test_qr_states_and_credentials_persist_only_after_confirmation(monkeypatch):
    responses = [{'code': 0, 'data': {'url': 'https://passport.bilibili.com/qr', 'qrcode_key': 'secret'}},
                 *[{'code': 0, 'data': {'code': code}} for code in (86101, 86090)],
                 {'code': 0, 'data': {'code': 0, 'url': 'https://www.bilibili.com/?SESSDATA=a%2Cb&bili_jct=csrf&DedeUserID=123'}},
                 {'code': 0, 'data': {'isLogin': True, 'mid': 123}}]
    service, saved = qr_service(monkeypatch, responses)
    qr = service.generate()
    assert qr['image'].startswith('data:image/svg+xml;base64,')
    assert 'secret' not in str(qr)
    assert service.poll(qr['session_id'])['state'] == 'waiting'
    assert service.poll(qr['session_id'])['state'] == 'scanned'
    saved.assert_not_called()
    result = service.poll(qr['session_id'])
    assert result == {'state': 'success', 'message': '登录成功，凭据已保存', 'uid': '123'}
    saved.assert_called_once_with({'bilibili_sessdata': 'a%2Cb', 'bilibili_bili_jct': 'csrf', 'bilibili_dede_user_id': '123'})
    assert service.poll(qr['session_id']) == result
    assert 'credentials' not in service._sessions[qr['session_id']]


def test_qr_missing_credentials_does_not_overwrite_login(monkeypatch):
    service, saved = qr_service(monkeypatch, [
        {'code': 0, 'data': {'url': 'https://passport.bilibili.com/qr', 'qrcode_key': 'secret'}},
        {'code': 0, 'data': {'code': 0, 'url': ''}}])
    qr = service.generate()
    with pytest.raises(RuntimeError, match='凭据不完整'):
        service.poll(qr['session_id'])
    saved.assert_not_called()


def test_expired_and_unknown_sessions_do_not_request_bilibili(monkeypatch):
    service, saved = qr_service(monkeypatch, [])
    service._sessions['old'] = {'key': 'secret', 'expires': 0}
    assert service.poll('old')['state'] == 'expired'
    assert service.poll('unknown')['state'] == 'expired'
    saved.assert_not_called()
