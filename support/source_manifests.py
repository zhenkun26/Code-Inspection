"""Bounded optional source observations; trusted host, ordinary checkouts.

No snapshot atomicity or build-provenance claim. Host controls Git metadata and
writers throughout capture. Unsupported layouts fail; historical validation is
pure and never reopens a source directory. Git and file payloads remain bytes.
"""
import configparser
import hashlib
import json
import os
from pathlib import Path
import re
import selectors
import shutil
import stat
import struct
import subprocess
import tempfile
import time
import unicodedata

from support.git_runtime import git_environment
from support.local_files import checked_root, read_bytes, require

MAX_FILES = 1024
MAX_FILE = 1048576
MAX_TOTAL = 8388608
MAX_MANIFEST = 1048576
MAX_VISITED = 4096
MAX_DEPTH = 32
CONFIG_FIELDS = {'mode', 'scope', 'exclusions', 'max_files', 'max_file_bytes',
                 'max_total_bytes', 'isolation'}


def _keys(value, fields):
    require(isinstance(value, dict) and set(value) == set(fields), 'UNKNOWN: manifest fields')


def _path(value, root=False):
    require(isinstance(value, str) and bool(value), 'UNKNOWN: manifest path')
    if root and value == '.': return value
    raw = value.encode('utf-8', 'strict')
    require(len(raw) <= 4096, 'UNKNOWN: manifest path limit')
    names = value.split('/')
    require(len(names) <= MAX_DEPTH, 'UNKNOWN: manifest depth limit')
    for name in names:
        require(name not in {'', '.', '..'} and not name.endswith((' ', '.'))
                and not any(ord(c) < 32 or c in '\\:*?"<>|\x7f' for c in name),
                'UNKNOWN: unsafe manifest path')
        require(re.fullmatch(r'CON|PRN|AUX|NUL|CLOCK\$|CONIN\$|CONOUT\$|COM[1-9¹²³]|LPT[1-9¹²³]',
                             name.split('.')[0], re.IGNORECASE) is None, 'UNKNOWN: reserved manifest path')
    return value


def _config(config):
    _keys(config, CONFIG_FIELDS)
    require(config['mode'] in {'directory', 'git-worktree'}, 'UNKNOWN: manifest mode')
    require(config['isolation'] == 'host-controlled', 'BLOCKED: host-controlled source isolation required')
    for key in ('scope', 'exclusions'):
        values = config[key]
        require(isinstance(values, list) and len(values) <= MAX_FILES and (key != 'scope' or values), 'UNKNOWN: manifest scope')
        for value in values: _path(value, root=key == 'scope')
        require(values == sorted(set(values), key=lambda x: x.encode('utf-8')), 'UNKNOWN: canonical unique scope required')
    for key, cap in [('max_files', MAX_FILES), ('max_file_bytes', MAX_FILE), ('max_total_bytes', MAX_TOTAL)]:
        require(type(config[key]) is int and 0 < config[key] <= cap, 'UNKNOWN: manifest limit')


def validate_config(config):
    """Pure configuration validation for host-policy adapters."""
    _config(config)


def _covered(path, prefixes):
    return any(prefix == '.' or path == prefix or path.startswith(prefix+'/') for prefix in prefixes)


def _included(path, config):
    return '.git' not in path.split('/') and _covered(path, config['scope']) and not _covered(path, config['exclusions'])


def _coverage(paths, config):
    for scope in config['scope']:
        require(scope == '.' or any(path == scope or path.startswith(scope+'/') for path in paths),
                'UNKNOWN: requested manifest scope unavailable or wholly excluded')


def _not_pointer(data):
    require(not data.startswith(b'version https://git-lfs.github.com/spec/v1'), 'UNKNOWN: LFS pointer unsupported')


def _collision(paths):
    seen = {}
    for path in paths:
        _path(path, root=True)
        # Check all prefixes too: a/A descendant collisions are ambiguous.
        for count in range(1, len(path.split('/'))+1):
            prefix = '/'.join(path.split('/')[:count])
            key = unicodedata.normalize('NFC', prefix).casefold()
            require(key not in seen or seen[key] == prefix, 'UNKNOWN: case/Unicode path collision')
            seen[key] = prefix


def _sha(value, size=64):
    require(isinstance(value, str) and re.fullmatch('[0-9a-f]{'+str(size)+'}', value) is not None,
            'UNKNOWN: manifest hash')


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False).encode()


def _file(value, config, git=False, windows=False):
    _keys(value, {'mode', 'size_bytes', 'sha256'} | ({'oid'} if git else set()))
    require(type(value['mode']) is int, 'UNKNOWN: unsupported manifest mode')
    valid_mode = value['mode'] in {0o100644, 0o100755} if git else (value['mode'] in {0,1} if windows else 0 <= value['mode'] <= 0o7777)
    require(type(value['mode']) is int and valid_mode,
            'UNKNOWN: unsupported manifest mode')
    require(type(value['size_bytes']) is int and 0 <= value['size_bytes'] <= config['max_file_bytes'],
            'UNKNOWN: manifest file limit')
    _sha(value['sha256'])
    if git: _sha(value['oid'], 40)


def validate(manifest):
    """Validate stored shape/coverage semantics; no live-source read or policy grant."""
    require(isinstance(manifest, dict), 'UNKNOWN: manifest object required')
    config = manifest.get('config'); _config(config)
    git = config['mode'] == 'git-worktree'
    _keys(manifest, {'schema', 'mode_model', 'config', 'entries'} | ({'base','head','head_tree','directories'} if git else set()))
    require(manifest['schema'] == 'source-manifest/v1' and manifest['mode_model'] in {'posix-permissions/v1','windows-readonly/v1'}, 'UNKNOWN: manifest schema/mode model')
    windows = manifest['mode_model'] == 'windows-readonly/v1'
    if git:
        for key in ('base', 'head', 'head_tree'):
            _sha(manifest[key], 40); require(manifest[key]!='0'*40, 'UNKNOWN: nonzero Git identity required')
    entries = manifest['entries']
    require(isinstance(entries, list) and len(entries) <= config['max_files'], 'UNKNOWN: manifest entry limit')
    paths = []; total = 0
    for item in entries:
        require(isinstance(item, dict), 'UNKNOWN: manifest entry')
        path = item.get('path'); _path(path, root=not git)
        require(_included(path, config), 'UNKNOWN: entry outside manifest scope')
        paths.append(path)
        if git:
            _keys(item, {'path', 'head', 'index', 'worktree'})
            require(any(item[k] is not None for k in ('head','index','worktree')), 'UNKNOWN: empty Git projection')
            for key in ('head','index','worktree'):
                if item[key] is not None:
                    _file(item[key], config, git=key != 'worktree', windows=windows)
                    total += item[key]['size_bytes']
        else:
            directory = item.get('kind') == 'directory'
            _keys(item, {'path','kind','mode'} | (set() if directory else {'size_bytes','sha256'}))
            require(item['kind'] in {'directory','file'}, 'UNKNOWN: manifest entry kind')
            require(type(item['mode']) is int and (item['mode'] in {0,1} if windows else 0 <= item['mode'] <= 0o7777), 'UNKNOWN: filesystem mode')
            if not directory:
                _file({k:v for k,v in item.items() if k not in {'path','kind'}}, config, windows=windows)
                total += item['size_bytes']
    require(paths == sorted(set(paths), key=lambda x:x.encode('utf-8')), 'UNKNOWN: canonical unique manifest paths required')
    if git:
        directories = manifest['directories']
        require(isinstance(directories, list) and len(directories) <= config['max_files'], 'UNKNOWN: directory projection')
        directory_paths = []
        for directory in directories:
            _keys(directory, {'path','kind','mode'}); _path(directory['path'], root=True)
            require(directory['kind']=='directory' and _included(directory['path'], config)
                    and type(directory['mode']) is int and (directory['mode'] in {0,1} if windows else 0 <= directory['mode'] <= 0o7777),
                    'UNKNOWN: directory projection')
            directory_paths.append(directory['path'])
        require(directory_paths == sorted(set(directory_paths), key=lambda x:x.encode('utf-8'))
                and not set(paths) & set(directory_paths)
                and len(paths)+len(directory_paths) <= config['max_files'], 'UNKNOWN: directory coverage')
        paths += directory_paths
    _collision(paths)
    _coverage(paths, config)
    # Total describes logical projections, even if Git blobs were read only once.
    require(total <= config['max_total_bytes'], 'UNKNOWN: manifest total limit')
    encoded = _canonical(manifest)
    require(len(encoded) <= MAX_MANIFEST, 'UNKNOWN: manifest serialization limit')
    return hashlib.sha256(encoded).hexdigest()


def identity(manifest): return 'source-manifest-sha256:' + validate(manifest)


def committed(manifest):
    """Whether all included file projections equal committed HEAD bytes/Git mode."""
    validate(manifest)
    if manifest['config']['mode'] != 'git-worktree': return False
    for entry in manifest['entries']:
        h, i, w = (entry[k] for k in ('head','index','worktree'))
        if h is None or i is None or w is None: return False
        if h != i or any(h[k] != w[k] for k in ('sha256','size_bytes')): return False
        if manifest['mode_model'] == 'posix-permissions/v1':
            if h['mode'] != (0o100755 if w['mode'] & 0o100 else 0o100644): return False
    return True


def _stamp(info):
    return (info.st_dev, info.st_ino, info.st_mode, info.st_nlink, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


def _walk(root_fd, config, metrics=None):
    result = {}; visited = 0
    if metrics is not None: metrics.repository_scan()
    def descend(fd, prefix, depth):
        nonlocal visited
        require(depth <= MAX_DEPTH, 'UNKNOWN: manifest depth limit')
        before = _stamp(os.fstat(fd))
        # os.scandir(fd) yields incrementally; never unbounded listdir allocation.
        with os.scandir(fd) as stream:
            for item in stream:
                visited += 1
                if metrics is not None: metrics.repository_entry()
                require(visited <= MAX_VISITED, 'UNKNOWN: manifest enumeration limit')
                path = item.name if prefix == '.' else prefix+'/'+item.name
                _path(path)
                if path == '.git' or path.startswith('.git/') or _covered(path, config['exclusions']): continue
                relevant = _covered(path, config['scope']) or any(scope.startswith(path+'/') for scope in config['scope'])
                if not relevant: continue
                require('.git' not in path.split('/'), 'UNKNOWN: nested Git repository unsupported')
                info = os.stat(item.name, dir_fd=fd, follow_symlinks=False)
                require(stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode), 'UNKNOWN: unsupported source entry (link/special file)')
                require(not stat.S_ISREG(info.st_mode) or info.st_nlink == 1, 'UNKNOWN: source hardlink refused')
                if _included(path, config): result[path] = _stamp(info)
                require(len(result) <= config['max_files'], 'UNKNOWN: manifest entry limit')
                if stat.S_ISDIR(info.st_mode):
                    child = os.open(item.name, os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW, dir_fd=fd)
                    try:
                        require(_stamp(os.fstat(child)) == _stamp(info), 'UNKNOWN: source changed during enumeration')
                        descend(child, path, depth+1)
                        require(_stamp(os.stat(item.name, dir_fd=fd, follow_symlinks=False)) == _stamp(info), 'UNKNOWN: source directory changed')
                    finally: os.close(child)
        require(_stamp(os.fstat(fd)) == before, 'UNKNOWN: source directory changed')
    if _included('.', config): result['.'] = _stamp(os.fstat(root_fd))
    descend(root_fd, '.', 0)
    _collision(result)
    return result


def _read(root_fd, path, expected, limit):
    descriptors = []; fd = root_fd
    try:
        names = path.split('/')
        for name in names[:-1]:
            fd = os.open(name, os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW, dir_fd=fd); descriptors.append(fd)
        leaf = os.open(names[-1], os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK, dir_fd=fd); descriptors.append(leaf)
        before = os.fstat(leaf)
        require(_stamp(before) == expected and stat.S_ISREG(before.st_mode) and before.st_nlink == 1,
                'UNKNOWN: source changed before read')
        require(before.st_size <= limit, 'UNKNOWN: manifest file/aggregate limit')
        data = bytearray()
        while len(data) <= limit:
            chunk = os.read(leaf, min(65536, limit+1-len(data)))
            if not chunk: break
            data.extend(chunk)
        require(len(data) <= limit and _stamp(os.fstat(leaf)) == expected
                and _stamp(os.stat(names[-1], dir_fd=fd, follow_symlinks=False)) == expected,
                'UNKNOWN: source changed during read or limit exceeded')
        return bytes(data)
    finally:
        for descriptor in reversed(descriptors): os.close(descriptor)


class _Git:
    def __init__(self, root, metrics):
        self.root = root; self.metrics = metrics; self.calls = 0; self.started = time.monotonic()
        self.executable = shutil.which('git'); require(self.executable, 'UNKNOWN: Git unavailable')
        self.env = git_environment()
        self.env.update(GIT_OPTIONAL_LOCKS='0', GIT_NO_LAZY_FETCH='1')

    def run(self, *args, input=b'', limit=MAX_TOTAL+262144, accepted=(0,)):
        self.calls += 1
        require(self.calls <= 12 and time.monotonic()-self.started < 30, 'UNKNOWN: Git capture work limit')
        if self.metrics is not None: self.metrics.git_attempt()
        argv = [self.executable, '--no-pager', '--git-dir='+str(self.root/'.git'),
                '--work-tree='+str(self.root), '-c', 'core.hooksPath='+os.devnull,
                '-c','core.attributesFile='+os.devnull,'-c','core.fsmonitor=false',
                '-c','core.untrackedCache=false','-c','maintenance.auto=false','-c','gc.auto=0',
                '-c','protocol.allow=never', *args]
        with tempfile.TemporaryFile() as incoming:
            incoming.write(input); incoming.seek(0)
            if os.name == 'nt':
                from support.bounded_process import run
                result = run(argv, stdin=incoming, env=self.env, stdout_limit=limit,
                             stderr_limit=8192, timeout=10, deadline=self.started+30)
                require(result.returncode in accepted, 'UNKNOWN: Git capture command failed')
                return result.stdout
            process = subprocess.Popen(argv, stdin=incoming, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=self.env)
            output = bytearray(); errors = bytearray(); deadline = min(self.started+30, time.monotonic()+10)
            try:
                with selectors.DefaultSelector() as selector:
                    selector.register(process.stdout, selectors.EVENT_READ, output)
                    selector.register(process.stderr, selectors.EVENT_READ, errors)
                    while selector.get_map():
                        require(time.monotonic() < deadline, 'UNKNOWN: Git capture timeout')
                        for key, _ in selector.select(min(.1, max(0, deadline-time.monotonic()))):
                            target = key.data; allowance = (limit if target is output else 8192)-len(target)
                            chunk = os.read(key.fileobj.fileno(), min(65536, allowance+1))
                            if not chunk: selector.unregister(key.fileobj); continue
                            target.extend(chunk)
                            require(len(target) <= (limit if target is output else 8192), 'UNKNOWN: Git output limit')
                code = process.wait(timeout=max(.001, deadline-time.monotonic()))
                require(code in accepted, 'UNKNOWN: Git capture command failed')
                return bytes(output)
            finally:
                if process.poll() is None: process.kill()
                process.wait(timeout=2)
                process.stdout.close(); process.stderr.close()


def _metadata(root):
    # Git object storage is host-controlled; never promise safe arbitrary Git metadata.
    require((root/'.git').is_dir() and not (root/'.git').is_symlink(), 'UNKNOWN: unsupported Git layout')
    for path in ('commondir','gitdir','objects/info/alternates','objects/info/http-alternates','config.worktree','shallow'):
        require(not os.path.lexists(root/'.git'/path), 'UNKNOWN: unsupported Git metadata layout')
    raw = read_bytes(root, '.git/config', limit=65536, single_link=True)
    cp = configparser.ConfigParser(interpolation=None)
    try: cp.read_string(raw.decode('utf-8'))
    except configparser.Error as exc: raise ValueError('UNKNOWN: unsupported Git config') from exc
    for section in cp.sections():
        if section == 'core':
            require(set(cp[section]) <= {'repositoryformatversion','filemode','bare','logallrefupdates','ignorecase','precomposeunicode','symlinks'},
                    'UNKNOWN: unsafe Git core config')
        elif re.fullmatch(r'remote "[^"\r\n]+"', section): require(set(cp[section]) <= {'url','fetch','pushurl'}, 'UNKNOWN: unsafe Git remote config')
        elif re.fullmatch(r'branch "[^"\r\n]+"', section): require(set(cp[section]) <= {'remote','merge'}, 'UNKNOWN: unsafe Git branch config')
        elif section == 'user': require(set(cp[section]) <= {'name','email'}, 'UNKNOWN: unsafe Git user config')
        else: raise ValueError('UNKNOWN: unsupported Git config section')
    require('core' in cp and cp['core'].get('repositoryformatversion') == '0'
            and cp['core'].get('bare') == 'false', 'UNKNOWN: ordinary SHA-1 checkout required')
    if os.name == 'nt':
        require(cp['core'].get('filemode', '').lower() == 'false',
                'UNKNOWN: Windows source requires explicit core.filemode=false')
    return raw, read_bytes(root, '.git/HEAD', limit=4096, single_link=True), read_bytes(root, '.git/index', limit=MAX_MANIFEST, single_link=True)


def _index(raw, config):
    require(len(raw) >= 32 and raw[:4] == b'DIRC' and hashlib.sha1(raw[:-20]).digest() == raw[-20:], 'UNKNOWN: invalid Git index')
    version, count = struct.unpack('>II', raw[4:12])
    require(version in {2,3} and count <= MAX_FILES, 'UNKNOWN: unsupported Git index version/size')
    offset = 12; result = {}
    for _ in range(count):
        start = offset; require(offset+62 <= len(raw)-20, 'UNKNOWN: truncated Git index')
        mode = struct.unpack('>I', raw[offset+24:offset+28])[0]
        oid = raw[offset+40:offset+60].hex(); flags = struct.unpack('>H', raw[offset+60:offset+62])[0]
        require(flags & 0xf000 == 0 and mode in {0o100644,0o100755}, 'UNKNOWN: unsupported Git index stage/flags/mode')
        end = raw.find(b'\0', offset+62, len(raw)-20)
        require(end >= 0, 'UNKNOWN: truncated index path')
        path = raw[offset+62:end].decode('utf-8', 'strict'); _path(path)
        require(path not in result, 'UNKNOWN: duplicate index path')
        offset = start + ((end+1-start+7)//8)*8
        require(offset <= len(raw)-20 and not any(raw[end:offset]), 'UNKNOWN: index padding')
        result[path] = (mode, oid)
    while offset < len(raw)-20:
        require(offset+8 <= len(raw)-20, 'UNKNOWN: index extension truncated')
        tag = raw[offset:offset+4]; size = struct.unpack('>I', raw[offset+4:offset+8])[0]
        require(tag in {b'TREE', b'REUC'} and offset+8+size <= len(raw)-20, 'UNKNOWN: unsupported Git index extension')
        offset += 8+size
    _collision(result)
    return {k:v for k,v in result.items() if _included(k, config)}


def _trees(raw, config):
    result = {}; count = 0
    require(not raw or raw.endswith(b'\0'), 'UNKNOWN: Git tree framing')
    for record in raw.split(b'\0')[:-1]:
        count += 1; require(count <= MAX_FILES, 'UNKNOWN: Git tree entry limit')
        meta, path = record.split(b'\t', 1); mode, kind, oid = meta.split(b' ')
        path = path.decode('utf-8', 'strict'); _path(path)
        require(mode in {b'100644',b'100755'} and kind == b'blob', 'UNKNOWN: unsupported Git tree entry')
        object_id = oid.decode('ascii'); _sha(object_id, 40)
        require(path not in result, 'UNKNOWN: duplicate tree entry')
        result[path] = (int(mode, 8), object_id)
    _collision(result)
    return {k:v for k,v in result.items() if _included(k, config)}


def _blobs(git, objects, config, filesystem_bytes):
    if not objects: return {}
    ordered = sorted(objects)
    request = ('\n'.join(ordered)+'\n').encode()
    sizes = git.run('cat-file', '--batch-check', input=request, limit=len(ordered)*100).splitlines()
    require(len(sizes)==len(ordered), 'UNKNOWN: Git blob size coverage')
    expected_sizes = {}; logical_total = filesystem_bytes
    for oid, line in zip(ordered, sizes):
        fields = line.split(b' ')
        require(len(fields)==3 and fields[0].decode('ascii')==oid and fields[1]==b'blob', 'UNKNOWN: unavailable Git blob')
        size = int(fields[2]); logical_total += size*objects[oid]
        require(0 <= size <= config['max_file_bytes'] and logical_total <= config['max_total_bytes'], 'UNKNOWN: Git blob byte limit')
        expected_sizes[oid] = size
    raw = git.run('cat-file', '--batch', input=request, limit=sum(expected_sizes.values())+len(ordered)*100)
    result = {}; offset = 0; total = 0
    for oid in ordered:
        end = raw.find(b'\n', offset); require(end >= 0, 'UNKNOWN: Git blob framing')
        header = raw[offset:end].split(b' ')
        require(len(header)==3 and header[0].decode('ascii')==oid and header[1]==b'blob', 'UNKNOWN: unavailable Git blob')
        size = int(header[2]); total += size
        require(size == expected_sizes[oid] and total <= config['max_total_bytes'], 'UNKNOWN: Git blob byte limit')
        start = end+1; data = raw[start:start+size]; offset = start+size+1
        require(len(data)==size and raw[start+size:offset]==b'\n'
                and hashlib.sha1(b'blob '+str(size).encode()+b'\0'+data).hexdigest()==oid,
                'UNKNOWN: Git blob integrity')
        _not_pointer(data)
        result[oid] = {'oid':oid,'size_bytes':size,'sha256':hashlib.sha256(data).hexdigest()}
    require(offset==len(raw), 'UNKNOWN: extra Git blob output')
    return result


class _PosixSource:
    mode_model = 'posix-permissions/v1'
    def __init__(self, root):
        require(all(hasattr(os, name) for name in ('O_NOFOLLOW','O_DIRECTORY')),
                'UNKNOWN: unsupported source manifest platform')
        self.path = checked_root(root)
    def directory_boundary(self):
        root = self.path
        require(not any(os.path.lexists(parent/'.git') for parent in (root, *root.parents)), 'UNKNOWN: Git checkout requires git-worktree manifest mode')
    def __enter__(self):
        self.fd = os.open(self.path, os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
        return self
    def __exit__(self, *exc): os.close(self.fd)
    def walk(self, config, metrics=None): return _walk(self.fd, config, metrics)
    def read(self, path, expected, limit): return _read(self.fd, path, expected, limit)
    def mode(self, info): return stat.S_IMODE(info[2])
    def verify_root(self):
        require(_stamp(os.stat(self.path, follow_symlinks=False)) == _stamp(os.fstat(self.fd)), 'UNKNOWN: source root changed')


def _source(root):
    if os.name == 'nt':
        from support.windows_manifest_files import NativeSource
        return NativeSource(root)
    return _PosixSource(root)


def capture(root, config, *, base=None, head=None, metrics=None):
    """Capture supported selected inputs. Every failure invalidates the whole result."""
    _config(config)
    with _source(root) as source:
        root = source.path
        if config['mode'] == 'directory': source.directory_boundary()
        before = source.walk(config, metrics)
        filesystem_bytes = 0
        for info in before.values():
            if stat.S_ISREG(info[2]):
                filesystem_bytes += info[4]
                require(info[4] <= config['max_file_bytes'] and filesystem_bytes <= config['max_total_bytes'],
                        'UNKNOWN: manifest file/aggregate limit')
        git = None; metadata = None; heads = {}; indexes = {}; blobs = {}; tree = None
        if config['mode'] == 'git-worktree':
            for value in (base,head): _sha(value,40); require(value!='0'*40, 'UNKNOWN: nonzero commit required')
            metadata = _metadata(root); indexes = _index(metadata[2], config); git = _Git(root, metrics)
            actual = git.run('rev-parse','--verify','HEAD',limit=128).decode('ascii').strip()
            require(actual == head, 'UNKNOWN: worktree HEAD binding mismatch')
            for value in sorted({base,head}):
                require(git.run('cat-file','-t',value,limit=32)==b'commit\n', 'UNKNOWN: commit unavailable')
            tree = git.run('rev-parse', '--verify', head+'^{tree}',limit=128).decode('ascii').strip(); _sha(tree,40)
            heads = _trees(git.run('ls-tree','-r','-z','--full-tree',head,limit=MAX_MANIFEST), config)
            # Check absent tracked/index paths too, before any Git blob payload.
            paths = sorted(path for path in set(before)|set(heads)|set(indexes) if path != '.')
            ignored = git.run('check-ignore','--no-index','--stdin','-z',input=b''.join(p.encode()+b'\0' for p in paths),
                              limit=MAX_MANIFEST, accepted=(0,1))
            require(not ignored, 'UNKNOWN: included ignored paths require explicit exclusions')
            _collision(set(before)|set(heads)|set(indexes))
            _coverage(set(before)|set(heads)|set(indexes), config)
            require(len(set(before)|set(heads)|set(indexes)) <= config['max_files'], 'UNKNOWN: manifest entry limit')
            objects = {}
            for value in list(heads.values())+list(indexes.values()): objects[value[1]] = objects.get(value[1],0)+1
            blobs = _blobs(git, objects, config, filesystem_bytes)
        else: _coverage(before, config)
        files = {}; directories = []; total = sum(blobs[v[1]]['size_bytes'] for v in list(heads.values())+list(indexes.values()))
        require(total <= config['max_total_bytes'], 'UNKNOWN: manifest aggregate limit')
        for path in sorted(before, key=lambda p:p.encode('utf-8')):
            info = before[path]; mode = source.mode(info)
            if stat.S_ISDIR(info[2]): directories.append({'path':path,'kind':'directory','mode':mode}); continue
            data = source.read(path, info, min(config['max_file_bytes'], config['max_total_bytes']-total)); total += len(data)
            if git is not None: _not_pointer(data)
            files[path] = {'mode':mode,'size_bytes':len(data),'sha256':hashlib.sha256(data).hexdigest()}
        require(source.walk(config, metrics) == before, 'UNKNOWN: source changed during capture')
        source.verify_root()
        manifest = {'schema':'source-manifest/v1','mode_model':source.mode_model,'config':json.loads(_canonical(config))}
        if git is None:
            manifest['entries'] = sorted(directories+[dict(path=p,kind='file',**v) for p,v in files.items()], key=lambda x:x['path'].encode('utf-8'))
        else:
            require(_metadata(root) == metadata and git.run('rev-parse','--verify','HEAD',limit=128).decode('ascii').strip()==head,
                    'UNKNOWN: Git metadata changed during capture')
            def projection(mapping, path):
                if path not in mapping: return None
                mode, oid = mapping[path]; return dict(blobs[oid], mode=mode)
            manifest.update(base=base,head=head,head_tree=tree,directories=directories,
                            entries=[{'path':path,'head':projection(heads,path),'index':projection(indexes,path),'worktree':files.get(path)}
                                     for path in sorted(set(heads)|set(indexes)|set(files), key=lambda p:p.encode('utf-8'))])
        validate(manifest)
        return manifest
