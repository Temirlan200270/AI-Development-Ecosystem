# Скрипт установки зависимостей для Temir
Write-Host "Установка зависимостей для Temir..." -ForegroundColor Green

# Попробуем найти Python
$pythonCmd = $null

# Вариант 1: python
try {
    $pythonCmd = Get-Command python -ErrorAction Stop
    Write-Host "Найден: python" -ForegroundColor Yellow
} catch {
    # Вариант 2: python3
    try {
        $pythonCmd = Get-Command python3 -ErrorAction Stop
        Write-Host "Найден: python3" -ForegroundColor Yellow
    } catch {
        # Вариант 3: py launcher
        try {
            $pythonCmd = Get-Command py -ErrorAction Stop
            Write-Host "Найден: py" -ForegroundColor Yellow
        } catch {
            Write-Host "❌ Python не найден в PATH!" -ForegroundColor Red
            Write-Host "Пожалуйста, установите Python или добавьте его в PATH" -ForegroundColor Red
            exit 1
        }
    }
}

$pythonExe = $pythonCmd.Source

Write-Host "Используется: $pythonExe" -ForegroundColor Cyan
Write-Host ""

# Установка diff-match-patch
Write-Host "Установка diff-match-patch..." -ForegroundColor Yellow
& $pythonExe -m pip install diff-match-patch

if ($LASTEXITCODE -eq 0) {
    Write-Host "✅ diff-match-patch установлен успешно!" -ForegroundColor Green
} else {
    Write-Host "❌ Ошибка при установке diff-match-patch" -ForegroundColor Red
    Write-Host "Попробуйте установить вручную: pip install diff-match-patch" -ForegroundColor Yellow
}

Write-Host ""
Write-Host "Проверка установки..." -ForegroundColor Yellow
& $pythonExe -c "import diff_match_patch; print('✅ Модуль diff_match_patch работает!')"

if ($LASTEXITCODE -eq 0) {
    Write-Host ""
    Write-Host "✅ Все готово! Теперь можно запускать temir." -ForegroundColor Green
} else {
    Write-Host "❌ Модуль не установлен корректно" -ForegroundColor Red
}

