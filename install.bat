@echo off
echo Установка diff-match-patch...
echo.

REM Попробуем разные варианты Python
python -m pip install diff-match-patch 2>nul
if %errorlevel% equ 0 (
    echo.
    echo ✅ Успешно установлено через python!
    goto :check
)

python3 -m pip install diff-match-patch 2>nul
if %errorlevel% equ 0 (
    echo.
    echo ✅ Успешно установлено через python3!
    goto :check
)

py -m pip install diff-match-patch 2>nul
if %errorlevel% equ 0 (
    echo.
    echo ✅ Успешно установлено через py!
    goto :check
)

echo.
echo ❌ Не удалось найти Python или pip
echo.
echo Попробуйте вручную:
echo   pip install diff-match-patch
echo.
echo Или установите Python с https://www.python.org/downloads/
echo (не забудьте отметить "Add Python to PATH" при установке)
pause
exit /b 1

:check
echo.
echo Проверка установки...
python -c "import diff_match_patch; print('✅ Модуль работает!')" 2>nul
if %errorlevel% equ 0 (
    echo.
    echo ✅ Всё готово! Модуль установлен и работает.
) else (
    echo.
    echo ⚠️ Модуль установлен, но проверка не прошла.
    echo Попробуйте перезапустить терминал.
)
echo.
pause

