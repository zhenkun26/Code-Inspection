"""Offline bounded structural checks; Python >= 3.9, standard library only.

Simple frontmatter only; not YAML parsing, behavior evaluation or a sandbox.
"""
import argparse
import json
import os
import re
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from support.local_files import is_link, checked_root, digest, read_bytes, require


def check(root, repo_root=None):
    # Cross-Skill references require an explicitly selected repository boundary.
    # Do not resolve the Skill path before checking its in-repository components:
    # resolution would hide a symlinked parent directory.
    selected_root = Path(os.path.abspath(repo_root if repo_root is not None else root))
    boundary = checked_root(selected_root)
    root_path = Path(os.path.abspath(root))
    try:
        root_relative = root_path.relative_to(selected_root)
    except ValueError:
        raise ValueError('skill directory must be inside the selected root') from None
    current = boundary
    for part in root_relative.parts:
        current /= part
        require(not is_link(current), 'symbolic links are not supported')
    root = checked_root(boundary / root_relative)
    entries = []
    for path in root.rglob('*'):
        entries.append(path)
        require(len(entries) <= 256, 'too many skill entries')
    require(not any(is_link(p) for p in entries), 'symbolic links are not supported')
    entry = read_bytes(boundary, root_relative / 'SKILL.md').decode('utf-8')
    parts = entry.split('---\n', 2)
    require(len(parts) == 3 and parts[0] == '', 'frontmatter delimiters')
    fields = {}
    for line in parts[1].splitlines():
        key, separator, value = line.partition(': ')
        require(separator and key in {'name', 'description'}, 'simple frontmatter only')
        require(key not in fields and value.strip(), 'duplicate or empty field')
        require(not any(c in value for c in ['\t', '<', '>']), 'invalid field content')
        fields[key] = value
    require(set(fields) == {'name', 'description'}, 'required fields')
    require(fields['name'] == root.name, 'folder/name mismatch')
    require(re.fullmatch(r'[a-z0-9]+(?:-[a-z0-9]+)*', fields['name']), 'invalid name')
    require(len(fields['name']) < 64 and len(fields['description']) <= 1024, 'field length')
    links, files = [], {}
    routes = {}
    for path in sorted(p for p in entries if p.suffix == '.md'):
        relative = path.relative_to(root)
        data = read_bytes(boundary, root_relative / relative)
        content = data.decode('utf-8')
        routes[root_relative / relative] = set()
        require('[TODO:' not in content and '{{' not in content, 'unfinished scaffold')
        require(content.count('```') % 2 == 0, 'unclosed fence')
        for ref in re.findall(r'\]\(([^)]+)\)', content):
            require('://' not in ref and not Path(ref).is_absolute(), 'local relative link required')
            target = Path(os.path.normpath(str(root_relative / relative.parent / ref)))
            # Normalize parent references, then let no-follow reads enforce the
            # selected boundary. Standalone mode keeps its original narrow root.
            read_bytes(boundary, target)
            links.append(str(target))
            routes[root_relative / relative].add(target)
        files[str(relative)] = digest(data)
    routed, pending = set(), [root_relative / 'SKILL.md']
    while pending:
        target = pending.pop()
        if target not in routed:
            routed.add(target)
            pending.extend(routes.get(target, ()))
    require({root_relative / name for name in files if name != 'SKILL.md'} <= routed,
            'unrouted resource')
    return {'result': 'PASS', 'files': files, 'local_links': len(links),
            'scope': 'plain-string frontmatter/name/references/scaffold checks only'}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('skill_directory')
    parser.add_argument('--repo-root', help='explicit boundary for cross-Skill repository references')
    args = parser.parse_args()
    try:
        print(json.dumps(check(Path(args.skill_directory), args.repo_root), ensure_ascii=False, indent=2))
    except (ValueError, OSError, UnicodeError) as error:
        print(json.dumps({'result': 'REJECTED', 'reason': str(error)}, ensure_ascii=False))
        sys.exit(2)
