"""The behaviours a two-hourly cron entry lives or dies by."""

from __future__ import annotations

from datetime import timedelta

from video_summary import check as check_run, db
from video_summary.fetch import Response


def run_check(conn, config, now, *, fetcher, transcriber, resolver=None, **kwargs):
    return check_run.check(
        conn, config, config.select(), now,
        fetcher=fetcher, transcriber=transcriber,
        resolver=resolver or (lambda url: None),
        sleeper=lambda _seconds: None,
        **kwargs,
    )


def test_a_cold_start_is_silent(conn, config, now, fetcher, transcriber):
    """A back catalogue is not news. The first check stores it already stamped."""
    payload = run_check(conn, config, now, fetcher=fetcher, transcriber=transcriber)
    assert payload["videos"] == []
    assert payload["pending_videos"] == 0
    assert payload["feeds"][0]["seeding"] is True
    # The sponsored entry was dropped at the door, seeding or not.
    assert payload["totals"]["videos_new"] == 2
    assert payload["totals"]["videos_excluded"] == 1


def test_the_second_check_reports_what_is_new(conn, config, now, feed_document, transcriber):
    responses = [feed_document, feed_document.replace("aaaaaaaaaa1", "dddddddddd4")]

    def fetcher(url, **_kwargs):
        return Response(url=url, status=200, text=responses.pop(0), etag=None, last_modified=None)

    run_check(conn, config, now, fetcher=fetcher, transcriber=transcriber)
    payload = run_check(conn, config, now, fetcher=fetcher, transcriber=transcriber)

    assert [video["video_id"] for video in payload["videos"]] == ["dddddddddd4"]
    video = payload["videos"][0]
    assert video["thumbnail_url"].endswith("dddddddddd4/hqdefault.jpg")
    assert video["transcript"]["status"] == "ok"
    assert video["transcript"]["path"].endswith("dddddddddd4.txt")
    assert video["feed_note"] == "rates and the long end"
    assert payload["summary_char_cap"] == 800


def test_a_304_costs_nothing(conn, config, now, fetcher, transcriber):
    run_check(conn, config, now, fetcher=fetcher, transcriber=transcriber)

    def unchanged(url, **_kwargs):
        return Response(url=url, status=304)

    payload = run_check(conn, config, now, fetcher=unchanged, transcriber=transcriber)
    assert payload["feeds"][0]["status"] == "unchanged"
    assert payload["totals"]["entries_seen"] == 0


def test_a_video_with_no_captions_is_held_then_released(
    conn, config, now, feed_document, no_transcriber
):
    responses = [feed_document, feed_document.replace("aaaaaaaaaa1", "dddddddddd4")]

    def fetcher(url, **_kwargs):
        return Response(url=url, status=200, text=responses.pop(0), etag=None, last_modified=None)

    run_check(conn, config, now, fetcher=fetcher, transcriber=no_transcriber)
    payload = run_check(conn, config, now, fetcher=fetcher, transcriber=no_transcriber)

    # Captions are generated after upload; a fresh video is not sent bare.
    assert payload["videos"] == []
    assert payload["held_for_transcript"] == 1

    later = now + timedelta(minutes=config.transcript_grace_minutes + 1)

    def unchanged(url, **_kwargs):
        return Response(url=url, status=304)

    payload = run_check(conn, config, later, fetcher=unchanged, transcriber=no_transcriber)
    assert [video["video_id"] for video in payload["videos"]] == ["dddddddddd4"]
    assert payload["videos"][0]["transcript"]["status"] == "unavailable"


def test_transcript_attempts_are_capped(conn, config, now, feed_document, no_transcriber):
    responses = [feed_document, feed_document.replace("aaaaaaaaaa1", "dddddddddd4")]

    def fetcher(url, **_kwargs):
        return Response(url=url, status=200, text=responses.pop(0), etag=None, last_modified=None)

    def unchanged(url, **_kwargs):
        return Response(url=url, status=304)

    run_check(conn, config, now, fetcher=fetcher, transcriber=no_transcriber)
    run_check(conn, config, now, fetcher=fetcher, transcriber=no_transcriber)
    for _ in range(5):
        run_check(conn, config, now, fetcher=unchanged, transcriber=no_transcriber)

    video = db.find_video(conn, "dddddddddd4")
    assert video.transcript_attempts == config.max_transcript_attempts


def test_marking_is_what_stops_a_repeat(conn, config, now, feed_document, transcriber):
    responses = [feed_document, feed_document.replace("aaaaaaaaaa1", "dddddddddd4")]

    def fetcher(url, **_kwargs):
        return Response(url=url, status=200, text=responses.pop(0), etag=None, last_modified=None)

    def unchanged(url, **_kwargs):
        return Response(url=url, status=304)

    run_check(conn, config, now, fetcher=fetcher, transcriber=transcriber)
    payload = run_check(conn, config, now, fetcher=fetcher, transcriber=transcriber)
    assert len(payload["videos"]) == 1

    # A check that is not followed by a mark hands the same video over again --
    # which is the correct behaviour when a send failed.
    again = run_check(conn, config, now, fetcher=unchanged, transcriber=transcriber)
    assert [video["video_id"] for video in again["videos"]] == ["dddddddddd4"]

    assert db.mark_summarised(conn, ["dddddddddd4"], now) == ["dddddddddd4"]
    assert db.mark_summarised(conn, ["dddddddddd4"], now) == []

    after = run_check(conn, config, now, fetcher=unchanged, transcriber=transcriber)
    assert after["videos"] == []
    assert after["pending_videos"] == 0


def test_the_same_video_in_two_feeds_is_one_video(
    conn, config_factory, now, feed_document, transcriber
):
    config = config_factory(feeds=[
        {"name": "a", "url": "https://www.youtube.com/feeds/videos.xml"
                             "?channel_id=UCnexoc6tvesvcCEzZhmI-Ag"},
        {"name": "b", "url": "https://www.youtube.com/feeds/videos.xml"
                             "?playlist_id=PLxxxxxxxxxxxxxxxxxx"},
    ], detect_shorts=False, exclude=[], max_per_check=5)

    def fetcher(url, **_kwargs):
        return Response(url=url, status=200, text=feed_document, etag=None, last_modified=None)

    payload = run_check(conn, config, now, fetcher=fetcher, transcriber=transcriber)
    # Three entries in each of two feeds, one row each: YouTube's id is the key.
    assert payload["totals"]["videos_new"] == 3
    assert len(db.recent_videos(conn, limit=50)) == 3


def test_a_dead_feed_does_not_take_the_others_down(
    conn, config_factory, now, feed_document, transcriber
):
    from video_summary.errors import FetchError

    config = config_factory(feeds=[
        {"name": "good", "url": "https://www.youtube.com/feeds/videos.xml"
                                "?channel_id=UCnexoc6tvesvcCEzZhmI-Ag"},
        {"name": "gone", "url": "https://www.youtube.com/feeds/videos.xml"
                                "?channel_id=UCaaaaaaaaaaaaaaaaaaaaaa"},
    ], detect_shorts=False, exclude=[])

    def fetcher(url, **_kwargs):
        if "UCaaaa" in url:
            raise FetchError("GET -> HTTP 404 Not Found", url=url)
        return Response(url=url, status=200, text=feed_document, etag=None, last_modified=None)

    payload = run_check(conn, config, now, fetcher=fetcher, transcriber=transcriber)
    assert payload["status"] == "partial"
    assert payload["ok"] is True
    assert [failure["feed"] for failure in payload["feed_failures"]] == ["gone"]
    assert payload["totals"]["videos_new"] == 3


def test_dry_run_writes_nothing(conn, config, now, fetcher, transcriber):
    payload = run_check(conn, config, now, fetcher=fetcher, transcriber=transcriber, dry_run=True)
    assert payload["feeds"][0]["candidates"]
    assert payload["run_id"] is None
    assert db.recent_videos(conn, limit=50) == []


def test_shorts_are_labelled_when_asked(conn, config_factory, now, feed_document, transcriber):
    """The feed does not say; one redirect does. A label, never a filter."""
    config = config_factory(detect_shorts=True, exclude=[])
    responses = [feed_document, feed_document.replace("aaaaaaaaaa1", "shortshort9")]

    def fetcher(url, **_kwargs):
        return Response(url=url, status=200, text=responses.pop(0), etag=None, last_modified=None)

    def resolver(url):
        # /shorts/<id> stays put for a Short, redirects to /watch for anything else.
        return url if "shortshort9" in url else url.replace("/shorts/", "/watch?v=")

    run_check(conn, config, now, fetcher=fetcher, transcriber=transcriber, resolver=resolver)
    payload = run_check(conn, config, now, fetcher=fetcher, transcriber=transcriber, resolver=resolver)

    assert [video["kind"] for video in payload["videos"]] == ["short"]
    # Videos absorbed by the cold start are never resolved: fifteen redirects to
    # label a back catalogue nobody will be shown is fifteen wasted requests.
    assert db.find_video(conn, "cccccccccc3").kind == "unknown"
