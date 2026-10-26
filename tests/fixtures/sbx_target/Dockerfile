# 샌드박스 self-test 대상 이미지 — 표준 라이브러리만이라 pip install 없음(egress 불필요).
# IAST 프리로드(sitecustomize.py)를 베이크하고 PYTHONPATH 앞에 둬 자동 로드. 비root 실행.
FROM python:3.12-slim
WORKDIR /app
COPY app.py sitecustomize.py /app/
ENV PYTHONPATH=/app
EXPOSE 8000
CMD ["python", "app.py"]
