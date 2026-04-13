"""
Patch Manager for Temir.

This module provides a robust system for creating and applying patches,
including fuzzy patching capabilities.
"""

import logging
from typing import List, Tuple

import diff_match_patch as dmp_module

logger = logging.getLogger(__name__)


class PatchManager:
    """Manages the creation and application of patches."""

    def __init__(self, fuzzy_match_threshold: float = 0.85):
        """Initializes the PatchManager."""
        self.dmp = dmp_module.diff_match_patch()
        self.dmp.Match_Threshold = fuzzy_match_threshold
        logger.info(
            f"PatchManager initialized with fuzzy_match_threshold={fuzzy_match_threshold}"
        )

    def create_patch(self, old_content: str, new_content: str) -> str:
        """
        Creates a patch in text format from two text strings.

        Args:
            old_content: The original text.
            new_content: The new text.

        Returns:
            A string representing the patch.
        """
        patches = self.dmp.patch_make(old_content, new_content)
        return self.dmp.patch_toText(patches)

    def apply_patch(
        self, patch_text: str, current_content: str
    ) -> Tuple[str, List[bool]]:
        """
        Applies a patch to a text string.

        This method attempts to apply the patch precisely. If that fails,
        it falls back to a fuzzy patch.

        Args:
            patch_text: The string representation of the patch.
            current_content: The text to which the patch should be applied.

        Returns:
            A tuple containing:
            - The patched text.
            - A list of booleans indicating the success of each patch chunk.
        """
        patches = self.dmp.patch_fromText(patch_text)
        if not patches:
            logger.error("Failed to parse patch text.")
            return current_content, [False] * len(patch_text.split("@@"))

        # First, try a precise patch
        new_content, results = self.dmp.patch_apply(patches, current_content)
        
        # Check if any part of the patch failed
        if not all(results):
            logger.warning(
                "Precise patch failed. Falling back to fuzzy patch. Results: %s", results
            )
            # If precise patch fails, try a fuzzy patch
            self.dmp.Match_Distance = 1000
            new_content, results = self.dmp.patch_apply(patches, current_content)

        if not all(results):
            logger.error(
                "Fuzzy patch also failed. Results: %s", results
            )

        return new_content, results
