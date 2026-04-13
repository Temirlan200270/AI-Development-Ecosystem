"""
Validation Pipeline for Temir Sandbox.

This module provides a structured way to run validation tasks like
linters and tests within a given sandbox environment.
"""

import logging
from typing import Any, Dict

# Forward-declaration for type hinting to avoid circular import
if False:
    pass

logger = logging.getLogger(__name__)


class ValidationPipeline:
    """Encapsulates logic for running validation steps."""

    def __init__(self, sandbox: "Any"):
        """
        Initializes the pipeline with a sandbox instance.

        Args:
            sandbox: An instance of DockerManager or LocalUnsafeSandbox.
        """
        if not hasattr(sandbox, "execute_command"):
            error_msg = "Sandbox instance must have an 'execute_command' method."
            raise TypeError(error_msg)
        self.sandbox = sandbox
        logger.info(
            f"ValidationPipeline initialized with sandbox: {type(sandbox).__name__}",
        )

    def run_linter(self, path: str = ".") -> Dict[str, Any]:
        """
        Runs the ruff linter on the specified path within the sandbox.
        """
        logger.info(f"Running linter on path: {path}")

        # First, ensure ruff is installed
        install_cmd = "pip install ruff --quiet"
        install_result = self.sandbox.execute_command(install_cmd)
        if install_result.get("exit_code", 1) != 0:
            logger.error("Failed to install ruff.")
            return {
                "success": False,
                "error": "Linter setup failed",
                "details": "Could not install ruff.",
                "stdout": install_result.get("stdout"),
                "stderr": install_result.get("stderr"),
            }

        # Then, run the linter
        lint_cmd = f"ruff check {path}"
        logger.info(f"Executing lint command: {lint_cmd}")
        result = self.sandbox.execute_command(lint_cmd)

        success = result.get("exit_code", 1) == 0
        return {
            "success": success,
            "stdout": result.get("stdout"),
            "stderr": result.get("stderr"),
            "exit_code": result.get("exit_code"),
            "details": "Linter check completed."
            if success
            else "Linter check found issues.",
        }

    def run_tests(self, path: str = "tests") -> Dict[str, Any]:
        """
        Runs pytest on the specified path within the sandbox.
        It ensures pytest and httpx (a common dependency for testing APIs) are installed.
        """
        logger.info(f"Running tests on path: {path}")

        # First, ensure pytest and common dependencies are installed
        install_cmd = "pip install pytest httpx --quiet"
        install_result = self.sandbox.execute_command(install_cmd)
        if install_result.get("exit_code", 1) != 0:
            logger.error("Failed to install pytest/httpx.")
            return {
                "success": False,
                "error": "Test setup failed",
                "details": "Could not install pytest and/or httpx.",
                "stdout": install_result.get("stdout"),
                "stderr": install_result.get("stderr"),
            }

        # Use 'python -m pytest' to avoid PATH issues and ensure modules are found
        test_cmd = f"python -m pytest {path}"
        logger.info(f"Executing test command: {test_cmd}")
        result = self.sandbox.execute_command(test_cmd)

        success = result.get("exit_code", 1) == 0
        return {
            "success": success,
            "stdout": result.get("stdout"),
            "stderr": result.get("stderr"),
            "exit_code": result.get("exit_code"),
            "details": "All tests passed." if success else "Some tests failed.",
        }
