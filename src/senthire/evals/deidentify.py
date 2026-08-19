"""Turn real CVs into corpus fixtures that carry no personal data.

Real CVs are the best evaluation material we will ever have and the worst thing
to keep lying around: KVKK/GDPR personal data cannot live in a git repository.
This module strips identity from an extracted profile while preserving
everything screening actually reasons about — titles, dates, companies,
skills, languages, education, geography.

The replacement is *deterministic under a secret salt*: the same person always
maps to the same pseudonym (so duplicate CVs still deduplicate, and twins stay
comparable), but nobody can reverse the map without the salt, which never
enters the repository.

The identity class (feminine/masculine/unknown name coding) is recorded as
corpus metadata for one purpose only: building counterfactual fairness twins,
where the *same* CV under a differently-coded name must score identically. It
is never an input to extraction, screening, or scoring.
"""

import hashlib
import re
import unicodedata
from dataclasses import dataclass, field

# Deliberately small, obviously-fictional pools. They exist to make the
# fairness twin meaningful (a name a Turkish reader codes one way vs the
# other), not to represent any real distribution of names.
FEMININE_NAMES = [
    "Ayşe", "Elif", "Zeynep", "Fatma", "Emine", "Şule", "Selin", "Ebru",
    "Pınar", "Gamze", "Büşra", "Merve", "Nalan", "Sevgi", "Aslı",
]
MASCULINE_NAMES = [
    "Ahmet", "Mehmet", "Mustafa", "Ali", "Hüseyin", "Emre", "Burak", "Kerem",
    "Onur", "Serkan", "Cem", "Yusuf", "Murat", "Tolga", "Volkan",
]
SURNAMES = [
    "Yılmaz", "Kaya", "Demir", "Şahin", "Çelik", "Yıldız", "Yıldırım",
    "Öztürk", "Aydın", "Özdemir", "Arslan", "Doğan", "Kılıç", "Çetin", "Koç",
]

IdentityClass = str  # "feminine" | "masculine" | "unknown"

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_PHONE_RE = re.compile(r"(?:\+90[\s-]?)?0?[\s-]?5\d{2}[\s-]?\d{3}[\s-]?\d{2}[\s-]?\d{2}")
# Turkish national ID: 11 digits. Never useful for screening, always sensitive.
_TCKN_RE = re.compile(r"\b[1-9]\d{10}\b")
_DOB_RE = re.compile(r"\b(0?[1-9]|[12]\d|3[01])[./](0?[1-9]|1[0-2])[./](19|20)\d{2}\b")


def _fold(value: str) -> str:
    """Case-fold for matching, with the Turkish dotted/dotless I handled."""
    return value.replace("İ", "i").replace("I", "ı").lower()


def _index(salt: str, value: str, modulo: int) -> int:
    digest = hashlib.blake2b(f"{salt}|{value}".encode(), digest_size=8).digest()
    return int.from_bytes(digest, "big") % modulo


def classify_name(full_name: str | None) -> IdentityClass:
    """Best-effort coding of the given name. 'unknown' is a normal answer."""
    if not full_name:
        return "unknown"
    given = _fold(full_name.strip().split()[0])
    if any(_fold(n) == given for n in FEMININE_NAMES):
        return "feminine"
    if any(_fold(n) == given for n in MASCULINE_NAMES):
        return "masculine"
    return "unknown"


def pseudonym(salt: str, seed: str, identity_class: IdentityClass) -> str:
    """A stable fake name for `seed`, coded like `identity_class` when known."""
    pool = MASCULINE_NAMES if identity_class == "masculine" else FEMININE_NAMES
    given = pool[_index(salt, seed + "|given", len(pool))]
    family = SURNAMES[_index(salt, seed + "|family", len(SURNAMES))]
    return f"{given} {family}"


def _fake_email(name: str, salt: str, seed: str) -> str:
    slug = re.sub(r"[^a-z]+", ".", unicodedata.normalize("NFKD", _fold(name))
                  .encode("ascii", "ignore").decode()).strip(".")
    return f"{slug}{_index(salt, seed + '|mail', 90) + 10}@ornek-eposta.tr"


def _fake_phone(salt: str, seed: str) -> str:
    tail = _index(salt, seed + "|phone", 10**7)
    return f"+90 5{tail // 10**5 % 100:02d} {tail // 100 % 1000:03d} {tail % 100:02d} 00"


@dataclass
class Deidentified:
    profile: dict
    identity_class: IdentityClass
    # What was replaced, for the caller to scrub any text it holds. Never
    # persisted with the corpus case — it is the re-identification key.
    replacements: dict[str, str] = field(default_factory=dict)


def deidentify_profile(profile: dict, *, salt: str, seed: str) -> Deidentified:
    """Replace identity fields in an ExtractedProfile-shaped dict.

    `seed` should be a stable per-document value (the file's sha256) so the
    same CV always yields the same pseudonym.
    """
    out = {**profile}
    identity = dict(out.get("identity") or {})
    original_name = identity.get("full_name")
    identity_class = classify_name(original_name)

    # An unclassifiable original still gets a clearly coded pseudonym: the
    # corpus fixture is what fairness twins are built from, so it must have a
    # coding to flip. Which one is chosen is arbitrary, hence hash-derived.
    if identity_class == "unknown":
        identity_class = "feminine" if _index(salt, seed + "|class", 2) else "masculine"
    fake_name = pseudonym(salt, seed, identity_class)
    replacements: dict[str, str] = {}
    if original_name:
        replacements[original_name] = fake_name
    identity["full_name"] = fake_name

    emails = identity.get("emails") or []
    fake_emails = [_fake_email(fake_name, salt, f"{seed}|{i}") for i, _ in enumerate(emails)]
    replacements.update(dict(zip(emails, fake_emails, strict=True)))
    identity["emails"] = fake_emails

    phones = identity.get("phones") or []
    fake_phones = [_fake_phone(salt, f"{seed}|{i}") for i, _ in enumerate(phones)]
    replacements.update(dict(zip(phones, fake_phones, strict=True)))
    identity["phones"] = fake_phones

    # Profile/portfolio URLs identify as surely as a name does, and no
    # requirement is evaluated from them.
    identity["links"] = []
    out["identity"] = identity
    return Deidentified(profile=out, identity_class=identity_class, replacements=replacements)


def scrub_text(text: str, replacements: dict[str, str]) -> str:
    """Apply the profile's replacements to raw CV text, then sweep for
    anything the extractor missed (addresses, ID numbers, birth dates)."""
    out = text
    for original, replacement in sorted(replacements.items(), key=lambda kv: -len(kv[0])):
        if original:
            out = out.replace(original, replacement)
    out = _EMAIL_RE.sub("eposta@ornek-eposta.tr", out)
    out = _PHONE_RE.sub("+90 5XX XXX XX XX", out)
    out = _TCKN_RE.sub("[kimlik-no]", out)
    out = _DOB_RE.sub("[tarih]", out)
    return out
