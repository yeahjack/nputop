from nputop.api import libdcmi


def touch_library(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('', encoding='utf-8')
    return str(path)


def paths(candidates):
    return [candidate.path for candidate in candidates]


def test_dcmi_candidates_prefer_common_paths_before_environment(tmp_path):
    common = touch_library(tmp_path / 'common' / 'libdcmi.so')
    env_library = touch_library(tmp_path / 'env' / 'libdcmi.so')

    candidates = list(
        libdcmi.iterDcmiLibraryCandidates(
            env={'LD_LIBRARY_PATH': str(tmp_path / 'env')},
            common_paths=[common],
            find_library=lambda name: None,
        ),
    )

    assert paths(candidates) == [common, env_library]
    assert [candidate.source for candidate in candidates] == ['common', 'LD_LIBRARY_PATH']


def test_dcmi_candidates_scan_env_roots_from_shallow_to_deep(tmp_path):
    root = tmp_path / 'Ascend'
    shallow = touch_library(root / 'libdcmi.so')
    middle = touch_library(root / 'driver' / 'libdcmi.so')
    deep = touch_library(root / 'driver' / 'lib64' / 'driver' / 'libdcmi.so')

    candidates = list(
        libdcmi.iterDcmiLibraryCandidates(
            env={'ASCEND_HOME_PATH': str(root)},
            common_paths=[],
            find_library=lambda name: None,
        ),
    )

    assert paths(candidates) == [shallow, middle, deep]


def test_dcmi_candidates_try_find_library_after_environment(tmp_path):
    env_library = touch_library(tmp_path / 'env' / 'libdcmi.so')

    candidates = list(
        libdcmi.iterDcmiLibraryCandidates(
            env={'ASCEND_TOOLKIT_HOME': str(tmp_path / 'env')},
            common_paths=[],
            find_library=lambda name: '/ldconfig/libdcmi.so',
        ),
    )

    assert paths(candidates) == [env_library, '/ldconfig/libdcmi.so']
    assert [candidate.source for candidate in candidates] == [
        'ASCEND_TOOLKIT_HOME',
        'find_library',
    ]


def test_find_dcmi_library_returns_first_candidate(tmp_path):
    first = touch_library(tmp_path / 'first' / 'libdcmi.so')
    second = touch_library(tmp_path / 'second' / 'libdcmi.so')

    candidate = libdcmi.findDcmiLibrary(
        env={'LD_LIBRARY_PATH': str(tmp_path / 'second')},
        common_paths=[first],
        find_library=lambda name: None,
    )

    assert candidate.path == first
    assert second not in candidate.path


def test_load_dcmi_library_reports_failure_for_npusmi_fallback(tmp_path):
    candidate = touch_library(tmp_path / 'libdcmi.so')
    attempts = []

    def fail_cdll(path):
        attempts.append(path)
        raise OSError('missing dependency')

    result = libdcmi.loadDcmiLibrary(
        env={},
        common_paths=[candidate],
        find_library=lambda name: None,
        cdll=fail_cdll,
    )

    assert attempts == [candidate]
    assert result.library is None
    assert result.path is None
    assert 'missing dependency' in result.error


def test_load_dcmi_library_returns_loaded_library(tmp_path):
    candidate = touch_library(tmp_path / 'libdcmi.so')
    fake_library = object()

    result = libdcmi.loadDcmiLibrary(
        env={},
        common_paths=[candidate],
        find_library=lambda name: None,
        cdll=lambda path: fake_library,
    )

    assert result.library is fake_library
    assert result.path == candidate
    assert result.source == 'common'
    assert result.error == libdcmi.NA

