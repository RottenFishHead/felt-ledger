FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /code

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Bundle CSS/JS/admin assets for WhiteNoise. The key is a throwaway used only for this build step.
RUN DJANGO_DEBUG=0 DJANGO_SECRET_KEY=build-only python manage.py collectstatic --noinput

EXPOSE 8000
CMD ["./start.sh"]
