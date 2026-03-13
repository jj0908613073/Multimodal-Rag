import os
import requests
from src.model_clients.base import MultiModalClient

class OllamaClient(MultiModalClient):
    """
    對接 Ollama 的實作
    適用於本機快速開發或部署小型/中型多模態開源模型
    """
    def __init__(self, endpoint: str = None, model_name: str = None):
        self.endpoint = endpoint or os.getenv("OLLAMA_ENDPOINT", "http://allm01:12384")
        self.model_name = model_name or os.getenv("MODEL_NAME", "glm-ocr-80k:latest")

    def _call_generate(self, prompt: str, images: list = None, system: str = "") -> str:
        payload = {
            "model": self.model_name,
            "prompt": prompt,
            "stream": False
        }
        if images:
            payload["images"] = images
        if system:
            payload["system"] = system
            
        res = requests.post(f"{self.endpoint}/api/generate", json=payload)
        res.raise_for_status()
        return res.json()["response"]

    def _encode_image(self, image_path: str) -> str:
        import base64
        with open(image_path, "rb") as image_file:
            return base64.b64encode(image_file.read()).decode('utf-8')

    def process_image(self, image_path: str, prompt: str = "請詳細描述這張圖片的內容") -> str:
        base64_img = self._encode_image(image_path)
        return self._call_generate(prompt=prompt, images=[base64_img])

    def extract_table(self, image_path: str) -> str:
        # Prompt based on best practices for generating clean markdown tables from images
        prompt = "Table Recognition: 請把這張圖中的表格完整轉成 Markdown 表格。"
        base64_img = self._encode_image(image_path)
        return self._call_generate(prompt=prompt, images=[base64_img])

    def generate_text(self, prompt: str, system_prompt: str = "") -> str:
        return self._call_generate(prompt=prompt, system=system_prompt)
