FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV TF_LLM_MODEL=deepseek-v4-flash
EXPOSE 8000

# 启动时自动 seed（建表+市场档案+KB+Prompt登记），再拉起 API
CMD ["python", "main.py", "serve"]
