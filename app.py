from flask import Flask, request, jsonify
from twilio.twiml.messaging_response import MessagingResponse
from openai import OpenAI
import os
from dotenv import load_dotenv
import time
import logging
from logging.handlers import RotatingFileHandler
import json
from typing import List, Dict, Optional
from datetime import datetime

# Configuração de logging detalhado
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s %(levelname)s [%(name)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)

handler = RotatingFileHandler(
    'logs/app.log',
    maxBytes=10000000,  # 10MB
    backupCount=5,
    encoding='utf-8'
)
handler.setFormatter(logging.Formatter(
    '%(asctime)s %(levelname)s [%(name)s] %(message)s'
))

# Carrega variáveis de ambiente
load_dotenv()

class MessageProcessor:
    @staticmethod
    def chunk_message(message: str, max_length: int = 1500) -> List[str]:
        """Divide uma mensagem em partes menores mantendo a formatação"""
        if not message:
            return []
            
        messages = []
        current_message = ""
        
        for line in message.split('\n'):
            # Se a linha é muito longa, divide em partes
            if len(line) > max_length:
                if current_message:
                    messages.append(current_message.strip())
                    current_message = ""
                    
                # Divide a linha em partes
                words = line.split()
                temp_line = ""
                for word in words:
                    if len(temp_line) + len(word) + 1 <= max_length:
                        temp_line += word + " "
                    else:
                        if temp_line:
                            messages.append(temp_line.strip())
                        temp_line = word + " "
                if temp_line:
                    current_message = temp_line
            else:
                if len(current_message) + len(line) + 1 <= max_length:
                    current_message += line + '\n'
                else:
                    messages.append(current_message.strip())
                    current_message = line + '\n'
                    
        if current_message:
            messages.append(current_message.strip())
            
        return messages
    
    @staticmethod
    def clean_message(message: str) -> str:
        """Remove formatação Markdown e caracteres especiais"""
        replacements = {
            '**': '',
            '*': '',
            '_': '',
            '`': '',
            '#': '',
            '\n\n\n': '\n\n'  # Remove múltiplas quebras de linha
        }
        
        for old, new in replacements.items():
            message = message.replace(old, new)
            
        return message.strip()

class OpenAIHandler:
    def __init__(self):
        self.api_key = os.getenv("OPENAI_API_KEY")
        self.assistant_id = os.getenv("ASSISTANT_ID")
        
        if not self.api_key or not self.assistant_id:
            raise ValueError("Credenciais OpenAI não encontradas no .env")
            
        logging.info(f"Inicializando OpenAI com Assistant ID: {self.assistant_id}")
        
        self.client = OpenAI(
            api_key=self.api_key,
            default_headers={"OpenAI-Beta": "assistants=v2"}
        )
        self.threads: Dict[str, str] = {}
        
    def get_response(self, message: str, sender: str) -> str:
        """Obtém resposta do Assistant"""
        try:
            logging.info(f"Processando mensagem de {sender}: {message}")
            
            # Obtém ou cria thread
            thread_id = self._get_or_create_thread(sender)
            logging.info(f"Usando thread: {thread_id}")
            
            # Adiciona mensagem
            self._add_message_to_thread(thread_id, message)
            
            # Executa o assistente
            run = self._create_run(thread_id)
            
            # Aguarda e obtém resposta
            self._wait_for_run(thread_id, run.id)
            response = self._get_last_message(thread_id)
            
            return response
            
        except Exception as e:
            logging.error(f"Erro ao obter resposta do assistente: {str(e)}", exc_info=True)
            return "Desculpe, estou tendo dificuldades técnicas no momento. Por favor, tente novamente em instantes."
            
    def _get_or_create_thread(self, sender: str) -> str:
        """Obtém thread existente ou cria uma nova"""
        if sender in self.threads:
            return self.threads[sender]
            
        thread = self.client.beta.threads.create()
        self.threads[sender] = thread.id
        return thread.id
        
    def _add_message_to_thread(self, thread_id: str, content: str) -> None:
        """Adiciona mensagem à thread"""
        self.client.beta.threads.messages.create(
            thread_id=thread_id,
            role="user",
            content=content
        )
        
    def _create_run(self, thread_id: str):
        """Cria e inicia um run do assistente"""
        return self.client.beta.threads.runs.create(
            thread_id=thread_id,
            assistant_id=self.assistant_id,
            model="gpt-4-1106-preview"
        )
        
    def _wait_for_run(self, thread_id: str, run_id: str, timeout: int = 30) -> None:
        """Aguarda a conclusão do run com timeout"""
        start_time = time.time()
        while time.time() - start_time < timeout:
            run_status = self.client.beta.threads.runs.retrieve(
                thread_id=thread_id,
                run_id=run_id
            )
            
            logging.debug(f"Status do run: {run_status.status}")
            
            if run_status.status == 'completed':
                return
            elif run_status.status in ['failed', 'expired', 'cancelled']:
                raise Exception(f"Run falhou com status: {run_status.status}")
                
            time.sleep(1)
            
        raise TimeoutError("Timeout aguardando resposta do assistente")
        
    def _get_last_message(self, thread_id: str) -> str:
        """Obtém a última mensagem da thread"""
        messages = self.client.beta.threads.messages.list(
            thread_id=thread_id,
            order="desc",
            limit=1
        )
        
        if not messages.data:
            raise Exception("Nenhuma mensagem encontrada")
            
        return messages.data[0].content[0].text.value

# Inicializa aplicação
app = Flask(__name__)
app.logger.addHandler(handler)

# Inicializa handlers
try:
    openai_handler = OpenAIHandler()
    message_processor = MessageProcessor()
    logging.info("Handlers inicializados com sucesso")
except Exception as e:
    logging.error(f"Erro ao inicializar handlers: {str(e)}")
    raise

@app.route("/", methods=['POST'])
@app.route("/webhook", methods=['POST'])
def webhook():
    """Webhook para mensagens do Twilio"""
    try:
        logging.info(f"Recebida requisição em {request.path}")
        logging.debug(f"Dados do formulário: {json.dumps(dict(request.form))}")
        
        # Obtém dados da mensagem
        incoming_msg = request.form.get('Body', '')
        sender = request.form.get('From', '')
        sms_status = request.form.get('SmsStatus', '')
        
        # Ignora atualizações de status
        if sms_status in ['sent', 'delivered', 'read']:
            msg_sid = request.form.get('MessageSid', 'Unknown')
            logging.info(f"Status update received for message {msg_sid}: {sms_status}")
            return '', 200
        
        # Processa a mensagem
        if incoming_msg and sender:
            logging.info(f"Processando mensagem: '{incoming_msg}' de {sender}")
            
            # Obtém resposta do Assistant
            full_response = openai_handler.get_response(incoming_msg, sender)
            logging.info(f"Resposta completa obtida: {full_response[:200]}...")
            
            # Prepara resposta TwiML
            twiml_response = MessagingResponse()
            
            # Divide a resposta em partes se necessário (limite de 1600 caracteres do WhatsApp)
            MAX_LENGTH = 1500
            current_part = ""
            
            # Divide por parágrafos primeiro
            paragraphs = full_response.split('\n\n')
            
            for paragraph in paragraphs:
                if len(current_part) + len(paragraph) + 2 <= MAX_LENGTH:
                    current_part += paragraph + "\n\n"
                else:
                    if current_part:
                        # Remove espaços extras e adiciona a parte
                        twiml_response.message(current_part.strip())
                        logging.debug(f"Adicionada parte da mensagem: {current_part[:100]}...")
                        current_part = paragraph + "\n\n"
                    else:
                        # Se o parágrafo é muito grande, divide por frases
                        sentences = paragraph.split('. ')
                        for sentence in sentences:
                            if len(current_part) + len(sentence) + 2 <= MAX_LENGTH:
                                current_part += sentence + ". "
                            else:
                                if current_part:
                                    twiml_response.message(current_part.strip())
                                    logging.debug(f"Adicionada parte da mensagem: {current_part[:100]}...")
                                current_part = sentence + ". "
            
            # Adiciona a última parte se houver
            if current_part:
                twiml_response.message(current_part.strip())
                logging.debug(f"Adicionada última parte da mensagem: {current_part[:100]}...")
            
            response_str = str(twiml_response)
            logging.info(f"TwiML response preparada: {response_str[:200]}...")
            
            headers = {
                'Content-Type': 'application/xml; charset=utf-8',
                'Cache-Control': 'no-cache, no-store, must-revalidate',
            }
            
            return response_str, 200, headers
            
        else:
            logging.warning("Mensagem ou remetente faltando")
            return 'Missing parameters', 400
            
    except Exception as e:
        logging.error(f"Erro no webhook: {str(e)}", exc_info=True)
        # Envia uma mensagem de erro simples
        twiml_response = MessagingResponse()
        twiml_response.message("Desculpe, ocorreu um erro. Por favor, tente novamente.")
        return str(twiml_response), 200, {'Content-Type': 'application/xml; charset=utf-8'}

@app.route("/health", methods=['GET'])
def health_check():
    """Endpoint para verificar a saúde da aplicação"""
    try:
        # Verifica o assistente
        assistant = openai_handler.client.beta.assistants.retrieve(
            openai_handler.assistant_id
        )
        
        return {
            "status": "healthy",
            "timestamp": datetime.now().isoformat(),
            "services": {
                "openai": {
                    "status": "ok",
                    "assistant_id": assistant.id,
                    "assistant_name": assistant.name,
                    "assistant_model": assistant.model,
                    "beta_version": "v2"
                }
            },
            "environment": {
                "openai_api_key": bool(os.getenv('OPENAI_API_KEY')),
                "assistant_id": bool(os.getenv('ASSISTANT_ID'))
            }
        }
    except Exception as e:
        logging.error(f"Health check falhou: {str(e)}")
        return {
            "status": "unhealthy",
            "timestamp": datetime.now().isoformat(),
            "error": str(e)
        }, 500

if __name__ == "__main__":
    os.makedirs('logs', exist_ok=True)
    logging.info("Iniciando aplicação...")
    app.run(host='0.0.0.0', port=5000, debug=True)