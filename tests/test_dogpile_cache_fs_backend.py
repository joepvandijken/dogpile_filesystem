import errno
import multiprocessing
import threading

import pytest
import dogpile.cache

from dogpile_cache_fs_backend import registry


@pytest.fixture
def region(tmpdir):
    r = dogpile.cache.make_region('test_region')
    r.configure(
        backend='paylogic.fs_backend',
        arguments={
            'base_dir': str(tmpdir),
        },
    )
    return r


def test_normal_usage(region):
    side_effect = []

    @region.cache_on_arguments()
    def fn(arg):
        side_effect.append(arg)
        return arg + 1

    assert fn(1) == 2
    assert fn(1) == 2

    assert side_effect == [1]


def test_recursive_usage(region):
    context = {'value': 3}

    @region.cache_on_arguments()
    def fn():
        if context['value'] == 0:
            return 42
        context['value'] -= 1
        return fn()

    assert fn() == 42
    assert context['value'] == 0


def test_dogpile_lock_threaded(region):
    mutex = region.backend.get_mutex('asd')

    mutex.acquire()
    mutex.acquire()

    mutex.release()

    thread_result = []
    def other_thread():
        o_mutex = region.backend.get_mutex('asd')
        assert o_mutex is mutex  # TODO: This should be a different test
        acquired = o_mutex.acquire(False)
        if acquired:
            o_mutex.release()
        thread_result.append(acquired)

    t = threading.Thread(target=other_thread)
    t.start()
    t.join()

    try:
        [other_thread_acquired_mutex] = thread_result
        assert other_thread_acquired_mutex is False
    finally:
        mutex.release()


def test_dogpile_lock_processes(region):
    mutex = region.backend.get_mutex('asd')

    mutex.acquire()
    mutex.acquire()

    mutex.release()

    proc_result = multiprocessing.Value('d', 42)
    assert proc_result.value == 42
    def other_process():
        o_mutex = region.backend.get_mutex('asd')
        proc_result.value = o_mutex.acquire(False)

    t = multiprocessing.Process(target=other_process)
    t.start()
    t.join()

    mutex.release()
    assert proc_result.value == 0
    assert not mutex.is_locked()


def test_locks_are_released_on_dereference(region):
    mutex = region.backend.get_mutex('asd')
    mutex.acquire()
    del mutex

    mutex = region.backend.get_mutex('asd')
    assert not mutex.is_locked()
    del mutex
    pass


@pytest.mark.parametrize('n_locks', [1, 2, 1000])
@pytest.mark.parametrize('n_files', [1, 2, 10])
def test_can_acquire_n_locks(tmpdir, n_locks, n_files):
    lockset = []
    for file_i in range(n_files):
        lock_file_path = str(tmpdir / 'lock_' + file_i)
        for i in range(n_locks):
            lock = registry.locks.get((lock_file_path, i))
            lock.acquire()
            lockset += [lock]


def test_deadlock_raises(region):
    mutex_1 = region.backend.get_mutex('1')
    mutex_2 = region.backend.get_mutex('2')

    mutex_1.acquire()

    def other_process():
        mutex_process_1 = region.backend.get_mutex('1')
        mutex_process_2 = region.backend.get_mutex('2')

        mutex_process_2.acquire()
        mutex_process_1.acquire()  # BOOM

    p = multiprocessing.Process(target=other_process)
    p.start()
    import time
    time.sleep(1)
    try:
        with pytest.raises(OSError) as excinfo:
            mutex_2.acquire()
        assert excinfo.value.errno == errno.EDEADLK
    finally:
        mutex_1.release()
        p.join()


