"""Plain text and SRT built exclusively from provider timestamps (milliseconds)."""

from dataclasses import dataclass
from textwrap import fill

from transcribe import Transcript, Utterance, Word


def speaker_label(speaker: str | None) -> str:
    return f"Спикер {speaker}" if speaker else "Спикер"


def timestamp(milliseconds: int, srt: bool = False) -> str:
    seconds, millis = divmod(milliseconds, 1000)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    base = f"{hours:02}:{minutes:02}:{seconds:02}"
    return f"{base},{millis:03}" if srt else base


def segments(result: Transcript) -> tuple[Utterance, ...]:
    if result.utterances:
        return result.utterances
    grouped: list[Utterance] = []
    words: list[Word] = []
    for word in result.words:
        if words and word.speaker != words[-1].speaker:
            grouped.append(_from_words(words))
            words = []
        words.append(word)
    if words:
        grouped.append(_from_words(words))
    return tuple(grouped)


def _from_words(words: list[Word]) -> Utterance:
    return Utterance(
        " ".join(word.text for word in words),
        words[0].start,
        words[-1].end,
        words[0].speaker,
        tuple(words),
    )


def format_text(result: Transcript) -> str:
    blocks = []
    for item in segments(result):
        if item.text.strip():
            prefix = f"[{timestamp(item.start)}] " if item.start is not None else ""
            blocks.append(f"{prefix}{speaker_label(item.speaker)}:\n{item.text.strip()}")
    return "\n\n".join(blocks) or result.text.strip()


def _valid(start: int | None, end: int | None) -> bool:
    return start is not None and end is not None and 0 <= start < end


def _word_cue(words: list[Word], speaker: str | None) -> tuple[int, int, str, str | None]:
    return (
        words[0].start,
        max(word.end for word in words),
        " ".join(word.text for word in words),
        words[0].speaker or speaker,
    )


@dataclass(frozen=True)
class Subtitles:
    text: str
    incomplete: bool = False


def format_srt(result: Transcript) -> Subtitles:
    cues: list[tuple[int, int, str, str | None]] = []
    incomplete = False
    for item in segments(result):
        if not item.text.strip():
            continue
        words = item.words
        usable = bool(words) and all(_valid(w.start, w.end) for w in words)
        usable = usable and all(a.start <= b.start for a, b in zip(words, words[1:], strict=False))
        if usable:
            group: list[Word] = []
            for word in words:
                if group and (
                    len(" ".join(w.text for w in (*group, word))) > 72
                    or word.end - group[0].start > 6000
                    or word.start - group[-1].end > 1500
                    or (word.speaker or item.speaker) != (group[-1].speaker or item.speaker)
                ):
                    cues.append(_word_cue(group, item.speaker))
                    group = []
                group.append(word)
            if group:
                cues.append(_word_cue(group, item.speaker))
        elif _valid(item.start, item.end):
            # Keep a long cue intact when word timestamps are unavailable.
            cues.append((item.start, item.end, item.text, item.speaker))
        else:
            incomplete = True
    cues.sort(key=lambda cue: (cue[0], cue[1]))
    blocks = []
    for number, (start, end, text, speaker) in enumerate(cues, 1):
        clean = " ".join(text.split()).replace("-->", "→")
        caption = fill(f"{speaker_label(speaker)}: {clean}", width=42, break_long_words=False)
        blocks.append(f"{number}\n{timestamp(start, True)} --> {timestamp(end, True)}\n{caption}\n")
    return Subtitles("\n".join(blocks), incomplete or not cues)
