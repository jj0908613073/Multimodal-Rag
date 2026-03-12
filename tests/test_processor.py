from src.processors.document_processor import DocumentProcessor
from dotenv import load_dotenv

load_dotenv()

from src.model_clients.ollama_client import OllamaClient

def main():
    # 使用符合使用者本地的 model_name
    vlm_client = OllamaClient(model_name="glm-ocr:latest")
    processor = DocumentProcessor(vlm_client=vlm_client, output_dir="./data/processed")
    test_file = "glm-ocr/examples/source/buys_20240226.pdf"
    
    print(f"Testing document processor with {test_file}...")
    try:
        markdown_result = processor.process(test_file)
        print("\n--- Output Preview (First 500 chars) ---")
        print(markdown_result[:500])
        print("------------------------------------------")
    except Exception as e:
        print(f"Error during processing: {e}")

if __name__ == "__main__":
    main()
