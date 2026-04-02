import logging
import os
import random
import time

import jwt
import requests
from mcstatus import JavaServer
from flask import Flask, jsonify, redirect, render_template, request, url_for
from werkzeug.middleware.proxy_fix import ProxyFix
from google.cloud import compute_v1

from pathlib import Path

_phrases_file = Path(__file__).parent / "phrases.txt"
PHRASES = [l.strip() for l in _phrases_file.read_text().splitlines() if l.strip()]

PROJECT = os.environ["GCP_PROJECT"]
ZONE = os.environ["GCP_ZONE"]
INSTANCE_NAME = os.environ["INSTANCE_NAME"]
REPO_OWNER = os.environ.get("GITHUB_REPO_OWNER", "axiomeye")
REPO_NAME = os.environ.get("GITHUB_REPO_NAME", "aria-minecraft-server-iac")
GH_APP_ID = os.environ["GH_APP_ID"]
GH_APP_INSTALLATION_ID = os.environ["GH_APP_INSTALLATION_ID"]
GH_APP_PRIVATE_KEY = os.environ["GH_APP_PRIVATE_KEY"]
PAYPAL_URL = os.environ.get("PAYPAL_URL", "")

app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)


def get_user():
    header = request.headers.get("X-Goog-Authenticated-User-Email", "")
    return header.split(":")[-1].strip() if ":" in header else header.strip()


def check_minecraft_ready(ip):
    try:
        server = JavaServer.lookup(f"{ip}:25565", timeout=1.5)
        server.status()
        return True
    except Exception:
        return False

def server_status():
    try:
        inst = compute_v1.InstancesClient().get(project=PROJECT, zone=ZONE, instance=INSTANCE_NAME)
        ip = next(
            (ac.nat_i_p for ni in inst.network_interfaces for ac in ni.access_configs if ac.nat_i_p),
            None,
        )
        status = inst.status.lower()
        
        # Keep it in spinning_up state if VM is running but Java isn't responding yet
        if status == 'running' and ip:
            if not check_minecraft_ready(ip):
                status = 'spinning_up'

        return status, ip
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
def index():
    status, ip = server_status()
    workflow = get_workflow_status() if status != 'running' else None
    return render_template("index.html", status=status, ip=ip, user=get_user(), phrase=random.choice(PHRASES), workflow=workflow, paypal_url=PAYPAL_URL)


@app.get("/api/status")
def api_status():
    status, ip = server_status()
    workflow = get_workflow_status() if status != 'running' else None
    return jsonify({"status": status, "ip": ip, "workflow": workflow})


@app.post("/action/<name>")
def action(name):
    events = {"start": "create-infr", "stop": "destroy-infr"}
    if name in events:
        trigger(events[name])
    return redirect(url_for("index"))


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
