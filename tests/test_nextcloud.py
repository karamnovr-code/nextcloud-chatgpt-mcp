import hashlib
import httpx
import pytest


def subject():
    import nextcloud
    return nextcloud


@pytest.mark.parametrize('path', ['../x','Documents/../secret','https://evil/x','/etc/passwd','Photos\\..\\x','Photos/%2e%2e/x','Photos/%252e%252e/x','Photos/x\x00','Photos//x'])
def test_reject_escape(path):
    with pytest.raises(ValueError):
        subject().normalize(path)


def test_source_writes_rejected_even_under_allowed_read_root():
    p = subject().Policy(
        ['Documents', 'Photos', 'ChatGPT Nextcloud'],
        ['ChatGPT Nextcloud', 'Documents/Results'],
        deny_roots=['Documents/System'],
        read_only_segments=['Source materials'],
    )
    p.check('Documents/Source materials/input.pdf')
    with pytest.raises(PermissionError):
        p.check('Documents/Source materials/input.pdf', write=True)
    with pytest.raises(PermissionError):
        p.check('Documents/System/config.txt')
    with pytest.raises(PermissionError):
        p.check('ChatGPT Nextcloud2/x', write=True)


@pytest.mark.asyncio
async def test_write_is_create_only_and_readback_verified():
    saved = {}
    def handler(req):
        if req.method == 'PUT':
            assert req.headers['if-none-match'] == '*'
            saved['data'] = req.content
            return httpx.Response(201)
        if req.method == 'GET':
            return httpx.Response(200, content=saved['data'], headers={'etag':'"one"'})
        raise AssertionError(req.method)
    cfg={'nextcloud_url':'https://cloud.example','owner':'test-owner','read_roots':['ChatGPT Nextcloud'],'write_roots':['ChatGPT Nextcloud']}
    n=subject().Nextcloud(cfg,'fixture',transport=httpx.MockTransport(handler))
    result=await n.write('ChatGPT Nextcloud/test.txt',b'hello')
    assert result['sha256']==hashlib.sha256(b'hello').hexdigest()
    assert result['verified'] is True


@pytest.mark.asyncio
async def test_conflict_does_not_retry_overwrite():
    seen=[]
    def handler(req):
        seen.append(req.method)
        return httpx.Response(412)
    cfg={'nextcloud_url':'https://cloud.example','owner':'test-owner','read_roots':['ChatGPT Nextcloud'],'write_roots':['ChatGPT Nextcloud']}
    n=subject().Nextcloud(cfg,'fixture',transport=httpx.MockTransport(handler))
    with pytest.raises(FileExistsError):
        await n.write('ChatGPT Nextcloud/test.txt',b'hello')
    assert seen==['PUT']


@pytest.mark.asyncio
async def test_download_aborts_at_memory_envelope():
    cfg={'nextcloud_url':'https://cloud.example','owner':'test-owner','read_roots':['ChatGPT Nextcloud'],'write_roots':['ChatGPT Nextcloud'],'max_input_bytes':4}
    n=subject().Nextcloud(cfg,'fixture',transport=httpx.MockTransport(lambda _:httpx.Response(200,content=b'12345')))
    with pytest.raises(ValueError,match='memory'):
        await n.read('ChatGPT Nextcloud/large.pdf')
