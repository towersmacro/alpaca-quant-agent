"""
TradingGraph: Orchestrates the multi-agent trading system using LangChain and LangGraph.
Merges the previous trading_graph.py (LLM setup) and graph_setup.py (graph construction).
"""

import os

from langchain_anthropic import ChatAnthropic
from langchain_core.language_models import BaseChatModel
from langchain_openai import ChatOpenAI
from langchain_qwq import ChatQwen
from langgraph.graph import END, START, StateGraph

from .default_config import DEFAULT_CONFIG
from .state import IndicatorAgentState
from .utils.graph_util import TechnicalTools
from .agents.indicator import create_indicator_agent
from .agents.pattern import create_pattern_agent
from .agents.trend import create_trend_agent
from .agents.decision import create_final_trade_decider


class TradingGraph:
    """
    Main orchestrator for the multi-agent trading system.
    Sets up LLMs, toolkit, agent nodes, and compiles the LangGraph.
    """

    def __init__(self, config=None):
        self.config = config if config is not None else DEFAULT_CONFIG.copy()

        self.agent_llm = self._create_llm(
            provider=self.config.get("agent_llm_provider", "openai"),
            model=self.config.get("agent_llm_model", "gpt-4o-mini"),
            temperature=self.config.get("agent_llm_temperature", 0.1),
        )
        self.graph_llm = self._create_llm(
            provider=self.config.get("graph_llm_provider", "openai"),
            model=self.config.get("graph_llm_model", "gpt-4o"),
            temperature=self.config.get("graph_llm_temperature", 0.1),
        )
        self.toolkit = TechnicalTools()

        self.graph = self._build_graph()

    def _get_api_key(self, provider: str = "openai") -> str:
        if provider == "openai":
            api_key = self.config.get("api_key") or os.environ.get("OPENAI_API_KEY")
            if not api_key:
                raise ValueError(
                    "OpenAI API key not found. Set OPENAI_API_KEY env var or config['api_key']."
                )
            if api_key in ("your-openai-api-key-here", ""):
                raise ValueError("Please replace the placeholder API key with your actual OpenAI API key.")
        elif provider == "anthropic":
            api_key = self.config.get("anthropic_api_key") or os.environ.get("ANTHROPIC_API_KEY")
            if not api_key:
                raise ValueError("Anthropic API key not found. Set ANTHROPIC_API_KEY env var.")
        elif provider == "qwen":
            api_key = self.config.get("qwen_api_key") or os.environ.get("DASHSCOPE_API_KEY")
            if not api_key:
                raise ValueError("Qwen API key not found. Set DASHSCOPE_API_KEY env var.")
        else:
            raise ValueError(f"Unsupported provider: {provider}")
        
        return api_key

    def _create_llm(self, provider: str, model: str, temperature: float) -> BaseChatModel:
        api_key = self._get_api_key(provider)
        
        if provider == "openai":
            return ChatOpenAI(model=model, temperature=temperature, api_key=api_key)
        elif provider == "anthropic":
            return ChatAnthropic(model=model, temperature=temperature, api_key=api_key)
        elif provider == "qwen":
            return ChatQwen(model=model, temperature=temperature, api_key=api_key, max_retries=4)
        else:
            raise ValueError(f"Unsupported provider: {provider}")

    def _build_graph(self):
        """Construct the LangGraph StateGraph with all agent nodes."""
        agent_nodes = {}
        all_agents = ["indicator", "pattern", "trend"]

        agent_nodes["indicator"] = create_indicator_agent(self.graph_llm, self.toolkit)
        agent_nodes["pattern"] = create_pattern_agent(self.agent_llm, self.graph_llm, self.toolkit)
        agent_nodes["trend"] = create_trend_agent(self.agent_llm, self.graph_llm, self.toolkit)

        decision_agent_node = create_final_trade_decider(self.graph_llm)

        graph = StateGraph(IndicatorAgentState)

        for agent_type, cur_node in agent_nodes.items():
            graph.add_node(f"{agent_type.capitalize()} Agent", cur_node)

        graph.add_node("Decision Maker", decision_agent_node)

        graph.add_edge(START, "Indicator Agent")

        for i, agent_type in enumerate(all_agents):
            current_agent = f"{agent_type.capitalize()} Agent"

            if i == len(all_agents) - 1:
                graph.add_edge(current_agent, "Decision Maker")
            else:
                next_agent = f"{all_agents[i + 1].capitalize()} Agent"
                graph.add_edge(current_agent, next_agent)

        graph.add_edge("Decision Maker", END)

        return graph.compile()

    def refresh_llms(self):
        """Refresh LLM instances and rebuild the graph."""
        self.agent_llm = self._create_llm(
            provider=self.config.get("agent_llm_provider", "openai"),
            model=self.config.get("agent_llm_model", "gpt-4o-mini"),
            temperature=self.config.get("agent_llm_temperature", 0.1),
        )
        self.graph_llm = self._create_llm(
            provider=self.config.get("graph_llm_provider", "openai"),
            model=self.config.get("graph_llm_model", "gpt-4o"),
            temperature=self.config.get("graph_llm_temperature", 0.1),
        )
        self.graph = self._build_graph()

    def update_api_key(self, api_key: str, provider: str = "openai"):
        """Update API key and refresh LLMs."""
        if provider == "openai":
            self.config["api_key"] = api_key
            os.environ["OPENAI_API_KEY"] = api_key
        elif provider == "anthropic":
            self.config["anthropic_api_key"] = api_key
            os.environ["ANTHROPIC_API_KEY"] = api_key
        elif provider == "qwen":
            self.config["qwen_api_key"] = api_key
            os.environ["DASHSCOPE_API_KEY"] = api_key
        else:
            raise ValueError(f"Unsupported provider: {provider}")
        
        self.refresh_llms()
