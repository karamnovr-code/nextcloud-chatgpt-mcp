import asyncio
import pytest


@pytest.mark.asyncio
async def test_job_id_and_owner_isolation(tmp_path):
    from jobs import Jobs
    j=Jobs(tmp_path)
    with pytest.raises(ValueError):
        await j.status('../secret','test-owner')
    handle=await j.submit('read',b'hello world','note.txt',{},'test-owner')
    with pytest.raises(PermissionError):
        await j.status(handle['job_id'],'another')
    for _ in range(100):
        result=await j.status(handle['job_id'],'test-owner')
        if result['status']!='running':break
        await asyncio.sleep(.05)
    assert result['status']=='completed',result
    assert 'hello' in str(result)


@pytest.mark.asyncio
async def test_worker_rejects_unknown_operation(tmp_path):
    from jobs import Jobs
    j=Jobs(tmp_path)
    with pytest.raises(ValueError):
        await j.submit('shell',b'','x',{},'test-owner')
