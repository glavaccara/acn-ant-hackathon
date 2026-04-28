"""Base specialist: runs an agent loop with explicit context and its own tool set."""
import json
import logging
import os
from typing import Any

from anthropic import AnthropicBedrock

from agent.bank_store import BankStore
from agent.tools.executor import execute_tool

logger = logging.getLogger(__name__)

MODEL = os.getenv("CASH_COMPASS_HAIKU_MODEL", "us.anthropic.claude-haiku-4-5-20251001-v1:0")
MAX_ITERATIONS = 8


class BaseSpecialist:
    name: str = "base"
    system_prompt: str = ""
    tools: list[dict] = []
    max_iterations: int = MAX_ITERATIONS  # subclasses can override for chattier workflows
    max_tokens_per_call: int = 4096

    def __init__(self, store: BankStore):
        self.store = store
        self.client = AnthropicBedrock(
            aws_profile=os.getenv("AWS_PROFILE", "bootcamp"),
            aws_region=os.getenv("AWS_REGION", "us-east-1"),
        )

    def run(self, context: dict) -> dict:
        """
        Run the specialist with explicit context dict.
        Context is passed via the user message — NOT inherited from coordinator.
        Returns structured result dict.
        """
        user_message = (
            f"Context from coordinator:\n{json.dumps(context, indent=2)}\n\n"
            "Process the above context using your tools and return a structured JSON result."
        )
        messages = [{"role": "user", "content": user_message}]
        reasoning_chain = []

        for iteration in range(self.max_iterations):
            response = self.client.messages.create(
                model=MODEL,
                max_tokens=self.max_tokens_per_call,
                system=self.system_prompt,
                messages=messages,
                tools=self.tools,
            )

            reasoning_chain.append({
                "iteration": iteration,
                "stop_reason": response.stop_reason,
                "content_types": [b.type for b in response.content],
            })

            if response.stop_reason == "max_tokens":
                logger.error(f"{self.name}: hit max_tokens — failing loudly")
                return {
                    "specialist": self.name,
                    "error": "MAX_TOKENS_HIT",
                    "reasoning_chain": reasoning_chain,
                }

            if response.stop_reason == "end_turn":
                text = next((b.text for b in response.content if hasattr(b, "text")), "")
                return {
                    "specialist": self.name,
                    "result": self._parse_result(text),
                    "reasoning_chain": reasoning_chain,
                    "mutations": self.store.get_mutations(),
                }

            if response.stop_reason == "tool_use":
                tool_results = []
                for block in response.content:
                    if block.type == "tool_use":
                        tool_out = execute_tool(block.name, block.input, self.store)
                        logger.debug(f"{self.name} called {block.name}: {tool_out}")
                        reasoning_chain[-1].setdefault("tool_calls", []).append({
                            "tool": block.name,
                            "input": block.input,
                            "output": tool_out,
                        })
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": block.id,
                            "content": json.dumps(tool_out),
                        })
                messages.append({"role": "assistant", "content": response.content})
                messages.append({"role": "user", "content": tool_results})

        return {
            "specialist": self.name,
            "error": "MAX_ITERATIONS_EXCEEDED",
            "reasoning_chain": reasoning_chain,
        }

    def _parse_result(self, text: str) -> dict:
        """Try to parse JSON from the specialist's final response."""
        try:
            start = text.find("{")
            end = text.rfind("}") + 1
            if start >= 0 and end > start:
                return json.loads(text[start:end])
        except json.JSONDecodeError:
            pass
        return {"raw_text": text}
