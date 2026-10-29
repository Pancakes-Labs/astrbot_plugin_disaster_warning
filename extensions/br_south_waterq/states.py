"""The states of the South region of Brazil and their providers.

To add a state, add a provider module in providers/ and one line to STATES.
"""

from __future__ import annotations

from .providers import pr_iat, rs_fepam, sc_ima

STATES = {
    "RS": rs_fepam,
    "SC": sc_ima,
    "PR": pr_iat,
}


def selected_states(config: dict) -> tuple[str, ...]:
    """Return the state codes in config["states"], in the order of STATES.

    If the setting is empty, not set or not a list, return all states.
    Ignore unknown codes.
    """
    wanted = (config or {}).get("states")
    if not wanted or not isinstance(wanted, (list, tuple)):
        return tuple(STATES)
    codes = {str(code).strip().upper() for code in wanted}
    return tuple(uf for uf in STATES if uf in codes)
