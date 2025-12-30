"""
Notifier - Notification des alertes
"""
from abc import ABC, abstractmethod
from typing import List
from datetime import datetime

from .rules import AlertTrigger, AlertSeverity


class Notifier(ABC):
    """
    Interface abstraite pour les notificateurs d'alertes.
    """
    
    @abstractmethod
    def notify(self, triggers: List[AlertTrigger]):
        """
        Envoie des notifications pour les alertes.
        
        Args:
            triggers: Liste des alertes à notifier
        """
        pass


class ConsoleNotifier(Notifier):
    """
    Notificateur console simple.
    
    Affiche les alertes dans la console avec formatage.
    """
    
    def __init__(self, min_severity: AlertSeverity = AlertSeverity.INFO):
        """
        Initialise le notificateur console.
        
        Args:
            min_severity: Sévérité minimale à afficher
        """
        self.min_severity = min_severity
        self.severity_order = {
            AlertSeverity.INFO: 0,
            AlertSeverity.WARNING: 1,
            AlertSeverity.ERROR: 2,
        }
    
    def notify(self, triggers: List[AlertTrigger]):
        """Affiche les alertes dans la console"""
        if not triggers:
            print("✓ Aucune alerte")
            return
        
        # Filtrer par sévérité
        filtered = [
            t for t in triggers
            if self.severity_order[t.severity] >= self.severity_order[self.min_severity]
        ]
        
        if not filtered:
            print("✓ Aucune alerte")
            return
        
        # Grouper par sévérité
        by_severity = {
            AlertSeverity.ERROR: [],
            AlertSeverity.WARNING: [],
            AlertSeverity.INFO: [],
        }
        
        for trigger in filtered:
            by_severity[trigger.severity].append(trigger)
        
        # Afficher
        print(f"\n{'='*70}")
        print(f"ALERTES - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"{'='*70}\n")
        
        for severity in [AlertSeverity.ERROR, AlertSeverity.WARNING, AlertSeverity.INFO]:
            alerts = by_severity[severity]
            if not alerts:
                continue
            
            symbol = self._get_symbol(severity)
            print(f"{symbol} {severity.value.upper()} ({len(alerts)})")
            print("-" * 70)
            
            for trigger in alerts:
                self._print_trigger(trigger)
            
            print()
    
    def _get_symbol(self, severity: AlertSeverity) -> str:
        """Retourne un symbole pour la sévérité"""
        symbols = {
            AlertSeverity.ERROR: "✗",
            AlertSeverity.WARNING: "⚠",
            AlertSeverity.INFO: "ℹ",
        }
        return symbols.get(severity, "•")
    
    def _print_trigger(self, trigger: AlertTrigger):
        """Affiche une alerte individuelle"""
        print(f"  • {trigger.message}")
        
        details = []
        if trigger.asset_id:
            details.append(f"Asset: {trigger.asset_id}")
        if trigger.position_id:
            details.append(f"Position: {trigger.position_id}")
        
        if details:
            print(f"    ({', '.join(details)})")


class LogNotifier(Notifier):
    """
    Notificateur fichier log.
    
    Écrit les alertes dans un fichier log.
    """
    
    def __init__(self, log_file: str):
        """
        Initialise le notificateur log.
        
        Args:
            log_file: Chemin du fichier log
        """
        self.log_file = log_file
    
    def notify(self, triggers: List[AlertTrigger]):
        """Écrit les alertes dans le fichier log"""
        if not triggers:
            return
        
        with open(self.log_file, 'a', encoding='utf-8') as f:
            timestamp = datetime.now().isoformat()
            f.write(f"\n{'='*70}\n")
            f.write(f"ALERTES - {timestamp}\n")
            f.write(f"{'='*70}\n\n")
            
            for trigger in triggers:
                f.write(f"[{trigger.severity.value.upper()}] {trigger.rule_name}\n")
                f.write(f"  Message: {trigger.message}\n")
                if trigger.asset_id:
                    f.write(f"  Asset: {trigger.asset_id}\n")
                if trigger.position_id:
                    f.write(f"  Position: {trigger.position_id}\n")
                if trigger.metadata:
                    f.write(f"  Metadata: {trigger.metadata}\n")
                f.write("\n")


class EmailNotifier(Notifier):
    """
    Notificateur email (facultatif).
    
    Envoie les alertes par email en utilisant smtplib.
    """
    
    def __init__(
        self, 
        smtp_host: str,
        smtp_port: int,
        from_addr: str,
        to_addr: str,
        username: str = None,
        password: str = None
    ):
        """
        Initialise le notificateur email.
        
        Args:
            smtp_host: Serveur SMTP
            smtp_port: Port SMTP
            from_addr: Adresse expéditeur
            to_addr: Adresse destinataire
            username: Nom d'utilisateur SMTP (optionnel)
            password: Mot de passe SMTP (optionnel)
        """
        self.smtp_host = smtp_host
        self.smtp_port = smtp_port
        self.from_addr = from_addr
        self.to_addr = to_addr
        self.username = username
        self.password = password
    
    def notify(self, triggers: List[AlertTrigger]):
        """Envoie les alertes par email"""
        if not triggers:
            return
        
        import smtplib
        from email.mime.text import MIMEText
        from email.mime.multipart import MIMEMultipart
        
        # Construire le message
        msg = MIMEMultipart()
        msg['From'] = self.from_addr
        msg['To'] = self.to_addr
        msg['Subject'] = f"Portfolio Tracker - {len(triggers)} alerte(s)"
        
        body = self._build_email_body(triggers)
        msg.attach(MIMEText(body, 'plain'))
        
        # Envoyer
        try:
            with smtplib.SMTP(self.smtp_host, self.smtp_port) as server:
                if self.username and self.password:
                    server.starttls()
                    server.login(self.username, self.password)
                server.send_message(msg)
        except Exception as e:
            print(f"Erreur lors de l'envoi de l'email: {e}")
    
    def _build_email_body(self, triggers: List[AlertTrigger]) -> str:
        """Construit le corps de l'email"""
        lines = [
            f"Portfolio Tracker - Alertes du {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            "",
            f"{len(triggers)} alerte(s) détectée(s):",
            "",
        ]
        
        for trigger in triggers:
            lines.append(f"[{trigger.severity.value.upper()}] {trigger.message}")
            if trigger.asset_id:
                lines.append(f"  Asset: {trigger.asset_id}")
            if trigger.position_id:
                lines.append(f"  Position: {trigger.position_id}")
            lines.append("")
        
        return "\n".join(lines)










