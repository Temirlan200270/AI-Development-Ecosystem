import hashlib
import logging
import sqlite3
from pathlib import Path
from typing import Any, Dict, List, Optional

from .vector_cache_manager import VectorCacheManager

logger = logging.getLogger(__name__)


class CacheManager:
    """Управление кэшем системы памяти (sqlite3 + ChromaDB).

    Контракт (микро):
    - inputs: task_description (str), role (str), plan_content (str / YAML string)
    - outputs: методы возвращают примитивные типы / dicts или bool
    - error modes: логируются и возвращается None/False при ошибках
    """

    def __init__(self, db_path: Optional[str] = None):
        if db_path is None:
            db_path = Path.home() / ".temir" / "cache.db"

        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        
        # Инициализация векторного кэша
        self.vector_cache = VectorCacheManager(db_path=str(self.db_path.parent / "vector_cache"))

        # Инициализировать базу данных
        self._init_database()

    def _init_database(self) -> None:
        schema_path = Path(__file__).parent / "schema.sql"
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("PRAGMA foreign_keys = ON")
                if schema_path.exists():
                    sql = schema_path.read_text(encoding="utf-8")
                    conn.executescript(sql)
                    logger.info("Cache DB initialized from schema.sql")
                else:
                    logger.warning("schema.sql not found; creating minimal tables")
                    self._create_basic_schema(conn)
        except Exception as e:
            logger.exception("Failed to initialize cache database: %s", e)
            raise

    def _create_basic_schema(self, conn: sqlite3.Connection) -> None:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS execution_plans (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_hash TEXT UNIQUE NOT NULL,
                task_description TEXT NOT NULL,
                plan_content TEXT NOT NULL,
                role TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                used_count INTEGER DEFAULT 0,
                last_used TIMESTAMP,
                success_rate REAL DEFAULT 0.0,
                is_successful BOOLEAN DEFAULT FALSE
            );
            CREATE TABLE IF NOT EXISTS plan_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                plan_id INTEGER NOT NULL,
                execution_result TEXT,
                exit_code INTEGER,
                execution_time REAL,
                executed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                was_successful BOOLEAN DEFAULT FALSE,
                FOREIGN KEY (plan_id) REFERENCES execution_plans (id)
            );
            CREATE TABLE IF NOT EXISTS task_patterns (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                pattern_hash TEXT UNIQUE NOT NULL,
                pattern_type TEXT NOT NULL,
                pattern_content TEXT NOT NULL,
                frequency INTEGER DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_seen TIMESTAMP
            );
            """,
        )
        logger.info("Minimal cache schema created")

    def _generate_task_hash(self, task_description: str, role: str) -> str:
        content = f"{role}:{task_description}"
        return hashlib.sha256(content.encode("utf-8")).hexdigest()

    def find_exact_or_none(
        self,
        task_description: str,
        role: str,
    ) -> Optional[Dict[str, Any]]:
        task_hash = self._generate_task_hash(task_description, role)
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cur = conn.execute(
                    "SELECT id, task_description, plan_content, used_count, is_successful, role, created_at FROM execution_plans WHERE task_hash = ? AND role = ?",
                    (task_hash, role),
                )
                row = cur.fetchone()
                if not row:
                    return None
                return {
                    "id": row["id"],
                    "task_description": row["task_description"],
                    "plan_content": row["plan_content"],
                    "used_count": row["used_count"],
                    "is_successful": bool(row["is_successful"]),
                    "role": row["role"],
                    "created_at": row["created_at"],
                }
        except Exception:
            logger.exception("find_exact_or_none failed")
            return None

    def find_similar_tasks(self, task_description: str) -> List[Dict[str, Any]]:
        """Finds similar tasks using the vector cache."""
        if self.vector_cache:
            return self.vector_cache.find_similar_tasks(task_description)
        return []

    def save_plan(
        self,
        task_description: str,
        role: str,
        plan_content: str,
        is_successful: bool = False,
    ) -> bool:
        task_hash = self._generate_task_hash(task_description, role)
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("PRAGMA foreign_keys = ON")
                # Try to insert, if exists - update plan_content and role
                conn.execute(
                    """
                    INSERT INTO execution_plans (task_hash, task_description, plan_content, role, is_successful)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(task_hash) DO UPDATE SET
                        task_description=excluded.task_description,
                        plan_content=excluded.plan_content,
                        role=excluded.role,
                        is_successful=excluded.is_successful
                    """,
                    (
                        task_hash,
                        task_description,
                        plan_content,
                        role,
                        int(bool(is_successful)),
                    ),
                )
                logger.info("Plan saved to cache (task_hash=%s)", task_hash[:8])
                
                # Add to vector cache
                if is_successful:
                    self.vector_cache.add_task(
                        task_description=task_description,
                        task_hash=task_hash,
                        metadata={"role": role}
                    )

                return True
        except Exception:
            logger.exception("save_plan failed")
            return False

    def mark_success(
        self,
        task_description: str,
        role: str,
        execution_result: Optional[str] = None,
        exit_code: int = 0,
        execution_time: Optional[float] = None,
    ) -> bool:
        task_hash = self._generate_task_hash(task_description, role)
        was_successful = exit_code == 0
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                cur = conn.execute(
                    "SELECT id, used_count FROM execution_plans WHERE task_hash = ? AND role = ?",
                    (task_hash, role),
                )
                row = cur.fetchone()
                if not row:
                    logger.warning(
                        "mark_success: plan not found for task_hash=%s",
                        task_hash[:8],
                    )
                    return False

                plan_id = row["id"]
                # update execution_plans counters
                conn.execute(
                    "UPDATE execution_plans SET used_count = used_count + 1, is_successful = ?, success_rate = CASE WHEN used_count = 0 THEN ? ELSE ((success_rate * used_count + ?) / (used_count + 1)) END WHERE id = ?",
                    (
                        int(was_successful),
                        float(100.0 if was_successful else 0.0),
                        float(100.0 if was_successful else 0.0),
                        plan_id,
                    ),
                )

                # insert into plan_results
                conn.execute(
                    "INSERT INTO plan_results (plan_id, execution_result, exit_code, execution_time, was_successful) VALUES (?, ?, ?, ?, ?)",
                    (
                        plan_id,
                        execution_result,
                        exit_code,
                        execution_time or None,
                        int(was_successful),
                    ),
                )
                logger.info("mark_success: recorded result for plan_id=%s", plan_id)
                return True
        except Exception:
            logger.exception("mark_success failed")
            return False

    def get_successful_plans(
        self,
        role: Optional[str] = None,
        limit: int = 10,
    ) -> List[Dict[str, Any]]:
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                if role:
                    cur = conn.execute(
                        "SELECT id, task_description, plan_content, used_count, created_at FROM execution_plans WHERE is_successful = 1 AND role = ? ORDER BY used_count DESC, created_at DESC LIMIT ?",
                        (role, limit),
                    )
                else:
                    cur = conn.execute(
                        "SELECT id, task_description, plan_content, used_count, created_at, role FROM execution_plans WHERE is_successful = 1 ORDER BY used_count DESC, created_at DESC LIMIT ?",
                        (limit,),
                    )
                return [dict(row) for row in cur.fetchall()]
        except Exception:
            logger.exception("get_successful_plans failed")
            return []

    def get_statistics(self) -> Dict[str, Any]:
        try:
            with sqlite3.connect(self.db_path) as conn:
                total = conn.execute("SELECT COUNT(*) FROM execution_plans").fetchone()[
                    0
                ]
                successful = conn.execute(
                    "SELECT COUNT(*) FROM execution_plans WHERE is_successful = 1",
                ).fetchone()[0]
                role_stats = {
                    r[0]: r[1]
                    for r in conn.execute(
                        "SELECT role, COUNT(*) FROM execution_plans GROUP BY role",
                    ).fetchall()
                }
                avg_success = (
                    conn.execute(
                        "SELECT AVG(success_rate) FROM execution_plans",
                    ).fetchone()[0]
                    or 0.0
                )
                hit_rate = (successful / total * 100.0) if total > 0 else 0.0
                return {
                    "total_plans": total,
                    "successful_plans": successful,
                    "success_rate": round(float(avg_success), 2),
                    "role_distribution": role_stats,
                    "cache_hit_rate": round(hit_rate, 2),
                }
        except Exception:
            logger.exception("get_statistics failed")
            return {
                "total_plans": 0,
                "successful_plans": 0,
                "success_rate": 0.0,
                "role_distribution": {},
                "cache_hit_rate": 0.0,
            }

    def clear_cache(self) -> bool:
        try:
            # Clear sqlite cache
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("PRAGMA foreign_keys = OFF")
                conn.execute("DELETE FROM plan_results")
                conn.execute("DELETE FROM execution_plans")
                conn.execute("DELETE FROM task_patterns")
                conn.execute("PRAGMA foreign_keys = ON")
            
            # Clear vector cache
            if self.vector_cache:
                self.vector_cache.clear_collection()

            logger.info("Cache cleared")
            return True
        except Exception:
            logger.exception("clear_cache failed")
            return False
