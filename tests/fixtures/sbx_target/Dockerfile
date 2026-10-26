# 샌드박스 self-test 대상 이미지. IAST 프리로드(sitecustomize.py)를 베이크하고
# PYTHONPATH 앞에 둬 앱 기동 시 자동 로드되게 한다. 비root 로 실행(보안 기본값).
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py sitecustomize.py /app/
ENV PYTHONPATH=/app
EXPOSE 8000
# gunicorn 없이 flask 내장 서버로 충분(테스트용). 0.0.0.0:8000.
CMD ["python", "-m", "flask", "--app", "app", "run", "--host", "0.0.0.0", "--port", "8000"]
