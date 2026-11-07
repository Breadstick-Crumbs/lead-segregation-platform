from app.core.rate_limit import Limit, RateLimiter

LIMIT = Limit(requests=3, window_seconds=60)


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_hits_over_the_limit_are_refused_until_the_window_slides() -> None:
    clock = Clock()
    limiter = RateLimiter(clock=clock)

    assert [limiter.hit("k", LIMIT) for _ in range(3)] == [None, None, None]
    clock.now += 10
    assert limiter.hit("k", LIMIT) == 50  # the first hit leaves the window in 50 s

    clock.now += 50
    assert limiter.hit("k", LIMIT) is None


def test_refused_hits_are_not_counted() -> None:
    clock = Clock()
    limiter = RateLimiter(clock=clock)
    for _ in range(3):
        limiter.hit("k", LIMIT)
    for _ in range(100):
        limiter.hit("k", LIMIT)

    clock.now += 60
    assert [limiter.hit("k", LIMIT) for _ in range(3)] == [None, None, None]


def test_keys_are_counted_separately() -> None:
    limiter = RateLimiter(clock=Clock())
    for _ in range(3):
        limiter.hit("a", LIMIT)

    assert limiter.hit("a", LIMIT) is not None
    assert limiter.hit("b", LIMIT) is None


def test_retry_after_only_looks() -> None:
    limiter = RateLimiter(clock=Clock())
    for _ in range(5):
        assert limiter.retry_after("k", LIMIT) is None
    for _ in range(3):
        limiter.hit("k", LIMIT)

    assert limiter.retry_after("k", LIMIT) == 60


def test_the_least_recently_hit_key_is_evicted_when_full() -> None:
    limiter = RateLimiter(clock=Clock(), max_keys=2)
    for _ in range(3):
        limiter.hit("old", LIMIT)
    limiter.hit("mid", LIMIT)
    limiter.hit("new", LIMIT)  # evicts "old"

    assert limiter.hit("old", LIMIT) is None


def test_release_hands_back_the_latest_hit() -> None:
    limiter = RateLimiter(clock=Clock())
    for _ in range(3):
        limiter.hit("k", LIMIT)
        limiter.release("k")

    assert limiter.retry_after("k", LIMIT) is None
    limiter.release("never-hit")  # nothing to hand back: no error
