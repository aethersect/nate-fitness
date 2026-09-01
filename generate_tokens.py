"""
A LANCER UNE SEULE FOIS, EN LOCAL SUR TON ORDI (jamais sur GitHub).

Ce script se connecte une fois à ton compte Garmin, récupère les jetons de
session (garth) et les affiche en base64. Ce base64 est ce que tu colles
dans le secret GitHub GARMIN_TOKENS_B64 — PAS ton mot de passe.

Les jetons Garmin durent plusieurs mois, donc tu n'as (normalement) besoin
de relancer ce script qu'occasionnellement, si jamais la session expire.

Usage:
    pip install garminconnect
    python generate_tokens.py
"""
import base64
import getpass
import io
import tarfile
from pathlib import Path

import garminconnect

TOKEN_DIR = Path.home() / ".garmin_tokens_tmp"


def main():
    email = input("Email Garmin Connect : ").strip()
    password = getpass.getpass("Mot de passe Garmin Connect (invisible en tapant, c'est normal) : ")

    # Compatible anciennes ET nouvelles versions de garminconnect.
    # Les versions récentes acceptent prompt_mfa=..., les anciennes non
    # (dans ce cas, garth demande le code MFA tout seul via input() si besoin).
    try:
        client = garminconnect.Garmin(
            email=email,
            password=password,
            prompt_mfa=lambda: input("Code MFA (si demandé) : ").strip(),
        )
    except TypeError:
        client = garminconnect.Garmin(email=email, password=password)

    client.login()

    TOKEN_DIR.mkdir(parents=True, exist_ok=True)
    client.garth.dump(str(TOKEN_DIR))

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        tar.add(TOKEN_DIR, arcname="garmin_tokens")
    b64 = base64.b64encode(buf.getvalue()).decode()

    print("\n" + "=" * 70)
    print("COPIE TOUT LE BLOC CI-DESSOUS DANS LE SECRET GITHUB 'GARMIN_TOKENS_B64'")
    print("=" * 70 + "\n")
    print(b64)
    print("\n" + "=" * 70)
    print("Connexion réussie et jetons générés. Tu peux fermer ce terminal.")


if __name__ == "__main__":
    main()
