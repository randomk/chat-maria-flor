# Imagem base
FROM python:3.9-slim

# Define o diretório de trabalho
WORKDIR /app

# Instala dependências do sistema
RUN apt-get update && \
    apt-get install -y --no-install-recommends gcc && \
    rm -rf /var/lib/apt/lists/*

# Cria diretório de logs e configura permissões
RUN mkdir -p /app/logs && \
    chmod 777 /app/logs

# Copia os requisitos
COPY requirements.txt .

# Instala dependências Python
RUN pip install --no-cache-dir -r requirements.txt

# Copia o código
COPY . .

# Expõe a porta
EXPOSE 5000

# Comando para iniciar
CMD ["python", "app.py"]