"""
placeholders.py

Replaces {Name}, {Company} ... inside a template with values from a row.
"""

import re
from typing import List, Mapping


class PlaceholderRenderer:

    # {Word} that is still left after rendering = a value we do not have
    _LEFTOVER = re.compile(r"\{[A-Za-z_][\w ]*\}")

    def render(self, text: str, values: Mapping) -> str:
        result = text

        for key, value in values.items():
            placeholder = "{" + str(key) + "}"
            result = result.replace(placeholder, "" if value is None else str(value))

        return result

    def unfilled(self, text: str) -> List[str]:
        return sorted(set(self._LEFTOVER.findall(text)))
