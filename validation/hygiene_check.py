"""Read-only checks of distributable paths and local Markdown links."""
import json
import hashlib
from pathlib import Path
import re
import stat
import subprocess
import sys
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
# Relocated payloads still use the canonical distribution's ignore policy.
POLICY_ROOT = ROOT
MAX_FILE_BYTES = 1024 * 1024
MAX_SELECTED_FILES = 1024
MAX_SCAN_BYTES = 8 * 1024 * 1024
sys.path.insert(0, str(ROOT))
from support.local_files import checked_root, is_link, read_bytes


def _parts(name):
    """Validate the raw spelling before Path can normalize any components."""
    if (not isinstance(name, str) or not name or name.startswith('/')
            or any(ord(char) < 32 or char in '\\:*?"<>|\x7f' for char in name)):
        raise ValueError('relative bounded path required')
    parts = name.split('/')
    if any(part in {'', '.', '..'} or part.endswith((' ', '.')) for part in parts):
        raise ValueError('relative bounded path required')
    for part in parts:
        if re.fullmatch(r'CON|PRN|AUX|NUL|CLOCK\$|CONIN\$|CONOUT\$|COM[1-9¹²³]|LPT[1-9¹²³]',
                        part.split('.')[0], re.IGNORECASE):
            raise ValueError('reserved Windows name')
    return parts


def _forbidden(parts):
    folded = [part.casefold() for part in parts]
    return (folded[0] in {'reference', 'inputs', 'private', 'uploads'}
            or any(part in {'.git', '__pycache__', 'results', 'history'} for part in folded)
            or folded[:2] == ['docs', 'reviews'] or folded[-1].endswith('.pyc'))


def _file_info(root, name):
    """Inspect each component without resolving a selected path through links."""
    parts = _parts(name)
    path = root
    for index, part in enumerate(parts):
        path = path / part
        if is_link(path):
            raise ValueError('distribution symlink/reparse point requires explicit review')
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            raise ValueError('distribution symlink/reparse point requires explicit review')
        required = stat.S_ISREG if index == len(parts) - 1 else stat.S_ISDIR
        if not required(info.st_mode):
            raise ValueError('not a regular file' if index == len(parts) - 1 else 'directory required')
    return info


def ignored_candidates(names):
    """Check tracked paths too, while respecting explicit negated ignore rules."""
    if not names:
        return set()
    result = subprocess.run(['git', 'check-ignore', '--no-index', '--stdin', '-z', '-v'],
                            input=('\0'.join(names)+'\0').encode(), cwd=POLICY_ROOT,
                            capture_output=True, timeout=10)
    if result.returncode not in (0, 1):
        raise ValueError('cannot verify distribution ignore rules')
    fields = result.stdout.decode().split('\0')
    if fields[-1] == '':
        fields.pop()
    if len(fields) % 4:
        raise ValueError('unexpected ignore-rule output')
    return {fields[i+3] for i in range(0, len(fields), 4) if not fields[i+2].startswith('!')}


def _preflight(root, names):
    """Reject the entire selection before reading even the manifest payload."""
    errors = []
    if len(names) > MAX_SELECTED_FILES:
        return None, {}, [{'path': '.', 'reason': 'selected-file count exceeds limit'}]
    seen = set()
    for name in names:
        try:
            parts = _parts(name)
            if name in seen:
                raise ValueError('duplicate selected path')
            seen.add(name)
            if _forbidden(parts):
                raise ValueError('private/generated path selected for distribution')
        except ValueError as exc:
            errors.append({'path': name, 'reason': str(exc)})
    if errors:
        return None, {}, errors
    try:
        for name in sorted(ignored_candidates(names)):
            errors.append({'path': name, 'reason': 'ignored path selected for distribution'})
        if errors:
            return None, {}, errors
        if 'SHA256SUMS' not in seen:
            errors.append({'path': 'SHA256SUMS', 'reason': 'checksum manifest is not selected'})
        if errors:
            return None, {}, errors
        root = checked_root(root)
    except (ValueError, OSError, UnicodeError, subprocess.SubprocessError) as exc:
        return None, {}, [{'path': '.', 'reason': str(exc)}]
    metadata = {}
    for name in names:
        try:
            info = _file_info(root, name)
            if info.st_size > MAX_FILE_BYTES:
                raise ValueError('file exceeds per-file read limit')
            metadata[name] = info
        except (ValueError, OSError) as exc:
            errors.append({'path': name, 'reason': str(exc)})
    if sum(info.st_size for info in metadata.values()) > MAX_SCAN_BYTES:
        errors.append({'path': '.', 'reason': 'selected payload bytes exceed scan limit'})
    return root, metadata, errors


def _probe_candidate(name, info):
    return re.fullmatch('[a-z0-9_]{8}', name) is not None and info.st_size == 4


class _Payloads:
    def __init__(self, root, metadata):
        self.root = root
        self.metadata = metadata
        self.cache = {}
        self.used = 0

    def get(self, name):
        if name not in self.cache:
            limit = min(MAX_FILE_BYTES, MAX_SCAN_BYTES - self.used)
            if _probe_candidate(name, self.metadata[name]):
                limit = min(limit, 4)
            if limit < 0:
                raise ValueError('selected payload bytes exceed scan limit')
            data = read_bytes(self.root, name, limit=limit)
            self.used += len(data)
            self.cache[name] = data
        return self.cache[name]


def _verify_manifest(root, names, metadata):
    payloads = _Payloads(root, metadata)
    try:
        entries = {}
        content = payloads.get('SHA256SUMS').decode('utf-8')
        for line in content.splitlines():
            match = re.fullmatch(r'([0-9a-f]{64})  (.+)', line)
            if not match:
                raise ValueError('invalid checksum line')
            sha, name = match.groups()
            _parts(name)
            if name == 'SHA256SUMS' or name in entries:
                raise ValueError('unsafe, duplicate or self-referencing checksum path')
            entries[name] = sha
        # Validate the complete entry set before dispatching any entry for hashing.
        if set(entries) != set(names) - {'SHA256SUMS'}:
            raise ValueError('checksum coverage differs from selected files')
        for name, expected in entries.items():
            if hashlib.sha256(payloads.get(name)).hexdigest() != expected:
                raise ValueError('checksum mismatch: ' + name)
    except (ValueError, OSError, UnicodeError) as exc:
        return [{'path': 'SHA256SUMS', 'reason': str(exc)}], payloads.cache
    return [], payloads.cache


def check_manifest(root, names):
    """Preflight the selection, then verify its exact checksum coverage and bytes."""
    names = list(names)
    root, metadata, errors = _preflight(root, names)
    if errors:
        return errors
    return _verify_manifest(root, names, metadata)[0]


def _local_target(source, ref):
    parsed = urlsplit(ref)
    if parsed.scheme or parsed.netloc or not parsed.path:
        return None
    path = unquote(parsed.path, errors='strict')
    if path.startswith('/') or '\\' in path:
        raise ValueError('link escapes repository')
    parts = source.split('/')[:-1]
    for part in path.split('/'):
        if part in {'', '.'}:
            continue
        if part == '..':
            if not parts:
                raise ValueError('link escapes repository')
            parts.pop()
        else:
            parts.append(part)
    relative = '/'.join(parts)
    _parts(relative)
    return relative


def check():
    result = subprocess.run(['git', 'ls-files', '--cached', '--others', '--exclude-standard', '-z'],
                            cwd=ROOT, capture_output=True, check=True, timeout=10)
    names = result.stdout.decode().split('\0') if result.stdout else []
    if names and names[-1] == '':
        names.pop()
    names.sort()
    root, metadata, errors = _preflight(ROOT, names)
    links = 0
    if not errors:
        errors, payloads = _verify_manifest(root, names, metadata)
        if not errors:
            selected = set(names)
            for name in names:
                if _probe_candidate(name, metadata[name]) and payloads[name] == b'blat':
                    errors.append({'path': name, 'reason': 'temporary-directory probe residue'})
                if Path(name).suffix != '.md':
                    continue
                try:
                    content = payloads[name].decode('utf-8')
                except UnicodeError as exc:
                    errors.append({'path': name, 'reason': str(exc)})
                    continue
                for ref in re.findall(r'(?<!!)\[[^\]]*\]\(([^)]+)\)', content):
                    try:
                        relative = _local_target(name, ref)
                        if relative is None:
                            continue
                        links += 1
                        if relative not in selected:
                            errors.append({'path': name, 'reason': 'link target is not distributed',
                                           'target': relative})
                            continue
                        _file_info(root, relative)
                    except (ValueError, OSError, UnicodeError) as exc:
                        errors.append({'path': name, 'reason': str(exc)})
    if (ROOT / '.git/index.lock').exists():
        errors.append({'path': '.git/index.lock', 'reason': 'index lock exists; inspect owner, never auto-delete'})
    return {'result': 'FAIL' if errors else 'PASS', 'distribution_files': len(names),
            'local_links': links, 'errors': errors,
            'limitations': 'Path/link hygiene only; payload read/count limits do not bound Git metadata '
                           'or total process memory and are not a sandbox. No authenticity, '
                           'secret-detection or licensing guarantee.'}


if __name__ == '__main__':
    output = check()
    print(json.dumps(output, indent=2))
    sys.exit(bool(output['errors']))
