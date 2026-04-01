import logging
import os
import random
import time
from functools import wraps

import jwt
import requests
from authlib.integrations.flask_client import OAuth
from flask import Flask, jsonify, redirect, render_template, session, url_for
from werkzeug.middleware.proxy_fix import ProxyFix
from google.cloud import compute_v1

from pathlib import Path

_phrases_file = Path(__file__).parent / "phrases.txt"
PHRASES = [l.strip() for l in _phrases_file.read_text().splitlines() if l.strip()]


PROJECT = os.environ["GCP_PROJECT"]
ZONE = os.environ["GCP_ZONE"]
INSTANCE_NAME = os.environ["INSTANCE_NAME"]
OAUTH_REDIRECT_URI = os.environ.get("OAUTH_REDIRECT_URI")
REPO_OWNER = os.environ.get("GITHUB_REPO_OWNER", "axiomeye")
REPO_NAME = os.environ.get("GITHUB_REPO_NAME", "aria-minecraft-server-iac")
GH_APP_ID = os.environ["GH_APP_ID"]
GH_APP_INSTALLATION_ID = os.environ["GH_APP_INSTALLATION_ID"]
GH_APP_PRIVATE_KEY = os.environ["GH_APP_PRIVATE_KEY"]
ALLOWED = {e.strip() for e in os.environ["ALLOWED_EMAILS"].split(",")}

app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)
app.secret_key = os.environ["FLASK_SECRET_KEY"]

oauth = OAuth(app)
oauth.register(
    name="google",
    client_id=os.environ["GOOGLE_CLIENT_ID"],
    client_secret=os.environ["GOOGLE_CLIENT_SECRET"],
    server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
    client_kwargs={"scope": "openid email"},
)


def require_login(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if "email" not in session:
            return redirect(url_for("login"))
        if session["email"] not in ALLOWED:
            return "Access denied.", 403
        return f(*args, **kwargs)
    return wrapper


def server_status():
    try:
        inst = compute_v1.InstancesClient().get(project=PROJECT, zone=ZONE, instance=INSTANCE_NAME)
        ip = next(
            (ac.nat_i_p for ni in inst.network_interfaces for ac in ni.access_configs if ac.nat_i_p),
            None,
        )
        return inst.status.lower(), ip
    except Exception:
        return "stopped", None


def get_installation_token():
    now = int(time.time())
    app_jwt = jwt.encode(
        {"iat": now - 60, "exp": now + 540, "iss": GH_APP_ID},
        GH_APP_PRIVATE_KEY,
        algorithm="RS256",
    )
    resp = requests.post(
        f"https://api.github.com/app/installations/{GH_APP_INSTALLATION_ID}/access_tokens",
        headers={"Authorization": f"Bearer {app_jwt}", "Accept": "application/vnd.github+json"},
        timeout=10,
    )
    if not resp.ok:
        logging.error("GitHub token exchange failed: %s %s", resp.status_code, resp.text)
        resp.raise_for_status()
    return resp.json()["token"]


def trigger(event_type):
    token = get_installation_token()
    resp = requests.post(
        f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/dispatches",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
        json={"event_type": event_type},
        timeout=10,
    )
    if not resp.ok:
        logging.error("GitHub dispatch failed: %s %s", resp.status_code, resp.text)
        resp.raise_for_status()


def get_workflow_status():
    try:
        token = get_installation_token()
        resp = requests.get(
            f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/actions/runs?event=repository_dispatch&per_page=1",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
            timeout=10,
        )
        if not resp.ok:
            return None
        runs = resp.json().get("workflow_runs", [])
        if not runs:
            return None
        return {
            "status": runs[0].get("status"),
            "conclusion": runs[0].get("conclusion"),
            "html_url": runs[0].get("html_url")
        }
    except Exception as e:
        logging.error("Failed to get workflow status: %s", getattr(e, "message", str(e)))
        return None


@app.get("/")
@require_login
def index():
    status, ip = server_status()
    workflow = get_workflow_status() if status != 'running' else None
    return render_template("index.html", status=status, ip=ip, user=session["email"], phrase=random.choice(PHRASES), workflow=workflow)


@app.get("/api/status")
@require_login
def api_status():
    status, ip = server_status()
    workflow = get_workflow_status() if status != 'running' else None
    return jsonify({"status": status, "ip": ip, "workflow": workflow})


@app.get("/login")
def login():
    redirect_uri = OAUTH_REDIRECT_URI or url_for("callback", _external=True)
    return oauth.google.authorize_redirect(redirect_uri)


@app.get("/auth/callback")
def callback():
    token = oauth.google.authorize_access_token()
    session["email"] = token["userinfo"]["email"]
    return redirect(url_for("index"))


@app.get("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.post("/action/<name>")
@require_login
def action(name):
    events = {"start": "create-infr", "stop": "destroy-infr"}
    if name in events:
        trigger(events[name])
    return redirect(url_for("index"))


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
