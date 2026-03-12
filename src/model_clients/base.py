from abc import ABC, abstractmethod
from typing import Dict, Any, List

class MultiModalClient(ABC):
    """
    通用多模態模型客戶端 Adapter。
    支援 OCR、圖文理解、表格解析等進階 VLM 任務。
    """

    @abstractmethod
    def process_image(self, image_path: str, prompt: str = "") -> str:
        """
        處理單張圖片並回傳理解結果 (Captioning / OCR)
        """
        pass

    @abstractmethod
    def extract_table(self, image_path: str) -> str:
        """
        將圖片中的表格提取為 Markdown 或 HTML 格式
        """
        pass

    @abstractmethod
    def generate_text(self, prompt: str, system_prompt: str = "") -> str:
        """
        純文本生成 (適用於 QA 或摘要)
        """
        pass
