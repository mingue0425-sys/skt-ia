"""Hash-bound external assertions. File integrity is not upstream authenticity."""
import json
from pathlib import Path
from ..pit.time import timestamp, assumed_available
from ..storage import sha256


def load_assertion(reference, *, kind, domain):
    if reference is None: return None
    path = Path(reference["path"])
    if not path.is_file() or sha256(path.read_bytes()) != reference["sha256"]:
        raise ValueError("assertion_file_missing_or_hash_mismatch")
    value = json.loads(path.read_text())
    if value.get("kind") != kind or value.get("input_domain") != domain:
        raise ValueError("assertion_kind_or_domain_mismatch")
    if value.get("verified") is not True or not value.get("verification_method"):
        raise ValueError("assertion_not_verified")
    return value | {"evidence": {"path": str(path.resolve()), "sha256": reference["sha256"]}}


def assertion_availability(value, *, policy, cutoff, rule=None):
    if not value: return None, "evidence_missing"
    try:
        if policy == "observed":
            received = timestamp(value.get("received_at")); usable = timestamp(value.get("usable_at"))
            if not received or not usable: return None, "assertion_actual_receipt_unproven"
            available = max(received, usable)
        elif policy == "historical_verified":
            if not value.get("version_content_verified"): return None, "assertion_historical_version_unproven"
            available = timestamp(value.get("published_at"))
            if available is None: return None, "assertion_publication_unknown"
            if value.get("revised_at"): available = max(available, timestamp(value["revised_at"]))
        else:
            if value.get("research_assumed_available_at") and value.get("research_assumption"):
                available = timestamp(value["research_assumed_available_at"])
                return (available, "assertion_after_cutoff" if available > cutoff else None)
            available = timestamp(value.get("published_at"))
            if not available:
                if not value.get("publication_date") or value.get("publication_timezone") != rule["timezone"]:
                    return None, "assertion_assumption_input_missing"
                available = assumed_available(value["publication_date"], rule)
            if value.get("revised_at"): available = max(available, timestamp(value["revised_at"]))
        if available > cutoff: return None, "assertion_after_cutoff"
        return available, None
    except (KeyError, TypeError, ValueError):
        return None, "assertion_time_invalid"
