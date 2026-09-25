"""Authenticated, deliberate pair reviews and reciprocal iNaturalist comments."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time
from urllib.parse import urlencode

import requests


CHOICES = {
    "same_individual": ("Waarschijnlijk hetzelfde exemplaar", "These observations may show the same individual."),
    "same_species": ("Waarschijnlijk dezelfde soort", "These observations may show the same species."),
    "closely_related": ("Waarschijnlijk nauw verwant", "These observations may show closely related organisms."),
    "unrelated": ("Waarschijnlijk geen verwantschap", None),
    "nothing_to_add": ("Hier heb ik niets aan toe te voegen", None),
}
NO_COMMENT_CHOICES = {key for key, (_, sentence) in CHOICES.items() if sentence is None}
INAT = "https://www.inaturalist.org"
API = "https://api.inaturalist.org/v1"


def pair_ids(left, right):
    a, b = sorted((int(left), int(right)))
    if a == b:
        raise ValueError("Een waarneming kan niet met zichzelf worden beoordeeld.")
    return a, b


def comment_body(choice, other_id):
    sentence = CHOICES[choice][1]
    if sentence is None:
        raise ValueError("Bij deze keuze wordt geen opmerking geplaatst.")
    return f"{sentence} Related observation: {INAT}/observations/{int(other_id)}"


def manual_review_record(left_id, right_id, choice, status="pending"):
    """Use reviewer 0 for the owner's manual workflow; OAuth uses real user IDs."""
    a, b = pair_ids(left_id, right_id)
    if choice not in CHOICES or status not in ("pending", "uncertain", "complete"):
        raise ValueError("Ongeldige handmatige beoordeling.")
    return {"left_id": a, "right_id": b, "reviewer_id": 0,
            "choice": choice, "status": status,
            "left_comment_id": None, "right_comment_id": None}


def _signed_state(secret, area=""):
    payload = json.dumps({"t": int(time.time()), "n": secrets.token_urlsafe(16),
                          "area": area[:3000]}, separators=(",", ":")).encode()
    data = base64.urlsafe_b64encode(payload).decode().rstrip("=")
    sig = hmac.new(secret.encode(), data.encode(), hashlib.sha256).hexdigest()
    return f"{data}.{sig}"


def _read_state(secret, state):
    data, signature = state.split(".", 1)
    expected = hmac.new(secret.encode(), data.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(signature, expected):
        raise ValueError("De aanmelding kon niet worden gecontroleerd.")
    payload = json.loads(base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)))
    if not 0 <= time.time() - payload["t"] <= 600:
        raise ValueError("De aanmeldlink is verlopen; probeer het opnieuw.")
    return payload


def authorization_url(client_id, client_secret, redirect_uri, area=""):
    return INAT + "/oauth/authorize?" + urlencode({
        "client_id": client_id, "redirect_uri": redirect_uri,
        "response_type": "code", "scope": "login write",
        "state": _signed_state(client_secret, area),
    })


def exchange_code(client_id, client_secret, redirect_uri, state, code):
    payload = _read_state(client_secret, state)
    response = requests.post(INAT + "/oauth/token", data={
        "grant_type": "authorization_code", "client_id": client_id,
        "client_secret": client_secret, "redirect_uri": redirect_uri, "code": code,
    }, timeout=20)
    response.raise_for_status()
    access_token = response.json()["access_token"]
    jwt_response = requests.get(INAT + "/users/api_token", headers={
        "Authorization": f"Bearer {access_token}"}, timeout=20)
    jwt_response.raise_for_status()
    jwt = jwt_response.json()["api_token"]
    user_response = requests.get(API + "/users/me", headers={
        "Authorization": f"Bearer {jwt}"}, timeout=20)
    user_response.raise_for_status()
    user = user_response.json()["results"][0]
    return access_token, user, payload.get("area", "")


def _database(url, secret_key, method, path, *, params=None, body=None):
    response = requests.request(method, url.rstrip("/") + "/rest/v1/" + path,
                                headers={"apikey": secret_key,
                                         "Prefer": "resolution=merge-duplicates,return=representation",
                                         "Content-Type": "application/json"},
                                params=params, json=body, timeout=25)
    if not response.ok:
        raise RuntimeError(f"Supabase {response.status_code}: {response.text}")
    return response.json() if response.content else []


def load_reviews(url, key, reviewer_id, ids):
    if not ids:
        return {}
    # Filter at the database, so browsing a large index never downloads the whole review table.
    fetched = []
    for offset in range(0, len(ids), 150):
        chunk = ids[offset:offset + 150]
        fetched.extend(_database(url, key, "GET", "pair_reviews", params={
            "select": "left_id,right_id,reviewer_id,choice,left_comment_id,right_comment_id,status",
            "reviewer_id": f"eq.{int(reviewer_id)}",
            "or": "(" + ",".join(f"and(left_id.eq.{a},right_id.eq.{b})" for a, b in chunk) + ")",
        }))
    return {(int(r["left_id"]), int(r["right_id"])): r for r in fetched}


def save_review(url, key, review):
    rows = _database(url, key, "POST", "pair_reviews", params={
        "on_conflict": "left_id,right_id,reviewer_id"}, body=review)
    return rows[0] if rows else review


def create_comment(access_token, observation_id, body):
    response = requests.post(INAT + "/comments.json", json={"comment": {
        "parent_type": "Observation", "parent_id": int(observation_id), "body": body,
    }}, headers={"Authorization": f"Bearer {access_token}"}, timeout=25)
    response.raise_for_status()
    result = response.json()
    if not result.get("id"):
        raise RuntimeError("iNaturalist gaf geen bevestiging met een opmerking-ID.")
    return int(result["id"])


def find_own_comment(access_token, reviewer_id, observation_id, body):
    """Reconcile after a definite rejection or interrupted database update."""
    jwt_response = requests.get(INAT + "/users/api_token", headers={
        "Authorization": f"Bearer {access_token}"}, timeout=20)
    jwt_response.raise_for_status()
    jwt = jwt_response.json()["api_token"]
    response = requests.get(API + f"/observations/{int(observation_id)}",
                            headers={"Authorization": f"Bearer {jwt}"}, timeout=25)
    response.raise_for_status()
    rows = response.json().get("results", [])
    for comment in (rows[0].get("comments", []) if rows else []):
        if int((comment.get("user") or {}).get("id") or 0) == reviewer_id and comment.get("body") == body:
            return int(comment["id"])
    return None


def publish_review(url, key, access_token, reviewer_id, left_id, right_id, choice, existing=None):
    """Persist each side before posting the next; ambiguous failures require manual inspection."""
    a, b = pair_ids(left_id, right_id)
    if choice not in CHOICES:
        raise ValueError("Onbekende beoordeling.")
    if existing and existing["choice"] != choice:
        raise ValueError("Dit paar is al beoordeeld. Gepubliceerde opmerkingen worden niet automatisch gewijzigd.")
    review = {"left_id": a, "right_id": b, "reviewer_id": int(reviewer_id),
              "choice": choice, "left_comment_id": existing.get("left_comment_id") if existing else None,
              "right_comment_id": existing.get("right_comment_id") if existing else None,
              "status": existing.get("status") if existing else "pending"}
    if existing and review["status"] in ("complete", "uncertain"):
        return review
    if choice in NO_COMMENT_CHOICES:
        review["status"] = "complete"
        return save_review(url, key, review)
    review = save_review(url, key, review)
    for field, observation_id, other_id in (("left_comment_id", a, b),
                                             ("right_comment_id", b, a)):
        if review.get(field):
            continue
        body = comment_body(choice, other_id)
        try:
            # Pending means a previous run stopped: look for the exact earlier
            # comment before posting again, including after a lost DB response.
            posted_id = (find_own_comment(access_token, reviewer_id, observation_id, body)
                         if existing else None)
            posted_id = posted_id or create_comment(access_token, observation_id, body)
        except requests.HTTPError:
            review["status"] = "pending"  # A definite HTTP rejection can be retried.
            save_review(url, key, review)
            raise
        except Exception:
            review["status"] = "uncertain"  # A timeout may have posted successfully.
            save_review(url, key, review)
            raise
        review[field] = posted_id
        # If saving fails, stop; inspect iNaturalist before any new attempt.
        review = save_review(url, key, review)
    review["status"] = "complete"
    return save_review(url, key, review)
