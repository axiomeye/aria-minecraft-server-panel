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

# Each world is an independent VM with its own disk and Terraform state; see
# terraform/vm/locals.tf in aria-minecraft-server-iac. The instance name for
# classic still comes from INSTANCE_NAME so existing deployments keep working.
WORLDS = {
    "classic": {
        "label": "AriA Classic",
        "instance": INSTANCE_NAME,
        "version": "1.20.1",
    },
    "cobblemon": {
        "label": "AriA Cobblemon",
        "instance": os.environ.get("COBBLEMON_INSTANCE_NAME", "aria-minecraft-cobblemon-instance"),
        "version": "1.21.1",
    },
    "latest": {
        "label": "AriA Latest",
        "instance": os.environ.get("LATEST_INSTANCE_NAME", "aria-minecraft-latest-instance"),
        "version": "26.2",
    },
}
DEFAULT_WORLD = "classic"


def pick_world(name):
    """Validate a world name from a query string or path segment."""
    return name if name in WORLDS else DEFAULT_WORLD
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
    """Query the Minecraft SLP endpoint and return player data.

    Returns a dict with 'online', 'players_online', 'players_max', and
    'player_names' when the server is reachable, or a falsy dict when it
    is not.
    """
    try:
        server = JavaServer.lookup(f"{ip}:25565", timeout=5)
        resp = server.status()
        names = []
        if resp.players.sample:
            names = [p.name for p in resp.players.sample]
        return {
            "online": True,
            "players_online": resp.players.online,
            "players_max": resp.players.max,
            "player_names": names,
        }
    except Exception:
        return {"online": False, "players_online": 0, "players_max": 0, "player_names": []}

_EMPTY_PLAYERS = {"players_online": 0, "players_max": 0, "player_names": []}

def server_status(instance):
    try:
        inst = compute_v1.InstancesClient().get(project=PROJECT, zone=ZONE, instance=instance)
        ip = next(
            (ac.nat_i_p for ni in inst.network_interfaces for ac in ni.access_configs if ac.nat_i_p),
            None,
        )
        status = inst.status.lower()
        players = dict(_EMPTY_PLAYERS)

        # Keep it in spinning_up state if VM is running but Java isn't responding yet
        if status == 'running' and ip:
            mc = check_minecraft_ready(ip)
            if not mc["online"]:
                status = 'spinning_up'
            else:
                players = {k: mc[k] for k in _EMPTY_PLAYERS}

        return status, ip, players
    except Exception:
        return "stopped", None, dict(_EMPTY_PLAYERS)


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


def trigger(event_type, world):
    token = get_installation_token()
    resp = requests.post(
        f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/dispatches",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
        json={"event_type": event_type, "client_payload": {"world": world}},
        timeout=10,
    )
    if not resp.ok:
        logging.error("GitHub dispatch failed: %s %s", resp.status_code, resp.text)
        resp.raise_for_status()


def get_workflow_status(world):
    """Status of the most recent create/destroy run for this world.

    The runs API cannot filter on client_payload, and reading a run's inputs
    costs an extra request each. The workflows therefore put the world in
    run-name, which comes back as the run's display title, and we match on
    that marker here.

    Runs predating that change carry no marker and are skipped, so an old run
    never shows up under the wrong world.
    """
    marker = f"[{world}]"
    try:
        token = get_installation_token()
        resp = requests.get(
            f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/actions/runs"
            "?event=repository_dispatch&per_page=20",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
            timeout=10,
        )
        if not resp.ok:
            return None
        for run in resp.json().get("workflow_runs", []):
            title = run.get("display_title") or run.get("name") or ""
            if marker not in title:
                continue
            return {
                "status": run.get("status"),
                "conclusion": run.get("conclusion"),
                "html_url": run.get("html_url"),
            }
        return None
    except Exception as e:
        logging.error("Failed to get workflow status: %s", getattr(e, "message", str(e)))
        return None


@app.get("/")
def index():
    world = pick_world(request.args.get("world", DEFAULT_WORLD))
    status, ip, players = server_status(WORLDS[world]["instance"])
    workflow = get_workflow_status(world) if status != 'running' else None
    return render_template(
        "index.html", status=status, ip=ip, user=get_user(),
        phrase=random.choice(PHRASES), workflow=workflow, paypal_url=PAYPAL_URL,
        world=world, worlds=WORLDS, players=players,
    )


@app.get("/api/status")
def api_status():
    world = pick_world(request.args.get("world", DEFAULT_WORLD))
    status, ip, players = server_status(WORLDS[world]["instance"])
    workflow = get_workflow_status(world) if status != 'running' else None
    return jsonify({"world": world, "status": status, "ip": ip, "workflow": workflow, **players})


@app.get("/api/status/all")
def api_status_all():
    """Status of every world, so the landing dashboard can show live data."""
    out = {}
    for name, cfg in WORLDS.items():
        status, ip, players = server_status(cfg["instance"])
        out[name] = {
            "status": status, "ip": ip,
            "label": cfg["label"], "version": cfg["version"],
            **players,
        }
    return jsonify(out)


@app.post("/action/<world>/<name>")
def action(world, name):
    world = pick_world(world)
    events = {"start": "create-infr", "stop": "destroy-infr"}
    if name in events:
        trigger(events[name], world)
    return redirect(url_for("index", world=world))


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
