"""The Notes API: users sign up, log in and manage their own notes.

Run it with `make run`. Interactive API docs are at /docs.
"""

# No `from __future__ import annotations` here: FastAPI must resolve the
# dependency aliases defined inside create_app(), which only works when
# annotations are evaluated where they are written.
import sqlite3
import time
from collections.abc import Awaitable, Callable, Iterator
from contextlib import closing
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response, status
from pydantic import BaseModel, Field

from notes_api import db, security
from notes_api.config import Settings

UNAUTHORIZED = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Not authenticated",
    headers={"WWW-Authenticate": "Bearer"},
)
NOT_FOUND = HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Note not found")


class SignUp(BaseModel):
    username: str = Field(pattern=r"^[a-z0-9_]{3,32}$")
    password: str = Field(min_length=12, max_length=128)


class LogIn(BaseModel):
    username: str = Field(max_length=32)
    password: str = Field(max_length=128)


class UserOut(BaseModel):
    id: int
    username: str


class Session(BaseModel):
    token: str
    expires_at: float


class NoteIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    body: str = Field(default="", max_length=10_000)


class NoteOut(BaseModel):
    id: int
    title: str
    body: str
    created_at: int
    updated_at: int


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the app. `uvicorn notes_api.main:create_app --factory` calls this."""
    settings = settings or Settings.from_env()
    db.init_db(settings.database_path)
    # Compared against when a username does not exist, so a failed login takes
    # the same time either way and does not reveal which usernames exist.
    dummy_hash = security.hash_password("not-a-real-password", n=settings.scrypt_n)

    app = FastAPI(title="Notes API", version="0.1.0")

    @app.middleware("http")
    async def security_headers(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        return response

    def get_conn() -> Iterator[sqlite3.Connection]:
        with closing(db.connect(settings.database_path)) as conn:
            yield conn

    Conn = Annotated[sqlite3.Connection, Depends(get_conn)]

    def current_user(conn: Conn, authorization: Annotated[str | None, Header()] = None) -> int:
        if not authorization or not authorization.startswith("Bearer "):
            raise UNAUTHORIZED
        digest = security.token_digest(authorization.removeprefix("Bearer ").strip())
        row = conn.execute(
            "SELECT user_id, expires_at FROM tokens WHERE token_hash = ?", (digest,)
        ).fetchone()
        if row is None or row["expires_at"] <= time.time():
            raise UNAUTHORIZED
        return int(row["user_id"])

    UserId = Annotated[int, Depends(current_user)]

    def own_note(conn: sqlite3.Connection, note_id: int, user_id: int) -> sqlite3.Row:
        # Someone else's note gets the same 404 as a missing one, so ids leak nothing.
        row = conn.execute(
            "SELECT * FROM notes WHERE id = ? AND user_id = ?", (note_id, user_id)
        ).fetchone()
        if row is None:
            raise NOT_FOUND
        return row

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/users", status_code=status.HTTP_201_CREATED)
    def sign_up(body: SignUp, conn: Conn) -> UserOut:
        password_hash = security.hash_password(body.password, n=settings.scrypt_n)
        try:
            cursor = conn.execute(
                "INSERT INTO users (username, password_hash, created_at) VALUES (?, ?, ?)",
                (body.username, password_hash, int(time.time())),
            )
            conn.commit()
        except sqlite3.IntegrityError:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT, detail="Username is taken"
            ) from None
        return UserOut(id=int(cursor.lastrowid or 0), username=body.username)

    @app.post("/sessions")
    def log_in(body: LogIn, conn: Conn) -> Session:
        row = conn.execute(
            "SELECT id, password_hash FROM users WHERE username = ?", (body.username,)
        ).fetchone()
        stored = row["password_hash"] if row else dummy_hash
        if not security.verify_password(body.password, stored) or row is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED, detail="Wrong username or password"
            )
        token, digest = security.new_token()
        expires_at = time.time() + settings.token_ttl_seconds
        conn.execute(
            "INSERT INTO tokens (token_hash, user_id, expires_at) VALUES (?, ?, ?)",
            (digest, row["id"], expires_at),
        )
        conn.commit()
        return Session(token=token, expires_at=expires_at)

    @app.delete("/sessions", status_code=status.HTTP_204_NO_CONTENT)
    def log_out(user_id: UserId, conn: Conn, authorization: Annotated[str, Header()]) -> Response:
        digest = security.token_digest(authorization.removeprefix("Bearer ").strip())
        conn.execute("DELETE FROM tokens WHERE token_hash = ?", (digest,))
        conn.commit()
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    @app.get("/notes")
    def list_notes(user_id: UserId, conn: Conn) -> list[NoteOut]:
        rows = conn.execute(
            "SELECT * FROM notes WHERE user_id = ? ORDER BY id", (user_id,)
        ).fetchall()
        return [NoteOut(**dict(row)) for row in rows]

    @app.post("/notes", status_code=status.HTTP_201_CREATED)
    def create_note(body: NoteIn, user_id: UserId, conn: Conn) -> NoteOut:
        now = int(time.time())
        cursor = conn.execute(
            "INSERT INTO notes (user_id, title, body, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (user_id, body.title, body.body, now, now),
        )
        conn.commit()
        return NoteOut(**dict(own_note(conn, int(cursor.lastrowid or 0), user_id)))

    @app.get("/notes/{note_id}")
    def get_note(note_id: int, user_id: UserId, conn: Conn) -> NoteOut:
        return NoteOut(**dict(own_note(conn, note_id, user_id)))

    @app.put("/notes/{note_id}")
    def update_note(note_id: int, body: NoteIn, user_id: UserId, conn: Conn) -> NoteOut:
        own_note(conn, note_id, user_id)
        conn.execute(
            "UPDATE notes SET title = ?, body = ?, updated_at = ? WHERE id = ? AND user_id = ?",
            (body.title, body.body, int(time.time()), note_id, user_id),
        )
        conn.commit()
        return NoteOut(**dict(own_note(conn, note_id, user_id)))

    @app.delete("/notes/{note_id}", status_code=status.HTTP_204_NO_CONTENT)
    def delete_note(note_id: int, user_id: UserId, conn: Conn) -> Response:
        own_note(conn, note_id, user_id)
        conn.execute("DELETE FROM notes WHERE id = ? AND user_id = ?", (note_id, user_id))
        conn.commit()
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    return app
