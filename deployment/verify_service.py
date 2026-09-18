"""Run synthetic acceptance with EVERY [Service] property of the supplied unit.
Requires sudo; replaces only ExecStart. Never reads production OAuth state.
"""
import argparse,subprocess
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('unit',type=Path);p.add_argument('--script',default='/opt/nextcloud-mcp/smoke_worker.py');a=p.parse_args()
cmd=['sudo','-n','systemd-run','--unit=nextcloud-mcp-acceptance','--collect','--wait','--pipe']
section=None
for line in a.unit.read_text().splitlines():
 line=line.strip()
 if line.startswith('['):section=line
 elif section=='[Service]' and line and not line.startswith('#'):
  key,sep,value=line.partition('=')
  if sep and key!='ExecStart':cmd.extend(['--property',line])
cmd.extend(['/opt/nextcloud-mcp/.venv/bin/python',a.script])
raise SystemExit(subprocess.run(cmd).returncode)
