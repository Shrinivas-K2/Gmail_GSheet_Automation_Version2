"""
validation.py

Small, stateless helpers for e-mail addresses.
"""

import re
from typing import List


class EmailValidator:

    _PATTERN = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}$")

    def is_valid(self, email) -> bool:
        if email is None:
            return False
        return self._PATTERN.match(email.strip()) is not None


class AddressListParser:
    """'a@x.com; b@y.com, c@z.com'  ->  ['a@x.com', 'b@y.com', 'c@z.com']"""

    def split(self, text) -> List[str]:
        if not text:
            return []
        return [part.strip() for part in str(text).replace(";", ",").split(",") if part.strip()]
