# Start from an official Python image.
# 'slim' means it has Python but strips out everything else — smaller image.
FROM python:3.12-slim

# All subsequent commands run inside /app inside the container.
WORKDIR /app

# Copy requirements FIRST and install them.
# Docker caches each layer. Because requirements.txt rarely changes,
# this layer stays cached even when you change app code.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Now copy the rest of the source code.
COPY . .

# Tell Docker this container listens on port 8000 (documentation only —
# the actual port binding happens in docker-compose.yml).
EXPOSE 8000

# The command that runs when the container starts.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
