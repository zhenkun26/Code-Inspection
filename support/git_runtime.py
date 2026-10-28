"""Environment for a host-selected Git, including HOME-dependent wrappers.

Executable resolution and HOME belong to the trusted host, never target input.
Git configuration overrides still disable global/system config and prompting.
"""
import os


def git_environment():
    environment = {key: os.environ[key] for key in ('PATH', 'HOME', 'TMPDIR', 'TMP', 'TEMP', 'SystemRoot', 'SYSTEMROOT', 'WINDIR', 'COMSPEC', 'USERPROFILE', 'HOMEDRIVE', 'HOMEPATH', 'SYSTEMDRIVE', 'PATHEXT')
                   if key in os.environ}
    environment.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull,
                       GIT_CONFIG_SYSTEM=os.devnull, GIT_NO_REPLACE_OBJECTS='1',
                       GIT_ATTR_NOSYSTEM='1', GIT_TERMINAL_PROMPT='0',
                       GIT_AUTHOR_NAME='Local lifecycle fixture',
                       GIT_AUTHOR_EMAIL='fixture@invalid',
                       GIT_COMMITTER_NAME='Local lifecycle fixture',
                       GIT_COMMITTER_EMAIL='fixture@invalid')
    return environment
