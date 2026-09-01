"""LangGraph checkout graph — compile when LangGraph checkpointer is configured.

Production checkout currently runs via services/session.py (CheckoutOrchestrator).
Wire AsyncPostgresSaver here when migrating to full LangGraph interrupt/resume.
"""


# from langgraph.graph import StateGraph
# builder = StateGraph(KeenPayState)
# ... add nodes from graph/nodes/
# graph = builder.compile(checkpointer=checkpointer, interrupt_before=["await_user_confirmation"])


def get_compiled_graph():
    raise NotImplementedError("Use SessionService for MVP checkout pipeline")
