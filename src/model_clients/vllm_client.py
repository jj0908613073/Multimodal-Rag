import os
import requests
from typing import Dict, Any
from src.model_clients.base import MultiModalClient

class VllmClient(MultiModalClient):
    """
    使用 vLLM (OpenAI Compatible API) 的實作
    通常用於串接自行架設的高效能 VLM 模型 (如 Qwen-VL, Llava 等)
    """
    def __init__(self, endpoint: str = None, model_name: str = None):
        self.endpoint = endpoint or os.getenv("VLLM_ENDPOINT", "http://localhost:8000/v1")
        self.model_name = model_name or os.getenv("MODEL_NAME", "qwen-vl")
        self.api_key = os.getenv("VLLM_API_KEY", "EMPTY")
        
    def _call_chat_completion(self, messages: list) -> str:
        headers = {"Authorization": f"Bearer {self.api_key}"}
        payload = {
            "model": self.model_name,
            "messages": messages,
            "temperature": 0.1
        }
        res = requests.post(f"{self.endpoint}/chat/completions", json=payload, headers=headers)
        res.raise_for_status()
        return res.json()["choices"][0]["message"]["content"]

    def process_image(self, image_path: str, prompt: str = "請將這張圖片轉換成 Markdown 格式保留所有排版細節") -> str:
        # TODO: 實作圖片轉 Base64 或 URL 根據 vLLM 格式
        messages = [
            {"role": "user", "content": [{"type": "text", "text": prompt}]}
        ]
        return self._call_chat_completion(messages)

    def extract_table(self, image_path: str) -> str:
        prompt = "請將圖片中表格提取為純 Markdown 格式表格，如果有合併的儲存格請盡可能還原語意"
        return self.process_image(image_path, prompt=prompt)

    def generate_text(self, prompt: str, system_prompt: str = "") -> str:
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})
        return self._call_chat_completion(messages)
