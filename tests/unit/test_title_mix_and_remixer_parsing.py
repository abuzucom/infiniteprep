"""Title parsing separates real mixes and remixers from look-alike text."""

from __future__ import annotations

import unittest

from infiniteprep.services.matching_engine import parse_dj_title_and_mix


class RemixerInferenceTests(unittest.TestCase):
    """A remixer is a name, never a mix descriptor."""

    def test_descriptor_only_mix_names_yield_no_remixer(self) -> None:
        descriptor_titles = (
            "Track (Extended Club Mix)",
            "Track (Original Club Mix)",
            "Track (Extended Vocal Mix)",
            "Track (Full Vocal Mix)",
            "Track (12 Inch Mix)",
            "Track (Original Mix)",
        )
        for title in descriptor_titles:
            with self.subTest(title=title):
                base, mix, remixer = parse_dj_title_and_mix(title)
                self.assertEqual(base, "Track")
                self.assertIsNotNone(mix)
                self.assertIsNone(remixer)

    def test_contraction_is_not_a_possessive_remixer(self) -> None:
        for title in ("Track (It's Alright Mix)", "Track (Let's Dance Mix)"):
            with self.subTest(title=title):
                self.assertIsNone(parse_dj_title_and_mix(title)[2])

    def test_named_remixers_are_still_found(self) -> None:
        self.assertEqual(parse_dj_title_and_mix("The Dream (Sharam's Dreamscape Mix)")[2], "Sharam")
        self.assertEqual(parse_dj_title_and_mix("Track (Danny Tenaglia Dub)")[2], "Danny Tenaglia")
        self.assertEqual(parse_dj_title_and_mix("Track (Deep Dish Remix)")[2], "Deep Dish")


class MixKeywordTests(unittest.TestCase):
    """A mix keyword counts only as a whole word."""

    def test_keyword_inside_another_word_is_not_a_mix(self) -> None:
        plain_titles = (
            "Song (Live in Dublin)",
            "Song (feat. Meredith)",
            "Song (Subversion)",
        )
        for title in plain_titles:
            with self.subTest(title=title):
                self.assertEqual(parse_dj_title_and_mix(title), (title, None, None))

    def test_whole_word_keyword_is_a_mix(self) -> None:
        base, mix, _ = parse_dj_title_and_mix("Song (Radio Edit)")

        self.assertEqual(base, "Song")
        self.assertEqual(mix, "Radio Edit")


if __name__ == "__main__":
    unittest.main()
