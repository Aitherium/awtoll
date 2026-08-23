"""Reduce a tool call to a comparable SHAPE.

`awgraph query "who calls X"` and `awgraph query "where is Y"` are the same
shape; `awgraph query` and `awgraph callers` are not. Without this, every call
is its own row and nothing can be compared to anything.

This is a deliberate cousin of the signature logic in agent-toil harvesters,
and it DIVERGES in one place on purpose. A toil harvester drops inline code
(`python -c`, heredocs) because "automate this" is meaningless for a one-off
analysis. A toll meter keeps it, folded into a single `python -c (inline)` row,
because the question here is not "should this be automated" but "what did it
cost you" -- and inline analysis has a real, payable cost that is worth seeing
as one bucket. Dropping it would silently exclude spend from a spend report.
"""

from __future__ import annotations

import os
import re
from typing import Any, Dict

# Commands whose meaning lives in the subcommand, not the binary. `git` alone
# is not a shape; `git log` is.
MULTI_VERB = {
    "git", "awgit", "awgraph", "awm", "awrepl", "awkno", "awfind", "awask",
    "awrelay", "awrun", "awsh", "adk", "docker", "podman", "kubectl", "npm",
    "pnpm", "yarn", "pip", "uv", "cargo", "go", "gh", "systemctl", "wsl",
    "aws", "az", "gcloud", "terraform", "tofu", "ruff", "pytest", "mypy",
}

# Anything that VARIES between two calls of the same shape.
_VARIABLE = re.compile(
    r"""(?x)
    ^-                      # a flag
    | ^/                    # posix path
    | ^[A-Za-z]:[\\/]       # windows path
    | [\\/]                 # anything with a separator
    | ^\d                   # numbers, ids, ports
    | ^\$                   # a shell variable
    | \.(py|ps1|sh|ya?ml|json|jsonl|md|txt|log|toml|ini|cfg|ts|tsx|js)$
    """
)

_INLINE_CODE = re.compile(
    r"""(?xi)
    (?:^|\s)(?:python3?|py|pwsh|powershell|bash|sh|zsh|node|perl|ruby)
    (?:\.exe)?\s+(?:-\S+\s+)*(?:-[a-z]*c|-s|-Command|-EncodedCommand)\b
    | <<-?\s*['"]?[A-Za-z_]+['"]?
    """
)

_CONTROL = {
    "if", "then", "else", "elif", "fi", "for", "while", "until", "do", "done",
    "case", "esac", "select", "&&", "||", "|", ";", "{", "}", "(", ")", "\\",
    "time",
}
_SKIP_STEPS = _CONTROL | {
    "cd", "pushd", "popd", "echo", "printf", "set", "export", "true", "sleep",
    "source", ".",
}
# Wrappers that hide the real command behind them.
_WRAPPERS = {"sudo", "timeout", "env", "nohup", "xargs", "command", "nice", "stdbuf"}

_PLAUSIBLE_BINARY = re.compile(r"^[a-z][a-z0-9_.+-]{0,39}$")


def shape_of_bash(command: str) -> str:
    """A stable shape for a shell command, or "" when undecidable."""
    if not command or not command.strip():
        return ""
    if _INLINE_CODE.search(command):
        # ONE bucket, not dropped and not exploded per-script. See module docstring.
        m = re.search(r"(?i)\b(python3?|py|pwsh|powershell|bash|sh|node)\b", command)
        return f"{(m.group(1).lower() if m else 'shell')} -c (inline)"

    step = _primary_step(command)
    tokens = _unwrap(step.split())
    if not tokens:
        return ""

    binary = os.path.basename(tokens[0]).lower().strip("'\"")
    binary = re.sub(r"\.(exe|cmd|bat)$", "", binary)
    if not _PLAUSIBLE_BINARY.match(binary):
        return ""

    parts = [binary]
    if binary in MULTI_VERB:
        for tok in tokens[1:]:
            clean = tok.strip("'\"")
            if not clean or clean.startswith("-"):
                continue
            if _VARIABLE.search(clean):
                continue
            if not _PLAUSIBLE_BINARY.match(clean.lower()):
                continue
            parts.append(clean.lower())
            break
    return " ".join(parts)


def _primary_step(command: str) -> str:
    """The first step that is actually the point of the command."""
    # Split on separators, keeping it simple: quoting subtleties matter far
    # less here than never letting `cd x && real-command` sign as `cd`.
    steps = re.split(r"\s*(?:&&|\|\||;|\n)\s*", command.strip())
    for step in steps:
        s = step.strip()
        if not s:
            continue
        first = os.path.basename(s.split()[0]).lower().strip("'\"")
        first = re.sub(r"\.(exe|cmd|bat)$", "", first)
        if first in _SKIP_STEPS:
            continue
        return s
    return steps[0].strip() if steps else ""


def _unwrap(tokens: list) -> list:
    """Strip VAR=x prefixes and wrapper binaries until the real command shows."""
    out = list(tokens)
    changed = True
    while out and changed:
        changed = False
        head = os.path.basename(out[0]).lower().strip("'\"")
        head = re.sub(r"\.(exe|cmd|bat)$", "", head)
        # `VAR=$(cmd ...)` -- the real binary is INSIDE the same token, so
        # dropping the token wholesale hands the shape to the SECOND word.
        # Measured: `RUN=$(gh run list ...)` signed as `run`, 390 calls filed
        # under a binary that does not exist. An assignment with no command
        # substitution is dropped as before.
        sub = re.match(r"^[A-Za-z_][A-Za-z0-9_]*=[\$`]\(?(.+)$", out[0])
        if sub:
            out = [sub.group(1)] + out[1:]
            changed = True
        elif re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", out[0]):
            out = out[1:]
            changed = True
        elif head in _WRAPPERS:
            out = out[1:]
            # A wrapper's own flags/values are not the command.
            while out and out[0].startswith("-"):
                out = out[1:]
            if head == "timeout" and out and re.match(r"^[\d.]+[smhd]?$", out[0]):
                out = out[1:]
            changed = True
    return out


def shape_of(tool: str, inp: Dict[str, Any]) -> str:
    """The shape of any tool call: shell commands by verb, others by tool name."""
    if tool == "Bash":
        cmd = inp.get("command")
        shape = shape_of_bash(cmd if isinstance(cmd, str) else "")
        return f"$ {shape}" if shape else "$ (undecidable)"
    # An MCP tool's full name IS its shape -- the server prefix is meaningful.
    return tool


def target_of(tool: str, inp: Dict[str, Any]) -> str:
    """What the call was ABOUT, for repeat detection. "" when not applicable.

    This is what makes "you read the same file four times" decidable. It is
    intentionally conservative: a wrong target invents repeats that are not
    repeats, and a fabricated finding is worse than a missed one.
    """
    for key in ("file_path", "path", "notebook_path"):
        v = inp.get(key)
        if isinstance(v, str) and v:
            return os.path.normpath(v).replace("\\", "/").lower()
    if tool == "Bash":
        v = inp.get("command")
        if isinstance(v, str):
            return " ".join(v.split())  # whitespace-normalised, otherwise exact
    for key in ("pattern", "query", "url"):
        v = inp.get(key)
        if isinstance(v, str) and v:
            extra = ""
            for k2 in ("path", "glob", "output_mode"):
                v2 = inp.get(k2)
                if isinstance(v2, str) and v2:
                    extra += f"|{k2}={v2}"
            return f"{v.strip()}{extra}".lower()
    return ""
