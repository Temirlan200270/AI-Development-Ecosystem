"""
Vector Cache Manager for Temir.

This module provides a vector-based cache for finding semantically similar tasks.
It uses ChromaDB for storage and SentenceTransformers for embeddings.
"""

import logging
from pathlib import Path
from typing import List, Optional, Dict, Any

try:
    import chromadb
    from chromadb.utils import embedding_functions
except ImportError:
    chromadb = None
    embedding_functions = None

logger = logging.getLogger(__name__)


class VectorCacheManager:
    """Manages a vector cache for finding similar tasks."""

    def __init__(
        self,
        collection_name: str = "temir_task_cache",
        db_path: Optional[str] = None,
        embedding_model_name: str = "all-MiniLM-L6-v2",
    ):
        """
        Initializes the VectorCacheManager.

        Args:
            collection_name: The name of the ChromaDB collection.
            db_path: The path to the ChromaDB database directory.
            embedding_model_name: The name of the SentenceTransformer model to use.
        """
        if not chromadb or not embedding_functions:
            logger.error(
                "ChromaDB or SentenceTransformers is not installed. "
                "Vector cache will be disabled."
            )
            self.client = None
            self.collection = None
            self.embed_fn = None
            return

        if db_path is None:
            db_path = Path.home() / ".temir" / "vector_cache"

        self.db_path = str(db_path)
        self.collection_name = collection_name
        self.embedding_model_name = embedding_model_name

        try:
            # 1. Initialize ChromaDB client
            self.client = chromadb.PersistentClient(path=self.db_path)

            # 2. Initialize the embedding function
            self.embed_fn = embedding_functions.SentenceTransformerEmbeddingFunction(
                model_name=self.embedding_model_name
            )

            # 3. Get or create the collection
            self.collection = self.client.get_or_create_collection(
                name=self.collection_name,
                embedding_function=self.embed_fn,
                metadata={"hnsw:space": "cosine"},  # Use cosine similarity
            )
            logger.info(
                f"VectorCacheManager initialized. Collection '{self.collection_name}' loaded from {self.db_path}."
            )

        except Exception as e:
            logger.exception(f"Failed to initialize VectorCacheManager: {e}")
            self.client = None
            self.collection = None
            self.embed_fn = None

    def add_task(self, task_description: str, task_hash: str, metadata: Dict[str, Any]):
        """
        Adds a task description and its metadata to the vector cache.

        Args:
            task_description: The text of the task.
            task_hash: The unique hash of the task (used as the document ID).
            metadata: A dictionary of metadata to store with the task.
        """
        if not self.collection:
            return

        try:
            self.collection.add(
                documents=[task_description],
                metadatas=[metadata],
                ids=[task_hash],
            )
            logger.debug(f"Added task '{task_hash}' to vector cache.")
        except Exception as e:
            logger.exception(f"Failed to add task to vector cache: {e}")

    def find_similar_tasks(
        self, query_text: str, n_results: int = 3, min_similarity: float = 0.8
    ) -> List[Dict[str, Any]]:
        """
        Finds tasks in the cache that are semantically similar to the query text.

        Args:
            query_text: The text to search for.
            n_results: The maximum number of similar tasks to return.
            min_similarity: The minimum similarity score (distance) for a result to be included.

        Returns:
            A list of dictionaries, where each dictionary contains the metadata
            and similarity score of a similar task.
        """
        if not self.collection:
            return []

        try:
            results = self.collection.query(
                query_texts=[query_text],
                n_results=n_results,
            )

            if not results or not results.get("ids") or not results.get("metadatas"):
                return []
                
            similar_tasks = []
            doc_rows = results.get("documents") or []
            row_docs = doc_rows[0] if doc_rows else []
            for i, task_id in enumerate(results["ids"][0]):
                distance = results["distances"][0][i]
                similarity = 1 - distance # For cosine distance, similarity is 1 - distance
                if similarity >= min_similarity:
                    metadata = dict(results["metadatas"][0][i] or {})
                    doc_text = row_docs[i] if i < len(row_docs) else ""
                    similar_tasks.append(
                        {
                            "id": task_id,
                            "similarity": similarity,
                            "task_description": metadata.get("task_description") or doc_text,
                            **metadata,
                        },
                    )
            
            logger.info(f"Found {len(similar_tasks)} similar tasks for query: '{query_text[:50]}...'")
            return similar_tasks

        except Exception as e:
            logger.exception(f"Failed to query vector cache: {e}")
            return []

    def clear_collection(self):
        """Clears all items from the collection."""
        if not self.client or not self.collection:
            return

        try:
            self.client.delete_collection(name=self.collection_name)
            self.collection = self.client.get_or_create_collection(
                name=self.collection_name,
                embedding_function=self.embed_fn,
            )
            logger.info(f"Cleared all items from collection '{self.collection_name}'.")
        except Exception as e:
            logger.exception("Failed to clear vector cache collection.")
