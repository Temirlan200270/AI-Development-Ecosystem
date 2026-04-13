"""
Specification Parser for Temir.

This module is responsible for loading, validating, and preparing the
execution plan from a spec.yaml file.
"""

import logging

import yaml

from temir.agents.universal_agent import UniversalAgent
from temir.core.models import AIRole, Specification, Task

logger = logging.getLogger(__name__)


class SpecParser:
    """Loads and prepares a specification."""

    def __init__(self, agent: UniversalAgent, config: dict):
        self.agent = agent
        self.config = config

    def load_from_path(self, spec_path: str) -> Specification:
        """
        Loads and validates the specification from a YAML file.
        """
        try:
            with open(spec_path, encoding="utf-8") as f:
                spec_data = yaml.safe_load(f.read())
                return Specification(**spec_data)
        except FileNotFoundError:
            logger.error(f"Specification file not found at: {spec_path}")
            raise
        except yaml.YAMLError as e:
            logger.error(f"Error parsing YAML in {spec_path}: {e}")
            raise ValueError(f"Invalid YAML format: {e}")
        except Exception as e:
            logger.error(
                f"Failed to load or validate specification from {spec_path}: {e}",
                exc_info=True,
            )
            raise ValueError(f"Failed to process specification: {e}")

    def prepare_execution_plan(
        self,
        specification: Specification,
        spec_path: str,
    ) -> list[Task]:
        """
        Ensures the specification has a valid execution plan.
        If the plan is empty and auto_plan is enabled, it uses the PLANNER agent.
        """
        if specification.execution_plan:
            return specification.execution_plan

        if not self.config.get("auto_plan"):
            message = (
                "The 'execution_plan' section in the specification is missing or empty. "
                "Add a list of tasks or enable auto-planning with the --auto-plan flag."
            )
            logger.warning(message)
            raise ValueError(message)

        logger.info("Execution plan is empty. Engaging PLANNER to generate a new plan.")

        plan_request = f"Generate an execution plan for the project described in the spec at '{spec_path}'. The plan should create a basic structure and at least one sample action based on the project's description."

        context = {"spec_path": spec_path}
        planner_response = self.agent.execute_task(
            plan_request,
            AIRole.PLANNER,
            context,
        )

        if not planner_response.get("success"):
            error_msg = "PLANNER agent failed to generate a plan."
            logger.error(error_msg)
            raise RuntimeError(error_msg)

        try:
            # The response from the agent might be a JSON string in 'output_text'
            # or a pre-parsed dict in 'action_json'.
            response_data = planner_response.get("action_json") or json.loads(
                planner_response.get("output_text", "{}"),
            )

            raw_plan = response_data.get("execution_plan")
            if not isinstance(raw_plan, list) or not raw_plan:
                raise ValueError(
                    "PLANNER returned an empty or invalid 'execution_plan' list.",
                )

            # Validate the generated plan by converting it to Task models
            validated_plan = [Task(**item) for item in raw_plan]
            logger.info(
                f"PLANNER successfully generated a plan with {len(validated_plan)} tasks.",
            )
            return validated_plan
        except Exception as e:
            logger.error(
                f"Failed to parse the plan from PLANNER's response: {e}",
                exc_info=True,
            )
            raise ValueError(f"PLANNER returned an invalid plan: {e}")
