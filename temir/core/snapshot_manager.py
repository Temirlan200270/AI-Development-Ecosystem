"""
Snapshot Manager for Temir.

This module provides functionality for creating, restoring, and managing
snapshots of the project's state.
"""

import logging
import shutil
from pathlib import Path
from typing import List, Optional

logger = logging.getLogger(__name__)


class SnapshotManager:
    """Manages snapshots of the project's working directory."""

    def __init__(self, project_dir: str, snapshots_dir: Optional[str] = None):
        """
        Initializes the SnapshotManager.

        Args:
            project_dir: The path to the project directory to be snapshotted.
            snapshots_dir: The directory where snapshots will be stored.
                           Defaults to a '.snapshots' directory inside the project_dir.
        """
        self.project_dir = Path(project_dir).resolve()
        if snapshots_dir:
            self.snapshots_dir = Path(snapshots_dir).resolve()
        else:
            self.snapshots_dir = self.project_dir / ".snapshots"

        self.snapshots_dir.mkdir(parents=True, exist_ok=True)
        logger.info(f"SnapshotManager initialized for project '{self.project_dir}'. Snapshots will be stored in '{self.snapshots_dir}'.")

    def create_snapshot(self, snapshot_name: str) -> bool:
        """
        Creates a snapshot of the project directory.

        Args:
            snapshot_name: A unique name for the snapshot.

        Returns:
            True if the snapshot was created successfully, False otherwise.
        """
        snapshot_path = self.snapshots_dir / snapshot_name
        if snapshot_path.exists():
            logger.warning(f"Snapshot '{snapshot_name}' already exists. Overwriting.")
            shutil.rmtree(snapshot_path)

        logger.info(f"Creating snapshot '{snapshot_name}'...")
        try:
            shutil.copytree(
                self.project_dir,
                snapshot_path,
                ignore=shutil.ignore_patterns(str(self.snapshots_dir.name)),
            )
            logger.info(f"Snapshot '{snapshot_name}' created successfully.")
            return True
        except Exception as e:
            logger.exception(f"Failed to create snapshot '{snapshot_name}': {e}")
            return False

    def restore_snapshot(self, snapshot_name: str) -> bool:
        """
        Restores the project directory from a snapshot.

        This will delete the current contents of the project directory and replace
        them with the contents of the snapshot.

        Args:
            snapshot_name: The name of the snapshot to restore.

        Returns:
            True if the snapshot was restored successfully, False otherwise.
        """
        snapshot_path = self.snapshots_dir / snapshot_name
        if not snapshot_path.exists():
            logger.error(f"Snapshot '{snapshot_name}' not found.")
            return False

        logger.info(f"Restoring snapshot '{snapshot_name}'...")
        try:
            # First, clear the current project directory (except for the snapshots dir)
            for item in self.project_dir.iterdir():
                if item.name != self.snapshots_dir.name:
                    if item.is_dir():
                        shutil.rmtree(item)
                    else:
                        item.unlink()

            # Then, copy the snapshot contents back
            shutil.copytree(
                snapshot_path,
                self.project_dir,
                dirs_exist_ok=True,
            )
            logger.info(f"Snapshot '{snapshot_name}' restored successfully.")
            return True
        except Exception as e:
            logger.exception(f"Failed to restore snapshot '{snapshot_name}': {e}")
            return False

    def delete_snapshot(self, snapshot_name: str) -> bool:
        """
        Deletes a snapshot.

        Args:
            snapshot_name: The name of the snapshot to delete.

        Returns:
            True if the snapshot was deleted successfully, False otherwise.
        """
        snapshot_path = self.snapshots_dir / snapshot_name
        if not snapshot_path.exists():
            logger.warning(f"Snapshot '{snapshot_name}' not found for deletion.")
            return True  # It's already gone, so it's a success in a way

        logger.info(f"Deleting snapshot '{snapshot_name}'...")
        try:
            shutil.rmtree(snapshot_path)
            logger.info(f"Snapshot '{snapshot_name}' deleted successfully.")
            return True
        except Exception as e:
            logger.exception(f"Failed to delete snapshot '{snapshot_name}': {e}")
            return False

    def list_snapshots(self) -> List[str]:
        """
        Lists the names of all available snapshots.

        Returns:
            A list of snapshot names.
        """
        if not self.snapshots_dir.exists():
            return []
        return [item.name for item in self.snapshots_dir.iterdir() if item.is_dir()]

    def cleanup_snapshots(self):
        """Removes the entire snapshots directory."""
        logger.info(f"Cleaning up all snapshots in '{self.snapshots_dir}'...")
        try:
            shutil.rmtree(self.snapshots_dir)
            logger.info("All snapshots cleaned up successfully.")
        except Exception as e:
            logger.exception(f"Failed to clean up snapshots: {e}")

