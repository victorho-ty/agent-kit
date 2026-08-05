"""M4 — purge and media GC. Over-tested on purpose."""

from __future__ import annotations

import pytest

from coupon_tracker import lifecycle, purge, store
from coupon_tracker.errors import ExitCode, NotFoundError

from .conftest import add

DEFAULT = purge.PurgeSelection()


def test_default_selection_takes_used_and_expired_only(scope, now):
    used = add(scope, now)
    lifecycle.mark_used(scope, used.id, now)
    expired = add(scope, now, expires_on="2026-08-01")
    lifecycle.sweep_expiry(scope, now)
    active = add(scope, now, title="keep me")
    pending = add(scope, now, title="review me", status="needs_review")
    voided = add(scope, now, title="void me")
    lifecycle.mark_void(scope, voided.id, "mis-scan", now)

    manifest = purge.run(scope, DEFAULT, now, commit=True)
    purged = {c["id"] for c in manifest["coupons_purged"]}

    assert purged == {used.id, expired.id}
    assert store.find(scope, active.id) is not None
    assert store.find(scope, pending.id) is not None
    assert store.find(scope, voided.id) is not None


def test_include_void_takes_void_too(scope, now):
    voided = add(scope, now)
    lifecycle.mark_void(scope, voided.id, "mis-scan", now)

    manifest = purge.run(scope, purge.PurgeSelection(include_void=True), now, commit=True)

    assert [c["id"] for c in manifest["coupons_purged"]] == [voided.id]
    assert store.find(scope, voided.id) is None


def test_image_with_three_coupons_is_released_one_at_a_time(scope, now, image):
    media = store.register_media(scope, image, now)
    coupons = [add(scope, now, title=f"coupon {i}", media_id=media.id) for i in range(3)]
    media_file = scope.media_dir / media.path

    for index, coupon in enumerate(coupons):
        lifecycle.mark_used(scope, coupon.id, now)
        manifest = purge.run(scope, DEFAULT, now, commit=True)
        remaining = 2 - index

        if remaining:
            assert manifest["media_held"][0]["refs"] == remaining
            assert manifest["totals"]["media_deleted"] == 0
            assert media_file.is_file()
        else:
            assert manifest["media_held"] == []
            assert manifest["totals"]["media_deleted"] == 1
            assert not media_file.exists()
            assert store.get_media(scope, media.id) is None


def test_sibling_active_coupon_keeps_its_image(scope, now, image):
    media = store.register_media(scope, image, now)
    doomed = add(scope, now, title="used one", media_id=media.id)
    sibling = add(scope, now, title="still good", media_id=media.id)
    lifecycle.mark_used(scope, doomed.id, now)

    purge.run(scope, DEFAULT, now, commit=True)

    assert store.get(scope, sibling.id).media_id == media.id
    assert (scope.media_dir / media.path).is_file()


def test_dry_run_changes_nothing_but_matches_the_commit_manifest(scope, now, image):
    media = store.register_media(scope, image, now)
    coupon = add(scope, now, media_id=media.id)
    lifecycle.mark_used(scope, coupon.id, now)

    dry = purge.run(scope, DEFAULT, now, commit=False)

    assert dry["dry_run"] is True
    assert store.find(scope, coupon.id) is not None
    assert (scope.media_dir / media.path).is_file()

    wet = purge.run(scope, DEFAULT, now, commit=True)
    assert dry.keys() == wet.keys()
    assert dry["coupons_purged"] == wet["coupons_purged"]
    assert dry["totals"] == wet["totals"]


def test_merchant_narrowing_holds_out_of_scope_images(scope, now, image):
    media = store.register_media(scope, image, now)
    mine = add(scope, now, merchant="Cafe de Coral", media_id=media.id)
    other = add(scope, now, merchant="Maxims", media_id=media.id)
    lifecycle.mark_used(scope, mine.id, now)
    lifecycle.mark_used(scope, other.id, now)

    manifest = purge.run(scope, purge.PurgeSelection(merchant="Maxims"), now, commit=True)

    assert [c["id"] for c in manifest["coupons_purged"]] == [other.id]
    assert manifest["media_held"][0]["refs"] == 1
    assert (scope.media_dir / media.path).is_file()


def test_older_than_narrowing(scope, now):
    old = add(scope, now, expires_on="2026-01-01")
    recent = add(scope, now, expires_on="2026-08-01")
    lifecycle.sweep_expiry(scope, now)

    manifest = purge.run(scope, purge.PurgeSelection(older_than_days=30), now, commit=True)

    assert [c["id"] for c in manifest["coupons_purged"]] == [old.id]
    assert store.find(scope, recent.id) is not None


def test_id_narrowing_refuses_another_accounts_id_and_purges_nothing(scope, other_scope, now):
    mine = add(scope, now)
    lifecycle.mark_used(scope, mine.id, now)
    theirs = add(other_scope, now)
    lifecycle.mark_used(other_scope, theirs.id, now)

    with pytest.raises(NotFoundError):
        purge.run(scope, purge.PurgeSelection(ids=(mine.id, theirs.id)), now, commit=True)

    assert store.find(scope, mine.id) is not None
    assert store.find(other_scope, theirs.id) is not None


def test_orphan_file_is_reported_and_never_deleted(scope, now):
    scope.ensure_dirs()
    orphan = scope.media_dir / "stray.jpg"
    orphan.write_bytes(b"not tracked")

    manifest = purge.run(scope, DEFAULT, now, commit=True)

    assert manifest["anomalies"]["orphan_files"] == ["stray.jpg"]
    assert purge.is_unsafe(manifest)
    assert orphan.is_file()

    with pytest.raises(Exception) as exc:
        purge.check(manifest)
    assert exc.value.exit_code is ExitCode.ERR_PURGE_UNSAFE


def test_missing_file_is_reported_and_the_row_is_kept(scope, now, image):
    media = store.register_media(scope, image, now)
    add(scope, now, media_id=media.id)
    (scope.media_dir / media.path).unlink()

    manifest = purge.run(scope, DEFAULT, now, commit=True)

    assert manifest["anomalies"]["missing_files"][0]["id"] == media.id
    assert store.get_media(scope, media.id) is not None


def test_bytes_freed_matches_the_deleted_file_size(scope, now, image):
    media = store.register_media(scope, image, now)
    coupon = add(scope, now, media_id=media.id)
    lifecycle.mark_used(scope, coupon.id, now)
    size = (scope.media_dir / media.path).stat().st_size

    manifest = purge.run(scope, DEFAULT, now, commit=True)

    assert manifest["totals"]["bytes_freed"] == size


class FlakyConn:
    """Delegates to the real connection, failing the Nth executemany.

    sqlite3.Connection is a C type and rejects attribute assignment, so the
    failure has to be injected with a wrapper rather than monkeypatch.setattr.
    """

    def __init__(self, real, fail_on: int):
        self._real = real
        self._fail_on = fail_on
        self.calls = 0

    def executemany(self, sql, params):
        self.calls += 1
        if self.calls == self._fail_on:
            raise RuntimeError("simulated disk failure")
        return self._real.executemany(sql, params)

    def __getattr__(self, name):
        return getattr(self._real, name)


def test_db_failure_mid_purge_leaves_rows_and_files_intact(scope, now, image):
    import dataclasses

    media = store.register_media(scope, image, now)
    coupon = add(scope, now, media_id=media.id)
    lifecycle.mark_used(scope, coupon.id, now)

    # Fail on the second executemany — deleting the media rows, after the
    # coupon rows have already gone in the same transaction.
    flaky = dataclasses.replace(scope, conn=FlakyConn(scope.conn, fail_on=2))

    with pytest.raises(RuntimeError):
        purge.run(flaky, DEFAULT, now, commit=True)

    assert store.find(scope, coupon.id) is not None
    assert store.get_media(scope, media.id) is not None
    assert (scope.media_dir / media.path).is_file()


def test_purging_one_account_never_touches_the_other(scope, other_scope, now, image):
    """The case this milestone exists for: same image bytes, two accounts."""
    mine = store.register_media(scope, image, now)
    theirs = store.register_media(other_scope, image, now)
    my_coupon = add(scope, now, media_id=mine.id)
    their_coupon = add(other_scope, now, media_id=theirs.id)
    lifecycle.mark_used(scope, my_coupon.id, now)
    lifecycle.mark_used(other_scope, their_coupon.id, now)

    manifest = purge.run(scope, DEFAULT, now, commit=True)

    assert not (scope.media_dir / mine.path).exists()
    assert (other_scope.media_dir / theirs.path).is_file()
    assert store.find(other_scope, their_coupon.id) is not None
    assert store.get_media(other_scope, theirs.id) is not None

    serialized = str(manifest)
    assert other_scope.account_id not in serialized
    assert their_coupon.id not in serialized


def test_another_accounts_media_directory_is_never_scanned(scope, other_scope, now):
    other_scope.ensure_dirs()
    stray = other_scope.media_dir / "theirs.jpg"
    stray.write_bytes(b"belongs to bob")

    manifest = purge.run(scope, DEFAULT, now, commit=True)

    assert manifest["anomalies"]["orphan_files"] == []
    assert stray.is_file()


def test_doctor_is_clean_on_a_healthy_account(scope, now, image):
    media = store.register_media(scope, image, now)
    add(scope, now, media_id=media.id)

    report = purge.doctor(scope)

    assert report["clean"] is True
    assert report["problems"] == []
