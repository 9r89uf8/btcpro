from app.books.binance_local_book import LocalBook


def test_snapshot_requires_bridge_delta_before_sync():
    book = LocalBook()
    book.apply_snapshot(
        bids=[["100.0", "1.5"]],
        asks=[["100.5", "2.0"]],
        last_update_id=100,
    )

    assert book.synced is False
    assert book.last_update_id == 100


def test_bridge_delta_after_snapshot_syncs_book_and_updates_top():
    book = LocalBook()
    book.apply_snapshot(
        bids=[["100.0", "1.5"]],
        asks=[["100.5", "2.0"]],
        last_update_id=100,
    )

    applied = book.apply_delta(
        first_update_id=99,
        final_update_id=101,
        prev_final_update_id=98,
        bids=[[100.1, 3.0]],
        asks=[[100.5, 0.0], [100.4, 1.25]],
    )

    assert applied is True
    assert book.synced is True
    assert book.last_update_id == 101
    assert book.top() == ((100.1, 3.0), (100.4, 1.25))


def test_non_bridging_delta_after_snapshot_is_rejected():
    book = LocalBook()
    book.apply_snapshot(
        bids=[["100.0", "1.5"]],
        asks=[["100.5", "2.0"]],
        last_update_id=100,
    )

    # Delta starts well past the snapshot — no bridge possible
    applied = book.apply_delta(
        first_update_id=200,
        final_update_id=210,
        prev_final_update_id=190,
        bids=[[100.1, 3.0]],
        asks=[],
    )

    assert applied is False
    assert book.synced is False
    assert book.last_update_id == 100


def test_futures_delta_can_bridge_snapshot_via_prev_final_update_id():
    book = LocalBook()
    book.apply_snapshot(
        bids=[["100.0", "1.5"]],
        asks=[["100.5", "2.0"]],
        last_update_id=100,
    )

    applied = book.apply_delta(
        first_update_id=105,
        final_update_id=120,
        prev_final_update_id=100,
        bids=[[100.1, 2.0]],
        asks=[[100.4, 1.0]],
    )

    assert applied is True
    assert book.synced is True
    assert book.last_update_id == 120


def test_spot_delta_can_bridge_snapshot_via_last_update_plus_one():
    book = LocalBook()
    book.apply_snapshot(
        bids=[["100.0", "1.5"]],
        asks=[["100.5", "2.0"]],
        last_update_id=100,
    )

    applied = book.apply_delta(
        first_update_id=101,
        final_update_id=101,
        prev_final_update_id=None,
        bids=[[100.2, 2.5]],
        asks=[[100.4, 1.0]],
    )

    assert applied is True
    assert book.synced is True
    assert book.last_update_id == 101


def test_continuity_mismatch_marks_book_unsynced():
    book = LocalBook()
    book.apply_snapshot(
        bids=[["100.0", "1.5"]],
        asks=[["100.5", "2.0"]],
        last_update_id=100,
    )
    assert book.apply_delta(
        first_update_id=99,
        final_update_id=101,
        prev_final_update_id=98,
        bids=[[100.1, 3.0]],
        asks=[],
    )

    applied = book.apply_delta(
        first_update_id=102,
        final_update_id=103,
        prev_final_update_id=99,
        bids=[],
        asks=[[100.4, 1.25]],
    )

    assert applied is False
    assert book.synced is False
    assert book.last_update_id == 101
