import pytest


@pytest.mark.asyncio
async def test_tools_surface_has_no_delete_or_shell(tmp_path):
    from server import build_server
    from fastmcp import Client
    cfg={'nextcloud_url':'https://cloud.example','owner':'test-owner','read_roots':['ChatGPT Nextcloud'],'write_roots':['ChatGPT Nextcloud'],'jobs_dir':str(tmp_path)}
    app=build_server(cfg,auth=None)
    async with Client(app) as c:
        tools=await c.list_tools()
    names={t.name for t in tools}
    assert {'search','fetch','list_files','read_document','get_job','save_job','create_word','create_excel','convert_document','preview_page'}<=names
    assert not any('delete' in n or 'shell' in n or 'execute' in n for n in names)
    assert next(t for t in tools if t.name=='save_job').annotations.read_only_hint is False
    assert next(t for t in tools if t.name=='read_document').annotations.read_only_hint is True


@pytest.mark.asyncio
async def test_inprocess_unauthorized_call_denied(tmp_path):
    from server import build_server
    from fastmcp import Client
    cfg={'nextcloud_url':'https://cloud.example','owner':'test-owner','read_roots':['ChatGPT Nextcloud'],'write_roots':['ChatGPT Nextcloud'],'jobs_dir':str(tmp_path)}
    app=build_server(cfg,auth=None)
    async with Client(app) as c:
        with pytest.raises(Exception,match='Authentication required'):
            await c.call_tool('list_files',{})
