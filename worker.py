"""Credential-free document subprocess; fixed operations only."""
import hashlib
import json
import os
import resource
import sys
from pathlib import Path

from jobs import atomic_json


def main():
    os.umask(0o077)
    # These are worker resource boundaries, not per-user request quotas.
    resource.setrlimit(resource.RLIMIT_CORE,(0,0))
    resource.setrlimit(resource.RLIMIT_AS,(3*1024**3,3*1024**3))
    resource.setrlimit(resource.RLIMIT_CPU,(1200,1200))
    d=Path(sys.argv[1])
    try:
        import doc_engine as e
        req=json.loads((d/'request.json').read_text())
        op=req['operation']; opts=req['options']; name=req['filename']
        data=(d/'input.bin').read_bytes()
        output=None
        if op=='read':
            if Path(name).suffix.lower() in ('.txt','.md','.csv','.json','.xml','.html'):
                text=data.decode('utf-8-sig')
                lines=text.splitlines(); start=opts.get('start',1); count=opts.get('count',100)
                if start<1 or count<1: raise ValueError('Positive start/count required')
                result={'unit_type':'line','total_units':len(lines),'start':start,'units':lines[start-1:start-1+count],
                        'next_start':start+count if start-1+count<len(lines) else None,'source':name}
            else:
                result=e.read_document(data,name,**opts)
        elif op=='preview':
            output=e.preview(data,name,**opts); ext='png'; result={}
        elif op=='create_docx':
            output=e.create_docx(opts,data or None); ext='docx'; result={}
        elif op=='create_xlsx':
            output=e.create_xlsx(opts,data or None); ext='xlsx'; result={}
        elif op=='convert':
            output=e.convert_document(data,name,**opts); ext=opts['target_format']; result={}
        else:
            raise ValueError('Unsupported operation')
        response={'status':'completed','result':result}
        if output is not None:
            (d/'output.bin').write_bytes(output)
            response['artifact']={'extension':ext,'bytes':len(output),'sha256':hashlib.sha256(output).hexdigest()}
        atomic_json(d/'result.json',response)
    except Exception as exc:
        # Engine messages contain no credentials; do not emit tracebacks or local paths.
        atomic_json(d/'result.json',{'status':'failed','error':type(exc).__name__+': '+str(exc)[:500]})


if __name__=='__main__':
    main()
