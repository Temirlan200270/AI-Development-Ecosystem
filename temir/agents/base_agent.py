"""
Base Agent for the Temir AI Development Ecosystem.
"""

import abc
from typing import Any, Dict, Optional

from temir.core.models import AIRole


class BaseAgent(abc.ABC):
    """
    Abstract Base Class for all AI agents in the Temir ecosystem.

    This class defines the essential interface that all agents must implement,
    ensuring that the Orchestrator can interact with any agent type in a
    consistent manner.
    """

    @abc.abstractmethod
    def execute_task(
        self,
        task_description: str,
        role: AIRole,
        context: Optional[Dict[str, Any]] = None,
        max_retries: int = 3,
    ) -> Dict[str, Any]:
        """
        Executes a given task based on the provided role and context.

        This method must be implemented by all concrete agent classes.

        Args:
            task_description: The natural language description of the task.
            role: The AI role the agent should assume (e.g., CODER, TESTER).
            context: A dictionary containing any additional data the agent might
                     need, such as source code to review or file paths.
            max_retries: The maximum number of times to retry on failure.

        Returns:
            A dictionary containing the result of the execution. For tool-using
            agents, this is typically a JSON object with 'action' and 'args'.
            For text-generating agents, it might be a dictionary with 'output_text'.
            Must include a 'success': bool key.
        """
        raise NotImplementedError
