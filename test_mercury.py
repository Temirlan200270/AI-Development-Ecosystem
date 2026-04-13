import requests
import os
import sys

# 1. Получаем ключ из окружения
api_key = os.getenv("INCEPTION_API_KEY")

if not api_key:
    print("❌ ОШИБКА: Переменная окружения INCEPTION_API_KEY не найдена.")
    print("Сначала выполните: set INCEPTION_API_KEY=ваш_ключ")
    sys.exit(1)

# 2. Чистим от пробелов (на всякий случай) и показываем инфо
original_len = len(api_key)
api_key = api_key.strip()
clean_len = len(api_key)

print(f"🔑 Ключ найден: {api_key[:5]}...{api_key[-4:]}")
if original_len != clean_len:
    print(f"⚠️ ПРЕДУПРЕЖДЕНИЕ: Ключ содержал пробелы! (Было: {original_len}, Стало: {clean_len})")
else:
    print(f"✅ Формат ключа корректный (Длина: {clean_len})")

# 3. Делаем запрос
url = 'https://api.inceptionlabs.ai/v1/chat/completions'
headers = {
    'Content-Type': 'application/json',
    'Authorization': f'Bearer {api_key}'
}
payload = {
    'model': 'mercury-2',
    'messages': [
        {'role': 'user', 'content': 'Output strictly JSON: {"status": "ok"}'}
    ],
    'max_tokens': 100
}

print("\n📡 Отправка запроса к Inception Labs...")

try:
    response = requests.post(url, headers=headers, json=payload, timeout=15)
    
    print(f"Status Code: {response.status_code}")
    
    if response.status_code == 200:
        print("✅ УСПЕХ! Ответ API:")
        print(response.json())
    else:
        print("❌ ОШИБКА API:")
        print(response.text)

except Exception as e:
    print(f"❌ Ошибка соединения: {e}")