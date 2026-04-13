"""
Cost Calculator for Temir.

This module provides a utility for calculating the cost of LLM operations
based on token usage and model pricing.
"""

import logging
from typing import Dict

logger = logging.getLogger(__name__)

class CostCalculator:
    """Calculates the cost of LLM operations."""

    # Placeholder pricing in USD per 1,000 tokens
    MODEL_PRICING: Dict[str, Dict[str, float]] = {
        "gemini-2.5-pro": {
            "input": 0.0025 / 1000,  # Example price
            "output": 0.0075 / 1000, # Example price
        },
        "gemini-3.1-pro-preview": {
            "input": 0.0025 / 1000,
            "output": 0.0075 / 1000,
        },
        "gemini-pro": {
            "input": 0.000125, # Per 1K tokens
            "output": 0.000375, # Per 1K tokens
        },
        "mercury-2": {
            "input": 0.0002,  # Placeholder price
            "output": 0.0006,  # Placeholder price
        },
        "mercury": {  # Alias
            "input": 0.0002,
            "output": 0.0006,
        },
        "mercury-coder": {  # Legacy alias for backward compatibility
            "input": 0.0002,
            "output": 0.0006,
        },
        "default": {
            "input": 0.001,
            "output": 0.003,
        },
    }

    def calculate_cost(
        self,
        model_name: str,
        input_tokens: int,
        output_tokens: int,
    ) -> float:
        """
        Calculates the cost of a single LLM operation.

        Args:
            model_name: The name of the model used.
            input_tokens: The number of input (prompt) tokens.
            output_tokens: The number of output (completion) tokens.

        Returns:
            The calculated cost in USD.
        """
        if model_name not in self.MODEL_PRICING:
            logger.warning(
                f"Model '{model_name}' not found in pricing list. Using default pricing."
            )
            pricing = self.MODEL_PRICING["default"]
        else:
            pricing = self.MODEL_PRICING[model_name]

        input_cost = (input_tokens / 1000) * pricing["input"]
        output_cost = (output_tokens / 1000) * pricing["output"]
        total_cost = input_cost + output_cost

        logger.info(
            f"Cost calculated for model '{model_name}': "
            f"Input: {input_tokens} tokens, Output: {output_tokens} tokens, Cost: ${total_cost:.6f}"
        )

        return total_cost
