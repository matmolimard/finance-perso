"""
OpenRouter Client - Client API pour OpenRouter
"""
import os
import json
import logging
from typing import Optional, Dict, Any
from pathlib import Path
import httpx

# Charger les variables d'environnement depuis .env si disponible
try:
    from dotenv import load_dotenv
    # Chercher .env à la racine du projet
    project_root = Path(__file__).parent.parent.parent.parent
    env_file = project_root / ".env"
    if env_file.exists():
        load_dotenv(env_file)
    else:
        # Fallback: chercher dans le répertoire courant
        load_dotenv()
except ImportError:
    # python-dotenv n'est pas installé, on continue sans
    pass

logger = logging.getLogger(__name__)

# Modèles disponibles (par ordre de préférence pour finance)
FINANCE_MODELS = [
    "anthropic/claude-3.5-sonnet",  # Bon modèle généraliste
    "openai/gpt-4o",  # Alternative
    "openai/gpt-4-turbo",  # Fallback
]

OPENROUTER_API_URL = "https://openrouter.ai/api/v1/chat/completions"


class OpenRouterClient:
    """Client pour l'API OpenRouter"""
    
    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None
    ):
        """
        Initialise le client OpenRouter
        
        Args:
            api_key: Clé API OpenRouter (défaut: variable d'environnement OPENROUTER_API_KEY)
            model: Modèle à utiliser (défaut: variable d'environnement OPENROUTER_MODEL ou premier de FINANCE_MODELS)
        """
        self.api_key = api_key or os.getenv("OPENROUTER_API_KEY")
        if not self.api_key:
            raise ValueError(
                "OPENROUTER_API_KEY doit être défini (variable d'environnement ou paramètre)"
            )
        
        self.model = model or os.getenv("OPENROUTER_MODEL") or FINANCE_MODELS[0]
        self.base_url = OPENROUTER_API_URL
    
    def generate_recommendations(
        self,
        prompt: str,
        temperature: float = 0.7,
        max_tokens: int = 4000
    ) -> Dict[str, Any]:
        """
        Génère des recommandations via OpenRouter
        
        Args:
            prompt: Prompt à envoyer à l'IA
            temperature: Température pour la génération (0.0-1.0)
            max_tokens: Nombre maximum de tokens
            
        Returns:
            Réponse JSON de l'IA
            
        Raises:
            httpx.HTTPError: En cas d'erreur HTTP
            ValueError: Si la réponse n'est pas valide
        """
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/portfolio-tracker",  # Optionnel mais recommandé
        }
        
        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": "Tu es un conseiller financier expert spécialisé dans l'analyse de portefeuilles d'assurance vie et contrats de capitalisation. Tu fournis des conseils structurés et factuels basés sur les données fournies."
                },
                {
                    "role": "user",
                    "content": prompt
                }
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "response_format": {"type": "json_object"},  # Forcer JSON
        }
        
        logger.info(f"Envoi requête à OpenRouter avec modèle {self.model}")
        
        try:
            with httpx.Client(timeout=60.0) as client:
                response = client.post(
                    self.base_url,
                    headers=headers,
                    json=payload
                )
                response.raise_for_status()
                
                data = response.json()
                
                # Extraire le contenu de la réponse
                if "choices" not in data or len(data["choices"]) == 0:
                    raise ValueError("Réponse OpenRouter invalide: pas de choix")
                
                content = data["choices"][0]["message"]["content"]
                
                # Parser le JSON
                try:
                    return json.loads(content)
                except json.JSONDecodeError as e:
                    logger.error(f"Erreur parsing JSON: {e}")
                    logger.error(f"Contenu reçu: {content[:500]}")
                    raise ValueError(f"Réponse JSON invalide: {e}")
                    
        except httpx.HTTPError as e:
            logger.error(f"Erreur HTTP OpenRouter: {e}")
            raise
        except Exception as e:
            logger.error(f"Erreur inattendue OpenRouter: {e}")
            raise
    
    def chat(
        self,
        messages: list,
        temperature: float = 0.7,
        max_tokens: int = 2000,
        force_json: bool = False
    ) -> str:
        """
        Mode conversationnel - envoie un message et retourne la réponse textuelle
        
        Args:
            messages: Liste de messages avec format [{"role": "user|assistant|system", "content": "..."}]
            temperature: Température pour la génération (0.0-1.0)
            max_tokens: Nombre maximum de tokens
            force_json: Si True, force le format JSON (pour les recommandations initiales)
            
        Returns:
            Réponse textuelle de l'IA
            
        Raises:
            httpx.HTTPError: En cas d'erreur HTTP
            ValueError: Si la réponse n'est pas valide
        """
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/portfolio-tracker",
        }
        
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        
        if force_json:
            payload["response_format"] = {"type": "json_object"}
        
        logger.info(f"Envoi message conversationnel à OpenRouter avec modèle {self.model}")
        
        try:
            with httpx.Client(timeout=60.0) as client:
                response = client.post(
                    self.base_url,
                    headers=headers,
                    json=payload
                )
                response.raise_for_status()
                
                data = response.json()
                
                # Extraire le contenu de la réponse
                if "choices" not in data or len(data["choices"]) == 0:
                    raise ValueError("Réponse OpenRouter invalide: pas de choix")
                
                return data["choices"][0]["message"]["content"]
                    
        except httpx.HTTPError as e:
            logger.error(f"Erreur HTTP OpenRouter: {e}")
            raise
        except Exception as e:
            logger.error(f"Erreur inattendue OpenRouter: {e}")
            raise

