"""
retry.py

RetryingSender wraps ANY MailSender and adds "try up to N times".
(Decorator pattern: it has the same job as the sender it wraps, plus retries,
so the rest of the program does not change.)
"""

from app.domain.entities import OutgoingMail, SendResult
from app.domain.interfaces import Clock, MailSender, Reporter


class RetryingSender:

    def __init__(self, sender: MailSender, attempts: int, pause_seconds: float,
                 clock: Clock, reporter: Reporter):
        self._sender = sender
        self._attempts = attempts
        self._pause = pause_seconds
        self._clock = clock
        self._reporter = reporter

    def send(self, mail: OutgoingMail) -> SendResult:

        errors = []

        for attempt in range(1, self._attempts + 1):

            try:
                receipt = self._sender.send(mail)
                return SendResult(True, "Sent Successfully", receipt, attempt, errors)

            except Exception as e:
                errors.append(str(e))
                self._reporter.warn(f"Retry {attempt}/{self._attempts} for {mail.to}...")

                if attempt < self._attempts:
                    self._clock.sleep(self._pause)

        return SendResult(False, errors[-1], None, self._attempts, errors)
