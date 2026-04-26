# This file is part of nputop, the interactive Ascend-NPU process viewer.
#
# Copyright (c) 2025 Xuehai Pan <XuehaiPan@pku.edu.cn>
# Copyright (c) 2025 Lianzhong You <youlianzhong@gml.ac.cn>
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from __future__ import annotations

import ctypes
import ctypes.util
import os
from collections import deque, namedtuple
from pathlib import Path
from typing import Callable, Iterable, Mapping, Optional, Set


NA = 'N/A'
DCMI_LIBRARY_NAME = 'libdcmi.so'
DCMI_ENV_PATHS = ('LD_LIBRARY_PATH', 'ASCEND_HOME_PATH', 'ASCEND_TOOLKIT_HOME')
MAX_SCAN_DEPTH = 8

COMMON_DCMI_LIBRARY_PATHS = (
    '/usr/local/dcmi/libdcmi.so',
    '/usr/local/lib/libdcmi.so',
    '/usr/local/Ascend/driver/lib64/libdcmi.so',
    '/usr/local/Ascend/driver/lib64/driver/libdcmi.so',
    '/usr/local/Ascend/driver/lib64/common/libdcmi.so',
)

DcmiLibraryCandidate = namedtuple('DcmiLibraryCandidate', ['path', 'source'])
DcmiLoadResult = namedtuple('DcmiLoadResult', ['library', 'path', 'source', 'error'])

_last_load_result = DcmiLoadResult(None, None, None, NA)


def _as_path(value) -> Path:
    return Path(os.path.expandvars(os.path.expanduser(os.fspath(value))))


def _path_key(value) -> str:
    value = os.fspath(value)
    if os.path.isabs(value) or os.sep in value:
        path = _as_path(value)
        try:
            return os.path.normcase(str(path.resolve()))
        except OSError:
            return os.path.normcase(str(path.absolute()))
    return value


def _existing_library(path) -> Optional[str]:
    path = _as_path(path)
    try:
        if path.is_file():
            return str(path)
    except OSError:
        pass
    return None


def _split_paths(value: Optional[str]) -> Iterable[Path]:
    if not value:
        return ()
    return tuple(_as_path(item) for item in value.split(os.pathsep) if item.strip())


def _iter_library_files_under(root, max_depth: int = MAX_SCAN_DEPTH) -> Iterable[str]:
    root = _as_path(root)
    if root.name == DCMI_LIBRARY_NAME:
        found = _existing_library(root)
        if found is not None:
            yield found
        return

    if not root.exists():
        return

    direct = _existing_library(root / DCMI_LIBRARY_NAME)
    if direct is not None:
        yield direct

    queue = deque([(root, 0)])
    visited: Set[str] = set()
    while queue:
        current, depth = queue.popleft()
        try:
            current_key = str(current.resolve())
        except OSError:
            current_key = str(current.absolute())
        if current_key in visited:
            continue
        visited.add(current_key)

        if depth >= max_depth:
            continue

        try:
            children = sorted(
                (path for path in current.iterdir() if path.is_dir()),
                key=lambda path: (len(path.parts), str(path)),
            )
        except OSError:
            continue

        for child in children:
            candidate = _existing_library(child / DCMI_LIBRARY_NAME)
            if candidate is not None:
                yield candidate
            queue.append((child, depth + 1))


def _dedupe(candidates: Iterable[DcmiLibraryCandidate]) -> Iterable[DcmiLibraryCandidate]:
    seen: Set[str] = set()
    for candidate in candidates:
        key = _path_key(candidate.path)
        if key in seen:
            continue
        seen.add(key)
        yield candidate


def _iter_common_candidates(common_paths) -> Iterable[DcmiLibraryCandidate]:
    for path in common_paths:
        found = _existing_library(path)
        if found is not None:
            yield DcmiLibraryCandidate(found, 'common')


def _iter_env_candidates(env: Mapping[str, str]) -> Iterable[DcmiLibraryCandidate]:
    for name in DCMI_ENV_PATHS:
        for root in _split_paths(env.get(name)):
            for path in _iter_library_files_under(root):
                yield DcmiLibraryCandidate(path, name)


def iterDcmiLibraryCandidates(
    *,
    env: Optional[Mapping[str, str]] = None,
    common_paths: Optional[Iterable[str]] = None,
    find_library: Optional[Callable[[str], Optional[str]]] = None,
) -> Iterable[DcmiLibraryCandidate]:
    """Yield loadable DCMI candidates in the configured backend search order."""
    if env is None:
        env = os.environ
    if common_paths is None:
        common_paths = COMMON_DCMI_LIBRARY_PATHS
    if find_library is None:
        find_library = ctypes.util.find_library

    candidates = list(_iter_common_candidates(common_paths))
    candidates.extend(_iter_env_candidates(env))

    library = find_library('dcmi')
    if library:
        candidates.append(DcmiLibraryCandidate(library, 'find_library'))

    yield from _dedupe(candidates)


def findDcmiLibrary(
    *,
    env: Optional[Mapping[str, str]] = None,
    common_paths: Optional[Iterable[str]] = None,
    find_library: Optional[Callable[[str], Optional[str]]] = None,
) -> Optional[DcmiLibraryCandidate]:
    """Return the first DCMI library candidate without loading it."""
    return next(
        iterDcmiLibraryCandidates(
            env=env,
            common_paths=common_paths,
            find_library=find_library,
        ),
        None,
    )


def loadDcmiLibrary(
    *,
    env: Optional[Mapping[str, str]] = None,
    common_paths: Optional[Iterable[str]] = None,
    find_library: Optional[Callable[[str], Optional[str]]] = None,
    cdll: Optional[Callable[[str], object]] = None,
) -> DcmiLoadResult:
    """Load DCMI if available; return a failed result so callers can fall back to npu-smi."""
    global _last_load_result

    if cdll is None:
        cdll = ctypes.CDLL

    last_error = NA
    for candidate in iterDcmiLibraryCandidates(
        env=env,
        common_paths=common_paths,
        find_library=find_library,
    ):
        try:
            library = cdll(candidate.path)
        except OSError as exc:
            last_error = f'{candidate.path}: {exc}'
            continue

        _last_load_result = DcmiLoadResult(
            library=library,
            path=candidate.path,
            source=candidate.source,
            error=NA,
        )
        return _last_load_result

    _last_load_result = DcmiLoadResult(
        library=None,
        path=None,
        source=None,
        error=last_error,
    )
    return _last_load_result


def dcmiLastLoadResult() -> DcmiLoadResult:
    return _last_load_result
