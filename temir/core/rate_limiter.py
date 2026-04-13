# -*- coding: utf-8 -*-
"""
Реализация ограничителя скорости с помощью алгоритма Token Bucket.
"""
import asyncio
import time
from typing import Optional


class TokenBucket:
    """
    Асинхронный ограничитель скорости по алгоритму 'Token Bucket'.

    Этот класс позволяет контролировать частоту выполнения операций,
    например, API-запросов, чтобы не превышать установленные лимиты.
    """

    def __init__(
        self,
        tokens_per_second: float,
        max_tokens: float,
        loop: Optional[asyncio.AbstractEventLoop] = None,
    ):
        """
        Инициализирует 'Token Bucket'.

        Args:
            tokens_per_second: Скорость пополнения токенов (в секунду).
            max_tokens: Максимальное количество токенов в контейнере.
            loop: Цикл событий asyncio (опционально).
        """
        self.max_tokens = max_tokens
        self.tokens_per_second = tokens_per_second
        self._tokens = max_tokens
        self._last_consumption_time = time.monotonic()
        self._loop = loop or asyncio.get_event_loop()
        self._lock = asyncio.Lock()

    def _refill(self):
        """Пополняет токены на основе времени, прошедшего с последнего пополнения."""
        now = time.monotonic()
        time_passed = now - self._last_consumption_time
        new_tokens = time_passed * self.tokens_per_second

        if new_tokens > 0:
            self._tokens = min(self._tokens + new_tokens, self.max_tokens)
            self._last_consumption_time = now

    async def acquire(self, tokens: int = 1):
        """
        Запрашивает указанное количество токенов, ожидая при необходимости.

        Args:
            tokens: Количество запрашиваемых токенов.
        """
        if tokens > self.max_tokens:
            raise ValueError(
                f"Запрос на {tokens} токенов превышает максимальный размер контейнера ({self.max_tokens})",
            )

        async with self._lock:
            while True:
                self._refill()

                if self._tokens >= tokens:
                    self._tokens -= tokens
                    return

                # Вычисляем время ожидания до появления нужного количества токенов
                tokens_needed = tokens - self._tokens
                wait_time = tokens_needed / self.tokens_per_second
                
                await asyncio.sleep(wait_time)
