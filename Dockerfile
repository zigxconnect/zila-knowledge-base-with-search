FROM python:3.10-slim
WORKDIR /srv
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

COPY requirements.txt .
RUN pip install --no-cache-dir --default-timeout=300 --retries 10 -r requirements.txt

COPY app ./app
COPY streamlit_app.py ./streamlit_app.py
RUN useradd -m appuser && mkdir -p /data/uploads && chown -R appuser /data
USER appuser
EXPOSE 8000 8501
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]