FROM python:3.11-slim

WORKDIR /usr/src/app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

RUN mkdir -p data config core

COPY bot.py ./
COPY core/__init__.py core/
COPY core/scraper.py core/
COPY config/lookup_tables.json config/

CMD [ "python", "bot.py" ]