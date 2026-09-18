"""Persistent, owner-scoped document jobs. Workers never receive credentials."""
import asyncio
import json
import os
import re
import secrets
import shutil
import sys
import time
from pathlib import Path

OPERATIONS={'read','preview','create_docx','create_xlsx','convert'}


def atomic_json(path, value):
    tmp=path.with_suffix('.tmp')
    tmp.write_text(json.dumps(value,ensure_ascii=False),encoding='utf-8')
    tmp.chmod(0o600)
    tmp.replace(path)


class Jobs:
    def __init__(self, root):
        self.root=Path(root)
        self.root.mkdir(parents=True,exist_ok=True,mode=0o700)
        self.running={}
        self.capacity=asyncio.Semaphore(1)

    def directory(self, job_id, owner):
        if not re.fullmatch('[0-9a-f]{32}',job_id):
            raise ValueError('Invalid job identifier')
        d=self.root/job_id
        meta=json.loads((d/'meta.json').read_text())
        if meta['owner']!=owner:
            raise PermissionError('Job belongs to another account')
        return d,meta

    async def submit(self, op, data, filename, options, owner):
        if op not in OPERATIONS:
            raise ValueError('Unknown document operation')
        ident=secrets.token_hex(16)
        d=self.root/ident
        d.mkdir(mode=0o700)
        (d/'input.bin').write_bytes(data)
        (d/'input.bin').chmod(0o600)
        atomic_json(d/'request.json',{'operation':op,'filename':Path(filename).name,'options':options})
        atomic_json(d/'meta.json',{'owner':owner,'operation':op,'created':time.time(),'source_name':Path(filename).name})
        # Fixed interpreter/script/working directory. No shell, user commands or OAuth env.
        worker=Path(__file__).with_name('worker.py')
        env={'PATH':'/usr/bin:/bin','LANG':'C.UTF-8','HOME':str(d),'TMPDIR':str(d),'OMP_THREAD_LIMIT':'2'}
        task=asyncio.create_task(self._run(d,worker,env))
        self.running[ident]=task
        task.add_done_callback(lambda _:self.running.pop(ident,None))
        return {'job_id':ident,'status':'running','next_action':'get_job','note':'Poll get_job until completed; do not resubmit the same operation.'}

    async def _run(self,d,worker,env):
        async with self.capacity:
            await self._run_isolated(d,worker,env)

    async def _run_isolated(self,d,worker,env):
        try:
            if not shutil.which('bwrap'):
                raise RuntimeError('Sandbox unavailable')
            root=worker.parent.resolve(); d=d.resolve()
            cmd=['/usr/bin/bwrap','--unshare-all','--die-with-parent','--new-session',
                 '--ro-bind','/usr','/usr','--ro-bind','/lib','/lib','--ro-bind','/lib64','/lib64',
                 '--symlink','usr/bin','/bin','--symlink','usr/sbin','/sbin',
                 '--proc','/proc','--dev','/dev','--tmpfs','/tmp','--dir','/app',
                 '--ro-bind',str(root/'.venv'),'/app/.venv',
                 '--bind',str(d),'/work','--chdir','/app']
            # Optional preinstalled licensed document fonts, exposed read-only.
            if (root/'fonts').is_dir():
                cmd.extend(['--ro-bind', str(root/'fonts'), '/usr/local/share/fonts'])
            for script in ('worker.py','jobs.py','doc_engine.py'):
                cmd.extend(['--ro-bind',str(root/script),'/app/'+script])
            for path in ('/etc/fonts','/etc/ld.so.cache','/etc/libreoffice'):
                if Path(path).exists():cmd.extend(['--ro-bind',path,path])
            cmd.extend(['/app/.venv/bin/python','/app/worker.py','/work'])
            env.update(HOME='/work',TMPDIR='/work',NEXTCLOUD_DOCUMENT_SANDBOX='1')
            proc=await asyncio.create_subprocess_exec(*cmd,env=env,stdout=asyncio.subprocess.DEVNULL,stderr=asyncio.subprocess.DEVNULL)
            code=await proc.wait()
            if code and not (d/'result.json').exists():
                atomic_json(d/'result.json',{'status':'failed','error':'Document worker failed or exhausted resources. Try a smaller page range.'})
        except Exception:
            atomic_json(d/'result.json',{'status':'failed','error':'Document worker could not start'})

    async def status(self,job_id,owner):
        d,meta=self.directory(job_id,owner)
        if (d/'result.json').exists():
            return {'job_id':job_id,**json.loads((d/'result.json').read_text())}
        if job_id not in self.running:
            return {'job_id':job_id,'status':'interrupted','note':'Service restarted before completion; resubmit the document operation.'}
        return {'job_id':job_id,'status':'running','elapsed_seconds':round(time.time()-meta['created'])}

    def artifact(self,job_id,owner):
        d,_=self.directory(job_id,owner)
        result=json.loads((d/'result.json').read_text())
        if result.get('status')!='completed' or not result.get('artifact'):
            raise ValueError('Job has no completed artifact')
        return (d/'output.bin').read_bytes(),result['artifact']['extension']
