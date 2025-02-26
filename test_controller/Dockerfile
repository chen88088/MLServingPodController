# 使用 Python 3.9 作為基礎映像
FROM python:3.9

# 設定工作目錄
WORKDIR /app

# 複製應用程式代碼到容器內
COPY app.py /app/
COPY requirements.txt /app/

# 安裝 FastAPI 及相關依賴
RUN pip install --no-cache-dir -r requirements.txt

# 暴露 8000 Port（這樣 Kubernetes Service 可以對外暴露）
EXPOSE 8000

# 指定容器啟動時的執行命令

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
