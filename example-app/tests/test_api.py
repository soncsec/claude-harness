"""API tests: the happy paths, then the security cases that must never regress."""

from __future__ import annotations

import pytest
from conftest import PASSWORD, make_client

# --- Happy paths -------------------------------------------------------------


def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_sign_up_and_log_in(client):
    created = client.post("/users", json={"username": "alice", "password": PASSWORD})
    assert created.status_code == 201
    assert created.json()["username"] == "alice"
    assert "password" not in created.text
    session = client.post("/sessions", json={"username": "alice", "password": PASSWORD})
    assert session.status_code == 200
    assert session.json()["token"]


def test_note_lifecycle(client, login):
    auth = login("alice")
    created = client.post("/notes", json={"title": "First", "body": "hello"}, headers=auth)
    assert created.status_code == 201
    note_id = created.json()["id"]

    assert client.get(f"/notes/{note_id}", headers=auth).json()["body"] == "hello"
    updated = client.put(
        f"/notes/{note_id}", json={"title": "First", "body": "edited"}, headers=auth
    )
    assert updated.json()["body"] == "edited"
    assert [n["id"] for n in client.get("/notes", headers=auth).json()] == [note_id]

    assert client.delete(f"/notes/{note_id}", headers=auth).status_code == 204
    assert client.get(f"/notes/{note_id}", headers=auth).status_code == 404


# --- Authentication ----------------------------------------------------------


def test_duplicate_username_is_rejected(client):
    client.post("/users", json={"username": "alice", "password": PASSWORD})
    again = client.post("/users", json={"username": "alice", "password": PASSWORD})
    assert again.status_code == 409


@pytest.mark.parametrize(
    "username,password",
    [
        ("al", PASSWORD),  # too short
        ("Alice", PASSWORD),  # uppercase not allowed
        ("alice; DROP TABLE users", PASSWORD),  # characters outside the allowed set
        ("alice", "short"),  # password under 12 characters
        ("alice", "x" * 129),  # password over 128 characters
    ],
)
def test_sign_up_validation(client, username, password):
    response = client.post("/users", json={"username": username, "password": password})
    assert response.status_code == 422


def test_wrong_password_and_unknown_user_look_the_same(client):
    client.post("/users", json={"username": "alice", "password": PASSWORD})
    wrong = client.post("/sessions", json={"username": "alice", "password": "wrong password!"})
    unknown = client.post("/sessions", json={"username": "nobody", "password": PASSWORD})
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json()  # no hint about which usernames exist


def test_dummy_password_never_logs_in_unknown_user(client):
    response = client.post(
        "/sessions", json={"username": "nobody", "password": "not-a-real-password"}
    )
    assert response.status_code == 401


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": "Bearer "},
        {"Authorization": "Bearer made-up-token"},
        {"Authorization": "Basic YWxpY2U6cGFzc3dvcmQ="},
    ],
)
def test_notes_require_a_valid_token(client, headers):
    assert client.get("/notes", headers=headers).status_code == 401
    assert client.post("/notes", json={"title": "x"}, headers=headers).status_code == 401


def test_expired_token_is_rejected(tmp_path):
    client = make_client(tmp_path, token_ttl_seconds=0)
    client.post("/users", json={"username": "alice", "password": PASSWORD})
    token = client.post("/sessions", json={"username": "alice", "password": PASSWORD}).json()[
        "token"
    ]
    assert client.get("/notes", headers={"Authorization": f"Bearer {token}"}).status_code == 401


def test_logout_revokes_the_token(client, login):
    auth = login("alice")
    assert client.delete("/sessions", headers=auth).status_code == 204
    assert client.get("/notes", headers=auth).status_code == 401


# --- Access control: one user can never reach another user's notes ------------


def test_users_cannot_touch_each_others_notes(client, login):
    alice, bob = login("alice"), login("bob")
    note_id = client.post("/notes", json={"title": "Alice only"}, headers=alice).json()["id"]

    assert client.get(f"/notes/{note_id}", headers=bob).status_code == 404
    assert client.put(f"/notes/{note_id}", json={"title": "hacked"}, headers=bob).status_code == 404
    assert client.delete(f"/notes/{note_id}", headers=bob).status_code == 404
    assert client.get("/notes", headers=bob).json() == []
    # Alice's note is untouched.
    assert client.get(f"/notes/{note_id}", headers=alice).json()["title"] == "Alice only"


def test_other_users_note_looks_like_a_missing_one(client, login):
    alice, bob = login("alice"), login("bob")
    note_id = client.post("/notes", json={"title": "Alice only"}, headers=alice).json()["id"]
    theirs = client.get(f"/notes/{note_id}", headers=bob)
    missing = client.get("/notes/999999", headers=bob)
    assert theirs.status_code == missing.status_code == 404
    assert theirs.json() == missing.json()


# --- Input handling ----------------------------------------------------------


@pytest.mark.parametrize(
    "note",
    [
        {"title": ""},  # empty title
        {"title": "x" * 201},  # title too long
        {"title": "ok", "body": "x" * 10_001},  # body too long
        {"body": "no title"},  # missing title
    ],
)
def test_note_validation(client, login, note):
    assert client.post("/notes", json=note, headers=login("alice")).status_code == 422


def test_sql_injection_is_stored_as_plain_text(client, login):
    auth = login("alice")
    payload = "x'); DROP TABLE notes; --"
    created = client.post("/notes", json={"title": payload, "body": payload}, headers=auth)
    assert created.status_code == 201
    assert client.get("/notes", headers=auth).json()[0]["title"] == payload


def test_injection_in_login_does_not_bypass_auth(client):
    client.post("/users", json={"username": "alice", "password": PASSWORD})
    response = client.post("/sessions", json={"username": "alice' --", "password": "anything"})
    assert response.status_code == 401


def test_security_headers_are_set(client):
    response = client.get("/health")
    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["Cache-Control"] == "no-store"
