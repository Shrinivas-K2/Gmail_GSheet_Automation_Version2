"""
console.py

Everything that prints to / asks the person at the keyboard.
"""

from typing import Dict, List

from app.domain.interfaces import Prompter, Reporter


class ConsoleReporter(Reporter):

    def info(self, text: str = "") -> None:
        print(text)

    def warn(self, text: str) -> None:
        print(text)

    def error(self, text: str) -> None:
        print(text)


class ConsolePrompter(Prompter):

    def choose_scope(self, table: Dict[str, Dict[str, int]]) -> List[str]:

        print("\nSent mails in the database:\n")
        print(f"  {'Company / Tab':<22}{'Sent':>6}{'Replied':>9}{'Follow-ups':>12}")
        print("  " + "-" * 49)

        for name, v in sorted(table.items()):
            print(f"  {name:<22}{v['sent']:>6}{v['replied']:>9}{v['followups']:>12}")

        answer = input("\nType company / tab names (comma separated) or press Enter for ALL: ").strip()

        return [x.strip() for x in answer.split(",") if x.strip()]

    def confirm(self, question: str) -> bool:
        return input(f"\n{question} [y/N]: ").strip().lower() == "y"
