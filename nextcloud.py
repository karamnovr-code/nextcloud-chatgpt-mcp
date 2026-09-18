"""Fixed-origin Nextcloud WebDAV with explicit read/write boundaries."""
import hashlib
import mimetypes
import re
from urllib.parse import quote, unquote, urlsplit
from xml.etree import ElementTree as ET

import httpx


def normalize(path: str) -> str:
    if not isinstance(path, str) or path.startswith('/') or '\\' in path or ':' in path:
        raise ValueError('Use a relative Nextcloud path, not a URL or filesystem path')
    if any(ord(c) < 32 for c in path) or re.search(r'%[0-9a-fA-F]{2}', path):
        raise ValueError('Encoded or control characters are not allowed')
    path = path.rstrip('/')
    if path and any(p in ('', '.', '..') for p in path.split('/')):
        raise ValueError('Invalid path segment')
    return path


def under(path, root):
    return path == root or path.startswith(root + '/')


class Policy:
    def __init__(self, read_roots, write_roots, deny_roots=(), read_only_segments=()):
        self.read_roots = [normalize(p) for p in read_roots]
        self.write_roots = [normalize(p) for p in write_roots]
        self.deny_roots = [normalize(p) for p in deny_roots]
        self.read_only_segments = set(read_only_segments)

    def check(self, path, write=False):
        path = normalize(path)
        if any(under(path, denied) for denied in self.deny_roots):
            raise PermissionError('Path is explicitly denied by policy')
        roots = self.write_roots if write else self.read_roots
        if not path and not write:
            return path
        if not any(under(path, p) for p in roots):
            raise PermissionError('Path is outside the configured access roots')
        if write and any(segment in self.read_only_segments for segment in path.split('/')):
            raise PermissionError('Source and system folders are read-only')
        return path


class Nextcloud:
    def __init__(self, config, token, transport=None):
        self.config = config
        self.base = config['nextcloud_url'].rstrip('/')
        self.owner = config['owner']
        self.token = token
        self.transport = transport
        self.policy = Policy(
            config['read_roots'], config['write_roots'],
            config.get('deny_roots', ()), config.get('read_only_segments', ()),
        )
        self.dav = self.base + '/remote.php/dav/files/' + quote(self.owner, safe='') + '/'

    def client(self):
        return httpx.AsyncClient(headers={'Authorization':'Bearer '+self.token},
                                 timeout=httpx.Timeout(120, connect=15), follow_redirects=False,
                                 transport=self.transport, trust_env=False)

    def url(self, path):
        return self.dav + quote(normalize(path), safe='/')

    def link(self, path):
        parent, _, name = path.rpartition('/')
        return self.base + '/index.php/apps/files/?dir=' + quote('/'+parent, safe='') + '&scrollto=' + quote(name,safe='')

    @staticmethod
    def checked(response):
        if response.status_code in (401,403):
            raise PermissionError('Nextcloud access denied; reconnect or check permissions')
        if response.status_code == 404:
            raise FileNotFoundError('Nextcloud file or folder not found')
        if response.status_code == 412:
            raise FileExistsError('File already exists or changed; no overwrite was performed')
        if not 200 <= response.status_code < 300:
            raise RuntimeError(f'Nextcloud returned HTTP {response.status_code}')
        return response

    async def read(self, path):
        path = self.policy.check(path)
        ceiling=self.config.get('max_input_bytes',256*1024*1024)
        chunks=[]; size=0
        async with self.client() as client:
            async with client.stream('GET',self.url(path)) as r:
                self.checked(r)
                if int(r.headers.get('content-length','0'))>ceiling:
                    raise ValueError('File exceeds the document worker memory envelope; split it into smaller source files')
                async for chunk in r.aiter_bytes(1024*1024):
                    size+=len(chunk)
                    if size>ceiling:
                        raise ValueError('File exceeds the document worker memory envelope; no partial content returned')
                    chunks.append(chunk)
                etag=r.headers.get('etag')
        return b''.join(chunks),etag

    async def list(self, path='', offset=0, limit=100):
        path = self.policy.check(path)
        if offset < 0 or not 1 <= limit <= 500:
            raise ValueError('Use offset>=0 and 1<=limit<=500; page through the remaining results')
        async with self.client() as client:
            r = self.checked(await client.request('PROPFIND', self.url(path), headers={'Depth':'1'}, content=b'<?xml version="1.0"?><d:propfind xmlns:d="DAV:" xmlns:oc="http://owncloud.org/ns"><d:prop><d:resourcetype/><d:getcontentlength/><d:getlastmodified/><d:getetag/><oc:fileid/></d:prop></d:propfind>'))
        root = ET.fromstring(r.content)
        rows=[]
        for response in root.findall('{DAV:}response'):
            href=response.findtext('{DAV:}href','')
            href_path=unquote(urlsplit(href).path)
            prefix=urlsplit(self.dav).path
            if not href_path.startswith(prefix):
                continue
            candidate=href_path[len(prefix):].rstrip('/')
            if candidate == path:
                continue
            try:
                self.policy.check(candidate)
            except (ValueError,PermissionError):
                continue
            prop=response.find('.//{DAV:}prop')
            if prop is None:
                continue
            rows.append({'path':candidate,'name':candidate.rsplit('/',1)[-1],
                         'is_dir':prop.find('.//{DAV:}collection') is not None,
                         'size':int(prop.findtext('{DAV:}getcontentlength','0') or 0),
                         'etag':prop.findtext('{DAV:}getetag'),
                         'file_id':prop.findtext('{http://owncloud.org/ns}fileid'),
                         'modified':prop.findtext('{DAV:}getlastmodified'),
                         'url':self.link(candidate)})
        rows.sort(key=lambda x:(not x['is_dir'],x['path'].casefold()))
        return {'entries':rows[offset:offset+limit], 'total':len(rows),
                'next_offset':offset+limit if offset+limit<len(rows) else None}

    async def mkdir(self, path):
        path=self.policy.check(path,write=True)
        async with self.client() as client:
            r=await client.request('MKCOL',self.url(path))
            if r.status_code==405:
                await self.list(path,limit=1)
                return {'path':path,'created':False}
            self.checked(r)
        return {'path':path,'created':True,'url':self.link(path)}

    async def write(self, path, data, etag=None):
        path=self.policy.check(path,write=True)
        headers={'Content-Type':mimetypes.guess_type(path)[0] or 'application/octet-stream'}
        if etag:
            if etag=='*' or '\n' in etag or '\r' in etag:
                raise ValueError('Exact previously read ETag required')
            headers['If-Match']=etag
        else:
            headers['If-None-Match']='*'
        async with self.client() as client:
            self.checked(await client.put(self.url(path),content=data,headers=headers))
        readback,new_etag=await self.read(path)
        digest=hashlib.sha256(data).hexdigest()
        if hashlib.sha256(readback).hexdigest()!=digest:
            raise RuntimeError('Written file failed readback checksum; inspect result before retrying')
        return {'path':path,'url':self.link(path),'bytes':len(data),'sha256':digest,'etag':new_etag,'verified':True}

    async def copy(self, source, destination):
        data,_=await self.read(source)
        return await self.write(destination,data)

    async def search(self, query, root, offset=0, limit=100):
        root=self.policy.check(root)
        if not root:
            raise ValueError('Choose one root from list_files first')
        if offset<0 or not 1<=limit<=500:
            raise ValueError('Invalid pagination')
        # Nextcloud DAV SEARCH searches names, not document bodies. State this explicitly.
        from xml.sax.saxutils import escape
        pattern='%'+query.replace('%','').replace('_','')+'%'
        xml=f'''<?xml version="1.0"?><d:searchrequest xmlns:d="DAV:"><d:basicsearch><d:select><d:prop><d:displayname/><d:resourcetype/><d:getcontentlength/></d:prop></d:select><d:from><d:scope><d:href>{escape('/files/'+self.owner+'/'+root)}</d:href><d:depth>infinity</d:depth></d:scope></d:from><d:where><d:like><d:prop><d:displayname/></d:prop><d:literal>{escape(pattern)}</d:literal></d:like></d:where></d:basicsearch></d:searchrequest>'''
        async with self.client() as client:
            r=self.checked(await client.request('SEARCH',self.base+'/remote.php/dav/',content=xml.encode(),headers={'Content-Type':'application/xml'}))
        rows=[]
        for item in ET.fromstring(r.content).findall('{DAV:}response'):
            p=unquote(urlsplit(item.findtext('{DAV:}href','')).path)
            prefix=urlsplit(self.dav).path
            if not p.startswith(prefix):
                continue
            p=p[len(prefix):].rstrip('/')
            try:
                self.policy.check(p)
            except (ValueError,PermissionError):
                continue
            rows.append({'id':p,'title':p.rsplit('/',1)[-1],'url':self.link(p)})
        return {'results':rows[offset:offset+limit],'total':len(rows),'next_offset':offset+limit if offset+limit<len(rows) else None,'search_mode':'filename; use read_document for contents'}
