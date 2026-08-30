"""Thin re-export of the approved §6 unified turn interface.

The interface is implemented in `agent.response_pipeline` (the extracted,
deterministic critical path). This module exists so the §6 import site
`from agent.run_turn import run_turn, TurnContext` works exactly as specified —
no logic lives here.
"""

from agent.response_pipeline import TurnContext, run_turn

__all__ = ["run_turn", "TurnContext"]
