import requests
import json

res = requests.post(
    'http://127.0.0.1:5000/api/inventory/analyze',
    json={"batch_id": "test"}
)
print(res.status_code)
print(res.text)
