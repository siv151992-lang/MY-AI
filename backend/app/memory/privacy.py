"""User-controlled privacy settings and the rules that decide what may be remembered.

Sensitivity levels (highest wins):
  secret     credentials, card numbers, government IDs, private keys. Never stored, whoever asks.
  sensitive  special-category data (health, religion, sexuality, politics, finances, legal status).
             Stored only when the user adds it themselves or explicitly asks the assistant to remember it,
             or when they enabled `allow_sensitive`; automatically extracted ones always need approval.
  personal   contact details (email, phone, street address). Stored, but redacted from source excerpts.
  normal     everything else.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field, fields

LEVELS = ("normal", "personal", "sensitive", "secret")
USER_SOURCES = {"manual", "explicit", "import"}  # the user themselves asked for it to be kept


@dataclass
class PrivacySettings:
    memory_enabled: bool = True             # master switch for reading and writing long-term memory
    auto_extract: bool = True               # propose memories from conversations
    require_approval: bool = True           # proposed memories wait for the user's approval
    use_in_chat: bool = True                # inject relevant memories into prompts
    allow_sensitive: bool = False           # allow special-category facts to be proposed
    store_source_excerpts: bool = True      # keep the (redacted) words a memory was learned from
    summarize_conversations: bool = True    # keep rolling summaries / segments for long chats
    cross_conversation_recall: bool = False  # recall summaries of *other* chats (opt-in)
    retention_days: int | None = None       # delete unpinned memories unused for this many days
    pending_expiry_days: int = 14           # discard unreviewed suggestions after this many days

    @classmethod
    def from_dict(cls, data: dict | None) -> "PrivacySettings":
        s = cls()
        for f in fields(cls):
            if not data or f.name not in data:
                continue
            v = data[f.name]
            if f.name == "retention_days":
                s.retention_days = None if v in (None, "", 0) else max(1, min(3650, int(v)))
            elif f.name == "pending_expiry_days":
                s.pending_expiry_days = max(1, min(365, int(v)))
            elif isinstance(v, bool):
                setattr(s, f.name, v)
        return s

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Sensitivity:
    level: str = "normal"
    reasons: list[str] = field(default_factory=list)

    def raise_to(self, level: str, reason: str) -> None:
        if LEVELS.index(level) > LEVELS.index(self.level):
            self.level = level
        if reason not in self.reasons:
            self.reasons.append(reason)


@dataclass
class Admission:
    allowed: bool
    sensitivity: str
    force_pending: bool = False
    reason: str = ""


def _luhn(digits: str) -> bool:
    total, alt = 0, False
    for ch in reversed(digits):
        d = int(ch)
        if alt:
            d = d * 2 - 9 if d > 4 else d * 2
        total += d
        alt = not alt
    return total % 10 == 0


_CARD = re.compile(r"\b(?:\d[ -]?){13,19}\b")
SECRET_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("password", re.compile(r"\b(?:password|passwd|pwd|passcode|pin(?: code)?)\b\s*(?:is|=|:)\s*\S+", re.I)),
    ("API key or token", re.compile(
        r"\b(?:sk-(?:ant-|proj-)?[A-Za-z0-9_-]{16,}|gh[pousr]_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}|"
        r"xox[abprs]-[A-Za-z0-9-]{10,}|AIza[0-9A-Za-z_-]{30,}|eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]+)")),
    ("API key or token", re.compile(r"\b(?:api[_ -]?key|secret|token|bearer)\b\s*(?:is|=|:)\s*[A-Za-z0-9_\-./+]{12,}", re.I)),
    ("private key", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("social security number", re.compile(r"\b\d{3}-\d{2}-\d{4}\b")),
    ("bank account number", re.compile(r"\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]{4}){3,7}\b")),  # IBAN
    ("government ID number", re.compile(r"\b(?:passport|aadhaar|ssn|national id|driver'?s licen[cs]e)\b[^.\n]{0,20}?\d[\dA-Z -]{5,}", re.I)),
]
SENSITIVE_TERMS: list[tuple[str, re.Pattern]] = [
    ("health", re.compile(r"\b(?:diagnos\w*|illness|disease|disorder|medication|prescri\w+|therap(?:y|ist)|depress\w*|"
                          r"anxiety|adhd|autis\w*|pregnan\w*|hiv|cancer|diabet\w*|disabilit\w*|surgery|mental health|"
                          r"allerg\w*)\b", re.I)),
    ("religion or beliefs", re.compile(r"\b(?:religio\w*|christian|muslim|islam\w*|hindu\w*|jewish|judaism|buddhis\w*|"
                                       r"atheis\w*|church|mosque|temple|synagogue)\b", re.I)),
    ("sexuality or gender identity", re.compile(r"\b(?:gay|lesbian|bisexual|transgender|queer|sexual orientation|"
                                                r"non-binary|nonbinary)\b", re.I)),
    ("political views", re.compile(r"\b(?:vote[sd]? for|political (?:party|views)|democrat|republican|"
                                   r"conservative|liberal|socialist)\b", re.I)),
    ("financial details", re.compile(r"\b(?:salary|income|debt|loan|mortgage|net worth|credit score|bank balance)\b", re.I)),
    ("legal status", re.compile(r"\b(?:criminal record|arrest\w*|convicted|immigration status|visa status|undocumented|"
                                r"lawsuit)\b", re.I)),
]
PERSONAL_PATTERNS: list[tuple[str, str, re.Pattern]] = [
    ("email address", "[email]", re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")),
    ("phone number", "[phone]", re.compile(r"(?<!\w)\+?\d[\d ()-]{7,}\d\b")),
    ("street address", "[address]", re.compile(
        r"\b\d{1,5}\s+(?:[A-Z][a-z]+\s){1,3}(?:Street|St|Avenue|Ave|Road|Rd|Lane|Ln|Boulevard|Blvd|Drive|Dr)\b")),
]


def _has_card(text: str) -> bool:
    for m in _CARD.finditer(text):
        digits = re.sub(r"\D", "", m.group())
        if 13 <= len(digits) <= 19 and _luhn(digits):
            return True
    return False


def classify(text: str) -> Sensitivity:
    s = Sensitivity()
    if _has_card(text):
        s.raise_to("secret", "payment card number")
    for reason, pat in SECRET_PATTERNS:
        if pat.search(text):
            s.raise_to("secret", reason)
    for reason, pat in SENSITIVE_TERMS:
        if pat.search(text):
            s.raise_to("sensitive", reason)
    for reason, _, pat in PERSONAL_PATTERNS:
        if pat.search(text):
            s.raise_to("personal", reason)
    return s


def redact(text: str) -> str:
    """Mask secrets and contact details (used for source excerpts and exports of excerpts)."""
    out = _CARD.sub(lambda m: "[card]" if _luhn(re.sub(r"\D", "", m.group())) and
                    13 <= len(re.sub(r"\D", "", m.group())) <= 19 else m.group(), text)
    for _, pat in SECRET_PATTERNS:
        out = pat.sub("[secret]", out)
    for _, mask, pat in PERSONAL_PATTERNS:
        out = pat.sub(mask, out)
    return out


def admit(text: str, settings: PrivacySettings, source: str) -> Admission:
    """Decide whether a memory may be stored, and whether it must wait for approval."""
    sens = classify(text)
    why = ", ".join(sens.reasons)
    if sens.level == "secret":
        return Admission(False, "secret", reason=f"Not stored: looks like it contains a {why}. "
                                                    "Secrets are never kept in memory.")
    if source in USER_SOURCES:
        return Admission(True, sens.level)
    if not settings.memory_enabled or not settings.auto_extract:
        return Admission(False, sens.level, reason="Automatic memory is turned off.")
    if sens.level == "sensitive":
        if not settings.allow_sensitive:
            return Admission(False, "sensitive", reason=f"Not proposed: sensitive ({why}).")
        return Admission(True, "sensitive", force_pending=True)
    return Admission(True, sens.level, force_pending=settings.require_approval)
