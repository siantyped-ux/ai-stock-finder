import stock_finder


def test_guard_rejects_the_773_of_1103_regression():
    # 2026-08-18 실제 사고: 429 rate limit으로 322종목 유실
    assert stock_finder.is_scan_complete(773, 1103, 0.90) is False


def test_guard_accepts_normal_run():
    assert stock_finder.is_scan_complete(1087, 1103, 0.90) is True


def test_guard_accepts_exactly_at_threshold():
    assert stock_finder.is_scan_complete(90, 100, 0.90) is True


def test_guard_rejects_empty_universe():
    assert stock_finder.is_scan_complete(0, 0, 0.90) is False
