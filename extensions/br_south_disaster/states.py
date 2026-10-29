"""The states of the South region of Brazil.

To add a state, add one line to STATES. CEMADEN gives the same data for all states.
"""

from __future__ import annotations

STATES = {
    "RS": "Rio Grande do Sul",
    "SC": "Santa Catarina",
    "PR": "Paraná",
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
