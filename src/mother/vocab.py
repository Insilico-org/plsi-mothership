"""Every enum of the Mother toolbelt, one name per concept (WIRE.md terms)."""

from __future__ import annotations

from enum import StrEnum


class Kind(StrEnum):
    """Message kinds (WIRE §3)."""
    CONTRIBUTION = "plsi_contribution"
    WITHDRAWAL = "plsi_withdrawal"
    RESEARCH_NOTE = "plsi_research_note"
    REGISTRY_UPDATE = "plsi_registry_update"
    PREREGISTRATION = "plsi_preregistration"
    FINDING = "plsi_finding"
    FEEDBACK = "plsi_feedback"


class Sender(StrEnum):
    """Who sent the message: the relay's parked record (which token), or `email` for a research note or feedback."""
    CONTRIBUTOR = "contributor"
    PROFESSIONAL = "professional"
    EMAIL = "email"


class Role(StrEnum):
    PHYSICIAN = "physician"
    RESEARCHER = "researcher"


class Scope(StrEnum):
    RELEASES_READ = "releases:read"
    FINDINGS_READ = "findings:read"
    FINDINGS_SUBMIT = "findings:submit"


class ContributionKind(StrEnum):
    """Consent kinds, which are also the package section names."""
    STATES = "states"
    LIFE_EVENTS = "life_events"
    SCORES = "scores"


class CodeSystem(StrEnum):
    """`code.system` of a contribution (WIRE §3)."""
    LOINC = "loinc"
    RXNORM = "rxnorm"
    SNOMED = "snomed"
    ICD10CM = "icd10cm"
    CPG = "cpg"
    FREE = "free"


class Dmarc(StrEnum):
    """A research email's DMARC result, read from its first Authentication-Results header (WIRE §7)."""
    PASS = "pass"
    FAIL = "fail"
    NONE = "none"


class Request(StrEnum):
    """Withdrawal requests."""
    STOP = "stop"
    DELETE_ALL = "delete_all"


class ContributorStatus(StrEnum):
    ACTIVE = "active"
    WITHDRAWN = "withdrawn"
    DELETED = "deleted"  # only in the intake reply: a deleted contributor leaves the index


class ValueStatus(StrEnum):
    """A coordinate's status in a contributed state."""
    PRESENT = "present"
    STALE = "stale"
    MISSING = "missing"
    ERROR = "error"


class ClinicalStatus(StrEnum):
    """Whether a coded item is current."""
    ACTIVE = "active"
    STOPPED = "stopped"
    RESOLVED = "resolved"
    UNKNOWN = "unknown"


class NoteHallmark(StrEnum):
    """The twelve hallmarks of aging (Lopez-Otin et al., Cell 2023, PMID 36599349), as PLSI spells them."""
    GENOMIC_INSTABILITY = "genomic instability"
    TELOMERE_ATTRITION = "telomere attrition"
    EPIGENETIC_ALTERATIONS = "epigenetic alterations"
    LOSS_OF_PROTEOSTASIS = "loss of proteostasis"
    DISABLED_MACROAUTOPHAGY = "disabled macroautophagy"
    DEREGULATED_NUTRIENT_SENSING = "deregulated nutrient-sensing"
    MITOCHONDRIAL_DYSFUNCTION = "mitochondrial dysfunction"
    CELLULAR_SENESCENCE = "cellular senescence"
    STEM_CELL_EXHAUSTION = "stem cell exhaustion"
    ALTERED_INTERCELLULAR_COMMUNICATION = "altered intercellular communication"
    CHRONIC_INFLAMMATION = "chronic inflammation"
    DYSBIOSIS = "dysbiosis"


class NoteStatus(StrEnum):
    """A stored registry update or research note before a steward decides on it."""
    PENDING_REVIEW = "pending_review"
    REVIEWED = "reviewed"  # research notes: the Mother reviewed it, a steward has not decided yet


class Sex(StrEnum):
    """A contribution's `subject.sex`; analysis specs group by F and M only (WIRE §5)."""
    F = "F"
    M = "M"
    X = "X"
    UNKNOWN = "unknown"


class Ethnicity(StrEnum):
    """A contribution's optional `subject.ethnicity`; a release cell without one is `unspecified` (WIRE §5), which a
    contribution never sends."""
    EUROPEAN = "european"
    AFRICAN = "african"
    MIDDLE_EASTERN = "middle_eastern"
    SOUTH_ASIAN = "south_asian"
    EAST_ASIAN = "east_asian"
    SOUTHEAST_ASIAN = "southeast_asian"
    LATIN_AMERICAN = "latin_american"
    OTHER = "other"
    UNSPECIFIED = "unspecified"


class Op(StrEnum):
    """Analysis spec operations (WIRE §5)."""
    COMPARE_GROUPS = "compare_groups"
    AGE_TREND = "age_trend"


class Direction(StrEnum):
    POSITIVE = "positive"
    NEGATIVE = "negative"
    TWO_SIDED = "two_sided"


class Entry(StrEnum):
    """Findings ledger entry types (WIRE §6)."""
    PREREGISTRATION = "preregistration"
    FINDING = "finding"
    CHECK = "check"
    REVIEW = "review"
    DECISION = "decision"


class Verdict(StrEnum):
    """An independent reviewer's verdict on a finding or a research note."""
    OK = "ok"
    REVISE = "revise"
    REJECT = "reject"


class Decision(StrEnum):
    """A steward's decision on a finding, a registry update or a research note."""
    ACCEPTED = "accepted"
    REJECTED = "rejected"


DECIDED = frozenset(Decision)


class ResearchEntry(StrEnum):
    """`research/log.jsonl` entry types (WIRE §6, §7)."""
    REVIEW = "review"
    DECISION = "decision"
    REPLIED = "replied"


class FindingStatus(StrEnum):
    """Derived from ledger entries, never stored."""
    PREREGISTERED = "preregistered"
    SUBMITTED = "submitted"
    CHECKED = "checked"
    CHECK_FAILED = "check_failed"
    REVIEWED = "reviewed"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class Publication(StrEnum):
    """What `/admin/publish` accepts."""
    CATALOG = "catalog"
    RELEASE = "release"
    FINDINGS = "findings"


class FeedbackStage(StrEnum):
    """Setup stages of plsi.md, then use (WIRE §8)."""
    INSTALL = "install"
    TEAM = "team"
    CHOICES = "choices"
    INTERVIEW = "interview"
    SPECIALISTS = "specialists"
    CONNECTORS = "connectors"
    INSTANCE = "instance"
    ROUTINES = "routines"
    MOTHERSHIP = "mothership"
    WELCOME = "welcome"
    RECORDS = "records"
    PLANS = "plans"
    RESEARCH = "research"
    OTHER = "other"


class Severity(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class FeedbackRole(StrEnum):
    INDIVIDUAL = "individual"
    PHYSICIAN = "physician"
    RESEARCHER = "researcher"
