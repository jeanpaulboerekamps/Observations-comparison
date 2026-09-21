"""Run with the dependencies from requirements.txt installed: python -m unittest test_review."""
import unittest
from unittest.mock import patch

from review import (CHOICES, comment_body, manual_review_record, pair_ids,
                    publish_review, _read_state, _signed_state)


class ReviewTests(unittest.TestCase):
    def test_reciprocal_texts_and_private_choice(self):
        self.assertEqual(pair_ids(8, 3), (3, 8))
        for choice in list(CHOICES)[:3]:
            self.assertIn("/observations/8", comment_body(choice, 8))
        with self.assertRaises(ValueError):
            comment_body("unrelated", 8)

    def test_state_is_signed_and_expires(self):
        state = _signed_state("test-secret", "area-token")
        self.assertEqual(_read_state("test-secret", state)["area"], "area-token")
        with self.assertRaises(ValueError):
            _read_state("another-secret", state)

    def test_manual_progress_never_claims_a_posted_comment_id(self):
        for stage in ("pending", "uncertain", "complete"):
            row = manual_review_record(8, 3, "same_species", stage)
            self.assertEqual((row["left_id"], row["right_id"], row["reviewer_id"]), (3, 8, 0))
            self.assertIsNone(row["left_comment_id"])
            self.assertIsNone(row["right_comment_id"])
        with self.assertRaises(ValueError):
            manual_review_record(3, 8, "same_species", "invalid")

    @patch("review.create_comment", side_effect=[51, 52])
    @patch("review.save_review", side_effect=lambda url, key, review: dict(review))
    def test_two_comments_one_each(self, save, post):
        result = publish_review("url", "secret", "token", 7, 8, 3, "same_species")
        self.assertEqual((result["left_comment_id"], result["right_comment_id"]), (51, 52))
        self.assertEqual(result["status"], "complete")
        self.assertEqual([a.args[1] for a in post.call_args_list], [3, 8])

    @patch("review.find_own_comment", return_value=None)
    @patch("review.create_comment", return_value=52)
    @patch("review.save_review", side_effect=lambda url, key, review: dict(review))
    def test_retry_skips_already_posted_side(self, save, post, lookup):
        prior = {"choice": "closely_related", "left_comment_id": 51,
                 "right_comment_id": None, "status": "pending"}
        result = publish_review("url", "secret", "token", 7, 3, 8,
                                "closely_related", prior)
        self.assertEqual(result["status"], "complete")
        self.assertEqual(post.call_count, 1)
        self.assertEqual(post.call_args.args[1], 8)

    @patch("review.create_comment")
    @patch("review.save_review", side_effect=lambda url, key, review: dict(review))
    def test_no_comments_for_unrelated(self, save, post):
        result = publish_review("url", "secret", "token", 7, 3, 8, "unrelated")
        self.assertEqual(result["status"], "complete")
        post.assert_not_called()


if __name__ == "__main__":
    unittest.main()
