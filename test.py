import requests

def send_telegram_msg(message):
    token = '8792563638:AAGnC8J3PXsUWZTiXyJoHNygWbkcqs2cyK4'
    chat_id = '5582749951'
 
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "Markdown" # Allows you to use *bold* or `code` tags
    }
    
    response = requests.post(url, data=payload)
    return response.json()

# Test the connection
status = send_telegram_msg("🚀 *NSE Trading Bot:* System Online and Monitoring.")
print(status)
