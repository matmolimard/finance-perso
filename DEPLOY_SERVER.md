# Deploiement Docker sur serveur

Ce projet est un CLI Python (pas une API web). Le mode serveur recommande est:
- image Docker reproductible
- donnees YAML persistees sur disque
- execution des commandes via `docker compose run`

## 1) Pre-requis serveur

- Docker Engine + Docker Compose plugin
- Git

Exemple Ubuntu:

```bash
sudo apt-get update
sudo apt-get install -y git docker.io docker-compose-plugin
sudo usermod -aG docker $USER
```

Reconnectez-vous ensuite pour appliquer le groupe `docker`.

## 2) Recuperer le projet

```bash
git clone <URL_DU_REPO> finance-perso
cd finance-perso
```

## 3) Configurer les variables d'environnement

Si vous utilisez la commande `advice`, creez `.env`:

```bash
cp env.example .env
# puis editez .env
```

## 4) Build de l'image

```bash
make docker-build
```

## 5) Executer des commandes dans le conteneur

```bash
# Vue globale
make docker-global

# Historique d'une valeur
make docker-history VALUE=bdl_rempart
make docker-history VALUE=CMS_EUR FROM=2026-01-01

# Mise a jour des VL UC
make docker-update-navs

# Commande arbitraire
make docker-run ARGS="global --details"
```

## 6) Persistance des donnees

`docker-compose.yml` monte le dossier:

- `./portfolio_tracker/data` (host)
- vers `/app/portfolio_tracker/data` (conteneur)

Toutes vos mises a jour de donnees restent sur le serveur.

## 7) Lancer une tache planifiee (cron)

Exemple: update quotidien des VL UC a 07:00.

```bash
crontab -e
```

Ajoutez:

```cron
0 7 * * * cd /path/to/finance-perso && /usr/bin/make docker-update-navs >> /var/log/portfolio-tracker-update-navs.log 2>&1
```

## 8) Mise a jour applicative

```bash
cd /path/to/finance-perso
git pull
make docker-build
```

