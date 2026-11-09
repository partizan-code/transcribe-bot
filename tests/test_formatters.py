import re
import unittest

from formatters import format_srt, format_text, timestamp
from processing import telegram_length
from transcribe import Transcript, Utterance, Word


class FormatterTests(unittest.TestCase):
    def test_text_preserves_html_and_speaker_changes(self) -> None:
        result = Transcript(
            "",
            utterances=(
                Utterance("<b>A & B</b>", 3000, 5000, "A"),
                Utterance("Да", 8000, 9000, "B"),
            ),
        )
        self.assertEqual(
            format_text(result), "[00:00:03] Спикер A:\n<b>A & B</b>\n\n[00:00:08] Спикер B:\nДа"
        )

    def test_plain_text_without_timings(self) -> None:
        result = Transcript("Только текст")
        self.assertEqual(format_text(result), result.text)
        self.assertEqual(format_srt(result).text, "")
        self.assertTrue(format_srt(result).incomplete)

    def test_neutral_speaker_and_missing_times(self) -> None:
        result = Transcript(
            "", utterances=(Utterance("Без времени"), Utterance("С временем", 10, 20))
        )
        self.assertTrue(format_text(result).startswith("Спикер:\nБез времени"))
        self.assertTrue(format_srt(result).incomplete)
        self.assertIn("00:00:00,010 --> 00:00:00,020", format_srt(result).text)

    def test_split_long_utterance_only_at_real_word_times(self) -> None:
        words = tuple(
            Word("Длинноеслово", index * 500, index * 500 + 400, "A") for index in range(30)
        )
        result = Transcript(
            "", utterances=(Utterance(" ".join(w.text for w in words), 0, 15000, "A", words),)
        )
        srt = format_srt(result)
        intervals = re.findall(r"(\d\d:\d\d:\d\d,\d{3}) --> (\d\d:\d\d:\d\d,\d{3})", srt.text)
        self.assertGreater(len(intervals), 3)
        self.assertEqual(intervals[0][0], "00:00:00,000")
        self.assertEqual(intervals[-1][1], "00:00:14,900")
        self.assertEqual(srt.text.count("Длинноеслово"), 30)
        self.assertFalse(srt.incomplete)

    def test_long_utterance_without_words_keeps_provider_interval(self) -> None:
        result = Transcript("", utterances=(Utterance("слово " * 80, 100, 9000),))
        self.assertEqual(format_srt(result).text.count(" --> "), 1)

    def test_invalid_times_and_overlap(self) -> None:
        result = Transcript(
            "",
            utterances=(
                Utterance("B", 2000, 4000, "B"),
                Utterance("A", 1000, 3000, "A"),
                Utterance("bad", 5000, 4000),
                Utterance("zero", 0, 0),
            ),
        )
        srt = format_srt(result)
        self.assertTrue(srt.incomplete)
        self.assertIn("1\n00:00:01,000 --> 00:00:03,000", srt.text)
        self.assertIn("2\n00:00:02,000 --> 00:00:04,000", srt.text)
        self.assertNotIn("bad", srt.text)

    def test_word_only_result_keeps_speakers(self) -> None:
        result = Transcript("one two", words=(Word("one", 0, 300, "A"), Word("two", 300, 500, "B")))
        self.assertIn("Спикер A", format_text(result))
        self.assertIn("Спикер B", format_srt(result).text)
        self.assertEqual(format_srt(result).text.count(" --> "), 2)

    def test_empty_result_and_utf16_length(self) -> None:
        self.assertEqual(format_text(Transcript(" \n")), "")
        self.assertEqual(format_srt(Transcript("")).text, "")
        self.assertEqual(telegram_length("😀" * 2049), 4098)
        self.assertEqual(timestamp(3_661_007, True), "01:01:01,007")

    def test_word_speaker_changes_inside_utterance(self) -> None:
        words = (Word("один", 0, 300, "A"), Word("два", 200, 500, "B"))
        result = Transcript("один два", utterances=(Utterance("один два", 0, 500, words=words),))
        srt = format_srt(result).text
        self.assertIn("Спикер A", srt)
        self.assertIn("Спикер B", srt)
        self.assertIn("00:00:00,200 --> 00:00:00,500", srt)

    def test_invalid_word_times_fall_back_to_valid_utterance(self) -> None:
        result = Transcript(
            "",
            utterances=(
                Utterance("два слова", 100, 1000, "A", (Word("два", 500, 400), Word("слова"))),
            ),
        )
        srt = format_srt(result)
        self.assertIn("00:00:00,100 --> 00:00:01,000", srt.text)
        self.assertFalse(srt.incomplete)
