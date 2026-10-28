"""Read-only checks of distributable paths and local Markdown links."""
import json
import hashlib
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from support.local_files import is_link, read_bytes


def check_manifest(root, names):
    """Verify every selected file, excluding the manifest itself, exactly once."""
    try:
        entries = {}
        content = read_bytes(root, 'SHA256SUMS').decode('utf-8')
        for line in content.splitlines():
            match = re.fullmatch(r'([0-9a-f]{64})  (.+)', line)
            if not match:
                raise ValueError('invalid checksum line')
            sha, name = match.groups()
            path = Path(name)
            if path.is_absolute() or '..' in path.parts or name == 'SHA256SUMS' or name in entries:
                raise ValueError('unsafe, duplicate or self-referencing checksum path')
            entries[name] = sha
        if set(entries) != set(names) - {'SHA256SUMS'}:
            raise ValueError('checksum coverage differs from selected files')
        for name, expected in entries.items():
            if hashlib.sha256(read_bytes(root, name)).hexdigest() != expected:
                raise ValueError('checksum mismatch: ' + name)
    except (ValueError, OSError, UnicodeError) as exc:
        return [{'path': 'SHA256SUMS', 'reason': str(exc)}]
    return []


def ignored_candidates(names):
    """Check tracked paths too, while respecting explicit negated ignore rules."""
    if not names:
        return set()
    result = subprocess.run(['git', 'check-ignore', '--no-index', '--stdin', '-z', '-v'],
                            input=('\0'.join(names)+'\0').encode(), cwd=ROOT,
                            capture_output=True, timeout=10)
    if result.returncode not in (0, 1):
        raise ValueError('cannot verify distribution ignore rules')
    fields = result.stdout.decode().split('\0')
    if fields[-1] == '':
        fields.pop()
    if len(fields) % 4:
        raise ValueError('unexpected ignore-rule output')
    return {fields[i+3] for i in range(0, len(fields), 4) if not fields[i+2].startswith('!')}


def check():
    result = subprocess.run(['git', 'ls-files', '--cached', '--others', '--exclude-standard', '-z'],
                            cwd=ROOT, capture_output=True, check=True, timeout=10)
    names = sorted(set(result.stdout.decode().strip('\0').split('\0')) - {''})
    errors = []
    links = 0
    for name in sorted(ignored_candidates(names)):
        errors.append({'path': name, 'reason': 'ignored path selected for distribution'})
    for name in names:
        path = ROOT / name
        if is_link(path):
            errors.append({'path': name, 'reason': 'distribution symlink requires explicit review'})
            continue
        if (name.split('/')[0] in {'reference', 'inputs', 'private', 'uploads'}
                or any(part in {'__pycache__', 'results', 'history'} for part in path.relative_to(ROOT).parts)
                or name.startswith('docs/reviews/') or name.endswith('.pyc')):
            errors.append({'path': name, 'reason': 'private/generated path selected for distribution'})
        if path.is_file() and path.parent == ROOT and re.fullmatch('[a-z0-9_]{8}', path.name) and path.read_bytes() == b'blat':
            errors.append({'path': name, 'reason': 'temporary-directory probe residue'})
        if path.suffix != '.md' or not path.is_file():
            continue
        for ref in re.findall(r'(?<!!)\[[^\]]*\]\(([^)]+)\)', path.read_text(encoding='utf-8')):
            parsed = urlsplit(ref)
            if parsed.scheme or parsed.netloc or not parsed.path:
                continue
            target = (path.parent / unquote(parsed.path)).resolve()
            try:
                relative = target.relative_to(ROOT).as_posix()
            except ValueError:
                errors.append({'path': name, 'reason': 'link escapes repository'})
                continue
            links += 1
            if relative not in names or not target.is_file():
                errors.append({'path': name, 'reason': 'link target is not distributed', 'target': relative})
    if (ROOT / '.git/index.lock').exists():
        errors.append({'path': '.git/index.lock', 'reason': 'index lock exists; inspect owner, never auto-delete'})
    # Do not read excluded/private payloads just to validate their checksums.
    if not errors:
        errors.extend(check_manifest(ROOT, names))
    return {'result': 'FAIL' if errors else 'PASS', 'distribution_files': len(names),
            'local_links': links, 'errors': errors,
            'limitations': 'Path/link hygiene only; no authenticity, secret-detection or licensing guarantee.'}


if __name__ == '__main__':
    output = check()
    print(json.dumps(output, indent=2))
    sys.exit(bool(output['errors']))
