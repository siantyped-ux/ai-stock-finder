"""선행 수익률 계산 테스트.

값이 틀리면 스코어링 변경을 반대로 판정하게 되므로, 경계 조건을 촘촘히 건다.

설계: docs/superpowers/specs/2026-08-24-flow-axis-design.md
"""
import pytest

import forward_returns as fr

# 종가가 100, 110, 121, ... 로 매일 10% 오르는 5거래일
PRICES = {
    "AAA": (["2026-08-03", "2026-08-04", "2026-08-05", "2026-08-06", "2026-08-07"],
            [100.0, 110.0, 121.0, 133.1, 146.41]),
}


# ─── 날짜 탐색 ──────────────────────────────────────────────
@pytest.mark.parametrize("target,expected", [
    ("2026-08-03", 0),
    ("2026-08-05", 2),
    ("2026-08-07", 4),
    ("2026-08-09", 4),   # 주말 - 그 이하의 마지막 봉
    ("2026-08-02", -1),  # 첫 봉보다 이르다
])
def test_index_at_or_before(target, expected):
    assert fr._index_at_or_before(PRICES["AAA"][0], target) == expected


def test_index_handles_a_holiday_gap():
    """bar_date 가 휴장일이면 그 이전 마지막 거래일을 잡아야 한다."""
    dates = ["2026-08-03", "2026-08-07"]
    assert fr._index_at_or_before(dates, "2026-08-05") == 0


# ─── 선행 수익률 ────────────────────────────────────────────
def test_one_day_forward_return():
    assert fr.forward_return(PRICES, "AAA", "2026-08-03", 1) == pytest.approx(10.0)


def test_multi_day_forward_return():
    # 100 -> 121
    assert fr.forward_return(PRICES, "AAA", "2026-08-03", 2) == pytest.approx(21.0)


def test_negative_return():
    prices = {"BBB": (["2026-08-03", "2026-08-04"], [100.0, 90.0])}
    assert fr.forward_return(prices, "BBB", "2026-08-03", 1) == pytest.approx(-10.0)


def test_returns_none_when_horizon_exceeds_available_bars():
    """마지막 봉으로 대체하지 않는다. 구간이 짧아진 행이 섞이면 평균이 왜곡된다."""
    assert fr.forward_return(PRICES, "AAA", "2026-08-06", 5) is None
    assert fr.forward_return(PRICES, "AAA", "2026-08-07", 1) is None


def test_returns_none_for_unknown_ticker():
    assert fr.forward_return(PRICES, "ZZZ", "2026-08-03", 1) is None


def test_returns_none_before_first_bar():
    assert fr.forward_return(PRICES, "AAA", "2020-01-01", 1) is None


def test_returns_none_on_zero_base_price():
    prices = {"CCC": (["2026-08-03", "2026-08-04"], [0.0, 10.0])}
    assert fr.forward_return(prices, "CCC", "2026-08-03", 1) is None


def test_bar_date_on_a_holiday_measures_from_the_prior_close():
    """휴장일 bar_date 는 직전 종가를 기준으로 잡는다.

    8/05·8/06 봉이 없는 종목에서 bar_date 8/05 는 8/04 종가(110)를 기준으로
    삼아야 하고, 1거래일 뒤는 8/07 종가(150)다.
    """
    prices = {"AAA": (["2026-08-03", "2026-08-04", "2026-08-07"],
                      [100.0, 110.0, 150.0])}
    assert fr.forward_return(prices, "AAA", "2026-08-05", 1) == pytest.approx(
        (150.0 / 110.0 - 1) * 100)


# ─── 신호의 값어치 ──────────────────────────────────────────
def test_edge_is_buy_mean_minus_universe_mean():
    data = {
        "BUY계열·STOCK": {5: [3.0, 5.0]},     # 평균 4.0
        "전체·STOCK": {5: [1.0, 1.0, 1.0, 1.0]},  # 평균 1.0
    }
    got = fr.edge(data, 5, "STOCK")
    assert got[0] == pytest.approx(3.0)
    assert got[1] == 2


def test_edge_is_negative_when_buys_lag_the_universe():
    """이 부호가 판정의 전부다. 음수면 신호가 무작위보다 못하다는 뜻이다."""
    data = {
        "BUY계열·STOCK": {5: [-1.0, -1.0]},
        "전체·STOCK": {5: [2.0, 2.0]},
    }
    assert fr.edge(data, 5, "STOCK")[0] == pytest.approx(-3.0)


def test_edge_is_none_without_enough_samples():
    """아카이브 막바지에 등장한 자산군은 선행 구간이 없어 표본이 빈다."""
    assert fr.edge({"BUY계열·ETF": {5: [1.0]}, "전체·ETF": {5: []}}, 5, "ETF") is None
    assert fr.edge({}, 5, "ETF") is None


# ─── 출력 ───────────────────────────────────────────────────
def test_stat_line_reports_sample_size_and_win_rate():
    out = fr.stat_line("BUY", [1.0, -1.0, 2.0, 4.0])
    assert "n=4" in out and "승률 75.0%" in out


def test_stat_line_flags_a_thin_sample():
    assert "표본 부족" in fr.stat_line("BUY", [1.0])
    assert "표본 부족" in fr.stat_line("BUY", [])


# ─── 순위상관 ───────────────────────────────────────────────
def test_ranks_handles_ties_with_average():
    """동점은 평균 순위를 나눠 갖는다. 안 그러면 입력 순서가 상관을 흔든다."""
    assert fr._ranks([10, 20, 20, 30]) == [1.0, 2.5, 2.5, 4.0]


def test_spearman_of_a_perfect_increase_is_one():
    assert fr.spearman([1, 2, 3, 4], [10, 20, 30, 40]) == pytest.approx(1.0)


def test_spearman_of_a_perfect_decrease_is_minus_one():
    assert fr.spearman([1, 2, 3, 4], [40, 30, 20, 10]) == pytest.approx(-1.0)


def test_spearman_uses_rank_not_magnitude():
    """단조증가면 값의 크기와 무관하게 1 이다. 이것이 피어슨과의 차이다."""
    assert fr.spearman([1, 2, 3, 4], [1, 2, 3, 1000]) == pytest.approx(1.0)


def test_spearman_of_a_constant_is_none():
    """한쪽이 상수면 분모가 0 이다. 0.0 을 내면 '무상관' 과 구별되지 않는다."""
    assert fr.spearman([5, 5, 5, 5], [1, 2, 3, 4]) is None


def test_spearman_needs_two_points():
    assert fr.spearman([1], [2]) is None


def test_spearman_rejects_mismatched_lengths():
    with pytest.raises(ValueError):
        fr.spearman([1, 2, 3], [1, 2])


def test_ranks_handles_ties_at_the_boundaries():
    """맨 앞·맨 뒤 동점. 가운데 동점만 테스트하면 경계에서 어긋나도 통과한다."""
    assert fr._ranks([5, 5, 8, 9]) == [1.5, 1.5, 3.0, 4.0]
    assert fr._ranks([1, 2, 9, 9]) == [1.0, 2.0, 3.5, 3.5]


def test_ranks_handles_several_tie_groups():
    assert fr._ranks([1, 1, 2, 2, 3]) == [1.5, 1.5, 3.5, 3.5, 5.0]


def test_spearman_with_ties_is_pearson_on_ranks():
    """동점이 있을 때 축약식과 갈리는 값을 고정한다.

    흔히 쓰는 축약식 1 - 6*sum(d^2)/(n*(n^2-1)) 은 동점이 없을 때만 맞는다.
    아래 입력에서 축약식은 0.95 를 내고 순위에 피어슨을 적용한 올바른 값은
    0.9487 이다. 이 테스트가 그 차이를 잡는다 - 축 점수가 0~100 정수라
    동점은 예외가 아니라 기본이다.

    손계산:
      순위 xs [1, 2.5, 2.5, 4]   ys [1, 2, 3, 4]   평균 둘 다 2.5
      분자   (-1.5)(-1.5) + 0(-0.5) + 0(0.5) + (1.5)(1.5) = 4.5
      분모   sqrt(4.5 * 5.0) = sqrt(22.5)
      rho    4.5 / sqrt(22.5) = 3/sqrt(10) = 0.9486832980505138
    """
    got = fr.spearman([1, 2, 2, 3], [10, 20, 30, 40])
    assert got == pytest.approx(3 / 10 ** 0.5)
    # 축약식이었다면 0.95 가 나온다. 그 값과 구별되어야 한다.
    assert got != pytest.approx(0.95, abs=1e-9)


# ─── 축별 수집 ──────────────────────────────────────────────
AXIS_CSV_HEADER = ("date,ticker,bar_date,tech,flow,filing,value,total\n")


def _write_axis_archive(tmp_path, rows):
    """축 열만 가진 최소 아카이브를 만든다."""
    p = tmp_path / "2026-08-03.csv"
    p.write_text(AXIS_CSV_HEADER + "".join(rows), encoding="utf-8")
    return str(tmp_path / "*.csv")


def _write_axis_archives(tmp_path, by_date):
    """날짜별로 여러 파일을 쓴다. glob 이 전부를 읽는지 보기 위한 것이다."""
    for date, rows in by_date.items():
        (tmp_path / f"{date}.csv").write_text(
            AXIS_CSV_HEADER + "".join(rows), encoding="utf-8")
    return str(tmp_path / "*.csv")


def test_collect_axes_pairs_each_axis_with_its_return(tmp_path):
    pattern = _write_axis_archive(tmp_path, [
        "2026-08-03,AAA,2026-08-03,70,60,50,40,58\n",
    ])
    got = fr.collect_axes(pattern, PRICES, (1,))
    # 축 다섯 개 × horizon 하나
    assert len(got) == 5
    assert ("2026-08-03", 1, "tech", 70, pytest.approx(10.0)) in got
    assert ("2026-08-03", 1, "total", 58, pytest.approx(10.0)) in got


def test_collect_axes_skips_blank_axis_values(tmp_path):
    """ETF 행은 filing·value 가 비어 있다. 0 으로 읽으면 상관이 오염된다."""
    pattern = _write_axis_archive(tmp_path, [
        "2026-08-03,AAA,2026-08-03,70,60,,,65\n",
    ])
    got = fr.collect_axes(pattern, PRICES, (1,))
    assert sorted(r[2] for r in got) == ["flow", "tech", "total"]


def test_collect_axes_skips_rows_without_a_forward_return(tmp_path):
    """마지막 봉 뒤의 행은 잴 수 없다. 마지막 값으로 대체하지 않는다."""
    pattern = _write_axis_archive(tmp_path, [
        "2026-08-07,AAA,2026-08-07,70,60,50,40,58\n",
    ])
    assert fr.collect_axes(pattern, PRICES, (1,)) == []


def test_collect_axes_reads_every_matched_file(tmp_path):
    """glob 이 잡은 파일을 전부 읽는다. 첫 파일만 읽으면 아카이브가 하루로 줄어든다."""
    pattern = _write_axis_archives(tmp_path, {
        "2026-08-03": ["2026-08-03,AAA,2026-08-03,70,60,50,40,58\n"],
        "2026-08-04": ["2026-08-04,AAA,2026-08-04,71,61,51,41,59\n"],
    })
    got = fr.collect_axes(pattern, PRICES, (1,))
    assert sorted({r[0] for r in got}) == ["2026-08-03", "2026-08-04"]
    assert len(got) == 10   # 2일 × 축 5


def test_collect_axes_keeps_each_horizon_separate(tmp_path):
    """horizon 마다 제 수익률이 붙어야 한다. 실제 실행은 늘 (5, 10) 두 개다.

    PRICES 는 매일 10% 오르므로 1일 뒤 +10%, 2일 뒤 +21% 다. 두 값이
    구별되므로 horizon 을 섞으면 이 테스트가 걸린다.
    """
    pattern = _write_axis_archive(tmp_path, [
        "2026-08-03,AAA,2026-08-03,70,60,50,40,58\n",
    ])
    got = fr.collect_axes(pattern, PRICES, (1, 2))
    tech = {r[1]: r[4] for r in got if r[2] == "tech"}
    assert tech[1] == pytest.approx(10.0)
    assert tech[2] == pytest.approx(21.0)


# ─── 기간 분할 ──────────────────────────────────────────────
def test_median_date_splits_distinct_dates():
    rows = [("2026-08-01", 1, "tech", 70, 1.0),
            ("2026-08-01", 1, "flow", 60, 1.0),
            ("2026-08-02", 1, "tech", 70, 1.0),
            ("2026-08-03", 1, "tech", 70, 1.0)]
    # 고유 날짜 3개의 가운데
    assert fr.median_date(rows) == "2026-08-02"


def test_median_date_of_nothing_is_empty():
    assert fr.median_date([]) == ""


def test_median_date_splits_an_even_count_evenly():
    """고유 날짜가 짝수면 전·후반 날짜 수가 같아야 한다."""
    dates = ["2026-08-01", "2026-08-02", "2026-08-03", "2026-08-04"]
    rows = [(d, 1, "tech", 70, 1.0) for d in dates]
    cut = fr.median_date(rows)
    assert cut == "2026-08-03"
    assert len([d for d in dates if d < cut]) == 2
    assert len([d for d in dates if d >= cut]) == 2


# ─── 축 판정 ────────────────────────────────────────────────
def _axis_rows(n_each, early_sign, late_sign, cut="2026-08-15"):
    """전·후반 각각 지정한 부호의 완전 단조 표본을 만든다."""
    rows = []
    for i in range(n_each):
        rows.append(("2026-08-01", 5, "tech", i, i * early_sign))
        rows.append(("2026-08-20", 5, "tech", i, i * late_sign))
    return rows, cut


def test_axis_verdict_reports_rho_for_each_period():
    rows, cut = _axis_rows(200, +1, +1)
    got = fr.axis_verdict(rows, "tech", 5, cut)
    assert got["all"] == pytest.approx(1.0)
    assert got["early"] == pytest.approx(1.0)
    assert got["late"] == pytest.approx(1.0)
    assert got["flip"] is False
    assert got["n_early"] == 200 and got["n_late"] == 200


def test_axis_verdict_flags_a_sign_flip():
    """전·후반 부호가 다르면 신호로 보지 않는다. 이 표시가 판정의 핵심이다."""
    rows, cut = _axis_rows(200, +1, -1)
    got = fr.axis_verdict(rows, "tech", 5, cut)
    assert got["early"] > 0 and got["late"] < 0
    assert got["flip"] is True


def test_axis_verdict_returns_none_below_the_sample_floor():
    rows, cut = _axis_rows(5, +1, +1)
    got = fr.axis_verdict(rows, "tech", 5, cut)
    assert got["all"] is None
    assert got["flip"] is False


def test_axis_verdict_ignores_other_axes_and_horizons():
    rows = [("2026-08-01", 5, "tech", 1, 1.0),
            ("2026-08-01", 5, "flow", 1, 1.0),
            ("2026-08-01", 10, "tech", 1, 1.0)]
    got = fr.axis_verdict(rows, "tech", 5, "2026-08-01")
    assert got["n_all"] == 1


def test_sign_census_counts_a_stable_axis_on_one_side():
    """어디서 잘라도 같은 부호면 한쪽에만 쌓인다."""
    rows = []
    for day in range(1, 9):
        for i in range(300):
            rows.append((f"2026-08-{day:02d}", 5, "tech", i, -float(i)))
    got = fr.sign_census(rows, "tech", 5)
    assert got["early"]["pos"] == 0
    assert got["late"]["pos"] == 0
    assert got["early"]["neg"] + got["late"]["neg"] > 0


def test_sign_census_skips_cuts_that_starve_one_side():
    """한쪽이 MIN_AXIS_SAMPLE 에 못 미치는 기준일은 세지 않는다.

    가장 이른 기준일은 전반이 비므로 어느 칸도 늘지 않아야 한다.
    """
    rows = [("2026-08-01", 5, "tech", i, float(i)) for i in range(300)]
    got = fr.sign_census(rows, "tech", 5)
    assert got["early"] == {"neg": 0, "pos": 0, "unreadable": 0}


# ─── rho 하한 ───────────────────────────────────────────────
def test_rho_floor_shrinks_as_the_sample_grows():
    """표본이 클수록 0 과 구별할 수 있는 rho 가 작아진다."""
    assert fr._rho_floor(101) == pytest.approx(0.1)
    assert fr._rho_floor(10001) == pytest.approx(0.01)


def test_rho_floor_of_a_thin_sample_is_none():
    assert fr._rho_floor(1) is None
    assert fr._rho_floor(0) is None


def test_axis_verdict_reports_a_floor_for_each_period():
    """얇은 후반의 부호를 얼마나 믿을지 사람이 가늠할 재료를 함께 낸다."""
    rows, cut = _axis_rows(200, +1, +1)
    got = fr.axis_verdict(rows, "tech", 5, cut)
    assert got["floor_early"] == pytest.approx(199 ** -0.5)
    assert got["floor_late"] == pytest.approx(199 ** -0.5)
