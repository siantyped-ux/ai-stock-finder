"""ETF 복제본 제거 테스트.

유니버스에 같은 지수를 추종하는 상품이 여러 개 들어 있으면 상위 목록이
복제본으로 채워진다. 여기서 거르는 것은 "같은 상품" 이지 "같은 테마" 가
아니다 - 임계 근거는 etf_dedupe 모듈 docstring 에 있다.
"""
import etf_dedupe as ed


def corr_from(pairs):
    """지정한 쌍만 상관을 내는 가짜 상관 함수. 나머지는 측정 불가(None)."""
    def get(a, b):
        return pairs.get((a, b), pairs.get((b, a)))
    return get


def test_the_less_liquid_twin_is_dropped():
    kept, dropped = ed.dedupe(
        ["VOO", "SPY"], corr_from({("SPY", "VOO"): 0.9996}),
        {"SPY": 900.0, "VOO": 300.0}, threshold=0.99)

    assert kept == ["SPY"]
    assert dropped == {"VOO": ("SPY", 0.9996)}


def test_a_pair_below_the_threshold_both_survive():
    kept, dropped = ed.dedupe(
        ["GDX", "SIL"], corr_from({("GDX", "SIL"): 0.97}),
        {"GDX": 500.0, "SIL": 100.0}, threshold=0.99)

    assert kept == ["GDX", "SIL"]
    assert dropped == {}


def test_exactly_at_the_threshold_is_dropped():
    kept, _ = ed.dedupe(
        ["A", "B"], corr_from({("A", "B"): 0.99}),
        {"A": 2.0, "B": 1.0}, threshold=0.99)

    assert kept == ["A"]


def test_ties_break_by_ticker_so_the_result_is_deterministic():
    # 거래대금이 같으면 dict 순서에 맡길 수 없다. 매번 다른 유니버스가 나온다.
    kept, _ = ed.dedupe(
        ["ZZZ", "AAA"], corr_from({("ZZZ", "AAA"): 0.999}),
        {"ZZZ": 1.0, "AAA": 1.0}, threshold=0.99)

    assert kept == ["AAA"]


def test_a_chain_does_not_drag_in_an_unrelated_fund():
    # A-B 와 B-C 가 복제본이어도 A-C 가 아니면 C 는 남아야 한다. 연결요소로
    # 묶으면 C 까지 지워진다.
    kept, dropped = ed.dedupe(
        ["A", "B", "C"],
        corr_from({("A", "B"): 0.999, ("B", "C"): 0.999, ("A", "C"): 0.20}),
        {"A": 3.0, "B": 2.0, "C": 1.0}, threshold=0.99)

    assert kept == ["A", "C"]
    assert set(dropped) == {"B"}


def test_unmeasurable_correlation_keeps_both():
    # 상장 직후라 이력이 짧은 종목을 조용히 지우면 안 된다. 조회 실패로
    # 유니버스가 비는 것이 중복을 남기는 것보다 나쁘다.
    kept, dropped = ed.dedupe(
        ["A", "B"], corr_from({}), {"A": 2.0, "B": 1.0}, threshold=0.99)

    assert kept == ["A", "B"]
    assert dropped == {}


def test_a_fund_without_turnover_sorts_last_but_survives():
    kept, _ = ed.dedupe(
        ["A", "B"], corr_from({}), {"A": 0.0}, threshold=0.99)

    assert set(kept) == {"A", "B"}


def test_empty_input_is_safe():
    assert ed.dedupe([], corr_from({}), {}, threshold=0.99) == ([], {})


def test_the_representative_is_reported_for_every_drop():
    # 무엇이 무엇 때문에 빠졌는지 남지 않으면 유니버스가 조용히 줄어든다.
    _, dropped = ed.dedupe(
        ["SPY", "VOO", "IVV"],
        corr_from({("SPY", "VOO"): 0.9996, ("SPY", "IVV"): 0.9992,
                   ("VOO", "IVV"): 0.9993}),
        {"SPY": 900.0, "VOO": 300.0, "IVV": 200.0}, threshold=0.99)

    assert dropped == {"VOO": ("SPY", 0.9996), "IVV": ("SPY", 0.9992)}


# ─── 제외 목록 파일 ──────────────────────────────────────────

def test_excluded_tickers_reads_the_file(tmp_path):
    p = tmp_path / "dupes.json"
    p.write_text('{"threshold": 0.99, "excluded":'
                 ' {"VOO": {"kept": "SPY", "rho": 0.9996}}}',
                 encoding="utf-8")

    assert ed.excluded_tickers(str(p)) == {"VOO"}


def test_a_missing_file_excludes_nothing(tmp_path):
    # 목록이 없다고 스캔이 멈추면 안 된다. 중복을 남긴 채 도는 편이 낫다.
    assert ed.excluded_tickers(str(tmp_path / "nope.json")) == set()


def test_a_broken_file_excludes_nothing(tmp_path):
    p = tmp_path / "dupes.json"
    p.write_text("{not json", encoding="utf-8")

    assert ed.excluded_tickers(str(p)) == set()
