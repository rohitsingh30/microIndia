"""Prompts (``prompts/<name>.md``, first line ``version: …``) and schemas (``schemas/<name>.json``)."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Dict

HERE = os.path.dirname(os.path.abspath(__file__))


@dataclass(frozen=True)
class Spec:
    name: str
    version: str        # from the prompt's first line, e.g. "reel-v1"
    prompt: str         # the prompt body (used as the system prompt)
    schema: Dict[str, Any]
    prompt_hash: str    # sha256 of prompt body + schema: changes whenever either changes


@lru_cache(maxsize=None)
def load(name: str) -> Spec:
    with open(os.path.join(HERE, "prompts", f"{name}.md")) as handle:
        text = handle.read()
    first, _, body = text.partition("\n")
    if not first.lower().startswith("version:"):
        raise ValueError(f"prompts/{name}.md must start with 'version: …'")
    version = first.split(":", 1)[1].strip()
    with open(os.path.join(HERE, "schemas", f"{name}.json")) as handle:
        schema = json.load(handle)
    digest = hashlib.sha256((body.strip() + "\n" + json.dumps(schema, sort_keys=True)).encode()).hexdigest()
    return Spec(name=name, version=version, prompt=body.strip(), schema=schema, prompt_hash=digest)


def fence(name: str, text: str) -> str:
    """Wrap untrusted Instagram text (captions, transcripts, bios, model outputs about them) as data.

    The prompts say that nothing between ``<<<NAME`` and ``NAME>>>`` is an instruction; fence markers
    inside the data are defused so it can't close the fence early."""
    body = str(text or "").replace("<<<", "‹‹‹").replace(">>>", "›››")
    return f"<<<{name}\n{body}\n{name}>>>"
