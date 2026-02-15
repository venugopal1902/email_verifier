# Use the official Python image
FROM python:3.11-slim

# Set environment variables
ENV PYTHONUNBUFFERED 1
ENV DJANGO_SETTINGS_MODULE core.settings

# Set working directory
WORKDIR /usr/src/app

# Install dependencies
COPY requirements.txt .
RUN pip install --upgrade pip
RUN pip install -r requirements.txt

# Copy project code
COPY . /usr/src/app

# 6. EXPOSE the port (documentation only, but good practice)
EXPOSE 8000

# 7. THE MISSING PIECE: Start the server
# We use 0.0.0.0 so external traffic (like Koyeb) can reach it.
CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]