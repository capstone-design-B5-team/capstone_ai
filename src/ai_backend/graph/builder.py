"""LangGraph StateGraph assembly."""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from ai_backend.graph.nodes.aggregate import aggregate_node
from ai_backend.graph.nodes.fact_check import fact_check_node
from ai_backend.graph.nodes.numeric_check import numeric_check_node
from ai_backend.graph.nodes.preprocess import preprocess_node
from ai_backend.graph.nodes.recency_check import recency_check_node
from ai_backend.graph.nodes.source_check import source_check_node
from ai_backend.graph.state import GraphState


def build_graph() -> CompiledStateGraph[GraphState, None, GraphState, GraphState]:
    """Build the full document verification graph.

    Flow:
        START -> preprocess
        preprocess -> fact/source/recency/numeric checks in parallel
        all check nodes -> aggregate
        aggregate -> END
    """
    graph = StateGraph(GraphState)

    graph.add_node("preprocess", preprocess_node)
    graph.add_node("fact_check", fact_check_node)
    graph.add_node("source_check", source_check_node)
    graph.add_node("recency_check", recency_check_node)
    graph.add_node("numeric_check", numeric_check_node)
    graph.add_node("aggregate", aggregate_node)

    graph.add_edge(START, "preprocess")
    graph.add_edge("preprocess", "fact_check")
    graph.add_edge("preprocess", "source_check")
    graph.add_edge("preprocess", "recency_check")
    graph.add_edge("preprocess", "numeric_check")
    graph.add_edge(
        ["fact_check", "source_check", "recency_check", "numeric_check"],
        "aggregate",
    )
    graph.add_edge("aggregate", END)

    return graph.compile()


verification_graph = build_graph()
"""Compiled verification graph reusable by API routes and tests."""
