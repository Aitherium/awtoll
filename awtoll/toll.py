"""Turn result text into a token count, and say which method produced it.

An estimate presented as a measurement is worse than no number, because it is
acted on with the same confidence. Every count carries `estimated: bool` and
the tokenizer's name, and every report prints both.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

# Measured, not assumed: 7,690 real tool results across 10 sessions of this
# repo's transcripts tokenize at 3.459 chars/token aggregate (median 3.463 --
# the two agree, so the constant is stable rather than dragged by outliers).
#
# The folk constant is 4.0, and using it would understate EVERY toll by ~13%.
# Tool output is denser than prose: paths, JSON, code and log lines fragment
# into more tokens per character than English does.
#
# Spread is real -- p10 2.68, p90 4.23 -- so a single call's estimate can be
# ~20% out either way. That is fine for RANKING shapes against each other and
# is not fine for a headline number, which is why the estimator is a fallback
# and never silently preferred.
MEASURED_CHARS_PER_TOKEN = 3.459

# A non-text result block (an image) is marked by the parser. It costs real
# tokens that a character count cannot see, so it is counted as unknown rather
# than as zero -- see Tokenizer.count.
IMAGE_SENTINEL = "\x00image\x00"


@dataclass
class Tokenizer:
    name: str
    estimated: bool
    _enc: object = None
    #: Why the exact tokenizer is absent, when it is. Reported, never swallowed:
    #: "tiktoken is not installed" and "tiktoken is installed and broken" are
    #: different problems and only one of them is the user's choice.
    unavailable_reason: str = ""
    #: Calls where the exact encoder threw and the estimator answered instead.
    #: Without this counter a partially-degraded run reports "(exact)" while
    #: some of its numbers are estimates -- the tokenizer equivalent of a
    #: silent no-op, and the reason a bare `except: pass` is wrong here.
    fallback_calls: int = 0

    def count(self, text: str) -> int:
        if not text:
            return 0
        if self._enc is not None:
            try:
                return len(self._enc.encode(text, disallowed_special=()))
            except Exception as exc:  # noqa: BLE001 - any encoder failure degrades
                # One bad input must not take the run down, but it must not
                # vanish either: count it and say so in describe().
                self.fallback_calls += 1
                if not self.unavailable_reason:
                    self.unavailable_reason = f"encoder failed on some input: {exc!r}"
        return int(round(len(text) / MEASURED_CHARS_PER_TOKEN))

    def describe(self) -> str:
        if self.estimated:
            why = f"; {self.unavailable_reason}" if self.unavailable_reason else ""
            return (
                f"{self.name} (ESTIMATED at {MEASURED_CHARS_PER_TOKEN} chars/token, "
                f"measured on 7,690 real tool results; install tiktoken for exact "
                f"counts{why})"
            )
        if self.fallback_calls:
            return (
                f"{self.name} (exact, EXCEPT {self.fallback_calls} call(s) that fell "
                f"back to the estimator -- {self.unavailable_reason})"
            )
        return f"{self.name} (exact)"


def get_tokenizer(prefer_estimate: bool = False) -> Tokenizer:
    """Exact tokenizer when one is installed, measured estimator otherwise.

    tiktoken is an OPTIONAL dependency on purpose: this brick must run on a
    stranger's machine with nothing installed, and a hard dependency on a
    tokenizer for a tool whose whole job is to be cheap to run is a bad trade.

    A failure to load it is RECORDED on the returned tokenizer rather than
    swallowed. "not installed" is a choice; "installed and raising" is a defect,
    and collapsing the two into the same silent fallback is how a broken
    dependency reads as a configuration preference.
    """
    if not prefer_estimate:
        try:
            import tiktoken  # type: ignore[import-not-found]

            enc = tiktoken.get_encoding("cl100k_base")
            return Tokenizer(name="tiktoken/cl100k_base", estimated=False, _enc=enc)
        except ImportError:
            reason = "tiktoken is not installed"
        except Exception as exc:  # noqa: BLE001 - a broken encoder is not an absent one
            reason = f"tiktoken is installed but would not load: {exc!r}"
        return Tokenizer(
            name="chars/token estimator", estimated=True, unavailable_reason=reason
        )
    return Tokenizer(name="chars/token estimator", estimated=True)


def count_result(tok: Tokenizer, text: Optional[str]) -> int:
    if not text:
        return 0
    if IMAGE_SENTINEL in text:
        # Text tokens plus an unknown image cost. Reported as the text part;
        # the caller flags the call so the number is never read as complete.
        text = text.replace(IMAGE_SENTINEL, "")
    return tok.count(text)


def has_image(text: Optional[str]) -> bool:
    return bool(text) and IMAGE_SENTINEL in (text or "")
