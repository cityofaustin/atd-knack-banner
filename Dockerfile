FROM python:3.8-slim

# Copy our own application
WORKDIR /app
COPY . /app/atd-knack-banner

RUN chmod -R 755 /app/*

# # Proceed to install the requirements...do
RUN cd /app/atd-knack-banner && apt-get update && \
    apt-get install -y gcc python-dev libkrb5-dev && \
    pip install -r requirements.txt