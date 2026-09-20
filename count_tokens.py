"""计算 raw_prompts_draft.txt 的 token 数量（GPT-4 编码 cl100k_base）。"""
import tiktoken

PATH = r"E:/AIbulid/hermesonly/raw_prompts_draft.txt"

with open(PATH, encoding="utf-8") as f:
    text = f.read()

enc = tiktoken.encoding_for_model("gpt-4")  # cl100k_base
tokens = enc.encode(text)

print(f"文件: {PATH}")
print(f"字符数: {len(text):,}")
print(f"Token 数 (GPT-4 / cl100k_base): {len(tokens):,}")
