"""Юнит-тесты пересчёта долей, pin и ручного share."""

import unittest

from app.services.weights import (
    OfferWeight,
    WeightError,
    equal_split,
    redistribute,
    set_pinned_share,
    set_unpinned_share,
    validate_pinned_state,
)


class WeightsTest(unittest.TestCase):
    def test_equal_four(self):
        self.assertEqual(equal_split(4), [25, 25, 25, 25])

    def test_equal_three(self):
        self.assertEqual(sum(equal_split(3)), 100)
        self.assertEqual(equal_split(3), [34, 33, 33])

    def test_redistribute_unpinned(self):
        offers = [
            OfferWeight(1, 50, False, "active"),
            OfferWeight(2, 50, False, "active"),
            OfferWeight(3, 0, False, "active"),
        ]
        redistribute(offers)
        self.assertEqual([o.share for o in offers], [34, 33, 33])
        self.assertEqual(sum(o.share for o in offers), 100)

    def test_pinned_preserved(self):
        offers = [
            OfferWeight(1, 40, True, "active"),
            OfferWeight(2, 0, False, "active"),
            OfferWeight(3, 0, False, "active"),
        ]
        redistribute(offers)
        self.assertEqual(offers[0].share, 40)
        self.assertEqual(offers[1].share + offers[2].share, 60)

    def test_removed_zero(self):
        offers = [
            OfferWeight(1, 50, False, "removed"),
            OfferWeight(2, 0, False, "active"),
        ]
        redistribute(offers)
        self.assertEqual(offers[0].share, 0)
        self.assertEqual(offers[1].share, 100)

    def test_pinned_overflow(self):
        offers = [
            OfferWeight(1, 60, True, "active"),
            OfferWeight(2, 50, True, "active"),
        ]
        with self.assertRaises(WeightError):
            redistribute(offers)

    def test_pin_blocks_when_sum_100_and_others_remain(self):
        offers = [
            OfferWeight(1, 100, True, "active"),
            OfferWeight(2, 0, False, "active"),
        ]
        with self.assertRaises(WeightError) as ctx:
            validate_pinned_state(offers)
        self.assertIn("100%", str(ctx.exception))

    def test_cannot_pin_leaving_no_room(self):
        offers = [
            OfferWeight(1, 70, False, "active"),
            OfferWeight(2, 30, False, "active"),
        ]
        offers[0].pinned = True
        offers[0].share = 100
        with self.assertRaises(WeightError):
            validate_pinned_state(offers)

    def test_manual_share_unpinned(self):
        offers = [
            OfferWeight(1, 25, False, "active"),
            OfferWeight(2, 25, False, "active"),
            OfferWeight(3, 25, False, "active"),
            OfferWeight(4, 25, False, "active"),
        ]
        set_unpinned_share(offers, 1, 40)
        self.assertEqual(offers[0].share, 40)
        self.assertEqual(sum(o.share for o in offers), 100)
        self.assertEqual(offers[1].share + offers[2].share + offers[3].share, 60)

    def test_manual_share_respects_pinned(self):
        offers = [
            OfferWeight(1, 40, True, "active"),
            OfferWeight(2, 30, False, "active"),
            OfferWeight(3, 30, False, "active"),
        ]
        set_unpinned_share(offers, 2, 50)
        self.assertEqual(offers[0].share, 40)
        self.assertEqual(offers[1].share, 50)
        self.assertEqual(offers[2].share, 10)
        self.assertEqual(sum(o.share for o in offers if o.status == "active"), 100)

    def test_manual_share_too_large(self):
        offers = [
            OfferWeight(1, 60, True, "active"),
            OfferWeight(2, 40, False, "active"),
        ]
        with self.assertRaises(WeightError):
            set_unpinned_share(offers, 2, 50)

    def test_set_pinned_share_overflow(self):
        offers = [
            OfferWeight(1, 40, True, "active"),
            OfferWeight(2, 40, True, "active"),
            OfferWeight(3, 20, False, "active"),
        ]
        with self.assertRaises(WeightError):
            set_pinned_share(offers, 1, 70)

    def test_three_offers_after_remove_and_bring_back(self):
        offers = [
            OfferWeight(1, 34, False, "active"),
            OfferWeight(2, 33, False, "active"),
            OfferWeight(3, 33, False, "active"),
        ]
        offers[1].status = "removed"
        redistribute(offers)
        self.assertEqual([o.share for o in offers], [50, 0, 50])
        offers[1].status = "active"
        redistribute(offers)
        self.assertEqual(sum(o.share for o in offers), 100)
        self.assertTrue(all(o.share >= 1 for o in offers))

    def test_no_room_for_min_share_of_unpinned(self):
        offers = [
            OfferWeight(1, 98, True, "active"),
            OfferWeight(2, 0, False, "active"),
            OfferWeight(3, 0, False, "active"),
            OfferWeight(4, 0, False, "active"),
        ]
        with self.assertRaises(WeightError) as ctx:
            redistribute(offers)
        self.assertIn("минимум 1%", str(ctx.exception))

    def test_tight_quota_gives_everyone_one_percent(self):
        offers = [
            OfferWeight(1, 97, True, "active"),
            OfferWeight(2, 0, False, "active"),
            OfferWeight(3, 0, False, "active"),
            OfferWeight(4, 0, False, "active"),
        ]
        redistribute(offers)
        self.assertEqual([o.share for o in offers], [97, 1, 1, 1])

    def test_manual_share_cannot_starve_others(self):
        for share in (100, 0):
            offers = [
                OfferWeight(1, 50, False, "active"),
                OfferWeight(2, 50, False, "active"),
            ]
            with self.assertRaises(WeightError):
                set_unpinned_share(offers, 1, share)
            self.assertEqual([o.share for o in offers], [50, 50])

        offers = [
            OfferWeight(1, 34, False, "active"),
            OfferWeight(2, 33, False, "active"),
            OfferWeight(3, 33, False, "active"),
        ]
        with self.assertRaises(WeightError):
            set_unpinned_share(offers, 1, 99)
        set_unpinned_share(offers, 1, 98)
        self.assertEqual([o.share for o in offers], [98, 1, 1])

    def test_pinned_share_zero_rejected(self):
        offers = [
            OfferWeight(1, 40, True, "active"),
            OfferWeight(2, 60, False, "active"),
        ]
        with self.assertRaises(WeightError):
            set_pinned_share(offers, 1, 0)

    def test_set_pinned_share_ok(self):
        offers = [
            OfferWeight(1, 40, True, "active"),
            OfferWeight(2, 30, False, "active"),
            OfferWeight(3, 30, False, "active"),
        ]
        set_pinned_share(offers, 1, 20)
        self.assertEqual(offers[0].share, 20)
        self.assertEqual(sum(o.share for o in offers), 100)


class DiffTest(unittest.TestCase):
    def test_diff_added_removed_share_pin(self):
        from types import SimpleNamespace

        from app.services.diff import build_stream_diff

        stream = SimpleNamespace(
            is_draft=True,
            published_snapshot={
                "offers": [
                    {
                        "keitaro_offer_id": 1,
                        "offer_name": "A",
                        "share": 50,
                        "pinned": False,
                        "status": "active",
                        "sort_order": 0,
                    },
                    {
                        "keitaro_offer_id": 2,
                        "offer_name": "B",
                        "share": 50,
                        "pinned": False,
                        "status": "active",
                        "sort_order": 1,
                    },
                ]
            },
            offers=[
                SimpleNamespace(
                    keitaro_offer_id=1,
                    offer_name="A",
                    share=70,
                    pinned=True,
                    status="active",
                    sort_order=0,
                ),
                SimpleNamespace(
                    keitaro_offer_id=3,
                    offer_name="C",
                    share=30,
                    pinned=False,
                    status="active",
                    sort_order=1,
                ),
                SimpleNamespace(
                    keitaro_offer_id=2,
                    offer_name="B",
                    share=0,
                    pinned=False,
                    status="removed",
                    sort_order=2,
                ),
            ],
        )
        diff = build_stream_diff(stream)
        self.assertIsNotNone(diff)
        self.assertEqual(len(diff["added"]), 1)
        self.assertEqual(diff["added"][0]["keitaro_offer_id"], 3)
        self.assertEqual(len(diff["removed"]), 1)
        self.assertEqual(diff["removed"][0]["keitaro_offer_id"], 2)
        self.assertEqual(len(diff["share_changed"]), 1)
        self.assertEqual(diff["share_changed"][0]["share_before"], 50)
        self.assertEqual(diff["share_changed"][0]["share_after"], 70)
        self.assertEqual(len(diff["pin_changed"]), 1)


if __name__ == "__main__":
    unittest.main()
