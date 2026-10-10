"""
files.py

File based adapters:
    FileTemplateRepository   reads templates/*.html
    FileActivityLog          appends to logs/mail_log.txt
"""

import os
from datetime import datetime

from app.domain.interfaces import ActivityLog, Template, TemplateRepository


class FileTemplateRepository(TemplateRepository):
    """
    A template file may start with   <!-- SUBJECT: Hello {Name} -->
    The rest of the file is the HTML body.
    """

    def __init__(self, folder: str):
        self._folder = folder

    def exists(self, name: str) -> bool:
        return os.path.exists(os.path.join(self._folder, name))

    def load(self, name: str) -> Template:

        with open(os.path.join(self._folder, name), "r", encoding="utf-8") as file:
            lines = file.read().splitlines()

        subject = ""

        if lines and lines[0].startswith("<!-- SUBJECT:"):
            subject = lines[0].replace("<!-- SUBJECT:", "").replace("-->", "").strip()
            lines = lines[1:]

        return Template(subject, "\n".join(lines))


class FileActivityLog(ActivityLog):

    def __init__(self, path: str):
        self._path = path

    def write(self, name: str, email: str, status: str, message: str = "") -> None:

        os.makedirs(os.path.dirname(self._path), exist_ok=True)

        with open(self._path, "a", encoding="utf-8") as file:
            file.write("=" * 70 + "\n")
            file.write(f"Time    : {datetime.now()}\n")
            file.write(f"Name    : {name}\n")
            file.write(f"Email   : {email}\n")
            file.write(f"Status  : {status}\n")
            if message:
                file.write(f"Message : {message}\n")
            file.write("\n")
