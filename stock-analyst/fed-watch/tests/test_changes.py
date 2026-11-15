"""Change detection, and the baseline behaviour that makes it useful."""

from __future__ import annotations

import json
from datetime import date, datetime
from io import BytesIO

import pytest

from fed_watch import changes, cli, db, news
from fed_watch.models import MeetingProbabilities, Outcome


def snapshot(taken_at: str, hike_pct: float, no_change_pct: float, effr: float = 3.63) -> dict:
    return {
        "taken_at": taken_at,
        "effr": effr,
        "target_low": 3.50,
        "target_high": 3.75,
        "meetings": [
            {
                "meeting_date": "2026-09-16",
                "contract": "ZQU26.CBT",
                "implied_rate": 3.73,
                "expected_rate": 3.85,
                "outcomes": [
                    {
                        "step": 0,
                        "band_low": 3.50,
                        "band_high": 3.75,
                        "probability": no_change_pct / 100,
                    },
                    {
                        "step": 1,
                        "band_low": 3.75,
                        "band_high": 4.00,
                        "probability": hike_pct / 100,
                    },
                ],
            }
        ],
    }


def test_first_run_reports_rather_than_claiming_nothing_moved():
    report = changes.diff(None, snapshot("2026-09-12T09:00:00+08:00", 86.7, 13.3), 1.0)
    assert report["changed"] is True
    assert report["baseline_taken_at"] is None


def test_movement_under_the_threshold_is_silent():
    before = snapshot("2026-09-12T09:00:00+08:00", 86.7, 13.3)
    after = snapshot("2026-09-12T10:00:00+08:00", 87.2, 12.8)
    assert changes.diff(before, after, 1.0)["changed"] is False


def test_movement_over_the_threshold_is_reported_per_band():
    before = snapshot("2026-09-12T09:00:00+08:00", 86.7, 13.3)
    after = snapshot("2026-09-12T15:00:00+08:00", 91.4, 8.6)
    report = changes.diff(before, after, 1.0)

    assert report["changed"] is True
    assert report["largest_move_pct"] == pytest.approx(4.7, abs=0.01)
    rows = {row["label"]: row for row in report["meetings"][0]["outcomes"]}
    assert rows["25bp hike"]["from_pct"] == 86.7
    assert rows["25bp hike"]["to_pct"] == 91.4
    assert rows["25bp hike"]["delta_pct"] == pytest.approx(4.7, abs=0.01)
    assert rows["no change"]["delta_pct"] == pytest.approx(-4.7, abs=0.01)


def test_a_policy_move_is_a_change_even_when_probabilities_have_not_shifted():
    before = snapshot("2026-09-16T09:00:00+08:00", 86.7, 13.3, effr=3.63)
    after = snapshot("2026-09-17T09:00:00+08:00", 86.7, 13.3, effr=3.88)
    report = changes.diff(before, after, 1.0)
    assert report["changed"] is True
    assert report["policy_changed"] is True


def test_a_meeting_that_has_rolled_off_is_named_not_silently_dropped():
    before = snapshot("2026-09-12T09:00:00+08:00", 86.7, 13.3)
    before["meetings"].append({**before["meetings"][0], "meeting_date": "2026-10-28"})
    after = snapshot("2026-09-17T09:00:00+08:00", 86.7, 13.3)

    statuses = {m["meeting_date"]: m["status"] for m in changes.diff(before, after, 1.0)["meetings"]}
    assert statuses["2026-10-28"] == "dropped"


def test_series_groups_by_meeting_and_preserves_order():
    history = [
        snapshot("2026-09-12T09:00:00+08:00", 86.7, 13.3),
        snapshot("2026-09-12T15:00:00+08:00", 91.4, 8.6),
    ]
    shaped = changes.series(history)
    assert set(shaped) == {"2026-09-16"}
    assert shaped["2026-09-16"]["series"]["25bp hike"] == [86.7, 91.4]
    assert shaped["2026-09-16"]["timestamps"] == [
        "2026-09-12T09:00:00+08:00",
        "2026-09-12T15:00:00+08:00",
    ]


def test_series_backfills_a_band_that_was_not_priced_earlier():
    """A band absent from an earlier snapshot was priced at nothing, not unknown."""
    first = snapshot("2026-09-12T09:00:00+08:00", 86.7, 13.3)
    second = snapshot("2026-09-12T15:00:00+08:00", 60.0, 5.0)
    second["meetings"][0]["outcomes"].append(
        {"step": 2, "band_low": 4.00, "band_high": 4.25, "probability": 0.35}
    )
    shaped = changes.series([first, second])
    assert shaped["2026-09-16"]["series"]["50bp hike"] == [0.0, 35.0]


@pytest.mark.parametrize(
    "delta, env, override, cutoff, feed_case",
    [
        (4.9, None, None, 5.0, None),
        (5.0, None, None, 5.0, "match"),
        (5.1, None, None, 5.0, "unavailable"),
        (5.5, "6.0", None, 6.0, None),
        (4.0, "6.0", "4.0", 4.0, "empty"),
    ],
)
def test_scheduled_report_passes_original_diff_and_cnbc_context(
    db_path, policy, tmp_path, monkeypatch, capsys, delta, env, override, cutoff, feed_case
):
    monkeypatch.delenv("FED_WATCH_CHANGE_THRESHOLD", raising=False)
    if env is not None:
        monkeypatch.setenv("FED_WATCH_CHANGE_THRESHOLD", env)
    monkeypatch.setenv("FED_WATCH_CHART_DIR", str(tmp_path / "charts"))
    baseline = datetime.fromisoformat("2026-09-12T07:00:00+08:00")

    def store(conn, taken_at, hike):
        meeting = MeetingProbabilities(
            meeting_date=date(2026, 9, 16),
            ordinal=1,
            contract="ZQU26.CBT",
            price=96.27,
            implied_rate=3.73,
            expected_rate=3.63 + hike * 0.25,
            method="blend_inversion",
            outcomes=(
                Outcome(0, 3.50, 3.75, 1 - hike),
                Outcome(1, 3.75, 4.00, hike),
            ),
        )
        return db.store_snapshot(conn, taken_at, policy, [meeting], "test", "ok", {})

    with db.connect() as conn:
        db.mark_reported(conn, store(conn, baseline, 0.40), baseline)

    def take(conn, *, now, meetings_tracked):
        return {"snapshot_id": store(conn, now, 0.40 + delta / 100), "taken_at": now.isoformat()}

    article_url = "https://www.cnbc.com/2026/09/11/inflation.html"
    requests = []

    def fetch(request, *, timeout):
        requests.append(request.full_url)
        if request.full_url == news.FEED_URLS[0]:
            raise OSError("feed blocked")
        if feed_case == "unavailable":
            return BytesIO(b"not RSS")
        published = (
            "Fri, 11 Sep 2026 23:30:00 GMT" if feed_case == "match"
            else "Fri, 11 Sep 2026 22:30:00 GMT"
        )
        xml = f"""
        <rss><channel>
          <item>
            <title>U.S. inflation rose faster than expected</title>
            <link>{article_url}?__source=rss</link>
            <description>Consumer prices rose 0.4%.</description>
            <pubDate>{published}</pubDate>
          </item>
          <item>
            <title>Fed chair speaks</title>
            <link>https://www.cnbc.com/speech.html</link>
            <pubDate>Sat, 12 Sep 2026 01:30:00 GMT</pubDate>
          </item>
        </channel></rss>
        """
        return BytesIO(xml.encode())

    monkeypatch.setattr(cli.snapshot, "take", take)
    monkeypatch.setattr(news, "urlopen", fetch)
    args = ["check-changes", "--hermes-cron"]
    if override:
        args += ["--threshold", override]
    assert cli.main(args) == 0
    result = json.loads(capsys.readouterr().out)
    if feed_case is None:
        assert result == {"wakeAgent": False}
        assert requests == []
        with db.connect() as conn:
            assert db.latest_reported(conn)["taken_at"] == baseline.isoformat()
        return

    assert result["wakeAgent"] is True
    report = result["context"]
    assert report["changes"]["baseline_taken_at"] == baseline.isoformat()
    assert report["changes"]["largest_move_pct"] == delta
    assert report["changes"]["threshold_pct"] == cutoff
    assert report["charts"][0]["points"] == 2
    assert requests == list(news.FEED_URLS)

    cnbc = report["cnbc"]
    if feed_case == "unavailable":
        assert cnbc["status"] == "unavailable"
    else:
        assert cnbc["status"] == "ok"
        if feed_case == "match":
            assert cnbc["items"] == [{
                "title": "U.S. inflation rose faster than expected",
                "url": article_url,
                "published_at": "2026-09-11T23:30:00+00:00",
                "summary": "Consumer prices rose 0.4%.",
            }]
        else:
            assert cnbc["items"] == []

    assert cli.main([*args, "--no-fetch"]) == 0
    assert json.loads(capsys.readouterr().out) == {"wakeAgent": False}
    assert requests == list(news.FEED_URLS)
